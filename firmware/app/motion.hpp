#pragma once
#include "FreeRTOS.h"
#include "queue.h"
#include <cstdint>

enum class SensorStatus : uint8_t { ok, offline, i2c, noData };
struct Sample {
    uint32_t seq;
    uint32_t ms;
    int16_t axes[6];
    SensorStatus status;
};

extern QueueHandle_t sampleQueue;
extern volatile uint32_t queueDrops;
uint32_t mainStackFreeMinimum();
uint32_t newlibHeapPeak();
