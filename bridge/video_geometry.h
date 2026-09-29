// Sizes of the delivered picture buffer per video standard; no engine dependency, so p6_query_caps can
// report them. Derivation: docs/adr/0006-video-output.md.
#ifndef P6_BRIDGE_VIDEO_GEOMETRY_H
#define P6_BRIDGE_VIDEO_GEOMETRY_H
#include <stdint.h>

namespace p6_bridge {

// The hires, line-doubled raster of the fixed preset: 756 columns; 574 (PAL) / 484 (NTSC) rows for normal
// guests, two more when a guest leaves the long-field bit set without interlace. The buffer is the larger.
constexpr uint32_t kBufferWidth = 756;
constexpr uint32_t kBufferHeightPal = 576;
constexpr uint32_t kBufferHeightNtsc = 486;

}  // namespace p6_bridge
#endif
