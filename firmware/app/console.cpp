#include "runtime.h"
#include "motion.hpp"
#include "motion_inference.h"
#include "usart.h"
#include "task.h"
#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace {
constexpr uint16_t rxCapacity = 256;
volatile uint8_t rxRing[rxCapacity];
volatile uint16_t rxHead, rxTail;
volatile uint32_t rxErrors;
uint8_t incoming;
uint32_t txErrors;
using Window = int16_t[100][6];

struct Replay {
    Window raw{};
    uint32_t id = 0;
    uint32_t lastActivity = 0;
    unsigned count = 0;
    bool active = false;
} replay;

Window liveRing, orderedWindow;
unsigned writeSlot, validRows, rowsSincePrediction;
uint32_t previousSeq, previousMs;

// USART1 has one TX owner; sampling preempts blocking output at all times.
void send(const char *format, ...) {
    char buffer[384];
    va_list args;
    va_start(args, format);
    const int n = std::vsnprintf(buffer, sizeof(buffer) - 2, format, args);
    va_end(args);
    if (n < 0 || static_cast<size_t>(n) >= sizeof(buffer) - 2) {
        ++txErrors;
        return;
    }
    buffer[n] = '\r';
    buffer[n+1] = '\n';
    if (HAL_UART_Transmit(&huart1, reinterpret_cast<uint8_t *>(buffer), n + 2, 50) != HAL_OK)
        ++txErrors;
}

void rearmRx() {
    if (huart1.RxState == HAL_UART_STATE_READY &&
        HAL_UART_Receive_IT(&huart1, &incoming, 1) != HAL_OK)
        ++rxErrors;
}

bool popByte(uint8_t &value) {
    if (rxTail == rxHead) return false;
    value = rxRing[rxTail];
    __DMB();
    rxTail = (rxTail + 1U) % rxCapacity;
    return true;
}

const char *statusName(SensorStatus status) {
    switch (status) {
        case SensorStatus::ok: return "OK";
        case SensorStatus::i2c: return "I2C";
        case SensorStatus::noData: return "NO_DATA";
        default: return "OFFLINE";
    }
}

unsigned bestClass(const int8_t scores[3]) {
    unsigned best = 0;
    for (unsigned i = 1; i < 3; ++i) if (scores[i] > scores[best]) best = i;
    return best;
}

void predict(const Window &raw, uint32_t id, bool echoInput, uint32_t begin) {
    int8_t input[24], scores[3];
    uint32_t netCycles;
    if (!motion_predict(raw, input, scores, &netCycles)) {
        send("ERR,%lu,MODEL", static_cast<unsigned long>(id));
        return;
    }
    const unsigned predicted = bestClass(scores);
    const uint32_t totalCycles = DWT->CYCCNT - begin;
    if (echoInput) {
        char fields[24 * 5 + 1];
        size_t used = 0;
        for (auto v : input) {
            int n = std::snprintf(fields+used, sizeof(fields)-used, ",%d", v);
            if (n < 0 || static_cast<size_t>(n) >= sizeof(fields)-used) {
                send("ERR,%lu,FORMAT", static_cast<unsigned long>(id));
                return;
            }
            used += n;
        }
        send("Q,%lu%s", static_cast<unsigned long>(id), fields);
    }
    send("%c,%lu,%u,%d,%d,%d,%lu,%lu", echoInput ? 'R' : 'P',
         static_cast<unsigned long>(id), predicted, scores[0], scores[1], scores[2],
         static_cast<unsigned long>(netCycles), static_cast<unsigned long>(totalCycles));
}

void onSample(const Sample &s) {
    send("S,%lu,%lu,%d,%d,%d,%d,%d,%d,%s", static_cast<unsigned long>(s.seq),
         static_cast<unsigned long>(s.ms), s.axes[0], s.axes[1], s.axes[2],
         s.axes[3], s.axes[4], s.axes[5], statusName(s.status));
    if (s.status != SensorStatus::ok || (validRows &&
        (s.seq != previousSeq+1U || s.ms-previousMs < 10 || s.ms-previousMs > 30))) {
        validRows = rowsSincePrediction = 0;
        writeSlot = 0;
    }
    if (s.status != SensorStatus::ok || !motion_model_ready()) return;
    previousSeq = s.seq;
    previousMs = s.ms;
    std::memcpy(liveRing[writeSlot], s.axes, sizeof(s.axes));
    writeSlot = (writeSlot + 1) % 100;
    if (validRows < 100) ++validRows;
    ++rowsSincePrediction;
    if (validRows < 100 || rowsSincePrediction < 25) return;
    rowsSincePrediction = 0;
    const uint32_t begin = DWT->CYCCNT;
    for (unsigned i = 0; i < 100; ++i)
        std::memcpy(orderedWindow[i], liveRing[(writeSlot+i)%100], sizeof(liveRing[0]));
    predict(orderedWindow, s.seq, false, begin);
}

bool decimal(const char *&cursor, uint32_t limit, uint32_t &result) {
    if (*cursor < '0' || *cursor > '9') return false;
    uint32_t n = 0;
    do {
        unsigned digit = static_cast<unsigned>(*cursor-'0');
        if (n > (limit-digit)/10) return false;
        n = n*10+digit;
        ++cursor;
    } while (*cursor >= '0' && *cursor <= '9');
    result = n;
    return true;
}

bool parseId(const char *&p, uint32_t &value) {
    if (*p != ',') return false;
    ++p;
    return decimal(p, UINT32_MAX, value);
}

template<typename T, size_t N>
bool values(const char *p, T (&out)[N], int limit) {
    for (auto &v : out) {
        if (*p++ != ',') return false;
        const bool negative = *p == '-';
        if (negative) ++p;
        uint32_t magnitude;
        if (!decimal(p, negative ? limit+1 : limit, magnitude)) return false;
        v = static_cast<T>(negative ? -static_cast<int>(magnitude) : static_cast<int>(magnitude));
    }
    return *p == '\0';
}

void abortReplay(const char *reason) {
    send("ERR,%lu,%s", static_cast<unsigned long>(replay.active ? replay.id : 0), reason);
    replay.active = false;
}

void sendInfo() {
    TaskStatus_t tasks[6];
    const auto count = uxTaskGetSystemState(tasks, 6, nullptr);
    unsigned long comm = 0, sample = 0, idle = 0, timer = 0;
    for (UBaseType_t i = 0; i < count; ++i) {
        const char *name = tasks[i].pcTaskName;
        const auto bytes = tasks[i].usStackHighWaterMark * sizeof(StackType_t);
        if (!std::strcmp(name,"comm")) comm = bytes;
        else if (!std::strcmp(name,"sampling")) sample = bytes;
        else if (!std::strcmp(name,"IDLE")) idle = bytes;
        else if (!std::strcmp(name,"Tmr Svc")) timer = bytes;
    }
    send("INFO,%s,%lu,heap_min_free=%lu,comm_stack_min_free=%lu,sample_stack_min_free=%lu,"
         "idle_stack_min_free=%lu,timer_stack_min_free=%lu,msp_min_free=%lu,"
         "queue_drops=%lu,rx_errors=%lu,tx_errors=%lu", motion_model_id(),
         static_cast<unsigned long>(SystemCoreClock),
         static_cast<unsigned long>(xPortGetMinimumEverFreeHeapSize()), comm, sample, idle, timer,
         static_cast<unsigned long>(mainStackFreeMinimum()), static_cast<unsigned long>(queueDrops),
         static_cast<unsigned long>(rxErrors), static_cast<unsigned long>(txErrors));
}

void command(const char *line) {
    if (std::strcmp(line, "STATUS") == 0) { sendInfo(); return; }
    const char *p = line;
    uint32_t id = 0;
    if (std::strncmp(p, "BEGIN,", 6) == 0) {
        p += 5;
        if (!parseId(p,id) || *p) { abortReplay("ID"); return; }
        replay.active = false;
        if (!motion_model_ready()) { send("ERR,%lu,NO_MODEL", static_cast<unsigned long>(id)); return; }
        replay.active = true;
        replay.id = id;
        replay.count = 0;
        replay.lastActivity = HAL_GetTick();
        send("READY,%lu", static_cast<unsigned long>(id));
    } else if (p[0] == 'D' && p[1] == ',') {
        if (!replay.active || replay.count >= 100 || !values(p+1, replay.raw[replay.count], 32767)) {
            abortReplay("ROW"); return;
        }
        replay.lastActivity = HAL_GetTick();
        send("ACK,%lu,%u", static_cast<unsigned long>(replay.id), ++replay.count);
    } else if (std::strncmp(p, "END,", 4) == 0) {
        p += 3;
        if (!parseId(p,id) || *p || !replay.active || id!=replay.id || replay.count!=100) {
            abortReplay("END"); return;
        }
        replay.active = false;
        predict(replay.raw, id, true, DWT->CYCCNT);
    } else if (p[0] == 'I' && p[1] == ',') {
        int8_t input[24], scores[3];
        uint32_t cycles;
        ++p;
        if (!parseId(p,id) || !values(p,input,127)) { abortReplay("INPUT"); return; }
        if (!motion_predict_q(input, scores, &cycles)) {
            send("ERR,%lu,MODEL", static_cast<unsigned long>(id)); return;
        }
        send("J,%lu,%d,%d,%d,%lu", static_cast<unsigned long>(id), scores[0], scores[1],
             scores[2], static_cast<unsigned long>(cycles));
    } else {
        abortReplay("COMMAND");
    }
}

struct LineReader {
    char data[160];
    unsigned used = 0;
    bool discard = false;
    uint32_t lastByte = 0, seenErrors = 0;

    void service() {
        if (replay.active && HAL_GetTick() - replay.lastActivity > 4000) abortReplay("TIMEOUT");
        if (used && HAL_GetTick() - lastByte > 1000) {
            used = 0;
            discard = true;
            abortReplay("PARTIAL");
        }
        uint8_t byte;
        for (unsigned n = 0; n < rxCapacity; ++n) {
            if (seenErrors != rxErrors) {
                // Bytes before/after a UART loss must never form one command.
                taskENTER_CRITICAL();
                rxTail = rxHead;
                seenErrors = rxErrors;
                taskEXIT_CRITICAL();
                discard = true;
                used = 0;
                abortReplay("RX");
            }
            if (!popByte(byte)) break;
            lastByte = HAL_GetTick();
            if (byte == '\n') {
                if (used && !discard) {
                    if (data[used-1]=='\r') --used;
                    data[used] = 0;
                    command(data);
                }
                discard = false;
                used = 0;
            } else if (!discard) {
                if (used+1 >= sizeof(data) || (byte!='\r' && (byte<32 || byte>126))) {
                    discard = true;
                    used = 0;
                    abortReplay("LINE");
                } else data[used++] = static_cast<char>(byte);
            }
        }
    }
};
}

extern "C" void HAL_UART_RxCpltCallback(UART_HandleTypeDef *uart) {
    if (uart != &huart1) return;
    uint16_t next = (rxHead + 1U) % rxCapacity;
    if (next == rxTail) ++rxErrors;
    else {
        rxRing[rxHead] = incoming;
        __DMB();
        rxHead = next;
    }
    rearmRx();
}

extern "C" void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart) {
    if (uart == &huart1) { ++rxErrors; rearmRx(); }
}

extern "C" void motion_start_console() {
    LineReader reader{};
    rearmRx();
    send("#motion-v1,sample_hz=50,accel_g=8,gyro_dps=1000,model=%s", motion_model_id());
    for (;;) {
        taskENTER_CRITICAL();
        rearmRx();
        taskEXIT_CRITICAL();
        reader.service();
        Sample sample;
        if (xQueueReceive(sampleQueue, &sample, pdMS_TO_TICKS(1)) == pdPASS) onSample(sample);
    }
}
