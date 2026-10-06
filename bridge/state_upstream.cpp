// Chunks P6DK (disk), P6AU (audio), P6CH (chipset) and P6VD (video) with the bodies WinUAE's own save_* and restore_* functions write and read
// (docs/adr/0009-state-format.md, point 8). Each body travels as a sub-body: its tag of 4 characters (the tag upstream's container gives it), a u32
// length and the bytes the engine wrote, with no ASF container and no zfile. The engine is compiled with P6_STATE_BODIES (patches 0086-0091), SAVESTATE
// stays undefined, and what upstream also saves but another chunk owns (CYCS, CHPD, CHPX, CIAA/B, KEYB, CINP, the CPU, the RAMs) is not here.
//
//   P6DK  DSK0..DSK3 (24 bytes each), DSD0..DSD3 (absent: length 0; present: the engine's track buffer), DISK (17)
//   P6CH  CHIP (360), BLTX (226), BLIT (63), CHSL (191), SPR0..SPR7 (25 each), BEAM (244: ours, not upstream's)
//   P6AU  AUD0..AUD3 (25 each)
//   P6VD  BPLX (292)
//
// BEAM is the one body that is not WinUAE's: its restore_custom reads VPOSR and VHPOSR and throws them away, and the counters of the frame, the line history and the
// sync latches of Agnus live in file statics that no body names. It is 61 words of 32 bits, big endian like the others, copied by p6_custom_beam_get and
// p6_custom_beam_set (patch 0094; the list is in custom.cpp): the beam and the line history, the frame phase (display_hsync_counter, display_vsync_counter,
// maxvpos_display_vsync_next, the field detection), the strobe that raises the vertical blank, the DMA list of the line in flight and the sync latches. The line length is the
// only value derived (setmaxhpos), from the lol that CHIP restores and the linetoggle it carries, which is why BEAM is the last body of the chunk and no init_hz or reset head runs.
//
// Save writes every body with the engine's save_* and changes nothing: STATE_DOSAVE keeps getjoystate from reading the host's input (maybe_read_input),
// the poll mask that POT0DAT and POT1DAT set is put back, and patch 0088 keeps DMACONR from writing dmacon.
//
// Validate reads each sub-body whole (a byte at a time through the archive, so that Validate and Apply see the same bytes) and refuses, before anything
// is applied: a tag out of its place, a length other than the one this build's save_* gives, and the values that end up indexing (a cylinder, a track length,
// a head position, the state of a channel, the channel count of a blit, the register of a slot...). It does not harden the engine's arithmetic against
// magnitudes this build never produces (ADR point 8). What belongs to the configuration is not adapted: the drive id and the disabled bit of a DSKn, the
// chipset mask and the video standard of CHIP are compared with the configuration of the image and a body that names others is refused; patches 0090 and
// 0091 remove the writes of restore_custom and restore_disk into the preferences, and the medium that sits in a drive is compared with the engine's once the
// machine exists (disk_matches_machine, called by load() after the reset head).
//
// Apply runs the engine's restore_* on the live machine in upstream's file order, with savestate_state = STATE_RESTORE for exactly that long. It never calls
// custom_reset, m68k_reset2, memory_clear, restore_*_start, init_hz or audio_activate. The *_finish that put back what is not saved are restore_audio_finish
// (last_cycles, which is not in the body, is the cycle counter P6SC has just restored) and restore_blitter_finish (the pending modulo loads); not called:
// restore_custom_finish (it would turn Genlock on in the preferences for a body with ERSY set) and restore_disk_finish (it works from the preferences and
// the GUI: DISK_check_change would act on a medium change the state does not carry). The scheduler belongs to P6SC: whatever a restore_* or a *_finish
// writes into eventtab, eventtab2 and their indices (schedule_audio, events_schedule, event2_newevent_xx from a register setter) is put back when the body is
// done.
#include "state.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "inputdevice.h"
#include "savestate.h"

extern void p6_events_get(int *idx, int *next, int *cnt, int *restart, int *remain);       // events.cpp 0078
extern void p6_events_set(int idx, int next, int remain);
extern bool p6_custom_beam_get(int32_t *out, unsigned n);       // custom.cpp 0094
extern bool p6_custom_beam_set(const int32_t *in, unsigned n);

namespace p6_state {

namespace {

// MAXMFMBUF of disk.cpp: words of the track buffer (DDHDMULT is 1: FLOPPY_DRIVE_HD is not in the profile). A DSDn body is 22 bytes plus a word, or a
// word and its timing, for every 16 bits of the track.
constexpr uint32_t kMfmWordsMax = 0x4000;
constexpr uint32_t kMfmBitsMax = kMfmWordsMax * 16;
constexpr uint32_t kDsdFixed = 4 + 2 + 4 + 2 + 4 + 4 + 2;
constexpr uint32_t kDsdMax = kDsdFixed + kMfmWordsMax * 4;
constexpr size_t kWork = kDsdMax + 64;
constexpr unsigned kCylinderMax = 80 + 3;       // hard_num_cyls of a 3.5" drive and the three steps drive_step allows beyond it
constexpr uint32_t kDriveId35DD = 0xFFFFFFFFu;  // DRIVE_ID_35DD of disk.cpp; DRIVE_ID_NONE is 0

// A body as validation reads it: the engine's own encoding, big endian.
struct Body {
    const uint8_t *p;
    uint32_t n;
    unsigned u8(uint32_t at) const { return p[at]; }
    unsigned u16(uint32_t at) const { return unsigned(p[at]) << 8 | p[at + 1]; }
    uint32_t u32(uint32_t at) const { return uint32_t(u16(at)) << 16 | u16(at + 2); }
};

// What a chunk's later bodies need to know of its earlier ones.
struct Seen {
    uint8_t drive_state[4];
    uint32_t head_position[4];
};

struct Sub {
    const char *tag;
    uint32_t fixed;                 // the length save_* gives in this build (0 = not fixed)
    uint32_t maximum;               // the largest it can be
    uint8_t *(*produce)(int index, size_t *length, uint8_t *work);      // nullptr result: the body is absent (only DSDn)
    uint8_t *(*apply)(int index, uint8_t *body);                        // the engine's restore_*: returns where it stopped reading
    const char *(*check)(const Sub &sub, const Body &body, Seen &seen); // nullptr: acceptable, otherwise why not
    int index;
    bool whole;                     // restore_* reads every byte of the body (restore_blitter reads 4 of the 63)
};

uint8_t *produce_dsk(int n, size_t *len, uint8_t *w) { return save_disk(n, len, w, false); }
uint8_t *produce_dsd(int n, size_t *len, uint8_t *w) { return save_disk2(n, len, w); }
uint8_t *produce_disk(int, size_t *len, uint8_t *w) { return save_floppy(len, w); }
uint8_t *produce_chip(int, size_t *len, uint8_t *w) { return save_custom(len, w, 0); }
uint8_t *produce_bltx(int, size_t *len, uint8_t *w) { return save_blitter_new(len, w); }
uint8_t *produce_blit(int, size_t *len, uint8_t *w) { return save_blitter(len, w, false); }
uint8_t *produce_chsl(int, size_t *len, uint8_t *w) { return save_custom_slots(len, w); }
uint8_t *produce_spr(int n, size_t *len, uint8_t *w) { return save_custom_sprite(n, len, w); }
uint8_t *produce_aud(int n, size_t *len, uint8_t *w) { return save_audio(n, len, w); }
uint8_t *produce_beam(int, size_t *len, uint8_t *w);
uint8_t *produce_bplx(int, size_t *len, uint8_t *w) { return save_custom_bpl(len, w); }

uint8_t *apply_dsk(int n, uint8_t *b) { return restore_disk(n, b); }
uint8_t *apply_dsd(int n, uint8_t *b) { return restore_disk2(n, b); }
uint8_t *apply_disk(int, uint8_t *b) { return restore_floppy(b); }
uint8_t *apply_chip(int, uint8_t *b) { return restore_custom(b); }
uint8_t *apply_bltx(int, uint8_t *b) { return restore_blitter_new(b); }
uint8_t *apply_blit(int, uint8_t *b) { return restore_blitter(b); }
uint8_t *apply_chsl(int, uint8_t *b) { return restore_custom_slots(b); }
uint8_t *apply_spr(int n, uint8_t *b) { return restore_custom_sprite(n, b); }
uint8_t *apply_aud(int n, uint8_t *b) { return restore_audio(n, b); }
uint8_t *apply_beam(int, uint8_t *b);
uint8_t *apply_bplx(int, uint8_t *b) { return restore_custom_bpl(b); }

// -- BEAM: the position of the beam and the phase of the frame (patch 0094) ---------------------------------------------------------------------------

// The words of the body, in the order of p6_custom_beam_get.
enum BeamWord : unsigned {
    kVpos, kVposPrev, kAgnusHpos, kAgnusHposPrev, kAgnusHposNext, kAgnusVposNext, kAgnusPosChange, kHhpos,
    kLinearVpos, kLinearHpos, kLinearVposPrev0, kLinearVposPrev1, kLinearVposPrev2, kLinearHposPrev0, kLinearHposPrev1, kLinearHposPrev2,
    kLinearVposVsync, kLinearDisplayVpos, kLinearVposVisible, kCurrentLinearVposVisible,
    kDisplayHsyncCounter, kDisplayVsyncCounter, kVsyncNext, kLinetoggle, kLofDisplay,
    kLofDetect, kLofPdetect, kLofDetectVsync, kPrevLof0, kPrevLof1, kPrevLof2,
    kLastHsyncLow, kLastHsyncHigh, kLastVsyncLow, kLastVsyncHigh,
    kAgnusVb, kAgnusPvb, kPrevStrobe, kDmalShifter, kDmal, kDmalNext, kLatches,      // 20 flags from here: phsync, phblank, pvsync, pcsync, csync, vb_active, ... bsvb_prev, equdis
    kBeamWords = kLatches + 20
};
constexpr uint32_t kBeamBytes = kBeamWords * 4;

uint8_t *produce_beam(int, size_t *len, uint8_t *w)
{
    int32_t words[kBeamWords];
    if (!p6_custom_beam_get(words, kBeamWords)) return nullptr;
    for (unsigned i = 0; i < kBeamWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kBeamBytes;
    return w;
}

uint8_t *apply_beam(int, uint8_t *body)
{
    int32_t words[kBeamWords];
    for (unsigned i = 0; i < kBeamWords; ++i) words[i] = int32_t(Body{body, kBeamBytes}.u32(i * 4));
    return p6_custom_beam_set(words, kBeamWords) ? body + kBeamBytes : nullptr;
}

// -- what each body must be before it is applied ----------------------------------------------------------------------------------------------------

// DSKn: id u32, state u8 (1 motor, 2 disabled, 4 id bit, 8 disk change, 16 side, 32 write protect), cylinder u8, ready u8, id counter u8, head position u32,
// CRC u32, two u16, the flag u32 that save_disk always writes as zero.
const char *check_dsk(const Sub &sub, const Body &b, Seen &seen)
{
    const int n = sub.index;
    const int type = currprefs.floppyslots[n].dfxtype;      // 0 is a 3.5" DD drive and -1 none: the drives of the A500 profile
    const bool absent = type < 0;
    if (type > 0 || b.u32(0) != (absent ? 0u : kDriveId35DD)) return "the drive id is not the one of this configuration";
    if (((b.u8(4) & 2) != 0) != absent) return "the disabled bit is not the one of this configuration";
    if (b.u8(4) & 0xC0) return "unknown bits in the drive state";
    if (b.u8(5) > kCylinderMax) return "the cylinder is beyond the last one the head can reach";
    if (b.u8(6) > 1) return "the ready flag is not a flag";
    if (b.u8(7) > 31) return "the drive id counter is beyond 31";
    if (b.u32(8) >= kMfmBitsMax) return "the head position is beyond the largest track";
    if (b.u32(20) != 0) return "the path flag is set: this state carries no path";
    seen.drive_state[n] = uint8_t(b.u8(4));
    seen.head_position[n] = b.u32(8);
    return nullptr;
}

// DSDn: mode u32 (1, or 3 with the timing of every word), bit counter u16, track length u32, speed u16, skip offset u32, index offset u32, the words, revolutions u16.
const char *check_dsd(const Sub &sub, const Body &b, Seen &seen)
{
    if (!b.n) return nullptr;
    const int n = sub.index;
    if ((seen.drive_state[n] & 3) != 1) return "a buffered track for a drive that is off or does not exist";
    if (b.n < kDsdFixed) return "shorter than its own header";
    const uint32_t mode = b.u32(0), length = b.u32(6);
    if (mode != 1 && mode != 3) return "the mode is neither 1 nor 3";
    if (!length || length > kMfmBitsMax) return "the track length is outside the buffer";
    if (b.n != kDsdFixed + (length + 15) / 16 * (mode == 3 ? 4 : 2)) return "the length does not follow from the track length in its header";
    if (seen.head_position[n] >= length) return "the head position of the drive is beyond the end of the track";
    return nullptr;
}

// DISK: word u16, bit offset u8, sync found u8, hpos u8, DMA state u8, 3 x (word u16, in use u8), length u16.
const char *check_disk(const Sub &, const Body &b, Seen &)
{
    if (b.u8(2) > 15) return "the bit offset is beyond 15";
    if (b.u8(3) > 1) return "the sync flag is not a flag";
    if (b.u8(5) != 0 && b.u8(5) != 2 && b.u8(5) != 3) return "the DMA state is not off, read or write";
    if (b.u8(8) > 2 || b.u8(11) > 2 || b.u8(14) > 2) return "a FIFO slot is in use in a way the controller never leaves it";
    if (b.u16(15) > 0x3fff) return "the DMA length is beyond 16383 words";
    return nullptr;
}

// CHIP: the chipset flags u32, then the register image. The chipset and the video standard are the configuration; FMODE is zero without AGA.
const char *check_chip(const Sub &, const Body &b, Seen &)
{
    if (b.u32(0) != (uint32_t(currprefs.chipset_mask) | 0x100)) return "the chipset is not the one of this configuration";
    if (b.u16(350) != (0x8000u | (currprefs.ntscmode ? 1u : 0u))) return "the video standard is not the one of this configuration";
    if (b.u16(352) != 0) return "FMODE is set in a chipset that has none";
    return nullptr;
}

// BLTX: the cycle-exact byte, the state, ... the channel mask blit_ch at 46 (BLTCON0's USEA..USED), blitfc and blitife at 51 and 52 (they index blit_filltable),
// the sentinel 0x1234 at 84 and 0x1235 at 130 (restore_blitter_new carries on past a wrong one).
const char *check_bltx(const Sub &, const Body &b, Seen &)
{
    if (b.u8(0) != (currprefs.blitter_cycle_exact ? 3u : 0u)) return "the blitter is not the one of this configuration";
    if (b.u8(1) > 2) return "the state of the blitter is not idle, pending or running";
    if (b.u8(46) > 15) return "the channel mask of the blit is beyond four bits";
    if (b.u8(51) > 1 || b.u8(52) > 1) return "a fill flag of the blit is not a flag";
    if (b.u16(84) != 0x1234 || b.u16(130) != 0x1235) return "a sentinel of the body is not where it must be";
    return nullptr;
}

// BLIT: the flags u32 that restore_blitter reads (save_blitter writes 8|16, or 1|4|8|16 for an idle blitter).
const char *check_blit(const Sub &, const Body &b, Seen &)
{
    const uint32_t flags = b.u32(0);
    if (flags != (8 | 16) && flags != (1 | 4 | 8 | 16)) return "the flags are not the ones save_blitter writes";
    return nullptr;
}

// CHSL: flags u32 (1|8, plus 2 or 4 for a copper in its last move), the copper's pointer and two words, 8 zero words, 3 slots of 54 bytes, the first offset u8.
const char *check_chsl(const Sub &, const Body &b, Seen &)
{
    const uint32_t flags = b.u32(0);
    if (flags != 9 && flags != 11 && flags != 13) return "the flags are not the ones save_custom_slots writes";
    for (uint32_t at = 12; at < 28; at += 2)
        if (b.u16(at)) return "a word that is always zero is not";
    for (uint32_t slot = 0; slot < 3; ++slot) {
        const uint32_t base = 28 + slot * 54;
        if (b.u16(base + 4) > 0x1fe) return "a slot names a register beyond 0x1FE";
        for (uint32_t at = base + 50; at <= base + 52; at += 2) {       // the registers of the pointers: getptfromreg takes anything else for a dummy
            const unsigned reg = b.u16(at);
            const bool pointer = reg == 0xFFFF || reg == 0x000 || reg == 0x070 || reg == 0x072 || reg == 0x074 || (reg >= 0x110 && reg <= 0x11a && !(reg & 1)) ||
                                 (reg >= 0x140 && reg <= 0x14e && !(reg & 1)) || reg == 0x08c || (reg >= 0x200 && reg <= 0x205);      // 0x200-0x205: patch 0092
            if (!pointer) return "a slot points at a register the engine has no pointer for";
        }
    }
    if (b.u8(190) > 3) return "the first slot offset is beyond 3";
    return nullptr;
}

// SPRn: pointer u32, position u16, control u16, 8 data words, flags u8 (armed, fetch mode).
const char *check_spr(const Sub &, const Body &b, Seen &)
{
    if (b.u8(24) & 0xF8) return "unknown bits in the sprite flags";
    return nullptr;
}

// AUDn: state u8, volume u8, interrupt flag u8, flags u8 (1 request, 2 request pending, 0x80 always), length, wlength, period, data u16, lc, pt, evtime u32, hpos u8.
const char *check_aud(const Sub &, const Body &b, Seen &)
{
    if (b.u8(0) > 5) return "the state of the channel is beyond 5";
    if (b.u8(2) > 1) return "the interrupt flag is not a flag";
    if ((b.u8(3) & 0xFC) != 0x80) return "the flags of the channel are not the ones save_audio writes";
    return nullptr;
}

// BPLX: the version, then the bitplane data registers.
const char *check_bplx(const Sub &, const Body &b, Seen &)
{
    if (b.u32(0) != 1) return "the version of the body is not 1";
    return nullptr;
}

// BEAM: where the beam is and where the frame is. The standard is the configuration's (PAL 312 lines, NTSC 262; no ECS programmable beam in the profile), so the beam
// and what the line start tests are bounded by it; a counter that only counts (display_vsync_counter, of which the parity is used; the linear counters of the display)
// has no bound but its sign, and wraps like the engine's.
const char *check_beam(const Sub &, const Body &b, Seen &)
{
    const int lines = currprefs.ntscmode ? 262 : 312, positions = 228;      // MAXVPOS_NTSC / MAXVPOS_PAL; the longest line (NTSC with lol) has 228 colour clocks
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    const auto within = [&at](unsigned word, int low, int high) { return at(word) >= low && at(word) <= high; };
    if (!within(kVpos, 0, lines) || !within(kVposPrev, 0, lines)) return "the line of the beam is beyond the last one of the standard";
    if (!within(kAgnusHpos, 0, positions - 1) || !within(kAgnusHposPrev, 0, positions - 1)) return "the colour clock of the beam is beyond the longest line";
    if (!within(kAgnusHposNext, -1, 255) || !within(kAgnusVposNext, -1, 2047) || !within(kAgnusPosChange, -2, 2)) return "a pending beam write is out of range";
    if (!within(kHhpos, 0, 0xffff)) return "the horizontal counter is not 16 bits";
    for (unsigned w : {kLinearVpos, kLinearVposPrev0, kLinearVposPrev1, kLinearVposPrev2, kLinearVposVsync, kLinearDisplayVpos, kLinearVposVisible, kCurrentLinearVposVisible,
                       kLinearHpos, kLinearHposPrev0, kLinearHposPrev1, kLinearHposPrev2})
        if (!within(w, 0, INT32_MAX)) return "a linear counter is negative";       // they count lines and colour clocks of the display: nothing indexes with them unchecked
    if (!within(kDisplayHsyncCounter, 0, lines + 1)) return "the display line counter is beyond the frame";
    for (unsigned w : {kVsyncNext, kLinetoggle, kLofDisplay, kLofDetect, kLofPdetect, kLofDetectVsync, kPrevLof0, kPrevLof1, kPrevLof2, kAgnusPvb})
        if (!within(w, 0, 1)) return "a flag of the frame phase is not a flag";
    if (!within(kDmal, 0, 0xffff) || !within(kDmalNext, 0, 1)) return "the DMA list of the line is not 16 bits, or its flag is not a flag";       // kDmalShifter is a bit register
    if (!within(kAgnusVb, 0, 2)) return "the vertical blank latch is not off, on or starting";
    if (at(kPrevStrobe) < 0x38 || at(kPrevStrobe) > 0x3e || (at(kPrevStrobe) & 1)) return "the strobe of the last line is not one of the four";
    for (unsigned w = kLatches; w < kBeamWords; ++w)
        if (!within(w, 0, 1)) return "a sync latch is not a flag";
    if (at(kLastHsyncHigh) < 0 || at(kLastVsyncHigh) < 0) return "a time stamp of the display is negative";
    return nullptr;
}

// The lengths of this build: tests/state/test_state_upstream.py checks them against a real save, and a save that writes another length fails.
constexpr Sub kDiskBodies[] = {
    {"DSK0", 24, 24, produce_dsk, apply_dsk, check_dsk, 0, true}, {"DSK1", 24, 24, produce_dsk, apply_dsk, check_dsk, 1, true},
    {"DSK2", 24, 24, produce_dsk, apply_dsk, check_dsk, 2, true}, {"DSK3", 24, 24, produce_dsk, apply_dsk, check_dsk, 3, true},
    {"DSD0", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 0, true}, {"DSD1", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 1, true},
    {"DSD2", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 2, true}, {"DSD3", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 3, true},
    {"DISK", 17, 17, produce_disk, apply_disk, check_disk, 0, true},
};
constexpr Sub kChipsetBodies[] = {
    {"CHIP", 360, 360, produce_chip, apply_chip, check_chip, 0, true}, {"BLTX", 226, 226, produce_bltx, apply_bltx, check_bltx, 0, true},
    {"BLIT", 63, 63, produce_blit, apply_blit, check_blit, 0, false}, {"CHSL", 191, 191, produce_chsl, apply_chsl, check_chsl, 0, true},
    {"SPR0", 25, 25, produce_spr, apply_spr, check_spr, 0, true}, {"SPR1", 25, 25, produce_spr, apply_spr, check_spr, 1, true},
    {"SPR2", 25, 25, produce_spr, apply_spr, check_spr, 2, true}, {"SPR3", 25, 25, produce_spr, apply_spr, check_spr, 3, true},
    {"SPR4", 25, 25, produce_spr, apply_spr, check_spr, 4, true}, {"SPR5", 25, 25, produce_spr, apply_spr, check_spr, 5, true},
    {"SPR6", 25, 25, produce_spr, apply_spr, check_spr, 6, true}, {"SPR7", 25, 25, produce_spr, apply_spr, check_spr, 7, true},
    {"BEAM", kBeamBytes, kBeamBytes, produce_beam, apply_beam, check_beam, 0, true},
};
constexpr Sub kAudioBodies[] = {
    {"AUD0", 25, 25, produce_aud, apply_aud, check_aud, 0, true}, {"AUD1", 25, 25, produce_aud, apply_aud, check_aud, 1, true},
    {"AUD2", 25, 25, produce_aud, apply_aud, check_aud, 2, true}, {"AUD3", 25, 25, produce_aud, apply_aud, check_aud, 3, true},
};
constexpr Sub kVideoBodies[] = {{"BPLX", 292, 292, produce_bplx, apply_bplx, check_bplx, 0, true}};

// What a save_* call may change besides the bytes it returns, put back when the call is done.
struct PureSave {
    int state;
    unsigned poll;
    PureSave() : state(savestate_state), poll(p6_input_poll_mask) { savestate_state = STATE_DOSAVE; }
    ~PureSave()
    {
        savestate_state = state;
        p6_input_poll_mask = poll;
    }
    PureSave(const PureSave &) = delete;
    PureSave &operator=(const PureSave &) = delete;
};

// The machine while the engine's restore_* write into it: savestate_state says "restoring" (a register setter then acts at once instead of being
// pipelined, the audio and the disk keep their hands off what is being put back), and the scheduler, which P6SC has just applied and owns, is put back when
// the body is done, whatever a restore_* or a *_finish scheduled.
struct ApplyScope {
    bool on;
    int state = 0, idx = 0, next = 1, count = 0, restart = 0, remain = 0;
    evt_t nextevent_before = 0;
    ev main[ev_max];
    ev2 secondary[ev2_max];
    explicit ApplyScope(bool apply) : on(apply)
    {
        if (!on) return;
        state = savestate_state;
        savestate_state = STATE_RESTORE;
        memcpy(main, eventtab, sizeof main);
        memcpy(secondary, eventtab2, sizeof secondary);
        p6_events_get(&idx, &next, &count, &restart, &remain);
        nextevent_before = nextevent;
    }
    ~ApplyScope()
    {
        if (!on) return;
        memcpy(eventtab, main, sizeof main);
        memcpy(eventtab2, secondary, sizeof secondary);
        p6_events_set(idx, next, remain);
        nextevent = nextevent_before;
        savestate_state = state;
    }
    ApplyScope(const ApplyScope &) = delete;
    ApplyScope &operator=(const ApplyScope &) = delete;
};

void fail_in(Archive &a, const char *tag, const char *why)
{
    static char text[128];      // the first failure sticks and the archive keeps the pointer: one message at a time is enough
    snprintf(text, sizeof text, "%s: %s", tag, why);
    a.fail(text);
}

template <size_t N> void visit_bodies(Archive &a, const Sub (&subs)[N], void (*finish)())
{
    static uint8_t work[kWork];
    Seen seen = {};
    const ApplyScope scope(a.applying());
    for (const Sub &sub : subs) {
        uint32_t length = sub.maximum;      // Measure: the largest body, whatever the machine holds
        if (a.mode() == Mode::Save) {
            size_t produced = 0;
            {
                const PureSave pure;
                uint8_t *body = sub.produce(sub.index, &produced, work);
                if (!body) produced = 0;
            }
            if (sub.fixed ? produced != sub.fixed : produced > sub.maximum) {
                static char why[96];
                snprintf(why, sizeof why, "upstream body %s is %zu bytes, this build's table says %u", sub.tag, produced, unsigned(sub.fixed ? sub.fixed : sub.maximum));
                a.fail(why);
                return;
            }
            length = uint32_t(produced);
            if (const char *reason = sub.check(sub, Body{work, length}, seen)) {     // a live value that no state may carry is a defect of the machine
                fail_in(a, sub.tag, reason);
                return;
            }
        }
        if (a.reading()) {
            char tag[4];
            for (char &c : tag) c = char(a.val<uint8_t>(0, "upstream.tag"));
            if (a.ok() && memcmp(tag, sub.tag, 4)) fail_in(a, sub.tag, "another body, or this one out of its place");
            length = a.val<uint32_t>(0, "upstream.length");
            if (a.ok() && (sub.fixed ? length != sub.fixed : length > sub.maximum)) fail_in(a, sub.tag, "the length is not the one of this build");
            if (!a.ok()) return;
            for (uint32_t i = 0; i < length && a.ok(); ++i) work[i] = a.val<uint8_t>(0, sub.tag);
            if (!a.ok()) return;
            if (const char *reason = sub.check(sub, Body{work, length}, seen)) {
                fail_in(a, sub.tag, reason);
                return;
            }
            if (a.applying() && (length || sub.fixed)) {
                const uint8_t *end = sub.apply(sub.index, work);
                const size_t read = size_t(end - work);
                if (end < work || read > length || (sub.whole && read != length)) {
                    fail_in(a, sub.tag, "the engine's reader did not stop where the body does");
                    return;
                }
            }
            continue;
        }
        a.bytes(const_cast<char *>(sub.tag), 4, sub.tag);
        a.val<uint32_t>(length, "upstream.length");
        a.bytes(work, length, sub.tag);
    }
    if (a.applying() && a.ok() && finish) finish();
}

void finish_chipset() { restore_blitter_finish(); }
void finish_audio() { restore_audio_finish(); }

}  // namespace

void visit_disk(Archive &a) { visit_bodies(a, kDiskBodies, nullptr); }
void visit_chipset(Archive &a) { visit_bodies(a, kChipsetBodies, finish_chipset); }
void visit_audio(Archive &a) { visit_bodies(a, kAudioBodies, finish_audio); }
void visit_video(Archive &a) { visit_bodies(a, kVideoBodies, nullptr); }

// The medium that sits in each drive is the one the state's body names: restore_disk neither inserts nor ejects, so a state taken with another disk in the
// drive (or none) would leave the engine with a disk the state knows nothing about. The CRC of the image is what drive_insert measured, and the DSKn bodies
// are the first four sub-bodies of the chunk (24 bytes each, after their tag and length).
const char *disk_matches_machine(const uint8_t *body, uint64_t length)
{
    static char text[96];
    if (length < 4 * (8 + 24)) return "the disk chunk is too short for its drive bodies";
    for (int n = 0; n < 4; ++n) {
        uint8_t live[64];
        size_t produced = 0;
        {
            const PureSave pure;
            if (!save_disk(n, &produced, live, false) || produced != 24) return "the engine's drive body is not 24 bytes";
        }
        const Body state{body + n * 32 + 8, 24}, now{live, 24};
        if (state.u32(12) != now.u32(12)) {
            snprintf(text, sizeof text, "DSK%d: the disk in the drive is not the one the state was taken with", n);
            return text;
        }
    }
    return nullptr;
}

}  // namespace p6_state
