// Compile the firmware console unchanged with a simulated UART and deterministic model.
#include "../../app/console.cpp"
#include <cassert>
#include <string>
#include <vector>
#include <iostream>

namespace {
std::vector<std::string> sent;
CycleCounter cycleCounter{};
uint32_t nowMs;
bool ready;
uint8_t *rxDestination;
int netRuns;
}
CycleCounter *DWT=&cycleCounter;
UART_HandleTypeDef huart1{};
uint32_t SystemCoreClock=168000000;
QueueHandle_t sampleQueue=nullptr;
volatile uint32_t queueDrops=0;
uint32_t mainStackFreeMinimum() { return 800; }
unsigned xPortGetMinimumEverFreeHeapSize() {return 7000;}
int xQueueReceive(QueueHandle_t, void*, unsigned) {return 0;}
unsigned uxTaskGetSystemState(TaskStatus_t*, unsigned, void*) {return 0;}
uint32_t HAL_GetTick() {return nowMs;}
int HAL_UART_Receive_IT(UART_HandleTypeDef*, uint8_t *byte, unsigned) {
    rxDestination=byte;
    return HAL_OK;
}
int HAL_UART_Transmit(UART_HandleTypeDef*, uint8_t *data, unsigned len, unsigned) {
    sent.emplace_back(reinterpret_cast<char*>(data),len);
    return HAL_OK;
}
extern "C" bool motion_model_ready() {return ready;}
extern "C" const char *motion_model_id() {return ready ? "fixture" : "none";}
extern "C" bool motion_predict_q(const int8_t q[24], int8_t scores[3], uint32_t *cycles) {
    if (!ready) return false;
    ++netRuns;
    scores[0]=q[0]; scores[1]=127; scores[2]=127;
    *cycles=18; DWT->CYCCNT+=18;
    return true;
}
extern "C" bool motion_predict(const int16_t raw[100][6], int8_t q[24],
                               int8_t scores[3], uint32_t *cycles) {
    std::fill_n(q,24,0);
    q[0]=static_cast<int8_t>(raw[0][0]);
    q[1]=static_cast<int8_t>(raw[99][5]);
    DWT->CYCCNT+=60;
    return motion_predict_q(q,scores,cycles);
}

namespace {
LineReader reader{};
void feed(const std::string &line) {
    rearmRx();
    for (char c : line) { *rxDestination=static_cast<uint8_t>(c); HAL_UART_RxCpltCallback(&huart1); }
    reader.service();
}
void expect(const std::string &line) {assert(!sent.empty() && sent.back()==line+"\r\n");}
void live(uint32_t seq, uint32_t ms, SensorStatus status=SensorStatus::ok) {
    Sample s{seq,ms,{static_cast<int16_t>(seq%100),0,0,0,0,1},status};
    onSample(s);
}
}

int main() {
    feed("STATUS\r\n");
    assert(sent.back().find("INFO,none,168000000,heap_min_free=7000,")==0);
    feed("BEGIN,2\n"); expect("ERR,2,NO_MODEL");
    ready=true;
    feed("BEGIN,4294967296\n"); expect("ERR,0,ID");
    feed("BEGIN,4294967295\n"); expect("READY,4294967295");
    feed("D,0,0,0,0,0,32768\n"); expect("ERR,4294967295,ROW");
    feed("BEGIN,8\nEND,8\n"); expect("ERR,8,END");
    feed("BEGIN,3\n"); nowMs+=4001; reader.service(); expect("ERR,3,TIMEOUT");
    feed("BEGIN,5\nD,0"); nowMs+=1001; reader.service(); expect("ERR,5,PARTIAL");
    feed("\n");
    feed("BEGIN,9\n");
    for (unsigned i=1;i<=100;++i) {
        feed("D,-4,0,32767,-32768,0,11\r\n"); expect("ACK,9,"+std::to_string(i));
    }
    feed("END,9\n");
    assert(sent[sent.size()-2].find("Q,9,-4,11,0,")==0);
    expect("R,9,1,-4,127,127,18,78");
    std::string q="I,7,-128";
    for (unsigned i=1;i<24;++i) q+=",127";
    feed(q+"\n"); expect("J,7,-128,127,127,18");
    feed(q+",0\n"); expect("ERR,0,INPUT");
    feed("BEGIN,2\n"); feed(std::string(300,'X')); expect("ERR,2,RX"); feed("\n");
    const int first=netRuns;
    uint32_t seq=UINT32_MAX-50, ms=UINT32_MAX-200;
    for (unsigned i=0;i<99;++i) {live(seq++,ms); ms+=20;}
    assert(netRuns==first);
    live(seq++,ms); ms+=20;
    assert(netRuns==first+1);
    for (unsigned i=0;i<25;++i) {live(seq++,ms); ms+=20;}
    assert(netRuns==first+2);
    ++seq; ms+=30; // Lost sample and long time gap reset the live window.
    for (unsigned i=0;i<99;++i) {live(seq++,ms); ms+=20;}
    assert(netRuns==first+2);
    live(seq++,ms); ms+=20; assert(netRuns==first+3);
    live(seq++,ms,SensorStatus::i2c); ms+=20;
    for (unsigned i=0;i<99;++i) {live(seq++,ms); ms+=20;}
    assert(netRuns==first+3);
    std::cout<<"Console replay / framing / integer input / live-window checks passed\n";
}
