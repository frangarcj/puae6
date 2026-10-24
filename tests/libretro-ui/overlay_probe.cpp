// Unit checks of the drawing code of the libretro adapter (adapters/libretro/overlay.h, statusbar.h and, since T31a, vkbd.h) on synthetic pictures, with no engine:
// the font, blending and clipping, text with a shadow or an outline, and what the statusbar and the virtual keyboard touch of a picture and what they leave
// alone. Exit code 0 and "overlay probe ok" when every check holds; each failure names the line.
#include "statusbar.h"

#include <stdio.h>
#include <string>
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

constexpr uint32_t kBase = 0x00336699;

struct Picture {
    std::vector<uint32_t> pixels;
    Canvas canvas;
    Picture(uint32_t width, uint32_t height, uint32_t color = kBase) : pixels(size_t(width) * height, color)
    {
        canvas.pixels = pixels.data();
        canvas.width = width;
        canvas.height = height;
        canvas.stride = width;
    }
    uint32_t at(int x, int y) const { return pixels[size_t(y) * canvas.width + size_t(x)]; }
    // The rows [from, to) have a pixel that differs from the base color.
    bool touched(int from, int to) const
    {
        for (int y = from; y < to; ++y)
            for (uint32_t x = 0; x < canvas.width; ++x)
                if (at(int(x), y) != kBase) return true;
        return false;
    }
};

static void font()
{
    // Every character the labels use has a glyph; lower case is drawn as capitals; the arrows are four distinct shapes; something the font lacks is a box.
    for (const char *c = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_~{}"; *c; ++c) CHECK(glyph(*c) != nullptr);
    CHECK(glyph('a') == glyph('A') && glyph('z') == glyph('Z'));
    CHECK(glyph('^') != glyph('~') && glyph('{') != glyph('}') && glyph('^') != glyph('{'));
    CHECK(glyph('\x01') == nullptr && glyph('|') == nullptr);
    const uint8_t *a = glyph('A');
    CHECK(a[0] == 0x0E && a[3] == 0x1F && a[6] == 0x11);                              // a capital A: round top, bar in the middle, two legs
    for (int row = 0; row < kGlyphHeight; ++row) CHECK(glyph(' ')[row] == 0);
    for (char c = '0'; c <= '9'; ++c) {                                                // every digit and letter draws something, and no two are alike
        int ink = 0;
        for (int row = 0; row < kGlyphHeight; ++row) ink += __builtin_popcount(glyph(c)[row]);
        CHECK(ink > 0);
        for (char d = c + 1; d <= '9'; ++d) CHECK(memcmp(glyph(c), glyph(d), kGlyphHeight) != 0);
    }
    for (char c = 'A'; c <= 'Z'; ++c)
        for (char d = c + 1; d <= 'Z'; ++d) CHECK(memcmp(glyph(c), glyph(d), kGlyphHeight) != 0);
}

static void primitives()
{
    CHECK(blend(0x00102030, 0x00FFFFFF, 255) == 0x00FFFFFF && blend(0x00102030, 0x00FFFFFF, 0) == 0x00102030);
    CHECK(blend(0x00000000, 0x00FF8000, 128) == 0x00804000 || blend(0x00000000, 0x00FF8000, 128) == 0x00804100 || (blend(0, 0xFF8000, 128) >> 16) == 128);
    Picture p(20, 10);
    fill_rect(p.canvas, -5, -5, 10, 8, 0x00FF0000);                                    // clipped on the left and the top
    CHECK(p.at(0, 0) == 0x00FF0000 && p.at(4, 2) == 0x00FF0000 && p.at(5, 0) == kBase && p.at(0, 3) == kBase);
    fill_rect(p.canvas, 15, 8, 100, 100, 0x0000FF00);                                  // and on the right and the bottom
    CHECK(p.at(19, 9) == 0x0000FF00 && p.at(14, 9) == kBase);
    fill_rect(p.canvas, 30, 30, 5, 5, 0x00FFFFFF);                                     // entirely outside: nothing
    fill_rect(p.canvas, 3, 3, 0, 4, 0x00FFFFFF);
    fill_rect(p.canvas, 3, 3, -4, 4, 0x00FFFFFF);
    CHECK(p.at(3, 3) == kBase);
    Picture t(40, 20);
    draw_text(t.canvas, 1, 1, "A", 1, 0x00FFFFFF);
    CHECK(t.at(1 + 1, 1) == 0x00FFFFFF && t.at(1 + 0, 1) == kBase && t.at(1 + 4, 1) == kBase);       // 0x0E: columns 1..3 of the first row
    CHECK(text_width("AB", 1) == 11 && text_width("AB", 2) == 22 && text_width("", 3) == 0);
    Picture shadow(40, 20), outline(40, 20), plain(40, 20);
    draw_text(plain.canvas, 5, 5, "I", 2, 0x00FFFFFF);
    draw_text(shadow.canvas, 5, 5, "I", 2, 0x00FFFFFF, TextStyle::Shadow, 0x00000000);
    draw_text(outline.canvas, 5, 5, "I", 2, 0x00FFFFFF, TextStyle::Outline, 0x00000000);
    CHECK(shadow.at(5 + 4, 5 + 2) == 0x00FFFFFF);                                       // the text is on top of its shadow
    CHECK(plain.at(5 + 9, 5 + 2) == kBase && shadow.at(5 + 9, 5 + 2) != kBase);       // the shadow lies right and below
    CHECK(outline.at(5, 3) == 0x00000000 && plain.at(5, 3) == kBase);   // the outline lies all around
    Picture box(20, 20);
    draw_text(box.canvas, 0, 0, "\x01", 1, 0x00FFFFFF);                                 // a character without a glyph: a box
    CHECK(box.at(0, 0) == 0x00FFFFFF && box.at(4, 6) == 0x00FFFFFF && box.at(2, 3) == kBase);
    Picture edge(8, 8);
    draw_text(edge.canvas, 6, 6, "WWWW", 3, 0x00FFFFFF, TextStyle::Outline, 0);         // text running off the picture is clipped, never written outside
    draw_text(edge.canvas, -50, -50, "WW", 2, 0x00FFFFFF);
}

static StatusData data()
{
    StatusData d;
    d.ports[0].type = P6_PORT_MOUSE;
    d.ports[1].type = P6_PORT_JOYSTICK;
    return d;
}

static void statusbar()
{
    StatusbarMode bottom;
    StatusData d = data();
    {   // Bottom, Full, on a picture wide enough for scale 2: the bar is 22 rows (7 * 2 + 4 * 2) at the foot, and everything above is as it was.
        Picture p(756, 574);
        draw_statusbar(p.canvas, bottom, d, true, false);
        CHECK(!p.touched(0, 574 - 22) && p.touched(574 - 22, 574));
        CHECK(p.at(300, 574 - 22 - 1) == kBase && p.at(755, 573) != kBase);
        // The LED: "DF0" at scale 2 is 34 pixels wide from x = 8, then 4 pixels, then the box (8 * 2 wide, 7 * 2 high) at the top of the text row.
        const int box_x = 8 + 34 + 4, box_y = 574 - 22 + 4;
        CHECK(p.at(box_x + 8, box_y + 7) == kLedOff);
        Picture lit(756, 574);
        d.drive_light = true;
        draw_statusbar(lit.canvas, bottom, d, true, false);
        CHECK(lit.at(box_x + 8, box_y + 7) == kLedOn);
        d.drive_light = false;
        // Nothing but the bar differs between a lit and an unlit LED.
        int differing = 0;
        for (size_t i = 0; i < p.pixels.size(); ++i) differing += p.pixels[i] != lit.pixels[i];
        CHECK(differing == 16 * 14);
    }
    {   // Top: the first rows.
        StatusbarMode mode;
        mode.top = true;
        Picture p(756, 574);
        draw_statusbar(p.canvas, mode, d, true, false);
        CHECK(p.touched(0, 22) && !p.touched(22, 574));
    }
    {   // A narrow picture takes the small scale: the bar is 11 rows.
        Picture p(300, 300);
        draw_statusbar(p.canvas, bottom, d, true, false);
        CHECK(!p.touched(0, 300 - 11) && p.touched(300 - 11, 300));
    }
    {   // Hidden: nothing at all, with or without a message; a message strip only when asked for and only with a message.
        Picture p(756, 574);
        d.message = kMessageTurboOn;
        draw_statusbar(p.canvas, bottom, d, false, false);
        CHECK(!p.touched(0, 574));
        StatusData silent = data();
        draw_statusbar(p.canvas, bottom, silent, false, true);
        CHECK(!p.touched(0, 574));
        draw_statusbar(p.canvas, bottom, d, false, true);
        CHECK(p.touched(574 - 22, 574) && !p.touched(0, 574 - 22));
        // The strip has the message and no LED.
        bool led = false;
        for (size_t i = 0; i < p.pixels.size(); ++i) led = led || p.pixels[i] == kLedOff || p.pixels[i] == kLedOn;
        CHECK(!led);
        d.message = kMessageNone;
    }
    {   // Minimal is the LED alone: no message, no text, only the box over the dimmed bar.
        StatusbarMode minimal;
        minimal.minimal = true;
        Picture p(756, 574);
        d.message = kMessageReset;
        d.turbo = true;
        draw_statusbar(p.canvas, minimal, d, true, false);
        int bright = 0;
        for (int y = 574 - 22; y < 574; ++y)
            for (int x = 0; x < 756; ++x) bright += (p.at(x, y) == kBarText || p.at(x, y) == kBarLit);
        CHECK(bright == 0);
        d.message = kMessageNone;
        d.turbo = false;
    }
    {   // Basic has the message and the LED but not the ports; Full has the ports (the lit symbols).
        StatusbarMode basic;
        basic.basic = true;
        d.ports[1].buttons = P6_BUTTON_1 | P6_BUTTON_RIGHT;
        d.ports[0].buttons = P6_BUTTON_2;
        Picture b(756, 574), f(756, 574);
        draw_statusbar(b.canvas, basic, d, true, false);
        draw_statusbar(f.canvas, bottom, d, true, false);
        auto count = [&](const Picture &p, uint32_t color) {
            int n = 0;
            for (int y = 574 - 22; y < 574; ++y)
                for (int x = 0; x < 756; ++x) n += p.at(x, y) == color;
            return n;
        };
        CHECK(count(b, kBarLit) == 0 && count(f, kBarLit) > 0);
        CHECK(count(f, kBarDim) > 0);                                                   // the idle symbols are drawn dim
        d.ports[0].buttons = d.ports[1].buttons = 0;
    }
    {   // A message is on the right of the bar.
        Picture p(756, 574);
        d.message = kMessageAspectNormal;
        draw_statusbar(p.canvas, bottom, d, true, false);
        int right = 0, left = 0;
        for (int y = 574 - 22; y < 574; ++y)
            for (int x = 0; x < 756; ++x)
                if (p.at(x, y) == kBarText) (x > 600 ? right : left) += 1;
        CHECK(right > 50);
        CHECK(left > 0);                                                                // "DF0" is on the left
        d.message = kMessageNone;
    }
    // Every message has text, and no other id does.
    for (uint8_t id = 1; id <= kMessageReset; ++id) CHECK(*message_text(id) != 0);
    CHECK(*message_text(0) == 0 && *message_text(kMessageReset + 1) == 0);
    for (const StatusbarMode m : {statusbar_mode(0), statusbar_mode(1), statusbar_mode(2), statusbar_mode(3), statusbar_mode(4), statusbar_mode(5), statusbar_mode(6), statusbar_mode(7)})
        (void)m;
    CHECK(!statusbar_mode(0).top && !statusbar_mode(0).basic && !statusbar_mode(0).minimal);
    CHECK(statusbar_mode(1).minimal && !statusbar_mode(1).basic && statusbar_mode(2).basic && !statusbar_mode(2).minimal);
    CHECK(statusbar_mode(3).basic && statusbar_mode(3).minimal && !statusbar_mode(3).top);
    CHECK(statusbar_mode(4).top && !statusbar_mode(4).basic && statusbar_mode(7).top && statusbar_mode(7).basic && statusbar_mode(7).minimal);
    // A picture too small for the bar is not written outside itself (the sanitizers watch).
    Picture tiny(30, 10);
    draw_statusbar(tiny.canvas, bottom, d, true, false);
    Picture flat(756, 5);
    draw_statusbar(flat.canvas, bottom, d, true, false);
}

int main()
{
    font();
    primitives();
    statusbar();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("overlay probe ok\n");
    return 0;
}
