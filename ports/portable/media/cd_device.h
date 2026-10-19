// One borrowed immutable disc behind the real WinUAE blkdev signatures.
#pragma once
#include "cd_akiko.h"
namespace p6_cd {
struct DeviceState;
constexpr uint32_t kDeviceMaxBurst = 128;
// Selected by the native graph; public CD32 profiles stay gated on complete
// state/boot integration. Caller owns the Disc until device_unbind returns.
Result device_bind(const Disc *);
void device_unbind();
Result device_last_error();
uint32_t device_position(); // Read: next sector. Audio: current/last played sector.
// Pure capture/preflight, transactional application; no I/O, scheduling or callbacks.
// Supply the verified Disc iff bound, callbacks iff their saved presence requires it.
bool device_capture(DeviceState *);
bool device_can_restore(const DeviceState &, const Disc *, play_status_callback, play_subchannel_callback);
bool device_restore(const DeviceState &, const Disc *, play_status_callback, play_subchannel_callback);
}
