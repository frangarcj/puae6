// Load-time lifecycle of the native ABI: p6_init, p6_load, p6_unload and p6_shutdown.
//
// p6_load runs the start-up sequence of WinUAE's real_main2() that the A500 profile keeps, in
// the same order, without the desktop parts (windows, GUI, command line, config files). It
// stops before the first CPU instruction: the reset head and everything after it advance only
// inside p6_run_frame, because they consume emulated time (the random start-up delay).
//
// The configuration is fixed to the built-in A500 preset (68000 cycle exact, OCS, 512 KiB Chip +
// 512 KiB Slow, MSM6242B clock) plus the fields of p6_config. Public validation of the whole
// configuration, media and state belong to later tasks; nothing here claims them.
#include "p6.h"
#include "error_util.h"
#include <stdio.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "newcpu.h"
#include "inputdevice.h"
#include "savestate.h"
#include "keybuf.h"
#include "disk.h"
#include "devices.h"
#include "audio.h"
#include "gensound.h"
#include "sounddep/sound.h"
#include "blob_io.h"
#include "diagnostics.h"
#include "rom_status.h"
#include "rommgr.h"
#include "rtc_time.h"
#include "video_present.h"

namespace {

enum class State { Uninitialized, Initialized, Loaded };
// How far the engine start-up got, so that a failed or finished load undoes exactly that.
enum class Stage { None, Blob, Devices, CpuBegun };

State state = State::Uninitialized;
Stage stage = Stage::None;
p6_config config;
bool active;  // an API call is in progress: calls are not reentrant

const char *const kFirmwareId = "p6-firmware.rom";

struct Guard {
    bool ok;
    Guard() : ok(!active) { active = true; }
    ~Guard() { if (ok) active = false; }
};

bool config_supported(const p6_config &c, p6_error *error, p6_result *result)
{
    if (c.abi_version != P6_ABI_VERSION || c.struct_size != sizeof c) {
        *result = p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config has the wrong ABI version or size");
    } else if (c.machine != P6_MACHINE_A500) {
        *result = p6_fail(error, P6_ERROR_UNSUPPORTED, "only the A500 is supported");
    } else if (c.video_standard != P6_VIDEO_PAL && c.video_standard != P6_VIDEO_NTSC) {
        *result = p6_fail(error, P6_ERROR_BAD_ARGUMENT, "video standard must be PAL or NTSC");
    } else if (c.audio_rate_hz != 44100 && c.audio_rate_hz != 48000) {
        *result = p6_fail(error, P6_ERROR_UNSUPPORTED, "audio rate must be 44100 or 48000 Hz");
    } else if (c.flags != 0 || c.reserved[0] || c.reserved[1] || c.reserved[2] || c.reserved[3]) {
        *result = p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config flags and reserved fields must be zero");
    } else {
        return true;
    }
    return false;
}

uint32_t fold_seed(uint64_t seed)
{
    return uint32_t(seed) ^ uint32_t(seed >> 32);
}

// The built-in A500 preset with the deterministic profile's fixed choices applied.
void build_preferences()
{
    default_prefs(&currprefs, true, 0);
    // The preset names its Kickstart by id through the ROM list, which has no entries here; the
    // firmware is the buffer given to p6_load, so the result of configure_rom() is not needed.
    built_in_prefs(&currprefs, 0, 0, 0, 0);
    currprefs.ntscmode = config.video_standard == P6_VIDEO_NTSC;
    currprefs.seed = fold_seed(config.random_seed);
    currprefs.sound_freq = int(config.audio_rate_hz);
    currprefs.sound_stereo = SND_STEREO;
    currprefs.produce_sound = 3;
    currprefs.sound_auto = 0;
    currprefs.start_gui = false;
    currprefs.gfx_autoresolution = 0;        // the presentation provider serves one fixed raster
    currprefs.gfx_autoresolution_vga = false;
    currprefs.reset_delay = 0;        // config-invariants.json: the delayed-reset wait cannot poll
    currprefs.crash_auto_reset = 0;   // config-invariants.json: a halted CPU stays in the polling wait
    _tcscpy(currprefs.romfile, kFirmwareId);
    fixup_prefs(&currprefs, true);
}

p6_result rom_failure(p6_error *error)
{
    switch (p6_memory_rom_status()) {
    case P6_ROM_UNLOADED:
    case P6_ROM_OPEN_ERROR:
        return p6_fail(error, P6_ERROR_NEED_FIRMWARE, "firmware was not found");
    case P6_ROM_READ_ERROR:
    case P6_ROM_UNSUPPORTED:
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is not an accepted Kickstart image");
    case P6_ROM_NO_MEMORY:
        return p6_fail(error, P6_ERROR_CAPACITY, "not enough memory for the firmware");
    }
    return p6_fail(error, P6_ERROR_INTERNAL, "firmware status is inconsistent");
}

// Undo whatever start-up reached; safe to call again.
void release_engine()
{
    if (stage >= Stage::CpuBegun)
        m68k_go_unit(M68K_UNIT_LEAVE);
    if (stage >= Stage::Devices) {
        do_leave_program();
        p6_video_clear();
    }
    p6_rtc_unload();
    p6_blob_clear();
    quit_program = 0;
    stage = Stage::None;
}

// The steps of real_main2() that the profile keeps, in their original order. Left out on purpose:
// graphics_setup/machdep_init/target_run/gui_update (no desktop), native2amiga_install (AUTOCONFIG off)
// and init_audio (see below).
p6_result start_engine(p6_buffer firmware, p6_error *error)
{
    if (p6_blob_register(kFirmwareId, P6_BLOB_ROM, firmware.data, size_t(firmware.bytes)) != P6_BLOB_OK)
        return p6_fail(error, P6_ERROR_CAPACITY, "firmware could not be registered");
    stage = Stage::Blob;

    build_preferences();
    if (!p6_sound_validate_config(currprefs))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "audio settings are not supported");
    if (const int video = p6_video_validate_config(&currprefs)) {
        char text[64];
        snprintf(text, sizeof text, "video settings are not supported (code %d)", video);
        return p6_fail(error, P6_ERROR_UNSUPPORTED, text);
    }
    const uint64_t chipset_clock = config.video_standard == P6_VIDEO_NTSC ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    if (p6_rtc_init(config.rtc_epoch_seconds, chipset_clock * CYCLE_UNIT, currprefs.seed) != 0)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the clock epoch was rejected");

    p6_diag_reset();
    p6_gui_reset();
    event_init();
    copy_prefs(&currprefs, &changed_prefs);
    setup_sound();
    inputdevice_init();
    copy_prefs(&changed_prefs, &currprefs);
    inputdevice_updateconfig(&currprefs, &changed_prefs);
    logging_init();
    fixup_prefs(&currprefs, true);
    uaerandomizeseed();
    copy_prefs(&currprefs, &changed_prefs);
    currprefs.produce_sound = 0;    // forces the first audio setup in the reset head
    savestate_init();
    keybuf_init();

    stage = Stage::Devices;
    memory_hardreset(2);
    memory_reset();
    if (p6_memory_rom_status() != P6_ROM_LOAD_OK)
        return rom_failure(error);
    // The loader accepts a bad checksum with a notice only; the decoded image must be intact.
    if (!kickstart_checksum(kickmem_bank.baseaddr, kickmem_bank.reserved_size))
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware checksum is invalid");
    custom_init();
    DISK_init();
    reset_frame_rate_hack();
    init_m68k();
    if (p6_video_init() != P6_VIDEO_CFG_OK)
        return p6_fail(error, P6_ERROR_INTERNAL, "video presentation could not start");
    // real_main2() calls init_audio() here, but with produce_sound forced to 0 above it only fails and, in
    // this port, would latch the transport as unsupported. The reset head opens the transport through
    // check_prefs_changed_audio() -> set_audio() -> init_sound(); its outcome is checked after APPLY.
    inputdevice_updateconfig(&changed_prefs, &currprefs);
    quit_program = UAE_RESET_HARD;

    if (m68k_go_unit(M68K_UNIT_BEGIN) != M68K_UNIT_DONE)
        return p6_memory_rom_status() == P6_ROM_LOAD_OK
            ? p6_fail(error, P6_ERROR_INTERNAL, "the CPU loop could not start")
            : rom_failure(error);
    stage = Stage::CpuBegun;
    return P6_OK;
}

}  // namespace

extern "C" p6_result p6_init(const p6_config *supplied, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (!supplied)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config is null");
    if (state != State::Uninitialized)
        return p6_fail(error, P6_ERROR_BAD_STATE, "already initialized");
    p6_result result = P6_OK;
    if (!config_supported(*supplied, error, &result))
        return result;
    config = *supplied;
    state = State::Initialized;
    return P6_OK;
}

extern "C" p6_result p6_load(p6_buffer firmware, const p6_media *media, uint32_t media_count, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (state != State::Initialized)
        return p6_fail(error, P6_ERROR_BAD_STATE,
                       state == State::Loaded ? "an image is already loaded" : "p6_init has not been called");
    if (media_count != 0)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "media are not supported yet");
    if (!firmware.data || firmware.bytes == 0)
        return p6_fail(error, P6_ERROR_NEED_FIRMWARE, "no firmware was supplied");
    if (firmware.bytes > (UINT64_C(1) << 24))
        return p6_fail(error, P6_ERROR_WRONG_MEDIA, "firmware is far larger than any Kickstart image");

    const p6_result result = start_engine(firmware, error);
    if (result != P6_OK) {
        release_engine();
        return result;
    }
    state = State::Loaded;
    return P6_OK;
}

extern "C" p6_result p6_unload(p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (state == State::Loaded) {
        release_engine();
        state = State::Initialized;
    }
    return P6_OK;
}

extern "C" p6_result p6_shutdown(p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (state == State::Loaded)
        release_engine();
    state = State::Uninitialized;
    return P6_OK;
}
