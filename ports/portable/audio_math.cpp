// Paula's SoftFloat tangent, derived from WinUAE 6030 fpp_native.cpp.
// Keep binary64 -> extended80 -> tangent -> binary64 and initialized rounding;
// avoid coupling audio to the optional FPU's mutable exception status.
#include <cstdint>
#include <cstring>
#include <limits>
#include "softfloat/softfloat.h"

static_assert(sizeof(double) == sizeof(uint64_t) &&
              std::numeric_limits<double>::is_iec559 &&
              std::numeric_limits<double>::digits == 53 &&
              std::numeric_limits<double>::max_exponent == 1024,
              "Paula audio math requires IEEE binary64");

double softfloat_tan(double value)
{
    float_status status{};
    set_floatx80_rounding_precision(80, &status);
    set_float_rounding_mode(float_round_to_zero, &status);
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    const floatx80 extended = float64_to_floatx80(bits, &status);
    bits = floatx80_to_float64(floatx80_tan(extended, &status), &status);
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}
