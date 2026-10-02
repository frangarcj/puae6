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

// What the engine patches add (patches/winuae/0081, 0082), declared here as lifecycle.cpp declares p6_cia_session_begin.
extern void p6_rtc_msm_get(uae_u8 out[3]);
extern void p6_rtc_msm_set(const uae_u8 in[3]);
extern void p6_cia_get(int num, p6_cia_raw *out);
extern void p6_cia_set(int num, const p6_cia_raw *in);
extern void p6_cia_misc_get(p6_cia_misc_raw *out);
extern void p6_cia_misc_set(const p6_cia_misc_raw *in);
extern void p6_cia_overlay_rebuild(void);
extern bool p6_cia_boundary(const char **why);

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
void visit_rtc(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    p6_rtc_state s = {};
    uint8_t msm[3] = {};
    if (a.mode() == Mode::Save) {
        a.require(currprefs.cs_rtc == 1, "rtc: only the MSM6242B is in the profile");
        a.require(p6_rtc_save(&s) == P6_CLOCK_OK, "rtc: the clock service failed");
        p6_rtc_msm_get(msm);
    }
    const uint64_t rate = clock_rate();
    const int64_t seconds = get<int64_t>(a, s.clock.seconds, P6_CIVIL_MIN_SECONDS, P6_CIVIL_MAX_SECONDS, "rtc.seconds");
    const uint64_t ticks = get<uint64_t>(a, s.clock.ticks_per_second, rate, rate, "rtc.ticks_per_second");
    const uint64_t subsecond = get<uint64_t>(a, s.clock.subsecond, 0, rate - 1, "rtc.subsecond");
    const uint32_t seed = get<uint32_t>(a, s.clock.seed, clock_seed(), clock_seed(), "rtc.seed");
    uint8_t chosen[3];
    chosen[0] = get<uint8_t>(a, msm[0], 0, 9, "rtc.msm_d");
    if (checks) a.require(chosen[0] == 0 || chosen[0] == 1 || chosen[0] == 8 || chosen[0] == 9, "rtc.msm_d: a bit the MSM6242B does not keep");
    chosen[1] = get<uint8_t>(a, msm[1], 0, 15, "rtc.msm_e");
    chosen[2] = get<uint8_t>(a, msm[2], 0, 15, "rtc.msm_f");
    if (a.applying() && a.ok()) {
        p6_rtc_state next = {};
        next.clock.seconds = seconds;
        next.clock.ticks_per_second = ticks;
        next.clock.subsecond = subsecond;
        next.clock.seed = seed;
        next.clock.version = 1;
        next.cycle = get_cycles();                      // the scheduler's chunk (index 1) has put the counter back
        if (p6_rtc_restore(&next) != P6_CLOCK_OK) a.fail("rtc: the clock refused a state that was validated");
        p6_rtc_msm_set(chosen);
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

}  // namespace p6_state
