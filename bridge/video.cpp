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
#include "config.h"
#include "error_util.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "video_present.h"
#include "rtg_device.h"
#include "rtg_uaegfx.h"

namespace p6_bridge {

namespace {

using p6_session::session;

// The raster the configuration asked for (T72): the engine's gfx_resolution and gfx_vresolution, whose profile values are hires and double.
int session_resolution() { return engine_resolution(session.config.video_resolution); }
int session_line_mode() { return engine_line_mode(session.config.video_line_mode); }

// The standard's pixel aspect lives in video_geometry.h pixel_aspect (the pinned PUAE reference uses the same PAL 26:25 and NTSC 43:50 for a hires pixel on a double line; the engine
// itself has no display aspect).

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
    // SetSwitch is guest state.  Once it selects a valid RTG mode, convert it
    // at the same emulated unit boundary as the chipset renderer.
    if (session.config.rtg_memory == P6_RTG_MEMORY_4_MIB && p6_uaegfx_selected() &&
        p6_rtg_present() == P6_RTG_OK)
        session.handed_over = true;
}

p6_result describe_video(p6_frame &frame, bool deliver, p6_error *error)
{
    (void)error;
    // The buffer is the one promised at load (it never changes while running); aspect and cadence are those of the beam the unit ran on.
    const int res = session_resolution(), vres = session_line_mode();
    const bool rtg_profile = session.config.rtg_memory == P6_RTG_MEMORY_4_MIB;
    const bool rtg_selected = rtg_profile && p6_uaegfx_selected();
    const uint32_t width = rtg_profile ? P6_RTG_MAX_WIDTH : config_buffer_width(session.config);
    const uint32_t height = rtg_profile ? P6_RTG_MAX_HEIGHT : config_buffer_height(session.config);
    const bool ntsc = beam_is_ntsc();

    frame.video = nullptr;
    frame.video_bytes = 0;
    frame.video_format = P6_PIXEL_XRGB8888;
    frame.video_width = width;
    frame.video_height = height;
    frame.video_stride_bytes = rtg_profile ? P6_RTG_OUTPUT_STRIDE_BYTES : P6_VIDEO_STRIDE_BYTES;
    frame.active = p6_rect{0, 0, 0, 0};
    if (rtg_selected) {
        frame.aspect_num = 1;
        frame.aspect_den = 1;
    } else {
        pixel_aspect(ntsc, res, vres, &frame.aspect_num, &frame.aspect_den);
    }
    frame.field_parity = P6_FIELD_PROGRESSIVE;

    p6_video_frame_info info{};
    p6_rtg_frame rtg_info{};
    const bool have = rtg_selected ? p6_rtg_frame_get(&rtg_info) == P6_RTG_OK
                                   : p6_video_frame_get(&info) == 0;
    const uint32_t flags = have ? info.flags : 0;
    const Rate rate = cadence(flags);
    frame.cadence_num = rate.num;
    frame.cadence_den = rate.den;
    // Single lines do not weave: each field of an interlaced guest is a whole picture of its own rows, so the buffer is progressive whatever the beam does (the cadence above still
    // carries the beam).
    if ((flags & P6_VIDEO_FRAME_INTERLACED) && vres != 0)
        frame.field_parity = (flags & P6_VIDEO_FRAME_LONG_FIELD) ? P6_FIELD_EVEN : P6_FIELD_ODD;

    // A picture exists for this unit when the renderer handed a new one over, it was not marked as
    // discarded (mode being reset) and it fits the buffer that was promised.
    const uint32_t active_width = rtg_selected ? rtg_info.width : info.width;
    const uint32_t active_height = rtg_selected ? rtg_info.height : info.height;
    const bool fresh = session.handed_over && have && (rtg_selected || !(info.flags & P6_VIDEO_FRAME_DISCARD)) &&
                       active_width <= width && active_height <= height;
    if (!fresh) {
        frame.flags |= P6_FRAME_NO_VIDEO;
        return P6_OK;
    }
    frame.active = p6_rect{(width - active_width) / 2, (height - active_height) / 2,
                           active_width, active_height};
    if (!deliver) {
        frame.flags |= P6_FRAME_NO_VIDEO;
        return P6_OK;
    }
    if (rtg_profile) {
        if (!rtg_selected && p6_rtg_canvas_compose(static_cast<const uint32_t *>(info.pixels),
                                                    info.width, info.height, info.stride_bytes) != P6_RTG_OK) {
            frame.flags |= P6_FRAME_NO_VIDEO;
            frame.active = p6_rect{0, 0, 0, 0};
            return P6_OK;
        }
        frame.video = p6_rtg_canvas_data();
        frame.video_bytes = uint64_t(P6_RTG_OUTPUT_STRIDE_BYTES) * P6_RTG_MAX_HEIGHT;
    } else {
        frame.video = info.pixels;
        frame.video_bytes = uint64_t(P6_VIDEO_STRIDE_BYTES) * height;
    }
    return P6_OK;
}

}  // namespace p6_bridge

// The raster a configuration delivers, before anything is initialized: the same numbers p6_run_frame reports for it, so that a frontend can announce the maximum geometry at load.
extern "C" p6_result p6_query_video(const p6_config *config, p6_video_geometry *geometry, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    if (!config || !geometry) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "config or geometry is null");
    if (geometry->abi_version != P6_ABI_VERSION || geometry->struct_size != sizeof *geometry)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "geometry has the wrong ABI version or size");
    if (const p6_result checked = p6_bridge::validate_config(*config, error)) return checked;
    const int res = p6_bridge::engine_resolution(config->video_resolution), vres = p6_bridge::engine_line_mode(config->video_line_mode);
    const bool ntsc = config->video_standard == P6_VIDEO_NTSC;
    geometry->buffer_width = config->rtg_memory ? P6_RTG_MAX_WIDTH : p6_bridge::config_buffer_width(*config);
    geometry->buffer_height = config->rtg_memory ? P6_RTG_MAX_HEIGHT : p6_bridge::config_buffer_height(*config);
    geometry->active_width = p6_bridge::active_width(res);
    geometry->active_height = p6_bridge::active_height(ntsc, vres);
    p6_bridge::pixel_aspect(ntsc, res, vres, &geometry->aspect_num, &geometry->aspect_den);
    return P6_OK;
}
