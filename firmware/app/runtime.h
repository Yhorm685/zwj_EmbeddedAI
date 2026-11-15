#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
void motion_runtime_init(void);
void motion_start_sampling(void);
void motion_start_console(void);
#ifdef __cplusplus
}
#endif
