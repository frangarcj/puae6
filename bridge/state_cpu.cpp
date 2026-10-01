// Chunks P6CP (the state of the 68000) and P6SC (the scheduler), docs/adr/0009-state-format.md, manifests/state-inventory.json components cpu-*
// and sched-*.
//
// A state is a unit boundary of a machine that has run its reset head, so a restore is "run the head if it has not run, then overwrite": the
// dispatch tables, the pointers into the engine and the host mirrors come from the head, and this chunk puts back only what is causal. Rules
// this file follows because of how archive.h works:
//  * Validate stores nothing, so a cross-check must use the value that val() returns, never the live variable it came from (num() and flag()
//    update the live variable only in Apply). Checking a live variable would judge the machine in Validate and the body in Apply, and a body
//    that Validate accepted could then fail to apply.
//  * Measure must not depend on the machine and must never fail: a failed require() makes every later field a no-op and the bound would come
//    out too small. Every check is skipped in Measure.
//  * Save refuses what Validate would refuse: a live value outside its range is a defect of the machine, not something to write.
#include "state.h"

#include <stdint.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "uae.h"
#include "memory.h"
#include "custom.h"
#include "blitter.h"
#include "newcpu.h"

// What the engine patches add (patches/winuae/0076-0080), declared here as lifecycle.cpp declares p6_cia_session_begin.
extern uae_u32 p6_rng_get(void);                                                           // main.cpp 0076
extern void p6_rng_set(uae_u32 state);
extern bool p6_cpu_boundary(const char **why);                                             // newcpu.cpp 0077
extern void p6_events_get(int *idx, int *next, int *cnt, int *restart, int *remain);       // events.cpp 0078
extern void p6_events_set(int idx, int next, int remain);
extern int p6_custom_extra_cycle_get(void);                                                // custom.cpp 0079
extern void p6_custom_extra_cycle_set(int v);
extern evfunc2 p6_custom_event2_handler(int which);                                        // 0 intreq, 1 intena, 2 fakehsync
extern evfunc2 p6_disk_motordelay_handler(void);                                           // disk.cpp 0080

namespace p6_state {

namespace {

static_assert(sizeof(int) == 4 && sizeof(evt_t) == 8, "the widths of the engine's own types are part of the format");

constexpr uint32_t kSpcSaved = SPCFLAG_CPUINRESET | SPCFLAG_TRACE | SPCFLAG_DOTRACE | SPCFLAG_BLTNASTY;
constexpr uint16_t kSr68000 = 0xA71F;       // T1, S, the interrupt mask, X N Z V C: a 68000 has no T0 and no M
constexpr int kEvent2Slots = ev2_max;       // 16
constexpr uint8_t kEv2Blitter = 1;          // ids of the secondary-event handlers a state can name (scheduler_handler_id)
constexpr uint8_t kEv2Last = 11;

// A value of the machine that travels as a fixed-width integer in [lo, hi]. Returns what was read (Save: the live value).
template <class W> W get(Archive &a, long long live, long long lo, long long hi, const char *name)
{
    if (a.mode() == Mode::Save) a.require(live >= lo && live <= hi, "a live value of the machine is outside the range of its field");
    return a.val<W>(W(live), W(lo), W(hi), name);
}

// SR as MakeSR() builds it (newcpu.cpp), without writing the cache field regs.sr: a save must not touch the machine.
uint16_t current_sr()
{
    return uint16_t((regs.t1 << 15) | (regs.t0 << 14) | (regs.s << 13) | (regs.m << 12) | (regs.intmask << 8) | (GET_XFLG() << 4) |
                    (GET_NFLG() << 3) | (GET_ZFLG() << 2) | (GET_VFLG() << 1) | GET_CFLG());
}

}  // namespace

// The handler a state's id stands for (nullptr for 0 and for an id that is not in the table): scheduler_handler_id's inverse.
evfunc2 scheduler_handler(uint8_t id)
{
    switch (id) {
    case 1: return blitter_handler;
    case 2: return event_doint_delay_do_ext;
    case 3: return p6_custom_event2_handler(0);
    case 4: return p6_custom_event2_handler(1);
    case 5: return event_audxdat_func;
    case 6: return event_setdsr;
    case 7: return event_CIA_synced_interrupt;
    case 8: return event_CIA_tod_inc_event;
    case 9: return event_DISK_handler;
    case 10: return p6_disk_motordelay_handler();
    case 11: return p6_custom_event2_handler(2);
    default: return nullptr;
    }
}

// Stable ids of the secondary-event handlers: the vocabulary of the state, not of the engine. Reachable in the A500 profile (read from the callers
// of event2_newevent* in the compiled units): 1 blitter_handler (slot 0 only, assigned by init_eventtab; never active with a cycle-exact blitter),
// 2 event_doint_delay_do_ext, 3 event_doint_delay_do_intreq, 4 event_doint_delay_do_intena (m68k_interrupt_delay is true), 5 event_audxdat_func,
// 6 event_setdsr, 7 event_CIA_synced_interrupt, 8 event_CIA_tod_inc_event, 9 event_DISK_handler, 10 motordelay_func, 11 fakehsync_handler. Not
// reachable: doint_delayed (needs cs_ipldelay), setup_fmodes_delayed (AGA), lightpen_trigger_func (no lightpen port), keymcu_event and
// cputester_event (keyboard_mode 0). 0 is "no handler that a state can name": a save refuses an active event that has one.
uint8_t scheduler_handler_id(evfunc2 f)
{
    if (f == blitter_handler) return 1;
    if (f == event_doint_delay_do_ext) return 2;
    if (f == p6_custom_event2_handler(0)) return 3;
    if (f == p6_custom_event2_handler(1)) return 4;
    if (f == event_audxdat_func) return 5;
    if (f == event_setdsr) return 6;
    if (f == event_CIA_synced_interrupt) return 7;
    if (f == event_CIA_tod_inc_event) return 8;
    if (f == event_DISK_handler) return 9;
    if (f == p6_disk_motordelay_handler()) return 10;
    if (f == p6_custom_event2_handler(2)) return 11;
    return 0;
}

// Body, little endian, in this order:
//   i32 go.quit_program (-4..0, not -1)
//   u32 pc  u16 sr  u32 d0..d7 a0..a7 (a7 raw: the active stack pointer)  u32 usp  u32 isp
//   u16 ir irc ird read_buffer write_buffer db opcode  u32 latch_rw latch_read latch_write
//   u8 stopped (0..1)  u8 halted (0..13, not 4)  u32 spcflags (CPUINRESET|TRACE|DOTRACE|BLTNASTY)
//   u8 ipl0 ipl1 ipl_pin ipl_pin_p (0..7)  u8 ipl_evt_pre_mode (0..1)  i64 ipl_evt ipl_evt_pre ipl_pin_change_evt ipl_pin_change_evt_p (>= 0)
//   u32 xorshiftstate (>= 1)
void visit_cpu(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    if (a.mode() == Mode::Save) {
        const char *why = nullptr;
        if (!p6_cpu_boundary(&why)) a.fail(why);            // an exception in flight, a flag the profile never leaves pending, a head that has not run...
    }

    // 1. m68k_go_unit's control state. A state exists only for a machine past its reset head (state.cpp refuses to save before the first unit),
    //    where the loop is suspended and not on a hard boot, so nothing of that travels and a load runs the head first. quit_program can be
    //    NEGATIVE at a boundary: uae_reset() posts -UAE_RESET*, the next hsync turns it into the request, and m68k_go_unit's DONE test is
    //    quit_program <= 0.
    const int32_t quit = get<int32_t>(a, quit_program, -UAE_RESET_HARD, 0, "go.quit_program");
    if (checks) a.require(quit != -UAE_QUIT, "go.quit_program: a state cannot be a request to quit");

    // 2. PC and SR. SR travels as MakeSR() builds it and is taken apart again below without MakeFromSR (it swaps A7, usp and isp and sets
    //    TRACE) and without m68k_reset_sr (it loads A7 from the slot of the mode, and A7 is saved raw).
    const uint32_t pc = a.val<uint32_t>(m68k_getpc(), "cpu.pc");
    const uint16_t sr = a.val<uint16_t>(current_sr(), "cpu.sr");
    if (checks) a.require((sr & ~kSr68000) == 0, "cpu.sr: a bit a 68000 does not have");

    // 3. D0-D7 and A0-A7 as they are, USP and ISP raw: the slot of the current mode is stale in the engine and can become the live one after a
    //    reset (m68k_reset2 clears neither), and the T14b digest hashes both.
    static const char *const kReg[16] = {"cpu.d0", "cpu.d1", "cpu.d2", "cpu.d3", "cpu.d4", "cpu.d5", "cpu.d6", "cpu.d7",
                                         "cpu.a0", "cpu.a1", "cpu.a2", "cpu.a3", "cpu.a4", "cpu.a5", "cpu.a6", "cpu.a7"};
    for (int i = 0; i < 16; ++i) a.num(regs.regs[i], kReg[i]);
    a.num(regs.usp, "cpu.usp");
    a.num(regs.isp, "cpu.isp");

    // 4. The prefetch queue and the bus latches. opcode, db, ird and the two buffers are not read by the 68000 cycle-exact core before they are
    //    written, but the T14b digest hashes them and the proof of this chunk is that the digest of the unit it was taken in comes back.
    a.num(regs.ir, "cpu.ir");
    a.num(regs.irc, "cpu.irc");
    a.num(regs.ird, "cpu.ird");
    a.num(regs.read_buffer, "cpu.read_buffer");
    a.num(regs.write_buffer, "cpu.write_buffer");
    a.num(regs.db, "cpu.db");
    a.num(regs.opcode, "cpu.opcode");
    a.num(regs.chipset_latch_rw, "cpu.latch_rw");
    a.num(regs.chipset_latch_read, "cpu.latch_read");
    a.num(regs.chipset_latch_write, "cpu.latch_write");

    // 5. STOP, HALT and the special flags. TRACE and DOTRACE verbatim (activate_trace() clears TRACE while T1 stays set); CHECK, INT, DOINT,
    //    CPU_SLOW and the rest are derived or never set (p6_cpu_boundary refuses a save with anything else pending). Apply REPLACES the word.
    const uint8_t stopped = get<uint8_t>(a, regs.stopped, 0, 1, "cpu.stopped");               // 2 (LPSTOP) is a 68060 state
    const uint8_t halted = get<uint8_t>(a, regs.halted, 0, 13, "cpu.halted");
    if (checks) a.require(halted != CPU_HALT_ACCELERATOR_CPU_FALLBACK, "cpu.halted: the accelerator fallback is not in the profile");
    const uint32_t spc = a.val<uint32_t>(uint32_t(regs.spcflags) & kSpcSaved, "cpu.spcflags");
    if (checks) a.require((spc & ~kSpcSaved) == 0, "cpu.spcflags: a flag that cannot be pending at a unit boundary");

    // 6. The interrupt pipeline. The times are absolute in the epoch of the scheduler's chunk, or stale ones from before a counter restart: no
    //    upper bound.
    const uint8_t ipl0 = get<uint8_t>(a, regs.ipl[0], 0, 7, "cpu.ipl0");
    const uint8_t ipl1 = get<uint8_t>(a, regs.ipl[1], 0, 7, "cpu.ipl1");
    const uint8_t pin = get<uint8_t>(a, regs.ipl_pin, 0, 7, "cpu.ipl_pin");
    const uint8_t pin_p = get<uint8_t>(a, regs.ipl_pin_p, 0, 7, "cpu.ipl_pin_p");
    const uint8_t pre_mode = get<uint8_t>(a, regs.ipl_evt_pre_mode, 0, 1, "cpu.ipl_evt_pre_mode");
    const int64_t evt = get<int64_t>(a, regs.ipl_evt, 0, INT64_MAX, "cpu.ipl_evt");
    const int64_t evt_pre = get<int64_t>(a, regs.ipl_evt_pre, 0, INT64_MAX, "cpu.ipl_evt_pre");
    const int64_t chg = get<int64_t>(a, regs.ipl_pin_change_evt, 0, INT64_MAX, "cpu.ipl_pin_change_evt");
    const int64_t chg_p = get<int64_t>(a, regs.ipl_pin_change_evt_p, 0, INT64_MAX, "cpu.ipl_pin_change_evt_p");

    // 7. The live generator (floppy jitter, the start-up delay, the chip-colour power-up). xorshift32 never reaches 0 from a nonzero state.
    const uint32_t rng = get<uint32_t>(a, p6_rng_get(), 1, 0xffffffffll, "rng.xorshift");

    if (a.applying() && a.ok()) {
        quit_program = quit;
        regs.sr = sr;
        regs.t1 = (sr >> 15) & 1;
        regs.t0 = 0;
        regs.s = (sr >> 13) & 1;
        regs.m = 0;
        regs.intmask = (sr >> 8) & 7;
        SET_XFLG((sr >> 4) & 1);
        SET_NFLG((sr >> 3) & 1);
        SET_ZFLG((sr >> 2) & 1);
        SET_VFLG((sr >> 1) & 1);
        SET_CFLG(sr & 1);
        m68k_setpc_normal(pc);            // pc_p = pc_oldp = 0 and regs.pc = instruction_pc = pc (m68k_pc_indirect is 1 after the reset head)
        regs.stopped = stopped;
        regs.halted = halted;             // m68k_go_unit sets SPCFLAG_CHECK again for a halted CPU when the unit resumes
        regs.spcflags = int32_t(spc);
        regs.ipl[0] = ipl0;
        regs.ipl[1] = ipl1;
        regs.ipl_pin = pin;
        regs.ipl_pin_p = pin_p;
        regs.ipl_evt_pre_mode = pre_mode;
        regs.ipl_evt = evt;
        regs.ipl_evt_pre = evt_pre;
        regs.ipl_pin_change_evt = chg;
        regs.ipl_pin_change_evt_p = chg_p;
        p6_rng_set(rng);
        // Not touched, on purpose: ir, ird and irc are not refilled from memory (restore_cpu_finish's fill_prefetch_quick would overwrite them with
        // what memory holds now, which can differ from the prefetch queue), doint() is not called (it would recompute ipl_pin from the chipset,
        // whose delayed events are still pending), and lastipl, vbr, sfc, dfc, msp, t0 and m are 0 in the profile (a save refuses otherwise).
    }
}

// P6SC body, little endian, in this order:
//   i64 currcycle (>= 0)  u16 extra_cycle (0..511)  u16 cycles_to_add_remain (0..511)  u32 hsync_counter  u32 vsync_counter
//   4 x { u8 active, i64 evtime (0 when inactive), i64 oldcycles }                      eventtab[ev_sync, ev_cia, ev_misc, ev_audio]
//   u8 event2idx (0..16)  u8 event2next (1..15)
//   16 x { u8 active, u8 handler id (0 when inactive), i64 evtime (0 when inactive), u32 data (0 when inactive) }        eventtab2[0..15]
// start_cycles is NOT written: it is 0 (m68k_go_unit BEGIN) and the next reset head restarts the counter from it; upstream's restore_cycles() would
// put the saved cycle count there and a later reset would start from the wrong epoch. nextevent is derived by rebuild_scheduler. currcycle_cck is not
// written either: the engine only reads it to take snapshots (agnus_*_cck) and to subtract them, so it is the origin of a clock that only differences
// are taken on, and it is not reset by a load (a reload keeps the previous session's). The chipset chunk saves those snapshots as ages against it.
void visit_scheduler(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    int idx = 0, nxt = 1, cnt = 0, restart = 0, remain = 0;
    p6_events_get(&idx, &nxt, &cnt, &restart, &remain);
    if (a.mode() == Mode::Save) {
        a.require(cnt == 0 && restart == 0, "the secondary scheduler is inside a handler");
        a.require(start_cycles == 0 && pissoff == 0, "the cycle counter is not in the state of an instruction boundary");
    }

    // 1. The counters.
    const int64_t now = get<int64_t>(a, currcycle, 0, INT64_MAX, "sched.currcycle");
    const uint16_t extra = get<uint16_t>(a, p6_custom_extra_cycle_get(), 0, CYCLE_UNIT - 1, "sched.extra_cycle");
    const uint16_t pending = get<uint16_t>(a, remain, 0, CYCLE_UNIT - 1, "sched.cycles_to_add_remain");
    const uint32_t hs = a.val<uint32_t>(hsync_counter, "sched.hsync_counter");        // (hsync_counter & 15) phases the keyboard poll (cia.cpp)
    const uint32_t vs = a.val<uint32_t>(vsync_counter, "sched.vsync_counter");

    // 2. eventtab[]: all four, active or not (oldcycles of ev_cia is read while it is inactive). The dead time of an inactive entry is written as 0
    //    so that two saves of the same machine are the same bytes. The handlers are not saved: they are fixed by the slot and the reset head's
    //    init_eventtab() assigned them. Every check uses what val() returned, never the live variable.
    bool main_active[ev_max];
    int64_t main_time[ev_max], main_old[ev_max];
    for (int i = 0; i < ev_max; ++i) {
        const ev &e = eventtab[i];
        main_active[i] = get<uint8_t>(a, e.active ? 1 : 0, 0, 1, "ev.active") != 0;
        main_time[i] = get<int64_t>(a, e.active ? e.evtime : 0, 0, INT64_MAX, "ev.evtime");
        main_old[i] = get<int64_t>(a, e.oldcycles, 0, INT64_MAX, "ev.oldcycles");
        if (checks) {
            a.require(main_active[i] ? main_time[i] >= now : main_time[i] == 0, "an event is in the past, or an inactive one has a time");
            a.require(main_old[i] <= now, "ev.oldcycles: later than the cycle counter");
            a.require(i != ev_sync || !main_active[i], "the sync event is never scheduled in the A500 profile (cpu_memory_cycle_exact)");
        }
    }

    // 3. The secondary scheduler: its round (event2idx decides which of two events due together runs first; event2next where the next one goes) and
    //    all 16 slots by index, because the slot decides that order too.
    const uint8_t round = get<uint8_t>(a, idx, 0, ev2_max, "ev2.idx");                // set to slot+1: 16 is valid, MISC_handler masks it
    const uint8_t next = get<uint8_t>(a, nxt, ev2_misc, ev2_max - 1, "ev2.next");
    bool sec_active[kEvent2Slots];
    uint8_t sec_id[kEvent2Slots];
    int64_t sec_time[kEvent2Slots];
    uint32_t sec_data[kEvent2Slots];
    bool any = false;
    int64_t earliest = INT64_MAX;
    for (int i = 0; i < kEvent2Slots; ++i) {
        const ev2 &e = eventtab2[i];
        const uint8_t live_id = e.active ? scheduler_handler_id(e.handler) : 0;
        if (a.mode() == Mode::Save && e.active && live_id == 0) a.fail("a secondary event has a handler that a state cannot name");
        sec_active[i] = get<uint8_t>(a, e.active ? 1 : 0, 0, 1, "ev2.active") != 0;
        sec_id[i] = get<uint8_t>(a, live_id, 0, kEv2Last, "ev2.handler");
        sec_time[i] = get<int64_t>(a, e.active ? e.evtime : 0, 0, INT64_MAX, "ev2.evtime");
        sec_data[i] = a.val<uint32_t>(e.active ? e.data : 0, "ev2.data");
        if (checks) {
            if (sec_active[i]) {
                a.require(sec_id[i] != 0 && sec_time[i] >= now, "a secondary event has no handler or is in the past");
                a.require((i == ev2_blitter) == (sec_id[i] == kEv2Blitter), "slot 0 holds the blitter's event and nothing else does");
                any = true;
                if (sec_time[i] < earliest) earliest = sec_time[i];
            } else {
                a.require(sec_id[i] == 0 && sec_time[i] == 0 && sec_data[i] == 0, "an inactive secondary event has data");
            }
        }
    }
    // ev_misc is armed for the earliest of the slots (MISC_handler), or earlier when a slot was removed without re-arming it.
    if (checks) a.require(!any || (main_active[ev_misc] && main_time[ev_misc] <= earliest), "secondary events are pending but ev_misc is not armed for them");

    if (a.applying() && a.ok()) {
        set_cycles(now);
        p6_custom_extra_cycle_set(extra);
        hsync_counter = hs;
        vsync_counter = vs;
        for (int i = 0; i < ev_max; ++i) {
            eventtab[i].active = main_active[i];
            eventtab[i].evtime = main_time[i];
            eventtab[i].oldcycles = main_old[i];
        }
        for (int i = 0; i < kEvent2Slots; ++i) {
            eventtab2[i].active = sec_active[i];
            if (sec_active[i]) {
                eventtab2[i].evtime = sec_time[i];
                eventtab2[i].data = sec_data[i];
                eventtab2[i].handler = scheduler_handler(sec_id[i]);
            }
        }
        p6_events_set(round, next, pending);       // also zeroes event2cnt and event2restart
    }
}

// After every chunk is applied: nextevent is derived from the table (events_schedule()). No other chunk may touch eventtab, eventtab2 or event2idx
// while it applies (no CIA_calctimers, no event2_newevent_*): this chunk owns them.
void rebuild_scheduler()
{
    events_schedule();
}

}  // namespace p6_state
