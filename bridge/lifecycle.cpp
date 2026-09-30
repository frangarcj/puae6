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
#include "session.h"
#include "config.h"
#include "input.h"
#include "media.h"
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

namespace p6_session {
Session session;
}

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace {

p6_config &config = session.config;

// How far the engine start-up got, so that a failed or finished load undoes exactly that.
enum class Stage { None, Blob, Devices, CpuBegun };

Stage stage = Stage::None;

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
    p6_bridge::forget_identity();
    quit_program = 0;
    stage = Stage::None;
}

// The steps of real_main2() that the profile keeps, in their original order. Left out on purpose:
// graphics_setup/machdep_init/target_run/gui_update (no desktop), native2amiga_install (AUTOCONFIG off)
// and init_audio (see below).
p6_result start_engine(p6_buffer firmware, const p6_media *media, uint32_t media_count, p6_error *error)
{
    p6_bridge::build_preferences(config);
    if (const p6_result preset = p6_bridge::check_preset(error))
        return preset;
    if (p6_blob_register(p6_bridge::kFirmwareId, P6_BLOB_ROM, firmware.data, size_t(firmware.bytes)) != P6_BLOB_OK)
        return p6_fail(error, P6_ERROR_CAPACITY, "firmware could not be registered");
    stage = Stage::Blob;
    if (const p6_result registered = p6_bridge::register_media(media, media_count, error))
        return registered;

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
    if (const p6_result loaded = p6_bridge::check_loaded_firmware(error))
        return loaded;
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
            : p6_bridge::check_loaded_firmware(error);
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
    if (session.state != State::Uninitialized)
        return p6_fail(error, P6_ERROR_BAD_STATE, "already initialized");
    if (const p6_result checked = p6_bridge::validate_config(*supplied, error))
        return checked;
    config = *supplied;
    session.state = State::Initialized;
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
    if (session.state != State::Initialized)
        return p6_fail(error, P6_ERROR_BAD_STATE,
                       session.state == State::Uninitialized ? "p6_init has not been called" : "an image is already loaded");
    if (const p6_result checked = p6_bridge::validate_firmware(firmware, error))
        return checked;
    if (const p6_result checked = p6_bridge::validate_media(media, media_count, error))
        return checked;

    const p6_result result = start_engine(firmware, media, media_count, error);
    if (result != P6_OK) {
        release_engine();
        return result;
    }
    p6_bridge::remember_identity(firmware, media, media_count);
    p6_bridge::forget_input();
    session.unit_index = 0;
    session.master_ticks = 0;
    session.cycles_seen = get_cycles();
    session.state = State::Loaded;
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
    if (session.state == State::Loaded || session.state == State::Faulted) {
        release_engine();
        session.state = State::Initialized;
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
    if (session.state == State::Loaded || session.state == State::Faulted)
        release_engine();
    session.state = State::Uninitialized;
    return P6_OK;
}
