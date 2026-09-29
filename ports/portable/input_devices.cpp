/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Logical host input devices for the PUAE6 A500 profile.
 *
 * WinUAE reaches the host through od-win32/dinput.cpp (DirectInput/RawInput
 * device enumeration, polling, acquire/unacquire) and od-win32/keyboard_win32.cpp
 * (scancode translation tables). None of that exists here. The bridge owns the
 * host state (p6_input: a 128-key bitmap and two ports) and pushes it into the
 * engine once per video unit through the engine's own setters
 * (inputdevice_do_keyboard, setjoystickstate, setjoybuttonstate, setmousestate,
 * setmousebuttonstate). This file only describes the devices those setters act
 * on, so that inputdevice.cpp can build its device tables, default mappings and
 * port assignment exactly as it does for real hardware.
 *
 * The provided devices are fixed and independent of the host:
 *   1 keyboard  : 128 key widgets, widget n is Amiga raw scancode n
 *   1 mouse     : X and Y axes (relative), 3 buttons
 *   2 joysticks : X and Y axes (digital), 3 buttons; joystick n is meant for
 *                 Amiga port n (0 = mouse port, 1 = joystick port)
 *
 * Properties that follow from "the state is pushed":
 *   - read() has nothing to poll. Input is applied by the bridge at the frame
 *     boundary, never from inside the emulation.
 *   - acquire()/unacquire() have nothing to acquire: there is no OS handle, no
 *     focus and no exclusive access to obtain or lose. They succeed for every
 *     device that exists because a logical device is always available, and they
 *     fail for indices that do not exist, like the DirectInput versions.
 *   - nothing here changes at run time. The tables are built at compile time
 *     (the key tables are constant-initialized; they are not const only because
 *     the engine takes non-const pointers and never writes through them), so
 *     there is no state to put in a snapshot and nothing depends on the host
 *     (no clock, no locale, no environment, no allocation).
 *
 * Text is ASCII. Unique names contain no space and no '{': matchdevices() in
 * inputdevice.cpp cuts names at the first of those before comparing (a rule for
 * DirectInput GUID suffixes). Names without either always take its exact-match
 * path; "P6 JOYSTICK1" and "P6 JOYSTICK2" would compare equal after the cut and
 * only be told apart by the friendly-name and full-string fallbacks.
 */

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "inputdevice.h"
#include "keyboard.h"

namespace {

/* ------------------------------------------------------------------ */
/* Device tables                                                       */
/* ------------------------------------------------------------------ */

struct Pointer {
    const TCHAR *friendly;
    const TCHAR *unique;
};

/* One mouse: a second one would let both bridge ports be mice at once.
 * inputdevice_validate_jports() refuses the same device in two ports. */
constexpr Pointer kMice[] = {
    { _T("P6 mouse"), _T("P6MOUSE1") },
};
constexpr Pointer kJoysticks[] = {
    { _T("P6 joystick 1"), _T("P6JOYSTICK1") },
    { _T("P6 joystick 2"), _T("P6JOYSTICK2") },
};
constexpr int kMouseCount = int(sizeof kMice / sizeof kMice[0]);
constexpr int kJoystickCount = int(sizeof kJoysticks / sizeof kJoysticks[0]);
constexpr int kKeyboardCount = 1;

/* Mice and joysticks share one widget layout, the same one dinput.cpp uses:
 * axes first, then buttons. The bridge contract has no wheel and no fourth
 * button, so none is offered; the default mappings below only map what exists. */
constexpr const TCHAR *kAxisNames[] = { _T("X-axis"), _T("Y-axis") };
constexpr const TCHAR *kButtonNames[] = { _T("Button 1"), _T("Button 2"), _T("Button 3") };
constexpr int kAxes = int(sizeof kAxisNames / sizeof kAxisNames[0]);
constexpr int kButtons = int(sizeof kButtonNames / sizeof kButtonNames[0]);

static_assert(kMouseCount <= MAX_INPUT_DEVICES && kJoystickCount <= MAX_INPUT_DEVICES,
              "device tables must fit the engine's per-type arrays");
static_assert(kAxes <= ID_AXIS_TOTAL && kButtons <= ID_BUTTON_TOTAL,
              "widgets must fit the engine's axis and button slots");

/* Keyboard widgets are the 128 Amiga raw scancodes 0x00..0x7f in order. */
constexpr int kKeyCodes = 128;
static_assert(kKeyCodes <= MAX_INPUT_DEVICE_EVENTS, "keys must fit the engine's widget slots");

/* ------------------------------------------------------------------ */
/* Key table, derived at compile time from inputevents.def             */
/* ------------------------------------------------------------------ */

/* The DEFEVENTKB rows of the engine's own event table: KEY_* events whose
 * "data" is the Amiga raw scancode. Using the .def keeps this file in step with
 * inputdevice.cpp's events[] without duplicating it or looking events up at run
 * time. (Filtering events[] by the "KEY_" prefix alone would also match
 * KEY_START, an AM_INFO heading whose data is 0.) */
struct KeyEvent {
    int event;
    int code;
    const TCHAR *name;
};

#define DEFEVENT(A, B, C, D, E, F)
#define DEFEVENT2(A, B, B2, C, D, E, F, G)
#define DEFEVENTKB(A, B, C, F, PC) { INPUTEVENT_ ## A, F, B },
constexpr KeyEvent kKeyEvents[] = {
#include "inputevents.def"
};
#undef DEFEVENT
#undef DEFEVENT2
#undef DEFEVENTKB
constexpr int kKeyEventCount = int(sizeof kKeyEvents / sizeof kKeyEvents[0]);

constexpr int cstrlen(const TCHAR *s)
{
    int n = 0;
    while (s[n])
        ++n;
    return n;
}

/* Widget names are copied into caller buffers with no length argument, as in
 * the DirectInput code. Keep them well below the smallest buffer callers use. */
constexpr int kMaxLabel = 32;

struct KeyMap {
    int entry[kKeyCodes]; /* index into kKeyEvents, or -1 */
    bool in_range;        /* every row's code fits 0..kKeyCodes-1 */
    bool names_fit;       /* every name fits kMaxLabel */
    bool complete;        /* every code has a row */
};

/* Two rows share code 0x2b (KEY_NUMBERSIGN, KEY_2B: the Amiga and the PC
 * spelling of the same key). The first row wins, which is KEY_NUMBERSIGN, the
 * one the od-win32 Amiga table uses; both send raw code 0x2b to the CIA. */
constexpr KeyMap build_key_map()
{
    KeyMap m{};
    for (int c = 0; c < kKeyCodes; ++c)
        m.entry[c] = -1;
    m.in_range = true;
    m.names_fit = true;
    for (int i = 0; i < kKeyEventCount; ++i) {
        const int c = kKeyEvents[i].code;
        if (cstrlen(kKeyEvents[i].name) >= kMaxLabel)
            m.names_fit = false;
        if (c < 0 || c >= kKeyCodes) {
            m.in_range = false;
            continue;
        }
        if (m.entry[c] < 0)
            m.entry[c] = i;
    }
    m.complete = true;
    for (int c = 0; c < kKeyCodes; ++c) {
        if (m.entry[c] < 0)
            m.complete = false;
    }
    return m;
}
constexpr KeyMap kKeyMap = build_key_map();

/* A code without a KEY event would silently lose its widget name and its
 * default event. Stop the build instead: the table needs a review. */
static_assert(kKeyMap.in_range, "DEFEVENTKB row outside the 128 Amiga raw codes");
static_assert(kKeyMap.names_fit, "DEFEVENTKB name longer than the widget label limit");
static_assert(kKeyMap.complete, "an Amiga raw code has no KEY_* event");
static_assert(kKeyEvents[kKeyMap.entry[AK_ESC]].event == INPUTEVENT_KEY_ESC,
              "raw code table out of step with keyboard.h");
static_assert(kKeyEvents[kKeyMap.entry[AK_CAPSLOCK]].event == INPUTEVENT_KEY_CAPS_LOCK,
              "raw code table out of step with keyboard.h");

/* ------------------------------------------------------------------ */
/* Default keyboard translation (inputdevice_setkeytranslation)        */
/* ------------------------------------------------------------------ */

/* scancode -> event, terminated by scancode -1, as set_kbr_default_event()
 * expects. The scancodes are what get_widget_type() reports in *code, i.e. the
 * Amiga raw codes, so the mapping is the identity onto KEY_* events. Only slot 0
 * is used and no flags are set: in particular Caps Lock has no ID_FLAG_TOGGLE
 * (od-win32 sets it to turn the momentary host key into a latch). Toggling would
 * keep the latch in prefs storage, outside any snapshot and outside the input
 * stream. Here bit 0x62 of the bridge bitmap is the latch itself, exactly what
 * a real A500 Caps Lock key reports (down when locked, up when unlocked). */
struct KbrTable {
    uae_input_device_kbr_default entry[kKeyCodes + 1];
};

constexpr KbrTable build_kbr_table()
{
    KbrTable t{};
    int n = 0;
    for (int c = 0; c < kKeyCodes; ++c) {
        if (kKeyMap.entry[c] < 0)
            continue;
        t.entry[n].scancode = c;
        t.entry[n].node[0].evt = kKeyEvents[kKeyMap.entry[c]].event;
        t.entry[n].node[0].flags = 0;
        ++n;
    }
    t.entry[n].scancode = -1;
    return t;
}

/* Constant-initialized. Not const only because the engine takes a non-const
 * pointer; inputdevice.cpp never writes through it. */
KbrTable g_kbr_table = build_kbr_table();

/* input_keyboard_type selects the table: KBTYPE_AMIGA, KBTYPE_PC1, KBTYPE_PC2.
 * A logical keyboard has no PC layout to translate, so all three are the same. */
uae_input_device_kbr_default *g_keytrans[] = {
    g_kbr_table.entry,
    g_kbr_table.entry,
    g_kbr_table.entry,
};
static_assert(sizeof g_keytrans / sizeof g_keytrans[0] == KBTYPE_PC2 + 1,
              "one translation table per input_keyboard_type");

/* Keyboard-as-joystick layouts (inputdevice.cpp KBR_DEFAULT_MAP_*): groups of
 * scancodes separated by -1, one group per joystick input, whole list ended by
 * a second -1. These are od-win32/keyboard_win32.cpp kb_* with each DIK_* code
 * replaced by the Amiga raw code of the same key. The bridge pushes joystick
 * state directly and does not use these layouts; they must exist because
 * compatibility_copy() reads keyboard_default_kbmaps[0..8] unconditionally. */
int kb_np[] = { AK_NP4, -1, AK_NP6, -1, AK_NP8, -1, AK_NP2, -1, AK_NP0, AK_NP5, -1, AK_NPDEL, -1, AK_ENT, -1, -1 };
int kb_ck[] = { AK_LF, -1, AK_RT, -1, AK_UP, -1, AK_DN, -1, AK_CTRL, AK_RALT, -1, AK_RSH, -1, -1 };
int kb_se[] = { AK_A, -1, AK_D, -1, AK_W, -1, AK_S, -1, AK_LALT, -1, AK_LSH, -1, -1 };
int kb_np3[] = { AK_NP4, -1, AK_NP6, -1, AK_NP8, -1, AK_NP2, -1, AK_NP0, AK_NP5, -1, AK_NPDEL, -1, AK_ENT, -1, -1 };
int kb_ck3[] = { AK_LF, -1, AK_RT, -1, AK_UP, -1, AK_DN, -1, AK_CTRL, -1, AK_RSH, -1, AK_RALT, -1, -1 };
int kb_se3[] = { AK_A, -1, AK_D, -1, AK_W, -1, AK_S, -1, AK_LALT, -1, AK_LSH, -1, AK_CTRL, -1, -1 };
int kb_cd32_np[] = { AK_NP4, -1, AK_NP6, -1, AK_NP8, -1, AK_NP2, -1, AK_NP0, AK_NP5, AK_NP1, -1, AK_NPDEL, AK_NP3, -1, AK_NP7, -1, AK_NP9, -1, AK_NPDIV, -1, AK_NPSUB, -1, AK_NPMUL, -1, -1 };
int kb_cd32_ck[] = { AK_LF, -1, AK_RT, -1, AK_UP, -1, AK_DN, -1, AK_CTRL, AK_RALT, AK_RSH, -1, AK_NP7, -1, AK_NP9, -1, AK_NPDIV, -1, AK_NPSUB, -1, AK_NPMUL, -1, -1 };
/* Same shape as upstream, including the doubled -1 after AK_S that ends the
 * list before the LALT/LSH groups. Mirrored, not fixed: CD32 is not part of
 * this profile and a fix would be an upstream change. */
int kb_cd32_se[] = { AK_A, -1, AK_D, -1, AK_W, -1, AK_S, -1, -1, AK_LALT, -1, AK_LSH, -1, AK_NP7, -1, AK_NP9, -1, AK_NPDIV, -1, AK_NPSUB, -1, AK_NPMUL, -1, -1 };
int kb_arcadia[] = { AK_F2, -1, AK_1, -1, AK_2, -1, AK_5, -1, AK_6, -1, -1 };
int kb_cdtv[] = { AK_NP1, -1, AK_NP3, -1, AK_NP7, -1, AK_NP9, -1, -1 };

/* Order is fixed by inputdevice.cpp KBR_DEFAULT_MAP_NP..KBR_DEFAULT_MAP_CDTV. */
int *g_kbmaps[] = {
    kb_np, kb_ck, kb_se, kb_np3, kb_ck3, kb_se3,
    kb_cd32_np, kb_cd32_ck, kb_cd32_se,
    kb_arcadia, kb_cdtv,
};
static_assert(sizeof g_kbmaps / sizeof g_kbmaps[0] == 11, "inputdevice.cpp reads 11 keyboard maps");

/* ------------------------------------------------------------------ */
/* inputdevice_functions implementations                               */
/* ------------------------------------------------------------------ */

inline bool valid(int index, int count)
{
    return index >= 0 && index < count;
}

/* Callers only read the returned strings (my_strdup, _tcscmp, _tcsncpy source).
 * The table type is TCHAR* in the shared inputdevice_functions ABI. */
inline TCHAR *text(const TCHAR *s)
{
    return const_cast<TCHAR *>(s);
}

int always_ok()
{
    return 1;
}

void nothing()
{
}

void nothing_for(int)
{
}

/* num == -1 is the "all devices" phase of inputdevice_acquire(); inputdevice.cpp
 * also walks every index up to MAX_INPUT_DEVICES, existing or not. */
int acquire_device(int num, int count)
{
    return num == -1 || valid(num, count) ? 1 : 0;
}

int no_flags(int)
{
    return 0;
}

/* Mice and joysticks: axes 0..kAxes-1, then buttons. */
int pointer_widget_num(int device, int count)
{
    return valid(device, count) ? kAxes + kButtons : 0;
}

int pointer_widget_type(int device, int count, int num, TCHAR *name, uae_u32 *code)
{
    const TCHAR *label = _T("");
    int type = IDEV_WIDGET_NONE;
    if (valid(device, count)) {
        if (valid(num, kAxes)) {
            type = IDEV_WIDGET_AXIS;
            label = kAxisNames[num];
        } else if (num >= kAxes && num < kAxes + kButtons) {
            type = IDEV_WIDGET_BUTTON;
            label = kButtonNames[num - kAxes];
        }
    }
    if (name)
        _tcscpy(name, label);
    if (code)
        *code = type == IDEV_WIDGET_NONE ? 0 : uae_u32(num);
    return type;
}

/* No BUTTONAXIS widgets (buttons synthesized from axes) exist; their nominal
 * first index is the one past the last real button, like dinput.cpp. */
int pointer_widget_first(int type)
{
    switch (type) {
    case IDEV_WIDGET_AXIS:
        return 0;
    case IDEV_WIDGET_BUTTON:
        return kAxes;
    case IDEV_WIDGET_BUTTONAXIS:
        return kAxes + kButtons;
    }
    return -1;
}

/* Mouse */
int mouse_num() { return kMouseCount; }
TCHAR *mouse_friendly(int n) { return valid(n, kMouseCount) ? text(kMice[n].friendly) : nullptr; }
TCHAR *mouse_unique(int n) { return valid(n, kMouseCount) ? text(kMice[n].unique) : nullptr; }
int mouse_acquire(int n, int) { return acquire_device(n, kMouseCount); }
int mouse_widget_num(int n) { return pointer_widget_num(n, kMouseCount); }
int mouse_widget_type(int n, int num, TCHAR *name, uae_u32 *code) { return pointer_widget_type(n, kMouseCount, num, name, code); }
int mouse_widget_first(int, int type) { return pointer_widget_first(type); }

/* Joystick */
int joystick_num() { return kJoystickCount; }
TCHAR *joystick_friendly(int n) { return valid(n, kJoystickCount) ? text(kJoysticks[n].friendly) : nullptr; }
TCHAR *joystick_unique(int n) { return valid(n, kJoystickCount) ? text(kJoysticks[n].unique) : nullptr; }
int joystick_acquire(int n, int) { return acquire_device(n, kJoystickCount); }
int joystick_widget_num(int n) { return pointer_widget_num(n, kJoystickCount); }
int joystick_widget_type(int n, int num, TCHAR *name, uae_u32 *code) { return pointer_widget_type(n, kJoystickCount, num, name, code); }
int joystick_widget_first(int, int type) { return pointer_widget_first(type); }

/* Keyboard */
int keyboard_init()
{
    /* inputdevice_default_prefs() calls inputdevice_init() and then reads the
     * translation table, so it must be installed from here. On Windows the same
     * call is made from keyboard_settrans() during start-up. Repeated calls
     * (inputdevice_devicechange, restart) store the same constant pointers. */
    inputdevice_setkeytranslation(g_keytrans, g_kbmaps);
    return 1;
}
int keyboard_num() { return kKeyboardCount; }
TCHAR *keyboard_friendly(int n) { return valid(n, kKeyboardCount) ? text(_T("P6 keyboard")) : nullptr; }
TCHAR *keyboard_unique(int n) { return valid(n, kKeyboardCount) ? text(_T("P6KEYBOARD1")) : nullptr; }
int keyboard_acquire(int n, int) { return acquire_device(n, kKeyboardCount); }
int keyboard_widget_num(int n) { return valid(n, kKeyboardCount) ? kKeyCodes : 0; }
int keyboard_widget_type(int n, int num, TCHAR *name, uae_u32 *code)
{
    const bool ok = valid(n, kKeyboardCount) && valid(num, kKeyCodes);
    if (name)
        _tcscpy(name, ok ? kKeyEvents[kKeyMap.entry[num]].name : _T(""));
    if (code)
        *code = ok ? uae_u32(num) : 0;
    return ok ? IDEV_WIDGET_KEY : IDEV_WIDGET_NONE;
}
int keyboard_widget_first(int, int) { return 0; }

/* ------------------------------------------------------------------ */
/* Default gameport mappings                                           */
/* ------------------------------------------------------------------ */

/* Events of one Amiga port. Index 0 is port 0 (the mouse port), index 1 is
 * port 1 (the joystick port). Ports 2 and 3 are the parallel-port joystick
 * adapter, which the A500 profile does not have. */
struct PortEvents {
    int mouse_h, mouse_v;
    int fire, second, third;
    int joy_h, joy_v;
    int pot_h, pot_v;
    int digital_left, digital_right, digital_up; /* analog stick button lines */
};
constexpr PortEvents kPorts[2] = {
    {
        INPUTEVENT_MOUSE1_HORIZ, INPUTEVENT_MOUSE1_VERT,
        INPUTEVENT_JOY1_FIRE_BUTTON, INPUTEVENT_JOY1_2ND_BUTTON, INPUTEVENT_JOY1_3RD_BUTTON,
        INPUTEVENT_JOY1_HORIZ, INPUTEVENT_JOY1_VERT,
        INPUTEVENT_JOY1_HORIZ_POT, INPUTEVENT_JOY1_VERT_POT,
        INPUTEVENT_JOY1_LEFT, INPUTEVENT_JOY1_RIGHT, INPUTEVENT_JOY1_UP,
    },
    {
        INPUTEVENT_MOUSE2_HORIZ, INPUTEVENT_MOUSE2_VERT,
        INPUTEVENT_JOY2_FIRE_BUTTON, INPUTEVENT_JOY2_2ND_BUTTON, INPUTEVENT_JOY2_3RD_BUTTON,
        INPUTEVENT_JOY2_HORIZ, INPUTEVENT_JOY2_VERT,
        INPUTEVENT_JOY2_HORIZ_POT, INPUTEVENT_JOY2_VERT_POT,
        INPUTEVENT_JOY2_LEFT, INPUTEVENT_JOY2_RIGHT, INPUTEVENT_JOY2_UP,
    },
};

/* Same as od-win32/dinput.cpp setid(): with advancedmultiinput the mapping
 * goes to the first free sub slot. In the game ports panel (gp) an existing
 * user mapping in sub 0 is parked in the spare slot first. */
int next_sub(const uae_input_device *uid, int i, int slot, int sub)
{
    if (currprefs.input_advancedmultiinput) {
        while (uid[i].eventid[slot][sub] > 0) {
            if (++sub >= MAX_INPUT_SUB_EVENT)
                return -1;
        }
    }
    return sub;
}

void set_event(uae_input_device *uid, int i, int slot, int sub, int port, int evt, bool gp)
{
    sub = next_sub(uid, i, slot, sub);
    if (sub < 0)
        return;
    if (gp && sub == 0)
        inputdevice_sparecopy(&uid[i], slot, sub);
    uid[i].eventid[slot][sub] = evt;
    uid[i].port[slot][sub] = port + 1;
}

void set_event_af(uae_input_device *uid, int i, int slot, int sub, int port, int evt, int af, bool gp)
{
    sub = next_sub(uid, i, slot, sub);
    if (sub < 0)
        return;
    set_event(uid, i, slot, sub, port, evt, gp);
    uae_u64 &flags = uid[i].flags[slot][sub];
    flags &= ~static_cast<uae_u64>(ID_FLAG_AUTOFIRE_MASK);
    if (af >= JPORT_AF_NORMAL)
        flags |= ID_FLAG_AUTOFIRE;
    if (af == JPORT_AF_TOGGLE)
        flags |= ID_FLAG_TOGGLE;
    if (af == JPORT_AF_ALWAYS)
        flags |= ID_FLAG_INVERTTOGGLE;
    if (af == JPORT_AF_TOGGLENOAF)
        flags |= ID_FLAG_INVERT;
}

/* uid is the array of the device *kind* being configured. joymouseswap names
 * a device that borrows the other kind's role (a joystick that emulates a
 * mouse, a mouse that emulates a joystick), so for input_get_default_mouse()
 * the array holds mice unless joymouseswap is set, and for
 * input_get_default_joystick*() it holds joysticks unless joymouseswap is set.
 * Both kinds expose the same widgets here, so the count is the only difference.
 * Ports 0 and 1 are the game ports; 2 and 3 (parallel adapter) do not exist. */
bool usable(const uae_input_device *uid, int i, int port, bool uid_is_mice)
{
    const int count = uid_is_mice ? kMouseCount : kJoystickCount;
    return uid && valid(i, count) && valid(port, 2);
}

} /* namespace */

/* ------------------------------------------------------------------ */
/* Exported symbols                                                    */
/* ------------------------------------------------------------------ */

struct inputdevice_functions inputdevicefunc_keyboard = {
    keyboard_init, nothing, keyboard_acquire, nothing_for, nothing,
    keyboard_num, keyboard_friendly, keyboard_unique,
    keyboard_widget_num, keyboard_widget_type, keyboard_widget_first,
    no_flags,
};

struct inputdevice_functions inputdevicefunc_mouse = {
    always_ok, nothing, mouse_acquire, nothing_for, nothing,
    mouse_num, mouse_friendly, mouse_unique,
    mouse_widget_num, mouse_widget_type, mouse_widget_first,
    no_flags,
};

struct inputdevice_functions inputdevicefunc_joystick = {
    always_ok, nothing, joystick_acquire, nothing_for, nothing,
    joystick_num, joystick_friendly, joystick_unique,
    joystick_widget_num, joystick_widget_type, joystick_widget_first,
    no_flags,
};

/* Mouse events for a mouse (or for a joystick when joymouseswap is set) on a
 * game port. Returns 1 when the device is the default one of its kind, 0 when
 * it is not or when nothing could be mapped. */
int input_get_default_mouse(struct uae_input_device *uid, int i, int port, int af, bool gp, bool /* wheel */, bool joymouseswap)
{
    /* wheel: neither logical device has a wheel axis, so there is nothing to
     * map; dinput.cpp maps axis 2 only when the device provides it. The extra
     * "back/forward" mouse buttons of the Windows mapping need buttons 4 and 5
     * and likewise do not exist. */
    if (!usable(uid, i, port, !joymouseswap))
        return 0;
    const PortEvents &p = kPorts[port];
    set_event(uid, i, ID_AXIS_OFFSET + 0, 0, port, p.mouse_h, gp);
    set_event(uid, i, ID_AXIS_OFFSET + 1, 0, port, p.mouse_v, gp);
    set_event_af(uid, i, ID_BUTTON_OFFSET + 0, 0, port, p.fire, af, gp);
    set_event(uid, i, ID_BUTTON_OFFSET + 1, 0, port, p.second, gp);
    set_event(uid, i, ID_BUTTON_OFFSET + 2, 0, port, p.third, gp);
    return i == 0 ? 1 : 0;
}

/* A light pen needs an absolute pointer (tablet_lightpen); the logical devices
 * only deliver relative motion, and the bridge contract has no light pen. */
int input_get_default_lightpen(struct uae_input_device *, int, int, int, bool, bool, int)
{
    return 0;
}

/* Digital joystick (or gamepad) on a game port. Modes that need hardware this
 * profile lacks map nothing and return 0: the CD32 pad (JSEM_MODE_JOYSTICK_CD32,
 * needs the CD32 pad protocol and 7 buttons), the CDTV mouse and the parallel
 * port adapter (port 2 and 3). The bridge must not configure them. */
int input_get_default_joystick(struct uae_input_device *uid, int i, int port, int af, int mode, bool gp, bool joymouseswap, bool /* default_osk */)
{
    /* default_osk: dinput.cpp maps the fourth button to the on-screen keyboard;
     * the logical devices have three buttons. */
    if (!usable(uid, i, port, joymouseswap))
        return 0;
    if (mode != JSEM_MODE_DEFAULT && mode != JSEM_MODE_JOYSTICK && mode != JSEM_MODE_GAMEPAD)
        return 0;
    const PortEvents &p = kPorts[port];
    set_event(uid, i, ID_AXIS_OFFSET + 0, 0, port, p.joy_h, gp);
    set_event(uid, i, ID_AXIS_OFFSET + 1, 0, port, p.joy_v, gp);
    set_event_af(uid, i, ID_BUTTON_OFFSET + 0, 0, port, p.fire, af, gp);
    set_event(uid, i, ID_BUTTON_OFFSET + 1, 0, port, p.second, gp);
    /* plain JSEM_MODE_JOYSTICK is the two-button joystick */
    if (mode != JSEM_MODE_JOYSTICK)
        set_event(uid, i, ID_BUTTON_OFFSET + 2, 0, port, p.third, gp);
    return i == 0 ? 1 : 0;
}

/* Analog stick: the axes drive the pot counters and the buttons take the
 * digital lines. A digital stick reaches the pot end stops only. */
int input_get_default_joystick_analog(struct uae_input_device *uid, int i, int port, int af, bool gp, bool joymouseswap, bool /* default_osk */)
{
    if (!usable(uid, i, port, joymouseswap))
        return 0;
    const PortEvents &p = kPorts[port];
    set_event(uid, i, ID_AXIS_OFFSET + 0, 0, port, p.pot_h, gp);
    set_event(uid, i, ID_AXIS_OFFSET + 1, 0, port, p.pot_v, gp);
    set_event_af(uid, i, ID_BUTTON_OFFSET + 0, 0, port, p.digital_left, af, gp);
    set_event(uid, i, ID_BUTTON_OFFSET + 1, 0, port, p.digital_right, gp);
    set_event(uid, i, ID_BUTTON_OFFSET + 2, 0, port, p.digital_up, gp);
    return i == 0 ? 1 : 0;
}

/* Keyboard 0 is the default keyboard. -1 asks for the index of the default
 * keyboard (inputdevice_translatekeycode), which is 0, so -1 answers 0. */
int input_get_default_keyboard(int num)
{
    return num == 0 ? 1 : 0;
}

/* Windows takes the mouse and the keyboard away from the desktop when input is
 * acquired. There is no desktop here and nothing to take. */
void target_inputdevice_acquire(void)
{
}

void target_inputdevice_unacquire(bool)
{
}

/* Called for the Caps Lock key with the requested state. Returning 0 means
 * "not a host-synchronised lock key": the state passes through unchanged, so
 * the bridge bitmap bit is the latch state (see the keyboard table above). */
int target_checkcapslock(int, int *)
{
    return 0;
}

/* Windows clears its record of which host keys are down and sends a release
 * for each. That record lives in the bridge bitmap here, and the engine has
 * already cleared its own Amiga-side key state before it calls this (the only
 * caller is the end of a keyboard reset sequence in inputdevice_do_keyboard).
 * This layer holds no key state, so there is nothing left to release. A key that
 * is still held when the reset ends stays held in the bitmap; the bridge sees no
 * change and sends no new press, as with DirectInput after release_keys(). */
void release_keys(void)
{
}

/* No tablet behind the logical devices: inputdevice_is_tablet() is always 0
 * and the absolute-pointer paths never run. */
int is_tablet(void)
{
    return 0;
}

/* Debug bitmask read by the tablet code in inputdevice.cpp. Constant. */
int tablet_log = 0;
