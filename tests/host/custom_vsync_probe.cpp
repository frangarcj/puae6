// Execute whole custom plus exact drawing/config bodies. Buffer/device callbacks
// below observe ordering only: this probe does not certify rendered pixels.
#include "custom.cpp"
#include <cassert>
#include <string>
#include <initializer_list>

uae_prefs currprefs{}, changed_prefs{};
amigadisplay adisplays[MAX_AMIGADISPLAYS]{};
regstruct regs{};
int cpu_last_stop_vpos, cpu_stopped_lines, interlace_seen;
int config_changed, config_changed_flags, pissoff;
static int last_redraw_point;
static bool denise_accurate_mode;
static std::string calls;
static int clear_seen;
static void finish_drawing_frame(bool drawlines)
{ assert(drawlines); calls += 'F'; clear_seen = display_reset; }
static void init_drawing_frame() { calls += 'I'; }
void draw_denise_vsync_queue(int redraw) { assert(redraw == 1); calls += 'Q'; }
void draw_denise_line_queue_flush() { calls += 'L'; }
void end_draw_denise() { calls += 'E'; }
bool start_draw_denise() { calls += 'S'; return true; }
void gui_flicker_led(int led, int unit, int status)
{ assert(led == -1 && unit == 0 && status == 0); calls += 'G'; }
void DISK_vsync() { calls += 'D'; }
void devices_vsync_post() { calls += 'V'; }
bool vsync_handle_check() { calls += 'C'; return false; }
bool drawing_can_lineoptimizations() { calls += 'O'; return false; }
#include "vsync_drawing_under_test.h"

// Compare clear retirement against the original presentation helper. Its GPU
// boundary is observed in this namespace only; it cannot satisfy custom imports.
namespace reference {
static int display_reset;
static bool result;
static bool render_screen(int, int, bool) { return result; }
#include "vsync_present_reference.h"
}

static void field(bool initial, bool lace, bool lof, int scan, int resolution,
                  int skip, bool inhibit, int clear)
{
    auto &ad = adisplays[0];
    currprefs.gfx_framerate = skip;
    currprefs.gfx_iscanlines = scan;
    currprefs.gfx_vresolution = resolution;
    ad.framecnt = 0; ad.inhibit_frame = inhibit;
    interlace_seen = lace; lof_store = lof_display = lof;
    initial_frame = initial; last_redraw_point = 0;
    custom_disabled = false; display_redraw = 1;
    display_reset = clear; clear_seen = -1;
    vsync_rendered = vsync_display_rendered = false;
    frame_shown = frame_rendered = true;
    nextline_how = nln_upper;
    vblank_hz_mult = 1; vblank_hz_state = 0;
    vsyncmintimepre = 719; frameskiptime = 821;
    currcycle = 123456; calls.clear();
    const bool redraw = !lace || scan || lof;
    const int expected_count = redraw && !initial ? (inhibit || skip > 1 ? 1 : 0) : 0;
    std::string expected = "QLE";
    if (redraw && !initial) expected += 'F';
    if (redraw && !expected_count) expected += 'I';
    expected += 'G';
    expected += 'S';
    regs.spcflags = 0;
    vsync_display_render();
    // The video-unit request comes from this render alone, once per field, with or without a frame to draw.
    assert(regs.spcflags == SPCFLAG_UNIT_END);
    assert(calls == expected && ad.framecnt == expected_count);
    assert(last_redraw_point == (redraw ? 0 : 1));
    assert(!initial_frame && vsync_display_rendered && !vsync_rendered);
    assert(!frame_shown && !frame_rendered && nextline_how == nln_normal);
    assert(vblank_hz_state == 1 && display_redraw == 0);
    assert(denise_accurate_mode);
    assert(currcycle == 123456 && vsyncmintimepre == 719 && frameskiptime == 821);
    if (redraw && !initial) assert(clear_seen == clear); // Retire after buffer work.
    for (bool success : {false, true}) {
        reference::display_reset = clear; reference::result = success;
        reference::crender_screen(0, 1, false);
        assert(display_reset == reference::display_reset);
    }
    const int remaining = display_reset;
    regs.spcflags = 0;
    vsync_display_render(); // Duplicate notification cannot drain/retire twice.
    assert(calls == expected && display_reset == remaining && regs.spcflags == 0);
}

int main()
{
    maxvpos = 312; currprefs.cpu_model = 68000; currprefs.cpu_compatible = true;
    for (bool initial : {false, true})
    for (bool lace : {false, true})
    for (bool lof : {false, true})
    for (int scan : {0, 1})
    for (int resolution : {0, 1})
    for (int skip : {1, 2, 3})
    for (bool inhibit : {false, true})
    for (int clear : {0, 1, 2})
        field(initial, lace, lof, scan, resolution, skip, inhibit, clear);

    // Two short interlace fields still complete a redraw; count_frame skips and
    // inhibit must survive across boundaries rather than being forcibly reset.
    currprefs.gfx_framerate = 3; currprefs.gfx_iscanlines = 0;
    interlace_seen = 1; lof_store = lof_display = false;
    adisplays[0].framecnt = adisplays[0].inhibit_frame = 0;
    custom_disabled = false; last_redraw_point = 0; initial_frame = false;
    for (int n = 0; n < 6; ++n) {
        display_redraw = 1; vsync_display_rendered = false; calls.clear();
        vsync_display_render();
        assert(adisplays[0].framecnt == ((n + 1) / 2) % 3);
        assert((calls.find('F') != std::string::npos) == (n == 1));
    }
    // Hardware post keeps configuration ageing, device callbacks and beam time.
    config_changed = 1; config_changed_flags = 7; bogusframe = timehack_alive = 2;
    currcycle = 100; bplcon0 = beamcon0 = 0; nosignal_trigger = false;
    lineoptimizations_draw_always = true;
    for (int n = 0; n < 3; ++n) {
        calls.clear(); regs.spcflags = 0; vsync_handler_post();
        assert(regs.spcflags == 0); // Only the render raises the unit request, never the vsync post.
        assert(calls == "DVCO" && vsync_cycles == currcycle && currcycle == 100);
        assert(vhposr_prev == 0xffffffff && !nosignal_trigger);
        assert(config_changed == (n == 0 ? 2 : 0));
        assert(config_changed_flags == (n == 0 ? 7 : 0));
    }
    assert(bogusframe == 0 && timehack_alive == 0);
    bplcon0 = 2; vsync_handler_post(); assert(nosignal_trigger);
    // STOP/idle accounting remains state bookkeeping, even with host pacing
    // preferences set. No clock or sleep provider is linked by this executable.
    currprefs.m68k_speed = -1; currprefs.cpu_idle = 150; regs.stopped = 1;
    currprefs.gfx_apmode[0].gfx_vsync = currprefs.gfx_apmode[0].gfx_vsyncmode = 1;
    cpu_sleepmode = cpu_sleepmode_cnt = 0; maxvpos = 312;
    for (int n = 0; n < 51; ++n) {
        cpu_last_stop_vpos = 0; vsync_handler_render();
        assert(cpu_sleepmode_cnt == (n < 50 ? n + 1 : 50));
        assert(cpu_stopped_lines == 0 && cpu_last_stop_vpos == 0);
    }
    assert(cpu_sleepmode == 1 && currcycle == 100);
    regs.halted = -1; vsync_handler_render();
    assert(cpu_sleepmode == 0 && cpu_sleepmode_cnt == 0);
    lastframetime = 937; idletime = 91;
    fps_mavg.size = idle_mavg.size = 9; fpscounter_reset();
    assert(lastframetime == 937 && idletime == 0 && bogusframe == 2);
    assert(fps_mavg.size == 0 && idle_mavg.size == 0);
    currprefs.m68k_speed = -1; vsyncmintime = 647;
    reset_frame_rate_hack(); assert(vsyncmintime == 647);
    is_syncline = 9; pissoff = 100; vsync_event_done();
    assert(is_syncline == 0 && pissoff == 0 && currcycle == 100);
}
