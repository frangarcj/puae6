// Actual CPU halt, Paula and scheduler; no CPU instructions or DMA fixture.
#include <newcpu.cpp>
#include <audio.cpp>
#include <cassert>
#include <vector>

uae_prefs currprefs{}, changed_prefs{};
static const uae_prefs empty_prefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int savestate_state, quit_program, vpos;
uae_u16 dmacon, intena, adkcon;
uae_u8 agnus_hpos;
int maxhpos = 227, maxvpos_nom = 312;
static uae_u8 pc_storage[8];
static int reset_requests, steps, exit_flag;
static constexpr int step_count = 1000, ticks_per_step = 8 * CYCLE_UNIT;

void INTREQ_INT(int, int) { abort(); }
uae_u16 INTREQR() { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
// Observe the automatic-reset request; this test does not reset a machine.
void uae_reset(int hard, int keyboard)
{
    assert(hard == 1 && keyboard == 0);
    ++reset_requests;
}

static void start(int rate, bool ntsc)
{
    close_sound(); currprefs = empty_prefs;
    currprefs.cpu_model = 68000;
    currprefs.cpu_compatible = currprefs.cpu_cycle_exact = true;
    currprefs.cpu_memory_cycle_exact = true;
    currprefs.produce_sound = 3; currprefs.sound_stereo = SND_STEREO;
    currprefs.sound_freq = rate; currprefs.ntscmode = ntsc;
    memset(&regs, 0, sizeof(regs)); memset(&regflags, 0, sizeof(regflags));
    regs.pc = 0x100; regs.pc_p = regs.pc_oldp = pc_storage;
    regs.s = 1; regs.intmask = 2;
    gui_data.cpu_halted = 0;
    clear_events(); set_cycles(0); events_schedule();
    eventtab[ev_audio].handler = audio_evhandler;
    eventtab[ev_misc].handler = MISC_handler;
    assert(setup_sound() == 1 && init_audio() == 1);
    audio_reset(); audio_work_to_do = 0; audio_activate();
    sound_paula_volume[0] = sound_paula_volume[1] = 32768;
    for (auto &channel : audio_channel) {
        channel.data.current_sample = -100;
        channel.data.mixvol = 64;
        channel.data.adk_mask = ~0u;
    }
    // A pending hardware event, safely beyond the test interval. Halt must not
    // erase its state or deadline. Reaching DMA/IRQ boundaries aborts above.
    audio_channel[0].state = 2;
    audio_channel[0].evtime = 1u << 28;
    audio_channel[0].per = 155 * CYCLE_UNIT;
    schedule_audio(); events_schedule();
    do_cycles_normal(123 * CYCLE_UNIT + 7); update_audio();
    assert(next_sample_evtime != scaled_sample_evtime);
}

static std::vector<int16_t> drain()
{
    int16_t pcm[2048]; size_t frames;
    assert(p6_sound_status() == P6_PCM_OK);
    assert(p6_pcm_read(pcm, 1024, &frames) == P6_PCM_OK && frames > 0);
    for (size_t i = 0; i < frames * 2; ++i) assert(pcm[i] == -25600);
    return {pcm, pcm + frames * 2};
}

// Test-only forwarding/stop boundary. The production haltloop owns the loop;
// events.cpp owns time advancement. This is not a new production scheduler.
static void step_chipset(int ticks)
{
    assert(ticks == ticks_per_step && regs.halted == CPU_HALT_DOUBLE_FAULT);
    do_cycles_normal(ticks); update_audio();
    vpos = ++steps & 1; // Cross the original haltloop's vpos==0 branch too.
    if (steps == step_count) {
        if (exit_flag) set_special(exit_flag);
        else cpu_halt_clear();
    }
}

static void halted_sampling(int rate, bool ntsc, int flag)
{
    start(rate, ntsc);
    do_cycles_normal(step_count * ticks_per_step); update_audio();
    const auto control = drain();
    const float phase = next_sample_evtime;
    const evt_t cycles = get_cycles();
    const auto remaining = audio_channel[0].evtime;
    start(rate, ntsc);
    const auto channel = audio_channel[0];
    const auto deadline = eventtab[ev_audio].evtime;
    const float before = next_sample_evtime;
    cpu_halt(CPU_HALT_DOUBLE_FAULT);
    assert(memcmp(&channel, &audio_channel[0], sizeof(channel)) == 0);
    assert(eventtab[ev_audio].active && eventtab[ev_audio].evtime == deadline);
    assert(next_sample_evtime == before && !sound_paused());
    assert(regs.halted == CPU_HALT_DOUBLE_FAULT && regs.intmask == 7);
    assert((regs.sr & 0x700) == 0x700 && (regs.spcflags & SPCFLAG_CHECK));
    assert(gui_data.cpu_halted == CPU_HALT_DOUBLE_FAULT);
    cpu_halt(CPU_HALT_INVALID_START_ADDRESS); // Already halted: retain reason/state.
    assert(regs.halted == CPU_HALT_DOUBLE_FAULT && next_sample_evtime == before);
    steps = 0; exit_flag = flag; x_do_cycles = step_chipset;
    assert(haltloop() == bool(flag));
    assert(steps == step_count && get_cycles() == cycles);
    assert(next_sample_evtime == phase && audio_channel[0].evtime == remaining);
    assert(drain() == control);
    if (flag) assert(regs.halted == CPU_HALT_DOUBLE_FAULT && (regs.spcflags & flag));
    else assert(regs.halted == 0 && gui_data.cpu_halted == 0);
    cpu_halt_clear();
    assert(next_sample_evtime == phase && !sound_paused());
}

int main()
{
    for (int rate : {44100, 48000}) for (bool ntsc : {false, true})
        for (int flag : {0, SPCFLAG_BRK, SPCFLAG_MODE_CHANGE})
            halted_sampling(rate, ntsc, flag);
    start(48000, false);
    const float reset_phase = next_sample_evtime;
    regs.s = 0; regs.intmask = 0;
    cpu_inreset();
    assert(regs.s == 1 && regs.intmask == 7 && (regs.sr & 0x2700) == 0x2700);
    assert(regs.spcflags & SPCFLAG_CPUINRESET);
    assert(next_sample_evtime == reset_phase && !sound_paused());
    start(48000, false);
    const float phase = next_sample_evtime;
    const auto channel = audio_channel[0];
    currprefs.crash_auto_reset = true;
    reset_requests = 0; quit_program = 7;
    cpu_halt(CPU_HALT_DOUBLE_FAULT);
    assert(reset_requests == 1 && quit_program == -7 && regs.halted == 0);
    assert((regs.spcflags & (SPCFLAG_BRK | SPCFLAG_MODE_CHANGE)) == (SPCFLAG_BRK | SPCFLAG_MODE_CHANGE));
    assert(next_sample_evtime == phase && !sound_paused());
    assert(memcmp(&channel, &audio_channel[0], sizeof(channel)) == 0);
}
