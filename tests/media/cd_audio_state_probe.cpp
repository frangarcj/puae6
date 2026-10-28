#define P6_CD_PLAYER_FIXTURE_ONLY
#include "cd_audio_probe.cpp"
#include "media/cd_audio_state.h"
static int rebound_calls, current_mode = -1;
static void record_sub(uint8_t *bytes, int count)
{
    assert(count == 1); subs.emplace_back(); memcpy(subs.back().data(), bytes, 96);
}
static int rebound_status(int status, int lba) { ++rebound_calls; return status_callback(status, lba); }
static void rebound_sub(uint8_t *bytes, int count) { ++rebound_calls; record_sub(bytes, count); }
static AudioPlayerState snapshot(const AudioPlayer &p)
{
    AudioPlayerState s{}; assert(p.capture(&s)); return s;
}
static void equal(const AudioPlayerState &a, const AudioPlayerState &b)
{
    if (memcmp(&a, &b, sizeof a)) {
        const auto *x = reinterpret_cast<const uint8_t *>(&a), *y = reinterpret_cast<const uint8_t *>(&b);
        for (size_t i = 0; i < sizeof a; ++i) if (x[i] != y[i]) {
            fprintf(stderr, "mode=%d offset=%zu values=%u/%u\n", current_mode, i, x[i], y[i]); break;
        }
        assert(false);
    }
}
static void rejects(AudioPlayer &p, const Disc &disc, const AudioPlayerState &saved)
{
    const auto before = snapshot(p); const auto cycles = get_cycles();
    for (int which = 0; which < 26; ++which) {
        auto s = saved;
        switch (which) {
        case 0: s.bound = 2; break; case 1: s.running = 2; break;
        case 2: s.sector = 2; break; case 3: s.callbacks = 4; break;
        case 4: s.left = 65536; break; case 5: s.right = 65536; break;
        case 6: s.delay = UINT64_MAX; break; case 7: s.position = UINT32_MAX; break;
        case 8: s.end = 5; break; case 9: s.error = 6; break;
        case 10: s.disc_id[31] ^= 1; break; case 11: s.status = AUDIO_STATUS_PAUSED; break;
        case 12: s.stream.offset = 589; break; case 13: s.stream.remaining = 589; break;
        case 14: ++s.stream.period; break; case 15: s.stream.event_cycles = s.stream.period + 1; break;
        case 16: s.stream.channel[0].cycles = -1; break;
        case 17: s.stream.channel[0].accumulator = INT_MAX; break;
        case 18: s.stream.channel[1].current = 32768; break;
        case 19: s.stream.userdata = 1; break; case 20: s.sector = 0; break;
        case 21: s.running = 0; break; case 22: s.position = s.end + 1; break;
        case 23: s.position = 3; s.end = 4; break;
        case 24: s.streamid = p6_cda::kMaxStreams + 1; break;
        case 25: s.streamid = s.stream.active ? 0 : 1; break;
        }
        assert(!p.can_restore(s, &disc, status_callback, record_sub));
        assert(!p.restore(s, &disc, status_callback, record_sub)); equal(snapshot(p), before);
    }
    assert(!p.can_restore(saved, nullptr, status_callback, record_sub));
    assert(!p.restore(saved, &disc, nullptr, record_sub)); equal(snapshot(p), before);
    assert(get_cycles() == cycles);
}
static void future(AudioPlayer &p, int mode)
{
    if (mode == 1) { advance(int(cda_evt * 20)); assert(p.pause(false) == Result::Ok); }
    advance(int(cda_evt * (mode == 1 ? 680 : 700) + 53));
}
static void replay(int mode)
{
    current_mode = mode; setup(); Fixture a, b; AudioPlayer p;
    if (mode == 2 || mode == 3) ms_delay = sector_delay = 1;
    if (mode == 6 || mode == 7) a.provider.fail = b.provider.fail = 1;
    if (mode == 8) a.provider.fail = b.provider.fail = 0;
    const auto initial = p.play(a.disc, 0, mode == 4 || mode == 5 ? 1 : 2, status_callback, record_sub);
    assert(initial == (mode == 8 ? Result::ReadError : Result::Ok));
    if (mode != 8) advance(int(cda_evt * (mode == 3 ? 600 : mode >= 4 ? 588 : 100) + 137));
    if (mode == 1) { assert(p.pause(true) == Result::Ok); advance(int(cda_evt * 3)); }
    if (mode == 5 || mode == 7) advance(int(cda_evt * 2));
    if (mode == 9) p.volume(12345, 0); // Current sample still carries its prior gain.
    pcm(); const auto saved = snapshot(p); const float phase = next_sample_evtime;
    if (mode == 0) rejects(p, a.disc, saved);
    if (mode == 1) assert(saved.stream.paused);
    if (mode == 2) assert(saved.delay && !saved.sector);
    if (mode == 3) assert(!saved.delay && saved.stream.remaining && !saved.sector);
    if (mode == 4 || mode == 6) assert(saved.stream.active && !saved.stream.remaining);
    if (mode == 5 || mode == 7 || mode == 8) assert(!saved.stream.active);
    notices.clear(); subs.clear();
    future(p, mode); const auto control_pcm = pcm();
    const auto control = snapshot(p); const auto control_status = notices; const auto control_subs = subs;
    p.stop(); // Release the old owner before restoring another object in slot0.
    AudioPlayer rebound;
    const auto calls = b.provider.calls; const auto cycles = get_cycles();
    rebound_calls = 0; notices.clear(); subs.clear();
    assert(rebound.can_restore(saved, &b.disc, rebound_status, rebound_sub));
    assert(rebound.restore(saved, &b.disc, rebound_status, rebound_sub));
    assert(b.provider.calls == calls && get_cycles() == cycles && !rebound_calls);
    equal(snapshot(rebound), saved);
    last_cycles = get_cycles(); next_sample_evtime = phase; p6_pcm_reset();
    future(rebound, mode); assert(pcm() == control_pcm);
    equal(snapshot(rebound), control); assert(notices == control_status && subs == control_subs);
    assert(rebound_calls == int(control_status.size() + control_subs.size()));
}
static void bindings()
{
    current_mode = 100; setup(); Fixture a; AudioPlayer p;
    const auto stopped = snapshot(p);
    assert(p.can_restore(stopped, nullptr, nullptr, nullptr));
    assert(p.restore(stopped, nullptr, nullptr, nullptr)); equal(snapshot(p), stopped);
    assert(p.play(a.disc, 0, 2, nullptr, nullptr) == Result::Ok);
    const auto saved = snapshot(p); p.stop();
    AudioPlayer rebound; Layout other = a.disc.layout(); other.content_id[31] = 1;
    Disc alien; Binding binding{{{1}, 4 * 2448, 1}, provide, &a.provider};
    assert(alien.bind(other, &binding, 1) == Result::Ok);
    const auto before = snapshot(rebound);
    assert(!rebound.restore(saved, &alien, nullptr, nullptr)); equal(snapshot(rebound), before);
    assert(rebound.restore(saved, &a.disc, nullptr, nullptr));
    // An inactive Akiko player is still an exact, restorable owner while FMV
    // occupies another Paula stream. Capturing it must not claim that slot.
    assert(p.can_restore(stopped, nullptr, nullptr, nullptr));
    const auto alongside = snapshot(p); assert(!alongside.stream.active && !alongside.streamid);
    assert(p.restore(stopped, nullptr, nullptr, nullptr)); equal(snapshot(p), stopped);
    rebound.stop();
    ms_delay = sector_delay = 1;
    assert(p.play(a.disc, 3, 4, status_callback, record_sub) == Result::Unsupported);
    const auto error = snapshot(p); assert(error.status == AUDIO_STATUS_PLAY_ERROR && error.delay == 633);
    p.stop(); const auto historical = snapshot(p);
    assert(rebound.restore(historical, nullptr, nullptr, nullptr)); equal(snapshot(rebound), historical);
    assert(rebound.restore(error, &a.disc, status_callback, record_sub));
}
static void regional_replay()
{
    using R=Track::Regions;
    for (bool stored : {false,true}) {
        setup(); Provider original_provider, fresh_provider; Disc original_disc,fresh_disc;
        Layout layout{}; layout.content_id[0]=9; layout.tracks=1; layout.leadout=4;
        layout.track[0]={0,2,4,0,2448,0,Mode::Mode1Raw,1,4,stored,stored?0u:2u,0};
        layout.track[0].regions={R::Explicit|R::ProgramSub96|(stored?R::PregapSub96:0),Mode::Audio,0};
        Binding a{{{9},4*2448,1},provide,&original_provider},b=a; b.context=&fresh_provider;
        assert(original_disc.bind(layout,&a,1)==Result::Ok&&fresh_disc.bind(layout,&b,1)==Result::Ok);
        AudioPlayer player; assert(player.play(original_disc,0,4,status_callback,record_sub)==Result::Ok);
        advance(int(cda_evt*100+137)); pcm(); const auto saved=snapshot(player); const auto phase=next_sample_evtime;
        assert(saved.running&&saved.sector&&saved.position==0&&saved.stream.remaining<588);
        notices.clear(); subs.clear(); const auto begin=get_cycles();
        advance(int(cda_evt*1300+53)); const auto elapsed=get_cycles()-begin;
        const auto control_pcm=pcm(); const auto control=snapshot(player); const auto control_status=notices; const auto control_subs=subs;
        assert(control.status==AUDIO_STATUS_PLAY_ERROR&&control.error==int(Result::Unsupported)&&control.position==1);
        assert(original_provider.calls==(stored?2:0)); player.stop();
        AudioPlayer rebound; notices.clear(); subs.clear(); rebound_calls=0; const auto cycles=get_cycles();
        assert(rebound.can_restore(saved,&fresh_disc,rebound_status,rebound_sub));
        assert(rebound.restore(saved,&fresh_disc,rebound_status,rebound_sub));
        assert(!fresh_provider.calls&&get_cycles()==cycles&&!rebound_calls); equal(snapshot(rebound),saved);
        last_cycles=get_cycles(); next_sample_evtime=phase; p6_pcm_reset();
        advance(int(cda_evt*1300+53)); assert(get_cycles()-cycles==elapsed&&pcm()==control_pcm);
        equal(snapshot(rebound),control); assert(notices==control_status&&subs==control_subs);
        assert(fresh_provider.calls==(stored?1:0)); rebound.stop();
        // The inverse region rejects data before consuming delay or touching the source.
        layout.track[0].mode=Mode::Audio; layout.track[0].control=0;
        layout.track[0].regions.pregap_mode=Mode::Mode1Raw; layout.track[0].regions.pregap_control=4;
        Disc inverse; assert(inverse.bind(layout,&b,1)==Result::Ok); AudioPlayer p; ms_delay=sector_delay=1;
        const auto calls=fresh_provider.calls; const auto now=get_cycles();
        assert(p.play(inverse,0,4,status_callback,record_sub)==Result::Unsupported);
        const auto rejected=snapshot(p); assert(rejected.delay==633&&!rejected.sector&&fresh_provider.calls==calls&&get_cycles()==now);
        p.stop(); ms_delay=sector_delay=0;
        assert(p.play(inverse,2,4,nullptr,nullptr)==Result::Ok); p.stop();
    }
}
int main() { for (int mode = 0; mode < 10; ++mode) replay(mode); bindings(); regional_replay(); }
