// State shared by the bridge units: one native instance per loaded image (docs/02-contracts.md).
#ifndef P6_BRIDGE_SESSION_H
#define P6_BRIDGE_SESSION_H
#include "p6.h"
#include <stdint.h>

namespace p6_session {

// Faulted: a unit failed after load (CPU loop left, clock or audio service error). Only unload leaves it.
enum class State { Uninitialized, Initialized, Loaded, Faulted };
enum ResetKind : uint8_t { ResetNone = 0, ResetHard = 1, ResetSoft = 2 };

struct Session {
    State state = State::Uninitialized;
    p6_config config{};
    bool active = false;          // an API call is in progress: calls are not reentrant
    uint64_t unit_index = 0;      // video units returned since load
    uint64_t master_ticks = 0;    // scheduler ticks (CYCLE_UNIT) executed since load
    int64_t cycles_seen = 0;      // engine cycle counter at the end of the previous call
    uint64_t audio_frames_total = 0;  // stereo frames Paula produced since load (delivered or not)
    bool handed_over = false;     // the renderer handed a new picture over during the current unit
    // Media events (docs/adr/0008-media-identity.md): a bounded queue applied at the next boundary, the drive contents the
    // caller asked for (media index, -1 = none; what the guest sees lags behind by the engine's own swap delay), and the
    // last accepted sequence number.
    struct MediaEvent { uint32_t type, drive, media_index; };
    static constexpr uint32_t kMediaQueue = 16;
    MediaEvent media_queue[kMediaQueue] = {};
    uint32_t media_queued = 0;
    int32_t drive_media[4] = {-1, -1, -1, -1};
    uint64_t last_sequence = 0;
    bool have_sequence = false;
    uint8_t reset_queued = ResetNone; // pending reset mode; 0/1 retain their existing serialized meaning
    bool started = false;         // the first p6_run_frame got past validation: the power-on reset head has been reached
    uint8_t firmware_sha256[32] = {};                       // identity of what was loaded (docs/adr/0008-media-identity.md)
    uint32_t media_count = 0;
    uint8_t media_type[P6_MAX_MEDIA] = {};                  // P6_MEDIA_ADF or P6_MEDIA_HDF of each medium
    uint8_t media_format[P6_MAX_MEDIA] = {};                // of a P6_MEDIA_ADF, its p6_media::Format (T57: 0 is the plain ADF, the one with a write overlay); 0 for a hard file
    uint64_t floppy_bytes = 0;                              // the bytes of all the floppy images of the manifest (at most p6_media::kMaxFloppyBytes)
    uint8_t media_content_id[P6_MAX_MEDIA][P6_CONTENT_ID_SIZE] = {};   // in the order of the p6_load array
};

extern Session session;

// Rejects a call made while another is running; the destructor releases it.
struct Guard {
    bool ok;
    Guard() : ok(!session.active) { session.active = true; }
    ~Guard() { if (ok) session.active = false; }
    Guard(const Guard &) = delete;
    Guard &operator=(const Guard &) = delete;
};

}  // namespace p6_session
#endif
