// Actual stream callbacks, scheduler, accumulator and PCM; no CPU/CD backend.
#include <audio.cpp>
#include <cassert>
int config_changed, config_changed_flags;
#include "upstream_audio_config.h"
#include <vector>
uae_prefs currprefs{}, changed_prefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int savestate_state, vpos;
uae_u16 dmacon, intena, adkcon;
uae_u8 agnus_hpos;
int maxhpos = 227, maxvpos_nom = 312;
regstruct regs{};
void INTREQ_INT(int, int) { abort(); }
uae_u16 INTREQR() { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
static int completed;
static void ended(int tag, void *) { assert(tag == 7); ++completed; }
static void advance(int cycles)
{
    schedule_audio(); events_schedule(); do_cycles_normal(cycles); update_audio();
}
static std::vector<int16_t> pcm()
{
    int16_t out[4096]; size_t frames;
    assert(p6_pcm_read(out, 2048, &frames) == P6_PCM_OK);
    return {out, out + frames * 2};
}
static void start(int rate, bool ntsc, cd_audio_state &cas, int16_t *buffer)
{
    close_sound(); currprefs = {}; cas = {}; completed = 0;
    currprefs.cpu_model = 68000; currprefs.produce_sound = 3;
    currprefs.sound_stereo = SND_STEREO; currprefs.sound_freq = rate; currprefs.ntscmode = ntsc;
    currprefs.sound_stereo_separation = 10; // Isolate CDA L/R, no presentation crossfeed.
    clear_events(); set_cycles(0); events_schedule(); eventtab[ev_audio].handler = audio_evhandler;
    eventtab[ev_misc].handler = MISC_handler;
    assert(setup_sound() == 1); changed_prefs = currprefs;
    audio_reset(); set_audio(); assert(p6_sound_status() == P6_PCM_OK);
    update_cda_sound(ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL);
    sound_cd_volume[0] = sound_cd_volume[1] = 32768;
    audio_cda_volume(&cas, 32768, 32768);
    audio_cda_new_buffer(&cas, buffer, 588, 7, ended, nullptr);
}
#ifndef P6_CDA_FIXTURE_ONLY
static void restore_case(int rate, bool ntsc)
{
    int16_t buffer[1176], rebound[1176];
    for (int n = 0; n < 588; ++n) { buffer[n * 2] = int16_t(1000 + n); buffer[n * 2 + 1] = int16_t(-2000 - n); }
    memcpy(rebound, buffer, sizeof buffer);
    cd_audio_state cas{}; start(rate, ntsc, cas, buffer);
    advance(int(cda_evt * 201 + CYCLE_UNIT + 137)); pcm();
    p6_cda::State saved{};
    assert(p6_cda::capture(cas, buffer, 588, &saved));
    assert(saved.offset == 202 && saved.remaining == 386 && saved.channel[0].cycles > 0);
    int32_t global_phase[p6_cda::kPhaseWords]; const auto cut_cycle = get_cycles();
    assert(p6_cda::phase_get(cas, global_phase, p6_cda::kPhaseWords));
    advance(int(cda_evt * 100 + 53)); const auto control = pcm();
    p6_cda::State future{}; assert(p6_cda::capture(cas, buffer, 588, &future));
    set_cycles(cut_cycle); assert(p6_cda::phase_set(cas, global_phase, p6_cda::kPhaseWords));
    assert(p6_cda::restore(cas, rebound, 588, saved, ended, nullptr)); p6_pcm_reset();
    advance(int(cda_evt * 100 + 53));
    assert(pcm() == control);
    p6_cda::State again{}; assert(p6_cda::capture(cas, rebound, 588, &again));
    assert(memcmp(&again, &future, sizeof again) == 0);
    // Reject every invalid mutation before touching stream, pointers or output.
    for (int which = 0; which < 10; ++which) {
        auto bad = saved;
        switch (which) {
        case 0: bad.active = 2; break; case 1: bad.paused = 2; break;
        case 2: bad.frames = 587; break; case 3: bad.offset = 589; break;
        case 4: bad.remaining = 589; break; case 5: ++bad.period; break;
        case 6: bad.event_cycles = bad.period + 1; break; case 7: bad.volume[0] = 32769; break;
        case 8: bad.channel[0].cycles = -1; break; case 9: bad.channel[0].current = 32768; break;
        }
        assert(!p6_cda::restore(cas, buffer, 588, bad, ended, nullptr));
        assert(p6_cda::capture(cas, rebound, 588, &again) && memcmp(&again, &future, sizeof again) == 0);
    }
    // Paused ticks produce silence while sample cursor and fractional deadline persist.
    assert(p6_cda::pause(cas, true)); advance(int(cda_evt * 3)); pcm();
    assert(p6_cda::capture(cas, rebound, 588, &saved));
    int32_t pause_phase[p6_cda::kPhaseWords]; const auto pause_cycle = get_cycles();
    assert(p6_cda::phase_get(cas, pause_phase, p6_cda::kPhaseWords));
    advance(int(cda_evt * 20)); auto silence = pcm();
    for (auto v : silence) assert(v == 0);
    assert(p6_cda::capture(cas, rebound, 588, &again));
    assert(saved.offset == again.offset && saved.remaining == again.remaining && saved.event_cycles == again.event_cycles);
    const auto paused_future = again;
    set_cycles(pause_cycle); assert(p6_cda::phase_set(cas, pause_phase, p6_cda::kPhaseWords));
    assert(p6_cda::restore(cas, rebound, 588, saved, ended, nullptr)); p6_pcm_reset();
    advance(int(cda_evt * 20)); assert(pcm() == silence);
    assert(p6_cda::capture(cas, rebound, 588, &again) && memcmp(&again, &paused_future, sizeof again) == 0);
    assert(p6_cda::pause(cas, false));
    audio_cda_volume(&cas, 0, 32768); advance(int(cda_evt * 5)); auto mixed = pcm();
    assert(!mixed.empty() && mixed[mixed.size() - 2] == 0 && mixed.back() < -2000);
    assert(p6_cda::capture(cas, rebound, 588, &again) && again.offset == saved.offset + 5);
    p6_pcm_set_delivery(false); advance(int(cda_evt * 7)); p6_pcm_set_delivery(true);
    assert(p6_cda::capture(cas, rebound, 588, &saved) && saved.offset == again.offset + 7);
    memset(&again, 0xA5, sizeof again); const auto canary = again;
    assert(!p6_cda::capture(cas, nullptr, 588, &again));
    assert(!p6_cda::capture(cas, rebound, 589, &again) && memcmp(&again, &canary, sizeof again) == 0);
    // Strict0099 remains closed; owned phase rejects forged owners and streams.
    int32_t phase[p6_cda::kPhaseWords], check[p6_cda::kPhaseWords], sentinel[p6_cda::kPhaseWords];
    assert(p6_cda::phase_get(cas, phase, p6_cda::kPhaseWords));
    memset(sentinel, 0xa5, sizeof sentinel); memcpy(check, sentinel, sizeof check);
    assert(!p6_audio_phase_get(check, p6_cda::kPhaseWords));
    assert(!memcmp(check, sentinel, sizeof check));
    assert(!p6_audio_phase_set(phase, p6_cda::kPhaseWords));
    cd_audio_state forged = cas; // Correct callback/id, wrong owner address.
    assert(!p6_cda::phase_get(forged, check, p6_cda::kPhaseWords));
    assert(!memcmp(check, sentinel, sizeof check));
    assert(!p6_cda::phase_set(forged, global_phase, p6_cda::kPhaseWords));
    assert(!p6_cda::phase_get(cas, nullptr, p6_cda::kPhaseWords));
    assert(!p6_cda::phase_set(cas, nullptr, p6_cda::kPhaseWords));
    for (unsigned n : {0u, p6_cda::kPhaseWords - 1, p6_cda::kPhaseWords + 1}) {
        assert(!p6_cda::phase_get(cas, check, n)); assert(!memcmp(check, sentinel, sizeof check));
        assert(!p6_cda::phase_set(cas, global_phase, n));
    }
    audio_extra_streams[1] = 2; audio_set_extra_channels();
    assert(!p6_cda::phase_get(cas, check, p6_cda::kPhaseWords));
    assert(!p6_cda::phase_set(cas, global_phase, p6_cda::kPhaseWords));
    audio_extra_streams[1] = 0; audio_set_extra_channels();
    const auto callback = audio_stream[0].cb; audio_stream[0].cb = nullptr;
    assert(!p6_cda::phase_get(cas, check, p6_cda::kPhaseWords));
    assert(!p6_cda::phase_set(cas, global_phase, p6_cda::kPhaseWords));
    audio_stream[0].cb = callback;
    for (int kind = 0; kind < 3; ++kind) {
        currprefs.sound_interpol = kind == 0 ? 2 : 0;
        currprefs.sound_volcnt = kind == 1; currprefs.sound_mixed_stereo_delay = kind == 2;
        assert(!p6_cda::phase_get(cas, check, p6_cda::kPhaseWords));
        assert(!p6_cda::phase_set(cas, global_phase, p6_cda::kPhaseWords));
    }
    currprefs.sound_interpol = currprefs.sound_volcnt = currprefs.sound_mixed_stereo_delay = 0;
    assert(!memcmp(check, sentinel, sizeof check));
    assert(p6_cda::phase_get(cas, check, p6_cda::kPhaseWords) && !memcmp(check, phase, sizeof check));
    audio_cda_new_buffer(&cas, nullptr, -1, 0, nullptr, nullptr);
    assert(p6_audio_phase_get(check, p6_cda::kPhaseWords));
    assert(p6_audio_phase_set(check, p6_cda::kPhaseWords));
    assert(p6_cda::phase_get(forged, phase, p6_cda::kPhaseWords) && !memcmp(phase, check, sizeof phase));
    assert(p6_cda::phase_set(forged, phase, p6_cda::kPhaseWords));
    audio_extra_streams[1] = 2; audio_set_extra_channels();
    assert(!p6_cda::capture(cas, rebound, 588, &again));
    assert(!p6_cda::restore(cas, rebound, 588, saved, ended, nullptr));
    audio_extra_streams[1] = 0; audio_set_extra_channels();
    // Final sample and one-past-end survive capture; next event stops without dereference.
    start(rate, ntsc, cas, buffer); advance(int(cda_evt * 587 + CYCLE_UNIT)); pcm();
    assert(completed == 1 && cas.cda_length == 0);
    assert(p6_cda::capture(cas, buffer, 588, &saved) && saved.offset == 588);
    assert(p6_cda::restore(cas, rebound, 588, saved, ended, nullptr));
    advance(int(cda_evt * 2)); assert(completed == 1 && cas.cda_streamid == 0);
    assert(p6_cda::capture(cas, rebound, 588, &saved) && !saved.active);
    audio_cda_new_buffer(&cas, buffer, 2, 7, nullptr, nullptr);
    advance(int(cda_evt * 3)); assert(cas.cda_streamid == 0);
    audio_cda_new_buffer(&cas, buffer, 2, 7, ended, nullptr);
    audio_cda_new_buffer(&cas, nullptr, 0, 0, nullptr, nullptr);
    assert(p6_cda::capture(cas, buffer, 588, &saved) && saved.remaining == 0);
    assert(p6_cda::restore(cas, rebound, 588, saved, nullptr, nullptr));
    advance(int(cda_evt * 2)); assert(cas.cda_streamid == 0);
    audio_cda_new_buffer(&cas, buffer, 2, 7, ended, nullptr);
    const auto &fresh = audio_stream[0].data[0];
    assert(!fresh.current_sample && !fresh.last_sample && !fresh.sample_accum && !fresh.sample_accum_time);
    audio_cda_new_buffer(&cas, rebound, 3, 7, ended, nullptr); // Refill must not reset the live integrator.
    advance(int(CYCLE_UNIT + 1)); const auto live = audio_stream[0].data[0];
    audio_cda_new_buffer(&cas, buffer, 3, 7, ended, nullptr);
    assert(memcmp(&live, &audio_stream[0].data[0], sizeof live) == 0);
    audio_cda_new_buffer(&cas, nullptr, -1, 0, nullptr, nullptr);
    assert(audio_data[4] == nullptr);
}
int main()
{
    for (int rate : {44100, 48000}) for (bool ntsc : {false, true}) restore_case(rate, ntsc);
}

#endif
