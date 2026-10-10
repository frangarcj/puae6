// Unit checks of the presentation code of the libretro adapter (adapters/libretro/present.h, T31c) on synthetic pictures, with no engine: the drawn
// area, the patience of the automatic window, the crop sizes of every level and mode, the window and its positions, the copy with black outside the raster,
// gamma, RGB565 and the aspect options. Exit code 0 and "present probe ok" when every check holds; each failure names the line.
#include "present.h"

#include <stdio.h>
#include <vector>

using namespace p6_libretro;

static int failures = 0;
#define CHECK(condition)                                                          \
    do {                                                                          \
        if (!(condition)) {                                                       \
            printf("FAILED line %d: %s\n", __LINE__, #condition);                 \
            ++failures;                                                           \
        }                                                                         \
    } while (0)

static std::vector<uint32_t> picture(uint32_t width, uint32_t height, uint32_t background)
{
    return std::vector<uint32_t>(size_t(width) * height, background);
}

static const uint8_t *bytes(const std::vector<uint32_t> &pixels) { return reinterpret_cast<const uint8_t *>(pixels.data()); }

static void drawn_area()
{
    auto pixels = picture(756, 574, 0x00112233);
    for (uint32_t y = 0; y < 574; ++y) {                                       // the renderer's black margin at both edges is not the border color
        for (uint32_t x = 0; x < 8; ++x) pixels[size_t(y) * 756 + x] = pixels[size_t(y) * 756 + 755 - x] = 0;
    }
    Box box;
    CHECK(!find_drawn_area(bytes(pixels), 756 * 4, 756, 574, &box));            // nothing drawn: the area stays unknown
    for (uint32_t y = 50; y < 250; ++y)
        for (uint32_t x = 100; x < 300; ++x) pixels[size_t(y) * 756 + x] = 0x00FF0000;
    CHECK(find_drawn_area(bytes(pixels), 756 * 4, 756, 574, &box));
    CHECK(box == (Box{100, 50, 200, 200}));
    pixels[size_t(400) * 756 + 600] = 0x0000FF00;                               // one more pixel far away grows the box to hold it
    CHECK(find_drawn_area(bytes(pixels), 756 * 4, 756, 574, &box));
    CHECK(box == (Box{100, 50, 501, 351}));
    pixels[size_t(500) * 756 + 3] = 0x00FFFFFF;                                 // a pixel in the black margin is not looked at
    CHECK(find_drawn_area(bytes(pixels), 756 * 4, 756, 574, &box));
    CHECK(box == (Box{100, 50, 501, 351}));
    auto full = picture(756, 574, 0x00112233);                                  // a row whose border differs from the next row's: each row has its own
    for (uint32_t x = 0; x < 756; ++x) full[size_t(10) * 756 + x] = 0x00445566;
    CHECK(!find_drawn_area(bytes(full), 756 * 4, 756, 574, &box));
}

static void patience()
{
    CropMemory memory;
    const Box first{10, 10, 700, 500}, second{20, 20, 650, 450};
    follow_drawn_area(&memory, false, Box(), true);
    CHECK(!memory.have);                                                        // nothing seen yet, nothing followed
    follow_drawn_area(&memory, true, first, true);
    CHECK(memory.have && memory.current == first);                              // the first box is taken at once
    for (uint32_t i = 0; i + 1 < kCropDelayUnits; ++i) follow_drawn_area(&memory, true, second, true);
    CHECK(memory.current == first);                                             // not yet: one unit short of the delay (the first sighting counts as 1)
    follow_drawn_area(&memory, true, second, true);
    CHECK(memory.current == second);
    follow_drawn_area(&memory, true, first, true);                              // a single unit of another box does not move it, and does not restart the count for the old one
    follow_drawn_area(&memory, true, second, true);
    CHECK(memory.current == second);
    for (uint32_t i = 0; i < kCropDelayUnits - 5; ++i) {                        // flapping between two other boxes never settles
        follow_drawn_area(&memory, true, first, true);
        follow_drawn_area(&memory, true, Box{1, 2, 3, 4}, true);
    }
    CHECK(memory.current == second);
    follow_drawn_area(&memory, false, Box(), true);                             // a unit with nothing drawn changes nothing
    CHECK(memory.current == second);
    CropMemory instant;
    follow_drawn_area(&instant, true, first, false);
    follow_drawn_area(&instant, true, second, false);
    CHECK(instant.current == second);                                           // without the delay the window follows at once
}

static void sizes()
{
    PresentOptions o;
    uint32_t w, h;
    const Box all{0, 0, 756, 574};
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 756 && h == 574);                                                // no crop: the raster
    struct Row { unsigned level; uint32_t pal_w, pal_h, ntsc_h; };
    const Row rows[] = {{1, 720, 540, 480}, {2, 696, 528, 480}, {3, 672, 512, 480}, {4, 640, 480, 480}, {5, 640, 448, 448}, {6, 640, 432, 432}, {7, 640, 400, 400}};
    for (const Row &row : rows) {
        o.crop = row.level;
        crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
        CHECK(w == row.pal_w && h == row.pal_h);
        crop_size(o, 756, 484, 43, 50, true, Box{0, 0, 756, 484}, &w, &h);
        CHECK(w == row.pal_w && h == row.ntsc_h);
    }
    o.crop = 1;                                                                 // minimum, on a raster smaller than it asks for: never larger than the raster
    crop_size(o, 640, 400, 26, 25, false, Box{0, 0, 640, 400}, &w, &h);
    CHECK(w == 640 && h == 400);
    o.crop = 4;
    o.crop_mode = kCropVertical;
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 756 && h == 480);
    o.crop_mode = kCropHorizontal;
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 640 && h == 574);
    o.crop_mode = kCrop4x3;                                                     // 640 x 480 is wider than 4:3 for 26:25 pixels: made taller (640 * 26 / (4/3 * 25) = 499.2)
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 640 && h == 500);
    o.crop_mode = kCrop5x4;
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 640 && h == 533);                                                // ceil(640 * 26 / (1.25 * 25))
    o.crop = 7;
    o.crop_mode = kCrop16x9;                                                    // 640 x 400: as wide as the ratio allows, 400 * 25 * 16/9 / 26 = 683.7
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 683 && h == 400);
    o.crop_mode = kCrop16x10;
    crop_size(o, 756, 574, 26, 25, false, all, &w, &h);
    CHECK(w == 640 && h == 400);                                                // 400 * 25 * 1.6 / 26 = 615.4, below the minimum width
    o.crop = kCropAuto;
    o.crop_mode = kCropBoth;
    crop_size(o, 756, 574, 26, 25, false, Box{100, 50, 500, 300}, &w, &h);
    CHECK(w == 640 && h == 400);                                                // never smaller than 640 x 400
    crop_size(o, 756, 574, 26, 25, false, Box{50, 20, 700, 520}, &w, &h);
    CHECK(w == 700 && h == 520);
    crop_size(o, 756, 574, 26, 25, false, Box{0, 0, 2000, 2000}, &w, &h);
    CHECK(w == 756 && h == 574);                                                // and never larger than the raster
}

static void windows()
{
    PresentOptions o;
    o.crop = 1;
    Window window = crop_window(o, 756, 574, 26, 25, false, false, Box());
    CHECK(window == (Window{18, 17, 720, 540}));                                // centered on the raster
    o.vertical_auto = false;
    o.vertical = 4;
    window = crop_window(o, 756, 574, 26, 25, false, false, Box());
    CHECK(window.y == 8 && window.x == 18);                                     // positive: the window starts lower, the picture moves up
    o.vertical = -3;
    window = crop_window(o, 756, 574, 26, 25, false, false, Box());
    CHECK(window.y == -6);                                                      // negative: part of the window is above the raster (black)
    o.horizontal_auto = false;
    o.horizontal = 5;
    window = crop_window(o, 756, 574, 26, 25, false, false, Box());
    CHECK(window.x == -10);                                                     // positive: the picture moves right, so the window starts left of the raster
    o.horizontal = -6;
    window = crop_window(o, 756, 574, 26, 25, false, false, Box());
    CHECK(window.x == 12);
    PresentOptions plain;
    window = crop_window(plain, 756, 574, 26, 25, false, false, Box());
    CHECK(window == (Window{0, 0, 756, 574}));
    plain.vertical_auto = false;
    plain.vertical = 2;
    window = crop_window(plain, 756, 574, 26, 25, false, false, Box());
    CHECK(window == (Window{0, 4, 756, 574}));                                  // without a crop the position moves the picture inside the frame
    PresentOptions automatic;
    automatic.crop = kCropAuto;
    window = crop_window(automatic, 756, 574, 26, 25, false, true, Box{100, 50, 500, 300});
    CHECK(window.width == 640 && window.height == 400 && window.x == 30 && window.y == 0);       // centered on the drawn area (350, 200), kept inside the raster
    window = crop_window(automatic, 756, 574, 26, 25, false, true, Box{40, 30, 700, 500});
    CHECK(window == (Window{40, 30, 700, 500}));
    window = crop_window(automatic, 756, 574, 26, 25, false, false, Box());
    CHECK(window == (Window{0, 0, 756, 574}));                                  // nothing known: all of the raster
}

static void copies()
{
    std::vector<uint32_t> raster(8 * 6);
    for (uint32_t y = 0; y < 6; ++y)
        for (uint32_t x = 0; x < 8; ++x) raster[y * 8 + x] = 0x01000000u | (y << 8) | x;
    std::vector<uint32_t> out(4 * 3);
    compose(bytes(raster), 8 * 4, 8, 6, Window{2, 1, 4, 3}, nullptr, out.data());
    for (uint32_t j = 0; j < 3; ++j)
        for (uint32_t i = 0; i < 4; ++i) CHECK(out[j * 4 + i] == (0x01000000u | ((1 + j) << 8) | (2 + i)));
    compose(bytes(raster), 8 * 4, 8, 6, Window{-2, -1, 4, 3}, nullptr, out.data());           // the part outside the raster is black
    for (uint32_t i = 0; i < 4; ++i) CHECK(out[i] == 0);
    CHECK(out[4] == 0 && out[5] == 0 && out[6] == 0x01000000u && out[7] == 0x01000001u);
    compose(bytes(raster), 8 * 4, 8, 6, Window{6, 4, 4, 3}, nullptr, out.data());
    CHECK(out[0] == (0x01000000u | (4 << 8) | 6) && out[1] == (0x01000000u | (4 << 8) | 7) && out[2] == 0 && out[3] == 0);
    CHECK(out[4] == (0x01000000u | (5 << 8) | 6) && out[8] == 0 && out[11] == 0);
    compose(bytes(raster), 8 * 4, 8, 6, Window{20, 20, 4, 3}, nullptr, out.data());           // entirely outside
    for (uint32_t pixel : out) CHECK(pixel == 0);
    uint8_t table[256];
    build_gamma(0, table);
    for (int i = 0; i < 256; ++i) CHECK(table[i] == i);
    build_gamma(5, table);
    CHECK(table[0] == 0 && table[255] == 255 && table[128] > 128 + 20);          // brightens the mid tones
    build_gamma(-5, table);
    CHECK(table[0] == 0 && table[255] == 255 && table[128] < 128 - 20);
    for (int i = 1; i < 256; ++i) CHECK(table[i] >= table[i - 1]);                // and stays monotonic
    build_gamma(-5, table);
    std::vector<uint32_t> one{0x7F102030u};
    std::vector<uint32_t> graded(1);
    compose(bytes(one), 4, 1, 1, Window{0, 0, 1, 1}, table, graded.data());
    CHECK((graded[0] >> 24) == 0x7F && ((graded[0] >> 16) & 255) == table[0x10] && ((graded[0] >> 8) & 255) == table[0x20] && (graded[0] & 255) == table[0x30]);
    const uint32_t colors[] = {0x00FF0000, 0x0000FF00, 0x000000FF, 0x00FFFFFF, 0x00000000, 0x00808080};
    uint16_t packed[6];
    to_rgb565(colors, 6, packed);
    CHECK(packed[0] == 0xF800 && packed[1] == 0x07E0 && packed[2] == 0x001F && packed[3] == 0xFFFF && packed[4] == 0 && packed[5] == 0x8410);
}

static void aspects()
{
    CHECK(picture_aspect(kAspectMachine, 756, 574, 26, 25) > 1.35f && picture_aspect(kAspectMachine, 756, 574, 26, 25) < 1.39f);
    CHECK(picture_aspect(kAspect4x3, 100, 100, 1, 1) == 4.0f / 3.0f);
    CHECK(picture_aspect(kAspectPal, 756, 574, 43, 50) == picture_aspect(kAspectMachine, 756, 574, 26, 25));       // PAL pixels whatever the machine runs
    CHECK(picture_aspect(kAspectNtsc, 756, 484, 26, 25) == picture_aspect(kAspectMachine, 756, 484, 43, 50));
}

static void options_from_settings()
{
    p6_options::Settings settings;
    PresentOptions o = present_options(settings);
    CHECK(o.crop == kCropOff && o.crop_mode == kCropBoth && o.crop_delay && o.vertical_auto && o.horizontal_auto && o.gamma_tenths == 0 && !o.depth16);
    settings.video_vertical_pos = 20 + kPositionBias;
    settings.video_horizontal_pos = kPositionBias - 8;
    settings.video_gamma = 2;
    settings.video_color_depth = 1;
    o = present_options(settings);
    CHECK(!o.vertical_auto && o.vertical == 20 && !o.horizontal_auto && o.horizontal == -8 && o.gamma_tenths == -3 && o.depth16);
}

static void presenter()
{
    // The presenter hands back the raster itself when nothing transforms it, and a copy otherwise.
    auto raster = picture(756, 574, 0x00123456);
    Presenter presenter;
    PresentOptions o;
    Window window = presenter.plan(o, bytes(raster), 756 * 4, 756, 574, 26, 25);
    Presented shown = presenter.compose_xrgb(o, window, bytes(raster), 756 * 4, 756, 574, false);
    CHECK(shown.data == bytes(raster) && shown.width == 756 && shown.height == 574 && shown.pitch == 756 * 4);
    shown = presenter.compose_xrgb(o, window, bytes(raster), 756 * 4, 756, 574, true);
    CHECK(shown.data != bytes(raster) && shown.pitch == 756 * 4);
    o.gamma_tenths = 3;
    shown = presenter.compose_xrgb(o, window, bytes(raster), 756 * 4, 756, 574, false);
    CHECK(shown.data != bytes(raster));
    o.depth16 = true;
    shown = presenter.finish(o, shown);
    CHECK(shown.bytes_per_pixel == 2 && shown.pitch == 756 * 2 && shown.width == 756);
    o.crop = 4;
    o.depth16 = false;
    o.gamma_tenths = 0;
    window = presenter.plan(o, bytes(raster), 756 * 4, 756, 574, 26, 25);
    shown = presenter.compose_xrgb(o, window, bytes(raster), 756 * 4, 756, 574, false);
    CHECK(shown.width == 640 && shown.height == 480 && shown.pitch == 640 * 4);
}

int main()
{
    drawn_area();
    patience();
    sizes();
    windows();
    copies();
    aspects();
    options_from_settings();
    presenter();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("present probe ok\n");
    return 0;
}
