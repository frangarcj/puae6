// What the frontend's input becomes in the p6_input packet (T17c): the keyboard, the mouse and the joystick, and the device types the core offers per port.
//
// Everything here is a function of what the frontend reports in one retro_run (input_state_cb after the one input poll) and of InputMemory, the few bits
// the adapter has to keep between two runs. The core never uses the asynchronous keyboard callback: a callback that changed the machine between two
// units would put input outside the boundary, and a polled state is the same thing a replay sees again. InputMemory travels in the adapter section of
// the state (state_frame.h), so a load, a rewind or a runahead branch gives back the memory that belongs to the timeline it restores, never the one of
// an abandoned future.
//
// Keyboard. The Amiga keyboard is attached whatever the ports hold. RETROK_* codes are matched by the symbol the frontend reports for the key without
// Shift, which on a US keyboard is the key's position; the table is the position map of the Amiga keyboard (Amiga Hardware Reference Manual, Appendix G)
// and reaches every key that integration/bizhawk/core/Puae6/Controllers.cs names (0x00..0x67). Keys the Amiga does not have (F11, F12, Pause, Print...)
// and codes this table does not know are ignored. Amiga keys without a PC twin sit on the nearest keys: Help on Insert, the keypad parentheses on Home
// and End, the Amiga keys on Page Up and Page Down besides the Super/Meta keys. Host layout independence: only the position/symbol of the key matters,
// never the text it would type under the host's layout. Caps Lock is the Amiga's latching key: every press toggles it (contract: make when it turns on,
// break when it turns off), so its two bits live in InputMemory.
//
// Ports. The Amiga has the mouse port (Amiga port 1, p6_input.ports[0]) and the joystick port (Amiga port 2, p6_input.ports[1]); the contract has a mouse
// only in ports[0]. Like PUAE5 and the BizHawk wrapper, the frontend's first player is the joystick of the second Amiga port and the mouse is in the first:
//   libretro port 0 (player 1): "Joystick + Mouse" (RETRO_DEVICE_JOYPAD, the default) -> the RetroPad drives ports[1] and the mouse ports[0], both at once;
//                               "Joystick only" (a JOYPAD subclass) -> ports[1];  "Mouse only" -> ports[0];  Keyboard only / None -> nothing
//   libretro port 1 (player 2): Joystick -> ports[0] (the mouse port, which is where two-player games read their second stick);  Keyboard only / None -> nothing
// The default is the combined type, like PUAE5 (which reads the physical mouse besides the pad) and the BizHawk wrapper (mouse in the first port, joystick in the
// second). RetroArch hands a core RETRO_DEVICE_JOYPAD for every port a person has not changed, so the plain JOYPAD of port 0 is the combined type and the
// "Joystick only" subclass is what a person picks to free the mouse port for player 2. A mouse that does not move sends zero counts, so a joystick player loses nothing.
// Both players cannot hold ports[0] at once. When libretro port 0 has the mouse (Joystick + Mouse, or Mouse only) and libretro port 1 is a Joystick, the mouse
// keeps the Amiga port and the second player's joystick is not sent (a mouse and a stick cannot share a connector); an INFO message says so at load and when a
// call to retro_set_controller_port_device makes it so. The rule is a function of the two devices, which are the frontend's configuration, not the timeline.
//
// Mouse. RETRO_DEVICE_MOUSE gives the movement since the last poll, read at libretro port 0 (where RetroArch delivers the one physical mouse). At most 120
// counts per axis and unit reach the guest (docs/02-contracts.md); what does not fit is not lost but kept in InputMemory and sent in the next runs (the
// BizHawk wrapper does the same with its pointer reference). The backlog is bounded, so a long burst never keeps the pointer moving for seconds after the
// hand stopped. The host's own pointer speed and acceleration are the frontend's business: the core adds none and reads no clock.
//
// Joystick. B, A and X are the buttons 1, 2 and 3 (the two fire buttons and the third of a gamepad); the D-pad and the left stick give the four
// directions, the stick beyond a fixed threshold (a pure function of the reading, no timers). The CD32 pad, the Arcadia pad, the analog joystick, the
// light gun and light pen of PUAE5, and the keyboard-as-joystick emulation, are not part of the A500 profile of p6_input (docs/02-contracts.md).
//
// Double input. A frontend that binds host keys to a RetroPad (RetroArch's default binds the arrows and some letters) makes those keys reach the core twice:
// as joystick and, through the keyboard, as Amiga keys. The core cannot know the bindings, and PUAE5's way out (its disable_keys, which silences the cursor
// keys while its own keyboard joystick is active) depends on a keyboard joystick this core does not have. What the core does is offer "Keyboard only" in
// each port: a port with that type reads no RetroPad, so a person who types uses it, and a person who plays with the keyboard bound to a pad keeps Joystick.
#ifndef P6_LIBRETRO_INPUT_MAP_H
#define P6_LIBRETRO_INPUT_MAP_H

#include "libretro.h"
#include "p6.h"

#include <algorithm>
#include <stdint.h>

namespace p6_libretro {

constexpr unsigned kPortCount = 2;
constexpr int kMouseMaxCounts = 120;              // what the engine delivers per axis and unit (docs/02-contracts.md)
constexpr int kMouseBacklog = 8 * kMouseMaxCounts; // the most movement kept for later runs, per axis
constexpr int kStickThreshold = 20000;            // of 32767, the same as PUAE5 uses
constexpr unsigned kCapsLockCode = 0x62;
// Player 1's RetroPad without the mouse: a subclass of RETRO_DEVICE_JOYPAD, which is how a frontend names a variant of the pad. Plain RETRO_DEVICE_JOYPAD
// in port 0 is the pad plus the mouse.
constexpr unsigned kDeviceJoystickOnly = RETRO_DEVICE_SUBCLASS(RETRO_DEVICE_JOYPAD, 0);

// The Amiga key (raw code 0..0x67) each RETROK_* code presses, in the order of the Amiga codes. Several host keys may press the same Amiga key; the
// bit is set when any is down. Caps Lock (0x62) is not here: it latches (see read_input).
struct KeyMapping {
    unsigned retro;
    uint8_t amiga;
};

constexpr KeyMapping kKeyMap[] = {
    {RETROK_BACKQUOTE, 0x00},
    {RETROK_1, 0x01}, {RETROK_2, 0x02}, {RETROK_3, 0x03}, {RETROK_4, 0x04}, {RETROK_5, 0x05},
    {RETROK_6, 0x06}, {RETROK_7, 0x07}, {RETROK_8, 0x08}, {RETROK_9, 0x09}, {RETROK_0, 0x0A},
    {RETROK_MINUS, 0x0B}, {RETROK_EQUALS, 0x0C}, {RETROK_BACKSLASH, 0x0D}, {RETROK_KP0, 0x0F},
    {RETROK_q, 0x10}, {RETROK_w, 0x11}, {RETROK_e, 0x12}, {RETROK_r, 0x13}, {RETROK_t, 0x14},
    {RETROK_y, 0x15}, {RETROK_u, 0x16}, {RETROK_i, 0x17}, {RETROK_o, 0x18}, {RETROK_p, 0x19},
    {RETROK_LEFTBRACKET, 0x1A}, {RETROK_RIGHTBRACKET, 0x1B}, {RETROK_KP1, 0x1D}, {RETROK_KP2, 0x1E}, {RETROK_KP3, 0x1F},
    {RETROK_a, 0x20}, {RETROK_s, 0x21}, {RETROK_d, 0x22}, {RETROK_f, 0x23}, {RETROK_g, 0x24},
    {RETROK_h, 0x25}, {RETROK_j, 0x26}, {RETROK_k, 0x27}, {RETROK_l, 0x28},
    {RETROK_SEMICOLON, 0x29}, {RETROK_QUOTE, 0x2A}, {RETROK_HASH, 0x2B}, {RETROK_KP4, 0x2D}, {RETROK_KP5, 0x2E}, {RETROK_KP6, 0x2F},
    {RETROK_LESS, 0x30}, {RETROK_OEM_102, 0x30},
    {RETROK_z, 0x31}, {RETROK_x, 0x32}, {RETROK_c, 0x33}, {RETROK_v, 0x34}, {RETROK_b, 0x35}, {RETROK_n, 0x36}, {RETROK_m, 0x37},
    {RETROK_COMMA, 0x38}, {RETROK_PERIOD, 0x39}, {RETROK_SLASH, 0x3A}, {RETROK_KP_PERIOD, 0x3C},
    {RETROK_KP7, 0x3D}, {RETROK_KP8, 0x3E}, {RETROK_KP9, 0x3F},
    {RETROK_SPACE, 0x40}, {RETROK_BACKSPACE, 0x41}, {RETROK_TAB, 0x42}, {RETROK_KP_ENTER, 0x43}, {RETROK_RETURN, 0x44}, {RETROK_ESCAPE, 0x45},
    {RETROK_DELETE, 0x46}, {RETROK_KP_MINUS, 0x4A},
    {RETROK_UP, 0x4C}, {RETROK_DOWN, 0x4D}, {RETROK_RIGHT, 0x4E}, {RETROK_LEFT, 0x4F},
    {RETROK_F1, 0x50}, {RETROK_F2, 0x51}, {RETROK_F3, 0x52}, {RETROK_F4, 0x53}, {RETROK_F5, 0x54},
    {RETROK_F6, 0x55}, {RETROK_F7, 0x56}, {RETROK_F8, 0x57}, {RETROK_F9, 0x58}, {RETROK_F10, 0x59},
    {RETROK_HOME, 0x5A}, {RETROK_END, 0x5B}, {RETROK_KP_DIVIDE, 0x5C}, {RETROK_KP_MULTIPLY, 0x5D}, {RETROK_KP_PLUS, 0x5E},
    {RETROK_HELP, 0x5F}, {RETROK_INSERT, 0x5F},
    {RETROK_LSHIFT, 0x60}, {RETROK_RSHIFT, 0x61},
    {RETROK_LCTRL, 0x63}, {RETROK_RCTRL, 0x63}, {RETROK_LALT, 0x64}, {RETROK_RALT, 0x65},
    {RETROK_LMETA, 0x66}, {RETROK_LSUPER, 0x66}, {RETROK_PAGEUP, 0x66},
    {RETROK_RMETA, 0x67}, {RETROK_RSUPER, 0x67}, {RETROK_PAGEDOWN, 0x67},
};

// What the adapter keeps between two runs for the input. It is part of the saved timeline (state_frame.h), and is zero at load.
struct InputMemory {
    bool caps_latched = false;         // the Amiga's Caps Lock is on: bit 0x62 of the packet
    bool caps_held = false;            // the host's Caps Lock key was down at the last run: a press is the edge from up to down
    int32_t mouse_rx = 0, mouse_ry = 0;    // movement of the host that did not fit the 120 counts of a unit

    bool operator==(const InputMemory &other) const
    {
        return caps_latched == other.caps_latched && caps_held == other.caps_held && mouse_rx == other.mouse_rx && mouse_ry == other.mouse_ry;
    }
};

// The device types a port accepts. Keyboard means "nothing in this port": the Amiga keyboard is attached whatever the ports hold, but frontends use the
// type to hand the physical keyboard to the core (RetroArch's game focus) instead of reading it as a RetroPad.
inline bool device_allowed(unsigned port, unsigned device)
{
    if (port >= kPortCount) return false;
    switch (device) {
    case RETRO_DEVICE_NONE:
    case RETRO_DEVICE_JOYPAD:
    case RETRO_DEVICE_KEYBOARD:
        return true;
    case RETRO_DEVICE_MOUSE:
    case kDeviceJoystickOnly:
        return port == 0;            // the one physical mouse is libretro port 0's, and it goes to the contract's only mouse port
    default:
        return false;
    }
}

inline void read_keys(retro_input_state_t state, InputMemory *memory, uint32_t keys[4])
{
    for (const KeyMapping &mapping : kKeyMap)
        if (state(0, RETRO_DEVICE_KEYBOARD, 0, mapping.retro))
            keys[mapping.amiga / 32] |= UINT32_C(1) << (mapping.amiga % 32);
    const bool held = state(0, RETRO_DEVICE_KEYBOARD, 0, RETROK_CAPSLOCK) != 0;
    if (held && !memory->caps_held) memory->caps_latched = !memory->caps_latched;       // every press flips the latch
    memory->caps_held = held;
    if (memory->caps_latched) keys[kCapsLockCode / 32] |= UINT32_C(1) << (kCapsLockCode % 32);
}

inline void read_joystick(retro_input_state_t state, unsigned port, p6_port_input *out)
{
    auto pressed = [&](unsigned id) { return state(port, RETRO_DEVICE_JOYPAD, 0, id) != 0; };
    auto stick = [&](unsigned id) { return int(state(port, RETRO_DEVICE_ANALOG, RETRO_DEVICE_INDEX_ANALOG_LEFT, id)); };
    out->type = P6_PORT_JOYSTICK;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_B)) out->buttons |= P6_BUTTON_1;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_A)) out->buttons |= P6_BUTTON_2;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_X)) out->buttons |= P6_BUTTON_3;
    const int x = stick(RETRO_DEVICE_ID_ANALOG_X), y = stick(RETRO_DEVICE_ID_ANALOG_Y);
    if (pressed(RETRO_DEVICE_ID_JOYPAD_UP) || y < -kStickThreshold) out->buttons |= P6_BUTTON_UP;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_DOWN) || y > kStickThreshold) out->buttons |= P6_BUTTON_DOWN;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_LEFT) || x < -kStickThreshold) out->buttons |= P6_BUTTON_LEFT;
    if (pressed(RETRO_DEVICE_ID_JOYPAD_RIGHT) || x > kStickThreshold) out->buttons |= P6_BUTTON_RIGHT;
}

// Takes `want` counts of the movement owed on one axis: at most kMouseMaxCounts now, the rest stays owed (bounded).
inline int16_t take_counts(int32_t *owed, int want)
{
    const int32_t total = std::clamp<int32_t>(*owed + want, -kMouseBacklog - kMouseMaxCounts, kMouseBacklog + kMouseMaxCounts);
    const int32_t now = std::clamp<int32_t>(total, -kMouseMaxCounts, kMouseMaxCounts);
    *owed = std::clamp<int32_t>(total - now, -kMouseBacklog, kMouseBacklog);
    return int16_t(now);
}

inline void read_mouse(retro_input_state_t state, InputMemory *memory, p6_port_input *out)
{
    auto read = [&](unsigned id) { return int(state(0, RETRO_DEVICE_MOUSE, 0, id)); };      // the one physical mouse is libretro port 0's
    out->type = P6_PORT_MOUSE;
    out->dx = take_counts(&memory->mouse_rx, read(RETRO_DEVICE_ID_MOUSE_X));
    out->dy = take_counts(&memory->mouse_ry, read(RETRO_DEVICE_ID_MOUSE_Y));
    if (read(RETRO_DEVICE_ID_MOUSE_LEFT)) out->buttons |= P6_BUTTON_1;
    if (read(RETRO_DEVICE_ID_MOUSE_RIGHT)) out->buttons |= P6_BUTTON_2;
    if (read(RETRO_DEVICE_ID_MOUSE_MIDDLE)) out->buttons |= P6_BUTTON_3;
}

// The Amiga port (index in p6_input.ports) the joystick of a libretro port drives: player 1 the joystick port, player 2 the mouse port.
inline unsigned joystick_amiga_port(unsigned libretro_port) { return libretro_port == 0 ? 1 : 0; }

// What libretro port 0's device reads: its RetroPad (-> ports[1]) and the mouse (-> ports[0]).
inline bool reads_pad(unsigned device) { return device == RETRO_DEVICE_JOYPAD || device == kDeviceJoystickOnly; }
inline bool reads_mouse(unsigned device) { return device == RETRO_DEVICE_JOYPAD || device == RETRO_DEVICE_MOUSE; }

// True when libretro port 1's joystick has no Amiga port to drive because libretro port 0's mouse holds it.
inline bool joystick_shadowed_by_mouse(const unsigned device[kPortCount])
{
    return reads_mouse(device[0]) && device[1] == RETRO_DEVICE_JOYPAD;
}

// The packet of one run, from the frontend's state (polled once, just before) and the device of each libretro port. An Amiga port nothing drives is empty
// (P6_PORT_NONE with all fields zero). Called once per p6_run_frame: it moves InputMemory.
inline p6_input read_input(retro_input_state_t state, const unsigned device[kPortCount], InputMemory *memory)
{
    p6_input input = {};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    read_keys(state, memory, input.keys);
    if (reads_mouse(device[0])) read_mouse(state, memory, &input.ports[0]);                   // the mouse wins the mouse port
    else memory->mouse_rx = memory->mouse_ry = 0;                                              // no movement is owed to a mouse that is not there
    if (reads_pad(device[0])) read_joystick(state, 0, &input.ports[joystick_amiga_port(0)]);
    if (device[1] == RETRO_DEVICE_JOYPAD && !joystick_shadowed_by_mouse(device)) read_joystick(state, 1, &input.ports[joystick_amiga_port(1)]);
    return input;
}

// What the frontend is told in RETRO_ENVIRONMENT_SET_CONTROLLER_INFO (the lists end with a null description) and SET_INPUT_DESCRIPTORS.
inline void offer_controllers(retro_environment_t environment)
{
    static const retro_controller_description port0[] = {
        {"Joystick + Mouse (Amiga ports 2 and 1)", RETRO_DEVICE_JOYPAD}, {"Joystick only (Amiga port 2)", kDeviceJoystickOnly},
        {"Mouse only (Amiga port 1)", RETRO_DEVICE_MOUSE}, {"Keyboard only (port empty)", RETRO_DEVICE_KEYBOARD}, {"None", RETRO_DEVICE_NONE},
    };
    static const retro_controller_description port1[] = {
        {"Joystick (Amiga port 1; needs Player 1 on Joystick only)", RETRO_DEVICE_JOYPAD}, {"Keyboard only (port empty)", RETRO_DEVICE_KEYBOARD},
        {"None", RETRO_DEVICE_NONE},
    };
    static const retro_controller_info ports[] = {
        {port0, sizeof port0 / sizeof port0[0]}, {port1, sizeof port1 / sizeof port1[0]}, {nullptr, 0},
    };
    environment(RETRO_ENVIRONMENT_SET_CONTROLLER_INFO, const_cast<retro_controller_info *>(ports));      // optional
}

inline void offer_input_descriptors(retro_environment_t environment)
{
#define P6_JOYSTICK_DESCRIPTORS(port)                                                                  \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_UP, "Up"},                                  \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_DOWN, "Down"},                              \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_LEFT, "Left"},                              \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_RIGHT, "Right"},                            \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_B, "Fire (button 1)"},                      \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_A, "Button 2"},                             \
    {port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_X, "Button 3"},                             \
    {port, RETRO_DEVICE_ANALOG, RETRO_DEVICE_INDEX_ANALOG_LEFT, RETRO_DEVICE_ID_ANALOG_X, "Stick X"}, \
    {port, RETRO_DEVICE_ANALOG, RETRO_DEVICE_INDEX_ANALOG_LEFT, RETRO_DEVICE_ID_ANALOG_Y, "Stick Y"}
    static const retro_input_descriptor descriptors[] = {
        P6_JOYSTICK_DESCRIPTORS(0),
        P6_JOYSTICK_DESCRIPTORS(1),
        {0, RETRO_DEVICE_MOUSE, 0, RETRO_DEVICE_ID_MOUSE_X, "Mouse X"},
        {0, RETRO_DEVICE_MOUSE, 0, RETRO_DEVICE_ID_MOUSE_Y, "Mouse Y"},
        {0, RETRO_DEVICE_MOUSE, 0, RETRO_DEVICE_ID_MOUSE_LEFT, "Mouse left button"},
        {0, RETRO_DEVICE_MOUSE, 0, RETRO_DEVICE_ID_MOUSE_RIGHT, "Mouse right button"},
        {0, RETRO_DEVICE_MOUSE, 0, RETRO_DEVICE_ID_MOUSE_MIDDLE, "Mouse middle button"},
        {0, 0, 0, 0, nullptr},
    };
#undef P6_JOYSTICK_DESCRIPTORS
    environment(RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS, const_cast<retro_input_descriptor *>(descriptors));      // optional
}

}  // namespace p6_libretro

#endif
