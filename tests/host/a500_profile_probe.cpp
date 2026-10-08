// Builds the preferences of a machine twice in one process, the way upstream does and the way the bridge does,
// and reports what differs. tests/host/test_a500_profile.py and test_a600_profile.py and test_a1200_profile.py compare the report with
// config/profiles/a500-ocs.json, a600-ecs.json and a1200-aga.json.
//   usage: p6_profile_probe [a500|a600|a1200]      (a500 when absent)
//   line "pinned <video standard> <name> <value>" : selected facts of the bridge preset
//   line "diff <video standard> <offset> <length>"  : byte ranges of struct uae_prefs where the two differ
//   line "size <bytes>"                              : sizeof(uae_prefs), to bound the ranges
#include "p6.h"
#include "config.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "audio.h"

#include <cstdio>
#include <cstring>
#include <vector>

static uae_prefs upstream_prefs, bridge_prefs;

static p6_config make_config(uint32_t machine, uint32_t standard)
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = machine;
    c.video_standard = standard;
    c.audio_rate_hz = 48000;
    c.random_seed = 0x0123456789abcdefull;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

// Fields the profile pins by value. Names are member paths of struct uae_prefs.
#define PINNED(X) \
    X(cpu_model) X(fpu_model) X(address_space_24) X(cpu_compatible) X(cpu_cycle_exact) X(cpu_memory_cycle_exact) \
    X(blitter_cycle_exact) X(m68k_speed) X(cpu_frequency) X(cpu_clock_multiplier) X(cpu_idle) X(mmu_model) \
    X(chipset_mask) X(cs_compatible) X(cs_rtc) X(cs_ide) X(cs_pcmcia) X(cs_cdtvcd) X(cs_cd32cd) \
    X(chipmem.size) X(bogomem.size) X(fastmem[0].size) X(z3fastmem[0].size) X(mbresmem_low.size) X(mbresmem_high.size) \
    X(nr_floppies) X(floppyslots[0].dfxtype) X(floppyslots[1].dfxtype) X(floppyslots[2].dfxtype) X(floppyslots[3].dfxtype) \
    X(floppy_read_only) \
    X(floppy_speed) X(immediate_blits) X(waiting_blits) X(collision_level) \
    X(ntscmode) X(seed) X(sound_freq) X(sound_stereo) X(produce_sound) X(sound_auto) X(sound_interpol) \
    X(sound_filter) X(sound_filter_type) X(start_gui) X(reset_delay) X(crash_auto_reset) \
    X(gfx_autoresolution) X(gfx_autoresolution_vga) X(gfx_framerate) X(gfx_resolution) X(gfx_vresolution) \
    X(gfx_iscanlines) X(gfx_pscanlines) X(gfx_scandoubler) X(leds_on_screen) X(refresh_indicator) \
    X(input_mouse_speed) X(input_joymouse_speed) X(input_autofire_linecnt) X(input_autoswitch) \
    X(illegal_mem) X(turbo_emulation) X(turbo_boot) X(cachesize) X(cpu_thread) X(uaeboard) \
    X(socket_emu) X(win32_automount_drives) X(filesys_no_uaefsdb) X(parallel_demand) X(use_serial) X(serial_demand) \
    X(nr_floppies) X(picasso96_nocustom) \
    X(gfx_lores_mode) X(gfx_overscanmode) X(gfx_xcenter) X(gfx_ycenter) X(gfx_extrawidth) X(gfx_extraheight) \
    X(gfx_threebitcolors) X(gfx_blackerthanblack) X(display_calibration) X(gfx_monitorblankdelay) \
    X(lightpen_crosshair) X(cs_optimizations) X(cs_hvcsync) \
    X(jports[0].jd[0].id) X(jports[0].jd[0].mode) X(jports[0].jd[1].id) X(jports[0].jd[1].mode) \
    X(jports[1].jd[0].id) X(jports[1].jd[0].mode) X(jports[1].jd[1].id) X(jports[2].jd[0].id) X(jports[3].jd[0].id) \
    X(input_contact_bounce) X(input_tablet) X(input_autoswitchleftright) X(input_keyboard_type) \
    X(input_advancedmultiinput) X(keyboard_mode) X(cs_resetwarning) X(dongle) \
    X(sound_stereo_separation) X(sound_mixed_stereo_delay) X(sound_volume_paula) X(sound_volume_master)

// The chipset facts upstream's A600 and A1200 tables set and the A500 profile does not name.
#define PINNED_A600(X) \
    X(cs_ksmirror_a8) X(cs_ciaoverlay) X(cs_ciatype[0]) X(cs_ciatype[1]) X(cs_eclocksync) X(cs_df0idhw) X(cs_ciatodbug) \
    X(cs_1mchipjumper)

static void print_pinned(const char *standard, const uae_prefs &p, bool table)
{
#define X(field) printf("pinned %s %s %lld\n", standard, #field, (long long)(p.field));
    PINNED(X)
    if (table) {
        PINNED_A600(X)
    }
#undef X
    printf("pinned %s romfile %s\n", standard, p.romfile);
    for (int i = 0; i < 4; ++i) printf("pinned %s floppyslots[%d].df %s\n", standard, i, p.floppyslots[i].df);
}

static void print_diff(const char *standard)
{
    const unsigned char *a = reinterpret_cast<const unsigned char *>(&upstream_prefs);
    const unsigned char *b = reinterpret_cast<const unsigned char *>(&bridge_prefs);
    for (size_t i = 0; i < sizeof(uae_prefs);) {
        if (a[i] == b[i]) { ++i; continue; }
        size_t j = i;
        while (j < sizeof(uae_prefs) && a[j] != b[j]) ++j;
        printf("diff %s %zu %zu\n", standard, i, j - i);
        i = j;
    }
}

int main(int argc, char **argv)
{
    const bool a600 = argc > 1 && !strcmp(argv[1], "a600");
    const bool a1200 = argc > 1 && !strcmp(argv[1], "a1200");
    if (argc > 1 && !a600 && !a1200 && strcmp(argv[1], "a500")) return 64;
    printf("size %zu\n", sizeof(uae_prefs));
    for (uint32_t standard : {P6_VIDEO_PAL, P6_VIDEO_NTSC}) {
        const char *name = standard == P6_VIDEO_PAL ? "pal" : "ntsc";
        const p6_config config = make_config(a1200 ? P6_MACHINE_A1200 : a600 ? P6_MACHINE_A600 : P6_MACHINE_A500, standard);
        // What upstream itself builds (cfgfile default_prefs, model 0 = A500, 2 = A600 or 4 = A1200, config 0, fixup).
        default_prefs(&currprefs, true, 0);
        built_in_prefs(&currprefs, a1200 ? 4 : a600 ? 2 : 0, 0, 0, 0);
        fixup_prefs(&currprefs, true);
        memcpy(&upstream_prefs, &currprefs, sizeof currprefs);
        p6_bridge::build_preferences(config);
        memcpy(&bridge_prefs, &currprefs, sizeof currprefs);
        print_pinned(name, bridge_prefs, a600 || a1200);
        print_diff(name);
    }
    return 0;
}
