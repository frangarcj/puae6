// Real CIA, custom sync/IRQ bodies, key queue, and event scheduler. Scanline
// boundaries are supplied by this probe; the custom raster loop is not run.
#include <cia.cpp>
#include "uae.h"
#include "blitter.h"
#include <cstdio>
#include <cstdlib>
#include <initializer_list>

uae_prefs currprefs{};
static const uae_prefs empty_prefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff, vpos, key_swap_hack, pause_emulation, slow_cpu_access;
regstruct regs{};
uae_u32 hsync_counter, vsync_counter;
int maxvpos, maxvpos_nom, maxhpos = 227, interlace_seen;
float vblank_hz;
uae_u8 agnus_hpos;
uae_u16 bplcon0, beamcon0, hsstop, hsstrt, vsstop, hcenter;
bool lof_store;
bool ecs_agnus;
static bool genlockhtoggle, genlockvtoggle, linetoggle;
static bool ciahsyncs, ciavsyncs;
static int cia_hsync;
extern volatile uae_atomic uae_int_requested;  // defined by ports/portable/atomic.cpp
static unsigned irq_count[2], pending_count;
static bool pending_levels[2];
static unsigned trace;
#ifndef TEST_REFERENCE
uae_u16 dmacon;
int quit_program;
bltinfo blt_info{};
enum class diw_states { DIW_waiting_start, DIW_waiting_stop };
static diw_states vdiwstate = diw_states::DIW_waiting_start;
static uae_u16 ddfstrt, ddfstop;
static int fetchmode;
static uae_s8 cycle_diagram_free_cycles[3][3][9];
static uae_s8 cycle_diagram_total_cycles[3][3][9];
static int GET_PLANES_LIMIT(uae_u16) { abort(); }
void blitter_slowdown(int, int, int, int) { abort(); }
static evt_t copper_dma_change_cycle;
static bool copper_dma_change_cycle_pending;
static bool custom_disabled, inside_hsync, toggle_before;
static unsigned line_stage;
static void check_irq_order();
void inputdevice_hsync(bool);
#endif
static void digest(unsigned v) { trace = (trace ^ v) * 16777619u; }
void safe_interrupt_set(int source, int id, bool level6)
{
    assert(id == 0);
    if (source == IRQ_SOURCE_UAE) {
#ifndef TEST_REFERENCE
        if (inside_hsync) check_irq_order();
#endif
        assert(pending_count < 2); pending_levels[pending_count++] = level6;
    } else {
        assert(source == IRQ_SOURCE_CIA);
        ++irq_count[level6]; digest(level6); digest(unsigned(get_cycles()));
    }
}
void inputdevice_do_kb_reset() { abort(); }
int do_cycles_cck(int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
#ifdef TEST_REFERENCE
// Excluded-device boundaries fail if the A500 reference ever reaches them.
volatile int bsd_int_requested;
void bsdsock_fake_int_handler() { abort(); }
#endif
// Both A500 variants fail if they request a CD32 provider.
bool akiko_ntscmode() { abort(); }
#include "custom_cia_under_test.h"

#ifndef TEST_REFERENCE
static void check_irq_order()
{
    assert(line_stage == 1 || line_stage == 2);
    assert(copper_enabled_thisline == -1); // Copper follows both IRQ levels.
    ++line_stage;
}
void inputdevice_hsync(bool vsync)
{
    assert(!vsync && inside_hsync && line_stage == 0);
    assert(genlockhtoggle != toggle_before); // CIA sync already ran.
    assert(copper_enabled_thisline == -1);
    line_stage = 1;
    uae_int_requested = 0x0101; // Same-line input may request both IRQ levels.
}
static void line_post(bool vsync)
{
    toggle_before = genlockhtoggle; line_stage = 0; pending_count = 0;
    copper_enabled_thisline = -1;
    const auto before = get_cycles();
    inside_hsync = true;
    hsync_handler_post(vsync);
    inside_hsync = false;
    // beta21 computes the next-line Copper eligibility after the post-handler.
    if (custom_disabled && !vsync)
        copper_enabled_thisline = 0;
    else
        compute_spcflag_copper();
    assert(line_stage == 3 && pending_count == 2);
    assert(pending_levels[0] && !pending_levels[1]);
    assert(copper_enabled_thisline == 0 || copper_enabled_thisline == 1);
    assert(get_cycles() == before); // No extra CPU/chipset advancement here.
}
#endif


static void setup(bool ntsc, bool lof, int todmode, int syncmode)
{
    currprefs = empty_prefs;
    currprefs.ntscmode = ntsc;
    currprefs.cs_ciaatod = todmode;
    currprefs.cpu_compatible = currprefs.cpu_memory_cycle_exact = true;
    currprefs.blitter_cycle_exact = true;
    currprefs.keyboard_mode = 0;
    maxvpos = maxvpos_nom = ntsc ? 262 : 312;
    maxhpos = 227;
    lof_store = lof; interlace_seen = 0; linetoggle = ntsc;
    vblank_hz = (ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL)
        / ((maxvpos + (lof ? 1.0f : 0)) * (maxhpos + (linetoggle ? .5f : 0)));
    beamcon0 = ntsc ? 0 : BEAMCON0_PAL;
    if (syncmode == 1 || syncmode == 2) beamcon0 |= BEAMCON0_VARVSYEN | BEAMCON0_VARHSYEN;
    hsstrt = 12; hsstop = syncmode == 2 ? maxhpos : 36;
    vsstop = 4; hcenter = 210; // Exercises a long-field pulse spilling into next line.
    bplcon0 = syncmode >= 3 ? 2 : 0;
    currprefs.genlock = syncmode == 4;
    cia_hsync = 0; genlockhtoggle = genlockvtoggle = false;
    currcycle = currcycle_cck = 0; nextevent = EVT_MAX; pissoff = 0;
    clear_events(); memset(eventtab2, 0, sizeof eventtab2);
    eventtab[ev_cia].handler = CIA_handler;
    eventtab[ev_misc].handler = MISC_handler;
    eventtab[ev_cia].oldcycles = get_cycles();
    memset(cia, 0, sizeof cia);
    cia_cycle_accurate = true; internaleclockphase = 0; cia_set_eclockphase();
    kbstate = 3; kblostsynccnt = resetwarning_phase = 0;
    irq_count[0] = irq_count[1] = 0;
    hsync_counter = 0; agnus_hpos = 0; vpos = 0;
    for (auto &c : cia) { c.todon = 1; c.alarm = 3; c.imask = ICR_ALARM; }
#ifndef TEST_REFERENCE
    quit_program = 0; custom_disabled = false;
    dmacon = 0; copper_dma_change_cycle = -1;
    cop_state = {}; regs.spcflags = 0;
#endif
}

static void waveform(bool ntsc, bool lof, int todmode, int syncmode)
{
    setup(ntsc, lof, todmode, syncmode);
    for (int frame = 0; frame < 4; ++frame) {
        genlockvtoggle = (frame & 1) != 0;
        for (vpos = 0; vpos < maxvpos + int(lof); ++vpos) {
            agnus_hpos = 0; ++hsync_counter;
#ifdef TEST_REFERENCE
            // Control is the original CIA sync operation, without host waits.
            cia_hsync_do();
#else
            line_post(vpos == 0);
#endif
            // The probe supplies raster boundaries: beta21 moved CIA-B's TOD
            // edge out of cia_hsync_do and into this real pulse-end helper.
            if (syncmode != 2)
                ciab_tod_maybe_inc(syncmode == 1 ? hsstop : 35);
            if (todmode == 0) {
                // Supply the fixed/programmed VSYNC end at the same raster
                // coordinates used by beta21; the real helper owns gating and TOD.
                const int line = syncmode == 1 || syncmode == 2 ? vsstop : (ntsc ? 6 : 5);
                const int delay = syncmode == 1 || syncmode == 2
                    ? (lof ? hcenter : hsstrt)
                    : (ntsc ? (lof ? 18 : 132) : (lof ? 132 : 18));
                if (vpos == line) ciaa_tod_maybe_inc(delay);
            }
            do_cycles_normal(maxhpos * CYCLE_UNIT);
            agnus_hpos = maxhpos - 1;
            CIA_tod_event_check(0); CIA_tod_event_check(1);
            for (auto &c : cia) {
                digest(c.tod); digest(c.tod_event_state); digest(c.tod_offset);
                digest(c.icr1); digest(c.icr2);
            }
        }
    }
    if (syncmode == 0) {
        assert(cia[1].tod == 4u * (maxvpos + int(lof)));
        assert(irq_count[1] == 1);
        if (todmode == 0) { assert(cia[0].tod == 4 && irq_count[0] == 1); }
    }
    if (syncmode == 2) assert(cia[1].tod == 0); // Invalid programmed HSync.
    if (syncmode == 3) {
        assert(cia[1].tod == 0);
        if (!todmode) assert(cia[0].tod == 0); // External sync absent.
    }
}

#ifndef TEST_REFERENCE
static void copper_and_reset()
{
    setup(false, false, 0, 0);
    dmacon = DMA_MASTER | DMA_COPPER;
    for (int inst : {COP_INS_MOVE, COP_INS_SKIP, COP_INS_WAIT}) {
        for (int ahead : {0, 1}) {
            cop_state = {}; cop_state.inst = inst;
            cop_state.ir[1] = 0xff80; cop_state.vcmp = vpos + ahead;
            line_post(false);
            const bool expected = inst != COP_INS_WAIT || !ahead;
            assert(copper_enabled_thisline == expected);
        }
    }
    cop_state = {}; cop_state.inst = COP_INS_WAIT; cop_state.start = 1;
    cop_state.vcmp = vpos + 1; line_post(false); assert(copper_enabled_thisline);
    cop_state = {}; cop_state.inst = COP_INS_MOVE;
    custom_disabled = true; line_post(false); assert(!copper_enabled_thisline);
    custom_disabled = false;
    dmacon = 0; line_post(false); assert(!copper_enabled_thisline);
    // DMA changes take effect after their CCK; preserve upstream's inversion.
    copper_dma_change_cycle = get_cycles() + CYCLE_UNIT;
    copper_dma_change_cycle_pending = true;
    line_post(false); assert(copper_enabled_thisline);
    dmacon = DMA_MASTER | DMA_COPPER;
    line_post(false); assert(!copper_enabled_thisline);
    copper_dma_change_cycle_pending = false;
    cop_state.startstrobe = 1;
    custom_disabled = true; line_post(true); assert(copper_enabled_thisline);
    for (int request : {-UAE_RESET, -UAE_RESET_HARD, -UAE_RESET_KEYBOARD, 0, -UAE_QUIT}) {
        // Host scheduling knobs cannot add waits/extra cycles to this boundary.
        currprefs.cpu_thread = true; currprefs.m68k_speed = -1;
        currprefs.cpu_idle = 100; currprefs.turbo_emulation = true;
        quit_program = request; regs.spcflags = SPCFLAG_CHECK;
        line_post(true);
        const bool reset = request == -UAE_RESET || request == -UAE_RESET_HARD
            || request == -UAE_RESET_KEYBOARD;
        assert(quit_program == (reset ? -request : request));
        assert(regs.spcflags == (SPCFLAG_CHECK | (reset ? SPCFLAG_BRK | SPCFLAG_MODE_CHANGE : 0)));
    }
}
#endif

int main()
{
    // Both bit ranges must preserve level and ordering, including mixed requests.
    for (unsigned pending = 0; pending < 65536; ++pending) {
        uae_int_requested = pending; pending_count = 0;
        rethink_uae_int();
        const bool high = pending & 0xff00, low = pending & 0xff;
        assert(pending_count == unsigned(high) + unsigned(low));
        if (high) assert(pending_levels[0]);
        if (low) assert(!pending_levels[high ? 1 : 0]);
        assert(unsigned(uae_int_requested) == pending);
    }
    trace = 2166136261u;
    for (bool ntsc : {false, true})
    for (bool lof : {false, true})
    for (int todmode : {0, 1, 2})
    for (int syncmode : {0, 1, 2, 3, 4}) waveform(ntsc, lof, todmode, syncmode);
    std::printf("%u\n", trace);
#ifndef TEST_REFERENCE
    copper_and_reset();
#endif
}
