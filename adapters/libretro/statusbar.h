// The statusbar and the on-screen messages (T31b): a bar at the top or the bottom of the picture the frontend gets, with the LED of the floppy drive, what the two
// Amiga ports are being sent, the pad's mode, and the last message of the core. It is drawn on a copy of the picture (overlay.h), from three things that are
// not the machine: the flags of the unit just run (P6_FRAME_DRIVE_LIGHT), the p6_input packet the adapter itself made for that unit, and the input memory (the
// mode flags and the message with the number of units it has left, which travel with the state so that a rewound or run-ahead picture shows what the saved
// timeline showed). Nothing here is read back by the machine.
//
// What the bar shows is what the ABI reports: the drive light is the single flag of P6_FRAME_DRIVE_LIGHT (some drive's LED is on; libretro drives DF0 alone), so
// the bar has one drive LED, DF0. The power LED, the track and the other devices of PUAE5's bar are not part of the ABI and are not drawn.
//
// Modes (the schema's Statusbar Mode): where (top or bottom), and how much: Full is the LED, the two ports and the pad's mode and turbo and the message; Basic is
// the LED and the message; Minimal is the LED alone. The bar is shown or hidden by the hotkey or the mapped button (Statusbar Startup says how a session starts);
// with the bar hidden, Statusbar Messages shows the message of an action alone for its few seconds.
#ifndef P6_LIBRETRO_STATUSBAR_H
#define P6_LIBRETRO_STATUSBAR_H

#include "mapper.h"
#include "overlay.h"

#include <stdio.h>
#include <string.h>

namespace p6_libretro {

struct StatusbarMode {
    bool top = false;           // at the top of the picture (else at the bottom)
    bool basic = false;         // the LED and the message only
    bool minimal = false;       // the LED only
};

// The schema's values of the Statusbar Mode option: bottom, bottom_minimal, bottom_basic, bottom_basic_minimal, then the same four on top (PUAE5's order).
inline StatusbarMode statusbar_mode(uint32_t value)
{
    StatusbarMode mode;
    mode.top = value >= 4;
    mode.minimal = (value & 1) != 0;
    mode.basic = (value & 2) != 0;
    return mode;
}

// What the bar draws from: the unit just run and the packet the adapter gave it.
struct StatusData {
    bool drive_light = false;
    p6_port_input ports[2] = {};
    bool turbo = false;          // auto-fire is on (the option as the hotkey left it)
    bool mouse_mode = false;     // the pad's joystick functions are the mouse's
    uint8_t message = 0;         // a kMessage* of mapper.h, 0 for none
};

inline const char *message_text(uint8_t id)
{
    switch (id) {
    case kMessageJoystick: return "JOYSTICK";
    case kMessageMouse: return "MOUSE";
    case kMessageTurboOn: return "TURBO ON";
    case kMessageTurboOff: return "TURBO OFF";
    case kMessageCropOn: return "CROP ON";
    case kMessageCropOff: return "CROP OFF";
    case kMessageAspectPal: return "ASPECT PAL";
    case kMessageAspectNtsc: return "ASPECT NTSC";
    case kMessageAspectNormal: return "ASPECT NORMAL";
    case kMessageReset: return "RESET";
    default: return "";
    }
}

constexpr uint32_t kBarBackground = 0x0C1418, kBarText = 0xF0F0F0, kBarDim = 0x586068, kBarLit = 0xFFE040, kLedOn = 0x40F040, kLedOff = 0x103810;

// The geometry of the bar: it is `height` rows high and `scale` pixels to a font pixel, the largest scale (2 or 1) at which `needed` pixels of text fit.
struct BarLayout {
    int scale = 1;
    int height = 0;
};

inline BarLayout bar_layout(uint32_t canvas_width, int needed_at_scale_1)
{
    BarLayout layout;
    layout.scale = (needed_at_scale_1 * 2 <= int(canvas_width)) ? 2 : 1;
    layout.height = kGlyphHeight * layout.scale + 4 * layout.scale;
    return layout;
}

// One text of the bar, advancing the pen: `lit` draws it bright, else dim.
inline void bar_text(const Canvas &canvas, int *pen, int y, const char *text, int scale, bool lit)
{
    draw_text(canvas, *pen, y, text, scale, lit ? kBarLit : kBarDim);
    *pen += text_width(text, scale) + 2 * scale;
}

// The two ports as the packet gives them: "1M LMR" for the mouse port (the mouse's three buttons), "2J ^~{}123" for the joystick port (the four directions and the
// three buttons); each symbol is lit while it is down, so a person sees what the pad and the mouse are sending.
inline void draw_ports(const Canvas &canvas, int *pen, int y, int scale, const p6_port_input ports[2])
{
    for (int index = 0; index < 2; ++index) {
        const p6_port_input &port = ports[index];
        char head[4] = {char('1' + index), port.type == P6_PORT_MOUSE ? 'M' : port.type == P6_PORT_JOYSTICK ? 'J' : '-', 0, 0};
        bar_text(canvas, pen, y, head, scale, port.type != P6_PORT_NONE);
        if (port.type == P6_PORT_MOUSE) {
            static const char names[3][2] = {"L", "M", "R"};
            for (int button = 0; button < 3; ++button) {
                draw_text(canvas, *pen, y, names[button], scale, (port.buttons >> button & 1) ? kBarLit : kBarDim);
                *pen += kGlyphAdvance * scale;
            }
        } else if (port.type == P6_PORT_JOYSTICK) {
            static const char names[7][2] = {"^", "~", "{", "}", "1", "2", "3"};
            static const uint32_t bits[7] = {P6_BUTTON_UP, P6_BUTTON_DOWN, P6_BUTTON_LEFT, P6_BUTTON_RIGHT, P6_BUTTON_1, P6_BUTTON_2, P6_BUTTON_3};
            for (int symbol = 0; symbol < 7; ++symbol) {
                draw_text(canvas, *pen, y, names[symbol], scale, (port.buttons & bits[symbol]) ? kBarLit : kBarDim);
                *pen += kGlyphAdvance * scale;
            }
        }
        *pen += 2 * scale;
    }
}

// Draws the bar (when `bar` is set) or the message alone (when only `message_strip` is), at the top or the bottom of the canvas.
inline void draw_statusbar(const Canvas &canvas, const StatusbarMode &mode, const StatusData &data, bool bar, bool message_strip)
{
    const char *message = message_text(data.message);
    const bool has_message = *message != 0;
    const bool show_message = has_message && !mode.minimal;           // Minimal is the LED alone
    if (!bar && !(message_strip && show_message)) return;
    // The width the content needs at scale 1 decides the scale (2 when it fits twice over): LED, ports, flags and message.
    const int led = bar ? (mode.minimal ? 10 : 29) : 0;
    const int full = bar && !mode.minimal && !mode.basic ? 90 + 62 : 0;
    const int needed = 4 + led + full + (show_message ? int(strlen(message)) * kGlyphAdvance + 8 : 0);
    const BarLayout layout = bar_layout(canvas.width, needed);
    const int scale = layout.scale;
    const int y = mode.top ? 0 : int(canvas.height) - layout.height;
    fill_rect(canvas, 0, y, int(canvas.width), layout.height, kBarBackground, 230);
    const int text_y = y + 2 * scale;
    int pen = 4 * scale;
    if (bar) {
        if (!mode.minimal) {
            draw_text(canvas, pen, text_y, "DF0", scale, kBarText);
            pen += text_width("DF0", scale) + 2 * scale;
        }
        fill_rect(canvas, pen, text_y, 8 * scale, kGlyphHeight * scale, data.drive_light ? kLedOn : kLedOff);
        pen += 10 * scale;
        if (!mode.minimal && !mode.basic) {
            draw_ports(canvas, &pen, text_y, scale, data.ports);
            if (data.mouse_mode) bar_text(canvas, &pen, text_y, "MOUSE", scale, true);
            if (data.turbo) bar_text(canvas, &pen, text_y, "TURBO", scale, true);
        }
    }
    if (show_message) {
        const int width = text_width(message, scale);
        const int at = bar ? std::max(pen, int(canvas.width) - width - 4 * scale) : 4 * scale;
        draw_text(canvas, at, text_y, message, scale, kBarText);
    }
}

}  // namespace p6_libretro

#endif
