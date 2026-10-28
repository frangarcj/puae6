// Private value state, not a wire format. Shared Paula/scheduler state is separate.
#pragma once
#include "audio_cda_state.h"
namespace p6_cd {
struct AudioPlayerState {
    uint64_t delay;
    int16_t pcm[1176];
    uint8_t disc_id[32]; // Owner verifies identity of normalized TOC and all sources.
    p6_cda::State stream;
    uint32_t streamid; // Exact Paula extra-stream slot; zero iff stream is inactive.
    uint32_t bound, running, sector, callbacks; // Presence bits: status=1, sub=2.
    uint32_t position, end, left, right;
    int32_t status, error;
};
}
