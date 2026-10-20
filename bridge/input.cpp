// Keyboard, mouse and joystick of the host, pushed into the engine once per video unit.
//
// The engine keeps input as level state (key table, button masks, direction) plus three queues: the keyboard ring
// that feeds the CIA serial line, additive mouse deltas and the JOYxDAT counters. The bridge keeps what it already
// applied and sends only the difference, through the engine's own setters and never through registers.
//   keys    the 128-key bitmap; key n is Amiga raw code n; bit 0x62 (Caps Lock) is the latch
//   either port: mouse (buttons, relative dx/dy), joystick or CD32 pad
#include "input.h"
#include "config.h"
#include "error_util.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "inputdevice.h"
#include "keybuf.h"

#include <string.h>

// Set by the port when the engine has cleared its own key state at the end of a keyboard reset sequence
// (ports/portable/input_devices.cpp, release_keys()).
extern int p6_input_engine_released_keys;
void p6_input_reset_keys();     // patch 0066
void p6_input_cd32(int port, bool enabled, unsigned buttons, unsigned directions); // patch 0350, boundary only
void p6_input_mouse2(bool enabled, unsigned buttons, int dx, int dy); // patch 0351, boundary only
void p6_input_pointer(int port, bool enabled, unsigned buttons, int x, int y); // patch 0352, boundary only

void p6_input_parallel(bool enabled, const uint32_t buttons[2], const uint32_t directions[2]); // patch0353

namespace p6_bridge {

namespace {

constexpr uint32_t kMouseButtons = P6_BUTTON_1 | P6_BUTTON_2 | P6_BUTTON_3;
constexpr uint32_t kJoystickBits = 0x7f;                       // three buttons and four directions
constexpr uint32_t kDirections = P6_BUTTON_UP | P6_BUTTON_DOWN | P6_BUTTON_LEFT | P6_BUTTON_RIGHT;
constexpr uint32_t kCd32Buttons = P6_CD32_RED | P6_CD32_BLUE | P6_CD32_GREEN | P6_CD32_YELLOW | P6_CD32_REWIND | P6_CD32_FORWARD | P6_CD32_PLAY;
constexpr int kKeyLimit = 0x68;                                // 0x68..0x7f: reserved, and 0x78 injects a reset warning
constexpr int kEdgeCost = 2;                                   // ring places one edge may use, with margin

struct Applied {
    uint32_t commands;
    uint32_t keys[4];
    uint32_t type[2];
    uint32_t buttons[2];
    uint32_t parallel_type[2], parallel_buttons[2];
    int axis[2][2];                                            // joystick directions -1, 0, +1 (x, y) per port
};
Applied applied;

bool key_down(const uint32_t *keys, int n)
{
    return (keys[n / 32] >> (n % 32)) & 1;
}

// Neutral when both opposite directions are asked for: the axis of the engine cannot express both.
int axis_of(uint32_t buttons, uint32_t negative, uint32_t positive)
{
    return int(bool(buttons & positive)) - int(bool(buttons & negative));
}

// Sends one key edge if it fits the keyboard ring; false leaves it for the next unit.
bool send_key(int code, bool down)
{
    if (keybuf_free() < kEdgeCost) return false;
    inputdevice_do_keyboard(code, down ? 1 : 0);
    return true;
}

void release_port(int port)
{
    Applied &a = applied;
    if (a.type[port] == P6_PORT_MOUSE) {
        if (port == 1) p6_input_mouse2(false, 0, 0, 0);
        else for (int b = 0; b < 3; ++b)
            if ((a.buttons[port] >> b) & 1) setmousebuttonstate(0, b, 0);
    } else if (a.type[port] == P6_PORT_CD32) {
        p6_input_cd32(port, false, 0, 0);
    } else if (a.type[port] == P6_PORT_LIGHTGUN || a.type[port] == P6_PORT_LIGHTPEN) {
        p6_input_pointer(port, false, 0, 0, 0);
    } else if (a.type[port] == P6_PORT_JOYSTICK) {
        for (int b = 0; b < 3; ++b)
            if ((a.buttons[port] >> b) & 1) setjoybuttonstate(port, b, 0);
        for (int axis = 0; axis < 2; ++axis)
            if (a.axis[port][axis]) setjoystickstate(port, axis, 0, 1);
    }
    a.type[port] = P6_PORT_NONE;
    a.buttons[port] = 0;
    a.axis[port][0] = a.axis[port][1] = 0;
}

void apply_port(int port, const p6_port_input &in)
{
    Applied &a = applied;
    if (a.type[port] != in.type) release_port(port);
    a.type[port] = in.type;
    if (in.type == P6_PORT_MOUSE) {
        if (port == 1) p6_input_mouse2(true, in.buttons, in.dx, in.dy);
        else {
            for (int b = 0; b < 3; ++b) {
                const uint32_t now = (in.buttons >> b) & 1;
                if (now != ((a.buttons[port] >> b) & 1)) setmousebuttonstate(0, b, int(now));
            }
            // Relative motion is added by the engine; the packet carries this unit's motion only.
            if (in.dx) setmousestate(0, 0, in.dx, 0);
            if (in.dy) setmousestate(0, 1, in.dy, 0);
        }
        a.buttons[port] = in.buttons & kMouseButtons;
    } else if (in.type == P6_PORT_LIGHTGUN || in.type == P6_PORT_LIGHTPEN) {
        p6_input_pointer(port, true, in.buttons, in.dx, in.dy);
        a.buttons[port] = in.buttons;
    } else if (in.type == P6_PORT_CD32) {
        const int x = axis_of(in.buttons, P6_BUTTON_LEFT, P6_BUTTON_RIGHT);
        const int y = axis_of(in.buttons, P6_BUTTON_UP, P6_BUTTON_DOWN);
        const unsigned dir = (x < 0 ? DIR_LEFT : x > 0 ? DIR_RIGHT : 0) | (y < 0 ? DIR_UP : y > 0 ? DIR_DOWN : 0);
        p6_input_cd32(port, true, cd32_buttons(in.buttons), dir);
        a.buttons[port] = in.buttons & kCd32Buttons;
        a.axis[port][0] = x;
        a.axis[port][1] = y;
    } else if (in.type == P6_PORT_JOYSTICK) {
        const int x = axis_of(in.buttons, P6_BUTTON_LEFT, P6_BUTTON_RIGHT);
        const int y = axis_of(in.buttons, P6_BUTTON_UP, P6_BUTTON_DOWN);
        if (x != a.axis[port][0]) setjoystickstate(port, 0, x, 1);
        if (y != a.axis[port][1]) setjoystickstate(port, 1, y, 1);
        a.axis[port][0] = x;
        a.axis[port][1] = y;
        for (int b = 0; b < 3; ++b) {
            const uint32_t now = (in.buttons >> b) & 1;
            if (now != ((a.buttons[port] >> b) & 1)) setjoybuttonstate(port, b, int(now));
        }
        a.buttons[port] = in.buttons & 7;
    }
}

}  // namespace

uint32_t cd32_buttons(uint32_t buttons)
{
    const uint32_t bits[] = {P6_CD32_PLAY, P6_CD32_REWIND, P6_CD32_FORWARD, P6_CD32_GREEN, P6_CD32_YELLOW, P6_CD32_RED, P6_CD32_BLUE};
    uint32_t raw = 0;
    for (unsigned i = 0; i < 7; ++i) if (buttons & bits[i]) raw |= 1u << (JOYBUTTON_CD32_PLAY + i);
    return raw;
}

p6_result validate_input(const p6_input &in, p6_error *error)
{
    if (!((in.abi_version == P6_ABI_VERSION && in.struct_size == sizeof in) ||
          (in.abi_version == P6_INPUT_VERSION_2 && in.struct_size == sizeof(p6_input_v2))))
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "input has the wrong ABI version or size");
    for (uint32_t r : in.reserved)
        if (r) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "input reserved fields must be zero");
    for (int n = kKeyLimit; n < 128; ++n)
        if (key_down(in.keys, n)) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "key codes 0x68..0x7f are reserved");
    unsigned pointers = 0;
    for (int p = 0; p < 2; ++p) {
        const p6_port_input &port = in.ports[p];
        if (port.reserved) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "port reserved field must be zero");
        switch (port.type) {
        case P6_PORT_NONE:
            if (port.buttons || port.dx || port.dy)
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "a port without a device carries no buttons or motion");
            break;
        case P6_PORT_JOYSTICK:
            if (port.buttons & ~kJoystickBits) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown joystick buttons");
            if (port.dx || port.dy) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "a joystick has no relative motion");
            break;
        case P6_PORT_CD32:
            if (port.buttons & ~(kCd32Buttons | kDirections)) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown CD32 buttons");
            if (port.dx || port.dy) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "a CD32 pad has no relative motion");
            break;
        case P6_PORT_MOUSE:
            if (port.buttons & ~kMouseButtons) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown mouse buttons");
            break;
        case P6_PORT_LIGHTGUN:
        case P6_PORT_LIGHTPEN:
            if (port.buttons & ~(P6_POINTER_TRIGGER | P6_POINTER_OFFSCREEN))
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown optical pointer buttons");
            if (port.dx == INT16_MIN || port.dy == INT16_MIN)
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "optical pointer coordinates are -32767..32767");
            if (++pointers > 1) return p6_fail(error, P6_ERROR_UNSUPPORTED, "only one optical sensor is supported");
            break;
        default:
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown port device");
        }
    }
    if (in.abi_version == P6_INPUT_VERSION_2) {
        const auto &extended = *reinterpret_cast<const p6_input_v2 *>(&in);
        if (extended.commands & ~P6_COMMAND_FREEZE) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown input commands");
        if (extended.commands && !hrtmon_bound()) return p6_fail(error, P6_ERROR_UNSUPPORTED, "Freeze requires HRTMon");
        for (const auto &port : extended.parallel) {
            if (port.type > P6_PORT_JOYSTICK || port.reserved || port.dx || port.dy ||
                (port.buttons & ~(P6_BUTTON_1 | kDirections)) || (port.type == P6_PORT_NONE && port.buttons))
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "parallel ports accept NONE or a joystick with D-pad and Fire1 only");
        }
    }
    return P6_OK;
}

uint32_t parallel_directions(uint32_t buttons)
{
    const int x = axis_of(buttons, P6_BUTTON_LEFT, P6_BUTTON_RIGHT), y = axis_of(buttons, P6_BUTTON_UP, P6_BUTTON_DOWN);
    return (x < 0 ? 1u : x > 0 ? 2u : 0u) | (y < 0 ? 4u : y > 0 ? 8u : 0u);
}

unsigned apply_input(const p6_input &in)
{
    if (p6_input_engine_released_keys) {       // the engine cleared its key table: whatever is held goes down again
        p6_input_engine_released_keys = 0;
        memset(applied.keys, 0, sizeof applied.keys);
    }
    unsigned deferred = 0;
    // Releases first: the release that ends Ctrl-Amiga-Amiga resets before new presses could be swallowed.
    for (int n = 0; n < kKeyLimit; ++n)
        if (key_down(applied.keys, n) && !key_down(in.keys, n)) {
            if (send_key(n, false)) applied.keys[n / 32] &= ~(1u << (n % 32));
            else ++deferred;
        }
    for (int n = 0; n < kKeyLimit; ++n)
        if (!key_down(applied.keys, n) && key_down(in.keys, n)) {
            if (send_key(n, true)) applied.keys[n / 32] |= 1u << (n % 32);
            else ++deferred;
        }
    apply_port(0, in.ports[0]);
    apply_port(1, in.ports[1]);
    uint32_t buttons[2] = {}, directions[2] = {};
    bool parallel = false;
    for (unsigned p = 0; p < 2; ++p) {
        p6_port_input port = {};
        if (in.abi_version == P6_INPUT_VERSION_2) port = reinterpret_cast<const p6_input_v2 *>(&in)->parallel[p];
        applied.parallel_type[p] = port.type; applied.parallel_buttons[p] = port.buttons;
        parallel |= port.type == P6_PORT_JOYSTICK;
        buttons[p] = port.buttons & P6_BUTTON_1; directions[p] = parallel_directions(port.buttons);
    }
    p6_input_parallel(parallel, buttons, directions);
    const uint32_t commands = in.abi_version == P6_INPUT_VERSION_2 ? reinterpret_cast<const p6_input_v2 *>(&in)->commands : 0;
    if ((commands & ~applied.commands) & P6_COMMAND_FREEZE) request_hrtmon_freeze();
    applied.commands = commands; // Consume even during monitor/first boot; a held level is never a pending action.
    if (p6_input_engine_released_keys) {       // a chord ended inside this packet
        p6_input_engine_released_keys = 0;
        memset(applied.keys, 0, sizeof applied.keys);
    }
    return deferred;
}

void begin_input_unit()
{
    p6_input_poll_mask = 0;
}

bool guest_polled_input()
{
    return p6_input_poll_mask != 0;     // the register bits, including the attached parallel adapter
}

void reset_keyboard()
{
    p6_input_reset_keys();     // clears the engine's table and flags the loss, so that apply_input presses the held keys again
}

void forget_input()
{
    memset(&applied, 0, sizeof applied);
}

void applied_get(p6_applied_raw *out)
{
    out->commands = applied.commands;
    memcpy(out->keys, applied.keys, sizeof out->keys);
    memcpy(out->parallel_type, applied.parallel_type, sizeof out->parallel_type);
    memcpy(out->parallel_buttons, applied.parallel_buttons, sizeof out->parallel_buttons);
    for (int p = 0; p < 2; ++p) {
        out->type[p] = applied.type[p];
        out->buttons[p] = applied.buttons[p];
        out->axis[p][0] = applied.axis[p][0];
        out->axis[p][1] = applied.axis[p][1];
    }
}

void applied_set(const p6_applied_raw &in)
{
    applied.commands = in.commands;
    memcpy(applied.keys, in.keys, sizeof applied.keys);
    memcpy(applied.parallel_type, in.parallel_type, sizeof applied.parallel_type);
    memcpy(applied.parallel_buttons, in.parallel_buttons, sizeof applied.parallel_buttons);
    for (int p = 0; p < 2; ++p) {
        applied.type[p] = in.type[p];
        applied.buttons[p] = in.buttons[p];
        applied.axis[p][0] = in.axis[p][0];
        applied.axis[p][1] = in.axis[p][1];
    }
}

}  // namespace p6_bridge
