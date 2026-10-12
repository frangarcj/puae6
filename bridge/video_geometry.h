// Sizes of the delivered picture buffer per video standard, resolution and line mode; no engine dependency, so p6_query_caps can
// report them. Derivation: docs/adr/0006-video-output.md.
#ifndef P6_BRIDGE_VIDEO_GEOMETRY_H
#define P6_BRIDGE_VIDEO_GEOMETRY_H
#include "p6.h"
#include <stdint.h>

namespace p6_bridge {

// The hires, line-doubled raster of the fixed preset: 756 columns; 574 (PAL) / 484 (NTSC) rows for normal
// guests, two more when a guest leaves the long-field bit set without interlace. NTSC lines alternate 227 and 228
// colour clocks and a guest that toggles interlace every unit makes the renderer adopt 228: 760 columns. The buffer
// is the largest of these; whatever exceeds it is refused.
constexpr uint32_t kBufferWidth = 760;
constexpr uint32_t kBufferHeightPal = 576;
constexpr uint32_t kBufferHeightNtsc = 486;

// The raster options (T72, p6_config.video_resolution and video_line_mode) as the engine's own gfx_resolution (0 lores, 1 hires, 2 super-hires) and gfx_vresolution
// (0 single, 1 double). A zero is the profile's setting: hires and double.
constexpr int engine_resolution(uint8_t option) { return option == 0 ? 1 : int(option) - 1; }
constexpr int engine_line_mode(uint8_t option) { return option == 1 ? 0 : 1; }
constexpr bool resolution_option_valid(uint8_t option) { return option <= 3; }
constexpr bool line_mode_option_valid(uint8_t option) { return option <= 2; }
// True when the options are the profile's raster, however they are spelled.
constexpr bool raster_is_profile(uint8_t resolution, uint8_t line_mode)
{
    return engine_resolution(resolution) == 1 && engine_line_mode(line_mode) == 1;
}

// Columns and rows of a raster scale with the resolution (a quarter of a colour clock per hires column, so lores has half the columns and super-hires twice) and with the
// line mode (one row per line instead of two). `res` and `vres` are the engine's values.
constexpr uint32_t buffer_width(int res) { return (kBufferWidth / 2) << res; }

// Whether the Agnus of the machine's preset is ECS or AGA (it has BEAMCON0), read from the preset table of config.cpp: the A500+, A600, A2000 and A1200. The A500, A500 OG, A1000
// and A2000 OG have an OCS Agnus. An unknown machine is the A500.
bool machine_has_ecs_agnus(uint32_t machine);

// Rows of the buffer a loaded machine delivers; announced at load and constant afterwards. An OCS Agnus cannot leave the standard of
// its configuration, so NTSC keeps the short buffer. An ECS or AGA Agnus can be programmed into the other standard (BEAMCON0), so its NTSC
// configuration reserves the PAL height: the raster of a programmed PAL beam still fits. Single line mode halves it (both heights are even).
constexpr uint32_t buffer_height(bool ntsc, bool beamcon0, int vres = 1)
{
    return (ntsc && !beamcon0 ? kBufferHeightNtsc : kBufferHeightPal) >> (1 - vres);
}

// The buffer of a configuration: what every frame of its image reports as video_width x video_height (an ECS or AGA Agnus can be programmed into the other standard; the preset decides, not the machine number).
inline uint32_t config_buffer_width(const p6_config &c) { return buffer_width(engine_resolution(c.video_resolution)); }
inline uint32_t config_buffer_height(const p6_config &c)
{
    return buffer_height(c.video_standard == P6_VIDEO_NTSC, machine_has_ecs_agnus(c.machine), engine_line_mode(c.video_line_mode));
}

// The nominal active raster of a normal unit (no long-field bit left set): 189 colour clocks of four hires columns, and 287 / 242 lines of one row (single) or two (double).
constexpr uint32_t active_width(int res) { return 189u << (res + 1); }
constexpr uint32_t active_height(bool ntsc, int vres) { return (ntsc ? 242u : 287u) << vres; }

// Pixel aspect (width : height of one buffer pixel), reduced: PAL 26:25 and NTSC 43:50 for a hires pixel on a double line; a lores pixel is twice as wide, a super-hires pixel half
// as wide, and a single line twice as tall.
inline void pixel_aspect(bool ntsc, int res, int vres, uint32_t *num, uint32_t *den)
{
    uint32_t n = ntsc ? 43 : 26, d = ntsc ? 50 : 25;
    if (res == 0) n *= 2;
    if (res == 2) d *= 2;
    if (vres == 0) d *= 2;
    uint32_t a = n, b = d;
    while (b) {
        const uint32_t t = a % b;
        a = b;
        b = t;
    }
    *num = n / a;
    *den = d / a;
}

}  // namespace p6_bridge
#endif
