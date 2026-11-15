#include "runtime.h"
#include "motion.hpp"
#include "main.h"

QueueHandle_t sampleQueue;
volatile uint32_t queueDrops;

namespace {
constexpr unsigned queueLength = 32;
StaticQueue_t queueControl;
alignas(4) uint8_t queueStorage[queueLength * sizeof(Sample)];
constexpr uint32_t stackFill = 0xCAE1B4AD;
extern "C" uint32_t _sstack;
extern "C" uint32_t _estack;
}

extern "C" void motion_runtime_init() {
    sampleQueue = xQueueCreateStatic(queueLength, sizeof(Sample), queueStorage, &queueControl);
    if (!sampleQueue) Error_Handler();
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    const uint32_t mask = __get_PRIMASK();
    __disable_irq();
    // Leave the running startup frame and a guard untouched.
    const uintptr_t top = __get_MSP() - 64U;
    for (uintptr_t address = reinterpret_cast<uintptr_t>(&_sstack); address < top; address += 4)
        *reinterpret_cast<volatile uint32_t *>(address) = stackFill;
    __set_PRIMASK(mask);
}

uint32_t mainStackFreeMinimum() {
    const uintptr_t base = reinterpret_cast<uintptr_t>(&_sstack);
    const uintptr_t end = reinterpret_cast<uintptr_t>(&_estack);
    uintptr_t cursor = base;
    while (cursor < end && *reinterpret_cast<volatile uint32_t *>(cursor) == stackFill)
        cursor += 4;
    return cursor - base;
}
