// One borrowed immutable disc behind the real WinUAE blkdev signatures.
#pragma once
#include "cd_akiko.h"
namespace p6_cd {
constexpr uint32_t kDeviceMaxBurst = 128;
// Not selected in the machine build yet: replaces the unsupported CD surface
// only when Akiko, CDDA and state integration are ready. Caller owns the Disc.
Result device_bind(const Disc *);
void device_unbind();
Result device_last_error();
uint32_t device_position(); // next sector after the last completed read
}
