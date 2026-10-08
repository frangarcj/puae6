// Video delivery: what p6_run_frame reports about the picture of each unit.
//
// The renderer draws into one static XRGB8888 buffer (ports/portable/video_present.cpp). For the
// fixed A500 preset (hires, line doubled, overscan mode 3, no centering) the drawn raster is
// 756 x 574 for PAL and 756 x 484 for NTSC, and does not depend on what the guest programs; a guest
// that leaves the long-field bit set makes it 576 / 486 rows. The bridge therefore reports a constant
// buffer (the long-frame raster of the largest standard the machine can run) and the raster actually
// drawn as `active`. Field parity and the lengthening of the unit come from the flags the port captured
// when the renderer finished the pass, never from live variables, which already describe the next field
// when the unit returns. The standard, line count and line length of the beam (aspect, cadence) are
// the integers of the engine's beam registers, which nothing changes between that pass and the return
// of the unit: an ECS guest that programs BEAMCON0 is reported with the beam it really runs.
// Measurements and code references: reports/tasks/T08b.md and docs/adr/0006-video-output.md.
#include "video.h"
#include "video_geometry.h"
#include "session.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "video_present.h"

namespace p6_bridge {

namespace {

using p6_session::session;

// Pixel aspect of one pixel of the hires, line-doubled raster (the pinned PUAE reference uses the
// same PAL 26:25 and NTSC 43:50; the engine itself has no display aspect).
constexpr uint32_t kAspectPal[2] = {26, 25};
constexpr uint32_t kAspectNtsc[2] = {43, 50};

// The standard of the beam the unit ran on, as init_beamcon0() decides it: an ECS Agnus follows the PAL bit of BEAMCON0, which a guest
// may program against the configuration; an OCS Agnus has no such register and keeps the configured standard. Read at the boundary the
// unit ended on: nothing that sets the beam runs between the renderer's pass and the return of the unit.
bool beam_is_ntsc()
{
    if (ecs_agnus) return (beamcon0 & BEAMCON0_PAL) == 0;
    return session.config.video_standard == P6_VIDEO_NTSC;
}

// Units per second as integers: the chipset clock over the colour clocks in the unit, reduced. The unit length comes from the integers
// of the beam, never from the engine's float vblank_hz or from a measurement: lines per unit are maxvpos, plus half a line when the
// guest interlaces and a whole one for the long field (VARBEAMEN has no long field: maxvpos_long is -1); a line is maxhpos_short
// clocks, plus half a clock on every line when the beam alternates 227/228 (NTSC timing without LOLDIS: custom.cpp linetoggle). The
// clock is that of the configuration, the one the engine and the RTC anchor their time to. Standard beams give the measured lengths:
// PAL 70824 CCK (312 lines), 71051 (313), interlaced pair 141875; NTSC 59605 (262 lines), 119665 per two long units, 119437.5
// interlaced pair.
struct Rate {
    uint32_t num, den;
};

uint64_t gcd(uint64_t a, uint64_t b)
{
    while (b) {
        const uint64_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

Rate cadence(uint32_t frame_flags)
{
    const bool ntsc = beam_is_ntsc();
    const uint16_t bc = ecs_agnus ? beamcon0 : 0;                   // an OCS Agnus has no BEAMCON0
    const bool free_beam = (bc & BEAMCON0_VARBEAMEN) != 0;
    const bool interlaced = frame_flags & P6_VIDEO_FRAME_INTERLACED;
    const bool long_field = frame_flags & P6_VIDEO_FRAME_LONG_FIELD;
    const bool toggle = ntsc && !(bc & BEAMCON0_LOLDIS);
    // maxhpos_short / maxvpos are the nominal beam of the standard when the guest programmed nothing (init_beamcon0).
    const uint64_t clocks = uint64_t(maxhpos_short > 0 ? maxhpos_short : (ntsc ? MAXHPOS_NTSC : MAXHPOS_PAL));
    const uint64_t lines = uint64_t(maxvpos > 0 ? maxvpos : (ntsc ? MAXVPOS_NTSC : MAXVPOS_PAL));
    const uint64_t half_lines = 2 * lines + (free_beam ? 0 : interlaced ? 1 : long_field ? 2 : 0);
    const uint64_t half_clocks = 2 * clocks + (toggle ? 1 : 0);
    const uint64_t clock = session.config.video_standard == P6_VIDEO_NTSC ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    uint64_t num = 4 * clock, den = half_lines * half_clocks;      // clock / (half_lines * half_clocks / 4)
    const uint64_t g = gcd(num, den);
    num /= g;
    den /= g;
    return {uint32_t(num), uint32_t(den)};
}

}  // namespace

void begin_unit()
{
    session.handed_over = false;
}

void present_unit()
{
    // The port hands a pass over once: asking again before the renderer finished another one returns 0.
    // So the STEP01 observer (at each boundary) and p6_run_frame (after the unit) can both call this.
    if (p6_video_present()) session.handed_over = true;
}

p6_result describe_video(p6_frame &frame, bool deliver, p6_error *error)
{
    (void)error;
    // The buffer is the one promised at load (it never changes while running); aspect and cadence are those of the beam the unit ran on.
    const bool has_beamcon0 = session.config.machine != P6_MACHINE_A500;   // ECS and AGA Agnus (A600, A1200); the OCS A500 has no BEAMCON0
    const uint32_t height = buffer_height(session.config.video_standard == P6_VIDEO_NTSC, has_beamcon0);
    const bool ntsc = beam_is_ntsc();

    frame.video = nullptr;
    frame.video_bytes = 0;
    frame.video_format = P6_PIXEL_XRGB8888;
    frame.video_width = kBufferWidth;
    frame.video_height = height;
    frame.video_stride_bytes = P6_VIDEO_STRIDE_BYTES;
    frame.active = p6_rect{0, 0, 0, 0};
    frame.aspect_num = ntsc ? kAspectNtsc[0] : kAspectPal[0];
    frame.aspect_den = ntsc ? kAspectNtsc[1] : kAspectPal[1];
    frame.field_parity = P6_FIELD_PROGRESSIVE;

    p6_video_frame_info info;
    const bool have = p6_video_frame_get(&info) == 0;
    const uint32_t flags = have ? info.flags : 0;
    const Rate rate = cadence(flags);
    frame.cadence_num = rate.num;
    frame.cadence_den = rate.den;
    if (flags & P6_VIDEO_FRAME_INTERLACED)
        frame.field_parity = (flags & P6_VIDEO_FRAME_LONG_FIELD) ? P6_FIELD_EVEN : P6_FIELD_ODD;

    // A picture exists for this unit when the renderer handed a new one over, it was not marked as
    // discarded (mode being reset) and it fits the buffer that was promised.
    const bool fresh = session.handed_over && have && !(info.flags & P6_VIDEO_FRAME_DISCARD) &&
                       info.width <= kBufferWidth && info.height <= height;
    if (!fresh) {
        frame.flags |= P6_FRAME_NO_VIDEO;
        return P6_OK;
    }
    frame.active = p6_rect{0, 0, info.width, info.height};
    if (!deliver) {
        frame.flags |= P6_FRAME_NO_VIDEO;
        return P6_OK;
    }
    frame.video = info.pixels;
    frame.video_bytes = uint64_t(P6_VIDEO_STRIDE_BYTES) * height;
    return P6_OK;
}

}  // namespace p6_bridge
