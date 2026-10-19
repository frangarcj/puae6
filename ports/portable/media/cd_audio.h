// Private cooperative player; buffer/stream/device codecs remain separate.
#pragma once
#include "sysconfig.h"
#include "sysdeps.h"
#include "audio.h"
#include "audio_cda_state.h"
struct TrapContext;
#include "blkdev.h"
#include "cd_read.h"
namespace p6_cd {
struct AudioPlayerState;
class AudioPlayer {
public:
    AudioPlayer() = default;
    ~AudioPlayer();
    AudioPlayer(const AudioPlayer &) = delete;
    AudioPlayer &operator=(const AudioPlayer &) = delete;
    // Borrowed immutable Disc, callbacks must not reenter. End is exclusive.
    // The subchannel buffer is borrowed only for the callback duration.
    // Delay queries -1(ms)/-2(sectors), readiness -3, then actual status codes.
    Result play(const Disc &, uint32_t start, uint32_t end, play_status_callback, play_subchannel_callback);
    Result pause(bool);
    void stop();
    uint32_t volume(uint16_t left, uint16_t right); // Previous logical L/R gains.
    uint32_t position() const { return position_; }
    int status() const { return stream_.cda_paused && running_ ? AUDIO_STATUS_PAUSED : status_; }
    Result error() const { return error_; }
    bool active() const { return running_; }
    // No I/O, scheduler advance or callbacks. Disc/callback presence is explicit;
    // restored addresses come from the new owner, never from the value state.
    bool phase_get(int32_t *out, unsigned n) const { return p6_cda::phase_get(stream_, out, n); }
    bool phase_set(const int32_t *in, unsigned n) const { return p6_cda::phase_set(stream_, in, n); }
    bool capture(AudioPlayerState *) const;
    static bool validate(const AudioPlayerState &, const Disc *, uint32_t period);
    bool can_restore(const AudioPlayerState &, const Disc *, play_status_callback, play_subchannel_callback) const;
    bool restore(const AudioPlayerState &, const Disc *, play_status_callback, play_subchannel_callback);
private:
    static void consumed(int, void *);
    Result queue(uint32_t lba);
    Result fail(Result);
    const Disc *disc_ = nullptr; // Derived binding, never serialized as an address.
    play_status_callback status_callback_ = nullptr;
    play_subchannel_callback sub_callback_ = nullptr;
    cd_audio_state stream_{};
    int16_t pcm_[1176]{}; // Exactly one sector; temporary raw staging is bounded.
    uint32_t position_ = 0, end_ = 0;
    uint64_t delay_ = 0; // Frames not queued yet; stream holds the queued cursor.
    uint16_t left_ = 32768, right_ = 32768;
    int status_ = AUDIO_STATUS_NO_STATUS;
    Result error_ = Result::Ok;
    bool running_ = false, sector_ = false;
};
}
