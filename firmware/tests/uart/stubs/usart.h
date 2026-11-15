#pragma once
#include <cstdint>
struct UART_HandleTypeDef { unsigned RxState; };
struct CycleCounter { uint32_t CYCCNT; };
extern CycleCounter *DWT;
extern UART_HandleTypeDef huart1;
extern uint32_t SystemCoreClock;
constexpr int HAL_UART_STATE_READY=0, HAL_OK=0;
inline void __DMB() {}
uint32_t HAL_GetTick();
int HAL_UART_Receive_IT(UART_HandleTypeDef*, uint8_t*, unsigned);
int HAL_UART_Transmit(UART_HandleTypeDef*, uint8_t*, unsigned, unsigned);
