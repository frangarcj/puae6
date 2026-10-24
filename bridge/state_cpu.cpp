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
#include "config.h"

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
#include "fpp.h"
#include "p6_fpu_state.h"
#include "cdtv_state.h"
#include "p6_state_access.h"

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
extern int p6_custom_ce020_extra_get(void);                                                // custom.cpp 0108
extern void p6_custom_ce020_extra_set(int v);
extern bool p6_caches020_get(uae_u32 *data, uae_u32 *tag, uae_u8 *valid, unsigned n);      // newcpu.cpp 0108
extern bool p6_caches020_set(const uae_u32 *data, const uae_u32 *tag, const uae_u8 *valid, unsigned n);
extern void p6_fpu_fsave_get(uae_u32 *ccr, uae_u32 *eo);                                   // fpp.cpp 0142
extern void p6_fpu_fsave_set(uae_u32 ccr, const uae_u32 *eo);

namespace p6_state {

namespace {

static_assert(sizeof(int) == 4 && sizeof(evt_t) == 8, "the widths of the engine's own types are part of the format");

constexpr uint32_t kSpcSaved = SPCFLAG_CPUINRESET | SPCFLAG_TRACE | SPCFLAG_DOTRACE | SPCFLAG_BLTNASTY | SPCFLAG_ACTION_REPLAY;
constexpr uint16_t kSr68000 = 0xA71F;       // T1, S, the interrupt mask, X N Z V C: a 68000 has no T0 and no M
constexpr uint16_t kSr68020 = 0xF71F;       // T1, T0, S, M, the interrupt mask, X N Z V C
constexpr int kEvent2Slots = ev2_max;       // 16
constexpr uint8_t kEv2Blitter = 1;          // ids of the secondary-event handlers a state can name (scheduler_handler_id)
constexpr uint8_t kEv2Last = 15;

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
    case 12: return p6_custom_event2_handler(3);
    case 13: return p6_custom_event2_handler(4);
    case 14: return p6_cdtv_subcode_handler();
    case 15: return p6_ar_cia_handler();
    default: return nullptr;
    }
}

// Stable ids of the secondary-event handlers: the vocabulary of the state, not of the engine. Reachable in the A500 profile (read from the callers
// of event2_newevent* in the compiled units): 1 blitter_handler (slot 0 only, assigned by init_eventtab; never active with a cycle-exact blitter),
// 2 event_doint_delay_do_ext, 3 event_doint_delay_do_intreq, 4 event_doint_delay_do_intena (m68k_interrupt_delay is true), 5 event_audxdat_func,
// 6 event_setdsr, 7 event_CIA_synced_interrupt, 8 event_CIA_tod_inc_event, 9 event_DISK_handler, 10 motordelay_func, 11 fakehsync_handler. 12 is
// setup_fmodes_delayed, reachable only with AGA (setup_fmodes queues it two colour clocks after a write that changes FMODE; T24c).
// 13 lightpen_trigger_func (T70c), 14 subcode_interrupt with the private CDTV owner, 15 AR2/3 delayed CIA activation. Not reachable: doint_delayed (needs cs_ipldelay), keymcu_event and
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
    if (f == p6_custom_event2_handler(3)) return 12;
    if (f == p6_custom_event2_handler(4)) return 13;
    if (f == p6_cdtv_subcode_handler()) return 14;
    if (p6_ar_cia_handler() && f == p6_ar_cia_handler()) return 15;
    return 0;
}

namespace {

// -- The 68020 block of P6CP (T24c) -------------------------------------------------------------------------------------------------------------------------------
// Appended to the body of a machine whose CPU is a 68020, in this order (little endian, 662 bytes):
//   u32 vbr  u32 msp  u32 caar  u8 sfc dfc (0..7)  u8 cacr (0..15)
//   u32 prefetch020addr  u32 cacheholdingaddr020  u32 cacheholdingdata020  u16 prefetch020[4]  u8 prefetch020_valid[4] (0..1)
//   i32 pipeline_pos (-1..255)  i32 pipeline_r8[2] (0..255)  i32 pipeline_stop (-1..1)
//   64 x { u32 data  u32 tag (bit 0 the supervisor bit, bits 1..7 zero)  u8 valid (0..1) }          caches020, the instruction cache of the 68020
//   i32 instruction_cnt  u8 ce020extracycles (0..16)  i64 ce020prefetchendcycle  i64 ce020startcycle  i64 ce020endcycle (each >= 0, in the epoch of P6SC)
//   u16 ce020_extra (the remainder below a colour clock of do_cycles_ce020, 0..CYCLE_UNIT-1)
// The registers, the cache and the prefetch queue with its pipeline are what WinUAE's own save_cpu carries for a 68020 (that body is under SAVESTATE and is not compiled): they are
// read from `regs` and from the one file-local table (patch 0108, p6_caches020_get/set). The last five fields are what the cycle-exact core keeps between two instructions and
// that DET02 showed to decide the future, one by one (docs/adr/0009-state-format.md point 9 and the comments in visit_020).
constexpr unsigned kCaches020 = CACHELINES020;

struct Cpu020 {
    uint32_t vbr = 0, msp = 0, caar = 0;
    uint8_t sfc = 0, dfc = 0, cacr = 0;
    uint32_t prefetch_addr = 0, holding_addr = 0, holding_data = 0;
    uint16_t prefetch[CPU_PIPELINE_MAX] = {};
    uint8_t prefetch_valid[CPU_PIPELINE_MAX] = {};
    int32_t pipeline_pos = 0, pipeline_r8[2] = {}, pipeline_stop = 0;
    int32_t instruction_cnt = 0;
    uint8_t extracycles = 0;
    int64_t prefetch_end = 0, start_cycle = 0, end_cycle = 0;
    uint16_t sub_cck = 0;
    uae_u32 cache_data[kCaches020] = {}, cache_tag[kCaches020] = {};
    uae_u8 cache_valid[kCaches020] = {};
};

void visit_020(Archive &a, Cpu020 &c)
{
    const bool checks = a.mode() != Mode::Measure;
    c.vbr = a.val<uint32_t>(regs.vbr, "cpu.vbr");
    c.msp = a.val<uint32_t>(regs.msp, "cpu.msp");
    c.caar = a.val<uint32_t>(regs.caar, "cpu.caar");
    c.sfc = get<uint8_t>(a, regs.sfc, 0, 7, "cpu.sfc");
    c.dfc = get<uint8_t>(a, regs.dfc, 0, 7, "cpu.dfc");
    c.cacr = get<uint8_t>(a, regs.cacr, 0, 15, "cpu.cacr");
    c.prefetch_addr = a.val<uint32_t>(regs.prefetch020addr, "cpu.prefetch020addr");
    c.holding_addr = a.val<uint32_t>(regs.cacheholdingaddr020, "cpu.cacheholdingaddr020");
    c.holding_data = a.val<uint32_t>(regs.cacheholdingdata020, "cpu.cacheholdingdata020");
    for (unsigned i = 0; i < CPU_PIPELINE_MAX; ++i) c.prefetch[i] = a.val<uint16_t>(regs.prefetch020[i], "cpu.prefetch020");
    for (unsigned i = 0; i < CPU_PIPELINE_MAX; ++i) c.prefetch_valid[i] = get<uint8_t>(a, regs.prefetch020_valid[i], 0, 1, "cpu.prefetch020_valid");
    c.pipeline_pos = get<int32_t>(a, regs.pipeline_pos, -1, 255, "cpu.pipeline_pos");
    for (unsigned i = 0; i < 2; ++i) c.pipeline_r8[i] = get<int32_t>(a, regs.pipeline_r8[i], 0, 255, "cpu.pipeline_r8");
    c.pipeline_stop = get<int32_t>(a, regs.pipeline_stop, -1, 1, "cpu.pipeline_stop");

    uae_u32 live_data[kCaches020] = {}, live_tag[kCaches020] = {};
    uae_u8 live_valid[kCaches020] = {};
    if (a.mode() == Mode::Save && !p6_caches020_get(live_data, live_tag, live_valid, kCaches020)) a.fail("cpu: the instruction cache of the 68020 is not the size of this build");
    for (unsigned i = 0; i < kCaches020; ++i) {
        c.cache_data[i] = a.val<uint32_t>(live_data[i], "cpu.icache.data");
        c.cache_tag[i] = a.val<uint32_t>(live_tag[i], "cpu.icache.tag");
        c.cache_valid[i] = get<uint8_t>(a, live_valid[i], 0, 1, "cpu.icache.valid");
        if (checks) a.require((c.cache_tag[i] & 0xfe) == 0, "cpu.icache.tag: bits that the tag of a line never has");
    }
    // The count of instructions executed: the audio hacks that a 68020 enables (usehacks) compare it with the count at which a channel's DMA went off (dmaofftime_cpu_cnt, which AUDP
    // carries as it was), so a machine with another count judged a channel differently (DET02 tone, same-image: the history of the loaded image is not the state's). It wraps; no range.
    c.instruction_cnt = a.val<int32_t>(regs.instruction_cnt, "cpu.instruction_cnt");
    // One instruction in sixteen the cycle-exact core pays two more cycles (wait_memory_cycles): the count of the instructions since the last one is state (DET02 vsync, new
    // process: a loaded machine paid them 14 instructions off the original's and the interrupt reached the CPU four cycles apart).
    c.extracycles = get<uint8_t>(a, regs.ce020extracycles, 0, 16, "cpu.ce020extracycles");
    // The cycle at which the last opcode prefetch ended: an opcode prefetch that starts in the very same cycle pays two more (start_020_cycle_prefetch), and the cycle counter only
    // moves by whole colour clocks, so two prefetches of the same clock are common. A new image has 0 there and paid nothing (DET02 master, new-process: the interrupt reached the CPU 4 cycles
    // early one unit after the load). Absolute, like the times of P6CP's interrupt pipeline.
    c.prefetch_end = get<int64_t>(a, regs.ce020prefetchendcycle, 0, INT64_MAX, "cpu.ce020prefetchendcycle");
    // The span of the last memory cycle: do_cycles_ce020_internal (cpu_prefetch.h) pays an internal cycle only for what outlasts endcycle - startcycle, so the next instruction's internal
    // cycles overlap the memory cycle that was in flight. A same image kept its own pair and its first instruction after the load took 4 cycles more or less (DET02 tone, same-image).
    c.start_cycle = get<int64_t>(a, regs.ce020startcycle, 0, INT64_MAX, "cpu.ce020startcycle");
    c.end_cycle = get<int64_t>(a, regs.ce020endcycle, 0, INT64_MAX, "cpu.ce020endcycle");
    // do_cycles_ce020 keeps the part of a colour clock it has not paid in a function-local static (patch 0108): the same kind of state as extra_cycle of P6SC, which the 68000 core
    // keeps the same way. A same image carried its own remainder into the loaded machine's first instruction (DET02 same-image: the registers differed one unit later).
    c.sub_cck = get<uint16_t>(a, p6_custom_ce020_extra_get(), 0, CYCLE_UNIT - 1, "cpu.ce020_extra");
}

void apply_020(const Cpu020 &c)
{
    regs.vbr = c.vbr;
    regs.msp = c.msp;
    regs.caar = c.caar;
    regs.sfc = c.sfc;
    regs.dfc = c.dfc;
    regs.cacr = c.cacr;
    regs.prefetch020addr = c.prefetch_addr;
    regs.cacheholdingaddr020 = c.holding_addr;
    regs.cacheholdingdata020 = c.holding_data;
    for (unsigned i = 0; i < CPU_PIPELINE_MAX; ++i) {
        regs.prefetch020[i] = c.prefetch[i];
        regs.prefetch020_valid[i] = c.prefetch_valid[i];
    }
    regs.pipeline_pos = c.pipeline_pos;
    regs.pipeline_r8[0] = c.pipeline_r8[0];
    regs.pipeline_r8[1] = c.pipeline_r8[1];
    regs.pipeline_stop = c.pipeline_stop;
    regs.instruction_cnt = c.instruction_cnt;
    regs.ce020extracycles = c.extracycles;
    regs.ce020prefetchendcycle = c.prefetch_end;
    regs.ce020startcycle = c.start_cycle;
    regs.ce020endcycle = c.end_cycle;
    p6_custom_ce020_extra_set(c.sub_cck);
    p6_caches020_set(c.cache_data, c.cache_tag, c.cache_valid, kCaches020);
}

// -- The 68010 block of P6CP (T52) ---------------------------------------------------------------------------------------------------------------------------------
// Appended to the body of a machine whose CPU is a 68010 (p6_config.cpu_model 2 on a 68000 machine), in this order (little endian, 10 bytes):
//   u32 vbr  u8 sfc dfc (0..7)  u32 loop_mode
// What a 68010 has that a 68000 does not and that decides its future: the vector base register (the exception vectors are read from it), the two function code registers (read by MOVEC,
// used by MOVES) and the loop mode of the cycle-exact core (patch 0310 lets p6_cpu_boundary through with it). The loop mode is the state of a DBcc loop of one instruction that the
// 68010 runs from its two-word queue: a loop that spans a unit boundary leaves regs.loop_mode set between two instructions (bit 0 the flag, the cycles of the looped instruction in the
// bits 1 to 15, the extra cycles of its exit in the bits 16 to 31), and the DBcc handler reads it to know whether it is the first pass; ird (carried above) says which instruction loops.
struct Cpu010 {
    uint32_t vbr = 0, loop_mode = 0;
    uint8_t sfc = 0, dfc = 0;
};

// T55b normal 68030: direct instruction reads do not consult the prefetch or cache tables.
// Control registers and instruction count still decide exceptions, guest reads and audio DMA.
struct Cpu030Normal {
    uint32_t vbr = 0, msp = 0, caar = 0, cacr = 0;
    uint8_t sfc = 0, dfc = 0;
    int32_t instruction_cnt = 0;
};

void visit_030_normal(Archive &a, Cpu030Normal &c)
{
    c.vbr = a.val<uint32_t>(regs.vbr, "cpu.vbr");
    c.msp = a.val<uint32_t>(regs.msp, "cpu.msp");
    c.caar = a.val<uint32_t>(regs.caar, "cpu.caar");
    c.cacr = a.val<uint32_t>(regs.cacr, "cpu.cacr");
    if (a.mode() != Mode::Measure) a.require(!(c.cacr & ~UINT32_C(0x3f1f)), "cpu.cacr: a bit a 68030 does not have");
    c.sfc = get<uint8_t>(a, regs.sfc, 0, 7, "cpu.sfc");
    c.dfc = get<uint8_t>(a, regs.dfc, 0, 7, "cpu.dfc");
    c.instruction_cnt = a.val<int32_t>(regs.instruction_cnt, "cpu.instruction_cnt");
}

void apply_030_normal(const Cpu030Normal &c)
{
    regs.vbr = c.vbr; regs.msp = c.msp; regs.caar = c.caar; regs.cacr = c.cacr;
    regs.sfc = c.sfc; regs.dfc = c.dfc; regs.instruction_cnt = c.instruction_cnt;
}

void visit_010(Archive &a, Cpu010 &c)
{
    const bool checks = a.mode() != Mode::Measure;
    c.vbr = a.val<uint32_t>(regs.vbr, "cpu.vbr");
    c.sfc = get<uint8_t>(a, regs.sfc, 0, 7, "cpu.sfc");
    c.dfc = get<uint8_t>(a, regs.dfc, 0, 7, "cpu.dfc");
    c.loop_mode = a.val<uint32_t>(regs.loop_mode, "cpu.loop_mode");
    // The cycles of the looped instruction are at most the 6 of TST.L and the exit adds at most 4 (gencpu's loop mode table).
    if (checks) a.require((c.loop_mode & 0xffff) <= 0x10 && (c.loop_mode >> 16) <= 0x10, "cpu.loop_mode: cycles beyond what the looped instructions pay");
}

void apply_010(const Cpu010 &c)
{
    regs.vbr = c.vbr;
    regs.sfc = c.sfc;
    regs.dfc = c.dfc;
    regs.loop_mode = c.loop_mode;
}

// -- The FPU block of P6CP (T54) ----------------------------------------------------------------------------------------------------------------------------------
// Appended after the 68020 block of a machine whose configuration has a 68881 or a 68882 (p6_config.fpu_model; the block exists with the FPU, whose model is in the
// identity, so the body follows the machine as the 68020 block does), in this order (little endian, 110 bytes):
//   8 x { u16 high  u64 low }  FP0..FP7 as the unit holds them: sign and 15-bit exponent, 64-bit significand. Every pattern is valid (the registers are written by
//                              FMOVEM and FRESTORE with whatever the program has, unnormals and pseudo-denormals included), so no range
//   u32 fpcr (only the bits of 0xFFF0)  u32 fpsr (only the bits of 0x0FFFFFF8)  u32 fpiar
//   u8 fpu_state (0..1: null or idle)  u8 fp_exp_pend (0, or the vector 48..55 of an exception that an instruction posted and the next FPU instruction takes)
//   u32 fsave_ccr  u32 fsave_eo[3]    the FSAVE data of fpp.cpp's file-local fsave_data (patch 0142): the 6888x part of it
// These are the fields of upstream's own save_fpu for a 6888x (that body is under SAVESTATE and is not compiled), packed the way the other blocks are, less what only the 68040
// and 68060 branches of fpp.cpp read: fpu_exp_state and fp_unimp_pend (a save refuses a machine that has them set: it would be a defect of the engine), and fp_opword, fp_ea
// and fp_ea_set, which are written when an exception is posted and read only to build the frame of a 68040, in the same instruction (the frames a 68881/68882 pushes in this engine
// are formats 0 and 3, whose fp_ea is the one just posted). What the unit does not carry: the SoftFloat status (rounding mode and precision follow FPCR and fpp_set_fpcr puts them back; the exception flags are cleared at the start of every instruction that
// reads them), the condition-code tables and masks (fpu_reset derives them from the model), the transient fp_exception/fp_branch/fpu_exp_pre/fp_unimp_ins (set and read
// within one instruction) and floatx80_internal_* (written by each rounding before anything reads it). tests/state/test_state_fpu.py pokes each field.
struct CpuFpu {
    uint16_t high[8] = {};
    uint64_t low[8] = {};
    uint32_t fpcr = 0, fpsr = 0, fpiar = 0;
    uint8_t state = 0, exp_pend = 0;
    uint32_t fsave_ccr = 0, fsave_eo[3] = {};
};

void visit_fpu(Archive &a, CpuFpu &f)
{
    for (unsigned i = 0; i < 8; ++i) {
        f.high[i] = a.val<uint16_t>(regs.fp[i].fpx.high, "fpu.fp.high");
        f.low[i] = a.val<uint64_t>(regs.fp[i].fpx.low, "fpu.fp.low");
    }
    f.fpcr = a.val<uint32_t>(regs.fpcr, "fpu.fpcr");
    f.fpsr = a.val<uint32_t>(regs.fpsr, "fpu.fpsr");
    if (a.mode() != Mode::Measure) {
        a.require((f.fpcr & ~0xfff0u) == 0, "fpu.fpcr: a bit the control register of a 6888x does not have");     // a mask, not a range
        a.require((f.fpsr & ~0x0ffffff8u) == 0, "fpu.fpsr: a bit the status register of a 6888x does not have");
    }
    f.fpiar = a.val<uint32_t>(regs.fpiar, "fpu.fpiar");
    f.state = get<uint8_t>(a, regs.fpu_state, 0, 1, "fpu.state");
    if (a.mode() == Mode::Save) a.require(regs.fpu_exp_state == 0 && regs.fp_unimp_pend == 0, "fpu: a 68040 or 68060 frame state on a 68881 or 68882");
    f.exp_pend = get<uint8_t>(a, regs.fp_exp_pend, 0, 55, "fpu.exp_pend");
    if (a.mode() != Mode::Measure) a.require(f.exp_pend == 0 || f.exp_pend >= 48, "fpu.exp_pend: not the vector of an FPU exception");
    uae_u32 live_ccr = 0, live_eo[3] = {};
    p6_fpu_fsave_get(&live_ccr, live_eo);
    f.fsave_ccr = a.val<uint32_t>(live_ccr, "fpu.fsave.ccr");
    for (unsigned i = 0; i < 3; ++i) f.fsave_eo[i] = a.val<uint32_t>(live_eo[i], "fpu.fsave.eo");
}

void apply_fpu(const CpuFpu &f)
{
    for (unsigned i = 0; i < 8; ++i) {
        regs.fp[i].fpx.high = f.high[i];
        regs.fp[i].fpx.low = f.low[i];
    }
    fpp_set_fpcr(f.fpcr);               // also puts SoftFloat's rounding mode and precision back
    regs.fpsr = f.fpsr;
    regs.fpiar = f.fpiar;
    regs.fpu_state = f.state;
    regs.fpu_exp_state = 0;
    regs.fp_unimp_pend = 0;
    regs.fp_exp_pend = f.exp_pend;
    p6_fpu_fsave_set(f.fsave_ccr, f.fsave_eo);
}

// Normal 040/060 instruction reads bypass caches, as on Normal 030. Translation
// controls are unavailable; preserve the architectural controls and CPU counter.
struct CpuLate {
    uint32_t vbr = 0, msp = 0, cacr = 0, buscr = 0, pcr = 0;
    uint8_t sfc = 0, dfc = 0;
    int32_t instruction_cnt = 0;
};

void visit_late(Archive &a, CpuLate &c)
{
    const bool is060 = currprefs.cpu_model == 68060;
    if (a.mode() != Mode::Measure)
        a.require(!currprefs.mmu_model && !currprefs.cpu_compatible && !currprefs.cpu_cycle_exact &&
                  !currprefs.cpu_memory_cycle_exact && !currprefs.cpu_data_cache, "cpu: only normal no-MMU 040/060 state is supported");
    c.vbr = a.val<uint32_t>(regs.vbr, "cpu.vbr");
    c.msp = a.val<uint32_t>(regs.msp, "cpu.msp");
    c.cacr = a.val<uint32_t>(regs.cacr, "cpu.cacr");
    c.sfc = get<uint8_t>(a, regs.sfc, 0, 7, "cpu.sfc");
    c.dfc = get<uint8_t>(a, regs.dfc, 0, 7, "cpu.dfc");
    c.instruction_cnt = a.val<int32_t>(regs.instruction_cnt, "cpu.instruction_cnt");
    if (a.mode() != Mode::Measure)
        a.require(!(c.cacr & ~(is060 ? UINT32_C(0xf8e0e000) : UINT32_C(0x80008000))), "cpu.cacr: unsupported control bits");
    if (is060) {
        c.buscr = a.val<uint32_t>(regs.buscr, "cpu.buscr");
        c.pcr = a.val<uint32_t>(regs.pcr, "cpu.pcr");
        const uint32_t owner = (currprefs.fpu_model == 68060 ? 0x04300000u : 0x04310000u) |
                               (uint32_t(currprefs.cpu060_revision & 255) << 8);
        if (a.mode() != Mode::Measure) {
            a.require(!(c.buscr & ~UINT32_C(0xf0000000)), "cpu.buscr: unsupported control bits");
            a.require((c.pcr & ~0x43u) == owner && (currprefs.fpu_model || (c.pcr & 2)), "cpu.pcr: wrong processor identity");
        }
    }
}

void apply_late(const CpuLate &c)
{
    regs.vbr = c.vbr; regs.msp = c.msp; regs.cacr = c.cacr;
    regs.sfc = c.sfc; regs.dfc = c.dfc; regs.instruction_cnt = c.instruction_cnt;
    if (currprefs.cpu_model == 68060) { regs.buscr = c.buscr; regs.pcr = c.pcr; }
    // Rebuild derived cache geometry, never refill serialized instruction data.
    set_cpu_caches(false);
}

void visit_internal_fpu(Archive &a, p6_fpu_state &f)
{
    const p6_fpu_context owner{uint32_t(currprefs.fpu_model),
        uint32_t(currprefs.fpu_model == 68040 ? (currprefs.fpu_revision == 0x40 ? 0x40 : 0x41) : 0),
        uint32_t((currprefs.fpu_strict ? 1 : 0) | (currprefs.fpu_no_unimplemented ? 2 : 0))};
    if (a.mode() == Mode::Save) a.require(p6_fpu_state_get(&f), "fpu: internal owner is not ready");
#define FIELD(name) f.name = a.val<std::remove_reference_t<decltype(f.name)>>(f.name, "fpu.internal." #name)
    FIELD(context.model); FIELD(context.revision); FIELD(context.flags);
    for (unsigned i = 0; i < 8; ++i) { FIELD(high[i]); FIELD(low[i]); }
    FIELD(fpcr); FIELD(fpsr); FIELD(fpiar); FIELD(state); FIELD(exp_state);
    FIELD(exp_pend); FIELD(unimp_pend); FIELD(ea); FIELD(opword); FIELD(flags);
#define FRAME(name) FIELD(frame.name);
    P6_FPU_FRAME_SCALARS(FRAME)
#undef FRAME
    for (unsigned i = 0; i < 3; ++i) { FIELD(frame.eo[i]); FIELD(frame.fpt[i]); FIELD(frame.et[i]); FIELD(frame.wbt[i]); }
#undef FIELD
    if (a.mode() != Mode::Measure) a.require(p6_fpu_state_validate(&f, owner), "fpu: invalid internal state or owner");
}

}  // namespace

// Body, little endian, in this order:
//   i32 go.quit_program (-4..0, not -1)
//   u32 pc  u16 sr  u32 d0..d7 a0..a7 (a7 raw: the active stack pointer)  u32 usp  u32 isp
//   u16 ir irc ird read_buffer write_buffer db opcode  u32 latch_rw latch_read latch_write
//   u8 stopped (0..1)  u8 halted (0..13, not 4)  u32 spcflags (CPUINRESET|TRACE|DOTRACE|BLTNASTY|ACTION_REPLAY with owner)
//   u8 ipl0 ipl1 ipl_pin ipl_pin_p (0..7)  u8 ipl_evt_pre_mode (0..1)  i64 ipl_evt ipl_evt_pre ipl_pin_change_evt ipl_pin_change_evt_p (>= 0)
//   u32 xorshiftstate (>= 1)
// A 68010 (T52) appends the block of the 68010 (visit_010 above, 10 bytes) and keeps the SR of a 68000 (it has neither T0 nor M).
// A 68020 machine (the A1200, T24c) appends the block of the 68020 (visit_020 above); the chunk version stays 1 because the body follows the machine, as
// P6RT's does, and the machine is part of the identity. SR then keeps T0 and M (mask 0xF71F instead of 0xA71F). A machine with an FPU (T54) appends the block of the FPU
// after that (visit_fpu above); the model of the FPU is in the identity too.
void visit_cpu(Archive &a)
{
    const bool checks = a.mode() != Mode::Measure;
    const bool m68020 = currprefs.cpu_model == 68020;
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
    if (checks) a.require((sr & ~(currprefs.cpu_model >= 68020 ? kSr68020 : kSr68000)) == 0, currprefs.cpu_model >= 68020 ? "cpu.sr: a bit a 68020/030 does not have" : "cpu.sr: a bit a 68000 does not have");

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
    const uint8_t stopped = get<uint8_t>(a, regs.stopped, 0, currprefs.cpu_model == 68060 ? 2 : 1, "cpu.stopped");
    const uint8_t halted = get<uint8_t>(a, regs.halted, 0, 13, "cpu.halted");
    if (checks) a.require(halted != CPU_HALT_ACCELERATOR_CPU_FALLBACK, "cpu.halted: the accelerator fallback is not in the profile");
    const uint32_t spc = a.val<uint32_t>(uint32_t(regs.spcflags) & kSpcSaved, "cpu.spcflags");
    if (checks) a.require((spc & ~kSpcSaved) == 0, "cpu.spcflags: a flag that cannot be pending at a unit boundary");

    if (checks) a.require(!(spc&SPCFLAG_ACTION_REPLAY) || p6_bridge::hrtmon_bound() || p6_bridge::ar_bound(), "CPU cartridge flag without an owner");
    if (a.reading()) publish_cpu_spc(spc);

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

    // 8. The 68020: its control registers and what its cycle-exact core keeps between two instructions.
    Cpu020 c020;
    if (m68020) visit_020(a, c020);
    Cpu010 c010;
    if (currprefs.cpu_model == 68010) visit_010(a, c010);
    Cpu030Normal c030;
    if (currprefs.cpu_model == 68030) visit_030_normal(a, c030);
    CpuLate late;
    if (currprefs.cpu_model == 68040 || currprefs.cpu_model == 68060) visit_late(a, late);

    // 9. The FPU, when the configuration has one.
    CpuFpu fpu;
    const bool has_fpu = currprefs.fpu_model == 68881 || currprefs.fpu_model == 68882;
    if (has_fpu) visit_fpu(a, fpu);
    p6_fpu_state internal{};
    const bool has_internal = currprefs.fpu_model == 68040 || currprefs.fpu_model == 68060;
    if (has_internal) visit_internal_fpu(a, internal);

    // Non-CE specialties detect the level-7 edge against lastipl. CE never reads it and keeps its original body unchanged.
    uint8_t lastipl = 0;
    if (!currprefs.cpu_cycle_exact) lastipl = get<uint8_t>(a, regs.lastipl, 0, 7, "cpu.lastipl");

    if (a.applying() && a.ok()) {
        quit_program = quit;
        regs.sr = sr;
        regs.t1 = (sr >> 15) & 1;
        regs.t0 = (sr >> 14) & 1;       // 0 on a 68000: the mask above refused it
        regs.s = (sr >> 13) & 1;
        regs.m = (sr >> 12) & 1;
        regs.intmask = (sr >> 8) & 7;
        SET_XFLG((sr >> 4) & 1);
        SET_NFLG((sr >> 3) & 1);
        SET_ZFLG((sr >> 2) & 1);
        SET_VFLG((sr >> 1) & 1);
        SET_CFLG(sr & 1);
        // HRTMon's bank map arrives in P6RM. A direct lookup here can enter dummy_xlate,
        // whose cpu_halt changes SR/IPL before the real bank exists. Keep only the numeric PC;
        // rebuild_ram binds it after mapping, without refilling the serialized prefetch queue.
        if (p6_bridge::hrtmon_bound() || p6_bridge::ar_bound()) {
            regs.pc_p = regs.pc_oldp = nullptr;
            m68k_setpci(pc);
        } else {
            m68k_setpc_normal(pc);
        }
        regs.stopped = stopped;
        regs.halted = halted;             // m68k_go_unit sets SPCFLAG_CHECK again for a halted CPU when the unit resumes
        regs.spcflags = int32_t(spc);
        regs.lastipl = lastipl;
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
        if (m68020) apply_020(c020);
        if (currprefs.cpu_model == 68010) apply_010(c010);
        if (currprefs.cpu_model == 68030) apply_030_normal(c030);
        if (currprefs.cpu_model == 68040 || currprefs.cpu_model == 68060) apply_late(late);
        if (has_fpu) apply_fpu(fpu);
        if (has_internal) a.require(p6_fpu_state_apply(&internal), "fpu: internal owner changed after validation");
        // Not touched, on purpose: ir, ird and irc are not refilled from memory (restore_cpu_finish's fill_prefetch_quick would overwrite them with
        // what memory holds now, which can differ from the prefetch queue), doint() is not called (it would recompute ipl_pin from the chipset,
        // whose delayed events are still pending), and lastipl is the non-CE edge state (zero in CE); vbr, sfc, dfc, msp and the rest of the 020 are the block's.
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
                if (sec_id[i] == 14) a.require(currprefs.cs_cdtvcd && sec_data[i] == 0, "CDTV subcode event has no owner or invalid data");
                if (sec_id[i] == 15) a.require(p6_ar_cia_handler() && sec_data[i] <= 1, "AR CIA event has no owner or invalid data");
                if (sec_id[i] == 13) a.require(sec_data[i] <= 255, "optical event horizontal position is out of range");
                any = true;
                if (sec_time[i] < earliest) earliest = sec_time[i];
            } else {
                a.require(sec_id[i] == 0 && sec_time[i] == 0 && sec_data[i] == 0, "an inactive secondary event has data");
            }
        }
    }
    // ev_misc is armed for the earliest of the slots (MISC_handler), or earlier when a slot was removed without re-arming it.
    if (checks) a.require(!any || (main_active[ev_misc] && main_time[ev_misc] <= earliest), "secondary events are pending but ev_misc is not armed for them");

    if (a.reading() && a.ok()) publish_scheduler_cycle(now);
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
