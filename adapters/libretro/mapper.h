// The RetroPad mapper, turbo fire and the mouse-by-stick / mouse-by-D-Pad of player 1 (T31d), as a pure function of what the frontend reported in one run and
// of InputMemory. Nothing here reads a frontend, a clock or the machine: read_input (input_map.h) gathers the controls into a `Controls`, mapper_step turns
// them into an `Effects` (joystick bits, mouse buttons and movement, Amiga keys), and the packet of p6_run_frame is made from that. The UI of the core
// is therefore input like any other: a recording of the packets replays it, and rewind and runahead reproduce it because everything mapper_step remembers
// is in InputMemory, which travels in the adapter section of the state (state_frame.h).
//
// Inputs. Player 1's RetroPad has 24 mappable inputs: its 16 buttons (numbered as RETRO_DEVICE_ID_JOYPAD_*) and the four directions of each analog stick
// (beyond kStickThreshold, a reading of the stick and nothing else). An input does what the mapper option says (a target of the schema's mapper_target set:
// a joystick direction or button, a mouse button or movement, an Amiga key, an action of the core, or nothing), and when the option says "Default function",
// what it always did: the D-Pad and the left stick are the joystick directions, B / A / X the fire buttons (Face Button Options re-deals them), the rest nothing.
// Keys can be hotkeys too: a key of the physical keyboard that runs an action of the core and is not sent to the Amiga.
//
// Actions happen on the press (the edge from released to pressed, remembered in InputMemory::held); the functions of an input last while it is down.
//   Toggle Joystick/Mouse   the pad's joystick functions become mouse ones (direction = movement at the D-Pad Mouse Speed, fire / 2nd fire / 3rd = left / right /
//                           middle button) while the mouse port is the pad's to use; the choice is InputMemory::dpad_mouse
//   Toggle Turbo Fire       inverts the Turbo Fire setting (InputMemory::turbo_toggled)
//   Toggle Aspect, Crop     presentation toggles kept in InputMemory (present.h reads them)
//   Reset                   holds Ctrl, Left Amiga and Right Amiga for kResetHoldUnits units: the Amiga's own soft reset, as a keyboard sends it
//
// Turbo. The turbo button, while held and while turbo is on, is pressed for the first half of every Pulse units and released for the second; the phase is a
// count of units in InputMemory (0 whenever the button is up), so the pattern is the same in every replay and is never a time.
//
// Analog stick mouse. Past the deadzone, the speed of a stick grows from zero to its full value at the edge of the stick (full value at 100%: 10 counts a unit,
// kStickFullCounts); what does not make a whole count is carried to the next unit (InputMemory::stick_frac) and dropped when the stick is back at the center,
// so a slow push still moves the mouse and a released stick leaves nothing behind. The arithmetic is integer: the same on every host.
#ifndef P6_LIBRETRO_MAPPER_H
#define P6_LIBRETRO_MAPPER_H

#include "libretro.h"
#include "firmware.h"
#include "p6.h"
#include "present.h"
#include "vkbd.h"

#include <algorithm>
#include <stdint.h>

namespace p6_libretro {

constexpr unsigned kPortCount = 4;
constexpr int kMouseMaxCounts = 120;               // what the engine delivers per axis and unit (docs/02-contracts.md)
constexpr int kMouseBacklog = 8 * kMouseMaxCounts; // the most movement kept for later runs, per axis
constexpr int kStickThreshold = 20000;             // of 32767, the same as PUAE5 uses for a stick as buttons
constexpr int kStickRange = 32768;
constexpr int kStickFullCounts = 10;               // counts a unit of a stick at the edge, at 100% speed
constexpr unsigned kTurboCycle = 120;              // the phase of the turbo wraps here: every even pulse up to 12 divides it
constexpr unsigned kResetHoldUnits = 40;           // how long the soft reset holds Ctrl-Amiga-Amiga
constexpr unsigned kCapsLockCode = 0x62;
constexpr unsigned kKeyCtrl = 0x63, kKeyLeftAmiga = 0x66, kKeyRightAmiga = 0x67;
constexpr uint32_t kKeyTargetBase = 0x100;         // mapper targets from here on are Amiga keys: base + the key's code
constexpr unsigned kMessageUnits = 150;            // how long the statusbar shows the message of an action: three seconds of PAL

// The messages of the actions (statusbar.h has their text). 0 is none.
constexpr uint8_t kMessageNone = 0, kMessageJoystick = 1, kMessageMouse = 2, kMessageTurboOn = 3, kMessageTurboOff = 4, kMessageCropOn = 5, kMessageCropOff = 6,
                  kMessageAspectPal = 7, kMessageAspectNtsc = 8, kMessageAspectNormal = 9, kMessageReset = 10;

constexpr unsigned kMapperInputs = 24;
constexpr unsigned kStickInput = 16;               // inputs 16..19: left stick up, down, left, right; 20..23 the right one
constexpr unsigned kHotkeyCount = 8;               // mouse, turbo, aspect, crop, reset, statusbar, virtual keyboard, Freeze
constexpr unsigned kHotkeyBit = 24;                // bits of Controls::down / InputMemory::held from here on are the hotkeys

// What the adapter keeps between two runs for the input. It is part of the saved timeline (state_frame.h), and is zero at load (the D-Pad mode starts from
// the Joystick/Mouse option).
struct InputMemory {
    bool caps_latched = false;         // the Amiga's Caps Lock is on: bit 0x62 of the packet
    bool caps_held = false;            // the host's Caps Lock key was down at the last run: a press is the edge from up to down
    int32_t mouse_rx = 0, mouse_ry = 0;    // movement that did not fit the 120 counts of a unit
    bool dpad_mouse = false;           // the pad's joystick functions drive the mouse (Toggle Joystick/Mouse)
    bool turbo_toggled = false;        // Toggle Turbo Fire: inverts the Turbo Fire option
    bool crop_toggled = false;         // Toggle Crop: inverts whether the picture is cropped
    bool aspect_toggled = false;       // Toggle Aspect Ratio: the other standard's pixel aspect
    bool statusbar_shown = false;      // the statusbar is up (Statusbar Startup, then the hotkey or the mapped button)
    uint8_t message = kMessageNone;    // the last action's message, and the units it has left to be shown
    uint8_t message_left = 0;
    bool vkbd_open = false;            // the virtual keyboard is up and owns the pad (vkbd.h)
    bool vkbd_opaque = false;          // its transparency is switched off (RetroPad A)
    uint8_t vk_cursor = 0;             // the key under the cursor (an index of kVkbd)
    uint8_t vk_held = kVkbdNoKey;      // the key code RetroPad B holds down, or kVkbdNoKey
    uint8_t vk_latch = 0;              // the modifiers latched (bits of kVkLatchCodes)
    uint8_t vk_repeat = 0;             // units a direction has been held, for the repeat
    uint8_t turbo_phase = 0;           // units the turbo button has been held, modulo kTurboCycle
    uint8_t reset_hold = 0;            // units left of the Ctrl-Amiga-Amiga of a soft reset
    uint32_t held = 0;                 // the inputs and hotkeys that were down at the last run (bit n of Controls::down)
    int16_t host_frac[2] = {0, 0};     // hundredths of a count the Mouse Speed left over, per axis
    int32_t mouse2_rx = 0, mouse2_ry = 0;
    int16_t host2_frac[2] = {0, 0};     // the second physical mouse has its own backlog and scaling fractions
    int32_t pointer_view_x = 0, pointer_view_y = 0;
    uint32_t pointer_view_w = 0, pointer_view_h = 0, pointer_raster_w = 0, pointer_raster_h = 0;
    int16_t stick_frac[2][2] = {{0, 0}, {0, 0}};      // thousandths of a count a stick left over: [left, right][x, y]

    bool operator==(const InputMemory &other) const
    {
        return pointer_view_x == other.pointer_view_x && pointer_view_y == other.pointer_view_y && pointer_view_w == other.pointer_view_w && pointer_view_h == other.pointer_view_h &&
               pointer_raster_w == other.pointer_raster_w && pointer_raster_h == other.pointer_raster_h &&
               mouse2_rx == other.mouse2_rx && mouse2_ry == other.mouse2_ry && host2_frac[0] == other.host2_frac[0] && host2_frac[1] == other.host2_frac[1] &&
               caps_latched == other.caps_latched && caps_held == other.caps_held && mouse_rx == other.mouse_rx && mouse_ry == other.mouse_ry &&
               dpad_mouse == other.dpad_mouse && turbo_toggled == other.turbo_toggled && crop_toggled == other.crop_toggled &&
               aspect_toggled == other.aspect_toggled && statusbar_shown == other.statusbar_shown && message == other.message &&
               message_left == other.message_left && vkbd_open == other.vkbd_open && vkbd_opaque == other.vkbd_opaque && vk_cursor == other.vk_cursor &&
               vk_held == other.vk_held && vk_latch == other.vk_latch && vk_repeat == other.vk_repeat && turbo_phase == other.turbo_phase && reset_hold == other.reset_hold && held == other.held &&
               host_frac[0] == other.host_frac[0] && host_frac[1] == other.host_frac[1] && stick_frac[0][0] == other.stick_frac[0][0] &&
               stick_frac[0][1] == other.stick_frac[0][1] && stick_frac[1][0] == other.stick_frac[1][0] && stick_frac[1][1] == other.stick_frac[1][1];
    }
};

// The options that shape the input, read from the settings at every run (they change at once, in the menu).
struct InputConfig {
    bool freeze = false;
    uint32_t mapper[kMapperInputs] = {};          // the schema's mapper_target number of each input (0 = default function)
    uint32_t hotkey_code[kHotkeyCount] = {};      // the RETROK_* code of each hotkey, 0 = none
    unsigned analog_mouse = 0;                    // bit 0 the left stick, bit 1 the right one
    int deadzone = 20;                            // percent of the stick's range
    int speed_tenths[2] = {10, 10};               // left, right: the stick mouse speed in tenths (10 = 100%)
    int dpad_speed = 6;                           // counts a unit of a digital mouse movement
    int mouse_speed = 100;                        // percent, of the frontend's mouse
    bool crop_on = false;                         // the Crop option is on: what Toggle Crop turns off (for its message)
    bool aspect_ntsc = false;                     // the aspect announced without the toggle is NTSC's: what Toggle Aspect Ratio swaps (for its message)
    bool turbo = false;
    unsigned turbo_input = 0;                     // the RETRO_DEVICE_ID_JOYPAD_* number of the turbo button
    int pulse = 6;
    unsigned face = 0;                            // 0 B/A, 1 B/Up, 2 Y/B, 3 Y/Up
};

inline InputConfig input_config(const p6_options::Settings &s)
{
    InputConfig c;
    const CartridgeChoice *const cartridge = cartridge_choice(s.cartridge);
    c.freeze = s.cartridge == P6_CONFIG_HRTMON || (cartridge && cartridge->freeze);
    const uint32_t mapper[kMapperInputs] = {s.mapper_b, s.mapper_y, s.mapper_select, s.mapper_start, s.mapper_up, s.mapper_down, s.mapper_left, s.mapper_right,
                                            s.mapper_a, s.mapper_x, s.mapper_l, s.mapper_r, s.mapper_l2, s.mapper_r2, s.mapper_l3, s.mapper_r3,
                                            s.mapper_lu, s.mapper_ld, s.mapper_ll, s.mapper_lr, s.mapper_ru, s.mapper_rd, s.mapper_rl, s.mapper_rr};
    for (unsigned i = 0; i < kMapperInputs; ++i) c.mapper[i] = mapper[i];
    c.hotkey_code[0] = s.hotkey_mouse_toggle;
    c.hotkey_code[1] = s.hotkey_turbo_toggle;
    c.hotkey_code[2] = s.hotkey_aspect_toggle;
    c.hotkey_code[3] = s.hotkey_crop_toggle;
    c.hotkey_code[4] = s.hotkey_reset;
    c.hotkey_code[5] = s.hotkey_statusbar;
    c.hotkey_code[6] = s.hotkey_vkbd;
    c.hotkey_code[7] = s.hotkey_freeze;
    c.crop_on = s.video_crop != 0;
    c.aspect_ntsc = s.video_aspect == kAspectNtsc || (s.video_aspect != kAspectPal && s.video_standard == P6_VIDEO_NTSC);
    c.analog_mouse = s.analog_mouse;
    c.deadzone = int(s.analog_mouse_deadzone);
    c.speed_tenths[0] = int(s.analog_mouse_speed_left);
    c.speed_tenths[1] = int(s.analog_mouse_speed_right);
    c.dpad_speed = int(s.dpad_mouse_speed);
    c.mouse_speed = int(s.mouse_speed);
    c.turbo = s.turbo_fire != 0;
    c.turbo_input = s.turbo_fire_button;
    c.pulse = int(s.turbo_pulse);
    c.face = s.face_buttons;
    return c;
}

// The action each hotkey runs, in the order of the options.
constexpr uint32_t kHotkeyTargets[kHotkeyCount] = {
    p6_options::kMapperTargetToggleMouse, p6_options::kMapperTargetToggleTurbo, p6_options::kMapperTargetToggleAspect,
    p6_options::kMapperTargetToggleCrop, p6_options::kMapperTargetReset, p6_options::kMapperTargetToggleStatusbar,
    p6_options::kMapperTargetToggleVkbd, p6_options::kMapperTargetFreeze,
};

// What the frontend reported this run, as far as the mapper cares: which inputs and hotkeys are down, and where the sticks are.
struct Controls {
    uint32_t down = 0;                // bit n: input n (0..23) is down; bit 24 + k: hotkey k is down
    int16_t stick[2][2] = {{0, 0}, {0, 0}};      // [left, right][x, y]
};

// What the mapper makes of the controls in one run.
struct Effects {
    uint32_t commands = 0;            // P6_COMMAND_* levels; the common bridge owns edges
    uint32_t joy = 0;                 // P6_BUTTON_* of the joystick port
    uint32_t mouse_buttons = 0;       // P6_BUTTON_1 left, 2 right, 3 middle
    int32_t move_x = 0, move_y = 0;   // counts the mapper wants the mouse to move this unit (the frontend's mouse is added by the caller)
    uint32_t keys[4] = {};
};

inline void press_key(uint32_t keys[4], unsigned code)
{
    if (code < 128) keys[code / 32] |= UINT32_C(1) << (code % 32);
}

inline bool is_key_target(uint32_t target) { return target >= kKeyTargetBase && target < kKeyTargetBase + 0x68; }

// What an input does when the mapper option leaves it at "Default function".
inline uint32_t default_target(unsigned input, const InputConfig &config, bool left_stick_mouse)
{
    using namespace p6_options;
    switch (input) {
    case RETRO_DEVICE_ID_JOYPAD_UP: return kMapperTargetJoyUp;
    case RETRO_DEVICE_ID_JOYPAD_DOWN: return kMapperTargetJoyDown;
    case RETRO_DEVICE_ID_JOYPAD_LEFT: return kMapperTargetJoyLeft;
    case RETRO_DEVICE_ID_JOYPAD_RIGHT: return kMapperTargetJoyRight;
    case RETRO_DEVICE_ID_JOYPAD_B: return config.face >= 2 ? (config.face == 2 ? kMapperTargetJoyFire2 : kMapperTargetJoyUp) : kMapperTargetJoyFire;
    case RETRO_DEVICE_ID_JOYPAD_A: return config.face == 1 ? kMapperTargetJoyUp : config.face == 0 ? kMapperTargetJoyFire2 : kMapperTargetNone;
    case RETRO_DEVICE_ID_JOYPAD_Y: return config.face >= 2 ? kMapperTargetJoyFire : kMapperTargetNone;
    case RETRO_DEVICE_ID_JOYPAD_X: return kMapperTargetJoyFire3;
    case kStickInput: return left_stick_mouse ? kMapperTargetNone : kMapperTargetJoyUp;
    case kStickInput + 1: return left_stick_mouse ? kMapperTargetNone : kMapperTargetJoyDown;
    case kStickInput + 2: return left_stick_mouse ? kMapperTargetNone : kMapperTargetJoyLeft;
    case kStickInput + 3: return left_stick_mouse ? kMapperTargetNone : kMapperTargetJoyRight;
    default: return kMapperTargetNone;
    }
}

// A stick that moves the mouse does so unless one of its four directions is mapped: the mapping wins ("no keymappings and mousing at the same time").
inline bool stick_moves_mouse(const InputConfig &config, unsigned stick)
{
    if (!(config.analog_mouse >> stick & 1)) return false;
    for (unsigned d = 0; d < 4; ++d)
        if (config.mapper[kStickInput + 4 * stick + d] != p6_options::kMapperTargetDefault) return false;
    return true;
}

inline uint32_t isqrt(uint64_t value)
{
    uint64_t root = 0, bit = UINT64_C(1) << 62;
    while (bit > value) bit >>= 2;
    while (bit) {
        if (value >= root + bit) {
            value -= root + bit;
            root = (root >> 1) + bit;
        } else {
            root >>= 1;
        }
        bit >>= 2;
    }
    return uint32_t(root);
}

// Thousandths of a count a stick moves one axis in a unit: zero inside the deadzone, then growing to kStickFullCounts * speed at the edge. The axis is scaled
// by how far the whole stick is past the deadzone (so a diagonal is as fast as a straight push), which is PUAE5's adjust_analog_deadzone.
inline int32_t stick_milli_counts(int axis, int x, int y, int deadzone_percent, int speed_tenths)
{
    const int64_t deadzone = int64_t(deadzone_percent) * kStickRange / 100;
    const int64_t magnitude = isqrt(uint64_t(int64_t(x) * x + int64_t(y) * y));
    if (magnitude <= deadzone || axis == 0) return 0;
    const int64_t past = std::min<int64_t>(magnitude, kStickRange) - deadzone;
    return int32_t(int64_t(axis) * past * kStickFullCounts * 100 * speed_tenths / ((kStickRange - deadzone) * magnitude));
}

// Adds `milli` thousandths to the carried fraction and returns the whole counts that makes; a stick at rest (milli 0) drops the carry.
inline int32_t take_whole(int16_t *carry, int32_t milli, int32_t unit)
{
    if (milli == 0) {
        *carry = 0;
        return 0;
    }
    const int32_t total = *carry + milli;
    const int32_t whole = total / unit;
    *carry = int16_t(total - whole * unit);
    return whole;
}

inline void apply_target(uint32_t target, bool mouse_mode, const InputConfig &config, Effects *out)
{
    using namespace p6_options;
    if (is_key_target(target)) {
        press_key(out->keys, target - kKeyTargetBase);
        return;
    }
    if (mouse_mode) {                         // the joystick functions of the pad are the mouse's
        switch (target) {
        case kMapperTargetJoyUp: target = kMapperTargetMouseMoveUp; break;
        case kMapperTargetJoyDown: target = kMapperTargetMouseMoveDown; break;
        case kMapperTargetJoyLeft: target = kMapperTargetMouseMoveLeft; break;
        case kMapperTargetJoyRight: target = kMapperTargetMouseMoveRight; break;
        case kMapperTargetJoyFire: target = kMapperTargetMouseLeft; break;
        case kMapperTargetJoyFire2: target = kMapperTargetMouseRight; break;
        case kMapperTargetJoyFire3: target = kMapperTargetMouseMiddle; break;
        default: break;
        }
    }
    switch (target) {
    case kMapperTargetFreeze: if (config.freeze) out->commands |= P6_COMMAND_FREEZE; break;
    case kMapperTargetJoyUp: out->joy |= P6_BUTTON_UP; break;
    case kMapperTargetJoyDown: out->joy |= P6_BUTTON_DOWN; break;
    case kMapperTargetJoyLeft: out->joy |= P6_BUTTON_LEFT; break;
    case kMapperTargetJoyRight: out->joy |= P6_BUTTON_RIGHT; break;
    case kMapperTargetJoyFire: out->joy |= P6_BUTTON_1; break;
    case kMapperTargetJoyFire2: out->joy |= P6_BUTTON_2; break;
    case kMapperTargetJoyFire3: out->joy |= P6_BUTTON_3; break;
    case kMapperTargetMouseLeft: out->mouse_buttons |= P6_BUTTON_1; break;
    case kMapperTargetMouseRight: out->mouse_buttons |= P6_BUTTON_2; break;
    case kMapperTargetMouseMiddle: out->mouse_buttons |= P6_BUTTON_3; break;
    case kMapperTargetMouseMoveUp: out->move_y -= config.dpad_speed; break;
    case kMapperTargetMouseMoveDown: out->move_y += config.dpad_speed; break;
    case kMapperTargetMouseMoveLeft: out->move_x -= config.dpad_speed; break;
    case kMapperTargetMouseMoveRight: out->move_x += config.dpad_speed; break;
    default: break;
    }
}

// The virtual keyboard's run (vkbd.h): while it is open the pad's D-Pad, B, A, Y and X belong to it. `edges` are the inputs that went down in this run.
inline void vkbd_step(const Controls &controls, uint32_t edges, InputMemory *memory, Effects *out)
{
    auto down = [&](unsigned id) { return (controls.down >> id & 1) != 0; };
    auto edge = [&](unsigned id) { return (edges >> id & 1) != 0; };
    const unsigned dirs[4] = {RETRO_DEVICE_ID_JOYPAD_UP, RETRO_DEVICE_ID_JOYPAD_DOWN, RETRO_DEVICE_ID_JOYPAD_LEFT, RETRO_DEVICE_ID_JOYPAD_RIGHT};
    int held = -1, pressed = -1;
    for (unsigned d : dirs) {
        if (held < 0 && down(d)) held = int(d);
        if (pressed < 0 && edge(d)) pressed = int(d);
    }
    if (held < 0) {
        memory->vk_repeat = 0;
    } else if (pressed >= 0) {                                    // a new press moves at once and starts the repeat count
        memory->vk_cursor = uint8_t(vk_move(memory->vk_cursor, unsigned(pressed)));
        memory->vk_repeat = 1;
    } else {
        if (memory->vk_repeat < 255) ++memory->vk_repeat;
        if (memory->vk_repeat > kVkRepeatDelay && (memory->vk_repeat - kVkRepeatDelay) % kVkRepeatEvery == 0)
            memory->vk_cursor = uint8_t(vk_move(memory->vk_cursor, unsigned(held)));
    }
    if (!down(RETRO_DEVICE_ID_JOYPAD_B)) memory->vk_held = kVkbdNoKey;
    if (edge(RETRO_DEVICE_ID_JOYPAD_B)) {
        const uint8_t code = kVkbd[memory->vk_cursor].code;
        const int latch = vk_latch_bit(code);
        if (code == kCapsLockCode) memory->caps_latched = !memory->caps_latched;
        else if (latch >= 0) memory->vk_latch ^= uint8_t(1u << latch);
        else memory->vk_held = code;
    }
    if (edge(RETRO_DEVICE_ID_JOYPAD_Y)) memory->caps_latched = !memory->caps_latched;
    if (edge(RETRO_DEVICE_ID_JOYPAD_A)) memory->vkbd_opaque = !memory->vkbd_opaque;
    if (memory->vk_held != kVkbdNoKey) press_key(out->keys, memory->vk_held);
    for (unsigned i = 0; i < kVkLatchCount; ++i)
        if (memory->vk_latch >> i & 1) press_key(out->keys, kVkLatchCodes[i]);
    if (down(RETRO_DEVICE_ID_JOYPAD_X)) press_key(out->keys, 0x40);
}

// One run of the mapper. `mouse_port` is whether the pad may use the mouse port (the frontend's player 1 is a pad with the mouse). Edges and the actions
// they run first, then the functions of the inputs that are down.
inline void mapper_step(const Controls &controls, const InputConfig &config, bool mouse_port, InputMemory *memory, Effects *out)
{
    using namespace p6_options;
    const bool left_mouse = mouse_port && stick_moves_mouse(config, 0), right_mouse = mouse_port && stick_moves_mouse(config, 1);       // without the mouse port a stick stays a stick
    auto target_of = [&](unsigned input) {
        return config.mapper[input] != kMapperTargetDefault ? config.mapper[input] : default_target(input, config, left_mouse);
    };
    const uint32_t edges = controls.down & ~memory->held;
    memory->held = controls.down;
    const bool was_open = memory->vkbd_open;
    auto say = [&](uint8_t message) {
        memory->message = message;
        memory->message_left = uint8_t(kMessageUnits);
    };
    auto run_action = [&](uint32_t target) {
        switch (target) {
        case kMapperTargetToggleMouse:
            memory->dpad_mouse = !memory->dpad_mouse;
            say(memory->dpad_mouse ? kMessageMouse : kMessageJoystick);
            break;
        case kMapperTargetToggleTurbo:
            memory->turbo_toggled = !memory->turbo_toggled;
            say(config.turbo != memory->turbo_toggled ? kMessageTurboOn : kMessageTurboOff);
            break;
        case kMapperTargetToggleAspect:
            memory->aspect_toggled = !memory->aspect_toggled;
            say(!memory->aspect_toggled ? kMessageAspectNormal : config.aspect_ntsc ? kMessageAspectPal : kMessageAspectNtsc);
            break;
        case kMapperTargetToggleCrop:
            memory->crop_toggled = !memory->crop_toggled;
            say(config.crop_on != memory->crop_toggled ? kMessageCropOn : kMessageCropOff);
            break;
        case kMapperTargetToggleStatusbar: memory->statusbar_shown = !memory->statusbar_shown; break;
        case kMapperTargetToggleVkbd:
            memory->vkbd_open = !memory->vkbd_open;
            if (!memory->vkbd_open) {                                  // closing lets go of everything the keyboard held; the cursor stays where it was
                memory->vk_held = kVkbdNoKey;
                memory->vk_latch = 0;
                memory->vk_repeat = 0;
            }
            break;
        case kMapperTargetReset:
            if (!memory->reset_hold) {
                memory->reset_hold = uint8_t(kResetHoldUnits);
                say(kMessageReset);
            }
            break;
        default: break;
        }
    };
    for (unsigned input = 0; input < kMapperInputs; ++input)
        if (edges >> input & 1) run_action(target_of(input));
    for (unsigned k = 0; k < kHotkeyCount; ++k) {
        if (edges >> (kHotkeyBit + k) & 1) run_action(kHotkeyTargets[k]);
        if (config.freeze && kHotkeyTargets[k] == kMapperTargetFreeze && (controls.down >> (kHotkeyBit + k) & 1)) out->commands |= P6_COMMAND_FREEZE;
    }
    const bool mouse_mode = memory->dpad_mouse && mouse_port;
    const bool turbo_on = config.turbo != memory->turbo_toggled;
    const unsigned pulse = unsigned(std::max(2, config.pulse));
    const bool turbo_held = turbo_on && (controls.down >> config.turbo_input & 1);
    if (memory->vkbd_open != was_open) {
        memory->turbo_phase = 0;                                       // the run that opens or closes the keyboard is the pad's to neither: what went down in it is not a press
    } else if (memory->vkbd_open) {
        vkbd_step(controls, edges, memory, out);                       // the pad belongs to the keyboard: it gives the joystick and the mapper nothing
        memory->turbo_phase = 0;
    } else {
        for (unsigned input = 0; input < kMapperInputs; ++input) {
            bool down = controls.down >> input & 1;
            if (down && turbo_held && input == config.turbo_input && target_of(input) != kMapperTargetFreeze) down = (memory->turbo_phase % pulse) < pulse / 2;
            if (down) apply_target(target_of(input), mouse_mode, config, out);
        }
        memory->turbo_phase = turbo_held ? uint8_t((memory->turbo_phase + 1) % kTurboCycle) : 0;
    }
    for (unsigned s = 0; s < 2; ++s) {
        const bool moves = s == 0 ? left_mouse : right_mouse;
        const int x = controls.stick[s][0], y = controls.stick[s][1];
        const int32_t mx = moves ? stick_milli_counts(x, x, y, config.deadzone, config.speed_tenths[s]) : 0;
        const int32_t my = moves ? stick_milli_counts(y, x, y, config.deadzone, config.speed_tenths[s]) : 0;
        out->move_x += take_whole(&memory->stick_frac[s][0], mx, 1000);
        out->move_y += take_whole(&memory->stick_frac[s][1], my, 1000);
    }
    if (memory->message_left && --memory->message_left == 0) memory->message = kMessageNone;
    if (memory->reset_hold) {
        press_key(out->keys, kKeyCtrl);
        press_key(out->keys, kKeyLeftAmiga);
        press_key(out->keys, kKeyRightAmiga);
        --memory->reset_hold;
    }
}

}  // namespace p6_libretro

#endif
