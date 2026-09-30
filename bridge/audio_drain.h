// Audio of the bridge. Until T11 delivers PCM to the caller, the samples Paula produced are dropped after each unit.
#ifndef P6_BRIDGE_AUDIO_DRAIN_H
#define P6_BRIDGE_AUDIO_DRAIN_H

namespace p6_bridge {

// Empties the PCM queue. Paula's filters and phases advance regardless (DET05); only the delivery is dropped. Called after
// every unit, and by the STEP01 observer at each boundary a swallowed call crosses, so the queue never fills.
void discard_audio();

}  // namespace p6_bridge
#endif
