// Actual audio lifecycle/sampling and scheduler. No DMA or full-machine claim.
#include <audio.cpp>
#include <cassert>
#include <limits>

uae_prefs currprefs{};
regstruct regs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff, savestate_state;
uae_u16 dmacon, intena, adkcon;
uae_u8 agnus_hpos;
int maxhpos = 227, maxvpos_nom = 312;
// These boundaries must never be needed by an idle-channel sampling test.
void INTREQ_INT(int, int) { abort(); }
uae_u16 INTREQR() { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }

static const uae_prefs empty_prefs{};
static int16_t output[4096];

static void prefs(int rate = 48000, bool ntsc = false)
{
    currprefs = empty_prefs;
    currprefs.produce_sound = 2;
    currprefs.sound_freq = rate;
    currprefs.sound_stereo = SND_STEREO;
    currprefs.ntscmode = ntsc;
    currprefs.cpu_memory_cycle_exact = true;
}

static size_t read_pcm()
{
    size_t n;
    assert(p6_pcm_read(output, 2048, &n) == 0);
    return n;
}

static void start(int rate, bool ntsc)
{
    close_sound(); prefs(rate, ntsc);
    clear_events(); set_cycles(0); events_schedule();
    eventtab[ev_audio].handler = audio_evhandler;
    eventtab[ev_misc].handler = MISC_handler;
    assert(setup_sound() == 1 && init_audio() == 1);
    assert(p6_sound_status() == 0 && !sound_paused());
    assert(sample_handler == sample16s_handler);
    const float clock = ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    const float interval = clock * float(CYCLE_UNIT) / rate;
    assert(scaled_sample_evtime == interval);
    update_sound(clock);
    audio_reset(); audio_activate();
    assert(!eventtab[ev_audio].active && next_sample_evtime == interval);
    sound_paula_volume[0] = sound_paula_volume[1] = 32768;
    for (auto &channel : audio_channel) {
        assert(channel.state == 0 && channel.evtime == MAX_EV);
        channel.data.current_sample = -10;
        channel.data.mixvol = 1;
        channel.data.adk_mask = ~0u;
    }
}

static void advance(int ticks)
{
    do_cycles_normal(ticks);
    update_audio();
    assert(p6_sound_status() == 0);
}

static void sampling_and_pause(int rate, bool ntsc)
{
    start(rate, ntsc);
    const int ticks = int(scaled_sample_evtime * 100.25f);
    advance(ticks);
    float initial_phase = next_sample_evtime;
    assert(init_audio() == 1); // Idempotent backend init preserves pending PCM.
    update_sound(ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL);
    assert(next_sample_evtime == initial_phase);
    assert(read_pcm() == 100);
    for (int i = 0; i < 200; ++i) assert(output[i] == -40);
    advance(ticks);
    const size_t second = read_pcm();
    const float phase = next_sample_evtime;
    const evt_t cycles = get_cycles();
    start(rate, ntsc);
    pause_sound(); assert(sound_paused());
    advance(ticks); assert(read_pcm() == 0);
    const float paused_phase = next_sample_evtime;
    auto handler = sample_handler;
    resume_sound();
    assert(!sound_paused() && next_sample_evtime == paused_phase && sample_handler == handler);
    advance(ticks);
    assert(read_pcm() == second && next_sample_evtime == phase && get_cycles() == cycles);
    for (size_t i = 0; i < second * 2; ++i) assert(output[i] == -40);
    // Independent presentation/device-buffer pauses do not resume each other.
    pause_sound_buffer(); pause_sound(); restart_sound_buffer();
    assert(sound_paused()); advance(ticks); assert(read_pcm() == 0);
    resume_sound(); assert(!sound_paused());
    advance(ticks); assert(read_pcm() > 0);
    float interval = scaled_sample_evtime;
    advance(ticks); audio_reset();
    assert(read_pcm() == 0 && next_sample_evtime == interval);
    assert(!eventtab[ev_audio].active);
    for (auto &channel : audio_channel) assert(channel.state == 0 && channel.data.mixvol == 0);
    close_sound(); close_sound();
    assert(p6_sound_status() == P6_SOUND_STATE && sound_paused());
}

static void rejected_configuration()
{
    close_sound(); prefs(); sound_available = 0;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_STATE);
    setup_sound();
    for (int rate : {-1, 0, 8000, 192000, INT_MAX}) {
        close_sound(); prefs(rate);
        assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
        assert(currprefs.sound_freq == rate && sample_handler == nullptr);
    }
    close_sound(); prefs(); currprefs.sound_stereo = SND_MONO;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    close_sound(); prefs(); currprefs.sound_auto = true;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    close_sound(); prefs(); currprefs.produce_sound = 1;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    close_sound(); prefs(); currprefs.sound_volume_master = 10;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    close_sound(); prefs(); currprefs.sound_stereo_swap_paula = true;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    close_sound(); prefs(); currprefs.sound_volume_paula = 101;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    start(44100, false);
    float interval = scaled_sample_evtime;
    currprefs.sound_freq = 48000;
    assert(init_audio() == 0 && p6_sound_status() == P6_SOUND_UNSUPPORTED);
    assert(scaled_sample_evtime == interval);
    currprefs.sound_freq = 44100;
    assert(init_audio() == 0); // Fault remains sticky until close/init.
    for (float bad : {0.0f, -1.0f, float(CHIPSET_CLOCK_NTSC),
                      std::numeric_limits<float>::infinity(),
                      std::numeric_limits<float>::quiet_NaN()}) {
        start(48000, false); interval = scaled_sample_evtime;
        update_sound(bad);
        assert(p6_sound_status() == P6_SOUND_CLOCK && scaled_sample_evtime == interval);
        reset_sound(); resume_sound(); update_sound(CHIPSET_CLOCK_PAL);
        assert(p6_sound_status() == P6_SOUND_CLOCK);
    }
    close_sound();
}

int main()
{
    rejected_configuration();
    for (int rate : {44100, 48000}) for (bool ntsc : {false, true}) sampling_and_pause(rate, ntsc);
}
