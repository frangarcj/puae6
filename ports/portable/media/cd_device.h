// Borrowed immutable CD catalog behind the real WinUAE blkdev signatures.
#pragma once
#include "cd_akiko.h"
namespace p6_cd {
struct DeviceState;
constexpr uint32_t kDeviceMaxBurst = 128;
// Selected by the native graph; public CD32 profiles stay gated on complete
// state/boot integration. Caller owns the Disc until device_unbind returns.
Result device_bind(const Disc *);
constexpr uint32_t kDeviceMaxDiscs = 16; // Same bound as the frontends' media lists.
// Copies only the pointer list, in declared order; every immutable Disc and its
// sources remain borrowed until unbind. IDs must be unique. No I/O or allocation.
Result device_catalog_bind(const Disc *const *, uint32_t count, uint32_t selected = 0);
uint32_t device_catalog_size();
const Disc *device_catalog_disc(uint32_t);
const Disc *device_resolve_disc(const uint8_t id[32]);
// Only while absent: retain the open unit and select an already verified owner.
// Akiko callers eject/reinsert through p6_akiko_media_set at unit boundaries.
Result device_select(uint32_t);
const Disc *device_bound_disc(); // Borrowed binding for identity/preflight, never serialized.
// Private quiescent-boundary primitive. Keep owner/identity/open unit while the
// medium is absent; Akiko callers must also invalidate its transport deliveries.
Result device_set_inserted(bool);
bool device_inserted();
void device_unbind();
Result device_last_error();
uint32_t device_position(); // Read: next sector. Audio: current/last played sector.
// Pure capture/preflight, transactional application; no I/O, scheduling or callbacks.
// Supply the verified Disc iff bound, callbacks iff their saved presence requires it.
bool device_phase_get(int32_t *, unsigned);
bool device_phase_set(const int32_t *, unsigned);
bool device_capture(DeviceState *);
bool device_can_restore(const DeviceState &, const Disc *, play_status_callback, play_subchannel_callback);
bool device_restore(const DeviceState &, const Disc *, play_status_callback, play_subchannel_callback);
}
