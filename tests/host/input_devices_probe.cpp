/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Probe for input_devices.cpp. Links the object as-is plus stand-ins for the
 * three symbols that live outside the input device layer (currprefs,
 * inputdevice_sparecopy, inputdevice_setkeytranslation). The oracle for every
 * mapping is an events[] table built from inputevents.def exactly the way
 * inputdevice.cpp builds its own, plus the od-win32 DirectInput tables typed in
 * again with DIK numbers.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "inputdevice.h"
#include "keyboard.h"

#include <stdio.h>
#include <stdlib.h>

#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) {                                                         \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            abort();                                                           \
        }                                                                      \
    } while (0)

/* ---- oracle: the engine's events[] ---------------------------------- */

/* private to inputdevice.cpp, same values there */
#define IE_INVERT 0x80
#define IE_CDTV 0x100

#define DEFEVENT(A, B, C, D, E, F) {_T(#A), B, NULL, C, D, E, F, 0 },
#define DEFEVENT2(A, B, B2, C, D, E, F, G) {_T(#A), B, B2, C, D, E, F, G },
#define DEFEVENTKB(A, B, C, F, PC) {_T(#A), B, NULL, C, 0, 0, F, 0, PC },
static const struct inputevent events[] = {
    {0, 0, 0, AM_K, 0, 0, 0, 0, 0},
#include "inputevents.def"
    {0, 0, 0, 0, 0, 0, 0, 0, 0}
};
#undef DEFEVENT
#undef DEFEVENT2
#undef DEFEVENTKB

static const struct inputevent *ev(int id)
{
    CHECK(id > 0 && id < int(sizeof events / sizeof events[0]) - 1);
    return &events[id];
}

static bool named(int id, const char *confname)
{
    return strcmp(ev(id)->confname, confname) == 0;
}

/* ---- stand-ins for symbols outside this file ------------------------ */

struct uae_prefs currprefs;

/* declared in inputdevice.cpp itself, not in a header */
extern int tablet_log;

static uae_input_device_kbr_default **g_trans;
static int **g_kbmaps;
static int g_settrans_calls;

void inputdevice_setkeytranslation(struct uae_input_device_kbr_default **trans, int **kbmaps)
{
    g_trans = trans;
    g_kbmaps = kbmaps;
    ++g_settrans_calls;
}

struct SpareCall {
    const uae_input_device *uid;
    int num, sub;
};
static SpareCall g_spare[64];
static int g_spare_calls;

void inputdevice_sparecopy(struct uae_input_device *uid, int num, int sub)
{
    if (g_spare_calls < 64)
        g_spare[g_spare_calls] = { uid, num, sub };
    ++g_spare_calls;
}

/* ---- helpers --------------------------------------------------------- */

static uae_input_device g_mice[MAX_INPUT_DEVICES];
static uae_input_device g_joys[MAX_INPUT_DEVICES];
static uae_input_device g_backup[MAX_INPUT_DEVICES];

static void clear_all()
{
    memset(g_mice, 0, sizeof g_mice);
    memset(g_joys, 0, sizeof g_joys);
    g_spare_calls = 0;
    currprefs.input_advancedmultiinput = false;
}

static int mapped_count(const uae_input_device *uid)
{
    int n = 0;
    for (int d = 0; d < MAX_INPUT_DEVICES; ++d)
        for (int s = 0; s < MAX_INPUT_DEVICE_EVENTS; ++s)
            for (int k = 0; k < MAX_INPUT_SUB_EVENT_ALL; ++k)
                if (uid[d].eventid[s][k] != 0)
                    ++n;
    return n;
}

static bool untouched(const uae_input_device *uid)
{
    return memcmp(uid, g_backup, sizeof g_backup) == 0;
}

static void snapshot(const uae_input_device *uid)
{
    memcpy(g_backup, uid, sizeof g_backup);
}

static bool all_ascii_printable(const char *s)
{
    if (!s || !*s)
        return false;
    for (; *s; ++s)
        if (*s < 0x20 || *s > 0x7e)
            return false;
    return true;
}

static const struct inputdevice_functions *const kinds[3] = {
    &inputdevicefunc_keyboard, &inputdevicefunc_mouse, &inputdevicefunc_joystick,
};

/* ---- tests ----------------------------------------------------------- */

static void test_counts_and_names()
{
    CHECK(inputdevicefunc_keyboard.get_num() == 1);
    CHECK(inputdevicefunc_mouse.get_num() == 1);
    CHECK(inputdevicefunc_joystick.get_num() == 2);
    CHECK(inputdevicefunc_keyboard.get_num() <= MAX_INPUT_DEVICES);

    CHECK(strcmp(inputdevicefunc_keyboard.get_friendlyname(0), "P6 keyboard") == 0);
    CHECK(strcmp(inputdevicefunc_mouse.get_friendlyname(0), "P6 mouse") == 0);
    CHECK(strcmp(inputdevicefunc_joystick.get_friendlyname(0), "P6 joystick 1") == 0);
    CHECK(strcmp(inputdevicefunc_joystick.get_friendlyname(1), "P6 joystick 2") == 0);
    CHECK(strcmp(inputdevicefunc_keyboard.get_uniquename(0), "P6KEYBOARD1") == 0);
    CHECK(strcmp(inputdevicefunc_mouse.get_uniquename(0), "P6MOUSE1") == 0);
    CHECK(strcmp(inputdevicefunc_joystick.get_uniquename(0), "P6JOYSTICK1") == 0);
    CHECK(strcmp(inputdevicefunc_joystick.get_uniquename(1), "P6JOYSTICK2") == 0);

    /* matchdevices() (inputdevice.cpp) cuts names at the first ' ' or '{' and
     * compares the heads, so unique names must contain neither and stay unique
     * across every device of every kind. */
    const char *uniques[8];
    const char *friendlies[8];
    int n = 0;
    for (const auto *k : kinds) {
        for (int i = 0; i < k->get_num(); ++i) {
            const char *u = k->get_uniquename(i);
            const char *f = k->get_friendlyname(i);
            CHECK(all_ascii_printable(u) && all_ascii_printable(f));
            CHECK(!strchr(u, ' ') && !strchr(u, '{'));
            CHECK(strlen(u) < MAX_JPORT_CONFIG && strlen(f) < MAX_JPORT_NAME);
            CHECK(k->get_uniquename(i) == u && k->get_friendlyname(i) == f); /* stable */
            for (int j = 0; j < n; ++j) {
                CHECK(strcmp(uniques[j], u) != 0);
                CHECK(strcmp(friendlies[j], f) != 0);
            }
            uniques[n] = u;
            friendlies[n] = f;
            ++n;
        }
    }
    CHECK(n == 4);

    for (const auto *k : kinds) {
        CHECK(k->get_friendlyname(-1) == nullptr && k->get_uniquename(-1) == nullptr);
        CHECK(k->get_friendlyname(k->get_num()) == nullptr);
        CHECK(k->get_uniquename(k->get_num()) == nullptr);
        CHECK(k->get_friendlyname(MAX_INPUT_DEVICES) == nullptr);
    }
}

static void test_lifecycle()
{
    for (const auto *k : kinds) {
        for (int round = 0; round < 3; ++round) {
            CHECK(k->init() == 1);
            for (int i = -1; i < MAX_INPUT_DEVICES; ++i) {
                const int ok = (i == -1 || i < k->get_num()) ? 1 : 0;
                CHECK(k->acquire(i, 0) == ok);
                CHECK(k->acquire(i, 1) == ok);
                k->unacquire(i);
                CHECK(k->get_flags(i) == 0);
            }
            CHECK(k->acquire(-2, 0) == 0);
            CHECK(k->acquire(MAX_INPUT_DEVICES + 5, 0) == 0);
            k->read();
            k->close();
        }
    }
    /* keyboard init is what installs the translation table */
    g_trans = nullptr;
    g_kbmaps = nullptr;
    g_settrans_calls = 0;
    CHECK(inputdevicefunc_keyboard.init() == 1);
    CHECK(g_settrans_calls == 1 && g_trans && g_kbmaps);
    uae_input_device_kbr_default **t1 = g_trans;
    int **m1 = g_kbmaps;
    CHECK(inputdevicefunc_keyboard.init() == 1);
    CHECK(g_trans == t1 && g_kbmaps == m1); /* idempotent, same constant tables */
    /* mouse and joystick init must not touch the keyboard translation */
    CHECK(inputdevicefunc_mouse.init() == 1 && inputdevicefunc_joystick.init() == 1);
    CHECK(g_settrans_calls == 2);
}

static void test_widgets()
{
    const struct inputdevice_functions &kb = inputdevicefunc_keyboard;
    CHECK(kb.get_widget_num(0) == 128);
    CHECK(kb.get_widget_num(1) == 0 && kb.get_widget_num(-1) == 0);
    for (int type = 0; type <= IDEV_WIDGET_KEY; ++type)
        CHECK(kb.get_widget_first(0, type) == 0);
    for (int n = 0; n < 128; ++n) {
        char name[32];
        memset(name, 'x', sizeof name);
        uae_u32 code = 0xdeadbeef;
        CHECK(kb.get_widget_type(0, n, name, &code) == IDEV_WIDGET_KEY);
        CHECK(code == uae_u32(n));
        CHECK(memchr(name, 0, sizeof name)); /* fits a 32 byte buffer, NUL included */
        CHECK(name[0] != 0);
        /* the name is the one of the KEY_* event of that raw code */
        const struct inputevent *first = nullptr;
        for (int e = 1; events[e].confname; ++e) {
            if (!strncmp(events[e].confname, "KEY_", 4) && !(events[e].allow_mask & AM_INFO) && events[e].data == n) {
                first = &events[e];
                break;
            }
        }
        CHECK(first && strcmp(first->name, name) == 0);
        CHECK(kb.get_widget_type(0, n, nullptr, nullptr) == IDEV_WIDGET_KEY);
    }
    {
        char name[8] = "junk";
        uae_u32 code = 7;
        CHECK(kb.get_widget_type(0, 128, name, &code) == IDEV_WIDGET_NONE && name[0] == 0 && code == 0);
        code = 7;
        CHECK(kb.get_widget_type(0, -1, name, &code) == IDEV_WIDGET_NONE && code == 0);
        CHECK(kb.get_widget_type(1, 0, name, &code) == IDEV_WIDGET_NONE);
        CHECK(kb.get_widget_type(-1, 0, nullptr, nullptr) == IDEV_WIDGET_NONE);
    }
    {
        char name[32];
        uae_u32 code;
        kb.get_widget_type(0, AK_ESC, name, &code);
        CHECK(strcmp(name, "ESC") == 0 && code == 0x45);
        kb.get_widget_type(0, AK_CAPSLOCK, name, &code);
        CHECK(strcmp(name, "Caps Lock") == 0 && code == 0x62);
        kb.get_widget_type(0, AK_F1, name, &code);
        CHECK(strcmp(name, "F1") == 0);
    }

    struct { const struct inputdevice_functions *f; int count; } pads[] = {
        { &inputdevicefunc_mouse, 1 }, { &inputdevicefunc_joystick, 2 },
    };
    for (const auto &p : pads) {
        const struct inputdevice_functions &f = *p.f;
        for (int d = 0; d < p.count; ++d) {
            CHECK(f.get_widget_num(d) == 5);
            CHECK(f.get_widget_first(d, IDEV_WIDGET_AXIS) == 0);
            CHECK(f.get_widget_first(d, IDEV_WIDGET_BUTTON) == 2);
            CHECK(f.get_widget_first(d, IDEV_WIDGET_BUTTONAXIS) == 5);
            CHECK(f.get_widget_first(d, IDEV_WIDGET_KEY) == -1);
            static const int types[5] = { IDEV_WIDGET_AXIS, IDEV_WIDGET_AXIS, IDEV_WIDGET_BUTTON, IDEV_WIDGET_BUTTON, IDEV_WIDGET_BUTTON };
            for (int n = 0; n < 5; ++n) {
                char name[32];
                uae_u32 code = 99;
                CHECK(f.get_widget_type(d, n, name, &code) == types[n]);
                CHECK(all_ascii_printable(name) && code == uae_u32(n));
                CHECK(f.get_widget_type(d, n, nullptr, nullptr) == types[n]);
            }
            char name[8] = "junk";
            uae_u32 code = 5;
            CHECK(f.get_widget_type(d, 5, name, &code) == IDEV_WIDGET_NONE && name[0] == 0 && code == 0);
            CHECK(f.get_widget_type(d, -1, nullptr, nullptr) == IDEV_WIDGET_NONE);
        }
        CHECK(f.get_widget_num(p.count) == 0 && f.get_widget_num(-1) == 0);
        CHECK(f.get_widget_type(p.count, 0, nullptr, nullptr) == IDEV_WIDGET_NONE);
        /* slots inputdevice.cpp derives: axis n -> ID_AXIS_OFFSET + n, button n -> n */
        CHECK(ID_AXIS_OFFSET + 1 < MAX_INPUT_DEVICE_EVENTS && ID_BUTTON_OFFSET + 2 < ID_AXIS_OFFSET);
    }
}

static void test_default_keyboard()
{
    CHECK(input_get_default_keyboard(-1) == 0);
    CHECK(input_get_default_keyboard(0) == 1);
    CHECK(input_get_default_keyboard(1) == 0);
    CHECK(input_get_default_keyboard(MAX_INPUT_DEVICES - 1) == 0);
}

static const uae_input_device_kbr_default *find_default(const uae_input_device_kbr_default *t, int code)
{
    for (int i = 0; t[i].scancode >= 0; ++i)
        if (t[i].scancode == code)
            return &t[i];
    return nullptr;
}

static void test_key_translation()
{
    CHECK(inputdevicefunc_keyboard.init() == 1);
    CHECK(g_trans && g_kbmaps);
    /* input_keyboard_type picks the table: KBTYPE_AMIGA, KBTYPE_PC1, KBTYPE_PC2 */
    for (int k = KBTYPE_AMIGA; k <= KBTYPE_PC2; ++k)
        CHECK(g_trans[k] != nullptr);

    const uae_input_device_kbr_default *t = g_trans[KBTYPE_AMIGA];
    int n = 0, prev = -1;
    for (; t[n].scancode >= 0; ++n) {
        CHECK(t[n].scancode > prev && t[n].scancode < 128);
        prev = t[n].scancode;
        const int evt = t[n].node[0].evt;
        CHECK(evt > 0 && evt < INPUTEVENT_SPC_LAST); /* set_kbr_default_event() range check */
        CHECK(!strncmp(ev(evt)->confname, "KEY_", 4));
        CHECK(!(ev(evt)->allow_mask & AM_INFO));
        CHECK(ev(evt)->data == t[n].scancode);       /* KEY events carry the Amiga raw code */
        CHECK(t[n].node[0].flags == 0);              /* no toggle, no qualifier, no autofire */
        for (int s = 1; s < MAX_INPUT_SUB_EVENT; ++s)
            CHECK(t[n].node[s].evt == 0 && t[n].node[s].flags == 0);
    }
    CHECK(n == 128 && t[n].scancode == -1);
    CHECK(g_trans[KBTYPE_PC1] == t && g_trans[KBTYPE_PC2] == t);

    CHECK(find_default(t, 0x45)->node[0].evt == INPUTEVENT_KEY_ESC);
    CHECK(named(find_default(t, 0x45)->node[0].evt, "KEY_ESC"));
    CHECK(named(find_default(t, 0x62)->node[0].evt, "KEY_CAPS_LOCK"));
    CHECK(find_default(t, 0x62)->node[0].flags == 0);
    CHECK(named(find_default(t, 0x63)->node[0].evt, "KEY_CTRL"));
    CHECK(named(find_default(t, 0x60)->node[0].evt, "KEY_SHIFT_LEFT"));
    CHECK(named(find_default(t, 0x66)->node[0].evt, "KEY_AMIGA_LEFT"));
    CHECK(named(find_default(t, 0x67)->node[0].evt, "KEY_AMIGA_RIGHT"));
    CHECK(named(find_default(t, 0x00)->node[0].evt, "KEY_BACKQUOTE")); /* raw code 0 is a real key */
    CHECK(named(find_default(t, 0x2b)->node[0].evt, "KEY_NUMBERSIGN")); /* first of the two rows sharing 0x2b */
    CHECK(named(find_default(t, 0x50)->node[0].evt, "KEY_F1"));
    CHECK(named(find_default(t, 0x7f)->node[0].evt, "KEY_7F"));

    /* every widget's scancode has a default event (what set_kbr_default() needs) */
    for (int w = 0; w < inputdevicefunc_keyboard.get_widget_num(0); ++w) {
        uae_u32 code = 0;
        CHECK(inputdevicefunc_keyboard.get_widget_type(0, w, nullptr, &code) == IDEV_WIDGET_KEY);
        CHECK(find_default(t, int(code)));
    }
}

/* od-win32/keyboard_win32.cpp kb_* typed again with DIK_* values, translated
 * to Amiga raw codes through the DEFEVENTKB "PC" column of inputevents.def. */
static int dik_to_amiga(int dik)
{
    /* DIK extended codes are 0x80|scan, the .def spells them 0xe0scan */
    int pc = (dik & 0x80) ? (0xe000 | (dik & 0x7f)) : dik;
    /* keys the .def does not list under their own PC code */
    switch (dik) {
    case 0x9d: return AK_CTRL;   /* DIK_RCONTROL: the Amiga has one Ctrl */
    case 0xb5: return AK_NPDIV;  /* DIK_DIVIDE (.def lists NP_DIV under 0x35) */
    }
    for (int e = 1; events[e].confname; ++e)
        if (!strncmp(events[e].confname, "KEY_", 4) && !(events[e].allow_mask & AM_INFO) && events[e].data2 == pc)
            return events[e].data;
    return -2;
}

static void test_kbmaps()
{
    enum {
        N0 = 0x52, N1 = 0x4f, N2 = 0x50, N3 = 0x51, N4 = 0x4b, N5 = 0x4c, N6 = 0x4d, N7 = 0x47, N8 = 0x48, N9 = 0x49,
        DEC = 0x53, NENTER = 0x9c, LEFT = 0xcb, RIGHT = 0xcd, UP = 0xc8, DOWN = 0xd0,
        RCTRL = 0x9d, RMENU = 0xb8, RSHIFT = 0x36, LMENU = 0x38, LSHIFT = 0x2a, LCTRL = 0x1d,
        A = 0x1e, D = 0x20, W = 0x11, S = 0x1f, DIVIDE = 0xb5, SUBTRACT = 0x4a, MULTIPLY = 0x37,
        F2 = 0x3c, K1 = 0x02, K2 = 0x03, K5 = 0x06, K6 = 0x07,
    };
    static const int np[] = { N4, -1, N6, -1, N8, -1, N2, -1, N0, N5, -1, DEC, -1, NENTER, -1, -1 };
    static const int ck[] = { LEFT, -1, RIGHT, -1, UP, -1, DOWN, -1, RCTRL, RMENU, -1, RSHIFT, -1, -1 };
    static const int se[] = { A, -1, D, -1, W, -1, S, -1, LMENU, -1, LSHIFT, -1, -1 };
    static const int ck3[] = { LEFT, -1, RIGHT, -1, UP, -1, DOWN, -1, RCTRL, -1, RSHIFT, -1, RMENU, -1, -1 };
    static const int se3[] = { A, -1, D, -1, W, -1, S, -1, LMENU, -1, LSHIFT, -1, LCTRL, -1, -1 };
    static const int cd_np[] = { N4, -1, N6, -1, N8, -1, N2, -1, N0, N5, N1, -1, DEC, N3, -1, N7, -1, N9, -1, DIVIDE, -1, SUBTRACT, -1, MULTIPLY, -1, -1 };
    static const int cd_ck[] = { LEFT, -1, RIGHT, -1, UP, -1, DOWN, -1, RCTRL, RMENU, RSHIFT, -1, N7, -1, N9, -1, DIVIDE, -1, SUBTRACT, -1, MULTIPLY, -1, -1 };
    static const int cd_se[] = { A, -1, D, -1, W, -1, S, -1, -1, LMENU, -1, LSHIFT, -1, N7, -1, N9, -1, DIVIDE, -1, SUBTRACT, -1, MULTIPLY, -1, -1 };
    static const int arcadia[] = { F2, -1, K1, -1, K2, -1, K5, -1, K6, -1, -1 };
    static const int cdtv[] = { N1, -1, N3, -1, N7, -1, N9, -1, -1 };
    static const int *const expect[11] = { np, ck, se, np, ck3, se3, cd_np, cd_ck, cd_se, arcadia, cdtv };

    for (int m = 0; m < 11; ++m) {
        const int *want = expect[m];
        const int *got = g_kbmaps[m];
        CHECK(got);
        int i = 0;
        for (;; ++i) {
            /* both lists end at the same place */
            if (want[i] == -1 && want[i + 1] == -1) {
                CHECK(got[i] == -1 && got[i + 1] == -1);
                break;
            }
            if (want[i] == -1) {
                CHECK(got[i] == -1);
            } else {
                const int amiga = dik_to_amiga(want[i]);
                CHECK(amiga >= 0);
                CHECK(got[i] == amiga);
            }
            CHECK(i < 40);
        }
        /* every code is a real widget scancode */
        for (int j = 0; got[j] != -1 || got[j + 1] != -1; ++j)
            CHECK(got[j] == -1 || (got[j] >= 0 && got[j] < 128));
    }
}

static void expect_map(const uae_input_device &d, int slot, const char *confname, int unit, int port)
{
    const int id = d.eventid[slot][0];
    CHECK(id > 0);
    CHECK(named(id, confname));
    CHECK(ev(id)->unit == unit);
    CHECK(d.port[slot][0] == port + 1);
}

static void test_mouse_defaults()
{
    /* mouse on port 0 (the standard mouse port) */
    clear_all();
    CHECK(input_get_default_mouse(g_mice, 0, 0, 0, false, true, false) == 1);
    expect_map(g_mice[0], ID_AXIS_OFFSET + 0, "MOUSE1_HORIZ", 1, 0);
    expect_map(g_mice[0], ID_AXIS_OFFSET + 1, "MOUSE1_VERT", 1, 0);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 0, "JOY1_FIRE_BUTTON", 1, 0);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 1, "JOY1_2ND_BUTTON", 1, 0);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 2, "JOY1_3RD_BUTTON", 1, 0);
    CHECK(ev(g_mice[0].eventid[ID_AXIS_OFFSET][0])->type & 8);         /* relative "real mouse" axis */
    CHECK(ev(g_mice[0].eventid[ID_BUTTON_OFFSET][0])->data == JOYBUTTON_1);
    CHECK(ev(g_mice[0].eventid[ID_BUTTON_OFFSET + 1][0])->data == JOYBUTTON_2);
    CHECK(ev(g_mice[0].eventid[ID_BUTTON_OFFSET + 2][0])->data == JOYBUTTON_3);
    CHECK(mapped_count(g_mice) == 5 && mapped_count(g_joys) == 0);
    CHECK(g_spare_calls == 0);
    for (int s = 0; s < MAX_INPUT_DEVICE_EVENTS; ++s)
        CHECK(g_mice[0].flags[s][0] == 0);

    /* the same in the game ports panel style (gp): user mappings are parked first */
    clear_all();
    CHECK(input_get_default_mouse(g_mice, 0, 1, 0, true, true, false) == 1);
    CHECK(g_spare_calls == 5);
    for (int c = 0; c < 5; ++c)
        CHECK(g_spare[c].uid == &g_mice[0] && g_spare[c].sub == 0);
    expect_map(g_mice[0], ID_AXIS_OFFSET + 0, "MOUSE2_HORIZ", 2, 1);
    expect_map(g_mice[0], ID_AXIS_OFFSET + 1, "MOUSE2_VERT", 2, 1);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 0, "JOY2_FIRE_BUTTON", 2, 1);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 1, "JOY2_2ND_BUTTON", 2, 1);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 2, "JOY2_3RD_BUTTON", 2, 1);

    /* wheel request changes nothing: there is no wheel widget */
    clear_all();
    input_get_default_mouse(g_mice, 0, 0, 0, false, false, false);
    CHECK(mapped_count(g_mice) == 5);

    /* autofire lands on the fire button only */
    struct { int af; uae_u64 flags; } afs[] = {
        { 0, 0 },
        { JPORT_AF_NORMAL, ID_FLAG_AUTOFIRE },
        { JPORT_AF_TOGGLE, ID_FLAG_AUTOFIRE | ID_FLAG_TOGGLE },
        { JPORT_AF_ALWAYS, ID_FLAG_AUTOFIRE | ID_FLAG_INVERTTOGGLE },
        { JPORT_AF_TOGGLENOAF, ID_FLAG_AUTOFIRE | ID_FLAG_INVERT },
    };
    for (const auto &a : afs) {
        clear_all();
        input_get_default_mouse(g_mice, 0, 0, a.af, false, true, false);
        CHECK(g_mice[0].flags[ID_BUTTON_OFFSET + 0][0] == a.flags);
        CHECK(g_mice[0].flags[ID_BUTTON_OFFSET + 1][0] == 0 && g_mice[0].flags[ID_BUTTON_OFFSET + 2][0] == 0);
        CHECK(g_mice[0].flags[ID_AXIS_OFFSET + 0][0] == 0);
    }

    /* things it must refuse leave the uid array untouched and return 0 */
    clear_all();
    snapshot(g_mice);
    CHECK(input_get_default_mouse(g_mice, 1, 0, 0, false, true, false) == 0);   /* one mouse only */
    CHECK(input_get_default_mouse(g_mice, -1, 0, 0, false, true, false) == 0);
    CHECK(input_get_default_mouse(g_mice, MAX_INPUT_DEVICES, 0, 0, false, true, false) == 0);
    CHECK(input_get_default_mouse(g_mice, 0, 2, 0, false, true, false) == 0);   /* parallel adapter port */
    CHECK(input_get_default_mouse(g_mice, 0, 3, 0, false, true, false) == 0);
    CHECK(input_get_default_mouse(g_mice, 0, -1, 0, false, true, false) == 0);
    CHECK(input_get_default_mouse(nullptr, 0, 0, 0, false, true, false) == 0);
    CHECK(untouched(g_mice) && g_spare_calls == 0);

    /* joystick borrowed as a mouse: the uid array is the joysticks', two exist */
    clear_all();
    CHECK(input_get_default_mouse(g_joys, 1, 1, 0, false, true, true) == 0);     /* mapped, not the default one */
    expect_map(g_joys[1], ID_AXIS_OFFSET + 0, "MOUSE2_HORIZ", 2, 1);
    expect_map(g_joys[1], ID_BUTTON_OFFSET + 2, "JOY2_3RD_BUTTON", 2, 1);
    CHECK(mapped_count(g_joys) == 5 && mapped_count(g_mice) == 0);
    clear_all();
    CHECK(input_get_default_mouse(g_joys, 0, 0, 0, false, true, true) == 1);
    snapshot(g_joys);
    CHECK(input_get_default_mouse(g_joys, 2, 0, 0, false, true, true) == 0);
    CHECK(untouched(g_joys));
}

static void test_joystick_defaults()
{
    for (int port = 0; port < 2; ++port) {
        const char *h = port ? "JOY2_HORIZ" : "JOY1_HORIZ";
        const char *v = port ? "JOY2_VERT" : "JOY1_VERT";
        const char *b1 = port ? "JOY2_FIRE_BUTTON" : "JOY1_FIRE_BUTTON";
        const char *b2 = port ? "JOY2_2ND_BUTTON" : "JOY1_2ND_BUTTON";
        const char *b3 = port ? "JOY2_3RD_BUTTON" : "JOY1_3RD_BUTTON";
        for (int mode : { JSEM_MODE_DEFAULT, JSEM_MODE_GAMEPAD }) {
            for (int dev = 0; dev < 2; ++dev) {
                clear_all();
                CHECK(input_get_default_joystick(g_joys, dev, port, 0, mode, false, false, true) == (dev == 0 ? 1 : 0));
                expect_map(g_joys[dev], ID_AXIS_OFFSET + 0, h, port + 1, port);
                expect_map(g_joys[dev], ID_AXIS_OFFSET + 1, v, port + 1, port);
                expect_map(g_joys[dev], ID_BUTTON_OFFSET + 0, b1, port + 1, port);
                expect_map(g_joys[dev], ID_BUTTON_OFFSET + 1, b2, port + 1, port);
                expect_map(g_joys[dev], ID_BUTTON_OFFSET + 2, b3, port + 1, port);
                CHECK(ev(g_joys[dev].eventid[ID_AXIS_OFFSET][0])->type == 0); /* "normal" digital axis */
                CHECK(ev(g_joys[dev].eventid[ID_AXIS_OFFSET][0])->data == (DIR_LEFT | DIR_RIGHT));
                CHECK(ev(g_joys[dev].eventid[ID_AXIS_OFFSET + 1][0])->data == (DIR_UP | DIR_DOWN));
                CHECK(ev(g_joys[dev].eventid[ID_BUTTON_OFFSET][0])->data == JOYBUTTON_1);
                CHECK(mapped_count(g_joys) == 5 && mapped_count(g_mice) == 0);
            }
        }
        /* plain JSEM_MODE_JOYSTICK is the two button stick */
        clear_all();
        input_get_default_joystick(g_joys, 0, port, 0, JSEM_MODE_JOYSTICK, false, false, true);
        expect_map(g_joys[0], ID_BUTTON_OFFSET + 1, b2, port + 1, port);
        CHECK(g_joys[0].eventid[ID_BUTTON_OFFSET + 2][0] == 0 && mapped_count(g_joys) == 4);
    }

    /* gp: parked before overwrite, autofire on the fire button */
    clear_all();
    input_get_default_joystick(g_joys, 0, 1, JPORT_AF_TOGGLE, JSEM_MODE_DEFAULT, true, false, true);
    CHECK(g_spare_calls == 5);
    CHECK(g_joys[0].flags[ID_BUTTON_OFFSET][0] == (ID_FLAG_AUTOFIRE | ID_FLAG_TOGGLE));
    CHECK(g_joys[0].flags[ID_BUTTON_OFFSET + 1][0] == 0);

    /* modes and ports this profile cannot honour map nothing */
    clear_all();
    snapshot(g_joys);
    CHECK(input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_JOYSTICK_CD32, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_MOUSE_CDTV, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_MOUSE, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_LIGHTPEN, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 0, 0, 99, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 2, 0, JSEM_MODE_DEFAULT, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, 3, 0, 0, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 0, -1, 0, JSEM_MODE_DEFAULT, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, 2, 0, 0, JSEM_MODE_DEFAULT, false, false, true) == 0);
    CHECK(input_get_default_joystick(g_joys, -1, 0, 0, JSEM_MODE_DEFAULT, false, false, true) == 0);
    CHECK(input_get_default_joystick(nullptr, 0, 0, 0, JSEM_MODE_DEFAULT, false, false, true) == 0);
    CHECK(untouched(g_joys) && g_spare_calls == 0);

    /* mouse borrowed as a joystick: the uid array is the mice's, one exists */
    clear_all();
    CHECK(input_get_default_joystick(g_mice, 0, 1, 0, JSEM_MODE_DEFAULT, false, true, true) == 1);
    expect_map(g_mice[0], ID_AXIS_OFFSET + 0, "JOY2_HORIZ", 2, 1);
    expect_map(g_mice[0], ID_BUTTON_OFFSET + 2, "JOY2_3RD_BUTTON", 2, 1);
    clear_all();
    snapshot(g_mice);
    CHECK(input_get_default_joystick(g_mice, 1, 0, 0, JSEM_MODE_DEFAULT, false, true, true) == 0);
    CHECK(untouched(g_mice));

    /* analog stick */
    clear_all();
    CHECK(input_get_default_joystick_analog(g_joys, 1, 1, JPORT_AF_NORMAL, false, false, true) == 0);
    expect_map(g_joys[1], ID_AXIS_OFFSET + 0, "JOY2_HORIZ_POT", 2, 1);
    expect_map(g_joys[1], ID_AXIS_OFFSET + 1, "JOY2_VERT_POT", 2, 1);
    expect_map(g_joys[1], ID_BUTTON_OFFSET + 0, "JOY2_LEFT", 2, 1);
    expect_map(g_joys[1], ID_BUTTON_OFFSET + 1, "JOY2_RIGHT", 2, 1);
    expect_map(g_joys[1], ID_BUTTON_OFFSET + 2, "JOY2_UP", 2, 1);
    CHECK(ev(g_joys[1].eventid[ID_AXIS_OFFSET][0])->type & 128); /* analog pot axis */
    CHECK(g_joys[1].flags[ID_BUTTON_OFFSET][0] == ID_FLAG_AUTOFIRE && g_joys[1].flags[ID_BUTTON_OFFSET + 1][0] == 0);
    CHECK(mapped_count(g_joys) == 5);
    clear_all();
    CHECK(input_get_default_joystick_analog(g_joys, 0, 0, 0, false, false, true) == 1);
    expect_map(g_joys[0], ID_AXIS_OFFSET + 0, "JOY1_HORIZ_POT", 1, 0);
    clear_all();
    snapshot(g_joys);
    CHECK(input_get_default_joystick_analog(g_joys, 2, 0, 0, false, false, true) == 0);
    CHECK(input_get_default_joystick_analog(g_joys, 0, 2, 0, false, false, true) == 0);
    CHECK(input_get_default_joystick_analog(nullptr, 0, 0, 0, false, false, true) == 0);
    CHECK(untouched(g_joys));

    /* light pen: needs an absolute pointer, refused without touching anything */
    clear_all();
    snapshot(g_mice);
    for (int sub : { 0, 1 }) {
        CHECK(input_get_default_lightpen(g_mice, 0, 0, 0, false, false, sub) == 0);
        CHECK(input_get_default_lightpen(g_joys, 0, 1, 0, true, true, sub) == 0);
    }
    CHECK(untouched(g_mice) && mapped_count(g_joys) == 0 && g_spare_calls == 0);
}

/* advancedmultiinput: next free sub slot, refuse when all eight are taken */
static void test_advanced_multi_input()
{
    clear_all();
    currprefs.input_advancedmultiinput = true;
    g_joys[0].eventid[ID_BUTTON_OFFSET][0] = 7;
    g_joys[0].eventid[ID_BUTTON_OFFSET][1] = 8;
    input_get_default_joystick(g_joys, 0, 0, JPORT_AF_NORMAL, JSEM_MODE_DEFAULT, false, false, true);
    CHECK(g_joys[0].eventid[ID_BUTTON_OFFSET][2] > 0 && named(g_joys[0].eventid[ID_BUTTON_OFFSET][2], "JOY1_FIRE_BUTTON"));
    CHECK(g_joys[0].flags[ID_BUTTON_OFFSET][2] == ID_FLAG_AUTOFIRE && g_joys[0].flags[ID_BUTTON_OFFSET][0] == 0);
    CHECK(g_joys[0].eventid[ID_BUTTON_OFFSET][0] == 7 && g_joys[0].eventid[ID_BUTTON_OFFSET][1] == 8);
    CHECK(named(g_joys[0].eventid[ID_AXIS_OFFSET][0], "JOY1_HORIZ"));

    clear_all();
    currprefs.input_advancedmultiinput = true;
    for (int s = 0; s < MAX_INPUT_SUB_EVENT; ++s)
        g_joys[0].eventid[ID_BUTTON_OFFSET + 1][s] = 9;
    input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_DEFAULT, false, false, true);
    for (int s = 0; s < MAX_INPUT_SUB_EVENT; ++s)
        CHECK(g_joys[0].eventid[ID_BUTTON_OFFSET + 1][s] == 9); /* full: left alone */
    CHECK(g_joys[0].port[ID_BUTTON_OFFSET + 1][0] == 0);
    currprefs.input_advancedmultiinput = false;
}

/* What inputdevice.cpp does with the mappings: enablejoydevice() turns a
 * device on when it holds an event of the port's joyinputs[] list or a sibling
 * of it in axistable[]. Reproduce that rule for the three port set-ups. */
static bool has_event(const uae_input_device &d, int evt)
{
    for (int s = 0; s < MAX_INPUT_DEVICE_EVENTS; ++s)
        for (int k = 0; k < MAX_INPUT_SUB_EVENT; ++k)
            if (d.eventid[s][k] == evt)
                return true;
    return false;
}

static void test_engine_port_rules()
{
    /* ip_mouse1 / ip_mouse2 / ip_joy1 / ip_joy2 heads and their axistable siblings */
    clear_all();
    input_get_default_mouse(g_mice, 0, 0, 0, false, true, false);
    CHECK(has_event(g_mice[0], INPUTEVENT_JOY1_FIRE_BUTTON) && has_event(g_mice[0], INPUTEVENT_MOUSE1_HORIZ));
    CHECK(has_event(g_mice[0], INPUTEVENT_JOY1_2ND_BUTTON) && has_event(g_mice[0], INPUTEVENT_MOUSE1_VERT));
    clear_all();
    input_get_default_mouse(g_mice, 0, 1, 0, false, true, false);
    CHECK(has_event(g_mice[0], INPUTEVENT_JOY2_FIRE_BUTTON) && has_event(g_mice[0], INPUTEVENT_MOUSE2_HORIZ));
    clear_all();
    input_get_default_joystick(g_joys, 1, 1, 0, JSEM_MODE_DEFAULT, false, false, true);
    CHECK(has_event(g_joys[1], INPUTEVENT_JOY2_FIRE_BUTTON) && has_event(g_joys[1], INPUTEVENT_JOY2_HORIZ) && has_event(g_joys[1], INPUTEVENT_JOY2_VERT));
    clear_all();
    input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_DEFAULT, false, false, true);
    CHECK(has_event(g_joys[0], INPUTEVENT_JOY1_FIRE_BUTTON) && has_event(g_joys[0], INPUTEVENT_JOY1_HORIZ) && has_event(g_joys[0], INPUTEVENT_JOY1_VERT));
    /* mouse in port 0 and joystick in port 1 together: no event is mapped twice */
    clear_all();
    input_get_default_mouse(g_mice, 0, 0, 0, false, true, false);
    input_get_default_joystick(g_joys, 1, 1, 0, JSEM_MODE_DEFAULT, false, false, true);
    for (int s = 0; s < MAX_INPUT_DEVICE_EVENTS; ++s) {
        const int a = g_mice[0].eventid[s][0];
        for (int t = 0; t < MAX_INPUT_DEVICE_EVENTS; ++t)
            CHECK(a == 0 || a != g_joys[1].eventid[t][0]);
    }
    /* joystick in port 0 and mouse in port 1 together */
    clear_all();
    input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_DEFAULT, false, false, true);
    input_get_default_mouse(g_mice, 0, 1, 0, false, true, false);
    for (int s = 0; s < MAX_INPUT_DEVICE_EVENTS; ++s) {
        const int a = g_mice[0].eventid[s][0];
        for (int t = 0; t < MAX_INPUT_DEVICE_EVENTS; ++t)
            CHECK(a == 0 || a != g_joys[0].eventid[t][0]);
    }
    /* the two joysticks in the two ports */
    clear_all();
    input_get_default_joystick(g_joys, 0, 0, 0, JSEM_MODE_DEFAULT, false, false, true);
    input_get_default_joystick(g_joys, 1, 1, 0, JSEM_MODE_DEFAULT, false, false, true);
    CHECK(has_event(g_joys[0], INPUTEVENT_JOY1_HORIZ) && !has_event(g_joys[0], INPUTEVENT_JOY2_HORIZ));
    CHECK(has_event(g_joys[1], INPUTEVENT_JOY2_HORIZ) && !has_event(g_joys[1], INPUTEVENT_JOY1_HORIZ));
}

static void test_target_glue()
{
    CHECK(is_tablet() == 0);
    CHECK(tablet_log == 0);
    int state = 1;
    CHECK(target_checkcapslock(AK_CAPSLOCK, &state) == 0 && state == 1);
    state = 0;
    CHECK(target_checkcapslock(AK_CAPSLOCK, &state) == 0 && state == 0);
    state = -1;
    CHECK(target_checkcapslock(AK_A, &state) == 0 && state == -1);
    target_inputdevice_acquire();
    target_inputdevice_unacquire(false);
    target_inputdevice_unacquire(true);
    release_keys();
    release_keys();
}

int main()
{
    test_counts_and_names();
    test_lifecycle();
    test_widgets();
    test_default_keyboard();
    test_key_translation();
    test_kbmaps();
    test_mouse_defaults();
    test_joystick_defaults();
    test_advanced_multi_input();
    test_engine_port_rules();
    test_target_glue();
    puts("input_devices_probe: OK");
    return 0;
}
