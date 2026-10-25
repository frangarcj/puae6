// p6_run_frame: the only public primitive that advances the emulated machine (docs/02-contracts.md).
//
// One call runs exactly one video unit, from one rendered field boundary to the next, through
// m68k_go_unit(). The CPU loop returns at an instruction boundary right after the rendering of
// the field, and the next call resumes it there; no stack is switched and no second scheduler
// exists. Order inside a call: queued reset, media events, input, then the unit. Reset is not
// implemented yet (T13). Video and audio come back exactly when requested (T08, T11).
#include "p6.h"
#include "error_util.h"
#include "session.h"
#include "pcm_out.h"
#include "frame_step.h"
#include "video.h"
#include "input.h"
#include "media.h"
#include "drive_sound.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "uae.h"
#include "memory.h"
#include "newcpu.h"
#include "gui.h"
#include "rom_status.h"
#include "rtc_time.h"
#include "sounddep/sound.h"

#include <string.h>

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace {

p6_result fault(p6_error *error, p6_result code, const char *text)
{
    session.state = State::Faulted;   // the CPU loop was left or a service failed: only unload recovers
    return p6_fail(error, code, text);
}

// Scheduler ticks are monotonic for the caller even though the engine counter restarts at a reset. The reset head reports every
// restart (counter_restarts in lifecycle.cpp), which adds what ran before it and zeroes cycles_seen, so the reading only goes
// backwards here for a restart nobody reported; then the ticks since it are all that can be counted.
void account_ticks()
{
    const int64_t now = int64_t(get_cycles());
    session.master_ticks += uint64_t(now >= session.cycles_seen ? now - session.cycles_seen : now);
    session.cycles_seen = now;
}

}  // namespace

bool p6_bridge::run_reset_head()
{
    if (!session.started)
        p6_bridge::apply_boot_media();
    return m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_APPLIED && p6_memory_rom_status() == P6_ROM_LOAD_OK;
}

extern "C" p6_result p6_run_frame(const p6_input *input, uint32_t render_flags, p6_frame *frame, p6_error *error)
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
    if (!frame || frame->abi_version != P6_ABI_VERSION || frame->struct_size != sizeof *frame)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "frame has the wrong ABI version or size");
    if (render_flags & ~(P6_RENDER_VIDEO | P6_RENDER_AUDIO | P6_RENDER_DRIVE_VOLUME_MASK))
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown render flags");
    const unsigned drive_volume = (render_flags & P6_RENDER_DRIVE_VOLUME_MASK) >> P6_RENDER_DRIVE_VOLUME_SHIFT;
    if (drive_volume > 100)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "drive sound volume must be 0..100");
    if (!input)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "input is missing");
    if (const p6_result checked = p6_bridge::validate_input(*input, error))
        return checked;                       // before anything of the machine is touched

    // 1. Reset and 2. media events. The first call also settles the power-on contents of the drives, which the reset head
    // inserts; a queued reset does not exist yet (T13). The head of a hard reset runs inside this call, in
    // APPLY, because its random start-up delay consumes emulated time; input comes after it, so that keys pressed
    // in this unit are not mixed with the keyboard's power-up sequence.
    if (!session.started)
        p6_bridge::apply_boot_media();
    if (session.reset_queued) {
        // Both modes use the upstream reset head. Soft keeps RAM and does not repeat
        // the power-on delay. Tick/RTC accounting follows the same restart hook.
        const bool hard = session.reset_queued == p6_session::ResetHard;
        session.reset_queued = p6_session::ResetNone;
        if (quit_program == 0)
            quit_program = hard ? UAE_RESET_HARD : UAE_RESET;
        p6_bridge::reset_keyboard();
    }
    const int applied = m68k_go_unit(M68K_UNIT_APPLY);
    if (applied != M68K_UNIT_APPLIED)
        return fault(error, P6_ERROR_BAD_STATE, "the CPU loop ended while applying the reset");
    if (p6_memory_rom_status() != P6_ROM_LOAD_OK)
        return fault(error, P6_ERROR_NEED_FIRMWARE, "the firmware is no longer available");
    p6_bridge::apply_media_events();

    // 3. Input.
    p6_bridge::apply_input(*input);
    p6_drive_sound::gain(drive_volume);        // APPLY may reopen audio; consume presentation only after it

    // 4. One video unit.
    p6_bridge::begin_unit();
    p6_bridge::begin_input_unit();
    if (m68k_go_unit(M68K_UNIT_RUN) != M68K_UNIT_DONE)
        return fault(error, P6_ERROR_BAD_STATE, "the CPU loop ended before the unit boundary");
    account_ticks();
    // Paula is brought up to the boundary first (the STEP01 observer does the same at every boundary), then the PCM is
    // taken whether or not the caller wants it: delivering or dropping it never changes what Paula does.
    p6_bridge::sync_audio_to_boundary();
    uint64_t audio_frames = 0;
    if (const p6_result taken = p6_bridge::take_audio(&audio_frames, error))
        return fault(error, taken, "the PCM queue failed during the unit");
    // The picture is handed over whether or not the caller wants it: presentation bookkeeping must not
    // depend on P6_RENDER_VIDEO, and the STEP01 observer may already have done it at the boundary.
    p6_bridge::present_unit();
    if (p6_rtc_status() != 0)
        return fault(error, P6_ERROR_INTERNAL, "the clock service failed during the unit");
    if (p6_sound_status() != P6_PCM_OK)
        return fault(error, P6_ERROR_UNSUPPORTED, "the audio transport failed during the unit");

    // 5. Result. Audio is present (possibly with zero frames) exactly when it was requested.
    p6_frame result;
    memset(&result, 0, sizeof result);
    result.abi_version = P6_ABI_VERSION;
    result.struct_size = sizeof result;
    result.master_ticks = session.master_ticks;
    result.unit_index = ++session.unit_index;
    if (!p6_bridge::guest_polled_input())
        result.flags |= P6_FRAME_LAG;
    if (gui_ledstate & (0xFu << LED_DF0))      // motor on or writing in any floppy drive; the host gui port keeps the engine's own LED bits
        result.flags |= P6_FRAME_DRIVE_LIGHT;
    if (render_flags & P6_RENDER_AUDIO) {
        result.audio = p6_bridge::audio_samples();
        result.audio_frames = audio_frames;
    }
    if (const p6_result described = p6_bridge::describe_video(result, render_flags & P6_RENDER_VIDEO, error))
        return fault(error, described, "the video description failed");
    *frame = result;
    return P6_OK;
}
