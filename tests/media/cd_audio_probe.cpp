#define P6_CDA_FIXTURE_ONLY
#include "../audio/cd_audio_probe.cpp"
#include "media/cd_audio.h"
#include <array>
using namespace p6_cd;
static_assert(sizeof(AudioPlayer) < 2700, "one sector, no disc-sized allocation");
struct Provider { int fail = -1, calls = 0; };
static bool provide(const void *context, uint64_t offset, void *out, size_t length, size_t *got)
{
    auto &p = *const_cast<Provider *>(static_cast<const Provider *>(context));
    assert(length == 2448 && offset % 2448 == 0); ++p.calls;
    const auto lba = int(offset / 2448);
    for (unsigned n = 0; n < length; ++n) {
        const int v = (n % 4 < 2) ? 0x1234 + lba : -0x2345 - lba;
        static_cast<uint8_t *>(out)[n] = n < 2352 ? uint8_t(uint16_t(v) >> (8 * (n % 2))) : uint8_t((n - 2352) * 17 + lba);
    }
    *got = lba == p.fail ? length - 1 : length; return true;
}
struct Fixture {
    Provider provider;
    Disc disc;
    Fixture() {
        Layout layout{}; layout.content_id[0] = 1; layout.tracks = 2; layout.leadout = 4;
        layout.track[0] = {0, 0, 3, 0, 2448, 0, Mode::Audio, 1, 0, true};
        layout.track[1] = {3, 3, 4, 0, 2448, 3 * 2448, Mode::Mode1Raw, 1, 4, true};
        Binding binding{{{1}, 4 * 2448, 1}, provide, &provider};
        assert(disc.bind(layout, &binding, 1) == Result::Ok);
    }
};
static std::vector<std::pair<int, int>> notices;
static std::vector<std::array<uint8_t, 96>> subs;
static int ms_delay, sector_delay, ready_count;
static int status_callback(int status, int lba)
{
    if (status == -1) return ms_delay;
    if (status == -2) return sector_delay;
    if (status == -3) { ++ready_count; return 0; }
    notices.emplace_back(status, lba); return 0;
}
static void sub_callback(uint8_t *bytes, int count)
{
    assert(count == 1); subs.emplace_back(); memcpy(subs.back().data(), bytes, 96);
    const unsigned lba = unsigned(subs.size() - 1);
    for (unsigned channel = 0; channel < 8; ++channel) for (unsigned n = 0; n < 12; ++n) {
        unsigned original = 0;
        for (unsigned bit = 0; bit < 8; ++bit)
            original |= ((bytes[n * 8 + bit] >> (7 - channel)) & 1) << (7 - bit);
        assert(original == uint8_t((channel * 12 + n) * 17 + lba));
    }
}
static void setup()
{
    int16_t dummy[1176]{}; cd_audio_state bootstrap{};
    start(48000, false, bootstrap, dummy);
    audio_cda_new_buffer(&bootstrap, nullptr, -1, 0, nullptr, nullptr);
    notices.clear(); subs.clear(); ms_delay = sector_delay = ready_count = 0;
}
static void controls()
{
    setup(); Fixture f; AudioPlayer p, foreign;
    assert(p.play(f.disc, 0, 2, status_callback, sub_callback) == Result::Ok);
    assert(f.provider.calls == 1 && subs.size() == 1 && ready_count == 1);
    assert(foreign.play(f.disc, 0, 1, status_callback, sub_callback) == Result::Invalid);
    assert(p.play(f.disc, 1, 1, status_callback, sub_callback) == Result::Range);
    advance(int(cda_evt * 100 + CYCLE_UNIT)); const auto sound = pcm();
    assert(sound.size() > 4 && sound[sound.size() - 2] == 0x1234 && sound.back() == -0x2345);
    assert(p.pause(true) == Result::Ok && p.status() == AUDIO_STATUS_PAUSED);
    advance(int(cda_evt * 3)); pcm(); advance(int(cda_evt * 20));
    for (auto v : pcm()) assert(v == 0);
    assert(p.position() == 0 && f.provider.calls == 1 && subs.size() == 1);
    assert(p.pause(false) == Result::Ok);
    assert(p.volume(0, 32768) == 0x80008000u);
    advance(int(cda_evt * 20)); const auto muted = pcm();
    assert(muted[muted.size() - 2] == 0 && muted.back() == -0x2345);
    p.stop(); advance(int(cda_evt * 10));
    for (auto v : pcm()) assert(v == 0);
    assert(p.status() == AUDIO_STATUS_NO_STATUS && f.provider.calls == 1);
    for (auto n : notices) assert(n.first != AUDIO_STATUS_PLAY_COMPLETE);
}
static void delay_and_finish()
{
    setup(); Fixture f; AudioPlayer p; ms_delay = sector_delay = 1; // ceil44.1 +588 =633frames.
    assert(p.play(f.disc, 0, 1, status_callback, sub_callback) == Result::Ok);
    assert(f.provider.calls == 0 && subs.empty() && ready_count == 1);
    advance(int(cda_evt * 588)); for (auto v : pcm()) assert(v == 0);
    assert(f.provider.calls == 0); advance(int(cda_evt * 45));
    for (auto v : pcm()) assert(v == 0);
    assert(f.provider.calls == 1 && subs.size() == 1);
    advance(int(cda_evt * 588)); const auto sound = pcm();
    assert(sound[sound.size() - 2] == 0x1234 && sound.back() == -0x2345);
    assert(p.position() == 1 && p.status() == AUDIO_STATUS_PLAY_COMPLETE);
    advance(int(cda_evt * 20)); pcm();
    unsigned count = 0; for (auto n : notices) if (n.first == AUDIO_STATUS_PLAY_COMPLETE) ++count;
    assert(count == 1 && f.provider.calls == 1 && subs.size() == 1);
}
static void boundaries()
{
    setup(); Fixture f; AudioPlayer p;
    assert(p.play(f.disc, 0, 2, status_callback, sub_callback) == Result::Ok);
    advance(int(cda_evt * 588)); pcm();
    assert(p.position() == 1 && f.provider.calls == 2 && subs.size() == 2);
    advance(int(cda_evt * 588)); const auto sound = pcm();
    assert(sound[sound.size() - 2] == 0x1235 && sound.back() == -0x2346);
    assert(p.position() == 2 && p.status() == AUDIO_STATUS_PLAY_COMPLETE);
    advance(int(cda_evt * 20)); pcm();
    assert(f.provider.calls == 2 && subs.size() == 2);
}
static void errors()
{
    setup(); Fixture f; AudioPlayer p; f.provider.fail = 1;
    assert(p.play(f.disc, 0, 2, status_callback, sub_callback) == Result::Ok);
    advance(int(cda_evt * 588)); const auto tail = pcm();
    assert(tail[tail.size() - 2] == 0x1234 && tail.back() == -0x2345);
    assert(f.provider.calls == 2 && p.position() == 0 && p.error() == Result::ReadError);
    assert(p.status() == AUDIO_STATUS_PLAY_ERROR && subs.size() == 1);
    advance(int(cda_evt * 20)); pcm();
    unsigned errors = 0; for (auto n : notices) if (n.first == AUDIO_STATUS_PLAY_ERROR) ++errors;
    assert(errors == 1);
    p.stop(); f.provider.fail = 0; notices.clear(); subs.clear();
    assert(p.play(f.disc, 0, 1, status_callback, sub_callback) == Result::ReadError);
    assert(p.position() == 0 && subs.empty() && audio_total_extra_streams == 0);
    p.stop(); f.provider.fail = -1; ms_delay = sector_delay = 1;
    assert(p.play(f.disc, 3, 4, status_callback, sub_callback) == Result::Unsupported);
    assert(p.error() == Result::Unsupported && audio_total_extra_streams == 0);
}
#ifndef P6_CD_PLAYER_FIXTURE_ONLY
int main() { controls(); delay_and_finish(); boundaries(); errors(); }
#endif
