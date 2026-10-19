// Chunks of the devices (docs/adr/0009-state-format.md, manifests/state-inventory.json components rtc-*, cia-*, input-*): P6RT here, P6CI and P6IN as they
// are written. The rules of bridge/state_cpu.cpp apply: a cross-check uses the value val()/get() returns and never the live variable (Validate stores
// nothing), Measure never fails, Save refuses what Validate would refuse, and Apply copies fields: nothing that schedules or fires an event, because the
// scheduler's chunk owns eventtab and eventtab2.
#include "state.h"

#include <stdint.h>

#include "session.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "custom.h"
#include "rtc_time.h"
#include "p6_state_access.h"
#include "input.h"

// What the engine patches add (patches/winuae/0081, 0082, 0084, 0085), declared here as lifecycle.cpp declares p6_cia_session_begin.
extern void p6_rtc_msm_get(uae_u8 out[3]);
extern void p6_rtc_msm_set(const uae_u8 in[3]);
extern void p6_rtc_ricoh_get(uae_u8 out[35]);
extern void p6_rtc_ricoh_set(const uae_u8 in[35]);
extern void p6_cia_get(int num, p6_cia_raw *out);
extern void p6_cia_set(int num, const p6_cia_raw *in);
extern void p6_cia_misc_get(p6_cia_misc_raw *out);
extern void p6_cia_misc_set(const p6_cia_misc_raw *in);
extern void p6_cia_overlay_rebuild(void);
extern bool p6_cia_boundary(const char **why);
extern void p6_input_get(p6_input_raw *out);
extern void p6_input_set(const p6_input_raw *in);
extern bool p6_input_boundary(const char **why);
extern int p6_keybuf_get(uae_u8 *codes, int max);
extern void p6_keybuf_set(const uae_u8 *codes, int n);
extern bool p6_keybuf_boundary(const char **why);
extern int p6_input_engine_released_keys;       // ports/portable/input_devices.cpp: set when the engine cleared its key table, consumed by the next packet

namespace p6_state {

namespace {

// The clock is driven by the scheduler's own counter: ticks per second is the chipset clock times CYCLE_UNIT of the video standard of the session, as
// p6_load gave it to p6_rtc_init (bridge/lifecycle.cpp), and the seed is the configuration's folded seed (the clock keeps 1 for a zero).
uint64_t clock_rate()
{
    const uint64_t chipset = p6_session::session.config.video_standard == P6_VIDEO_NTSC ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
    return chipset * CYCLE_UNIT;
}

uint32_t clock_seed()
{
    return currprefs.seed ? currprefs.seed : 1;
}

// A field of a plain struct that travels as a fixed-width integer in [lo, hi]: Save writes it (and refuses a live value outside the range), Validate
// checks the value read, Apply stores it narrowed. Returns what was read, which is what a cross-check has to use.
template <class W, class L> W field(Archive &a, L &live, long long lo, long long hi, const char *name)
{
    const W v = get<W>(a, static_cast<long long>(live), lo, hi, name);
    if (a.applying() && a.ok()) live = static_cast<L>(v);
    return v;
}

}  // namespace

// P6RT body, little endian, in this order:
//   i64 seconds (Gregorian years 1..9999)  u64 ticks_per_second (the session's)  u64 subsecond (< ticks_per_second)  u32 seed (the session's)
//   u8 msm_d (0, 1, 8 or 9)  u8 msm_e (0..15)  u8 msm_f (0..15)
// The anchor of the clock (the cycle count it was last brought up to) is NOT written. A save brings the clock up to the cycle counter, and Apply anchors
// it to the counter that the scheduler's chunk has just restored, so the clock goes on from the instant it was saved whatever the machine's counter said
// before. p6_rtc_save and p6_rtc_read are never called while applying: with the counter put back they would latch the clock's sticky fault.
// T55a: the RF5C01A (cs_rtc 2) keeps the same calendar prefix, followed by D/E/F, 16 alarm bytes and 16 battery-RAM bytes.
// Its body follows the machine's chip; no existing MSM body changes. E/F are write-only but causal chip state, not discarded.
// A machine with no clock chip (cs_rtc == 0: the A600 of the profile) has nothing to carry: the chunk stays in the table, as for every machine, and its body is empty. Nothing
// of the machine reads the clock service then (only the chip does), so its position is not machine state; the live machine decides whether the body is empty, and a body of
// the other kind is refused by its length. The A500 body below is unchanged.
void visit_rtc(Archive &a)
{
    if (currprefs.cs_rtc == 0) return;
    const bool checks = a.mode() != Mode::Measure;
    p6_rtc_state s = {};
    uint8_t msm[3] = {};
    if (a.mode() == Mode::Save) {
        a.require(currprefs.cs_rtc == 1 || currprefs.cs_rtc == 2, "rtc: only the MSM6242B and RF5C01A are supported");
        a.require(p6_rtc_save(&s) == P6_CLOCK_OK, "rtc: the clock service failed");
        p6_rtc_msm_get(msm);
    }
    const uint64_t rate = clock_rate();
    const int64_t seconds = get<int64_t>(a, s.clock.seconds, P6_CIVIL_MIN_SECONDS, P6_CIVIL_MAX_SECONDS, "rtc.seconds");
    const uint64_t ticks = get<uint64_t>(a, s.clock.ticks_per_second, rate, rate, "rtc.ticks_per_second");
    const uint64_t subsecond = get<uint64_t>(a, s.clock.subsecond, 0, rate - 1, "rtc.subsecond");
    const uint32_t seed = get<uint32_t>(a, s.clock.seed, clock_seed(), clock_seed(), "rtc.seed");
    uint8_t chosen[35] = {};
    const bool ricoh = currprefs.cs_rtc == 2;
    if (ricoh) {
        uint8_t live[35] = {};
        if (a.mode() == Mode::Save) p6_rtc_ricoh_get(live);
        for (unsigned i = 0; i < 3; ++i)
            chosen[i] = get<uint8_t>(a, live[i], 0, 15, i == 0 ? "rtc.ricoh_d" : i == 1 ? "rtc.ricoh_e" : "rtc.ricoh_f");
        // put_clock_ricoh's masks, including the inaccessible tail of each bank (reset to zero).
        static const uint8_t alarm_mask[16] = {0, 0, 15, 7, 15, 3, 7, 15, 3, 0, 1, 3, 0, 0, 0, 0};
        for (unsigned i = 0; i < 16; ++i)
            chosen[3 + i] = get<uint8_t>(a, live[3 + i], 0, alarm_mask[i], "rtc.ricoh_alarm");
        for (unsigned i = 0; i < 16; ++i)
            chosen[19 + i] = get<uint8_t>(a, live[19 + i], 0, i < 13 ? 255 : 0, "rtc.ricoh_ram");
    } else {
        chosen[0] = get<uint8_t>(a, msm[0], 0, 9, "rtc.msm_d");
        if (checks) a.require(chosen[0] == 0 || chosen[0] == 1 || chosen[0] == 8 || chosen[0] == 9, "rtc.msm_d: a bit the MSM6242B does not keep");
        chosen[1] = get<uint8_t>(a, msm[1], 0, 15, "rtc.msm_e");
        chosen[2] = get<uint8_t>(a, msm[2], 0, 15, "rtc.msm_f");
    }
    if (a.applying() && a.ok()) {
        p6_rtc_state next = {};
        next.clock.seconds = seconds;
        next.clock.ticks_per_second = ticks;
        next.clock.subsecond = subsecond;
        next.clock.seed = seed;
        next.clock.version = 1;
        next.cycle = get_cycles();                      // the scheduler's chunk (index 1) has put the counter back
        if (p6_rtc_restore(&next) != P6_CLOCK_OK) a.fail("rtc: the clock refused a state that was validated");
        if (ricoh) p6_rtc_ricoh_set(chosen);
        else p6_rtc_msm_set(chosen);
    }
}

// P6CI body, little endian, in this order. Per CIA (A, then B; 56 bytes):
//   u8 pra prb dra drb | per timer (A, B): u16 timer, u16 latch, u8 inputpipe (0..3), u32 loaddelay, u8 preovfl (0..1), u8 cr |
//   u32 tod tol alarm (<= 0xFFFFFF)  u8 tlatch todon (0..1)  i32 tod_event_state (0..4)  i32 tod_offset (-1..4095) |
//   u8 icr1 (0x40 needs 0x80, and 0x20 needs both)  u8 icr2 (<= 0x1F)  u8 icr_change (0..1)  u8 imask (<= 0x7F) | u8 sdr sdr_buf  u8 sdr_load (0..1)  u8 sdr_cnt (0..16)
// then the rest of cia.cpp (31 bytes): u8 cia_cycle_accurate  i32 kbstate (0..3)  i32 kblostsynccnt (0..1<<20)  u8 internaleclockphase (0..19)  u8 led
// i16 led_old_brightness (-1..255)  i64 led_age (>= 0)  i32 led_cycles_on led_cycles_off (>= 0)  u8 powerled (0..1)  u8 powerled_brightness.
// The timers are the raw counters, in the epoch of eventtab[ev_cia].oldcycles that the scheduler's chunk restores, and not the visible values (timer minus
// passed, a cache that every read recomputes). led_cycle is an instant of the cycle counter: it travels as the age against it, and Apply puts it back
// against the counter that the scheduler's chunk has restored. kbcode, kbhandshakestart, passed, timerval_prev and the rest of what the analysis found
// to be written before it is read are not state.
// Apply copies fields: nothing that schedules an event or raises an interrupt (CIA_update, CIA_calctimers, CIA_check_ICR, event2_newevent_*), because the
// scheduler's chunk owns the event tables. The one thing derived is the ROM overlay at address 0, made again from PRA and DDRA by rebuild_cia after every
// chunk is applied.
void visit_cia(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    p6_cia_raw ci[2] = {};
    p6_cia_misc_raw m = {};
    int64_t age = 0;
    if (a.mode() == Mode::Save) {
        const char *why = nullptr;
        if (!p6_cia_boundary(&why)) a.fail(why);
        p6_cia_get(0, &ci[0]);
        p6_cia_get(1, &ci[1]);
        p6_cia_misc_get(&m);
        age = get_cycles() - m.led_cycle;
    }
    for (int n = 0; n < 2; ++n) {
        p6_cia_raw &c = ci[n];
        field<uint8_t>(a, c.pra, 0, 255, "cia.pra");
        field<uint8_t>(a, c.prb, 0, 255, "cia.prb");
        field<uint8_t>(a, c.dra, 0, 255, "cia.dra");
        field<uint8_t>(a, c.drb, 0, 255, "cia.drb");
        for (int t = 0; t < 2; ++t) {
            p6_cia_timer_raw &x = c.t[t];
            field<uint16_t>(a, x.timer, 0, 0xFFFF, "cia.timer");
            field<uint16_t>(a, x.latch, 0, 0xFFFF, "cia.latch");
            field<uint8_t>(a, x.inputpipe, 0, 3, "cia.inputpipe");              // bitstodelay() abort()s on anything else
            const uint32_t delay = field<uint32_t>(a, x.loaddelay, 0, 0xFFFFFFFFll, "cia.loaddelay");
            if (checks) a.require((delay & 0x88888888u) == 0, "cia.loaddelay: a bit no writer sets");
            field<uint8_t>(a, x.preovfl, 0, 1, "cia.preovfl");
            field<uint8_t>(a, x.cr, 0, 255, "cia.cr");
        }
        field<uint32_t>(a, c.tod, 0, 0xFFFFFF, "cia.tod");
        field<uint32_t>(a, c.tol, 0, 0xFFFFFF, "cia.tol");                    // upstream's restore wrote the timer B latch here
        field<uint32_t>(a, c.alarm, 0, 0xFFFFFF, "cia.alarm");
        field<uint8_t>(a, c.tlatch, 0, 1, "cia.tlatch");
        field<uint8_t>(a, c.todon, 0, 1, "cia.todon");
        field<int32_t>(a, c.tod_event_state, 0, 4, "cia.tod_event_state");
        field<int32_t>(a, c.tod_offset, -1, 4095, "cia.tod_offset");
        // RethinkICR is the only writer of 0x80 and 0x40 (together) and ICR() of 0x20 (after them, in the same call), and a read clears the whole byte.
        const uint8_t icr1 = field<uint8_t>(a, c.icr1, 0, 255, "cia.icr1");
        if (checks) a.require(!(icr1 & 0x40) || (icr1 & 0x80), "cia.icr1: INT asserted without its latch");
        if (checks) a.require(!(icr1 & 0x20) || (icr1 & 0xC0) == 0xC0, "cia.icr1: the interrupt flag without the bits RethinkICR sets first");
        field<uint8_t>(a, c.icr2, 0, 0x1F, "cia.icr2");
        field<uint8_t>(a, c.icr_change, 0, 1, "cia.icr_change");
        field<uint8_t>(a, c.imask, 0, 0x7F, "cia.imask");
        field<uint8_t>(a, c.sdr, 0, 255, "cia.sdr");
        field<uint8_t>(a, c.sdr_buf, 0, 255, "cia.sdr_buf");
        field<uint8_t>(a, c.sdr_load, 0, 1, "cia.sdr_load");
        field<uint8_t>(a, c.sdr_cnt, 0, 16, "cia.sdr_cnt");
    }
    field<uint8_t>(a, m.cia_cycle_accurate, 0, 1, "cia.cycle_accurate");
    field<int32_t>(a, m.kbstate, 0, 3, "kb.state");
    field<int32_t>(a, m.kblostsynccnt, 0, 1 << 20, "kb.lostsync");
    field<uint8_t>(a, m.internaleclockphase, 0, 19, "cia.eclockphase");
    field<uint8_t>(a, m.led, 0, 1, "led.on");
    field<int16_t>(a, m.led_old_brightness, -1, 255, "led.old_brightness");
    const int64_t led_age = get<int64_t>(a, age, 0, INT64_MAX, "led.age");
    field<int32_t>(a, m.led_cycles_on, 0, INT32_MAX, "led.on_cycles");
    field<int32_t>(a, m.led_cycles_off, 0, INT32_MAX, "led.off_cycles");
    field<uint8_t>(a, m.powerled, 0, 1, "gui.powerled");
    field<uint8_t>(a, m.powerled_brightness, 0, 255, "gui.powerled_brightness");
    if (a.applying() && a.ok()) {
        m.led_cycle = get_cycles() - led_age;                       // the scheduler's chunk (index 1) has put the counter back
        p6_cia_set(0, &ci[0]);
        p6_cia_set(1, &ci[1]);
        p6_cia_misc_set(&m);
    }
}

void rebuild_cia()
{
    p6_cia_overlay_rebuild();
}

// P6IN body, little endian, in this order:
//   pot lines: u16 potgo_value (bits 8..15 only)  per line (port 0 x, y, port 1 x, y): i16 cap (0..511)  u8 dat  u8 dat_act (0..2)                          (2 + 4 x 4)
//   per gameport: u8 joybutton (<= 7) joydir (<= 15) oleft oright otop obot (0..1)  4 x i16 mouse_delta  4 x u8 mouse_deltanoreset (0..1)  i16 mouse_x mouse_y
//     mouse_frame_x mouse_frame_y                                                                                                                       (2 x 26)
//   u32 input_frame prev_input_frame  u32 prev_input_vpos (<= 4095)                                                                                    (12)
//   the engine's edge memory: per joystick u8 buttonmask (<= 7) and 2 axes x 9 x i8 state (-1..1); u8 mouse buttonmask (<= 7)                           (2 x 19 + 1)
//   the keyboard: 16 bytes of key table (bit n = Amiga key n; 0x68 and above are never down)  u8 reset_seq_mode (0..4)  i32 reset_seq (>= 0)
//     u32 ring_count (<= 255)  ring_count x u8 code, oldest first                                                                                 (25 + ring)
//   the bridge's edge memory (Applied): 4 x u32 keys (same bits)  2 x u8 type (0..2)  2 x u8 buttons (<= 7)  2 x 2 x i8 axis (-1..1)                      (24)
// The engine's table of the keys it has seen and the bridge's say the same thing, and the two port memories the same as the engine's per device: they are checked
// against each other here (with the values read, never the live variables), because a state in which they differ leaves a key or a button stuck or lost
// after the load. When the engine has just cleared its key table and the bridge has not been told (p6_input_engine_released_keys), the state says that no key is applied, which is
// what the bridge's next packet starts from; Apply clears the flag that p6_load left set. Apply copies fields: no key goes through inputdevice_do_keyboard (it would reach the ring and the chord), no event is handled again, and
// resetinput and inputdevice_updateconfig, which erase the configuration, are not called.
void visit_input(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    p6_input_raw r = {};
    p6_applied_raw ap = {};
    uae_u8 ring[255] = {};
    uint32_t ring_n = 0;
    if (a.mode() == Mode::Save) {
        const char *why = nullptr;
        if (!p6_input_boundary(&why) || !p6_keybuf_boundary(&why)) a.fail(why);
        p6_input_get(&r);
        p6_bridge::applied_get(&ap);
        // The engine cleared its key table (a reset requested through the API, or the start of a session) and the bridge has not been told yet: its next packet forgets
        // what it had applied and presses every key the host holds. The state says what that leaves: no key applied, which must be what the engine's table says too.
        if (p6_input_engine_released_keys) memset(ap.keys, 0, sizeof ap.keys);
        const int n = p6_keybuf_get(ring, 255);
        if (n < 0) a.fail("the keyboard ring does not fit a state");
        else ring_n = uint32_t(n);
    }
    const uint16_t potgo = get<uint16_t>(a, r.potgo_value, 0, 0xFFFF, "pot.potgo");
    if (checks) a.require((potgo & 0x00FF) == 0, "pot.potgo: a bit POTGO does not keep");
    if (a.applying() && a.ok()) r.potgo_value = potgo;
    for (int p = 0; p < 2; ++p)
        for (int i = 0; i < 2; ++i) {
            field<int16_t>(a, r.pot_cap[p][i], 0, 511, "pot.cap");
            field<uint8_t>(a, r.pot_dat[p][i], 0, 255, "pot.dat");
            field<uint8_t>(a, r.pot_dat_act[p][i], 0, 2, "pot.dat_act");
        }
    uint8_t joy_dir[2] = {}, joy_button[2] = {};
    for (int p = 0; p < 2; ++p) {
        p6_input_port_raw &g = r.port[p];
        uint32_t buttons = r.cd32_enabled[p] ? g.joybutton & 7 : g.joybutton;
        joy_button[p] = field<uint8_t>(a, buttons, 0, 7, "joy.button"); // CD32's upper seven bits are derived from its applied levels below.
        if (a.applying()) g.joybutton = buttons;
        joy_dir[p] = field<uint8_t>(a, g.joydir, 0, 15, "joy.dir");
        field<uint8_t>(a, g.oleft, 0, 1, "joy.left");
        field<uint8_t>(a, g.oright, 0, 1, "joy.right");
        field<uint8_t>(a, g.otop, 0, 1, "joy.top");
        field<uint8_t>(a, g.obot, 0, 1, "joy.bottom");
        for (int k = 0; k < 4; ++k) field<int16_t>(a, g.mouse_delta[k], -32768, 32767, "mouse.delta");
        for (int k = 0; k < 4; ++k) field<uint8_t>(a, g.mouse_deltanoreset[k], 0, 1, "mouse.noreset");
        field<int16_t>(a, g.mouse_x, -32768, 32767, "mouse.x");
        field<int16_t>(a, g.mouse_y, -32768, 32767, "mouse.y");
        field<int16_t>(a, g.mouse_frame_x, -32768, 32767, "mouse.frame_x");
        field<int16_t>(a, g.mouse_frame_y, -32768, 32767, "mouse.frame_y");
    }
    field<uint32_t>(a, r.input_frame, 0, 0xFFFFFFFFll, "input.frame");
    field<uint32_t>(a, r.prev_input_frame, 0, 0xFFFFFFFFll, "input.prev_frame");
    field<uint32_t>(a, r.prev_input_vpos, 0, 4095, "input.prev_vpos");
    uint8_t joy_buttons[2] = {};
    int8_t joy_axis[2][2] = {};
    for (int j = 0; j < 2; ++j) {
        joy_buttons[j] = field<uint8_t>(a, r.joy_buttonmask[j], 0, 7, "edge.joy_buttons");
        for (int ax = 0; ax < 2; ++ax) {
            for (int i = 0; i < 9; ++i) {
                const int8_t s = field<int8_t>(a, r.joy_states[j][ax][i], -1, 1, "edge.joy_state");
                if (i == 8) joy_axis[j][ax] = s;
            }
        }
    }
    const uint8_t mouse_buttons = field<uint8_t>(a, r.mouse_buttonmask, 0, 7, "edge.mouse_buttons");
    uint8_t keys[16] = {};
    for (int k = 0; k < 16; ++k) keys[k] = field<uint8_t>(a, r.keys[k], 0, 255, "kb.keys");
    field<uint8_t>(a, r.reset_seq_mode, 0, 4, "kb.reset_mode");
    field<int32_t>(a, r.reset_seq, 0, INT32_MAX, "kb.reset_seq");
    const uint32_t ring_count = a.count(ring_n, 255, "kb.ring_count");
    a.bytes(ring, ring_count, "kb.ring");
    uint32_t applied_keys[4] = {};
    uint8_t applied_type[2] = {};
    uint32_t applied_buttons[2] = {};
    int8_t applied_axis[2][2] = {};
    for (int k = 0; k < 4; ++k) applied_keys[k] = field<uint32_t>(a, ap.keys[k], 0, 0xFFFFFFFFll, "applied.keys");
    for (int p = 0; p < 2; ++p) applied_type[p] = field<uint8_t>(a, ap.type[p], 0, P6_PORT_CD32, "applied.type");
    for (int p = 0; p < 2; ++p) {
        uint32_t buttons = ap.type[p] == P6_PORT_CD32 ? ap.buttons[p] & 7 : ap.buttons[p];
        applied_buttons[p] = field<uint8_t>(a, buttons, 0, 7, "applied.buttons");
    }
    for (int p = 0; p < 2; ++p)
        for (int ax = 0; ax < 2; ++ax) applied_axis[p][ax] = field<int8_t>(a, ap.axis[p][ax], -1, 1, "applied.axis");
    // Only an attached CD32 pad has this tail. Existing joystick/mouse bodies and canonical hashes stay identical.
    // Measure reserves both ports (six bytes), since a device may change without unloading the machine.
    for (int p = 0; p < 2; ++p) {
        const bool cd32 = applied_type[p] == P6_PORT_CD32;
        if (cd32 || a.mode() == Mode::Measure) {
            const uint8_t extra = get<uint8_t>(a, ap.buttons[p] >> 7, 0, 15, "cd32.buttons_extra");
            applied_buttons[p] |= uint32_t(extra) << 7;
            field<uint8_t>(a, r.cd32_shifter[p], 0, 8, "cd32.shifter");
            field<uint8_t>(a, r.cd32_clock[p], 0, 1, "cd32.clock");
        }
        if (a.mode() == Mode::Save) {
            a.require(bool(r.cd32_enabled[p]) == cd32, "CD32 mode and the bridge's device disagree");
            a.require(ap.buttons[p] == applied_buttons[p], "unknown applied CD32 button bits");
            if (cd32) a.require(r.port[p].joybutton == p6_bridge::cd32_buttons(applied_buttons[p]), "CD32 buttons and the bridge disagree");
        }
        if (a.applying()) {
            r.cd32_enabled[p] = cd32;
            if (cd32) r.port[p].joybutton = p6_bridge::cd32_buttons(applied_buttons[p]);
            else { r.cd32_shifter[p] = 8; r.cd32_clock[p] = 0; }
            ap.buttons[p] = applied_buttons[p];
        }
    }
    if (a.mode() == Mode::Save)
        a.require(bool(r.second_mouse_enabled) == (applied_type[1] == P6_PORT_MOUSE), "second mouse mode and the bridge's device disagree");
    if (a.applying()) r.second_mouse_enabled = applied_type[1] == P6_PORT_MOUSE;
    if (checks) {
        a.require((keys[13] | keys[14] | keys[15]) == 0, "kb.keys: a key above 0x67 is down");
        a.require((applied_keys[3] >> 8) == 0, "applied.keys: a key above 0x67 is down");
        for (int k = 0; k < 4; ++k) {
            uint32_t engine = 0;
            for (int b = 0; b < 4; ++b) engine |= uint32_t(keys[k * 4 + b]) << (8 * b);
            a.require(engine == applied_keys[k], "the engine's key table and the bridge's disagree");
        }
        for (int p = 0; p < 2; ++p) {
            const bool joystick = applied_type[p] == P6_PORT_JOYSTICK, mouse = applied_type[p] == P6_PORT_MOUSE, cd32 = applied_type[p] == P6_PORT_CD32;
            if (cd32) {
                a.require(joy_button[p] == 0, "CD32 has no ordinary joystick button bits");
                const unsigned directions = (applied_axis[p][0] < 0 ? 1 : applied_axis[p][0] > 0 ? 2 : 0) |
                    (applied_axis[p][1] < 0 ? 4 : applied_axis[p][1] > 0 ? 8 : 0);
                a.require(joy_dir[p] == directions, "CD32 directions and the bridge disagree");
            }
            if (!joystick && !mouse && !cd32) a.require(applied_buttons[p] == 0 && applied_axis[p][0] == 0 && applied_axis[p][1] == 0, "applied: a port without a device has buttons or a direction down");
            if (mouse) a.require(applied_axis[p][0] == 0 && applied_axis[p][1] == 0, "applied: a mouse has a direction down");
            if (mouse && p == 1) {
                a.require(joy_button[p] == applied_buttons[p], "the second mouse buttons and the bridge disagree");
                a.require(joy_dir[p] == 0, "the second mouse has a joystick direction");
            }
            a.require(joy_buttons[p] == (joystick ? applied_buttons[p] : 0), "the engine's joystick buttons and the bridge's disagree");
            a.require(joy_axis[p][0] == (joystick ? applied_axis[p][0] : 0) && joy_axis[p][1] == (joystick ? applied_axis[p][1] : 0),
                      "the engine's joystick directions and the bridge's disagree");
        }
        a.require(mouse_buttons == (applied_type[0] == P6_PORT_MOUSE ? applied_buttons[0] : 0), "the engine's mouse buttons and the bridge's disagree");
    }
    if (a.applying() && a.ok()) {
        p6_input_set(&r);
        p6_keybuf_set(ring, int(ring_count));
        p6_bridge::applied_set(ap);
        p6_input_engine_released_keys = 0;      // p6_load left it set for the first unit; a state is only taken with it clear, and Applied is what the engine's table says
    }
}

}  // namespace p6_state
