#pragma once
#include <cstdint>
using StackType_t = uint32_t;
using UBaseType_t = unsigned;
inline void taskENTER_CRITICAL() {}
inline void taskEXIT_CRITICAL() {}
constexpr int pdPASS=1;
constexpr unsigned pdMS_TO_TICKS(unsigned ms) {return ms;}
unsigned xPortGetMinimumEverFreeHeapSize();
