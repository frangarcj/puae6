// The virtual keyboard (T31a): the Amiga keyboard drawn over the picture, driven by player 1's RetroPad. It is input like any other. The keys it presses are
// bits of the same p6_input key bitmap as the host keyboard's (mapper.h calls vkbd_step; read_input ORs the result into the packet), so the machine cannot tell
// a key of the virtual keyboard from a real one, a recording of the packets replays it, and everything the keyboard remembers between two runs (open, cursor,
// the key held, the latched modifiers, the repeat count, see) is in InputMemory and so in the adapter section of a state: a rewind or a run-ahead branch gives
// back the keyboard of the timeline it restores. What is drawn (draw_vkbd) is a function of that memory, over a copy of the picture (overlay.h).
//
// Use. Select (or any button/hotkey mapped to Toggle Virtual Keyboard) opens and closes it. Open, the RetroPad belongs to the keyboard and gives the joystick
// nothing (the frontend's mouse still works): the D-Pad moves the cursor (it repeats while held), B presses the key under it for as long as B is down, A switches
// the transparency, Y toggles Caps Lock and X presses Space. A modifier (Shift, Ctrl, Alt, Amiga) pressed with B latches until it is pressed again, so Shift+key
// is two presses; Caps Lock is the Amiga's own latch (the same one the host's Caps Lock key flips). Closing releases everything the keyboard held.
//
// The layout is the Amiga keyboard (A500/A1200 family): the main block, the cursor keys and the numeric keypad. Every key of input_map.h's table is in it once.
#ifndef P6_LIBRETRO_VKBD_H
#define P6_LIBRETRO_VKBD_H

#include "libretro.h"
#include "overlay.h"

#include <stdint.h>
#include <stdlib.h>

namespace p6_libretro {

struct VKey {
    const char *label;
    uint8_t code;          // the Amiga raw key code
    uint8_t row;           // 0..5
    uint8_t x;             // left edge in half keys
    uint8_t width;         // in half keys
};

constexpr unsigned kVkbdRows = 6;
constexpr unsigned kVkbdKeys = 96;
constexpr unsigned kVkbdHalfKeys = 46;        // the width of the whole layout, in half keys
constexpr uint8_t kVkbdNoKey = 0xFF;

#define VK(label, code, row, x, w) {label, code, row, x, w}
constexpr VKey kVkbd[kVkbdKeys] = {
    // row 0: ESC, F1-F10, DEL, HELP | keypad ( ) / *
    VK("ESC", 0x45, 0, 0, 2), VK("F1", 0x50, 0, 2, 2), VK("F2", 0x51, 0, 4, 2), VK("F3", 0x52, 0, 6, 2), VK("F4", 0x53, 0, 8, 2), VK("F5", 0x54, 0, 10, 2),
    VK("F6", 0x55, 0, 12, 2), VK("F7", 0x56, 0, 14, 2), VK("F8", 0x57, 0, 16, 2), VK("F9", 0x58, 0, 18, 2), VK("F10", 0x59, 0, 20, 2),
    VK("DEL", 0x46, 0, 22, 3), VK("HELP", 0x5F, 0, 25, 4),
    VK("(", 0x5A, 0, 34, 3), VK(")", 0x5B, 0, 37, 3), VK("/", 0x5C, 0, 40, 3), VK("*", 0x5D, 0, 43, 3),
    // row 1: ` 1-0 - = \ BS | 7 8 9 -
    VK("`", 0x00, 1, 0, 2), VK("1", 0x01, 1, 2, 2), VK("2", 0x02, 1, 4, 2), VK("3", 0x03, 1, 6, 2), VK("4", 0x04, 1, 8, 2), VK("5", 0x05, 1, 10, 2),
    VK("6", 0x06, 1, 12, 2), VK("7", 0x07, 1, 14, 2), VK("8", 0x08, 1, 16, 2), VK("9", 0x09, 1, 18, 2), VK("0", 0x0A, 1, 20, 2), VK("-", 0x0B, 1, 22, 2),
    VK("=", 0x0C, 1, 24, 2), VK("\\", 0x0D, 1, 26, 2), VK("BS", 0x41, 1, 28, 4),
    VK("7", 0x3D, 1, 34, 3), VK("8", 0x3E, 1, 37, 3), VK("9", 0x3F, 1, 40, 3), VK("-", 0x4A, 1, 43, 3),
    // row 2: TAB Q-P [ ] RET | 4 5 6 +
    VK("TAB", 0x42, 2, 0, 3), VK("Q", 0x10, 2, 3, 2), VK("W", 0x11, 2, 5, 2), VK("E", 0x12, 2, 7, 2), VK("R", 0x13, 2, 9, 2), VK("T", 0x14, 2, 11, 2),
    VK("Y", 0x15, 2, 13, 2), VK("U", 0x16, 2, 15, 2), VK("I", 0x17, 2, 17, 2), VK("O", 0x18, 2, 19, 2), VK("P", 0x19, 2, 21, 2), VK("[", 0x1A, 2, 23, 2),
    VK("]", 0x1B, 2, 25, 2), VK("RET", 0x44, 2, 27, 5),
    VK("4", 0x2D, 2, 34, 3), VK("5", 0x2E, 2, 37, 3), VK("6", 0x2F, 2, 40, 3), VK("+", 0x5E, 2, 43, 3),
    // row 3: CTRL CAPS A-L ; ' # | 1 2 3 ENTER
    VK("CTRL", 0x63, 3, 0, 4), VK("CAPS", 0x62, 3, 4, 3), VK("A", 0x20, 3, 7, 2), VK("S", 0x21, 3, 9, 2), VK("D", 0x22, 3, 11, 2), VK("F", 0x23, 3, 13, 2),
    VK("G", 0x24, 3, 15, 2), VK("H", 0x25, 3, 17, 2), VK("J", 0x26, 3, 19, 2), VK("K", 0x27, 3, 21, 2), VK("L", 0x28, 3, 23, 2), VK(";", 0x29, 3, 25, 2),
    VK("'", 0x2A, 3, 27, 2), VK("#", 0x2B, 3, 29, 2),
    VK("1", 0x1D, 3, 34, 3), VK("2", 0x1E, 3, 37, 3), VK("3", 0x1F, 3, 40, 3), VK("ENT", 0x43, 3, 43, 3),
    // row 4: LSHIFT < Z-M , . / RSHIFT UP | 0 .
    VK("SHIFT", 0x60, 4, 0, 4), VK("<", 0x30, 4, 4, 2), VK("Z", 0x31, 4, 6, 2), VK("X", 0x32, 4, 8, 2), VK("C", 0x33, 4, 10, 2), VK("V", 0x34, 4, 12, 2),
    VK("B", 0x35, 4, 14, 2), VK("N", 0x36, 4, 16, 2), VK("M", 0x37, 4, 18, 2), VK(",", 0x38, 4, 20, 2), VK(".", 0x39, 4, 22, 2), VK("/", 0x3A, 4, 24, 2),
    VK("SHIFT", 0x61, 4, 26, 4), VK("^", 0x4C, 4, 30, 3),
    VK("0", 0x0F, 4, 34, 6), VK(".", 0x3C, 4, 40, 3),
    // row 5: ALT AMIGA SPACE AMIGA ALT { ~ }
    VK("ALT", 0x64, 5, 0, 3), VK("AMI", 0x66, 5, 3, 3), VK("SPACE", 0x40, 5, 6, 12), VK("AMI", 0x67, 5, 18, 3), VK("ALT", 0x65, 5, 21, 3),
    VK("{", 0x4F, 5, 24, 3), VK("~", 0x4D, 5, 27, 3), VK("}", 0x4E, 5, 30, 3),
};
#undef VK

// The modifiers that latch, as bits of InputMemory::vk_latch and their key codes.
constexpr unsigned kVkLatchCount = 7;
constexpr uint8_t kVkLatchCodes[kVkLatchCount] = {0x60, 0x61, 0x63, 0x64, 0x65, 0x66, 0x67};

inline int vk_latch_bit(uint8_t code)
{
    for (unsigned i = 0; i < kVkLatchCount; ++i)
        if (kVkLatchCodes[i] == code) return int(i);
    return -1;
}

inline int vk_find(uint8_t code)
{
    for (unsigned i = 0; i < kVkbdKeys; ++i)
        if (kVkbd[i].code == code) return int(i);
    return -1;
}

constexpr unsigned kVkRepeatDelay = 16, kVkRepeatEvery = 4;        // units a direction is held before it repeats, and between repeats

// Moves the cursor one key in a direction (RETRO_DEVICE_ID_JOYPAD_UP/DOWN/LEFT/RIGHT). Left and right walk along the row and wrap; up and down go to the
// neighbouring row (wrapping) and land on the key whose middle is nearest to the middle of the one left.
inline unsigned vk_move(unsigned cursor, unsigned direction)
{
    const VKey &here = kVkbd[cursor];
    if (direction == RETRO_DEVICE_ID_JOYPAD_LEFT || direction == RETRO_DEVICE_ID_JOYPAD_RIGHT) {
        const int step = direction == RETRO_DEVICE_ID_JOYPAD_RIGHT ? 1 : -1;
        int best = -1;
        // The keys of this row in order of x: the nearest one on the chosen side, else the far end of the row.
        for (unsigned i = 0; i < kVkbdKeys; ++i) {
            if (kVkbd[i].row != here.row || i == cursor) continue;
            const int delta = (int(kVkbd[i].x) - int(here.x)) * step;
            if (delta > 0 && (best < 0 || delta < (int(kVkbd[best].x) - int(here.x)) * step)) best = int(i);
        }
        if (best < 0)
            for (unsigned i = 0; i < kVkbdKeys; ++i) {
                if (kVkbd[i].row != here.row || i == cursor) continue;
                const int delta = (int(kVkbd[i].x) - int(here.x)) * step;
                if (best < 0 || delta < (int(kVkbd[best].x) - int(here.x)) * step) best = int(i);
            }
        return best < 0 ? cursor : unsigned(best);
    }
    const int row = int(here.row) + (direction == RETRO_DEVICE_ID_JOYPAD_DOWN ? 1 : -1);
    const unsigned target_row = unsigned((row + int(kVkbdRows)) % int(kVkbdRows));
    const int middle = 2 * int(here.x) + int(here.width);
    int best = -1, best_distance = 0;
    for (unsigned i = 0; i < kVkbdKeys; ++i) {
        if (kVkbd[i].row != target_row) continue;
        const int distance = std::abs(2 * int(kVkbd[i].x) + int(kVkbd[i].width) - middle);
        if (best < 0 || distance < best_distance) {
            best = int(i);
            best_distance = distance;
        }
    }
    return best < 0 ? cursor : unsigned(best);
}

// --- drawing ----------------------------------------------------------------------------------------------------------------------------------------

// What the drawing needs of the keyboard's memory.
struct VkbdView {
    unsigned cursor = 0;
    uint8_t held = kVkbdNoKey;      // the key code B holds down, or kVkbdNoKey
    uint8_t latch = 0;              // bits of the latched modifiers (kVkLatchCodes)
    bool caps = false;              // the Amiga's Caps Lock is on
    bool opaque = false;            // A switched the transparency off
};

// The look: a theme (the schema's Virtual KBD Theme: auto, beige, cd32, light, dark, each with a shadow or an outline on the labels), how see-through the keys are
// and how much the picture around the keyboard is darkened.
struct VkbdLook {
    unsigned theme = 0;             // 0 auto, 1 beige, 2 cd32, 3 light, 4 dark
    bool outline = false;
    unsigned transparency = 25;     // percent
    unsigned dimming = 25;          // percent
    bool a1200 = false;             // for "auto"
};

struct VkbdPalette {
    uint32_t face, text, edge, select, pressed, border;
};

inline VkbdPalette vkbd_palette(const VkbdLook &look)
{
    unsigned theme = look.theme;
    if (theme == 0) theme = look.a1200 ? 3 : 1;                      // Automatic: the A500 and A600 keyboards are beige, the A1200's is light grey
    switch (theme) {
    case 1: return {0xD6CDB0, 0x202020, 0xE8E0C8, 0xFFC040, 0xE06020, 0x6A6450};
    case 2: return {0x30386A, 0xE8E8E8, 0x101830, 0xFFD040, 0xE05030, 0x8890C0};
    case 3: return {0xE4E4E4, 0x101010, 0xFFFFFF, 0x4C90FF, 0xFF8040, 0x808080};
    default: return {0x383838, 0xF0F0F0, 0x101010, 0x30A8FF, 0xFF8030, 0x808080};
    }
}

// Where the keyboard goes on a canvas: a half key is `half` pixels wide, a row `half * 2` high, centered, with its bottom `lift` rows above the bottom of the picture.
struct VkbdGeometry {
    int half = 0, x = 0, y = 0, width = 0, height = 0;
};

inline VkbdGeometry vkbd_geometry(uint32_t canvas_width, uint32_t canvas_height, int lift)
{
    VkbdGeometry g;
    g.half = (int(canvas_width) - 8) / int(kVkbdHalfKeys);
    if (g.half < 7) {                                     // too small to read: no keyboard
        g.half = 0;
        return g;
    }
    g.width = g.half * int(kVkbdHalfKeys);
    g.height = g.half * 2 * int(kVkbdRows);
    g.x = (int(canvas_width) - g.width) / 2;
    g.y = int(canvas_height) - g.height - lift - 4;
    if (g.y < 0) g.y = 0;
    return g;
}

inline void draw_vkbd(const Canvas &canvas, const VkbdView &view, const VkbdLook &look, int lift)
{
    const VkbdGeometry g = vkbd_geometry(canvas.width, canvas.height, lift);
    if (!g.half) return;
    const VkbdPalette palette = vkbd_palette(look);
    // The picture around the keyboard is darkened (the keyboard itself is not): four rectangles, so that the keys are drawn over the real picture.
    const unsigned dim = std::min(look.dimming, 100u) * 255 / 100;
    if (dim) {
        fill_rect(canvas, 0, 0, int(canvas.width), g.y, 0, dim);
        fill_rect(canvas, 0, g.y, g.x, g.height, 0, dim);
        fill_rect(canvas, g.x + g.width, g.y, int(canvas.width) - g.x - g.width, g.height, 0, dim);
        fill_rect(canvas, 0, g.y + g.height, int(canvas.width), int(canvas.height) - g.y - g.height, 0, dim);
    }
    const unsigned alpha = view.opaque ? 255 : (100 - std::min(look.transparency, 100u)) * 255 / 100;
    const int row_height = g.half * 2;
    for (unsigned i = 0; i < kVkbdKeys; ++i) {
        const VKey &key = kVkbd[i];
        const int x = g.x + int(key.x) * g.half, y = g.y + int(key.row) * row_height, w = int(key.width) * g.half, h = row_height;
        const int latch = vk_latch_bit(key.code);
        const bool latched = latch >= 0 && (view.latch >> latch & 1);
        const bool down = latched || key.code == view.held || (key.code == 0x62 && view.caps);
        const bool here = i == view.cursor;
        fill_rect(canvas, x + 1, y + 1, w - 2, h - 2, down ? palette.pressed : palette.face, down ? 255u : alpha);
        // The border: a frame in the key's edge color, thicker and highlighted on the key under the cursor.
        const uint32_t frame = here ? palette.select : palette.border;
        const int thick = here ? 2 : 1;
        fill_rect(canvas, x + 1, y + 1, w - 2, thick, frame);
        fill_rect(canvas, x + 1, y + h - 1 - thick, w - 2, thick, frame);
        fill_rect(canvas, x + 1, y + 1, thick, h - 2, frame);
        fill_rect(canvas, x + w - 1 - thick, y + 1, thick, h - 2, frame);
        int scale = 2;
        while (scale > 1 && text_width(key.label, scale) + 4 > w) --scale;
        if (text_width(key.label, scale) + 2 > w || kGlyphHeight * scale + 2 > h) continue;       // too small for a label
        draw_text(canvas, x + (w - text_width(key.label, scale)) / 2, y + (h - kGlyphHeight * scale) / 2, key.label, scale, palette.text,
                  look.outline ? TextStyle::Outline : TextStyle::Shadow, palette.edge);
    }
}

}  // namespace p6_libretro

#endif
