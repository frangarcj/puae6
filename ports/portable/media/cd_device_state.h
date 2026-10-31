// Private component value state; no addresses or machine codec/wire layout.
#pragma once
#include "cd_audio_state.h"
namespace p6_cd {
class Disc;
struct AtapiState;
struct DeviceState {
    AudioPlayerState audio;
    uint8_t disc_id[32]; // Verified by owner, including TOC and every source.
    uint32_t bound, inserted, opens, position, audio_head, terminal_ack;
    int32_t error; // Device command error, distinct from asynchronous player error.
};
// Value-only validation; no open device/player/stream is required.
bool device_validate(const DeviceState &, const Disc *, uint32_t period);
// The MMC contingent sense/unit-attention state owned by uaescsi.device. It is
// separate from the IDE transport's per-drive AtapiState, but names the same
// selected immutable Disc and is restored only after that owner validates.
bool uaescsi_backend_capture(AtapiState *);
bool uaescsi_backend_validate(const AtapiState &, const DeviceState &);
bool uaescsi_backend_restore(const AtapiState &, const DeviceState &);
}
