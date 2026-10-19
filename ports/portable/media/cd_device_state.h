// Private component value state; no addresses or machine codec/wire layout.
#pragma once
#include "cd_audio_state.h"
namespace p6_cd {
class Disc;
struct DeviceState {
    AudioPlayerState audio;
    uint8_t disc_id[32]; // Verified by owner, including TOC and every source.
    uint32_t bound, opens, position, audio_head, terminal_ack;
    int32_t error; // Device command error, distinct from asynchronous player error.
};
// Value-only validation; no open device/player/stream is required.
bool device_validate(const DeviceState &, const Disc *, uint32_t period);
}
