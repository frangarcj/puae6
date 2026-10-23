/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Harness: the REAL inputdevice.cpp/keybuf.cpp objects of build/native-debug
 * (unmodified) linked with input_devices.o and stand-ins for the rest of the
 * engine. Exercises the engine's own default-preferences, port assignment,
 * device matching, enable logic and the setter -> JOYxDAT/CIA/keyboard path.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "inputdevice.h"
#include "keyboard.h"
#include "cdtv.h"
#include <stdio.h>
#include <stdlib.h>

#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) {                                                         \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            abort();                                                           \
        }                                                                      \
    } while (0)

/* ---- data the engine objects expect from other units ---------------- */
struct uae_prefs currprefs, changed_prefs;
int pause_emulation, quit_program, kickstart_version, uae_boot_rom_type;
int vpos, maxvpos = 312, maxvpos_display = 256;
unsigned long long syncbase = 1000000;
long long currcycle;
unsigned short dmacon;
int lightpen_active, lightpen_enabled, lightpen_enabled2, lightpen_x[2], lightpen_y[2];
/* custom.cpp owns the converted beam coordinates; no pointer is used here. */
int lightpen_cx[2] = {-1, -1}, lightpen_cy[2] = {-1, -1};
char savestate_fname[1024];
int savestate_state;
/* opaque, only touched by code paths this harness never enters */
char adisplays[1 << 20];
char mem_banks[1 << 20];
char regs[1 << 16];
char uaeboard_bank[1 << 12];

/* ---- functions ------------------------------------------------------ */
extern "C++" {
TCHAR *my_strdup(const TCHAR *s) { return s ? strdup(s) : nullptr; }
void write_log(const TCHAR *, ...) {}
void set_config_changed(int) {}
int current_maxvpos() { return maxvpos; }
unsigned int uaerand() { return 0; }
int handle_msgpump(bool) { return 0; }
unsigned short dongle_joydat(int, unsigned short v) { return v; }
unsigned short dongle_potgor(unsigned short v) { return v; }
void dongle_potgo(unsigned short) {}
void dongle_joytest(unsigned short) {}
void dongle_reset() {}
int dongle_analogjoy(int, int) { return 0; }
struct GpEvent { int port, button, on; };
static GpEvent g_gp[64];
static int g_gp_n;
void gui_gameport_axis_change(int, int, int, int) {}
void gui_gameport_button_change(int port, int button, int on) { if (g_gp_n < 64) g_gp[g_gp_n++] = { port, button, on }; }
void gui_message(const TCHAR *, ...) { abort(); }
void statusline_add_message(int, const TCHAR *, ...) {}
void translate_message(int, TCHAR *out) { out[0] = 0; }
static int g_reset_calls, g_uae_reset_calls, g_cpu_inreset_calls;
static unsigned g_cdtv_queries;
bool cdtv_front_panel(int button)
{
    CHECK(button == -1 && !currprefs.cs_cdtvcd);
    ++g_cdtv_queries;
    return false; // Explicit inactive-panel observation for this A500 fixture.
}
void custom_reset(bool, bool) { ++g_reset_calls; }
void uae_reset(int, int) { ++g_uae_reset_calls; }
void cpu_inreset() { ++g_cpu_inreset_calls; }
int resetwarning_do(int) { return 0; }
bool can_cpu_tracer() { return false; }
bool is_cpu_tracer() { return false; }
void setsystime() {}
unsigned long long uae_time() { return 0; }
void copy_inputdevice_prefs(struct uae_prefs *, struct uae_prefs *) {}
}

#include "keybuf.h"

static uae_prefs g_seed;

/* what the bridge will do to describe a port: NONE, or a device of the logical set */
enum PortKind { NONE, MOUSE, JOY };
static void configure(PortKind p0, PortKind p1)
{
    PortKind kinds[2] = { p0, p1 };
    for (int p = 0; p < MAX_JPORTS; ++p)
        for (int d = 0; d < MAX_JPORT_DEVS; ++d)
            changed_prefs.jports[p].jd[d].id = JPORT_NONE;
    for (int p = 0; p < 2; ++p) {
        jport_dev &jd = changed_prefs.jports[p].jd[0];
        jd.mode = JSEM_MODE_DEFAULT;
        jd.submode = 0;
        jd.autofire = 0;
        jd.idc.name[0] = jd.idc.configname[0] = jd.idc.shortid[0] = 0;
        if (kinds[p] == MOUSE)
            jd.id = JSEM_MICE + 0;
        else if (kinds[p] == JOY)
            jd.id = JSEM_JOYS + p; /* joystick n is meant for port n */
    }
    inputdevice_updateconfig(&changed_prefs, &currprefs);
}


/* ---- observation helpers -------------------------------------------- */

static void run_frame_reads(int reads)
{
    /* what the emulated CPU/DMA do to the input hardware during one frame:
     * JOYxDAT reads spread over the lines, then the vertical blank */
    for (int i = 0; i < reads; ++i) {
        vpos = 1 + i * (maxvpos - 2) / (reads > 1 ? reads - 1 : 1);
        (void)JOY0DAT();
        (void)JOY1DAT();
    }
    vpos = 0;
    inputdevice_vsync();
}

static int counter_x(uae_u16 v) { return v & 0xff; }
static int counter_y(uae_u16 v) { return (v >> 8) & 0xff; }

static void drain(int frames = 12)
{
    for (int f = 0; f < frames; ++f)
        run_frame_reads(16);
}

static void clear_gp() { g_gp_n = 0; }

static bool gp_has(int port, int button, int on)
{
    for (int i = 0; i < g_gp_n; ++i)
        if (g_gp[i].port == port && g_gp[i].button == button && g_gp[i].on == on)
            return true;
    return false;
}

static int cia_pra_buttons()
{
    /* CIA-A PRA fire lines: bit 6 = port 0, bit 7 = port 1, low when pressed */
    return handle_joystick_buttons(0xff, 0x00) & 0xc0;
}

static int drain_keys(int *out, int cap)
{
    int n = 0;
    while (keys_available() && n < cap)
        out[n++] = get_next_key();
    return n;
}

static void check_devices_and_names()
{
    CHECK(inputdevice_get_device_total(IDTYPE_JOYSTICK) == 2);
    CHECK(inputdevice_get_device_total(IDTYPE_MOUSE) == 1);
    CHECK(inputdevice_get_device_total(IDTYPE_KEYBOARD) == 1);
    /* global device numbers: joysticks 0-1, mouse 2, keyboard 3, internal events 4 */
    CHECK(inputdevice_get_device_status(0) >= 0 && inputdevice_get_device_status(3) >= 0);
    CHECK(inputdevice_get_device_status(5) == -1);
    CHECK(inputdevice_get_widget_num(2) == 5);
    CHECK(inputdevice_get_widget_num(3) == 128);
    TCHAR name[256];
    CHECK(inputdevice_get_widget_type(3, 0x45, name, false) == IDEV_WIDGET_KEY && strcmp(name, "ESC") == 0);
    CHECK(inputdevice_get_widget_type(2, 0, name, false) == IDEV_WIDGET_AXIS);
    CHECK(inputdevice_get_widget_type(2, 2, name, false) == IDEV_WIDGET_BUTTON);
    CHECK(inputdevice_get_widget_type(0, 4, name, false) == IDEV_WIDGET_BUTTON);
    CHECK(inputdevice_get_widget_type(0, 5, name, false) == IDEV_WIDGET_NONE);
}

/* ports as prefs describe them */
static void check_port_prefs(int p, const char *name, const char *config)
{
    const jport_dev &jd = currprefs.jports[p].jd[0];
    CHECK(strcmp(jd.idc.name, name) == 0);
    CHECK(strcmp(jd.idc.configname, config) == 0);
}

static void test_mouse0_joy1()
{
    puts("== mouse in port 0, joystick in port 1");
    configure(MOUSE, JOY);
    CHECK(currprefs.jports[0].jd[0].id == JSEM_MICE);
    CHECK(currprefs.jports[1].jd[0].id == JSEM_JOYS + 1);
    check_port_prefs(0, "P6 mouse", "P6MOUSE1");
    check_port_prefs(1, "P6 joystick 2", "P6JOYSTICK2");
    /* the devices the bridge will push to are the enabled ones, the other joystick is not */
    CHECK(getmousestate(0) != 0);
    CHECK(getjoystickstate(1) != 0);
    CHECK(getjoystickstate(0) == 0);
    check_devices_and_names();
    clear_gp();
    drain();

    /* mouse motion arrives as counter movement on JOY0DAT */
    const uae_u16 base = JOY0DAT();
    setmousestate(0, 0, 5, 0);
    setmousestate(0, 1, -3, 0);
    drain();
    const uae_u16 after = JOY0DAT();
    CHECK(counter_x(after) - counter_x(base) == 5);
    CHECK(((counter_y(after) - counter_y(base)) & 0xff) == 0xfd);
    /* and not on the other port */
    const uae_u16 j1 = JOY1DAT();
    setmousestate(0, 0, 2, 0);
    drain();
    CHECK(JOY1DAT() == j1);
    CHECK(counter_x(JOY0DAT()) - counter_x(after) == 2);

    /* a large delta is spread over frames, none is lost */
    const uae_u16 b2 = JOY0DAT();
    setmousestate(0, 0, 300, 0);
    drain(40);
    CHECK(((counter_x(JOY0DAT()) - counter_x(b2)) & 0xff) == (300 & 0xff));

    /* buttons: CIA fire line of port 0 and the decoded gameport events */
    CHECK(cia_pra_buttons() == 0xc0);
    setmousebuttonstate(0, 0, 1);
    CHECK(cia_pra_buttons() == 0x80 && gp_has(0, JOYBUTTON_1, 1));
    setmousebuttonstate(0, 0, 0);
    CHECK(cia_pra_buttons() == 0xc0 && gp_has(0, JOYBUTTON_1, 0));
    setmousebuttonstate(0, 1, 1);
    setmousebuttonstate(0, 2, 1);
    CHECK(gp_has(0, JOYBUTTON_2, 1) && gp_has(0, JOYBUTTON_3, 1));
    setmousebuttonstate(0, 1, 0);
    setmousebuttonstate(0, 2, 0);
    CHECK(gp_has(0, JOYBUTTON_2, 0) && gp_has(0, JOYBUTTON_3, 0));
    CHECK(cia_pra_buttons() == 0xc0);

    /* joystick 1 on port 1: directions as the Amiga encodes them on JOY1DAT */
    clear_gp();
    const uae_u16 j0 = JOY0DAT();
    setjoystickstate(1, 0, +1, 1); /* right */
    setjoystickstate(1, 1, -1, 1); /* up */
    CHECK((JOY1DAT() & 0x0303) == 0x0103);
    setjoystickstate(1, 0, 0, 1);
    setjoystickstate(1, 1, 0, 1);
    CHECK((JOY1DAT() & 0x0303) == 0x0000);
    setjoystickstate(1, 0, -1, 1); /* left */
    setjoystickstate(1, 1, +1, 1); /* down */
    CHECK((JOY1DAT() & 0x0303) == 0x0301);
    setjoystickstate(1, 0, 0, 1);
    setjoystickstate(1, 1, 0, 1);
    CHECK(JOY0DAT() == j0); /* nothing leaked to port 0 */
    setjoybuttonstate(1, 0, 1);
    CHECK(cia_pra_buttons() == 0x40 && gp_has(1, JOYBUTTON_1, 1));
    setjoybuttonstate(1, 0, 0);
    CHECK(cia_pra_buttons() == 0xc0);
    setjoybuttonstate(1, 1, 1);
    setjoybuttonstate(1, 2, 1);
    CHECK(gp_has(1, JOYBUTTON_2, 1) && gp_has(1, JOYBUTTON_3, 1));
    setjoybuttonstate(1, 1, 0);
    setjoybuttonstate(1, 2, 0);
}

static void test_joy0_mouse1()
{
    puts("== joystick in port 0, mouse in port 1");
    configure(JOY, MOUSE);
    CHECK(currprefs.jports[0].jd[0].id == JSEM_JOYS + 0);
    CHECK(currprefs.jports[1].jd[0].id == JSEM_MICE);
    check_port_prefs(0, "P6 joystick 1", "P6JOYSTICK1");
    check_port_prefs(1, "P6 mouse", "P6MOUSE1");
    CHECK(getjoystickstate(0) != 0 && getjoystickstate(1) == 0 && getmousestate(0) != 0);
    clear_gp();
    drain();
    setjoystickstate(0, 0, -1, 1); /* left */
    CHECK((JOY0DAT() & 0x0303) == 0x0300);
    setjoystickstate(0, 0, 0, 1);
    const uae_u16 b = JOY1DAT();
    setmousestate(0, 1, 7, 0);
    drain();
    CHECK(((counter_y(JOY1DAT()) - counter_y(b)) & 0xff) == 7);
    setjoybuttonstate(0, 0, 1);
    CHECK(cia_pra_buttons() == 0x80 && gp_has(0, JOYBUTTON_1, 1));
    setjoybuttonstate(0, 0, 0);
    setmousebuttonstate(0, 0, 1);
    CHECK(cia_pra_buttons() == 0x40 && gp_has(1, JOYBUTTON_1, 1));
    setmousebuttonstate(0, 0, 0);
}

static void test_joy_joy()
{
    puts("== joystick 1 in port 0, joystick 2 in port 1");
    configure(JOY, JOY);
    CHECK(getjoystickstate(0) != 0 && getjoystickstate(1) != 0 && getmousestate(0) == 0);
    drain();
    setjoystickstate(0, 1, +1, 1);
    setjoystickstate(1, 0, +1, 1);
    CHECK((JOY0DAT() & 0x0303) == 0x0001); /* down */
    CHECK((JOY1DAT() & 0x0303) == 0x0003); /* right */
    setjoystickstate(0, 1, 0, 1);
    setjoystickstate(1, 0, 0, 1);
}

static void test_none_ports()
{
    puts("== both ports NONE");
    configure(NONE, NONE);
    CHECK(getmousestate(0) == 0 && getjoystickstate(0) == 0 && getjoystickstate(1) == 0);
}

static void test_keyboard()
{
    puts("== keyboard: direct raw codes and the mapped route agree");
    configure(MOUSE, JOY);
    keybuf_init();
    int q[8];
    drain_keys(q, 8);
    /* direct: what the bridge does */
    inputdevice_do_keyboard(0x45, 1);
    inputdevice_do_keyboard(0x45, 0);
    CHECK(drain_keys(q, 8) == 2 && q[0] == 0x8a && q[1] == 0x8b);
    /* the mapped route through the default table */
    CHECK(inputdevice_iskeymapped(0, 0x45) != 0);
    CHECK(inputdevice_translatekeycode(0, 0x45, 1, false) == 1);
    CHECK(inputdevice_translatekeycode(0, 0x45, 0, false) == 1);
    CHECK(drain_keys(q, 8) == 2 && q[0] == 0x8a && q[1] == 0x8b);

    const unsigned before_cdtv = g_cdtv_queries;
    for (int code = 0; code < 128; ++code) {
        if (code == 0x78) /* reset warning: not a key */
            continue;
        /* (inputdevice_iskeymapped() stores event ids in a uae_u8 table, so id 256 = KEY_F11 reads as unmapped: upstream quirk, unused here) */
        inputdevice_do_keyboard(code, 1);
        inputdevice_do_keyboard(code, 0);
        int a[8], b[8];
        int na = drain_keys(a, 8);
        CHECK(inputdevice_translatekeycode(0, code, 1, false) == 1);
        CHECK(inputdevice_translatekeycode(0, code, 0, false) == 1);
        int nb = drain_keys(b, 8);
        CHECK(na == 2 && nb == 2 && a[0] == b[0] && a[1] == b[1]);
        CHECK(a[0] == (((code << 1) | (code >> 7)) & 0xff) && a[1] == (a[0] | 1));
    }
    CHECK(g_cdtv_queries == before_cdtv + 6 * 4);

    /* Caps Lock passes through unchanged: bit down = key down, bit up = key up */
    CHECK(inputdevice_translatekeycode(0, AK_CAPSLOCK, 1, false) == 1);
    CHECK(drain_keys(q, 8) == 1 && q[0] == ((AK_CAPSLOCK << 1) & 0xff));
    CHECK(inputdevice_translatekeycode(0, AK_CAPSLOCK, 0, false) == 1);
    CHECK(drain_keys(q, 8) == 1 && q[0] == (((AK_CAPSLOCK << 1) | 1) & 0xff));
    CHECK(inputdevice_translatekeycode(0, AK_CAPSLOCK, 1, false) == 1);
    CHECK(drain_keys(q, 8) == 1 && q[0] == ((AK_CAPSLOCK << 1) & 0xff)); /* pressed again: down again, no toggle memory */
    inputdevice_translatekeycode(0, AK_CAPSLOCK, 0, false);
    drain_keys(q, 8);

    /* Ctrl + Amiga + Amiga: the engine's reset chord, which ends in release_keys() */
    int r0 = g_reset_calls, u0 = g_uae_reset_calls;
    inputdevice_do_keyboard(AK_CTRL, 1);
    inputdevice_do_keyboard(AK_LAMI, 1);
    inputdevice_do_keyboard(AK_RAMI, 1);
    CHECK(g_reset_calls == r0 + 1);
    inputdevice_do_keyboard(AK_RAMI, 0);
    inputdevice_do_keyboard(AK_LAMI, 0);
    inputdevice_do_keyboard(AK_CTRL, 0);
    CHECK(g_uae_reset_calls >= u0 + 1);
    drain_keys(q, 8);
    /* keyboard still works afterwards */
    inputdevice_do_keyboard(0x10, 1);
    inputdevice_do_keyboard(0x10, 0);
    CHECK(drain_keys(q, 8) == 2);
}


/* the engine's own start-up defaults, i.e. what happens if nobody sets jports:
 * cfgfile.cpp default_prefs(reset) stores "mouse" for port 0 and "kbd1" for port 1 */
static void test_upstream_default_jports()
{
    puts("== upstream default jports (mouse, kbd1)");
    for (int p = 0; p < MAX_JPORTS; ++p)
        for (int d = 0; d < MAX_JPORT_DEVS; ++d)
            changed_prefs.jports[p].jd[d].id = JPORT_NONE, changed_prefs.jports[p].jd[d].idc = {};
    inputdevice_joyport_config_store(&changed_prefs, _T("mouse"), 0, -1, -1, 0, 0);
    inputdevice_joyport_config_store(&changed_prefs, _T("kbd1"), 1, -1, -1, 0, 0);
    inputdevice_fix_prefs(&changed_prefs, false);
    CHECK(changed_prefs.jports[0].jd[0].id == JSEM_MICE);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_KBDLAYOUT); /* numpad joystick, not the P6 joystick */
    inputdevice_updateconfig(&changed_prefs, &currprefs);
    CHECK(getmousestate(0) != 0);
    CHECK(getjoystickstate(0) == 0 && getjoystickstate(1) == 0);
    /* the keyboard is what drives port 1 here: numpad 4 becomes joystick left, no Amiga key */
    keybuf_init();
    int q[8];
    drain_keys(q, 8);
    drain();
    CHECK(inputdevice_translatekeycode(0, AK_NP4, 1, false) == 1);
    CHECK(keys_available() == 0);
    CHECK((JOY1DAT() & 0x0303) == 0x0300);
    inputdevice_translatekeycode(0, AK_NP4, 0, false);
    /* the direct raw-code route of the bridge bypasses that mapping entirely */
    inputdevice_do_keyboard(AK_NP4, 1);
    CHECK(drain_keys(q, 8) == 1 && q[0] == (AK_NP4 << 1));
    inputdevice_do_keyboard(AK_NP4, 0);
    drain_keys(q, 8);
}

/* acquire / unacquire as inputdevice.cpp drives them, then a re-enumeration */
static void test_acquire_and_devicechange()
{
    puts("== acquire, unacquire, device change");
    configure(MOUSE, JOY);
    inputdevice_acquire(TRUE);
    inputdevice_unacquire();
    inputdevice_acquire(FALSE);
    inputdevice_unacquire(0);
    inputdevice_acquire(TRUE);
    /* devices are closed and initialised again; ports must come back as they were */
    changed_prefs.jports[0].changed = changed_prefs.jports[1].changed = true;
    inputdevice_devicechange(&changed_prefs);
    CHECK(changed_prefs.jports[0].jd[0].id == JSEM_MICE);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_JOYS + 1);
    inputdevice_updateconfig(&changed_prefs, &currprefs);
    CHECK(getmousestate(0) != 0 && getjoystickstate(1) != 0 && getjoystickstate(0) == 0);
    inputdevice_unacquire();
    /* names stored in prefs (as a saved config would hold them) find the same devices again */
    for (int p = 0; p < 2; ++p)
        changed_prefs.jports[p].changed = true;
    inputdevice_fix_prefs(&changed_prefs, true);
    CHECK(changed_prefs.jports[0].jd[0].id == JSEM_MICE);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_JOYS + 1);
    /* "joy0", "joy1", "mouse0" are the strings a config or the bridge can use */
    CHECK(inputdevice_joyport_config(&changed_prefs, _T("joy0"), NULL, 0, -1, -1, 0, 0, false) == 1);
    CHECK(changed_prefs.jports[0].jd[0].id == JSEM_JOYS + 0);
    CHECK(inputdevice_joyport_config(&changed_prefs, _T("joy1"), NULL, 1, -1, -1, 0, 0, false) == 1);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_JOYS + 1);
    CHECK(inputdevice_joyport_config(&changed_prefs, _T("mouse0"), NULL, 1, -1, -1, 0, 0, false) == 1);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_MICE);
    /* by the names the devices report */
    CHECK(inputdevice_joyport_config(&changed_prefs, _T("P6 joystick 2"), _T("P6JOYSTICK2"), 1, -1, -1, 1, 0, false) == 1);
    CHECK(changed_prefs.jports[1].jd[0].id == JSEM_JOYS + 1);
    /* the same device in both ports is refused by validation: only one mouse exists */
    changed_prefs.jports[0].jd[0].id = JSEM_MICE;
    changed_prefs.jports[1].jd[0].id = JSEM_MICE;
    inputdevice_validate_jports(&changed_prefs, 1, NULL);
    CHECK(changed_prefs.jports[0].jd[0].id != changed_prefs.jports[1].jd[0].id || changed_prefs.jports[1].jd[0].id < 0);
}

int main()
{
    /* the engine's own defaults; this is where the key translation table is needed */
    for (int p = 0; p < MAX_JPORTS; ++p)
        for (int d = 0; d < MAX_JPORT_DEVS; ++d)
            g_seed.jports[p].jd[d].id = JPORT_NONE;
    inputdevice_default_prefs(&g_seed);
    memcpy(&currprefs, &g_seed, sizeof g_seed);
    memcpy(&changed_prefs, &g_seed, sizeof g_seed);
    puts("default prefs ok");

    test_mouse0_joy1();
    test_joy0_mouse1();
    test_joy_joy();
    test_none_ports();
    test_keyboard();
    test_upstream_default_jports();
    test_acquire_and_devicechange();
    CHECK(lightpen_cx[0] == -1 && lightpen_cx[1] == -1);
    CHECK(lightpen_cy[0] == -1 && lightpen_cy[1] == -1);
    puts("engine_harness: OK");
    return 0;
}
