// Only the two extracted custom timing functions execute here. Drawing and
// device notifications are observations, not production device implementations.
#include <cassert>
#include <initializer_list>
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include "events.h"
#include "devices.h"

uae_prefs currprefs{}, changed_prefs{};
amigadisplay adisplays[MAX_AMIGADISPLAYS]{};
bool aga_mode, ecs_agnus, ecs_denise, ecs_denise_only;
bool lof_store, lof_display;
static bool linetoggle;
int interlace_seen, current_linear_vpos, current_linear_vpos_nom;
int current_linear_hpos_short, maxhpos, maxvpos, doublescan;
int minfirstline_linear, hsync_end_left_border;
static int display_hstart_cyclewait, display_hstart_cyclewait_end;
static int display_hstart_cyclewait_skip, display_hstart_cyclewait_skip2;
uae_u16 beamcon0, bplcon0;
float vblank_hz, fake_vblank_hz, hblank_hz;
static float vblank_hz_lace, vblank_hz_lof, vblank_hz_shf;
frame_time_t syncbase, vsynctimebase, vsynctimebase_orig, cputimebase;
static float sound_clock, sound_adjust, sync_vpos, sync_adjust;
static int sound_calls, sync_calls, buffer_calls, ext_calls, config_calls;
static int drawing_calls, nocustom_calls, reset_calls;

void devices_update_sound(float clk, float adjust)
{ ++sound_calls; sound_clock = clk; sound_adjust = adjust; }
void devices_update_sync(float vpos, float adjust)
{ ++sync_calls; sync_vpos = vpos; sync_adjust = adjust; }
void write_log(const TCHAR *, ...) {}
void set_drawbuffer() { adisplays[0].gfxvidinfo.inbuffer = &adisplays[0].gfxvidinfo.drawbuffer; }
static void updateextblk() { ++ext_calls; }
static void check_nocustom() { ++nocustom_calls; }
void set_config_changed(int) { ++config_calls; }
void custom_end_drawing() { ++drawing_calls; }
bool target_graphics_buffer_update(int monid, bool force)
{ assert(monid == 0 && !force); ++buffer_calls; return false; }
void resetfulllinestate() { ++reset_calls; }

#include "custom_timing_under_test.h"

static void run(bool ntsc, bool lace, bool lof, bool half, float refresh,
                bool turbo, int limit, frame_time_t hostbase, int sound)
{
    currprefs.ntscmode = ntsc;
    currprefs.chipset_refreshrate = refresh;
    changed_prefs.chipset_refreshrate = -123;
    currprefs.gfx_variable_sync = turbo;
    currprefs.turbo_emulation = turbo;
    currprefs.turbo_emulation_limit = limit;
    currprefs.produce_sound = sound;
    currprefs.gfx_overscanmode = OVERSCANMODE_ULTRA;
    currprefs.gfx_resolution = currprefs.gfx_vresolution = 0;
    currprefs.monitoremu_mon = 0;
    current_linear_vpos = current_linear_vpos_nom = maxvpos = ntsc ? 262 : 312;
    current_linear_hpos_short = maxhpos = 227;
    beamcon0 = ntsc ? 0 : BEAMCON0_PAL;
    interlace_seen = lace;
    lof_store = lof_display = lof;
    linetoggle = half;
    const float clock = ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    const float horizontal = 227 + (half ? 0.5f : 0);
    // Inputs have the same physical geometry as init_beamcon0's field rates.
    // This test does not execute init_beamcon0 or claim a CIA/TOD waveform.
    vblank_hz_shf = clock / (current_linear_vpos_nom * horizontal);
    vblank_hz_lof = clock / ((current_linear_vpos_nom + 1.0f) * horizontal);
    vblank_hz_lace = clock / ((current_linear_vpos_nom + 0.5f) * horizontal);
    const float field_rate = lace ? vblank_hz_lace : lof ? vblank_hz_lof : vblank_hz_shf;
    syncbase = hostbase;
    vsynctimebase = 731; vsynctimebase_orig = 947; cputimebase = 1291;
    auto &vb = adisplays[0].gfxvidinfo.drawbuffer;
    vb.width_allocated = 1024; vb.height_allocated = 1024;
    sound_calls = sync_calls = buffer_calls = ext_calls = config_calls = 0;
    drawing_calls = nocustom_calls = reset_calls = 0;
    compute_framesync();
    assert(vblank_hz == field_rate);
    assert(hblank_hz == clock / horizontal);
    assert(currprefs.chipset_refreshrate == refresh);
    assert(changed_prefs.chipset_refreshrate == -123);
    assert(vsynctimebase == 731 && vsynctimebase_orig == 947 && cputimebase == 1291);
    // 0369 initializes Paula/CDDA clocks even before the PCM transport opens.
    assert(sound_calls == 1);
    assert(sound_clock == clock && sound_adjust == 1);
    assert(sync_calls == 1 && sync_adjust == 1);
    assert(sync_vpos == current_linear_vpos_nom + (lace ? 0.5f : lof ? 1.0f : 0));
    assert(buffer_calls == 1 && ext_calls == 1 && config_calls == 1);
    assert(drawing_calls == 1 && nocustom_calls == 1 && reset_calls == 1);
    assert(vb.inwidth == 454 && vb.inheight == current_linear_vpos);
    assert(vb.outwidth == vb.inwidth && vb.outheight == vb.inheight);
    // Notifications before/after a refresh recalculation must agree as well.
    compute_vsynctime();
    assert(vblank_hz == field_rate && sync_calls == 2);
    assert(sound_calls == 2);
    assert(sound_clock == clock && sound_adjust == 1);
}

int main()
{
    for (bool ntsc : {false, true})
    for (bool lace : {false, true})
    for (bool lof : {false, true})
    for (bool half : {false, true})
    for (float refresh : {0.0f, 75.0f, 120.0f})
    for (bool turbo : {false, true})
    for (int limit : {0, 8})
    for (frame_time_t hostbase : {frame_time_t(0), frame_time_t(1000000000)})
    for (int sound : {0, 1, 2, 3})
        run(ntsc, lace, lof, half, refresh, turbo, limit, hostbase, sound);
}
