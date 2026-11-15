#include "newlib_break.hpp"
#include "main.h"
#include <cerrno>

namespace {
NewlibBreak heap;
extern "C" uint8_t _end;
extern "C" uint8_t _sstack;
}

extern "C" void *__wrap__sbrk(ptrdiff_t delta) {
    const uint32_t mask = __get_PRIMASK();
    __disable_irq();
    const uintptr_t base = reinterpret_cast<uintptr_t>(&_end);
    const uintptr_t stackBase = reinterpret_cast<uintptr_t>(&_sstack);
    const uintptr_t msp = __get_MSP();
    const uintptr_t limit = msp < stackBase ? msp : stackBase;
    const uintptr_t previous = heap.bytes;
    const bool ok = limit >= base && heap.move(delta, limit - base);
    if (!ok) errno = ENOMEM;
    __set_PRIMASK(mask);
    return ok ? reinterpret_cast<void *>(base + previous) : reinterpret_cast<void *>(-1);
}

uint32_t newlibHeapPeak() {
    const uint32_t mask = __get_PRIMASK();
    __disable_irq();
    const uint32_t peak = heap.peak;
    __set_PRIMASK(mask);
    return peak;
}
