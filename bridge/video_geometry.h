// Sizes of the delivered picture buffer per video standard; no engine dependency, so p6_query_caps can
// report them. Derivation: docs/adr/0006-video-output.md.
#ifndef P6_BRIDGE_VIDEO_GEOMETRY_H
#define P6_BRIDGE_VIDEO_GEOMETRY_H
#include <stdint.h>

namespace p6_bridge {

// The hires, line-doubled raster of the fixed preset: 756 columns; 574 (PAL) / 484 (NTSC) rows for normal
// guests, two more when a guest leaves the long-field bit set without interlace. NTSC lines alternate 227 and 228
// colour clocks and a guest that toggles interlace every unit makes the renderer adopt 228: 760 columns. The buffer
// is the largest of these; whatever exceeds it is refused.
constexpr uint32_t kBufferWidth = 760;
constexpr uint32_t kBufferHeightPal = 576;
constexpr uint32_t kBufferHeightNtsc = 486;

// Rows of the buffer a loaded machine delivers; announced at load and constant afterwards. An OCS machine cannot leave the standard of
// its configuration, so NTSC keeps the short buffer. An ECS or AGA Agnus (A600, A1200) can be programmed into the other standard (BEAMCON0), so its NTSC
// configuration reserves the PAL height: the raster of a programmed PAL beam still fits.
constexpr uint32_t buffer_height(bool ntsc, bool beamcon0)
{
    return ntsc && !beamcon0 ? kBufferHeightNtsc : kBufferHeightPal;
}

}  // namespace p6_bridge
#endif
