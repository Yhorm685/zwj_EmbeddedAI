#include "runtime.h"
#include "motion.hpp"
#include "i2c.h"
#include "task.h"
#include <cstring>

namespace {
uint16_t address;
uint32_t nextSeq;
constexpr uint32_t ioTimeoutMs = 5;

bool readRegisters(uint8_t reg, uint8_t *data, uint16_t size) {
    return HAL_I2C_Mem_Read(&hi2c1, address, reg, I2C_MEMADD_SIZE_8BIT,
                            data, size, ioTimeoutMs) == HAL_OK;
}

bool configureRegister(uint8_t reg, uint8_t value, uint8_t mask = 0xff) {
    uint8_t observed = 0;
    return HAL_I2C_Mem_Write(&hi2c1, address, reg, I2C_MEMADD_SIZE_8BIT,
                             &value, 1, ioTimeoutMs) == HAL_OK &&
           readRegisters(reg, &observed, 1) && (observed & mask) == value;
}

bool initializeSensor() {
    // Reset the STM32 peripheral after errors / reconnects.
    if (HAL_I2C_DeInit(&hi2c1) != HAL_OK) return false;
    MX_I2C1_Init();
    uint8_t who = 0;
    for (uint16_t candidate = 0x68; candidate <= 0x69; ++candidate) {
        address = candidate << 1;
        if (readRegisters(0x75, &who, 1) && who == 0x68) {
            uint8_t reset = 0x80;
            if (HAL_I2C_Mem_Write(&hi2c1, address, 0x6b, I2C_MEMADD_SIZE_8BIT,
                                  &reset, 1, ioTimeoutMs) != HAL_OK) return false;
            vTaskDelay(pdMS_TO_TICKS(100));
            if (!configureRegister(0x6b, 0x01)) return false;
            vTaskDelay(pdMS_TO_TICKS(40));
            // DLPF_CFG=4: ~20 Hz BW. 1 kHz / (1 + 19) = 50 Hz.
            return configureRegister(0x1a, 4) && configureRegister(0x19, 19)
                && configureRegister(0x1c, 0x10, 0xf8)  // ±8 g
                && configureRegister(0x1b, 0x10, 0xf8)  // ±1000 °/s
                && configureRegister(0x38, 1);
        }
    }
    return false;
}

void publish(Sample &sample) {
    sample.seq = nextSeq++;
    if (xQueueSend(sampleQueue, &sample, 0) != pdPASS) ++queueDrops;
}

void publishError(SensorStatus status) {
    Sample sample{};
    sample.ms = HAL_GetTick();
    sample.status = status;
    publish(sample);
}

int16_t decode(const uint8_t *bytes) {
    uint16_t bits = (static_cast<uint16_t>(bytes[0]) << 8) | bytes[1];
    int16_t result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}
}

extern "C" void motion_start_sampling() {
    for (;;) {
        if (!initializeSensor()) {
            publishError(SensorStatus::offline);
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }
        // Discard the first available frame after changing range/filter settings.
        uint8_t status;
        vTaskDelay(pdMS_TO_TICKS(60));
        if (!readRegisters(0x3a, &status, 1)) {
            publishError(SensorStatus::i2c);
            continue;
        }
        uint32_t lastSampleMs = HAL_GetTick();
        for (;;) {
            // One burst snapshots RAW_DATA_RDY plus accel, temperature and gyro.
            uint8_t frame[15];
            if (!readRegisters(0x3a, frame, sizeof(frame))) {
                publishError(SensorStatus::i2c);
                break;
            }
            uint32_t now = HAL_GetTick();
            if (frame[0] & 1U) {
                Sample sample{};
                sample.ms = now;
                for (unsigned axis = 0; axis < 3; ++axis) {
                    sample.axes[axis] = decode(frame + 1 + 2 * axis);
                    sample.axes[axis + 3] = decode(frame + 9 + 2 * axis);
                }
                publish(sample);
                lastSampleMs = now;
            } else if (now - lastSampleMs > 50) {
                publishError(SensorStatus::noData);
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(2));
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
