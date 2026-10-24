// Actual audio.cpp coefficient path and independent, extracted upstream wrapper.
#include <cassert>
#include <cfenv>
#include <cstdint>
#include <cstring>
#include "upstream_audio_math.h"
#include "audio.cpp"

template<class T> static uint64_t float_bits(T value)
{
    uint64_t result = 0;
    std::memcpy(&result, &value, sizeof(value));
    return result;
}

static double value(uint64_t raw)
{
    double result;
    std::memcpy(&result, &raw, sizeof(result));
    return result;
}

static void compare(uint64_t raw)
{
    const double input = value(raw);
    const int mode = std::fegetround();
    assert(std::fesetround(FE_TONEAREST) == 0);
    const uint64_t expected = float_bits(original::softfloat_tan(input));
    assert(std::fesetround(mode) == 0);
    assert(float_bits(softfloat_tan(input)) == expected);
    // Interleaving other calls cannot carry rounding or exceptions into this one.
    softfloat_tan(value(0x7ff0000000000000ULL));
    softfloat_tan(-1.0);
    assert(float_bits(softfloat_tan(input)) == expected);
}

int main()
{
    original::init(); // Same fs initialization as fp_init_native, no FPU dispatch.
    assert(float_bits(softfloat_tan(0.0)) == 0);
    assert(float_bits(softfloat_tan(-0.0)) == 0x8000000000000000ULL);
    for (int rate : {44100, 48000}) {
        for (int cutoff : {6200, 7000, 20000}) {
            const float actual = rc_calculate_a0(rate, cutoff);
            assert(actual > 0.0f && actual < 1.0f);
            assert(float_bits(actual) == float_bits(original::rc_calculate_a0(rate, cutoff)));
        }
        assert(rc_calculate_a0(rate, rate / 2) == 1.0f);
        assert(rc_calculate_a0(rate, rate) == 1.0f);
    }
    const uint64_t special[] = {
        0, 1, 0x000fffffffffffffULL, 0x0010000000000000ULL,
        0x3ff0000000000000ULL, 0x3ff8000000000000ULL,
        0x7ff0000000000000ULL, 0x7ff8000000000123ULL,
        0x7ff0000000000123ULL
    };
    // Integer SoftFloat must ignore the host FP rounding environment.
    const int saved = std::fegetround();
    for (int mode : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
        assert(std::fesetround(mode) == 0);
        for (uint64_t raw : special) {
            compare(raw);
            compare(raw | 0x8000000000000000ULL);
        }
        // Sweep the filter input interval, including both FPSP parity branches.
        for (uint64_t i = 1; i <= 4096; ++i) {
            const uint64_t raw = 0x3f90000000000000ULL + i * 0x68000000000ULL;
            compare(raw);
            compare(raw | 0x8000000000000000ULL);
        }
    }
    assert(std::fesetround(saved) == 0);
}
