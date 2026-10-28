#include "cd_audio.h"
#include "cd_audio_state.h"
#include <limits.h>
#include <string.h>
namespace p6_cd {
bool AudioPlayer::capture(AudioPlayerState *out) const
{
    if (!out) return false;
    AudioPlayerState s{};
    if (!p6_cda::capture_runtime(stream_, pcm_, 588, &s.stream, &s.streamid)) return false;
    s.delay = delay_; memcpy(s.pcm, pcm_, sizeof pcm_);
    s.bound = disc_ != nullptr;
    if (disc_) memcpy(s.disc_id, disc_->layout().content_id, sizeof s.disc_id);
    s.running = running_; s.sector = sector_;
    s.callbacks = (status_callback_ ? 1u : 0u) | (sub_callback_ ? 2u : 0u);
    s.position = position_; s.end = end_; s.left = left_; s.right = right_;
    s.status = status_; s.error = int32_t(error_);
    if (!can_restore(s, disc_, status_callback_, sub_callback_)) return false;
    *out = s; return true;
}
bool AudioPlayer::validate(const AudioPlayerState &s, const Disc *disc, uint32_t period)
{
    const uint64_t max_delay = (uint64_t(INT_MAX) * 44100 + 999) / 1000 + uint64_t(INT_MAX) * 588;
    if (s.bound > 1 || s.running > 1 || s.sector > 1 || s.callbacks > 3
        || s.left > 65535 || s.right > 65535 || s.delay > max_delay || s.position > s.end || s.end > INT_MAX
        || s.error < int(Result::Ok) || s.error > int(Result::Unsupported) || s.stream.userdata
        || s.streamid > p6_cda::kMaxStreams || bool(s.streamid) != bool(s.stream.active)
        || !p6_cda::validate(s.stream, 588, period)) return false;
    if (!s.bound) {
        const uint8_t empty[32]{};
        return !disc && !s.callbacks && !memcmp(s.disc_id, empty, sizeof empty) && !s.running && !s.sector
            && !s.delay && s.status == AUDIO_STATUS_NO_STATUS && s.error == int(Result::Ok) && !s.stream.active;
    }
    if (!disc || memcmp(s.disc_id, disc->layout().content_id, sizeof s.disc_id)
        || !s.end || s.end > disc->layout().leadout || (s.stream.paused && !s.running)) return false;
    if (s.running || s.status == AUDIO_STATUS_PLAY_COMPLETE) {
        SectorInfo sector;
        const uint32_t lba = s.status == AUDIO_STATUS_PLAY_COMPLETE ? s.position - 1 : s.position;
        if (describe_sector(*disc, lba, &sector) != Result::Ok || (sector.effective.control & 4)) return false;
    }
    switch (s.status) {
    case AUDIO_STATUS_NOT_SUPPORTED: // WAIT: last queued silence may have exhausted delay already.
        if (!s.running || s.sector || !s.stream.active || !s.stream.remaining || s.error != int(Result::Ok)) return false;
        for (uint32_t i = 0; i < (s.stream.offset + s.stream.remaining) * 2; ++i) if (s.pcm[i]) return false;
        return s.position < s.end;
    case AUDIO_STATUS_IN_PROGRESS:
        return s.running && s.sector && !s.delay && s.error == int(Result::Ok) && s.position < s.end
            && s.stream.active && s.stream.remaining && s.stream.offset + s.stream.remaining == 588;
    case AUDIO_STATUS_PLAY_COMPLETE:
        return !s.running && s.sector && !s.delay && s.error == int(Result::Ok) && s.position == s.end
            && (!s.stream.active || (s.stream.offset == 588 && !s.stream.remaining));
    case AUDIO_STATUS_PLAY_ERROR:
        return !s.running && !s.sector && s.error != int(Result::Ok) && s.position < s.end
            && (!s.stream.active || (s.stream.offset && !s.stream.remaining));
    default: return false;
    }
}
bool AudioPlayer::can_restore(const AudioPlayerState &s, const Disc *disc,
                              play_status_callback status, play_subchannel_callback sub) const
{
    return validate(s, disc, s.stream.period) && s.callbacks == ((status ? 1u : 0u) | (sub ? 2u : 0u)) &&
        p6_cda::can_restore_runtime(stream_, pcm_, 588, s.stream, s.streamid);
}
bool AudioPlayer::restore(const AudioPlayerState &s, const Disc *disc,
                          play_status_callback status, play_subchannel_callback sub)
{
    if (!can_restore(s, disc, status, sub)
        || !p6_cda::restore_runtime(stream_, pcm_, 588, s.stream, s.streamid, consumed, this)) return false;
    // No failure or reentry remains after the stream preflight; PCM is owned here.
    memcpy(pcm_, s.pcm, sizeof pcm_); disc_ = disc;
    status_callback_ = status; sub_callback_ = sub;
    position_ = s.position; end_ = s.end; delay_ = s.delay;
    left_ = uint16_t(s.left); right_ = uint16_t(s.right);
    status_ = s.status; error_ = Result(s.error); running_ = s.running; sector_ = s.sector;
    return true;
}
AudioPlayer::~AudioPlayer()
{
    // Teardown does not run the scheduler or read another sector.
    if (stream_.cda_streamid > 0) audio_cda_new_buffer(&stream_, nullptr, -1, 0, nullptr, nullptr);
}
Result AudioPlayer::fail(Result reason)
{
    running_ = false; status_ = AUDIO_STATUS_PLAY_ERROR; error_ = reason;
    if (status_callback_) status_callback_(status_, int(position_));
    return reason;
}
Result AudioPlayer::queue(uint32_t lba)
{
    sector_ = false;
    SectorInfo sector;
    const auto described = describe_sector(*disc_, lba, &sector);
    if (described != Result::Ok) return fail(described);
    if (sector.effective.control & 4) return fail(Result::Unsupported);
    if (delay_) {
        const auto frames = uint32_t(delay_ < 588 ? delay_ : 588);
        memset(pcm_, 0, frames * 4); delay_ -= frames;
        audio_cda_new_buffer(&stream_, pcm_, int(frames), 0, consumed, this);
        return Result::Ok;
    }
    uint8_t raw[2448], interleaved[96]{};
    const auto result = canonical_read(*disc_, lba, 2448, raw, sizeof raw);
    if (result != Result::Ok) return fail(result); // Keep the last published LBA and PCM.
    for (unsigned n = 0; n < 1176; ++n) {
        const int sample = int(raw[n * 2]) | (int(raw[n * 2 + 1]) << 8);
        pcm_[n] = int16_t(sample < 32768 ? sample : sample - 65536);
    }
    sub_to_interleaved(raw + 2352, interleaved);
    position_ = lba; sector_ = true; status_ = AUDIO_STATUS_IN_PROGRESS;
    audio_cda_new_buffer(&stream_, pcm_, 588, 0, consumed, this);
    if (status_callback_) status_callback_(status_, int(position_));
    if (sub_callback_) sub_callback_(interleaved, 1);
    return Result::Ok;
}
void AudioPlayer::consumed(int, void *context)
{
    auto &p = *static_cast<AudioPlayer *>(context);
    if (!p.running_) return;
    const uint32_t next = p.position_ + (p.sector_ ? 1 : 0);
    if (next == p.end_) {
        p.running_ = false; p.position_ = next; p.status_ = AUDIO_STATUS_PLAY_COMPLETE;
        if (p.status_callback_) p.status_callback_(p.status_, int(next));
        // Keep the last sample for its full interval; the real stream retires
        // itself on the next event when it observes length0 (I1).
    } else p.queue(next);
}
Result AudioPlayer::play(const Disc &disc, uint32_t start, uint32_t end,
                         play_status_callback status, play_subchannel_callback sub)
{
    if (start >= end || end > disc.layout().leadout || end > INT_MAX) return Result::Range;
    p6_cda::State current;
    if (!p6_cda::capture(stream_, pcm_, 588, &current)) return Result::Invalid;
    const int ms = status ? status(-1, -1) : 0, sectors = status ? status(-2, -1) : 0;
    if (ms < 0 || sectors < 0) return Result::Invalid;
    stop(); disc_ = &disc; position_ = start; end_ = end;
    status_callback_ = status; sub_callback_ = sub; running_ = true;
    delay_ = (uint64_t(ms) * 44100 + 999) / 1000 + uint64_t(sectors) * 588;
    audio_cda_volume(&stream_, left_, right_);
    if (delay_) { status_ = AUDIO_STATUS_NOT_SUPPORTED; if (status) status(status_, int(start)); }
    const auto result = queue(start);
    if (result == Result::Ok && status) status(-3, -1);
    return result;
}
Result AudioPlayer::pause(bool paused)
{
    update_audio();
    return running_ && p6_cda::pause(stream_, paused) ? Result::Ok : Result::Invalid;
}
void AudioPlayer::stop()
{
    update_audio();
    if (stream_.cda_streamid > 0) audio_cda_new_buffer(&stream_, nullptr, -1, 0, nullptr, nullptr);
    disc_ = nullptr; running_ = sector_ = false; delay_ = 0;
    status_callback_ = nullptr; sub_callback_ = nullptr;
    status_ = AUDIO_STATUS_NO_STATUS; error_ = Result::Ok;
}
uint32_t AudioPlayer::volume(uint16_t left, uint16_t right)
{
    update_audio();
    const uint32_t previous = uint32_t(right_) << 16 | left_;
    left_ = left; right_ = right; audio_cda_volume(&stream_, left_, right_);
    return previous;
}
}
