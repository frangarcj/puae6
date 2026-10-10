// How the picture of a unit is presented to the frontend (T31c): crop, position, aspect, gamma and the 16-bit pixel format, all of them a transformation of
// a COPY of the picture the engine drew. The native picture (p6_frame.video), its hash, the machine and the state never see any of it: the options that
// live here are the schema's presentation options, so they are not part of p6_config, of the configuration hash or of the state identity, and
// tests/libretro-ui proves it by comparing the native state, the audio and the cadence of a run with every option on against a run with all of them off.
//
// The window. The native raster is the machine's active area (756 x 574 on PAL, 756 x 484 on NTSC: hires pixels and double lines, docs/adr/0006). Crop
// chooses a window of it:
//   * a level is a size in lores pixels and normal lines (PUAE5's table: Minimum 360 x 270 on PAL, Maximum 320 x 200), doubled for the raster, and never larger
//     than the raster; Automatic takes the area the guest drew: the bounding box of the pixels that differ from the border, found in the picture itself
//     (the same bytes on every host), never smaller than 640 x 400;
//   * the mode says which dimensions are cut (both, width only, height only) or fits the smallest window of a picture aspect ratio around the size;
//   * the position says where the window starts: automatic centers it on the raster (on the drawn area for Automatic), a number is the distance of its top
//     (vertical, positive = picture up) or minus the distance of its left edge (horizontal, positive = picture right) from the raster's origin, in lores
//     pixels and lines. The part of a window that falls outside the raster is black. Without a crop the position moves the picture inside the same frame.
// The automatic window has a little memory (the box it follows and for how many units a new box has been the same): it counts emulated units only, it is a
// view of the picture and not of the machine, and it is not part of the saved state: after a load the first picture adopts the box at once.
//
// Pixels. Gamma is a 256 entry table applied to the three channels (exponent 1 / (1 + value), value from -0.5 to +0.5), RGB565 packs the result. The
// machine always draws XRGB8888; both only convert the copy.
#ifndef P6_LIBRETRO_PRESENT_H
#define P6_LIBRETRO_PRESENT_H

#include "options_generated.h"

#include <algorithm>
#include <math.h>
#include <stdint.h>
#include <string.h>
#include <vector>

namespace p6_libretro {

// The numbers the schema gives the values of the presentation options (config/schema/options.json, "abi").
constexpr unsigned kCropOff = 0, kCropMaximum = 7, kCropAuto = 8;
constexpr unsigned kCropBoth = 0, kCropHorizontal = 1, kCropVertical = 2, kCrop16x9 = 3, kCrop16x10 = 4, kCrop4x3 = 5, kCrop5x4 = 6;
constexpr unsigned kAspectMachine = 0, kAspect4x3 = 1, kAspectPal = 2, kAspectNtsc = 3;
constexpr uint32_t kPositionAuto = 0, kPositionBias = 128;       // a position option is its value + 128, and 0 is "Automatic"
constexpr int kGammaBias = 5;                                     // the gamma option is its value in tenths + 5

constexpr uint32_t kMinCropWidth = 640, kMinCropHeight = 400;     // 320 x 200 lores pixels and lines, the smallest crop PUAE5 has
constexpr uint32_t kCropDelayUnits = 50;                          // how long a new drawn area must stay the same before the window follows it (one second of PAL)
constexpr uint32_t kBorderMargin = 8;                             // columns at each edge the renderer leaves black: not looked at to find the drawn area

struct PresentOptions {
    unsigned aspect = kAspectMachine, crop = kCropOff, crop_mode = kCropBoth;
    bool crop_delay = true;
    bool vertical_auto = true, horizontal_auto = true;
    int vertical = 0, horizontal = 0;          // lores lines / pixels, as the option says
    int gamma_tenths = 0;
    bool depth16 = false;
};

inline PresentOptions present_options(const p6_options::Settings &settings)
{
    PresentOptions options;
    options.aspect = settings.video_aspect;
    options.crop = settings.video_crop;
    options.crop_mode = settings.video_crop_mode;
    options.crop_delay = settings.video_crop_delay != 0;
    options.vertical_auto = settings.video_vertical_pos == kPositionAuto;
    options.horizontal_auto = settings.video_horizontal_pos == kPositionAuto;
    options.vertical = options.vertical_auto ? 0 : int(settings.video_vertical_pos) - int(kPositionBias);
    options.horizontal = options.horizontal_auto ? 0 : int(settings.video_horizontal_pos) - int(kPositionBias);
    options.gamma_tenths = int(settings.video_gamma) - kGammaBias;
    options.depth16 = settings.video_color_depth == 1;
    return options;
}

// A rectangle of the native raster; x and y are negative or beyond the raster when a position pushes the window out of it.
struct Window {
    int32_t x = 0, y = 0;
    uint32_t width = 0, height = 0;
    bool operator==(const Window &other) const { return x == other.x && y == other.y && width == other.width && height == other.height; }
};

// The drawn area of the picture (a bounding box, in raster coordinates).
struct Box {
    uint32_t x = 0, y = 0, width = 0, height = 0;
    bool operator==(const Box &other) const { return x == other.x && y == other.y && width == other.width && height == other.height; }
};

// The memory of the automatic window: the box it follows, the one it has seen last, and for how many units that one has been the same.
struct CropMemory {
    bool have = false;
    Box current, candidate;
    uint32_t stable = 0;
};

// PUAE5's crop levels (libretro-core.c): lores width, and lines for PAL and NTSC; the raster is twice as large in both directions.
struct CropSize {
    uint16_t width, lines_pal, lines_ntsc;
};
constexpr CropSize kCropSizes[] = {
    {360, 270, 240},    // minimum
    {348, 264, 240},    // smaller
    {336, 256, 240},    // small
    {320, 240, 240},    // medium
    {320, 224, 224},    // large
    {320, 216, 216},    // larger
    {320, 200, 200},    // maximum
};

// The pixels of the raster that differ from the border. A row's border is its pixel just inside the black margin; a row with nothing else is blank.
// Returns false for a picture with nothing drawn (everything the color of the border).
inline bool find_drawn_area(const uint8_t *origin, size_t stride, uint32_t width, uint32_t height, Box *out)
{
    const uint32_t margin = std::min<uint32_t>(kBorderMargin, width / 8);
    if (width <= 2 * margin + 1 || height == 0) return false;
    uint32_t left = width, right = 0, top = height, bottom = 0;
    for (uint32_t y = 0; y < height; ++y) {
        const uint8_t *row = origin + size_t(y) * stride;
        uint32_t background;
        memcpy(&background, row + size_t(margin) * 4, 4);
        uint32_t first = margin;
        for (;; ++first) {
            if (first >= width - margin) break;
            uint32_t pixel;
            memcpy(&pixel, row + size_t(first) * 4, 4);
            if (pixel != background) break;
        }
        if (first >= width - margin) continue;         // blank row
        uint32_t last = width - margin - 1;
        for (;; --last) {
            uint32_t pixel;
            memcpy(&pixel, row + size_t(last) * 4, 4);
            if (pixel != background || last == first) break;
        }
        left = std::min(left, first);
        right = std::max(right, last);
        top = std::min(top, y);
        bottom = y;
    }
    if (bottom < top || right < left) return false;
    out->x = left;
    out->y = top;
    out->width = right - left + 1;
    out->height = bottom - top + 1;
    return true;
}

// Follows the drawn area: at once, or only after the same new area has been seen for kCropDelayUnits units in a row (a guest that switches between two screens
// does not make the picture jump back and forth). A picture with nothing drawn keeps the last area.
inline void follow_drawn_area(CropMemory *memory, bool found, const Box &seen, bool delay)
{
    if (!found) return;
    if (!memory->have || !delay) {
        memory->have = true;
        memory->current = memory->candidate = seen;
        memory->stable = 0;
        return;
    }
    if (seen == memory->current) {
        memory->candidate = seen;
        memory->stable = 0;
    } else if (seen == memory->candidate) {
        if (++memory->stable >= kCropDelayUnits) {
            memory->current = seen;
            memory->stable = 0;
        }
    } else {
        memory->candidate = seen;
        memory->stable = 1;
    }
}

// The size of the crop window for a raster of native_width x native_height whose pixels are num:den (width:height of one pixel); `drawn` is the area the
// automatic level follows (the whole raster when nothing is known).
inline void crop_size(const PresentOptions &options, uint32_t native_width, uint32_t native_height, uint32_t num, uint32_t den, bool ntsc, const Box &drawn,
                      uint32_t *width, uint32_t *height)
{
    uint32_t w = native_width, h = native_height;
    if (options.crop != kCropOff) {
        if (options.crop == kCropAuto) {
            w = std::max(kMinCropWidth, drawn.width);
            h = std::max(kMinCropHeight, drawn.height);
        } else {
            const CropSize &size = kCropSizes[std::min<unsigned>(options.crop, kCropMaximum) - 1];
            w = 2u * size.width;
            h = 2u * (ntsc ? size.lines_ntsc : size.lines_pal);
        }
        w = std::min(w, native_width);
        h = std::min(h, native_height);
        const double dar = options.crop_mode == kCrop16x9 ? 16.0 / 9 : options.crop_mode == kCrop16x10 ? 16.0 / 10 : options.crop_mode == kCrop4x3 ? 4.0 / 3 :
                           options.crop_mode == kCrop5x4 ? 5.0 / 4 : 0.0;
        switch (options.crop_mode) {
        case kCropVertical: w = native_width; break;
        case kCropHorizontal: h = native_height; break;
        case kCrop16x9:
        case kCrop16x10:
            // As wide as the raster, narrowed until the picture is no wider than the ratio.
            w = native_width;
            if (double(w) * num > double(h) * den * dar) w = uint32_t(double(h) * den * dar / num);
            break;
        case kCrop4x3:
        case kCrop5x4:
            // As wide as the size says, made taller until the picture is no wider than the ratio.
            if (double(w) * num > double(h) * den * dar) {
                h = uint32_t(ceil(double(w) * num / (dar * den)));
                if (h > native_height) {
                    h = native_height;
                    w = uint32_t(double(h) * den * dar / num);
                }
            }
            break;
        default: break;
        }
        w = std::min(std::max(w, kMinCropWidth), native_width);
        h = std::min(std::max(h, kMinCropHeight), native_height);
    }
    *width = w;
    *height = h;
}

// The window itself. `drawn` centers the Automatic level on what the guest drew; every other level centers on the raster.
inline Window crop_window(const PresentOptions &options, uint32_t native_width, uint32_t native_height, uint32_t num, uint32_t den, bool ntsc, bool have_drawn,
                          const Box &drawn)
{
    Box area{0, 0, native_width, native_height};
    if (options.crop == kCropAuto && have_drawn) area = drawn;
    Window window;
    crop_size(options, native_width, native_height, num, den, ntsc, area, &window.width, &window.height);
    const bool follows = options.crop == kCropAuto && have_drawn;
    if (options.crop == kCropOff) {
        window.x = options.horizontal_auto ? 0 : -2 * options.horizontal;
        window.y = options.vertical_auto ? 0 : 2 * options.vertical;
        return window;
    }
    const int64_t center_x = follows ? int64_t(area.x) + int64_t(area.width) / 2 : int64_t(native_width) / 2;
    const int64_t center_y = follows ? int64_t(area.y) + int64_t(area.height) / 2 : int64_t(native_height) / 2;
    window.x = options.horizontal_auto ? int32_t(center_x - int64_t(window.width) / 2) : -2 * options.horizontal;
    window.y = options.vertical_auto ? int32_t(center_y - int64_t(window.height) / 2) : 2 * options.vertical;
    if (options.horizontal_auto) window.x = std::max<int32_t>(0, std::min<int32_t>(window.x, int32_t(native_width - window.width)));       // centered windows stay in the raster
    if (options.vertical_auto) window.y = std::max<int32_t>(0, std::min<int32_t>(window.y, int32_t(native_height - window.height)));
    return window;
}

// The aspect ratio announced for a picture of width x height pixels whose pixels are num:den, after the aspect option.
inline float picture_aspect(unsigned aspect, uint32_t width, uint32_t height, uint32_t num, uint32_t den)
{
    switch (aspect) {
    case kAspect4x3: return 4.0f / 3.0f;
    case kAspectPal: num = 26; den = 25; break;
    case kAspectNtsc: num = 43; den = 50; break;
    default: break;
    }
    return float(double(width) * num / (double(height) * den));
}

inline void build_gamma(int tenths, uint8_t table[256])
{
    const double exponent = 1.0 / (1.0 + tenths / 10.0);
    for (int i = 0; i < 256; ++i)
        table[i] = tenths == 0 ? uint8_t(i) : uint8_t(floor(255.0 * pow(i / 255.0, exponent) + 0.5));
}

// Copies the window of the native raster into `out` (window.width x window.height pixels, no padding), the part outside the raster black, and applies
// the gamma table (null: none) to the three channels.
inline void compose(const uint8_t *origin, size_t stride, uint32_t native_width, uint32_t native_height, const Window &window, const uint8_t *gamma,
                    uint32_t *out)
{
    for (uint32_t j = 0; j < window.height; ++j) {
        uint32_t *row = out + size_t(j) * window.width;
        const int64_t sy = int64_t(window.y) + j;
        if (sy < 0 || sy >= int64_t(native_height)) {
            memset(row, 0, size_t(window.width) * 4);
            continue;
        }
        const uint8_t *source = origin + size_t(sy) * stride;
        const int64_t first = std::max<int64_t>(0, -int64_t(window.x));                                           // first output column with a source pixel
        const int64_t end = std::min<int64_t>(window.width, int64_t(native_width) - window.x);                    // one past the last
        if (first >= end) {
            memset(row, 0, size_t(window.width) * 4);
            continue;
        }
        if (first) memset(row, 0, size_t(first) * 4);
        memcpy(row + first, source + size_t(int64_t(window.x) + first) * 4, size_t(end - first) * 4);
        if (end < int64_t(window.width)) memset(row + end, 0, size_t(window.width - end) * 4);
        if (gamma)
            for (int64_t i = first; i < end; ++i) {
                const uint32_t p = row[i];
                row[i] = (p & 0xFF000000u) | uint32_t(gamma[(p >> 16) & 255]) << 16 | uint32_t(gamma[(p >> 8) & 255]) << 8 | gamma[p & 255];
            }
    }
}

inline void to_rgb565(const uint32_t *in, size_t count, uint16_t *out)
{
    for (size_t i = 0; i < count; ++i) {
        const uint32_t p = in[i];
        out[i] = uint16_t(((p >> 8) & 0xF800) | ((p >> 5) & 0x07E0) | ((p >> 3) & 0x001F));
    }
}

// What a unit's picture becomes: pointer, size, pitch and bytes per pixel as retro_video_refresh_t takes them.
struct Presented {
    const void *data = nullptr;
    uint32_t width = 0, height = 0;
    size_t pitch = 0;
    unsigned bytes_per_pixel = 4;
};

// The presenter holds the buffers (sized for the largest raster, allocated once per picture size) and the automatic window's memory.
struct Presenter {
    std::vector<uint32_t> xrgb;
    std::vector<uint16_t> packed;
    CropMemory crop;
    uint8_t gamma[256];
    int gamma_built = 1000;

    void forget() { crop = CropMemory(); }

    const uint8_t *gamma_table(int tenths)
    {
        if (tenths == 0) return nullptr;
        if (gamma_built != tenths) {
            build_gamma(tenths, gamma);
            gamma_built = tenths;
        }
        return gamma;
    }

    // The window of a raster (updating the automatic window's memory from it). `identity` is true when the window is the raster itself.
    Window plan(const PresentOptions &options, const uint8_t *origin, size_t stride, uint32_t width, uint32_t height, uint32_t num, uint32_t den)
    {
        const bool ntsc = num < den;
        Box seen;
        bool found = false;
        if (options.crop == kCropAuto) {
            found = find_drawn_area(origin, stride, width, height, &seen);
            follow_drawn_area(&crop, found, seen, options.crop_delay);
        } else {
            crop = CropMemory();
        }
        return crop_window(options, width, height, num, den, ntsc, crop.have, crop.current);
    }

    // The 24-bit picture: the raster itself when nothing transforms it and `copy` is false, else a composed copy (which overlays may draw on).
    Presented compose_xrgb(const PresentOptions &options, const Window &window, const uint8_t *origin, size_t stride, uint32_t width, uint32_t height,
                           bool copy)
    {
        Presented result;
        result.width = window.width;
        result.height = window.height;
        const uint8_t *table = gamma_table(options.gamma_tenths);
        if (!copy && !table && window.x == 0 && window.y == 0 && window.width == width && window.height == height) {
            result.data = origin;
            result.pitch = stride;
            return result;
        }
        xrgb.resize(size_t(window.width) * window.height);
        compose(origin, stride, width, height, window, table, xrgb.data());
        result.data = xrgb.data();
        result.pitch = size_t(window.width) * 4;
        return result;
    }

    // The format the frontend asked for: converts a 24-bit picture to RGB565 when the option says so.
    Presented finish(const PresentOptions &options, Presented picture)
    {
        if (!options.depth16) return picture;
        packed.resize(size_t(picture.width) * picture.height);
        for (uint32_t y = 0; y < picture.height; ++y) {
            const uint8_t *row = static_cast<const uint8_t *>(picture.data) + size_t(y) * picture.pitch;
            uint32_t pixels[kMaxRow];
            for (uint32_t x = 0; x < picture.width; x += kMaxRow) {
                const uint32_t n = std::min<uint32_t>(kMaxRow, picture.width - x);
                memcpy(pixels, row + size_t(x) * 4, size_t(n) * 4);
                to_rgb565(pixels, n, packed.data() + size_t(y) * picture.width + x);
            }
        }
        picture.data = packed.data();
        picture.pitch = size_t(picture.width) * 2;
        picture.bytes_per_pixel = 2;
        return picture;
    }

private:
    static constexpr uint32_t kMaxRow = 1024;
};

}  // namespace p6_libretro

#endif
