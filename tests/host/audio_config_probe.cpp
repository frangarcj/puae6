// Execute the real set_audio/filter/scheduler path with portable PCM.
#include <audio.cpp>
int config_changed, config_changed_flags;
#include "upstream_audio_config.h"
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <vector>

uae_prefs currprefs{}, changed_prefs{};
regstruct regs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff, savestate_state;
uae_u16 dmacon, intena, adkcon;
uae_u8 agnus_hpos;
int maxhpos = 227, maxvpos_nom = 312;

void INTREQ_INT(int, int) { std::abort(); }
uae_u16 INTREQR() { std::abort(); }
int do_cycles_cck(int) { std::abort(); }
void event_doint_delay_do_ext(uae_u32) { std::abort(); }

// Preferences are multi-megabyte; mirror the engine's static storage.
static const uae_prefs empty_prefs{};

static uae_prefs &defaults(int rate = 48000, bool ntsc = false)
{
    static uae_prefs p;
    p = empty_prefs;
    p.produce_sound = 3; p.sound_stereo = SND_STEREO;
    p.sound_stereo_separation = 7; p.sound_mixed_stereo_delay = 0;
    p.sound_freq = rate; p.sound_interpol = 1;
    p.sound_filter = FILTER_SOUND_EMUL;
    p.sound_filter_type = FILTER_SOUND_TYPE_A500;
    p.sound_volume_paula = 0; p.sound_volume_master = 0;
    p.ntscmode = ntsc;
    p.cpu_memory_cycle_exact = true;
    return p;
}

static void prepare(const uae_prefs &p)
{
    close_sound();
    currprefs = p; changed_prefs = p;
    audio_work_to_do = 0;
    clear_events(); set_cycles(0);
    eventtab[ev_audio].handler = audio_evhandler;
    eventtab[ev_misc].handler = MISC_handler;
    gui_data.powerled = 0;
    config_changed = config_changed_flags = 0;
    assert(setup_sound() == 1);
    audio_reset();
    set_audio();
    assert(p6_sound_status() == P6_PCM_OK);
    assert(config_changed == 1);
}

static void check_selection(const uae_prefs &p)
{
    prepare(p);
    assert(currprefs.sound_freq == p.sound_freq && currprefs.ntscmode == p.ntscmode);
    assert(currprefs.produce_sound == 3 && currprefs.sound_stereo == SND_STEREO);
    assert(currprefs.sound_stereo_separation == p.sound_stereo_separation);
    assert(currprefs.sound_mixed_stereo_delay == p.sound_mixed_stereo_delay);
    assert(currprefs.sound_interpol == p.sound_interpol);
    assert(currprefs.sound_filter == p.sound_filter && currprefs.sound_filter_type == p.sound_filter_type);
    assert(sample_handler == sample16si_anti_handler);
    assert(sound_use_filter == FILTER_MODEL_A500 && sound_use_filter_sinc == 0);
    assert(led_filter_forced == 0 && !led_filter_on);
    const float clock = p.ntscmode ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    assert(scaled_sample_evtime == clock * float(CYCLE_UNIT) / p.sound_freq);
}

static void option_matrix()
{
    auto &p = defaults();
    for (int interpolation = 0; interpolation <= 4; ++interpolation) {
        p.sound_interpol = interpolation; prepare(p);
        void (*expected)() = interpolation == 0 ? sample16s_handler
            : interpolation == 1 ? sample16si_anti_handler
            : interpolation == 2 ? sample16si_sinc_handler
            : interpolation == 3 ? sample16si_rh_handler : sample16si_crux_handler;
        assert(sample_handler == expected);
        assert((sample_prehandler != nullptr) == (interpolation == 1 || interpolation == 2));
        assert(sound_use_filter_sinc == (interpolation == 2 ? FILTER_MODEL_A500 : 0));
        assert(sound_use_filter == (interpolation == 2 ? 0 : FILTER_MODEL_A500));
    }
    for (int filter = FILTER_SOUND_OFF; filter <= FILTER_SOUND_ON; ++filter) {
        for (int type = FILTER_SOUND_TYPE_A500; type <= FILTER_SOUND_TYPE_A500_FIXEDONLY; ++type) {
            p = defaults(); p.sound_filter = filter; p.sound_filter_type = type;
            prepare(p);
            const int model = filter == FILTER_SOUND_OFF ? FILTER_NONE
                : type == FILTER_SOUND_TYPE_A500 ? FILTER_MODEL_A500
                : type == FILTER_SOUND_TYPE_A1200 ? FILTER_MODEL_A1200 : FILTER_MODEL_A500_FIXEDONLY;
            assert(sound_use_filter == model);
            assert(led_filter_forced == (filter == FILTER_SOUND_ON ? 1
                : filter == FILTER_SOUND_EMUL ? 0 : -1));
            assert(led_filter_on == (filter == FILTER_SOUND_ON));
            gui_data.powerled = 1; led_filter_audio();
            assert(led_filter_on == (filter != FILTER_SOUND_OFF));
        }
    }
    p = defaults();
    for (int delay = 0; delay <= 10; ++delay) { p.sound_mixed_stereo_delay = delay; prepare(p); }
    p = defaults();
    for (bool volcnt : {false, true}) {
        p.sound_volcnt = volcnt; prepare(p);
        for (const auto &channel : audio_channel) assert(channel.data.mixvol == int(volcnt));
    }
    // Exercise the volume-counter FIR path too, without claiming a DMA oracle
    // or partition invariance for this optional mode.
    assert(firmem[FIR_WIDTH] == float(p.sound_freq) / float(PAULARATE));
    for (int i = 0; i < 2 * FIR_WIDTH + 1; ++i) {
        assert(std::isfinite(firmem[i]));
        assert(firmem[i] == firmem[2 * FIR_WIDTH - i]);
    }
    for (auto &channel : audio_channel) {
        channel.data.new_sample = 100;
        channel.data.audvol = 64;
        channel.data.adk_mask = ~0u;
    }
    do_cycles_normal(int(scaled_sample_evtime * 80.25f)); update_audio();
    int16_t pcm[160]; size_t frames;
    assert(p6_pcm_read(pcm, 80, &frames) == P6_PCM_OK && frames == 80);
    bool nonzero = false;
    for (int16_t sample : pcm) nonzero |= sample != 0;
    assert(nonzero);
}

static std::vector<int16_t> render(const uae_prefs &p, bool fragmented, bool led = false)
{
    prepare(p);
    gui_data.powerled = led; led_filter_audio();
    for (int i = 0; i < AUDIO_CHANNELS_PAULA; ++i) {
        assert(audio_channel[i].state == 0 && audio_channel[i].evtime == MAX_EV);
        audio_channel[i].data.current_sample = i < 2 ? 100 + i * 10 : 0;
        audio_channel[i].data.mixvol = 64;
        audio_channel[i].data.adk_mask = ~0u;
    }
    const int total = int(scaled_sample_evtime * 80.25f);
    std::vector<int16_t> pcm;
    auto drain = [&](size_t cap) {
        int16_t block[2048]; size_t frames = 0;
        assert(p6_pcm_read(block, cap, &frames) == P6_PCM_OK);
        pcm.insert(pcm.end(), block, block + frames * 2);
    };
    if (!fragmented) {
        do_cycles_normal(total); update_audio(); drain(1024);
    } else {
        int left = total;
        while (left > 0) {
            const int step = left < CYCLE_UNIT * 37 ? left : CYCLE_UNIT * 37;
            do_cycles_normal(step); update_audio(); drain(7); left -= step;
        }
        drain(7);
    }
    bool nonzero = false;
    for (int16_t sample : pcm) nonzero |= sample != 0;
    assert(pcm.size() == 160 && nonzero);
    return pcm;
}

static void invalid_configurations()
{
    const auto &valid = defaults();
    for (int which = 0; which < 10; ++which) {
        prepare(valid);
        const float phase = next_sample_evtime;
        const auto handler = sample_handler;
        const int oldfreq = currprefs.sound_freq, oldinterp = currprefs.sound_interpol;
        const int olddelay = currprefs.sound_mixed_stereo_delay;
        const int oldsep = currprefs.sound_stereo_separation, oldfilter = currprefs.sound_filter;
        const int oldvol = currprefs.sound_volume_paula;
        changed_prefs = valid;
        if (which == 0) changed_prefs.sound_freq = 0;
        if (which == 1) changed_prefs.sound_interpol = 5;
        if (which == 2) changed_prefs.sound_mixed_stereo_delay = 11;
        if (which == 3) changed_prefs.sound_stereo_separation = 11;
        if (which == 4) changed_prefs.sound_filter = FILTER_SOUND_ON + 1;
        if (which == 5) changed_prefs.sound_volume_paula = 101;
        if (which == 6) changed_prefs.sound_mixed_stereo_delay = INT_MAX;
        if (which == 7) changed_prefs.sound_stereo_separation = INT_MAX;
        if (which == 8) changed_prefs.sound_filter_type = 3;
        if (which == 9) changed_prefs.sound_volume_cd = INT_MAX;
        config_changed = config_changed_flags = 0;
        set_audio();
        assert(p6_sound_status() == P6_SOUND_UNSUPPORTED);
        assert(currprefs.sound_freq == oldfreq && currprefs.sound_interpol == oldinterp);
        assert(currprefs.sound_mixed_stereo_delay == olddelay && currprefs.sound_stereo_separation == oldsep);
        assert(currprefs.sound_filter == oldfilter && currprefs.sound_volume_paula == oldvol);
        assert(next_sample_evtime == phase && sample_handler == handler);
        assert(config_changed == 0);
    }
    prepare(valid);
    changed_prefs.sound_freq = 0;
    const int work = --audio_work_to_do;
    check_prefs_changed_audio();
    assert(p6_sound_status() == P6_SOUND_UNSUPPORTED);
    assert(currprefs.sound_freq == valid.sound_freq && audio_work_to_do == work);
    prepare(valid);
    close_sound(); sound_available = 0;
    currprefs = empty_prefs; changed_prefs = valid;
    config_changed = config_changed_flags = 0;
    set_audio();
    assert(currprefs.produce_sound == 3 && changed_prefs.produce_sound == 3);
    assert(p6_sound_status() == P6_SOUND_STATE && config_changed == 0);
}

int main()
{
    for (int rate : {44100, 48000}) for (bool ntsc : {false, true}) {
        const auto &p = defaults(rate, ntsc);
        check_selection(p);
        const auto batch = render(p, false);
        const float phase = next_sample_evtime;
        const evt_t cycles = get_cycles();
        const auto fragments = render(p, true);
        assert(batch == fragments && next_sample_evtime == phase && get_cycles() == cycles);
        assert(batch != render(p, false, true));
    }
    option_matrix();
    // Reconfiguration closes and reopens the PCM transport, flushing queued frames.
    assert(setup_sound() == 1);
    auto &p = defaults(); prepare(p);
    for (int i = 0; i < AUDIO_CHANNELS_PAULA; ++i) {
        audio_channel[i].data.current_sample = 100;
        audio_channel[i].data.mixvol = 64;
        audio_channel[i].data.adk_mask = ~0u;
    }
    do_cycles_normal(int(scaled_sample_evtime * 8)); update_audio();
    p.sound_interpol = 4; changed_prefs = p; set_audio();
    int16_t drained[2]; size_t frames = 99;
    assert(p6_pcm_read(drained, 1, &frames) == P6_PCM_OK && frames == 0);
    assert(sample_handler == sample16si_crux_handler && p6_sound_status() == P6_PCM_OK);
    invalid_configurations();
}
