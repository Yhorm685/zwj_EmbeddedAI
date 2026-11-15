/* Text harness for desktop comparison of exported model headers. */
#include "motion_inference.h"
#include <stdio.h>

int main(void)
{
    printf("%s\n", motion_model_id());
    char mode;
    while (scanf(" %c", &mode) == 1) {
        int8_t input[24], output[3];
        int16_t raw[100][6];
        uint32_t cycles = 0;
        int value;
        bool ok;
        if (mode == 'Q') {
            for (int i = 0; i < 24; ++i) {
                if (scanf("%d", &value) != 1 || value < -128 || value > 127) return 2;
                input[i] = (int8_t)value;
            }
            ok = motion_predict_q(input, output, &cycles);
        } else if (mode == 'W') {
            for (int row = 0; row < 100; ++row)
                for (int axis = 0; axis < 6; ++axis) {
                    if (scanf("%d", &value) != 1 || value < -32768 || value > 32767) return 2;
                    raw[row][axis] = (int16_t)value;
                }
            ok = motion_predict(raw, input, output, &cycles);
        } else return 2;
        if (!ok) {
            if (motion_model_ready()) return 3;
            puts("NO_MODEL");
            continue;
        }
        if (mode == 'W')
            for (int i = 0; i < 24; ++i) printf("%d ", input[i]);
        printf("%d %d %d\n", output[0], output[1], output[2]);
    }
    return 0;
}
