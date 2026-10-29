// Private CDA component state. Shared Paula phase/scheduler belong to P6AU/P6SC.
#pragma once
#include <stdint.h>
struct cd_audio_state;
namespace p6_cda {
constexpr uint32_t kMaxFrames = 588;
constexpr uint32_t kMaxRuntimeFrames = 4 * 1152; // One bounded FMV PCM block.
constexpr uint32_t kMaxStreams = 9; // WinUAE AUDIO_CHANNEL_STREAMS, checked in the owner patch.
constexpr unsigned kPhaseWords = 89;
// Shared Paula values, not a CDA codec. Caller supplies candidate scheduler cycle
// before set and restores CDA separately. Only no extras or this exact owner at
// slot0 stereo is accepted. No allocation, I/O, scheduling or hardware advance.
bool phase_get(const cd_audio_state &, int32_t *, unsigned);
bool phase_set(const cd_audio_state &, const int32_t *, unsigned);
struct Channel { int32_t current, previous, accumulator, cycles; };
struct State {
    uint32_t active, paused, frames, offset, remaining, event_cycles, period;
    int32_t userdata, volume[2];
    Channel channel[2];
};
// One CDA stream at slot0, two channels, caller-owned native signed PCM buffer.
// No time advancement, activation, callbacks or scheduling. Capture/set are
// transactional. Restore derives pointers and callbacks from these arguments;
// caller restores shared audio phase and scheduler before advancing again.
bool capture(const cd_audio_state &, const int16_t *, uint32_t frames, State *);
// Value-only validation accepts the verified candidate period, without owners.
bool validate(const State &, uint32_t frames, uint32_t period);
// Pure geometry/ownership/value preflight for aggregate restores.
bool can_restore(const cd_audio_state &, const int16_t *, uint32_t frames, const State &);
bool restore(cd_audio_state &, int16_t *, uint32_t frames, const State &,
             void (*next)(int, void *), void *context);
// FMV-D uses the same value codec with a bounded 4608-frame owner in any
// stereo stream slot. The slot is a value, never an address; callers restore
// CDDA first and then its distinct FMV owner.
bool capture_runtime(const cd_audio_state &, const int16_t *, uint32_t frames,
                     State *, uint32_t *streamid);
bool can_restore_runtime(const cd_audio_state &, const int16_t *, uint32_t frames,
                         const State &, uint32_t streamid);
bool restore_runtime(cd_audio_state &, int16_t *, uint32_t frames, const State &,
                     uint32_t streamid, void (*next)(int, void *), void *context);
// Host presentation gain for one exact owner. It is deliberately absent from
// State: the frontend reapplies its current preference before every run.
// Muting changes samples only; the stream still consumes every deadline.
bool presentation(cd_audio_state &, uint32_t percent);
// Caller first brings audio to the command's emulated cycle with update_audio.
bool pause(cd_audio_state &, bool);
}
