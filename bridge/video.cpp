// Video delivery: what p6_run_frame reports about the picture of each unit.
//
// The renderer draws into one static XRGB8888 buffer (ports/portable/video_present.cpp). For the
// fixed A500 preset (hires, line doubled, overscan mode 3, no centering) the drawn raster is
// 756 x 574 for PAL and 756 x 484 for NTSC, and does not depend on what the guest programs; a guest
// that leaves the long-field bit set makes it 576 / 486 rows. The bridge therefore reports a constant
// buffer per standard (the long-frame raster) and the raster actually drawn as `active`. Everything
// below is derived from the flags the port captured when the renderer finished the pass, never from
// live engine variables, which already describe the next field when the unit returns.
// Measurements and code references: reports/tasks/T08b.md and docs/adr/0006-video-output.md.
#include "video.h"
#include "video_geometry.h"
#include "session.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "video_present.h"

namespace p6_bridge {

namespace {

using p6_session::session;

// Pixel aspect of one pixel of the hires, line-doubled raster (the pinned PUAE reference uses the
// same PAL 26:25 and NTSC 43:50; the engine itself has no display aspect).
constexpr uint32_t kAspectPal[2] = {26, 25};
constexpr uint32_t kAspectNtsc[2] = {43, 50};

// Units per second as integers: CHIPSET_CLOCK / colour clocks in the unit, reduced. PAL lines are 227 CCK.
// NTSC lines alternate 227 and 228 CCK. Measured lengths in CCK: PAL 70824 (312 lines), 71051 (313 lines),
// interlaced pair 141875; NTSC 59605, non-interlaced long 119665 per two units, interlaced pair 119437.5.
struct Rate {
    uint32_t num, den;
};

Rate cadence(bool ntsc, uint32_t frame_flags)
{
    const bool interlaced = frame_flags & P6_VIDEO_FRAME_INTERLACED;
    const bool long_field = frame_flags & P6_VIDEO_FRAME_LONG_FIELD;
    if (!ntsc) {
        if (interlaced) return {1418758, 28375};
        return long_field ? Rate{3546895, 71051} : Rate{3546895, 70824};
    }
    if (interlaced) return {2863636, 47775};
    return long_field ? Rate{1431818, 23933} : Rate{715909, 11921};
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
    const bool ntsc = session.config.video_standard == P6_VIDEO_NTSC;
    const uint32_t height = ntsc ? kBufferHeightNtsc : kBufferHeightPal;

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
    const Rate rate = cadence(ntsc, flags);
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
