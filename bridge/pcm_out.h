// Audio of the bridge: what Paula produced during a unit, handed to the caller (docs/02-contracts.md, "Audio").
#ifndef P6_BRIDGE_PCM_OUT_H
#define P6_BRIDGE_PCM_OUT_H
#include "p6.h"
#include <stdint.h>

namespace p6_bridge {

// Largest number of stereo frames one call can return: the capacity of the port's PCM queue. It is a bound, not a typical
// size (a unit is 734..962 frames at 44.1/48 kHz); p6_query_caps publishes it as max_audio_frames.
constexpr uint32_t kMaxAudioFrames = 8192;

// Brings Paula up to the tick of the boundary. Paula integrates lazily, up to the last register access or line, so the samples
// of the tail of a unit would otherwise appear in the next one and the count per unit would depend on where the CPU was.
// The engine already does this catch-up at arbitrary instants (every register write), so it changes nothing of the machine.
void sync_audio_to_boundary();

// Drains the PCM queue into the bridge's buffer; returns the number of stereo frames now there (interleaved int16 left/right,
// left = channels 0 and 3, right = 1 and 2). Adds them to the session total whether or not anybody wants them: delivering or
// dropping the samples never changes what Paula does (DET05). On a transport error returns the typed error.
p6_result take_audio(uint64_t *frames, p6_error *error);

// Where take_audio put the samples; valid until the next take_audio.
const int16_t *audio_samples();

}  // namespace p6_bridge
#endif
