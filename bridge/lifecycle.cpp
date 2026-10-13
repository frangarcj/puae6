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
#include <string.h>

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

extern void (*p6_counter_restart_hook)(void);   // newcpu.cpp, patch 0073: called by the reset head as the cycle counter starts over
extern void p6_cia_session_begin(void);          // cia.cpp, patch 0074: the CIA's per-frame state is a clean process's at the start of a load
extern void p6_input_session_begin(void);        // inputdevice.cpp, patch 0075: the counters a session of mouse input leaves behind

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace {

p6_config &config = session.config;

// The reset head restarts the engine's cycle counter whatever asked for the reset (p6_reset, the Ctrl-Amiga-Amiga chord, the reset
// warning). Everything the bridge anchors to that counter follows it here: the ticks of the part of the unit that ran before the
// restart are added to the caller's count, and the clock of the board, which keeps time through a reset, is anchored to the new
// counter. A failure of the clock is recorded by the clock and reported by the status check at the end of the unit.
void counter_restarts()
{
    const int64_t now = int64_t(get_cycles());
    if (now > session.cycles_seen)
        session.master_ticks += uint64_t(now - session.cycles_seen);
    session.cycles_seen = 0;
    p6_rtc_rebase(0);
}

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
    p6_counter_restart_hook = nullptr;
    p6_rtc_unload();
    p6_blob_clear();
    p6_bridge::release_input_device_names();
    p6_bridge::forget_media();
    quit_program = 0;
    stage = Stage::None;
}

// The steps of real_main2() that the profile keeps, in their original order. Left out on purpose:
// graphics_setup/machdep_init/target_run/gui_update (no desktop), native2amiga_install (AUTOCONFIG off)
// and init_audio (see below).
p6_result start_engine(p6_buffer firmware, const p6_media *media, uint32_t media_count, p6_error *error)
{
    // T56: Gayle's IDE port is there only while a hard file is behind it
    const bool ide = p6_bridge::ide_attached(config, p6_bridge::hard_files_in(media, media_count));
    p6_bridge::build_preferences(config, ide);
    if (const p6_result mounted = p6_bridge::mount_hard_files(media, media_count, ide, error))
        return mounted;
    if (const p6_result preset = p6_bridge::check_preset(error))
        return preset;
    if (p6_blob_register(p6_bridge::kFirmwareId, P6_BLOB_ROM, firmware.data, size_t(firmware.bytes)) != P6_BLOB_OK)
        return p6_fail(error, P6_ERROR_CAPACITY, "firmware could not be registered");
    stage = Stage::Blob;
    if (config.flags & P6_CONFIG_FLOPPY_WRITES)
        if (p6_blob_overlay_reserve() != P6_BLOB_OK)
            return p6_fail(error, P6_ERROR_CAPACITY, "the write overlay of the floppies could not be reserved");
    if (const p6_result registered = p6_bridge::register_media(media, media_count, 0, error))
        return registered;

    // The reset head starts the engine's cycle counter at zero, as a clean process has it; the clock is anchored to that counter, so
    // it must be anchored to the counter the head will use, not to where the previous session left it.
    set_cycles(0);
    const uint64_t chipset_clock = config.video_standard == P6_VIDEO_NTSC ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    if (p6_rtc_init(config.rtc_epoch_seconds, chipset_clock * CYCLE_UNIT, currprefs.seed) != 0)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the clock epoch was rejected");

    p6_diag_reset();
    p6_gui_reset();
    event_init();
    copy_prefs(&currprefs, &changed_prefs);
    setup_sound();
    p6_input_session_begin();       // no input counter of a previous session reaches this one (before inputdevice_init sets up the ports)
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
    p6_bridge::reset_keyboard();    // the key table and the reset-chord state belong to a session: a chord held when the last one
                                    // ended would make the first key event of this load reset the machine

    stage = Stage::Devices;
    p6_cia_session_begin();         // the first frame of a load is the first frame of a process
    p6_counter_restart_hook = counter_restarts;
    memory_hardreset(2);
    memory_reset();
    if (const p6_result loaded = p6_bridge::check_loaded_firmware(error))
        return loaded;
    // Before 1.3 (version 33) the board is not found by autoconfig: the engine patches PutMsg and DoIO (the "boot hack"), which this profile does not test.
    if (p6_bridge::hard_files_in(media, media_count) && kickstart_version < 34)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "a hard file needs Kickstart 1.3 or later");
    custom_init();
    // A load is a new power-on. A clean process starts with the CPU registers and the beam at zero, but the reset head keeps
    // D0-D7/A0-A6 and lets the beam run on, so whatever the previous session left there would decide how the new one begins
    // (its first unit would depend on how long the last one ran). memset(&regs) comes before init_m68k(), where a clean
    // process also has it zero.
    vpos = 0;
    DISK_init();
    reset_frame_rate_hack();
    memset(&regs, 0, sizeof regs);
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
    session.audio_frames_total = 0;     // "since load": the previous session's count used to carry over (found by the state probe)
    session.handed_over = false;
    session.state = State::Loaded;
    return P6_OK;
}

extern "C" p6_result p6_reset(p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE,
                       session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    // Before the first frame the power-on reset is still pending: there is nothing to start over.
    session.reset_queued = session.started;
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
