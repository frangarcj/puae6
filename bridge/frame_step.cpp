// p6_run_frame: the only public primitive that advances the emulated machine (docs/02-contracts.md).
//
// One call runs exactly one video unit, from one rendered field boundary to the next, through
// m68k_go_unit(). The CPU loop returns at an instruction boundary right after the rendering of
// the field, and the next call resumes it there; no stack is switched and no second scheduler
// exists. Order inside a call: queued reset, media events, input, then the unit. Reset, media
// and input are not implemented yet (T10-T13), so a non-empty input is refused instead of being
// ignored, and video/audio delivery is not filled in (T08, T11): the caps stay clear.
#include "p6.h"
#include "error_util.h"
#include "session.h"
#include "video.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "memory.h"
#include "newcpu.h"
#include "rom_status.h"
#include "rtc_time.h"
#include "sounddep/sound.h"

#include <string.h>

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace {

bool input_is_valid(const p6_input &in)
{
    if (in.abi_version != P6_ABI_VERSION || in.struct_size != sizeof in)
        return false;
    for (uint32_t r : in.reserved)
        if (r) return false;
    for (const p6_port_input &port : in.ports) {
        if (port.type != P6_PORT_NONE && port.type != P6_PORT_JOYSTICK && port.type != P6_PORT_MOUSE)
            return false;
        if (port.reserved || (port.type == P6_PORT_NONE && (port.buttons || port.dx || port.dy)))
            return false;
    }
    return true;
}

bool input_is_empty(const p6_input &in)
{
    for (uint32_t key : in.keys)
        if (key) return false;
    for (const p6_port_input &port : in.ports)
        if (port.type != P6_PORT_NONE) return false;
    return true;
}

p6_result fault(p6_error *error, p6_result code, const char *text)
{
    session.state = State::Faulted;   // the CPU loop was left or a service failed: only unload recovers
    return p6_fail(error, code, text);
}

// Until T11 delivers audio, the PCM the chipset produced is dropped after every unit; left in the queue it
// would fill up and stop the machine. Paula's filters and phases advance regardless (DET05).
void discard_audio()
{
    int16_t scratch[2048];
    size_t written;
    do {
        written = 0;
        if (p6_pcm_read(scratch, sizeof scratch / (2 * sizeof scratch[0]), &written) != 0) break;
    } while (written);
}

// Scheduler ticks are monotonic for the caller even though the engine counter restarts at a reset:
// a smaller reading means a reset happened and the ticks since it are all new.
void account_ticks()
{
    const int64_t now = int64_t(get_cycles());
    session.master_ticks += uint64_t(now >= session.cycles_seen ? now - session.cycles_seen : now);
    session.cycles_seen = now;
}

}  // namespace

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
    if (render_flags & ~(P6_RENDER_VIDEO | P6_RENDER_AUDIO))
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown render flags");
    if (!input || !input_is_valid(*input))
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "input is missing or malformed");
    if (!input_is_empty(*input))
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "keyboard and port input are not implemented yet");

    // 1. Reset and 2. media events: nothing is queued yet. 3. Input: empty. The head of a hard reset
    // runs inside this call, in APPLY, because its random start-up delay consumes emulated time.
    const int applied = m68k_go_unit(M68K_UNIT_APPLY);
    if (applied != M68K_UNIT_APPLIED)
        return fault(error, P6_ERROR_BAD_STATE, "the CPU loop ended while applying the reset");
    if (p6_memory_rom_status() != P6_ROM_LOAD_OK)
        return fault(error, P6_ERROR_NEED_FIRMWARE, "the firmware is no longer available");

    // 4. One video unit.
    p6_bridge::begin_unit();
    if (m68k_go_unit(M68K_UNIT_RUN) != M68K_UNIT_DONE)
        return fault(error, P6_ERROR_BAD_STATE, "the CPU loop ended before the unit boundary");
    account_ticks();
    // The picture is handed over whether or not the caller wants it: presentation bookkeeping must not
    // depend on P6_RENDER_VIDEO, and the STEP01 observer may already have done it at the boundary.
    p6_bridge::present_unit();
    discard_audio();
    if (p6_rtc_status() != 0)
        return fault(error, P6_ERROR_INTERNAL, "the clock service failed during the unit");
    if (p6_sound_status() != P6_PCM_OK)
        return fault(error, P6_ERROR_UNSUPPORTED, "the audio transport failed during the unit");

    // 5. Result. Audio delivery belongs to T11 and stays empty.
    p6_frame result;
    memset(&result, 0, sizeof result);
    result.abi_version = P6_ABI_VERSION;
    result.struct_size = sizeof result;
    result.master_ticks = session.master_ticks;
    result.unit_index = ++session.unit_index;
    if (const p6_result described = p6_bridge::describe_video(result, render_flags & P6_RENDER_VIDEO, error))
        return fault(error, described, "the video description failed");
    *frame = result;
    return P6_OK;
}
