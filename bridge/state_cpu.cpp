// Chunk P6CP: the state of the 68000 (docs/adr/0009-state-format.md, manifests/state-inventory.json components cpu-*).
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
#include "newcpu.h"

// What the engine patches add (patches/winuae/0076, 0077), declared here as lifecycle.cpp declares p6_cia_session_begin.
extern uae_u32 p6_rng_get(void);
extern void p6_rng_set(uae_u32 state);
extern bool p6_cpu_boundary(const char **why);

namespace p6_state {

namespace {

static_assert(sizeof(int) == 4 && sizeof(evt_t) == 8, "the widths of the engine's own types are part of the format");

constexpr uint32_t kSpcSaved = SPCFLAG_CPUINRESET | SPCFLAG_TRACE | SPCFLAG_DOTRACE | SPCFLAG_BLTNASTY;
constexpr uint16_t kSr68000 = 0xA71F;       // T1, S, the interrupt mask, X N Z V C: a 68000 has no T0 and no M

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

}  // namespace p6_state
