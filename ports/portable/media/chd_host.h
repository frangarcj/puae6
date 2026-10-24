// Host-only, read-only CDv5 ABI. One owner/thread; no reentry. All outputs are
// transactional. Status:0 Ok,1 Invalid,2 Incompatible,3 Range,4 ReadError,5 Unsupported.
// No decoder structs cross the ABI. The base file must remain immutable until close.
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define P6_CHD_EXPORT __attribute__((visibility("default")))
typedef struct {
    uint32_t version, size, unit_bytes, hunk_bytes, tracks, reserved;
    uint64_t bytes;
} p6_chd_host_info;
typedef struct {
    uint32_t version, size, number, frames, pregap, postgap, extra_frames, stored_pregap, flags, reserved;
    uint64_t offset_frames;
    char type[16], subtype[8], pgtype[16], pgsub[8];
} p6_chd_host_track;
P6_CHD_EXPORT int p6_chd_host_open(const char *utf8_path, void **owner, p6_chd_host_info *info);
P6_CHD_EXPORT int p6_chd_host_get_track(void *owner, uint32_t index, p6_chd_host_track *track);
P6_CHD_EXPORT int p6_chd_host_read(void *owner, uint64_t offset, void *out, uint32_t count);
P6_CHD_EXPORT void p6_chd_host_close(void *owner);
#ifdef __cplusplus
}
#endif
