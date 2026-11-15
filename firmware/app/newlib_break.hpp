#pragma once
#include <cstddef>
#include <cstdint>

// The board wrapper serializes updates and supplies the reserved MSP boundary.
struct NewlibBreak {
    uintptr_t bytes = 0;
    uintptr_t peak = 0;
    bool move(ptrdiff_t delta, uintptr_t capacity) {
        if (delta >= 0) {
            uintptr_t grow = static_cast<uintptr_t>(delta);
            if (bytes > capacity || grow > capacity - bytes) return false;
            bytes += grow;
            if (peak < bytes) peak = bytes;
        } else {
            uintptr_t shrink = static_cast<uintptr_t>(-(delta + 1)) + 1;
            if (shrink > bytes) return false;
            bytes -= shrink;
        }
        return true;
    }
};
