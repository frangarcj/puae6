#pragma once
#include "cd_disc.h"
namespace p6_cd {
// Effective type comes from validated metadata, never stored Q (which may be intentionally invalid).
struct SectorInfo { Track effective; uint32_t number; bool stored_subs, pause, audio_gap; };
Result describe_sector(const Disc &, uint32_t lba, SectorInfo *);
// Stored P-W/SBI wins. Otherwise synthesize from validated geometry, including
// the long audio->data pregap control policy and pauses in pregap/postgap.
Result read_subchannel(const Disc &, uint32_t lba, void *out, size_t capacity);
// 2448 is 2352 main-channel bytes +96 P-W deinterleaved. The low-level Disc
// contract is unchanged. Errors preserve output; no fallback after an I/O fault.
Result canonical_read(const Disc &, uint32_t lba, uint32_t view, void *out, size_t capacity);
}
