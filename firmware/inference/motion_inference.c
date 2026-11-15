#include "motion_inference.h"

#ifdef MOTION_HAS_MODEL
#include "motion_parameters.h"
#include "arm_nnfunctions.h"
#include <math.h>
#include <stddef.h>

_Static_assert(sizeof(motion_w1) == 24 * 16 && sizeof(motion_w2) == 16 * 3,
               "Expected two row-major int8 weight matrices");
_Static_assert(sizeof(motion_b1) == 16 * sizeof(int32_t) &&
               sizeof(motion_b2) == 3 * sizeof(int32_t), "Expected int32 biases");
_Static_assert(sizeof(motion_feature_mean) == 24 * sizeof(float) &&
               sizeof(motion_feature_std) == 24 * sizeof(float), "Expected 24 normalization values");

bool motion_model_ready(void) { return true; }
const char *motion_model_id(void) { return MOTION_MODEL_ID; }

/* Called only for finite inputs; explicit nearest-even works independently
   of the process' integer rounding mode and handles negative ties. */
static int8_t quantize(float value)
{
    if (value >= 127.0f) return 127;
    if (value <= -128.0f) return -128;
    int rounded = (int)value;
    const float fraction = fabsf(value - (float)rounded);
    if (fraction > 0.5f || (fraction == 0.5f && rounded % 2 != 0))
        rounded += value < 0.0f ? -1 : 1;
    return (int8_t)rounded;
}

static void preprocess(const int16_t raw[100][6], int8_t input_q[24])
{
    for (int axis = 0; axis < 6; ++axis) {
        const float counts_per_unit = axis < 3 ? 4096.0f : 32.8f;
        float sum = 0.0f, sumsq = 0.0f;
        float minimum = (float)raw[0][axis] / counts_per_unit;
        float maximum = minimum;
        for (int row = 0; row < 100; ++row) {
            const float sample = (float)raw[row][axis] / counts_per_unit;
            sum += sample;
            sumsq += sample * sample;
            if (sample < minimum) minimum = sample;
            if (sample > maximum) maximum = sample;
        }
        const float mean = sum / 100.0f;
        const float mean_square = sumsq / 100.0f;
        const float variance = fmaxf(0.0f, mean_square - mean * mean);
        const float features[4] = {mean, sqrtf(variance), sqrtf(mean_square), maximum - minimum};
        for (int stat = 0; stat < 4; ++stat) {
            const int i = axis * 4 + stat;
            const float normalized = (features[stat] - motion_feature_mean[i]) / motion_feature_std[i];
            input_q[i] = quantize(normalized / motion_input_scale);
        }
    }
}

static uint32_t cycles(void)
{
#if defined(__ARM_ARCH_7EM__) && defined(__thumb__)
    /* Cortex-M4 DWT_CYCCNT. Application enables it before starting the scheduler. */
    __asm__ volatile ("" ::: "memory");
    return *(volatile const uint32_t *)0xE0001004u;
#else
    return 0;
#endif
}

static bool fully_connected(const int8_t *input, int channels, const int8_t *weights,
                            const int32_t *bias, int outputs, int32_t multiplier,
                            int32_t shift, int activation_min, int8_t *result)
{
    const cmsis_nn_context ctx = {0};
    const cmsis_nn_dims in = {1, 1, 1, channels}, out = {1, 1, 1, outputs};
    const cmsis_nn_dims filter = {channels, 1, 1, outputs};
    const cmsis_nn_fc_params fc = {.activation = {activation_min, 127}};
    const cmsis_nn_per_tensor_quant_params quant = {multiplier, shift};
    if (arm_fully_connected_s8_get_buffer_size(&filter) != 0) return false;
    return arm_fully_connected_s8(&ctx, &fc, &quant, &in, input, &filter, weights,
                                  &out, bias, &out, result) == ARM_CMSIS_NN_SUCCESS;
}

bool motion_predict_q(const int8_t input_q[24], int8_t scores[3], uint32_t *network_cycles)
{
    if (!input_q || !scores || !network_cycles) return false;
    int8_t hidden[16];
    const uint32_t start = cycles();
    const bool ok = fully_connected(input_q, 24, motion_w1, motion_b1, 16,
                                    motion_mult1, motion_shift1, 0, hidden)
                 && fully_connected(hidden, 16, motion_w2, motion_b2, 3,
                                    motion_mult2, motion_shift2, -128, scores);
    *network_cycles = cycles() - start;
    return ok;
}

bool motion_predict(const int16_t raw[100][6], int8_t input_q[24],
                    int8_t scores[3], uint32_t *network_cycles)
{
    if (!raw || !input_q || !scores || !network_cycles) return false;
    preprocess(raw, input_q);
    return motion_predict_q(input_q, scores, network_cycles);
}
#else
bool motion_model_ready(void) { return false; }
const char *motion_model_id(void) { return "none"; }
bool motion_predict_q(const int8_t input_q[24], int8_t scores[3], uint32_t *network_cycles)
{
    (void)input_q;
    (void)scores;
    if (network_cycles) *network_cycles = 0;
    return false;
}
bool motion_predict(const int16_t raw[100][6], int8_t input_q[24],
                    int8_t scores[3], uint32_t *network_cycles)
{
    (void)raw;
    return motion_predict_q(input_q, scores, network_cycles);
}
#endif
