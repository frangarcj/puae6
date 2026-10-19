// Host-only benchmark argument parsing; no clock or emulator state.
#pragma once
#include <string.h>
#include <stdint.h>
inline bool bench_cpu_options(const char *cpu, const char *accuracy, uint8_t *model, uint8_t *mode)
{
    const char *models[] = {"auto", "68000", "68010", "68020"};
    const char *modes[] = {"exact", "compatible", "normal"};
    for (*model = 0; *model < 4 && strcmp(cpu, models[*model]); ++*model) {}
    for (*mode = 0; *mode < 3 && strcmp(accuracy, modes[*mode]); ++*mode) {}
    return *model < 4 && *mode < 3;
}
