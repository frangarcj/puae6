#define P6_CD_PLAYER_FIXTURE_ONLY
#include "cd_audio_probe.cpp"
#include "media/cd_device.h"
static void bind(Fixture &f)
{
    assert(device_bind(&f.disc) == Result::Ok && sys_command_open(0) == 1);
}
static void device_controls()
{
    setup(); Fixture f; bind(f); assert(sys_command_open(0) == -1);
    assert(sys_command_cd_play(0, 0, 2, 0, status_callback, sub_callback));
    assert(ready_count == 1 && subs.size() == 1);
    uint8_t q[96];
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_IN_PROGRESS);
    assert(q[4] == uint8_t(12 * 17)); // Stored Q claims data; classification still uses metadata.
    advance(int(cda_evt * 100 + CYCLE_UNIT)); const auto sound = pcm();
    assert(sound[sound.size() - 2] == 0x1234 && sound.back() == -0x2345);
    assert(sys_command_cd_pause(0, 1) == 0 && sys_command_cd_pause(0, 1) == 1);
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_PAUSED);
    const auto calls = f.provider.calls;
    advance(int(cda_evt * 20)); pcm(); assert(device_position() == 0 && f.provider.calls == calls);
    assert(sys_command_cd_pause(0, 0) == 1);
    assert(sys_command_cd_volume(0, 0, 0x7fff) == 0x7fff7fffu);
    advance(int(cda_evt * 10)); const auto muted = pcm();
    assert(muted[muted.size() - 2] == 0 && muted.back() == -0x2345);
    sys_command_close(0); assert(sys_command_isopen(0) == 1 && audio_total_extra_streams == 1);
    sys_command_close(0); assert(!sys_command_isopen(0) && !audio_total_extra_streams);
    const auto stopped = f.provider.calls; advance(int(cda_evt * 20)); pcm(); assert(f.provider.calls == stopped);
    device_unbind(); bind(f);
    assert(sys_command_cd_volume(0, 0x7fff, 0x7fff) == 0x7fff7fffu);
    sector_delay = 1; assert(sys_command_cd_play(0, 0, 1, 0, status_callback, nullptr));
    device_unbind(); assert(!audio_total_extra_streams && !sys_command_isopen(0));
}
static void device_terminal()
{
    setup(); Fixture f; bind(f); uint8_t q[96];
    assert(sys_command_cd_play(0, 0, 2, 0));
    advance(int(cda_evt * 1176)); pcm();
    assert(device_position() == 1);
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_PLAY_COMPLETE && q[4] == uint8_t(12 * 17 + 1));
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_NO_STATUS);
    assert(sys_command_cd_play(0, 0, 1, 0)); advance(int(cda_evt * 588)); pcm();
    assert(sys_command_cd_qcode(0, q, -1, true));
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_NO_STATUS);
    assert(!sys_command_cd_play(0, 0, 2, 1, status_callback, nullptr) && device_last_error() == Result::Unsupported);
    assert(!sys_command_cd_play(0, -1, 2, 0) && device_last_error() == Result::Range);
    f.provider.fail = 1; notices.clear(); subs.clear();
    assert(sys_command_cd_play(0, 0, 2, 0, status_callback, sub_callback));
    advance(int(cda_evt * 588)); pcm();
    assert(device_position() == 0 && device_last_error() == Result::ReadError && subs.size() == 1);
    f.provider.fail = 0; memset(q, 0xA5, sizeof q);
    assert(!sys_command_cd_qcode(0, q, -1, false)); for (auto v : q) assert(v == 0xA5);
    f.provider.fail = -1;
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_PLAY_ERROR);
    assert(sys_command_cd_qcode(0, q, -1, true)); // Successful all-sub query also acknowledges terminals.
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_NO_STATUS);
    sys_command_cd_stop(0); assert(device_position() == 0 && !audio_total_extra_streams);
    device_unbind();
}
static bool zeros(const void *, uint64_t, void *out, size_t bytes, size_t *got)
{
    memset(out, 0, bytes); *got = bytes; return true;
}
static void pregap()
{
    setup(); Disc disc; Layout layout{}; layout.tracks = 2; layout.leadout = 304; layout.content_id[0] = 9;
    layout.track[0] = {0, 0, 2, 0, 2352, 0, Mode::Audio, 1, 0, true};
    layout.track[1] = {2, 303, 304, 1, 2048, 0, Mode::Mode1_2048, 1, 4, false};
    Binding source[2] = {{{{1}, 4704, 1}, zeros, nullptr}, {{{2}, 2048, 1}, zeros, nullptr}};
    assert(disc.bind(layout, source, 2) == Result::Ok);
    SectorInfo audio{}, data{};
    assert(describe_sector(disc, 152, &audio) == Result::Ok && !(audio.effective.control & 4) && audio.audio_gap);
    assert(describe_sector(disc, 153, &data) == Result::Ok && (data.effective.control & 4) && !data.audio_gap);
    assert(describe_sector(disc, 0, nullptr) == Result::Invalid);
    assert(device_bind(&disc) == Result::Ok && sys_command_open(0) == 1);
    assert(sys_command_cd_play(0, 152, 154, 0)); uint8_t q[96];
    assert(sys_command_cd_qcode(0, q, -1, false) && q[4] == 0x01 && q[5] == 2 && q[6] == 0 && q[9] == 1);
    assert(sys_command_cd_qcode(0, q, 153, false) && q[4] == 0x41 && q[9] == 0);
    advance(int(cda_evt * 588)); for (auto v : pcm()) assert(v == 0);
    assert(device_position() == 152 && device_last_error() == Result::Unsupported);
    assert(sys_command_cd_qcode(0, q, -1, false) && q[1] == AUDIO_STATUS_PLAY_ERROR && q[4] == 0x01);
    device_unbind();
}
int main() { device_controls(); device_terminal(); pregap(); }
