// State probe (T14b): one canonical digest per subsystem of the machine, computed field by field from what can be reached outside the
// engine, for the tests that will prove a restore (T15) and for comparing a reload with a clean process. It is a test tool, not part
// of the libraries: the CMake target p6_state_probe compiles it with the engine's headers.
//
// Canonical means: every value is written by name, in a fixed order, as a little-endian integer of a fixed width; no struct is hashed
// as memory (padding), no pointer or address is hashed, and what is scheduled (not what a dead event left behind) is what counts.
// Each digest is SHA-256 over "p6/probe/v1/<subsystem>" and a zero byte, then those bytes, so it cannot be mistaken for the digest of
// another subsystem, for an envelope hash (docs/adr/0009-state-format.md) or for the digest of a Waterbox snapshot.
//
// What a subsystem's digest does NOT see is the state of the engine that is file-local or function-local (the CIA registers, the floppy
// mechanics, Denise's input ring and its cursors, the copper, the audio filter, the event allocator...): manifests/state-inventory.json
// says, per component, whether a probe covers it or waits for an accessor (T15).
//
//   p6_state_probe ROM [--units N] [--every K] [--loads L] [--ntsc] [--machine a500|a500og|a500plus|a600|a1000|a1200|a2000og|a2000] [--rate HZ] [--port N:type:buttons:dx:dy]... [--dump SUBSYSTEM] [--list]
//                      [--seed S] [--adf FILE]... [--hdf FILE]... [--event-at UNIT DRIVE INDEX SEQUENCE]... [--reset-at UNIT]... [--append-at UNIT FILE]... [--only CHUNK]... [--save-at UNIT FILE]... [--save-fields-at UNIT FILE]... [--load-at UNIT FILE]...
//                      [--load-fault-at UNIT FILE]... [--load-fault-head-at UNIT FILE]... [--mutate-at UNIT CHUNK FILE]... [--poke-at UNIT WHAT]... [--public-at UNIT]... [--public-save-at UNIT FILE]... [--public-load-at UNIT FILE]...
// --save-at / --load-at (T15a) run after unit UNIT (0 = right after p6_load) through the native state container with placeholders for the
// chunks that do not exist yet, so a state made here restores only what the registry implements. They print "save UNIT BYTES SHA256",
// "load UNIT CODE MESSAGE" and, after a load, the digests again as "digest UNIT SUBSYSTEM HEX BYTES" under a line "after-load UNIT".
// --only CHUNK limits what a load applies to the named chunks (T15b1). --save-fields-at is --save-at and also prints "field CHUNK NAME OFFSET SIZE"
// for every field written (offsets in the whole state). --mutate-at UNIT CHUNK FILE loads FILE, then every one-byte change of that chunk's body with
// its checksums made right, and prints "mutate UNIT CHUNK bytes=N attempts=A accepted=B refused=C faulted=D changed=E verdicts=SHA256".
// --poke-at UNIT ev2:ID schedules a real secondary event with the handler a state calls ID (events a program rarely leaves pending at a boundary) and
// ev2-foreign one with a handler outside the table. --handler-table prints "handler ID OK|BAD" for every id against the engine's own functions.
// --poke-at UNIT exception|spc-int|lastipl|clear changes a live CPU variable that is never set at a unit boundary (and clear puts them back), so that
// a save made right after it must be refused.
// --load-fault-head-at does the same for the reset head that a load into an image that has not started runs first (use it at unit 0).
// --load-fault-at loads with a failure injected into the apply pass (the Faulted path): prints "load", "run-after-fault CODE MESSAGE", "unload CODE",
// "reload CODE" and the digests of the reloaded image under "after-load UNIT".
// --digest-at UNIT prints the digests of the machine as it is then (under "after-load UNIT", like a load does), for after a poke. --rtc-at UNIT prints
// the clock in the open, "rtc UNIT STATUS SECONDS TICKS SUBSECOND CYCLE D E F" (p6_rtc_save brings it up to the cycle counter); --poke-at UNIT
// rtc-fault latches the clock's sticky fault, as a failed read would, and cs-rtc names another clock chip than the profile's.
// --poke-at UNIT ce020-extracycles|extra|prefetchend|span|instruction-cnt|vbr|cacr|cache-line changes one field of the 68020 that the CPU chunk carries (T24c), for the tests that show that each one decides the future.
// --cpu 68000|68010|68020 (T52) names the CPU of the machine (p6_config.cpu_model); --poke-at UNIT cpu010-vbr|cpu010-sfc|cpu010-dfc|cpu010-loop changes one field of the 68010 that the CPU chunk carries.
// --poke-at UNIT cia-tod2 puts CIA-A in the state of a TOD increment pending, without the event that carries it (a save must refuse it) and cia-tod2-event
// with the event; cia-ovl flips the overlay latch against PRA/DDRA and kbmode names a keyboard other than the A500's (clear puts the CIAs and the mode back).
// --poke-at UNIT keyinject|ignorerelease|turbo|mousespeed|resetwarning|released put the input in a state outside the profile (a text typed into the ring, a release to
// swallow, turbo emulation, another mouse speed, a reset warning), and released clears the engine's key table as a reset of the keyboard does, without telling the bridge (a save is
// then valid and says that no key is applied), and engine-key|engine-button|engine-axis|engine-mouse
// make one entry of the engine's edge memory differ from the bridge's; a save made then must be refused.
// --key UNIT CODE down|up and --port-at UNIT N:type:buttons:dx:dy (T15c3) change the packet the probe sends from that unit on (a key held, a mouse that moves only in some
// units), so that a boundary can find the keyboard in the middle of a handshake or the mouse in flight; both are applied right before the unit they name.
// --hdf mounts a hard file at p6_load (T29c; after the --adf images, in the order given), so that the controller and the overlay of its writes are part of the "ram" digest.
// --adf mounts a disk image at p6_load (its content id is the SHA-256 of its bytes), so that the media identity of a state can be tested.
// --event-at UNIT DRIVE INDEX SEQUENCE (T15h) calls p6_media_event_enqueue after UNIT (INDEX -1 ejects; at UNIT 0 the event is a boot medium) and prints "event UNIT CODE MESSAGE";
// --append-at UNIT FILE (T17e) calls p6_media_append with the image in FILE and prints "append UNIT CODE INDEX MESSAGE"; the medium is not mounted (use --event-at with the index).
// --reset-at UNIT calls p6_reset and prints "reset UNIT CODE MESSAGE". Both only queue: the next unit applies them, as a host's call would.
// --public-at calls the public p6_state_* functions (T15 released them) and prints "public UNIT cap_state=B bound=C save=C load=C written=N junk=C small=C MESSAGE":
// the bound, a save into a buffer of exactly that size, a save into a buffer of 16 bytes (small: CAPACITY), a load of 16 bytes of zeros (junk: refused) and a load of the state just saved,
// and then "public-size UNIT BOUND WRITTEN SHA256". --public-save-at UNIT FILE and --public-load-at UNIT FILE are --save-at and --load-at through the public ABI and print the same lines
// ("save UNIT BYTES SHA256 bound=B", "load UNIT CODE MESSAGE" and the digests under "after-load UNIT"), so that the state of a process can be loaded into another one (DET03).
// Output, one line per digest: "digest UNIT SUBSYSTEM HEX BYTES", after "pass K" when there is more than one load. Every unit also prints
// "pcm UNIT HEX FRAMES" (T15i3), the SHA-256 of the stereo samples that unit handed over, which no digest sees, "picture UNIT none" or "picture UNIT WxH pFIELD CADENCE HEX" (T15j), the
// picture the unit handed over (none when it had no new one) with its field parity and cadence, and every chipset digest is followed by
// "chip UNIT GROUP HEX" for each group of what it hashes (beam, counters, irq_dma, clxdat, lof, timing, registers). --poke-at UNIT beam:VPOS:LINEAR:HPOS sets the beam counters. --dump also prints the
// first 4 KiB of the canonical bytes of one subsystem ("bytes UNIT SUBSYSTEM HEX"), to find what differs between two digests.
#include "p6.h"
#include "session.h"
#include "state.h"
#include "drive_sound.h"
#include "config.h"
#include "p6_state_access.h"
#include "sha256.h"
#include "media.h"
#include "blob_io.h"
#include "rtc_time.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "blitter.h"
#include "newcpu.h"
#include "fpp.h"
#include "video_present.h"
#include "inputdevice.h"
#include "input.h"
#include "keybuf.h"
#include "savestate.h"

#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern void p6_mbres_get(uae_u8 *r);
extern bool p6_expansion_state_get(uint32_t *w, unsigned n, uint8_t *autoconfig);    // expansion.cpp, patch 0111
extern bool p6_frame_raster(int *width, int *height, int *stride, uint8_t **first);     // custom.cpp, patch 0103

extern uae_u32 p6_rng_get(void);                                       // main.cpp, patch 0076: the live generator
extern void p6_events_get(int *idx, int *next, int *cnt, int *restart, int *remain);   // events.cpp, patch 0078: the secondary scheduler's statics
extern int p6_custom_extra_cycle_get(void);                            // custom.cpp, patch 0079: the half-CCK remainder of the CPU
extern bool p6_lightpen_latch_get(int32_t out[4]);
extern int p6_custom_ce020_extra_get(void);                            // custom.cpp, patch 0108: the same for the cycle-exact 68020
extern bool p6_caches020_get(uae_u32 *data, uae_u32 *tag, uae_u8 *valid, unsigned n);      // newcpu.cpp, patch 0108: the instruction cache of the 68020
extern bool p6_caches020_set(const uae_u32 *data, const uae_u32 *tag, const uae_u8 *valid, unsigned n);
extern void p6_custom_ce020_extra_set(int v);
extern void p6_rtc_msm_get(uae_u8 out[3]);                             // cia.cpp, patch 0081: the MSM6242B's control registers D, E and F
extern void p6_rtc_ricoh_get(uae_u8 out[35]);
extern void rtc_hardreset(void);
extern void p6_cia_get(int num, p6_cia_raw *out);                      // cia.cpp, patch 0082: both CIAs and what cia.cpp keeps beside them
extern void p6_cia_set(int num, const p6_cia_raw *in);
extern void p6_cia_misc_get(p6_cia_misc_raw *out);
extern void p6_cia_misc_set(const p6_cia_misc_raw *in);
namespace p6_state { uint8_t scheduler_handler_id(evfunc2 f); evfunc2 scheduler_handler(uint8_t id); }    // bridge/state_cpu.cpp: the ids a state gives the handlers
extern evfunc2 p6_custom_event2_handler(int which);                    // custom.cpp, patch 0079
extern evfunc2 p6_disk_motordelay_handler(void);                       // disk.cpp, patch 0080
extern uae_u8 *save_audio(int nr, size_t *len, uae_u8 *dstptr);       // audio.cpp: compiled in the profile, reads only
extern void p6_input_get(p6_input_raw *out);                           // inputdevice.cpp, patch 0085: the gameports, the pot lines, the host's edge memory, the key table
extern int p6_keybuf_get(uae_u8 *codes, int max);                      // keybuf.cpp, patch 0084: the keyboard ring from the oldest code to the newest
extern void p6_input_set(const p6_input_raw *in);
extern int p6_input_engine_released_keys;
extern void p6_fpu_fsave_get(uae_u32 *ccr, uae_u32 *eo);               // fpp.cpp, patch 0142
extern void p6_fpu_fsave_set(uae_u32 ccr, const uae_u32 *eo);

namespace {

struct Canon {
    std::vector<uint8_t> bytes;
    template <class T> Canon &put(T value)
    {
        uint64_t v = uint64_t(static_cast<typename std::make_unsigned<T>::type>(value));
        for (size_t i = 0; i < sizeof(T); ++i) bytes.push_back(uint8_t(v >> (8 * i)));
        return *this;
    }
    Canon &raw(const void *data, size_t size)
    {
        const uint8_t *p = static_cast<const uint8_t *>(data);
        bytes.insert(bytes.end(), p, p + size);
        return *this;
    }
};

struct Probe {
    const char *subsystem;
    void (*collect)(Canon &);
};

void cpu(Canon &c)
{
    for (uae_u32 r : regs.regs) c.put(r);                  // D0-D7, A0-A7
    MakeSR();
    c.put(uae_u32(m68k_getpc())).put(regs.sr).put(regs.usp).put(regs.isp);
    c.put(regs.ir).put(regs.irc).put(regs.ird).put(regs.opcode).put(regs.db).put(regs.write_buffer).put(regs.read_buffer);
    c.put(regs.stopped).put(regs.halted).put(regs.intmask).put(regs.lastipl);
    c.put(regs.ipl[0]).put(regs.ipl[1]).put(regs.ipl_pin).put(regs.ipl_pin_p);
    c.put(int64_t(regs.ipl_evt)).put(int64_t(regs.ipl_evt_pre)).put(regs.ipl_evt_pre_mode);
    c.put(int64_t(regs.ipl_pin_change_evt)).put(int64_t(regs.ipl_pin_change_evt_p));
    c.put(uae_u32(regs.spcflags & ~SPCFLAG_UNIT_END));     // the unit-end request is how the boundary is reached, not machine state
    c.put(int32_t(quit_program)).put(uint32_t(p6_rng_get()));      // T15b1: what the chunk carries and the digest did not see
    c.put(regs.chipset_latch_rw).put(regs.chipset_latch_read).put(regs.chipset_latch_write);
    if (currprefs.cpu_model == 68020) {
        // T24c: the 68020 appends to the digest what the CPU chunk carries (its control registers, the prefetch queue with its pipeline and the instruction cache). The 68000
        // machines' digest is unchanged byte for byte. P6_PROBE_020X=1 adds what the chunk does NOT carry and the cycle-exact core keeps, to attribute a divergence to a field
        // (it is a diagnostic: the DET02 tests run without it, and a field goes into the chunk only when its absence changed the future).
        c.put(regs.msp).put(regs.vbr).put(regs.sfc).put(regs.dfc).put(regs.caar).put(regs.cacr);
        c.put(int32_t(regs.instruction_cnt)).put(int64_t(regs.ce020extracycles));
        c.put(int64_t(regs.ce020prefetchendcycle)).put(int64_t(regs.ce020startcycle)).put(int64_t(regs.ce020endcycle)).put(int32_t(p6_custom_ce020_extra_get()));
        c.put(regs.prefetch020addr).put(regs.cacheholdingaddr020).put(regs.cacheholdingdata020);
        for (int i = 0; i < CPU_PIPELINE_MAX; ++i) c.put(regs.prefetch020[i]).put(regs.prefetch020_valid[i]);
        c.put(int32_t(regs.pipeline_pos)).put(int32_t(regs.pipeline_r8[0])).put(int32_t(regs.pipeline_r8[1])).put(int32_t(regs.pipeline_stop));
        uae_u32 data[CACHELINES020], tag[CACHELINES020];
        uae_u8 valid[CACHELINES020];
        p6_caches020_get(data, tag, valid, CACHELINES020);
        for (int i = 0; i < CACHELINES020; ++i) c.put(data[i]).put(tag[i]).put(valid[i]);
        if (getenv("P6_PROBE_020X")) {
            c.put(regs.cacheholdingdata_valid).put(regs.fc030).put(int32_t(regs.memory_waitstate_cycles));
            c.put(uint8_t(regs.ce020memcycle_data)).put(int32_t(regs.ce020_tail)).put(int64_t(regs.ce020_tail_cycles));
        }
    }
    if (currprefs.cpu_model == 68010) {
        // T52: a 68010 appends what its block of the CPU chunk carries; the digest of every other CPU is unchanged byte for byte.
        c.put(regs.vbr).put(regs.sfc).put(regs.dfc).put(regs.loop_mode);
    }
    if (currprefs.cpu_model == 68030)
        c.put(regs.vbr).put(regs.msp).put(regs.caar).put(regs.cacr).put(regs.sfc).put(regs.dfc).put(int32_t(regs.instruction_cnt));
    if (currprefs.fpu_model) {
        // T54: with an FPU the digest also holds what the FPU block of the CPU chunk carries (the data registers as bit patterns, the control registers, the pending-exception words
        // and the FSAVE data). A machine without one has the digest it always had.
        for (int i = 0; i < 8; ++i) c.put(regs.fp[i].fpx.high).put(regs.fp[i].fpx.low);
        c.put(regs.fpcr).put(regs.fpsr).put(regs.fpiar);
        c.put(regs.fpu_state).put(regs.fp_exp_pend);
        uae_u32 ccr = 0, eo[3] = {};
        p6_fpu_fsave_get(&ccr, eo);
        c.put(ccr).put(eo[0]).put(eo[1]).put(eo[2]);
    }
}

void scheduler(Canon &c)
{
    // The bridge's tick accounting reads the same counter (cycles_seen): it is the scheduler's, and at a boundary both are equal.
    c.put(int64_t(get_cycles())).put(p6_session::session.cycles_seen);
    // Only what is scheduled is state: the time of an event that is not active is what its last firing left behind. oldcycles is read even while
    // an entry is inactive (cia.cpp), so it counts always.
    for (const ev &e : eventtab) {
        c.put(uint8_t(e.active != 0));
        if (e.active) c.put(int64_t(e.evtime));
        c.put(int64_t(e.oldcycles));
    }
    for (int i = 0; i < ev2_max; ++i) {
        c.put(uint8_t(eventtab2[i].active != 0));
        if (eventtab2[i].active) c.put(int64_t(eventtab2[i].evtime)).put(uint64_t(eventtab2[i].data)).put(p6_state::scheduler_handler_id(eventtab2[i].handler));
    }
    // T15b2: what the scheduler's chunk carries and the digest did not see. nextevent is derived from the table and must come back with it.
    int idx = 0, next = 0, cnt = 0, restart = 0, remain = 0;
    p6_events_get(&idx, &next, &cnt, &restart, &remain);
    c.put(int64_t(nextevent)).put(int32_t(p6_custom_extra_cycle_get()));
    c.put(int32_t(idx)).put(int32_t(next)).put(int32_t(cnt)).put(int32_t(restart)).put(int32_t(remain));
    c.put(uint32_t(hsync_counter)).put(uint32_t(vsync_counter));
}

void ram(Canon &c)
{
    c.put(uint32_t(chipmem_bank.allocated_size)).raw(chipmem_bank.baseaddr, chipmem_bank.allocated_size);
    c.put(uint32_t(bogomem_bank.allocated_size));
    if (bogomem_bank.baseaddr) c.raw(bogomem_bank.baseaddr, bogomem_bank.allocated_size);
    // T49: a Zorro II Fast RAM board adds itself and the autoconfig chain it is on (nothing for the machines without one, whose digest is what it was).
    if (fastmem_bank[0].allocated_size) {
        c.put(uint32_t(fastmem_bank[0].allocated_size)).raw(fastmem_bank[0].baseaddr, fastmem_bank[0].allocated_size);
        uint32_t w[13] = {};
        uint8_t ac[256] = {};
        const bool got = p6_expansion_state_get(w, 13, ac);
        c.put(uint8_t(got));
        for (uint32_t v : w) c.put(v);
        c.raw(ac, sizeof ac);
    }
    // T29c: a machine with a hard file adds its controller and the overlay of its writes, as the state writes them (the section of P6DK: the trap area, the UAEFS board, the units of the
    // file system and of uaehf.device, the EXTER queue and the dirty chunks). Nothing for a machine without one, whose digest is what it was.
    if (p6_bridge::motherboard_ide(p6_session::session.config)) {
        uint8_t r[4]; p6_mbres_get(r); c.raw(r, sizeof r);
    }
    if (p6_bridge::hard_file_count() || p6_bridge::motherboard_ide(p6_session::session.config)) {
        p6_state::Archive measure = p6_state::Archive::measurer();
        p6_state::visit_hdf(measure);
        std::vector<uint8_t> section(size_t(measure.position()));
        p6_state::Archive save = p6_state::Archive::saver(section.data(), section.size());
        p6_state::visit_hdf(save);
        c.put(uint8_t(save.ok())).put(uint64_t(save.position()));
        if (save.ok()) c.raw(section.data(), size_t(save.position()));
    }
}

void rtc(Canon &c)
{
    // A machine without the clock chip (the A600 of the profile) has no clock state: its chunk is empty (T23b), and nothing reads the service, so its anchor is not compared.
    if (currprefs.cs_rtc == 0) {
        c.put(int32_t(-1));
        return;
    }
    p6_rtc_state s{};
    const int status = p6_rtc_save(&s);
    c.put(int32_t(status));
    if (status == 0)
        c.put(s.clock.seconds).put(s.clock.ticks_per_second).put(s.clock.subsecond).put(s.clock.seed).put(s.clock.version).put(s.cycle);
    c.put(int32_t(p6_rtc_status()));
    if (currprefs.cs_rtc == 2) {
        uae_u8 ricoh[35];
        p6_rtc_ricoh_get(ricoh);
        c.raw(ricoh, sizeof ricoh);
    } else {
        uae_u8 msm[3];
        p6_rtc_msm_get(msm);                                // T15c1: what the chunk carries and the digest did not see
        c.put(msm[0]).put(msm[1]).put(msm[2]);
    }
}

// T15c2: every field of both CIAs and of what cia.cpp keeps beside them that the chunk carries. The timers are the raw counters, in the epoch of
// eventtab[ev_cia].oldcycles; passed, timerval_prev, kbcode and the rest that are written before they are read are not state and are not hashed.
void cias(Canon &c)
{
    for (int n = 0; n < 2; ++n) {
        p6_cia_raw r{};
        p6_cia_get(n, &r);
        c.put(r.pra).put(r.prb).put(r.dra).put(r.drb);
        for (const p6_cia_timer_raw &x : r.t) c.put(x.timer).put(x.latch).put(x.inputpipe).put(x.loaddelay).put(x.preovfl).put(x.cr);
        c.put(r.tod).put(r.tol).put(r.alarm).put(r.tlatch).put(r.todon).put(r.tod_event_state).put(r.tod_offset);
        c.put(r.icr1).put(r.icr2).put(r.icr_change).put(r.imask).put(r.sdr).put(r.sdr_buf).put(r.sdr_load).put(r.sdr_cnt);
    }
    p6_cia_misc_raw m{};
    p6_cia_misc_get(&m);
    c.put(m.cia_cycle_accurate).put(m.kbstate).put(m.kblostsynccnt).put(m.internaleclockphase).put(m.led).put(m.led_old_brightness).put(m.led_cycle);
    c.put(m.led_cycles_on).put(m.led_cycles_off).put(m.powerled).put(m.powerled_brightness).put(m.oldovl);
    c.put(uint8_t(&get_mem_bank(0) == &chipmem_bank));    // what the overlay latch selects at address 0: Chip RAM, or the ROM while the latch is set
}

// The chipset digest in contiguous groups, so that a divergence can be told apart by what it is (T15i3): the groups are written one after the other in the order the
// digest has always had, and each is also digested alone under "chip UNIT GROUP HEX".
void chip_beam(Canon &c) {
    c.put(int32_t(vpos)).put(int32_t(linear_vpos)).put(agnus_hpos);
    int32_t latch[4];
    const bool lpen = p6_lightpen_latch_get(latch);
    if (lpen || latch[3]) for (int32_t value : latch) c.put(value);
}
void chip_counters(Canon &c) { c.put(hsync_counter).put(vsync_counter); }
// Bits 14 and 13 of dmacon are not state: a CPU read of DMACONR stores the derived blitter busy and zero flags into the live variable (DMACONR) and the next write of DMACON clears
// them, so a machine whose program reads DMACONR has them where one that loaded the state, which holds the register image without them, does not (T15k: the Kickstart 2.05 does it on the A500).
void chip_irq_dma(Canon &c) { c.put(uint16_t(dmacon & ~0x6000)).put(intena).put(intreq).put(adkcon); }
// Bit 15 is not state: a read of CLXDAT is (clxdat | 0x8000) and restore_custom stores the saved image, bit included, so a restored machine has it set where a live one does not.
void chip_clxdat(Canon &c) { c.put(uint16_t(clxdat & 0x7FFF)); }
void chip_lof(Canon &c) { c.put(uint8_t(lof_store)).put(uint8_t(lof_display)); }
void chip_timing(Canon &c) { c.put(int32_t(maxvpos)).put(int32_t(maxhpos)).put(beamcon0).put(new_beamcon0).put(htotal).put(vtotal); }
void chip_registers(Canon &c) { for (int i = 0; i < 256; ++i) c.put(custom_storage[i].value); }        // the last value written to each register (not its writer's pc)
// T24c: the AGA palette as Agnus holds it (the body CAGA, which reads without side effects; Denise's copy and the registers that select the bank show in the picture and in CHIP). Nothing for a machine without AGA, so the chipset digest of the A500 and the A600 is the one it was.
void chip_aga(Canon &c)
{
    if (!(currprefs.chipset_mask & CSMASK_AGA)) return;
    uae_u8 buffer[1024 + 64];
    size_t length = 0;
    if (save_custom_agacolors(&length, buffer)) c.put(uint32_t(length)).raw(buffer, length);
}

const Probe kChipGroups[] = {{"beam", chip_beam}, {"counters", chip_counters}, {"irq_dma", chip_irq_dma}, {"clxdat", chip_clxdat}, {"lof", chip_lof},
                             {"timing", chip_timing}, {"registers", chip_registers}, {"aga", chip_aga}};

void chipset(Canon &c)
{
    // The beam and the registers a CPU or a DMA slot can read back; Denise's input ring is left out until its cursors have an accessor.
    for (const Probe &group : kChipGroups) group.collect(c);
}

void audio(Canon &c)
{
    // What the upstream audio serializer knows of each channel (state, volume, length, period, data, pointers, event time): a subset.
    for (int channel = 0; channel < 4; ++channel) {
        uae_u8 buffer[128] = {};
        size_t length = 0;
        save_audio(channel, &length, buffer);
        c.put(uint32_t(length)).raw(buffer, length);
    }
}

// T15c3: every field of the input that the chunk carries: the pot lines, both gameports, the engine's edge memory and key table, the keyboard ring as a list,
// and what the bridge remembers it applied. The counters of JOYxDAT are the low bytes of mouse_x and mouse_y.
void input(Canon &c)
{
    p6_input_raw r{};
    p6_input_get(&r);
    c.put(r.potgo_value);
    for (int p = 0; p < 2; ++p)
        for (int i = 0; i < 2; ++i) c.put(r.pot_cap[p][i]).put(r.pot_dat[p][i]).put(r.pot_dat_act[p][i]);
    for (const p6_input_port_raw &g : r.port) {
        c.put(g.joybutton).put(g.joydir).put(g.oleft).put(g.oright).put(g.otop).put(g.obot);
        for (int k = 0; k < 4; ++k) c.put(g.mouse_delta[k]).put(g.mouse_deltanoreset[k]);
        c.put(g.mouse_x).put(g.mouse_y).put(g.mouse_frame_x).put(g.mouse_frame_y);
    }
    c.put(r.input_frame).put(r.prev_input_frame).put(r.prev_input_vpos);
    for (int j = 0; j < 2; ++j) {
        c.put(r.joy_buttonmask[j]);
        for (int a = 0; a < 2; ++a)
            for (int i = 0; i < 9; ++i) c.put(r.joy_states[j][a][i]);
    }
    c.put(r.mouse_buttonmask);
    for (int k = 0; k < 16; ++k) c.put(r.keys[k]);
    c.put(r.reset_seq_mode).put(r.reset_seq);
    uae_u8 ring[255] = {};
    const int n = p6_keybuf_get(ring, 255);
    c.put(int32_t(n));
    for (int i = 0; i < n; ++i) c.put(ring[i]);
    p6_applied_raw ap{};
    p6_bridge::applied_get(&ap);
    for (int k = 0; k < 4; ++k) c.put(ap.keys[k]);
    for (int p = 0; p < 2; ++p) {
        c.put(ap.type[p]).put(ap.buttons[p]).put(ap.axis[p][0]).put(ap.axis[p][1]);
    }
    for (int p = 0; p < 2; ++p)
        if (r.cd32_enabled[p]) c.put(r.cd32_shifter[p]).put(r.cd32_clock[p]);
    if (r.second_mouse_enabled) c.put(r.second_mouse_enabled);
    if (r.pointer_enabled) c.put(r.pointer_port).put(r.pointer_cx).put(r.pointer_cy);
}

void video(Canon &c)
{
    // The rows of the drawing buffer as they are: the renderer writes only the ones it draws, so they are state (PIXL of P6VD), and a unit that draws none hands over what they hold.
    // Not the hand-over bookkeeping of the port (the last frame it captured, its pass and frame counters): that is not state, and what a unit hands over is the "picture" line
    // the probe prints (T15j). Before T15j this digest was the last captured frame, which a new image that loaded a state taken in a unit without a picture did not have.
    int width = 0, height = 0, stride = 0;
    uint8_t *first = nullptr;
    const bool have = p6_frame_raster(&width, &height, &stride, &first);
    c.put(uint8_t(have));
    if (!have) return;
    c.put(int32_t(width)).put(int32_t(height));
    for (int y = 0; y < height; ++y) c.raw(first + size_t(y) * size_t(stride), size_t(width) * 4);
}

void bridge(Canon &c)
{
    const p6_session::Session &s = p6_session::session;
    c.put(int32_t(s.state)).put(s.unit_index).put(s.master_ticks).put(s.audio_frames_total);
    c.put(uint8_t(s.handed_over)).put(uint8_t(s.reset_queued)).put(uint8_t(s.started)).put(uint8_t(s.have_sequence)).put(s.last_sequence);
    c.put(s.media_queued);
    for (uint32_t i = 0; i < s.media_queued && i < p6_session::Session::kMediaQueue; ++i)
        c.put(s.media_queue[i].type).put(s.media_queue[i].drive).put(s.media_queue[i].media_index);
    for (int32_t index : s.drive_media) c.put(index);
    // The medium each drive of the engine holds, which is not what the session believes it holds (a change in flight, or a state loaded into a drive with another disk): the CRC
    // drive_insert measured, the one save_disk puts in DSKn (T15d2). The rest of the drive mechanics is not here.
    for (int n = 0; n < int(p6_bridge::resolve_floppy_drives(s.config)); ++n) {
        uint8_t body[64];
        size_t length = 0;
        c.put(save_disk(n, &length, body, false) && length == 24 ? uint32_t(body[12]) << 24 | uint32_t(body[13]) << 16 | uint32_t(body[14]) << 8 | body[15] : 0u);
    }
    c.put(s.media_count).raw(s.firmware_sha256, sizeof s.firmware_sha256);
    for (uint32_t i = 0; i < s.media_count && i < P6_MAX_MEDIA; ++i) c.raw(s.media_content_id[i], P6_CONTENT_ID_SIZE);
    c.put(s.config.machine).put(s.config.video_standard).put(s.config.audio_rate_hz).put(s.config.flags).put(s.config.random_seed)
        .put(s.config.rtc_epoch_seconds);
    if (p6_bridge::resolve_floppy_drives(s.config) != 2) c.put(p6_bridge::resolve_floppy_drives(s.config));
    if (!p6_bridge::memory_is_preset(s.config)) c.put(s.config.chip_kib).put(s.config.slow_kib).put(s.config.fast_kib);
}

const Probe kProbes[] = {{"cpu", cpu}, {"scheduler", scheduler}, {"ram", ram}, {"rtc", rtc}, {"cia", cias}, {"chipset", chipset},
                         {"audio", audio}, {"input", input}, {"video", video}, {"bridge", bridge}};

std::string hex(const uint8_t *data, size_t size)
{
    std::string text;
    char two[3];
    for (size_t i = 0; i < size; ++i) { snprintf(two, sizeof two, "%02x", data[i]); text += two; }
    return text;
}

const char *g_dump = nullptr;

void print_digests(uint64_t unit)
{
    for (const Probe &probe : kProbes) {
        Canon canon;
        canon.bytes.assign({'p', '6', '/', 'p', 'r', 'o', 'b', 'e', '/', 'v', '1', '/'});
        canon.raw(probe.subsystem, strlen(probe.subsystem));
        canon.put(uint8_t(0));
        const size_t header = canon.bytes.size();
        probe.collect(canon);
        uint8_t digest[32];
        p6_sha256_hash(canon.bytes.data(), canon.bytes.size(), digest);
        printf("digest %" PRIu64 " %s %s %zu\n", unit, probe.subsystem, hex(digest, 32).c_str(), canon.bytes.size() - header);
        if (!strcmp(probe.subsystem, "chipset")) {
            for (const Probe &group : kChipGroups) {
                Canon part;
                group.collect(part);
                p6_sha256_hash(part.bytes.data(), part.bytes.size(), digest);
                printf("chip %" PRIu64 " %s %s\n", unit, group.subsystem, hex(digest, 32).c_str());
            }
        }
        if (g_dump && !strcmp(g_dump, probe.subsystem))        // the first 4 KiB: enough for every field, not for RAM or the raster
            printf("bytes %" PRIu64 " %s %s\n", unit, probe.subsystem, hex(canon.bytes.data() + header, std::min<size_t>(canon.bytes.size() - header, 4096)).c_str());
    }
}

bool read_file(const char *path, std::vector<uint8_t> *data);

std::vector<uint8_t> g_rom;          // what p6_load was given, for the operations that load again
std::vector<p6_media> g_media;
std::vector<std::vector<uint8_t>> g_appended;      // the images --append-at registered: borrowed by the bridge until p6_unload

enum class OpKind { Save, SaveFields, Load, LoadFault, LoadFaultHead, Mutate, Poke, HandlerTable, Digest, Rtc, Reset, Event, Append, Public, PublicSave, PublicLoad, Export, Status, Ram, RamOut };

struct StateOp {
    uint64_t unit;
    OpKind kind;
    const char *path;
    const char *chunk;      // Mutate: the name of the chunk whose body is changed; Event: the drive
    const char *extra = nullptr;      // Event: the sequence number
};

uint32_t g_only = 0;        // --only: the chunks a load applies (bit i = chunk i); 0 = all

// One digest of the whole machine, to see whether a refused state changed anything.
void combined_digest(uint8_t out[32])
{
    p6_sha256 context;
    p6_sha256_init(&context);
    for (const Probe &probe : kProbes) {
        Canon canon;
        probe.collect(canon);
        p6_sha256_update(&context, canon.bytes.data(), canon.bytes.size());
    }
    p6_sha256_final(&context, out);
}

uint64_t le64(const uint8_t *p)
{
    uint64_t v = 0;
    for (int i = 7; i >= 0; --i) v = v << 8 | p[i];
    return v;
}

void print_field(void *, uint32_t chunk, uint64_t offset, uint64_t size, const char *name)
{
    printf("field %u %s %" PRIu64 " %" PRIu64 "\n", chunk, name, offset, size);
}

// Every change of one byte of a chunk's body (four values per byte) loaded into the machine with its checksums made right again. Nothing may
// fault the image, a refused state may not change the machine, and what is accepted is put back by loading the good state again. The verdicts
// are hashed so that two runs, from different machines, can be compared: the verdict must not depend on the machine.
void mutate_chunk(const StateOp &op, uint64_t unit, p6_state::Options options)
{
    p6_error error{P6_ABI_VERSION, sizeof error, {}};
    uint32_t index = p6_state::kChunkCount;
    for (uint32_t i = 0; i < p6_state::kChunkCount; ++i)
        if (!strcmp(p6_state::kChunks[i].name, op.chunk)) index = i;
    std::vector<uint8_t> good;
    if (index == p6_state::kChunkCount || !read_file(op.path, &good)) { printf("mutate %" PRIu64 " error no chunk or file\n", unit); return; }
    uint64_t at = p6_state::kHeaderSize, body_at = 0, body_length = 0;
    for (uint32_t i = 0; i < p6_state::kChunkCount; ++i) {
        const uint64_t length = le64(&good[at + 8]);
        if (i == index) { body_at = at + p6_state::kChunkHeaderSize; body_length = length; }
        at += p6_state::kChunkHeaderSize + length;
    }
    const uint64_t state_unit = le64(&good[24]), state_ticks = le64(&good[32]);
    const p6_state::Identity identity = p6_state::current_identity();
    options.only_chunks = g_only;
    p6_result result = p6_state::load(good.data(), good.size(), options, &error);
    if (result != P6_OK) { printf("mutate %" PRIu64 " error the good state was refused: %s\n", unit, error.message); return; }
    uint8_t baseline[32];
    combined_digest(baseline);
    unsigned attempts = 0, accepted = 0, refused = 0, faulted = 0, changed = 0;
    p6_sha256 verdicts;
    p6_sha256_init(&verdicts);
    for (uint64_t k = 0; k < body_length && !faulted; ++k) {
        for (int variant = 0; variant < 4 && !faulted; ++variant) {
            std::vector<uint8_t> bad = good;
            const uint8_t was = bad[body_at + k];
            const uint8_t now = variant == 0 ? uint8_t(was ^ 0x01) : variant == 1 ? uint8_t(was ^ 0x80) : variant == 2 ? 0 : 0xFF;
            if (now == was) continue;
            bad[body_at + k] = now;
            p6_state::seal(bad.data(), bad.size() - p6_state::kHeaderSize, p6_state::kChunkCount, identity, state_unit, state_ticks);
            ++attempts;
            result = p6_state::load(bad.data(), bad.size(), options, &error);
            const uint8_t code = uint8_t(result);
            p6_sha256_update(&verdicts, &code, 1);
            if (result == P6_OK) {
                ++accepted;
                if (p6_state::load(good.data(), good.size(), options, &error) != P6_OK) { ++faulted; break; }
            } else if (result == P6_ERROR_INTERNAL) {
                ++faulted;
            } else {
                ++refused;
                uint8_t digest[32];
                combined_digest(digest);
                if (memcmp(digest, baseline, 32)) ++changed;
            }
        }
    }
    uint8_t summary[32];
    p6_sha256_final(&verdicts, summary);
    printf("mutate %" PRIu64 " %s bytes=%" PRIu64 " attempts=%u accepted=%u refused=%u faulted=%u changed=%u verdicts=%s\n", unit, op.chunk, body_length,
           attempts, accepted, refused, faulted, changed, hex(summary, 32).c_str());
}

void foreign_handler(uae_u32) {}        // not in the scheduler's table: a save must refuse a secondary event that has it

// The table of handler ids against the engine's own functions, named here independently of bridge/state_cpu.cpp: "handler ID OK|BAD".
void print_handler_table()
{
    struct Expected { uint8_t id; evfunc2 handler; };
    const Expected table[] = {{1, blitter_handler}, {2, event_doint_delay_do_ext}, {3, p6_custom_event2_handler(0)}, {4, p6_custom_event2_handler(1)},
                              {5, event_audxdat_func}, {6, event_setdsr}, {7, event_CIA_synced_interrupt}, {8, event_CIA_tod_inc_event},
                              {9, event_DISK_handler}, {10, p6_disk_motordelay_handler()}, {11, p6_custom_event2_handler(2)}, {12, p6_custom_event2_handler(3)}, {13, p6_custom_event2_handler(4)}};
    for (const Expected &e : table) {
        const bool ok = e.handler && p6_state::scheduler_handler(e.id) == e.handler && p6_state::scheduler_handler_id(e.handler) == e.id;
        printf("handler %u %s\n", unsigned(e.id), ok ? "OK" : "BAD");
    }
    printf("handler 0 %s\n", !p6_state::scheduler_handler(0) && p6_state::scheduler_handler_id(nullptr) == 0 ? "OK" : "BAD");
    printf("handler 14 %s\n", !p6_state::scheduler_handler(14) ? "OK" : "BAD");
    printf("handler foreign %s\n", p6_state::scheduler_handler_id(foreign_handler) == 0 ? "OK" : "BAD");
}

// A change of the packet that the probe sends, applied right before the unit it names (0 means before the first one).
struct InputEvent {
    uint64_t unit;
    unsigned kind;          // 0 key down, 1 key up, 2 port
    unsigned code;
    unsigned port;
    p6_port_input value;
};

// The input as a poke found it, for "clear".
int g_profile_rtc = 1;      // the clock chip of the machine after p6_load, which the poke "clear" puts back
p6_input_raw g_input_before;
bool g_input_poked = false;

void input_poke_begin()
{
    if (g_input_poked) return;
    p6_input_get(&g_input_before);
    g_input_poked = true;
}

// The CIAs as a poke found them, for "clear".
p6_cia_raw g_cia_before[2];
p6_cia_misc_raw g_misc_before;
bool g_cia_poked = false;

void cia_poke_begin()
{
    if (g_cia_poked) return;
    p6_cia_get(0, &g_cia_before[0]);
    p6_cia_get(1, &g_cia_before[1]);
    p6_cia_misc_get(&g_misc_before);
    g_cia_poked = true;
}

void run_state_ops(const std::vector<StateOp> &ops, uint64_t unit)
{
    for (const StateOp &op : ops) {
        if (op.unit != unit) continue;
        p6_error error{P6_ABI_VERSION, sizeof error, {}};
        p6_state::Options options;
        options.allow_placeholders = true;
        if (op.kind == OpKind::Reset) {
            const p6_result result = p6_reset(&error);
            printf("reset %" PRIu64 " %d %s\n", unit, int(result), error.message);
        } else if (op.kind == OpKind::Event) {
            // The public p6_media_event_enqueue with an explicit sequence; INDEX -1 ejects, otherwise it mounts a medium registered at p6_load.
            p6_media_event event{};
            event.abi_version = P6_ABI_VERSION;
            event.struct_size = sizeof event;
            event.type = P6_MEDIA_EVENT_MOUNT;
            event.drive = uint32_t(strtoul(op.chunk, nullptr, 10));
            event.media_index = uint32_t(strtoul(op.path, nullptr, 10));
            if (!strcmp(op.path, "-1")) {
                event.type = P6_MEDIA_EVENT_EJECT;
                event.media_index = P6_MEDIA_INDEX_EJECT;
            }
            event.sequence = strtoull(op.extra, nullptr, 10);
            const p6_result result = p6_media_event_enqueue(&event, &error);
            printf("event %" PRIu64 " %d %s\n", unit, int(result), error.message);
        } else if (op.kind == OpKind::Append) {
            // The public p6_media_append (T17e) of the image in the file; prints the index the manifest gave it.
            std::vector<uint8_t> image;
            if (!read_file(op.path, &image)) { printf("append %" PRIu64 " error cannot read the file\n", unit); continue; }
            g_appended.push_back(std::move(image));
            const std::vector<uint8_t> &kept = g_appended.back();
            p6_media medium = {};
            medium.type = P6_MEDIA_ADF;
            p6_sha256_hash(kept.data(), kept.size(), medium.content_id);
            medium.image = p6_buffer{kept.data(), kept.size()};
            uint32_t index = UINT32_MAX;
            const p6_result result = p6_media_append(&medium, &index, &error);
            printf("append %" PRIu64 " %d %u %s\n", unit, int(result), index, error.message);
        } else if (op.kind == OpKind::Export) {
            // The public p6_media_export (T27) of medium INDEX: the file it is written to and the SHA-256 of the 901120 bytes. "export UNIT CODE INDEX SHA256 WRITTEN MESSAGE".
            const uint32_t index = uint32_t(strtoul(op.chunk, nullptr, 10));
            std::vector<uint8_t> image(901120);
            uint64_t written = 0;
            const p6_result result = p6_media_export(index, image.data(), image.size(), &written, &error);
            uint8_t digest[32] = {};
            if (result == P6_OK) {
                p6_sha256_hash(image.data(), size_t(written), digest);
                if (FILE *f = fopen(op.path, "wb")) { fwrite(image.data(), 1, size_t(written), f); fclose(f); }
            }
            printf("export %" PRIu64 " %d %u %s %" PRIu64 " %s\n", unit, int(result), index, hex(digest, 32).c_str(), written, error.message);
        } else if (op.kind == OpKind::Status) {
            p6_media_write_status st = {};
            st.abi_version = P6_ABI_VERSION;
            st.struct_size = sizeof st;
            st.media_index = uint32_t(strtoul(op.chunk, nullptr, 10));
            const p6_result result = p6_media_write_status_get(&st, &error);
            // base= is the SHA-256 of what the blob store holds for the medium (the "rb" stream): the base must stay the registered image whatever the guest writes.
            std::string base = "none";
            if (st.media_index < g_media.size()) {
                char id[p6_bridge::kMediaBlobIdBytes];
                p6_bridge::media_blob_id(g_media[st.media_index].content_id, id);
                p6_blob_handle handle = 0;
                std::vector<uint8_t> bytes(size_t(g_media[st.media_index].image.bytes));
                size_t items = 0;
                if (p6_blob_open(id, "rb", &handle) == P6_BLOB_OK) {
                    uint8_t digest[32];
                    if (p6_blob_read(handle, bytes.data(), bytes.size(), 1, bytes.size(), &items) == P6_BLOB_OK && items == bytes.size()) {
                        p6_sha256_hash(bytes.data(), bytes.size(), digest);
                        base = hex(digest, 32);
                    }
                    p6_blob_close(handle);
                }
            }
            printf("status %" PRIu64 " %d %u capacity=%" PRIu64 " used=%" PRIu64 " medium=%" PRIu64 " refused=%" PRIu64 " raw=%" PRIu64 " base=%s %s\n", unit, int(result), st.media_index,
                   st.overlay_capacity, st.overlay_used, st.medium_sectors, st.refused_writes, st.raw_tracks, base.c_str(), error.message);
        } else if (op.kind == OpKind::Ram) {
            // An external write into Chip RAM at the boundary (a host action, never part of a replay): the file's bytes at ADDRESS. "ram UNIT CODE ADDRESS BYTES".
            std::vector<uint8_t> data;
            const unsigned long at = strtoul(op.chunk, nullptr, 0);
            p6_memory_region regions[4] = {};
            uint32_t count = 0;
            int code = -1;
            if (read_file(op.path, &data) && p6_memory_regions(regions, 4, &count, &error) == P6_OK)
                for (uint32_t i = 0; i < count; ++i)
                    if (regions[i].type == P6_REGION_CHIP_RAM && (regions[i].flags & P6_REGION_WRITABLE) && at <= regions[i].bytes && data.size() <= regions[i].bytes - at) {
                        memcpy(static_cast<uint8_t *>(const_cast<void *>(regions[i].data)) + at, data.data(), data.size());
                        code = 0;
                        break;
                    }
            printf("ram %" PRIu64 " %d %lu %zu\n", unit, code, at, data.size());
        } else if (op.kind == OpKind::RamOut) {
            // Chip RAM [ADDRESS, ADDRESS + LENGTH) into a file, what a host could read through p6_memory_regions. "ramout UNIT CODE ADDRESS LENGTH".
            const unsigned long at = strtoul(op.chunk, nullptr, 0), length = strtoul(op.extra, nullptr, 0);
            p6_memory_region regions[4] = {};
            uint32_t count = 0;
            int code = -1;
            if (p6_memory_regions(regions, 4, &count, &error) == P6_OK)
                for (uint32_t i = 0; i < count; ++i)
                    if (regions[i].type == P6_REGION_CHIP_RAM && at <= regions[i].bytes && length <= regions[i].bytes - at) {
                        if (FILE *f = fopen(op.path, "wb")) {
                            fwrite(static_cast<const uint8_t *>(regions[i].data) + at, 1, length, f);
                            fclose(f);
                            code = 0;
                        }
                        break;
                    }
            printf("ramout %" PRIu64 " %d %lu %lu\n", unit, code, at, length);
        } else if (op.kind == OpKind::Public) {
            // The public functions of p6.h, in one process: the bound, a save into a buffer of exactly that size, one into a buffer that is too small, a load of
            // garbage and the load of the state just saved. Only the last one changes the machine (to the one it was).
            p6_caps caps = {P6_ABI_VERSION, sizeof caps, 0, 0, 0, 0, 0};
            p6_query_caps(&caps, &error);
            uint64_t bytes = 0, written = 0, unused = 0;
            const p6_result b = p6_state_bound(&bytes, &error);
            char message[P6_ERROR_TEXT_CAPACITY];
            memcpy(message, error.message, sizeof message);
            std::vector<uint8_t> state(b == P6_OK ? bytes : 0);
            const p6_result s = b == P6_OK ? p6_state_save(state.data(), state.size(), &written, &error) : b;
            uint8_t scratch[16] = {};
            const p6_result small = p6_state_save(scratch, sizeof scratch, &unused, &error);
            const p6_result junk = p6_state_load(scratch, sizeof scratch, &error);
            const p6_result l = s == P6_OK ? p6_state_load(state.data(), written, &error) : s;
            printf("public %" PRIu64 " cap_state=%d bound=%d save=%d load=%d written=%" PRIu64 " junk=%d small=%d %s\n", unit, (caps.flags & P6_CAP_STATE) ? 1 : 0,
                   int(b), int(s), int(l), written, int(junk), int(small), message);
            uint8_t digest[32];
            p6_sha256_hash(state.data(), size_t(written), digest);
            printf("public-size %" PRIu64 " %" PRIu64 " %" PRIu64 " %s\n", unit, bytes, written, hex(digest, 32).c_str());
        } else if (op.kind == OpKind::HandlerTable) {
            print_handler_table();
        } else if (op.kind == OpKind::Rtc) {
            // The clock in the open, for a test that compares it with what a state said: "rtc UNIT STATUS SECONDS TICKS SUBSECOND CYCLE D E F".
            p6_rtc_state clock{};
            const int status = p6_rtc_save(&clock);
            uae_u8 msm[3];
            p6_rtc_msm_get(msm);
            printf("rtc %" PRIu64 " %d %" PRId64 " %" PRIu64 " %" PRIu64 " %" PRId64 " %u %u %u\n", unit, status, int64_t(clock.clock.seconds),
                   uint64_t(clock.clock.ticks_per_second), uint64_t(clock.clock.subsecond), int64_t(clock.cycle), unsigned(msm[0]), unsigned(msm[1]),
                   unsigned(msm[2]));
        } else if (op.kind == OpKind::Digest) {
            printf("after-load %" PRIu64 "\n", unit);
            print_digests(unit);
        } else if (op.kind == OpKind::Mutate) {
            mutate_chunk(op, unit, options);
        } else if (op.kind == OpKind::Poke) {
            // Puts the live CPU in a state that is not a unit boundary, to see that a save refuses it. Test only.
            if (!strcmp(op.chunk, "exception")) regs.exception = 3;
            else if (!strcmp(op.chunk, "spc-int")) regs.spcflags |= SPCFLAG_INT;
            else if (!strcmp(op.chunk, "lastipl")) regs.lastipl = 5;
            else if (!strncmp(op.chunk, "ev2:", 4)) {
                // A real secondary event, scheduled through the engine's own call (slot 0 is the blitter's): ev2:ID[:delay], ID of scheduler_handler and optional emulated-clock delay.
                int id = 0; unsigned delay = 0;
                sscanf(op.chunk + 4, "%d:%u", &id, &delay);
                const evfunc2 handler = p6_state::scheduler_handler(uint8_t(id));
                if (handler) event2_newevent_xx(id == 1 ? ev2_blitter : -1, evt_t(delay ? delay : 2000 + 100 * id) * CYCLE_UNIT, uae_u32(id == 13 ? 128 : 0x1000 + id), handler);
            } else if (!strncmp(op.chunk, "beam:", 5)) {
                // beam:VPOS:LINEAR:HPOS puts the beam counters where a state had them (diagnosis only: what DET02 shows once the beam is out of the way).
                int v = 0, l = 0, h = 0;
                if (sscanf(op.chunk + 5, "%d:%d:%d", &v, &l, &h) == 3) { vpos = v; linear_vpos = l; agnus_hpos = uae_u8(h); }
            } else if (!strcmp(op.chunk, "rtc-fault")) p6_rtc_read(nullptr, 0);       // latches the clock's sticky fault, as a failed read would
            else if (!strcmp(op.chunk, "cs-rtc")) currprefs.cs_rtc = 4;               // an unsupported clock chip
            else if (!strcmp(op.chunk, "rtc-ricoh")) {
                // Diagnostic-only chip selection after the reset head, until the real A4000 preset is implemented.
                // No public configuration or frontend offers this hybrid machine.
                currprefs.cs_rtc = 2;
                rtc_hardreset();
            }
            else if (!strcmp(op.chunk, "cia-tod2") || !strcmp(op.chunk, "cia-tod2-event")) {
                // A TOD increment pending in CIA-A (state 2), without the event that carries it or with it (the engine's own call, data = the CIA's number).
                cia_poke_begin();
                p6_cia_raw r{};
                p6_cia_get(0, &r);
                r.tod_event_state = 2;
                p6_cia_set(0, &r);
                if (!strcmp(op.chunk, "cia-tod2-event")) event2_newevent_xx(-1, evt_t(2000) * CYCLE_UNIT, 0, event_CIA_tod_inc_event);
            } else if (!strcmp(op.chunk, "cia-ovl")) {                                // the overlay latch against PRA/DDRA
                cia_poke_begin();
                p6_cia_misc_raw m{};
                p6_cia_misc_get(&m);
                m.oldovl ^= 1;
                p6_cia_misc_set(&m);
            } else if (!strcmp(op.chunk, "kbmode")) currprefs.keyboard_mode = 1;      // another keyboard than the A500's
            else if (!strcmp(op.chunk, "keyinject")) keybuf_inject("a");              // a text typed into the keyboard ring
            else if (!strcmp(op.chunk, "ignorerelease")) keybuf_ignore_next_release();
            else if (!strcmp(op.chunk, "turbo")) currprefs.turbo_emulation = 1;
            else if (!strcmp(op.chunk, "mousespeed")) currprefs.input_mouse_speed = 50;
            else if (!strcmp(op.chunk, "resetwarning")) currprefs.cs_resetwarning = true;
            else if (!strcmp(op.chunk, "released")) {                                // the engine cleared its key table and the bridge was not told
                input_poke_begin();
                p6_input_raw r{};
                p6_input_get(&r);
                memset(r.keys, 0, sizeof r.keys);
                p6_input_set(&r);
                p6_input_engine_released_keys = 1;
            }
            else if (!strcmp(op.chunk, "engine-key") || !strcmp(op.chunk, "engine-button") || !strcmp(op.chunk, "engine-axis") || !strcmp(op.chunk, "engine-mouse")) {
                // The engine's edge memory against the bridge's: one bit of the key table, a button of joystick 1, a direction of joystick 1 or a mouse button differs.
                input_poke_begin();
                p6_input_raw r{};
                p6_input_get(&r);
                if (!strcmp(op.chunk, "engine-key")) r.keys[1] ^= 1;
                else if (!strcmp(op.chunk, "engine-button")) r.joy_buttonmask[1] ^= 1;
                else if (!strcmp(op.chunk, "engine-axis")) r.joy_states[1][0][8] = r.joy_states[1][0][8] == 1 ? 0 : 1;
                else r.mouse_buttonmask ^= 1;
                p6_input_set(&r);
            }
            else if (!strncmp(op.chunk, "ce020-", 6)) {
                // T24c: change one field of the cycle-exact 68020 that the CPU chunk carries, to show that it decides the future (a field that no program can notice would not need to travel).
                // ce020-FIELD[:N]: the counter of extra cycles moves by N (9) modulo 16, the remainder below a colour clock by N units (half a colour clock) modulo a colour clock, the end
                // of the last prefetch to N units before the present cycle (0), the span of the last memory cycle becomes N units (a colour clock) ending now, the count of
                // instructions moves by N (1000), VBR by N (0x100), CACR flips its enable bit and a line of the instruction cache is invalidated and another one changed.
                char what[32];
                snprintf(what, sizeof what, "%s", op.chunk + 6);
                long n = -1;
                if (char *colon = strchr(what, ':')) {
                    *colon = 0;
                    n = strtol(colon + 1, nullptr, 0);
                }
                if (!strcmp(what, "extracycles")) regs.ce020extracycles = (regs.ce020extracycles + (n < 0 ? 9 : n)) % 16;
                else if (!strcmp(what, "extra")) p6_custom_ce020_extra_set((p6_custom_ce020_extra_get() + (n < 0 ? CYCLE_UNIT / 2 : n)) % CYCLE_UNIT);
                else if (!strcmp(what, "prefetchend")) regs.ce020prefetchendcycle = get_cycles() - (n < 0 ? 0 : n);
                else if (!strcmp(what, "span")) { regs.ce020endcycle = get_cycles(); regs.ce020startcycle = regs.ce020endcycle - (n < 0 ? CYCLE_UNIT : n); }
                else if (!strcmp(what, "instruction-cnt")) regs.instruction_cnt += n < 0 ? 1000 : int(n);
                else if (!strcmp(what, "vbr")) regs.vbr += n < 0 ? 0x100 : uae_u32(n);
                else if (!strcmp(what, "cacr")) regs.cacr ^= 1;
                else if (!strcmp(what, "cache-line")) { uae_u32 d[CACHELINES020], t[CACHELINES020]; uae_u8 v[CACHELINES020]; p6_caches020_get(d, t, v, CACHELINES020); v[0] ^= 1; d[1] ^= 0xff; p6_caches020_set(d, t, v, CACHELINES020); }
                else { fprintf(stderr, "unknown poke %s\n", op.chunk); exit(64); }
            }
            else if (!strncmp(op.chunk, "cpu010-", 7)) {
                // T52: change one field of the 68010 block of the CPU chunk (tests/state/test_state_cpu010.py): VBR moves by 0x100, SFC and DFC by one (modulo 8), and the loop mode
                // is entered with the flag and the cycles of a one-instruction loop (or left, when it was).
                const char *what = op.chunk + 7;
                if (!strcmp(what, "vbr")) regs.vbr += 0x100;
                else if (!strcmp(what, "sfc")) regs.sfc = (regs.sfc + 1) & 7;
                else if (!strcmp(what, "dfc")) regs.dfc = (regs.dfc + 1) & 7;
                else if (!strcmp(what, "loop")) regs.loop_mode = regs.loop_mode ? 0 : 0x20001;
                else { fprintf(stderr, "unknown poke %s\n", op.chunk); exit(64); }
            }
            else if (!strncmp(op.chunk, "fpu-", 4)) {
                // T54: change one field of the FPU that the CPU chunk carries, to show that it decides the future (tests/state/test_state_fpu.py). fpu-reg moves the mantissa of FP0 by
                // one unit in the last place, fpu-fpcr flips the rounding mode, fpu-fpsr flips the N condition code, fpu-fpiar adds 2, fpu-state flips the idle/null bit and fpu-fsave
                // changes the exceptional operand that an FSAVE writes, and exp-pend posts an exception for the next FPU instruction (or takes back the one that was posted).
                const char *what = op.chunk + 4;
                if (!strcmp(what, "reg")) regs.fp[0].fpx.low += 1;
                else if (!strcmp(what, "fpcr")) { uae_u32 v = regs.fpcr ^ 0x10; fpp_set_fpcr(v); }
                else if (!strcmp(what, "fpsr")) regs.fpsr ^= 0x08000000;
                else if (!strcmp(what, "fpiar")) regs.fpiar += 2;
                else if (!strcmp(what, "state")) regs.fpu_state ^= 1;
                else if (!strcmp(what, "exp-pend")) regs.fp_exp_pend = regs.fp_exp_pend ? 0 : 50;
                else if (!strcmp(what, "fsave")) { uae_u32 ccr, eo[3]; p6_fpu_fsave_get(&ccr, eo); eo[0] ^= 0x7fff0000; ccr ^= 0x1; p6_fpu_fsave_set(ccr, eo); }
                else { fprintf(stderr, "unknown poke %s\n", op.chunk); exit(64); }
            }
            else if (!strcmp(op.chunk, "ev2-foreign")) event2_newevent_xx(-1, evt_t(3000) * CYCLE_UNIT, 0, foreign_handler);
            else if (!strcmp(op.chunk, "ev2-stale")) {
                // What a fired event leaves in its slot: inactive, with the time, the data and the handler it had. A save writes zeros for it.
                eventtab2[7].active = 0;
                eventtab2[7].evtime = 99999;
                eventtab2[7].data = 0xBEEF;
                eventtab2[7].handler = foreign_handler;
            } else if (!strcmp(op.chunk, "start-cycles")) start_cycles = 100;
            else if (!strcmp(op.chunk, "pissoff")) pissoff = 1;
            else if (!strcmp(op.chunk, "bltnasty")) regs.spcflags |= SPCFLAG_BLTNASTY;      // flags a state may carry: they are valid, only not set now
            else if (!strcmp(op.chunk, "trace")) regs.spcflags |= SPCFLAG_TRACE;
            else if (!strcmp(op.chunk, "dotrace")) regs.spcflags |= SPCFLAG_DOTRACE;
            else if (!strcmp(op.chunk, "clear")) {
                regs.exception = 0;
                regs.spcflags &= ~(SPCFLAG_INT | SPCFLAG_BLTNASTY | SPCFLAG_TRACE | SPCFLAG_DOTRACE);
                regs.lastipl = 0;
                start_cycles = 0;
                pissoff = 0;
                currprefs.cs_rtc = g_profile_rtc;
                currprefs.keyboard_mode = 0;
                currprefs.turbo_emulation = 0;
                currprefs.input_mouse_speed = 100;
                currprefs.cs_resetwarning = false;
                p6_input_engine_released_keys = 0;
                if (g_input_poked) {
                    p6_input_set(&g_input_before);
                    g_input_poked = false;
                }
                if (g_cia_poked) {                                                      // the CIAs as they were before the first poke (an event it scheduled stays)
                    p6_cia_set(0, &g_cia_before[0]);
                    p6_cia_set(1, &g_cia_before[1]);
                    p6_cia_misc_set(&g_misc_before);
                    g_cia_poked = false;
                }
            }
            printf("poke %" PRIu64 " %s\n", unit, op.chunk);
        } else if (op.kind == OpKind::Save || op.kind == OpKind::SaveFields || op.kind == OpKind::PublicSave) {
            // PublicSave goes through p6_state_bound and p6_state_save as a host does; the others through the registry with the test options.
            const bool pub = op.kind == OpKind::PublicSave;
            if (op.kind == OpKind::SaveFields) options.on_field = print_field;
            uint64_t bound = 0, written = 0;
            p6_result result = pub ? p6_state_bound(&bound, &error) : p6_state::bound(&bound, options, &error);
            std::vector<uint8_t> state(result == P6_OK ? bound : 0);
            if (result == P6_OK) result = pub ? p6_state_save(state.data(), state.size(), &written, &error) : p6_state::save(state.data(), state.size(), &written, options, &error);
            if (result != P6_OK) { printf("save %" PRIu64 " error %d %s\n", unit, int(result), error.message); continue; }
            uint8_t digest[32];
            p6_sha256_hash(state.data(), written, digest);
            FILE *f = fopen(op.path, "wb");
            if (!f || fwrite(state.data(), 1, size_t(written), f) != written) { perror(op.path); if (f) fclose(f); continue; }
            fclose(f);
            printf("save %" PRIu64 " %" PRIu64 " %s bound=%" PRIu64 "\n", unit, written, hex(digest, 32).c_str(), bound);
        } else if (op.kind == OpKind::LoadFault || op.kind == OpKind::LoadFaultHead) {
            // A load whose apply pass fails after validation: the image is Faulted, a frame is refused, p6_unload recovers it and the
            // image loaded again is the one of a clean process.
            std::vector<uint8_t> state;
            if (!read_file(op.path, &state)) continue;
            options.fail_apply = op.kind == OpKind::LoadFault;
            options.fail_head = op.kind == OpKind::LoadFaultHead;
            options.only_chunks = g_only;
            p6_result result = p6_state::load(state.data(), state.size(), options, &error);
            printf("load %" PRIu64 " %d %s\n", unit, int(result), error.message);
            p6_input input = {};
            input.abi_version = P6_ABI_VERSION;
            input.struct_size = sizeof input;
            p6_frame frame = {};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            result = p6_run_frame(&input, 0, &frame, &error);
            printf("run-after-fault %d %s\n", int(result), error.message);
            result = p6_unload(&error);
            printf("unload %d\n", int(result));
            result = p6_load(p6_buffer{g_rom.data(), g_rom.size()}, g_media.empty() ? nullptr : g_media.data(), uint32_t(g_media.size()), &error);
            printf("reload %d %s\n", int(result), error.message);
            printf("after-load %" PRIu64 "\n", unit);
            print_digests(unit);
        } else {
            std::vector<uint8_t> state;
            if (!read_file(op.path, &state)) continue;
            options.only_chunks = g_only;
            const p6_result result = op.kind == OpKind::PublicLoad ? p6_state_load(state.data(), state.size(), &error)
                                                                   : p6_state::load(state.data(), state.size(), options, &error);
            printf("load %" PRIu64 " %d %s\n", unit, int(result), error.message);
            printf("after-load %" PRIu64 "\n", unit);
            print_digests(unit);
        }
    }
}

bool read_file(const char *path, std::vector<uint8_t> *data)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return false; }
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data->insert(data->end(), buffer, buffer + n);
    fclose(f);
    return true;
}

int fail(const char *what, p6_result result, const p6_error &error)
{
    fprintf(stderr, "%s failed (%d): %s\n", what, int(result), error.message);
    return 1;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--list")) {
        for (const Probe &probe : kProbes) printf("%s\n", probe.subsystem);
        return 0;
    }
    const char *rom_path = nullptr;
    uint64_t units = 3, every = 1, loads = 1;
    uint32_t standard = P6_VIDEO_PAL;
    uint32_t machine = P6_MACHINE_A500;
    uint32_t rate = 48000;
    uint32_t cpu_multiplier = 0;                             // T52: p6_config cpu_multiplier (--cpu-multiplier 1|2|4|8|10|12|16; 0 the machine's own)
    uint32_t cpu_compatibility = P6_CPU_EXACT;
    uint32_t cpu_model = 0;                                  // T52: p6_config cpu_model as the ABI number (--cpu 68000|68010|68020; 0 the machine's own)
    uint32_t fpu_model = 0;                                  // T54: 0 = none, 68881 or 68882 (--fpu 68882)
    uint32_t hdf_controller = 0;                             // T56: p6_config hdf_controller as the ABI number (--hdf-controller uae|ide: 0 the UAE controller, 1 the IDE port of Gayle)
    uint32_t floppy_drives = 0;
    uint32_t drive_sound = 0;
    uint32_t floppy_speed = 0;                               // T62: p6_config floppy_speed as the ABI number (--floppy-speed: 0 the profile, 1 100 %, 2 200 %, 3 400 %, 4 800 %, 5 turbo)
    uint32_t sound[4] = {0, 0, 0, 0};                       // T73: p6_config sound_separation, sound_interpolation, sound_filter, sound_filter_type as ABI numbers (--sound-sep, --sound-interp, --sound-filter, --sound-type)
    uint32_t chip_kib = 0, slow_kib = 0, fast_kib = 0;      // T49: 0 is the machine's own memory (slow: "none" is P6_MEMORY_NONE)
    uint64_t seed = 1;
    bool floppy_writes = false;
    std::vector<StateOp> state_ops;
    std::vector<const char *> adf_paths;
    std::vector<const char *> hdf_paths;
    p6_port_input ports[2] = {{P6_PORT_JOYSTICK, 0, 0, 0, 0}, {P6_PORT_JOYSTICK, 0, 0, 0, 0}};
    std::vector<InputEvent> input_events;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--units") && i + 1 < argc) units = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--every") && i + 1 < argc) every = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--loads") && i + 1 < argc) loads = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--ntsc")) standard = P6_VIDEO_NTSC;
        else if (!strcmp(argv[i], "--drive-sound") && i + 1 < argc) {
            drive_sound = uint32_t(strtoul(argv[++i], nullptr, 10));
            if (drive_sound > 100) return 64;
        }
        else if (!strcmp(argv[i], "--machine") && i + 1 < argc) {
            const char *name = argv[++i];
            if (!strcmp(name, "a500")) machine = P6_MACHINE_A500;
            else if (!strcmp(name, "a500og")) machine = P6_MACHINE_A500OG;
            else if (!strcmp(name, "a500plus")) machine = P6_MACHINE_A500PLUS;
            else if (!strcmp(name, "a600")) machine = P6_MACHINE_A600;
            else if (!strcmp(name, "a1000")) machine = P6_MACHINE_A1000;
            else if (!strcmp(name, "a1200")) machine = P6_MACHINE_A1200;
            else if (!strcmp(name, "a2000og")) machine = P6_MACHINE_A2000OG;
            else if (!strcmp(name, "a2000")) machine = P6_MACHINE_A2000;
            else if (!strcmp(name, "a4030")) machine = P6_MACHINE_A4030;
            else return 64;
        }
        else if (!strcmp(argv[i], "--rate") && i + 1 < argc) rate = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--chip") && i + 1 < argc) chip_kib = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--slow") && i + 1 < argc) { const char *v = argv[++i]; slow_kib = !strcmp(v, "none") ? P6_MEMORY_NONE : uint32_t(strtoul(v, nullptr, 10)); }
        else if (!strcmp(argv[i], "--fast") && i + 1 < argc) fast_kib = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--cpu") && i + 1 < argc) { const uint32_t model = uint32_t(strtoul(argv[++i], nullptr, 10)); cpu_model = model >= 68000 ? 1 + (model - 68000) / 10 : model; }
        else if (!strcmp(argv[i], "--cpu-compatibility") && i + 1 < argc) {
            const char *v = argv[++i];
            if (!strcmp(v, "exact")) cpu_compatibility = P6_CPU_EXACT;
            else if (!strcmp(v, "compatible")) cpu_compatibility = P6_CPU_COMPATIBLE;
            else if (!strcmp(v, "normal")) cpu_compatibility = P6_CPU_NORMAL;
            else return 64;
        }
        else if (!strcmp(argv[i], "--cpu-multiplier") && i + 1 < argc) cpu_multiplier = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--fpu") && i + 1 < argc) fpu_model = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--hdf-controller") && i + 1 < argc) { const char *v = argv[++i]; hdf_controller = !strcmp(v, "ide") ? P6_HDF_CONTROLLER_IDE : P6_HDF_CONTROLLER_UAE; }
        else if (!strcmp(argv[i], "--sound-sep") && i + 1 < argc) sound[0] = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--sound-interp") && i + 1 < argc) sound[1] = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--sound-filter") && i + 1 < argc) sound[2] = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--sound-type") && i + 1 < argc) sound[3] = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--floppy-drives") && i + 1 < argc) floppy_drives = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--floppy-speed") && i + 1 < argc) floppy_speed = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--dump") && i + 1 < argc) g_dump = argv[++i];
        else if ((!strcmp(argv[i], "--save-at") || !strcmp(argv[i], "--save-fields-at") || !strcmp(argv[i], "--load-at") ||
                  !strcmp(argv[i], "--load-fault-at") || !strcmp(argv[i], "--load-fault-head-at") || !strcmp(argv[i], "--public-save-at") ||
                  !strcmp(argv[i], "--public-load-at")) && i + 2 < argc) {
            const OpKind kind = !strcmp(argv[i], "--save-at") ? OpKind::Save : !strcmp(argv[i], "--save-fields-at") ? OpKind::SaveFields
                              : !strcmp(argv[i], "--load-at") ? OpKind::Load : !strcmp(argv[i], "--load-fault-at") ? OpKind::LoadFault
                              : !strcmp(argv[i], "--public-save-at") ? OpKind::PublicSave : !strcmp(argv[i], "--public-load-at") ? OpKind::PublicLoad
                              : OpKind::LoadFaultHead;
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), kind, argv[i + 2], nullptr});
            i += 2;
        } else if (!strcmp(argv[i], "--mutate-at") && i + 3 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Mutate, argv[i + 3], argv[i + 2]});
            i += 3;
        } else if (!strcmp(argv[i], "--rtc-at") && i + 1 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[++i], nullptr, 10), OpKind::Rtc, nullptr, nullptr});
        } else if (!strcmp(argv[i], "--reset-at") && i + 1 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[++i], nullptr, 10), OpKind::Reset, nullptr, nullptr});
        } else if (!strcmp(argv[i], "--event-at") && i + 4 < argc) {
            // --event-at UNIT DRIVE INDEX SEQUENCE (T15h): the API as a host calls it, with its own sequence number
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Event, argv[i + 3], argv[i + 2], argv[i + 4]});
            i += 4;
        } else if (!strcmp(argv[i], "--append-at") && i + 2 < argc) {
            // --append-at UNIT FILE (T17e): registers the image after UNIT, at the end of the manifest (prints "append UNIT CODE INDEX MESSAGE")
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Append, argv[i + 2], nullptr});
            i += 2;
        } else if (!strcmp(argv[i], "--export-at") && i + 3 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Export, argv[i + 3], argv[i + 2]});
            i += 3;
        } else if (!strcmp(argv[i], "--status-at") && i + 2 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Status, nullptr, argv[i + 2]});
            i += 2;
        } else if (!strcmp(argv[i], "--ram-at") && i + 3 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Ram, argv[i + 3], argv[i + 2]});
            i += 3;
        } else if (!strcmp(argv[i], "--ramout-at") && i + 4 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::RamOut, argv[i + 4], argv[i + 2], argv[i + 3]});
            i += 4;
        } else if (!strcmp(argv[i], "--writable")) {
            floppy_writes = true;
        } else if (!strcmp(argv[i], "--digest-at") && i + 1 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[++i], nullptr, 10), OpKind::Digest, nullptr, nullptr});
        } else if (!strcmp(argv[i], "--handler-table")) {
            state_ops.push_back(StateOp{0, OpKind::HandlerTable, nullptr, nullptr});
        } else if (!strcmp(argv[i], "--poke-at") && i + 2 < argc) {
            state_ops.push_back(StateOp{strtoull(argv[i + 1], nullptr, 10), OpKind::Poke, nullptr, argv[i + 2]});
            i += 2;
        } else if (!strcmp(argv[i], "--only") && i + 1 < argc) {
            const char *name = argv[++i];
            uint32_t found = p6_state::kChunkCount;
            for (uint32_t k = 0; k < p6_state::kChunkCount; ++k)
                if (!strcmp(p6_state::kChunks[k].name, name)) found = k;
            if (found == p6_state::kChunkCount) { fprintf(stderr, "no chunk named %s\n", name); return 64; }
            g_only |= 1u << found;
        } else if (!strcmp(argv[i], "--public-at") && i + 1 < argc) state_ops.push_back(StateOp{strtoull(argv[++i], nullptr, 10), OpKind::Public, nullptr, nullptr});
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--adf") && i + 1 < argc) adf_paths.push_back(argv[++i]);
        else if (!strcmp(argv[i], "--hdf") && i + 1 < argc) hdf_paths.push_back(argv[++i]);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) {
            unsigned port, type, buttons;
            int dx, dy;
            if (sscanf(argv[++i], "%u:%u:%u:%d:%d", &port, &type, &buttons, &dx, &dy) != 5 || port > 1) return 64;
            ports[port] = {type, buttons, int16_t(dx), int16_t(dy), 0};
        } else if (!strcmp(argv[i], "--key") && i + 3 < argc) {
            // --key UNIT CODE down|up: from UNIT on the packet holds (or no longer holds) the Amiga key CODE (0..0x67)
            InputEvent e{strtoull(argv[i + 1], nullptr, 10), !strcmp(argv[i + 3], "up") ? 1u : 0u, unsigned(strtoul(argv[i + 2], nullptr, 0)), 0, {}};
            if (e.code > 0x7f || (strcmp(argv[i + 3], "up") && strcmp(argv[i + 3], "down"))) return 64;
            input_events.push_back(e);
            i += 3;
        } else if (!strcmp(argv[i], "--port-at") && i + 2 < argc) {
            // --port-at UNIT N:type:buttons:dx:dy: from UNIT on the packet carries this for port N (the motion is repeated every unit)
            unsigned port, type, buttons;
            int dx, dy;
            if (sscanf(argv[i + 2], "%u:%u:%u:%d:%d", &port, &type, &buttons, &dx, &dy) != 5 || port > 1) return 64;
            input_events.push_back(InputEvent{strtoull(argv[i + 1], nullptr, 10), 2u, 0, port, {type, buttons, int16_t(dx), int16_t(dy), 0}});
            i += 2;
        } else if (!rom_path && argv[i][0] != '-') rom_path = argv[i];
        else { fprintf(stderr, "usage: %s ROM [--units N] [--every K] [--loads L] [--ntsc] [--machine a500|a500og|a500plus|a600|a1000|a1200|a2000og|a2000] [--chip KIB] [--slow KIB|none] [--fast KIB] [--cpu 68000|68010] [--cpu-multiplier N] [--cpu-compatibility exact|compatible|normal] [--fpu 68881|68882] [--hdf-controller uae|ide] [--rate HZ] [--port N:type:buttons:dx:dy]... [--port-at UNIT N:type:buttons:dx:dy]... [--key UNIT CODE down|up]... [--seed S] [--adf FILE]... [--hdf FILE]... [--event-at UNIT DRIVE INDEX SEQUENCE]... [--reset-at UNIT]... [--append-at UNIT FILE]... [--only CHUNK]... [--save-at UNIT FILE]... [--save-fields-at UNIT FILE]... [--load-at UNIT FILE]... [--load-fault-at UNIT FILE]... [--mutate-at UNIT CHUNK FILE]... [--poke-at UNIT exception|spc-int|lastipl|bltnasty|trace|dotrace|ev2:ID|ev2-foreign|ev2-stale|start-cycles|pissoff|rtc-fault|cs-rtc|cia-tod2|cia-tod2-event|cia-ovl|kbmode|keyinject|ignorerelease|turbo|mousespeed|resetwarning|released|engine-key|engine-button|engine-axis|engine-mouse|clear]... [--handler-table] [--digest-at UNIT]... [--rtc-at UNIT]... [--public-at UNIT]... [--public-save-at UNIT FILE]... [--public-load-at UNIT FILE]... | --list\n", argv[0]); return 64; }
    }
    if (!rom_path || !every || !loads) return 64;
    std::vector<uint8_t> &rom = g_rom;
    if (!read_file(rom_path, &rom)) return 1;
    std::vector<std::vector<uint8_t>> adfs(adf_paths.size());
    std::vector<p6_media> &media = g_media;
    for (size_t k = 0; k < adf_paths.size(); ++k) {
        if (!read_file(adf_paths[k], &adfs[k])) return 1;
        p6_media m = {};
        m.type = P6_MEDIA_ADF;
        p6_sha256_hash(adfs[k].data(), adfs[k].size(), m.content_id);
        m.image = p6_buffer{adfs[k].data(), adfs[k].size()};
        media.push_back(m);
    }

    std::vector<std::vector<uint8_t>> hdfs(hdf_paths.size());
    for (size_t k = 0; k < hdf_paths.size(); ++k) {
        if (!read_file(hdf_paths[k], &hdfs[k])) return 1;
        p6_media m = {};
        m.type = P6_MEDIA_HDF;
        p6_sha256_hash(hdfs[k].data(), hdfs[k].size(), m.content_id);
        m.image = p6_buffer{hdfs[k].data(), hdfs[k].size()};
        media.push_back(m);
    }

    p6_config config = {};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = machine;
    config.video_standard = standard;
    config.audio_rate_hz = rate;
    config.random_seed = seed;
    config.rtc_epoch_seconds = 946684800;
    config.flags = floppy_writes ? P6_CONFIG_FLOPPY_WRITES : 0;
    config.chip_kib = chip_kib;
    config.slow_kib = slow_kib;
    config.fast_kib = fast_kib;
    config.fpu_model = fpu_model;
    config.cpu_model = uint8_t(cpu_model);
    config.cpu_compatibility = uint8_t(cpu_compatibility);
    config.cpu_multiplier = uint8_t(cpu_multiplier);
    config.hdf_controller = uint8_t(hdf_controller);
    config.sound_separation = uint8_t(sound[0]); config.sound_interpolation = uint8_t(sound[1]); config.sound_filter = uint8_t(sound[2]); config.sound_filter_type = uint8_t(sound[3]);
    config.floppy_speed = uint8_t(floppy_speed);
    config.floppy_drives = uint8_t(floppy_drives);
    p6_error error{P6_ABI_VERSION, sizeof error, {}};
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) return fail("p6_init", result, error);
    for (uint64_t pass = 1; pass <= loads; ++pass) {
        if (loads > 1) printf("pass %" PRIu64 "\n", pass);
        result = p6_load(p6_buffer{rom.data(), rom.size()}, media.empty() ? nullptr : media.data(), uint32_t(media.size()), &error);
        if (result != P6_OK) return fail("p6_load", result, error);
        g_profile_rtc = currprefs.cs_rtc;
        p6_drive_sound::gain(drive_sound);
        print_digests(0);
        run_state_ops(state_ops, 0);
        p6_input input = {};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        input.ports[0] = ports[0];
        input.ports[1] = ports[1];
        for (uint64_t unit = 1; unit <= units; ++unit) {
            for (const InputEvent &e : input_events) {
                if (e.unit != unit && !(e.unit == 0 && unit == 1)) continue;
                if (e.kind == 2) input.ports[e.port] = e.value;
                else if (e.kind == 0) input.keys[e.code / 32] |= 1u << (e.code % 32);
                else input.keys[e.code / 32] &= ~(1u << (e.code % 32));
            }
            p6_frame frame = {};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
            if (result != P6_OK) return fail("p6_run_frame", result, error);
            // The PCM that the unit produced, hashed as it was handed over (T15i3): Paula's filter and resampler are in no digest above, and a lost phase shows only here.
            if (frame.audio && frame.audio_frames) {
                uint8_t pcm[32];
                p6_sha256_hash(frame.audio, size_t(frame.audio_frames) * 2 * sizeof(int16_t), pcm);
                printf("pcm %" PRIu64 " %s %" PRIu64 "\n", unit, hex(pcm, 32).c_str(), frame.audio_frames);
            }
            // The picture that unit handed over, which the "video" digest cannot tell from the one before it (it hashes the last one captured): none when the unit had no
            // new picture, else the size and the SHA-256 of the pixels of the active raster, row by row (T15j).
            if (frame.flags & P6_FRAME_NO_VIDEO) {
                printf("picture %" PRIu64 " none\n", unit);
            } else if (frame.video) {
                std::vector<uint8_t> raster;
                for (uint32_t y = 0; y < frame.active.height; ++y) {
                    const uint8_t *row = static_cast<const uint8_t *>(frame.video) + size_t(y) * frame.video_stride_bytes;
                    raster.insert(raster.end(), row, row + size_t(frame.active.width) * 4);
                }
                uint8_t picture[32];
                p6_sha256_hash(raster.data(), raster.size(), picture);
                printf("picture %" PRIu64 " %ux%u p%d %u/%u %s\n", unit, frame.active.width, frame.active.height, int(frame.field_parity), frame.cadence_num, frame.cadence_den,
                       hex(picture, 32).c_str());
            }
            if (unit % every == 0 || unit == units) print_digests(unit);
            run_state_ops(state_ops, unit);
        }
        result = p6_unload(&error);
        if (result != P6_OK) return fail("p6_unload", result, error);
    }
    p6_shutdown(nullptr);
    return 0;
}
