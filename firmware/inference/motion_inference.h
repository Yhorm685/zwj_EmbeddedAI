#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool motion_model_ready(void);
const char *motion_model_id(void);
/* All output pointers are required. Scores are signed logits in model output units. */
bool motion_predict(const int16_t raw[100][6], int8_t input_q[24],
                    int8_t scores[3], uint32_t *network_cycles);
bool motion_predict_q(const int8_t input_q[24], int8_t scores[3],
                      uint32_t *network_cycles);

#ifdef __cplusplus
}
#endif
