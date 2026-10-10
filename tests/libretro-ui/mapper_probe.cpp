// Unit checks of the RetroPad mapper (adapters/libretro/mapper.h, T31d) on synthetic controls, with no engine and no frontend: the default functions, the
// targets, Face Button Options, the edges and the actions they run, the D-Pad mouse mode, turbo fire, the arithmetic of the analog stick mouse, the soft
// reset, and the input memory through the adapter section of a state. Exit code 0 and "mapper probe ok" when every check holds.
#include "state_frame.h"

#include <stdio.h>
#include <string.h>

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

static Effects step(const Controls &controls, const InputConfig &config, InputMemory *memory, bool mouse_port = true)
{
    Effects fx;
    mapper_step(controls, config, mouse_port, memory, &fx);
    return fx;
}

static Controls pressing(uint32_t down)
{
    Controls c;
    c.down = down;
    return c;
}

static void defaults()
{
    InputConfig config;
    InputMemory memory;
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_B)), config, &memory).joy == P6_BUTTON_1);
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_A)), config, &memory).joy == P6_BUTTON_2);
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_X)), config, &memory).joy == P6_BUTTON_3);
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_UP) | bit(RETRO_DEVICE_ID_JOYPAD_LEFT)), config, &memory).joy == (P6_BUTTON_UP | P6_BUTTON_LEFT));
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_DOWN) | bit(RETRO_DEVICE_ID_JOYPAD_RIGHT)), config, &memory).joy == (P6_BUTTON_DOWN | P6_BUTTON_RIGHT));
    for (unsigned id : {RETRO_DEVICE_ID_JOYPAD_Y, RETRO_DEVICE_ID_JOYPAD_SELECT, RETRO_DEVICE_ID_JOYPAD_START, RETRO_DEVICE_ID_JOYPAD_L, RETRO_DEVICE_ID_JOYPAD_R,
                        RETRO_DEVICE_ID_JOYPAD_L2, RETRO_DEVICE_ID_JOYPAD_R2, RETRO_DEVICE_ID_JOYPAD_L3, RETRO_DEVICE_ID_JOYPAD_R3}) {
        const Effects fx = step(pressing(bit(id)), config, &memory);
        CHECK(fx.joy == 0 && fx.mouse_buttons == 0 && fx.move_x == 0 && fx.move_y == 0 && !fx.keys[0] && !fx.keys[1] && !fx.keys[2] && !fx.keys[3]);
    }
    // The left stick is the four joystick directions, the right stick is nothing.
    CHECK(step(pressing(bit(kStickInput) | bit(kStickInput + 3)), config, &memory).joy == (P6_BUTTON_UP | P6_BUTTON_RIGHT));
    CHECK(step(pressing(bit(kStickInput + 4) | bit(kStickInput + 7)), config, &memory).joy == 0);
    // Nothing down: nothing happens, and no memory is left but the (empty) edge set.
    InputMemory fresh;
    step(pressing(0), config, &fresh);
    CHECK(fresh == InputMemory());
}

static void face_options()
{
    struct Row { unsigned face; uint32_t b, a, y, x; };
    const Row rows[] = {{0, P6_BUTTON_1, P6_BUTTON_2, 0, P6_BUTTON_3}, {1, P6_BUTTON_1, P6_BUTTON_UP, 0, P6_BUTTON_3},
                        {2, P6_BUTTON_2, 0, P6_BUTTON_1, P6_BUTTON_3}, {3, P6_BUTTON_UP, 0, P6_BUTTON_1, P6_BUTTON_3}};
    for (const Row &row : rows) {
        InputConfig config;
        config.face = row.face;
        InputMemory memory;
        CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_B)), config, &memory).joy == row.b);
        CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_A)), config, &memory).joy == row.a);
        CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_Y)), config, &memory).joy == row.y);
        CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_X)), config, &memory).joy == row.x);
    }
}

static void targets()
{
    InputConfig config;
    InputMemory memory;
    config.mapper[RETRO_DEVICE_ID_JOYPAD_X] = kMapperTargetNone;                  // a button can be silenced
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_X)), config, &memory).joy == 0);
    config.mapper[RETRO_DEVICE_ID_JOYPAD_Y] = kKeyTargetBase + 0x40;                // an Amiga key
    Effects fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_Y)), config, &memory);
    CHECK(key(fx, 0x40) && fx.joy == 0);
    config.mapper[RETRO_DEVICE_ID_JOYPAD_L] = kMapperTargetJoyFire3;
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_L)), config, &memory).joy == P6_BUTTON_3);
    config.mapper[RETRO_DEVICE_ID_JOYPAD_R] = kMapperTargetMouseRight;
    CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_R)), config, &memory).mouse_buttons == P6_BUTTON_2);
    config.mapper[RETRO_DEVICE_ID_JOYPAD_L2] = kMapperTargetMouseMoveLeft;
    config.mapper[RETRO_DEVICE_ID_JOYPAD_R2] = kMapperTargetMouseMoveDown;
    fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_L2) | bit(RETRO_DEVICE_ID_JOYPAD_R2)), config, &memory);
    CHECK(fx.move_x == -config.dpad_speed && fx.move_y == config.dpad_speed);
    config.mapper[kStickInput + 2] = kKeyTargetBase + 0x4F;                         // a stick direction as a key; the other directions keep their default
    fx = step(pressing(bit(kStickInput + 2) | bit(kStickInput)), config, &memory);
    CHECK(key(fx, 0x4F) && fx.joy == P6_BUTTON_UP);
    config.mapper[RETRO_DEVICE_ID_JOYPAD_START] = kKeyTargetBase + 0x70;            // beyond the keyboard: ignored
    fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_START)), config, &memory);
    CHECK(!fx.keys[0] && !fx.keys[1] && !fx.keys[2] && !fx.keys[3]);
}

static void actions_and_edges()
{
    InputConfig config;
    config.mapper[RETRO_DEVICE_ID_JOYPAD_SELECT] = kMapperTargetToggleMouse;
    InputMemory memory;
    step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_SELECT)), config, &memory);
    CHECK(memory.dpad_mouse);                                                       // on the press
    step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_SELECT)), config, &memory);
    CHECK(memory.dpad_mouse);                                                       // and not again while it is held
    step(pressing(0), config, &memory);
    step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_SELECT)), config, &memory);
    CHECK(!memory.dpad_mouse);                                                      // the next press flips it back
    // D-Pad mouse mode: the joystick functions of the pad are the mouse's, and the joystick gets nothing from the pad.
    memory.dpad_mouse = true;
    config.dpad_speed = 7;
    Effects fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_UP) | bit(RETRO_DEVICE_ID_JOYPAD_RIGHT) | bit(RETRO_DEVICE_ID_JOYPAD_B) | bit(RETRO_DEVICE_ID_JOYPAD_A) |
                                      bit(RETRO_DEVICE_ID_JOYPAD_X)), config, &memory);
    CHECK(fx.joy == 0 && fx.move_x == 7 && fx.move_y == -7 && fx.mouse_buttons == (P6_BUTTON_1 | P6_BUTTON_2 | P6_BUTTON_3));
    fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_DOWN) | bit(RETRO_DEVICE_ID_JOYPAD_LEFT)), config, &memory);
    CHECK(fx.move_x == -7 && fx.move_y == 7);
    fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_DOWN)), config, &memory, false);          // without the mouse port the pad stays a joystick
    CHECK(fx.joy == P6_BUTTON_DOWN && fx.move_y == 0);
    // A key target is not turned into a mouse thing in mouse mode.
    config.mapper[RETRO_DEVICE_ID_JOYPAD_Y] = kKeyTargetBase + 0x20;
    CHECK(key(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_Y)), config, &memory), 0x20));
    // Hotkeys are edges too; each runs its own action.
    InputConfig keys;
    InputMemory m2;
    step(pressing(bit(kHotkeyBit + 1)), keys, &m2);
    CHECK(m2.turbo_toggled && !m2.dpad_mouse);
    step(pressing(bit(kHotkeyBit + 2) | bit(kHotkeyBit + 3)), keys, &m2);
    CHECK(m2.aspect_toggled && m2.crop_toggled && m2.turbo_toggled);
    step(pressing(bit(kHotkeyBit + 0)), keys, &m2);
    CHECK(m2.dpad_mouse);
    step(pressing(bit(kHotkeyBit + 1)), keys, &m2);                                  // released in between, so this is a new press
    CHECK(!m2.turbo_toggled);
}

static void turbo()
{
    InputConfig config;
    config.turbo = true;
    config.turbo_input = RETRO_DEVICE_ID_JOYPAD_B;
    for (int pulse : {2, 4, 6, 8, 10, 12}) {
        config.pulse = pulse;
        InputMemory memory;
        for (unsigned n = 0; n < 240; ++n) {
            const Effects fx = step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_B) | bit(RETRO_DEVICE_ID_JOYPAD_A)), config, &memory);
            CHECK((fx.joy & P6_BUTTON_1) == ((n % pulse) < unsigned(pulse / 2) ? P6_BUTTON_1 : 0u));       // the first half of every cycle is down, from the first unit
            CHECK(fx.joy & P6_BUTTON_2);                                                                   // another button is untouched
        }
        step(pressing(0), config, &memory);
        CHECK(memory.turbo_phase == 0);                                                                    // released: the next press starts down again
        CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_B)), config, &memory).joy == P6_BUTTON_1);
    }
    // Off: the button is plain. Turned on by the toggle (an edge of a hotkey), off again by the same toggle if the option is on.
    config.turbo = false;
    config.pulse = 4;
    InputMemory memory;
    for (unsigned n = 0; n < 8; ++n) CHECK(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_B)), config, &memory).joy == P6_BUTTON_1);
    Controls toggle = pressing(bit(RETRO_DEVICE_ID_JOYPAD_B) | bit(kHotkeyBit + 1));
    CHECK(step(toggle, config, &memory).joy == P6_BUTTON_1);                         // pressed, turbo is on from this unit, phase 0: down
    toggle.down = bit(RETRO_DEVICE_ID_JOYPAD_B);
    CHECK(step(toggle, config, &memory).joy == P6_BUTTON_1);
    CHECK(step(toggle, config, &memory).joy == 0);
    CHECK(step(toggle, config, &memory).joy == 0);
    CHECK(step(toggle, config, &memory).joy == P6_BUTTON_1);
    // A mapped turbo button auto-fires what it is mapped to.
    config.turbo = true;
    config.pulse = 2;
    config.turbo_input = RETRO_DEVICE_ID_JOYPAD_L;
    config.mapper[RETRO_DEVICE_ID_JOYPAD_L] = kKeyTargetBase + 0x40;
    InputMemory keyed;
    CHECK(key(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_L)), config, &keyed), 0x40));
    CHECK(!key(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_L)), config, &keyed), 0x40));
    CHECK(key(step(pressing(bit(RETRO_DEVICE_ID_JOYPAD_L)), config, &keyed), 0x40));
}

static void stick_mouse()
{
    // The arithmetic, from the formula in the header: the whole stick past the deadzone, scaled.
    CHECK(stick_milli_counts(0, 0, 0, 20, 10) == 0);
    CHECK(stick_milli_counts(6000, 6000, 0, 20, 10) == 0);                           // inside the deadzone (20% of 32768 is 6553)
    CHECK(stick_milli_counts(8000, 8000, 0, 20, 10) == 551);                         // 8000: past = 1447, 1447 * 10000 / 26215
    CHECK(stick_milli_counts(32767, 32767, 0, 20, 10) == 9999);                      // the edge: about 10 counts
    CHECK(stick_milli_counts(-32767, -32767, 0, 20, 10) == -9999);
    CHECK(stick_milli_counts(32767, 32767, 0, 20, 30) == 29998);                     // 300%
    CHECK(stick_milli_counts(32767, 32767, 0, 0, 10) == 9999);                       // no deadzone
    CHECK(stick_milli_counts(23170, 23170, 23170, 20, 10) >= 7070 && stick_milli_counts(23170, 23170, 23170, 20, 10) <= 7072);      // a diagonal at the edge: 10 counts in all
    CHECK(isqrt(0) == 0 && isqrt(1) == 1 && isqrt(15) == 3 && isqrt(16) == 4 && isqrt(UINT64_C(4294836225)) == 65535);
    // Over several units: the carry makes a slow push a whole count every few units, and a stick at rest forgets it.
    InputConfig config;
    config.analog_mouse = 1;
    config.mapper[kStickInput] = config.mapper[kStickInput + 1] = config.mapper[kStickInput + 2] = config.mapper[kStickInput + 3] = kMapperTargetDefault;
    InputMemory memory;
    int total = 0;
    Controls c;
    c.stick[0][0] = 8000;
    for (int n = 0; n < 20; ++n) {
        const Effects fx = step(c, config, &memory);
        total += fx.move_x;
        CHECK(fx.move_y == 0 && fx.joy == 0);                                          // the left stick is the mouse's: no joystick directions
    }
    CHECK(total == 11);                                                              // 20 units of 551 thousandths
    CHECK(memory.stick_frac[0][0] != 0);
    c.stick[0][0] = 0;
    step(c, config, &memory);
    CHECK(memory.stick_frac[0][0] == 0);
    // The right stick only when the option says so; a mapped direction wins over the mouse.
    c.stick[1][1] = -32767;
    c.stick[0][0] = 0;
    CHECK(step(c, config, &memory).move_y == 0);
    config.analog_mouse = 2;
    CHECK(step(c, config, &memory).move_y == -9);
    config.analog_mouse = 3;
    config.mapper[kStickInput + 5] = kMapperTargetNone;                              // the right stick's "down" is mapped: that stick is not a mouse any more
    InputMemory other;
    CHECK(step(c, config, &other).move_y == 0);
    // No mouse port, no mouse movement (and no carry).
    config.mapper[kStickInput + 5] = kMapperTargetDefault;
    InputMemory none;
    CHECK(step(c, config, &none, false).move_y == 0 && none.stick_frac[1][1] == 0);
}

static void soft_reset()
{
    InputConfig config;
    InputMemory memory;
    step(pressing(bit(kHotkeyBit + 4)), config, &memory);
    CHECK(memory.reset_hold == kResetHoldUnits - 1);                                  // the first unit of the chord is this one
    Effects fx;
    unsigned units = 1;
    Controls c;
    for (; units < 100; ++units) {
        fx = step(c, config, &memory);
        if (!key(fx, kKeyCtrl)) break;
        CHECK(key(fx, kKeyLeftAmiga) && key(fx, kKeyRightAmiga));
    }
    CHECK(units == kResetHoldUnits);                                                 // exactly kResetHoldUnits units of Ctrl-Amiga-Amiga
    CHECK(!key(fx, kKeyLeftAmiga) && !key(fx, kKeyRightAmiga) && memory.reset_hold == 0);
    // A second press while it holds does not lengthen it.
    InputMemory again;
    step(pressing(bit(kHotkeyBit + 4)), config, &again);
    step(pressing(0), config, &again);
    step(pressing(bit(kHotkeyBit + 4)), config, &again);
    CHECK(again.reset_hold == kResetHoldUnits - 3);
}

static void memory_in_a_state()
{
    Section section;
    section.input.caps_latched = true;
    section.input.dpad_mouse = true;
    section.input.turbo_toggled = true;
    section.input.crop_toggled = true;
    section.input.aspect_toggled = true;
    section.input.mouse_rx = -480;
    section.input.mouse_ry = 17;
    section.input.turbo_phase = 119;
    section.input.reset_hold = kResetHoldUnits;
    section.input.held = bit(3) | bit(23) | bit(kHotkeyBit + 4);
    section.input.host_frac[0] = -99;
    section.input.host_frac[1] = 42;
    section.input.stick_frac[0][0] = 999;
    section.input.stick_frac[0][1] = -1;
    section.input.stick_frac[1][0] = 0;
    section.input.stick_frac[1][1] = -999;
    std::vector<uint8_t> bytes;
    encode_section(section, &bytes);
    CHECK(bytes.size() <= section_bound(0, 0));
    Section back;
    const char *why = nullptr;
    CHECK(decode_section(bytes.data(), bytes.size(), 100, 760, 576, &back, &why));
    CHECK(back.input == section.input);
    // Every field has a range, and a value outside it is refused. A section with no disk, no audio and no picture has 40 bytes before the input memory, which
    // is eight words (flags, the two owed counts, turbo and reset counters, the inputs held, and the three pairs of fractions).
    auto accepts = [&](size_t word, uint32_t value) {
        std::vector<uint8_t> copy = bytes;
        put32(&copy[40 + 4 * word], value);
        Section out;
        return decode_section(copy.data(), copy.size(), 100, 760, 576, &out, &why);
    };
    CHECK(accepts(0, 63) && !accepts(0, 64));                                        // the six flags
    CHECK(accepts(1, uint32_t(kMouseBacklog)) && !accepts(1, uint32_t(kMouseBacklog + 1)));
    CHECK(accepts(3, kTurboCycle - 1) && !accepts(3, kTurboCycle));                  // the turbo phase stays inside its cycle
    CHECK(accepts(3, kResetHoldUnits << 8) && !accepts(3, (kResetHoldUnits + 1) << 8) && !accepts(3, UINT32_C(1) << 16));
    CHECK(accepts(4, bit(kHotkeyBit + kHotkeyCount - 1)) && !accepts(4, bit(kHotkeyBit + kHotkeyCount)));      // a hotkey that does not exist
    CHECK(accepts(5, 99) && !accepts(5, 100) && !accepts(5, uint32_t(uint16_t(-100))));     // the fraction a Mouse Speed leaves: -99 .. 99
    CHECK(accepts(6, 999) && !accepts(6, 1000) && accepts(7, uint32_t(uint16_t(-999)) << 16) && !accepts(7, uint32_t(uint16_t(-1000)) << 16));
    // A section cut inside the input memory is refused.
    Section out;
    CHECK(!decode_section(bytes.data(), 60, 100, 760, 576, &out, &why));
}

int main()
{
    defaults();
    face_options();
    targets();
    actions_and_edges();
    turbo();
    stick_mouse();
    soft_reset();
    memory_in_a_state();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("mapper probe ok\n");
    return 0;
}
