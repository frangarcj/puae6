// Private CDA component state. Shared Paula phase/scheduler belong to P6AU/P6SC.
#pragma once
#include <stdint.h>
struct cd_audio_state;
namespace p6_cda {
constexpr uint32_t kMaxFrames = 588;
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
// Caller first brings audio to the command's emulated cycle with update_audio.
bool pause(cd_audio_state &, bool);
}
