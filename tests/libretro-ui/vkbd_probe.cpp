// Unit checks of the virtual keyboard (adapters/libretro/vkbd.h and its step in mapper.h, T31a) on synthetic controls and pictures, with no engine: the layout
// has every Amiga key once, the cursor reaches every key, the keys the pad presses are the bits of the packet, modifiers latch, closing lets go, what is drawn
// touches only what it should, and the keyboard's memory goes through the adapter section of a state. Exit code 0 and "vkbd probe ok" when every check holds.
#include "state_frame.h"
#include "statusbar.h"

#include <set>
#include <stdio.h>
#include <string.h>
#include <vector>

using namespace p6_libretro;
using namespace p6_options;

static int failures = 0;
#define CHECK(condition)                                                          \
    do {                                                                          \
        if (!(condition)) {                                                       \
            printf("FAILED line %d: %s\n", __LINE__, #condition);                 \
            ++failures;                                                           \
        }                                                                         \
    } while (0)

static uint32_t bit(unsigned input) { return UINT32_C(1) << input; }
static bool key(const Effects &fx, unsigned code) { return fx.keys[code / 32] >> (code % 32) & 1; }
static bool no_keys(const Effects &fx) { return !fx.keys[0] && !fx.keys[1] && !fx.keys[2] && !fx.keys[3]; }

static Effects step(uint32_t down, const InputConfig &config, InputMemory *memory)
{
    Controls controls;
    controls.down = down;
    Effects fx;
    mapper_step(controls, config, true, memory, &fx);
    return fx;
}

static void layout()
{
    std::set<unsigned> codes;
    for (const VKey &k : kVkbd) {
        CHECK(codes.insert(k.code).second);                                               // every code once
        CHECK(k.row < kVkbdRows && k.width > 0 && unsigned(k.x) + k.width <= kVkbdHalfKeys);
        CHECK(k.label && *k.label);
        for (const char *c = k.label; *c; ++c) CHECK(glyph(*c) != nullptr);              // and every label can be drawn
    }
    CHECK(codes.size() == kVkbdKeys);
    // The keyboard of input_map.h and the virtual one have the same keys (the Caps Lock is the virtual keyboard's own, and the host's Caps Lock key flips the same latch).
    std::set<unsigned> host;
    for (const KeyMapping &mapping : kKeyMap) host.insert(mapping.amiga);
    host.insert(kCapsLockCode);
    CHECK(host == codes);
    // No two keys overlap on a row.
    for (unsigned i = 0; i < kVkbdKeys; ++i)
        for (unsigned j = i + 1; j < kVkbdKeys; ++j) {
            if (kVkbd[i].row != kVkbd[j].row) continue;
            CHECK(kVkbd[i].x + kVkbd[i].width <= kVkbd[j].x || kVkbd[j].x + kVkbd[j].width <= kVkbd[i].x);
        }
    for (unsigned i = 0; i < kVkbdKeys; ++i) CHECK(vk_find(kVkbd[i].code) == int(i));
    CHECK(vk_find(0x68) == -1 && vk_find(0x0E) == -1);
    for (unsigned i = 0; i < kVkLatchCount; ++i) CHECK(vk_find(kVkLatchCodes[i]) >= 0 && vk_latch_bit(kVkLatchCodes[i]) == int(i));
    CHECK(vk_latch_bit(0x62) == -1 && vk_latch_bit(0x40) == -1);
}

static void navigation()
{
    const unsigned UP = RETRO_DEVICE_ID_JOYPAD_UP, DOWN = RETRO_DEVICE_ID_JOYPAD_DOWN, LEFT = RETRO_DEVICE_ID_JOYPAD_LEFT, RIGHT = RETRO_DEVICE_ID_JOYPAD_RIGHT;
    for (unsigned start = 0; start < kVkbdKeys; ++start) {
        // Right walks the whole row and comes back to where it started; left undoes right.
        unsigned row_keys = 0;
        for (const VKey &k : kVkbd) row_keys += k.row == kVkbd[start].row;
        unsigned at = start;
        std::set<unsigned> seen;
        for (unsigned n = 0; n < row_keys; ++n) {
            seen.insert(at);
            at = vk_move(at, RIGHT);
            CHECK(kVkbd[at].row == kVkbd[start].row);
        }
        CHECK(at == start && seen.size() == row_keys);
        CHECK(vk_move(vk_move(start, RIGHT), LEFT) == start);
        // Down six times is every row once and the same row again (it wraps); it always lands on a key of the next row.
        unsigned down = start;
        for (unsigned n = 1; n <= kVkbdRows; ++n) {
            down = vk_move(down, DOWN);
            CHECK(kVkbd[down].row == (kVkbd[start].row + n) % kVkbdRows);
        }
        unsigned up = vk_move(start, UP);
        CHECK(kVkbd[up].row == (kVkbd[start].row + kVkbdRows - 1) % kVkbdRows);
    }
    // The key below the space bar's middle, going up, is on row 4 and near the middle; Q is above A-ish: up from A lands on Q or W.
    const unsigned a = unsigned(vk_find(0x20));
    CHECK(kVkbd[vk_move(a, UP)].code == 0x12);                                          // A's middle is E's
    CHECK(kVkbd[vk_move(unsigned(vk_find(0x40)), UP)].row == 4);
    // Every key can be reached from every other (a walk of moves).
    std::set<unsigned> reached{0};
    std::vector<unsigned> queue{0};
    for (size_t i = 0; i < queue.size(); ++i)
        for (unsigned d : {UP, DOWN, LEFT, RIGHT}) {
            const unsigned next = vk_move(queue[i], d);
            if (reached.insert(next).second) queue.push_back(next);
        }
    CHECK(reached.size() == kVkbdKeys);
}

static void use()
{
    InputConfig config;
    config.mapper[RETRO_DEVICE_ID_JOYPAD_SELECT] = kMapperTargetToggleVkbd;
    InputMemory memory;
    // Select opens it; what else was pressed in that very run (B went down too) is neither a keyboard press nor the joystick's: the keyboard takes the pad from the next run.
    Effects fx = step(bit(RETRO_DEVICE_ID_JOYPAD_SELECT) | bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    CHECK(memory.vkbd_open && no_keys(fx) && fx.joy == 0);
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    CHECK(memory.vk_held == kVkbdNoKey);
    step(0, config, &memory);
    // The pad belongs to the keyboard: it gives the joystick nothing, and an Amiga key mapped to a button is not pressed.
    config.mapper[RETRO_DEVICE_ID_JOYPAD_L] = kKeyTargetBase + 0x40;
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_UP) | bit(RETRO_DEVICE_ID_JOYPAD_RIGHT) | bit(RETRO_DEVICE_ID_JOYPAD_L), config, &memory);
    CHECK(fx.joy == 0 && no_keys(fx));
    step(0, config, &memory);
    // The cursor: a press moves it, holding repeats after kVkRepeatDelay units and then every kVkRepeatEvery.
    memory.vk_cursor = uint8_t(vk_find(0x10));                                      // Q
    step(bit(RETRO_DEVICE_ID_JOYPAD_RIGHT), config, &memory);
    CHECK(kVkbd[memory.vk_cursor].code == 0x11);                                      // W
    unsigned moves = 0;
    uint8_t before = memory.vk_cursor;
    for (unsigned n = 0; n < 40; ++n) {
        step(bit(RETRO_DEVICE_ID_JOYPAD_RIGHT), config, &memory);
        if (memory.vk_cursor != before) ++moves;
        before = memory.vk_cursor;
    }
    CHECK(moves == (40 - kVkRepeatDelay) / kVkRepeatEvery);                         // the press moved once before the loop; the loop's 40 units repeat from the 17th on, every 4th
    step(0, config, &memory);
    CHECK(memory.vk_repeat == 0);
    // B presses the key under the cursor for as long as it is down, wherever the cursor goes meanwhile.
    memory.vk_cursor = uint8_t(vk_find(0x20));                                        // A
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    CHECK(key(fx, 0x20));
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_B) | bit(RETRO_DEVICE_ID_JOYPAD_RIGHT), config, &memory);
    CHECK(key(fx, 0x20) && !key(fx, 0x21));
    fx = step(0, config, &memory);
    CHECK(no_keys(fx) && memory.vk_held == kVkbdNoKey);
    // Modifiers latch: Shift stays down over later presses until it is pressed again.
    memory.vk_cursor = uint8_t(vk_find(0x60));
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    fx = step(0, config, &memory);
    CHECK(key(fx, 0x60) && memory.vk_latch == 1);
    memory.vk_cursor = uint8_t(vk_find(0x20));
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    CHECK(key(fx, 0x60) && key(fx, 0x20));
    step(0, config, &memory);
    memory.vk_cursor = uint8_t(vk_find(0x63));                                        // Ctrl too
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    fx = step(0, config, &memory);
    CHECK(key(fx, 0x60) && key(fx, 0x63) && memory.vk_latch == ((1u << vk_latch_bit(0x60)) | (1u << vk_latch_bit(0x63))));
    memory.vk_cursor = uint8_t(vk_find(0x60));
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    fx = step(0, config, &memory);
    CHECK(!key(fx, 0x60) && key(fx, 0x63));                                           // the second press let Shift go
    // Caps Lock: the key and RetroPad Y flip the Amiga's own latch (the one the host's Caps Lock key flips).
    memory.vk_cursor = uint8_t(vk_find(0x62));
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    CHECK(memory.caps_latched && memory.vk_held == kVkbdNoKey);
    step(0, config, &memory);
    step(bit(RETRO_DEVICE_ID_JOYPAD_Y), config, &memory);
    CHECK(!memory.caps_latched);
    step(0, config, &memory);
    // X is the space bar, A the transparency.
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_X), config, &memory);
    CHECK(key(fx, 0x40));
    step(bit(RETRO_DEVICE_ID_JOYPAD_A), config, &memory);
    CHECK(memory.vkbd_opaque);
    step(0, config, &memory);
    step(bit(RETRO_DEVICE_ID_JOYPAD_A), config, &memory);
    CHECK(!memory.vkbd_opaque);
    step(0, config, &memory);
    // Closing lets go of everything it held (and keeps the cursor), and the pad is the joystick again.
    memory.vk_cursor = uint8_t(vk_find(0x20));
    step(bit(RETRO_DEVICE_ID_JOYPAD_B), config, &memory);
    const uint8_t cursor = memory.vk_cursor;
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_B) | bit(RETRO_DEVICE_ID_JOYPAD_SELECT), config, &memory);
    CHECK(!memory.vkbd_open && memory.vk_held == kVkbdNoKey && memory.vk_latch == 0 && memory.vk_cursor == cursor);
    CHECK(!key(fx, 0x20) && !key(fx, 0x63));
    fx = step(bit(RETRO_DEVICE_ID_JOYPAD_X), config, &memory);
    CHECK(fx.joy == P6_BUTTON_3 && no_keys(fx));
    // The hotkey opens it too.
    InputConfig plain;
    InputMemory other;
    step(bit(kHotkeyBit + 6), plain, &other);
    CHECK(other.vkbd_open);
}

struct Picture {
    std::vector<uint32_t> pixels;
    Canvas canvas;
    Picture(uint32_t width, uint32_t height, uint32_t color) : pixels(size_t(width) * height, color)
    {
        canvas.pixels = pixels.data();
        canvas.width = width;
        canvas.height = height;
        canvas.stride = width;
    }
    uint32_t at(int x, int y) const { return pixels[size_t(y) * canvas.width + size_t(x)]; }
};

static void drawing()
{
    constexpr uint32_t kBase = 0x00336699;
    VkbdLook look;
    look.transparency = 0;
    look.dimming = 100;
    VkbdView view;
    view.cursor = unsigned(vk_find(0x20));
    Picture p(756, 574, kBase);
    draw_vkbd(p.canvas, view, look, 0);
    const VkbdGeometry g = vkbd_geometry(756, 574, 0);
    CHECK(g.half == 16 && g.width == 16 * 46 && g.height == 16 * 2 * 6 && g.x == (756 - g.width) / 2 && g.y == 574 - g.height - 4);
    // Fully dimmed: everything around the keyboard is black, and the keyboard's rectangle is not the picture.
    CHECK(p.at(0, 0) == 0 && p.at(755, 0) == 0 && p.at(5, g.y + 5) == 0 && p.at(750, g.y + 5) == 0 && p.at(300, 573) == 0 && p.at(300, g.y - 1) == 0);
    int base_left = 0;
    for (int y = g.y; y < g.y + g.height; ++y)
        for (int x = g.x; x < g.x + g.width; ++x) base_left += p.at(x, y) == kBase;
    CHECK(base_left > 0);                                                              // the gaps between keys show the picture, undimmed
    const VkbdPalette palette = vkbd_palette(look);
    // The key under the cursor has the highlight border; a key does not; the face is the palette's (opaque with transparency 0).
    const VKey &a = kVkbd[view.cursor];
    const int ax = g.x + a.x * g.half, ay = g.y + a.row * g.half * 2;
    CHECK(p.at(ax + 2, ay + 2) == palette.select);
    const VKey &s = kVkbd[vk_find(0x21)];
    const int sx = g.x + s.x * g.half, sy = g.y + s.row * g.half * 2;
    CHECK(p.at(sx + 1, sy + 1) == palette.border);
    CHECK(p.at(sx + 5, sy + 6) == palette.face || p.at(sx + 5, sy + 6) == palette.text || p.at(sx + 5, sy + 6) == palette.edge);
    // A held key and a latched modifier are in the pressed color; Caps Lock shows while it is on.
    view.held = 0x21;
    view.latch = 1u << vk_latch_bit(0x60);
    view.caps = true;
    Picture q(756, 574, kBase);
    draw_vkbd(q.canvas, view, look, 0);
    auto face = [&](const Picture &pic, uint8_t code) {
        const VKey &k = kVkbd[vk_find(code)];
        return pic.at(g.x + k.x * g.half + 4, g.y + k.row * g.half * 2 + g.half * 2 - 5);
    };
    CHECK(face(q, 0x21) == palette.pressed && face(q, 0x60) == palette.pressed && face(q, 0x62) == palette.pressed && face(q, 0x22) == palette.face);
    // Transparency: with 25% the face lets a quarter of the picture through; the opaque flag (RetroPad A) puts the full face back.
    look.transparency = 25;
    Picture r(756, 574, kBase);
    draw_vkbd(r.canvas, VkbdView(), look, 0);
    CHECK(face(r, 0x22) != palette.face && face(r, 0x22) == blend(kBase, palette.face, 191));
    VkbdView solid;
    solid.opaque = true;
    Picture o(756, 574, kBase);
    draw_vkbd(o.canvas, solid, look, 0);
    CHECK(face(o, 0x22) == palette.face);
    look.transparency = 100;                                                           // all transparent: only labels and frames
    Picture t(756, 574, kBase);
    draw_vkbd(t.canvas, VkbdView(), look, 0);
    CHECK(face(t, 0x22) == kBase);
    // The themes differ, an outline differs from a shadow, and Automatic follows the machine.
    VkbdLook dark = look, beige = look;
    dark.theme = 4;
    beige.theme = 1;
    CHECK(vkbd_palette(dark).face != vkbd_palette(beige).face);
    VkbdLook a500 = look, a1200 = look;
    a1200.a1200 = true;
    CHECK(vkbd_palette(a500).face == vkbd_palette(beige).face && vkbd_palette(a1200).face == vkbd_palette(VkbdLook{3, false, 25, 25, false}).face);
    Picture shadow(756, 574, kBase), outline(756, 574, kBase);
    look.transparency = 0;
    draw_vkbd(shadow.canvas, VkbdView(), look, 0);
    look.outline = true;
    draw_vkbd(outline.canvas, VkbdView(), look, 0);
    CHECK(shadow.pixels != outline.pixels);
    // Lifted above a bar, a keyboard stays above it; on a picture too narrow to read it draws nothing (and never outside the picture).
    const VkbdGeometry lifted = vkbd_geometry(756, 574, 22);
    CHECK(lifted.y + lifted.height + 22 + 4 == 574);
    Picture narrow(300, 200, kBase);
    draw_vkbd(narrow.canvas, VkbdView(), look, 0);
    CHECK(vkbd_geometry(300, 200, 0).half == 0);
    for (uint32_t pixel : narrow.pixels) CHECK(pixel == kBase);
    Picture tiny(500, 120, kBase);                                                     // a keyboard taller than the picture is kept inside it
    draw_vkbd(tiny.canvas, VkbdView(), look, 0);
}

static void state()
{
    Section section;
    section.input.vkbd_open = true;
    section.input.vkbd_opaque = true;
    section.input.vk_cursor = 95;
    section.input.vk_held = 0x20;
    section.input.vk_latch = 0x7F;
    section.input.vk_repeat = 200;
    std::vector<uint8_t> bytes;
    encode_section(section, &bytes);
    CHECK(bytes.size() <= section_bound(0, 0));
    Section back;
    const char *why = nullptr;
    CHECK(decode_section(bytes.data(), bytes.size(), 100, 760, 576, &back, &why) && back.input == section.input);
    auto accepts = [&](size_t word, uint32_t value) {
        std::vector<uint8_t> copy = bytes;
        put32(&copy[40 + 4 * word], value);
        Section out;
        return decode_section(copy.data(), copy.size(), 100, 760, 576, &out, &why);
    };
    const uint32_t open = 1;
    CHECK(accepts(9, open | 2u | 95u << 8 | 0x20u << 16) && !accepts(9, open | 96u << 8 | 0xFFu << 16));      // the cursor is one of the keys
    CHECK(accepts(9, open | 0xFFu << 16) && !accepts(9, open | 0x68u << 16) && !accepts(9, open | 0x0Eu << 16));   // a held key is a key of the keyboard (or none)
    CHECK(!accepts(9, open | 4u) && !accepts(9, open | 1u << 24 | 0xFFu << 16));                                    // no other flag, nothing above
    CHECK(accepts(10, 0x7F) && !accepts(10, 0x80) && !accepts(10, 1u << 16));                                      // the latch is seven bits
    // A closed keyboard holds nothing.
    Section closed;
    closed.input.vk_cursor = 7;
    encode_section(closed, &bytes);
    CHECK(decode_section(bytes.data(), bytes.size(), 100, 760, 576, &back, &why) && back.input.vk_cursor == 7);
    std::vector<uint8_t> closed_bytes = bytes;
    put32(&closed_bytes[40 + 4 * 9], 0x20u << 16 | 7u << 8);                          // closed, but a key held
    CHECK(!decode_section(closed_bytes.data(), closed_bytes.size(), 100, 760, 576, &back, &why));
    put32(&closed_bytes[40 + 4 * 9], 0xFFu << 16 | 7u << 8);
    put32(&closed_bytes[40 + 4 * 10], 3);                                              // closed, but a modifier latched
    CHECK(!decode_section(closed_bytes.data(), closed_bytes.size(), 100, 760, 576, &back, &why));
}

int main()
{
    layout();
    navigation();
    use();
    drawing();
    state();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("vkbd probe ok\n");
    return 0;
}
