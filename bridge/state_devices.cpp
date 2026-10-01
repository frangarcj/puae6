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

// What the engine patches add (patches/winuae/0081), declared here as lifecycle.cpp declares p6_cia_session_begin.
extern void p6_rtc_msm_get(uae_u8 out[3]);
extern void p6_rtc_msm_set(const uae_u8 in[3]);

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

}  // namespace p6_state
