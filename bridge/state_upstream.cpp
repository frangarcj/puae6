// Chunks P6DK (disk), P6AU (audio), P6CH (chipset) and P6VD (video) with the bodies WinUAE's own save_* and restore_* functions write and read
// (docs/adr/0009-state-format.md, point 8). Each body travels as a sub-body: its tag of 4 characters (the tag upstream's container gives it), a u32
// length and the bytes the engine wrote, with no ASF container and no zfile. The engine is compiled with P6_STATE_BODIES (patches 0086-0091), SAVESTATE
// stays undefined, and what upstream also saves but another chunk owns (CYCS, CHPD, CHPX, CIAA/B, KEYB, CINP, the CPU, the RAMs) is not here.
//
//   P6DK  DSK0..DSK3 (24 bytes each), DSD0..DSD3 (absent: length 0; present: the engine's track buffer), DISK (17) and DSKX (248), the last ours and not upstream's
//   P6CH  CHIP (360), CAGA (1024, AGA machines only), BLTX (226), BLIT (63), CHSL (245), SPR0..SPR7 (25 each), BEAM (320), COPR (60) and BLTP (72), the last three ours and not upstream's
//   P6AU  AUD0..AUD3 (25 each), AUDP (356) and DCLK (160), the last two portable extensions
//   P6VD  BPLX (292), DNSP (64), DRNG (90132, AGA machines only), GEOM (104) and PIXL (the rows of the picture, up to kRasterBound bytes), the last four ours and not upstream's
//
// BEAM (patch 0103 added four words) also carries the first and last line of the vertical display window and the flag that tests them: calcvdiw derives the lines from the registers and
// restore_custom runs it, but custom_reset does not, so a machine that never wrote DIWSTRT has other lines than the one that loaded its state, and the vertical window bit that DIWHIGH
// saves one frame later differs. The fourth is interlace_seen: a machine that had not seen the interlace found it at the next vertical blank and restarted the display.
// Patch 0104 adds two more, programmed_register_accessed_h and _v (65 to 67 words): on an ECS Agnus restore_custom sets the horizontal one itself, and the programmed-beam checks that it
// switches on add update_agnus_vb calls that shift current_linear_vpos_visible by a line; they are not derived from the registers.
// Patch 0220 adds thirteen (67 to 80 words, T15l): the monitor's no-signal countdown (nosignal_cnt, nosignal_status, nosignal_trigger), lines_after_beamcon_change, syncs_stopped, and the bltadd and
// conflictaddr of the four slots of the DMA pipeline. A change of the programmed beam, which is how the Kickstart 2.05 switches the display on, blanks the picture for two vertical blanks, and
// BPLCON0 ERSY stops the beam counters at the end of a line; a state taken inside them drew the display on one or two units early, or ran with real hsyncs; and a blit in flight whose D write
// had lost its pointer increment wrote a whole screen over one word. save_custom_slots (CHSL, which comes before BEAM) does not write those two fields.
//
// Patch 0261 adds nine (80 to 89 words, T15n): the bitplane sequencer of Agnus (bprun, bprun_cycle, ddf_stopping, ddf_enable_on, ddf_limit_in, ddf_limit, ddfstrt_match, hwi_old and ddfstrt_val).
// custom_reset puts them to idle and no body names them; a program that rewrites DIWHIGH all the time (the vertical window opens and closes in the middle of a fetch) leaves them
// elsewhere at a vertical blank, and the machine that loaded the state fetched its first display line differently: the CPU, which the DMA of that line had been holding off the bus,
// ran 148 colour clocks early. Patch 0260 is the other half of T15n: p6_custom_rebuild_derived runs init_beamcon0, so that a programmed beam loaded into a fresh image gets its derived lengths.
//
// BEAM is the one body that is not WinUAE's: its restore_custom reads VPOSR and VHPOSR and throws them away, and the counters of the frame, the line history and the
// sync latches of Agnus live in file statics that no body names. It is 61 words of 32 bits, big endian like the others, copied by p6_custom_beam_get and
// p6_custom_beam_set (patch 0094; the list is in custom.cpp): the beam and the line history, the frame phase (display_hsync_counter, display_vsync_counter,
// maxvpos_display_vsync_next, the field detection), the strobe that raises the vertical blank, the DMA list of the line in flight and the sync latches. The line length is the
// only value derived (setmaxhpos), from the lol that CHIP restores and the linetoggle it carries, which is why BEAM comes after CHIP and no init_hz or reset head runs.
//
// COPR is the other: CHSL keeps the Copper's ip and instruction words and restores its state as stopped (or as the last cycle of a MOVE), so a Copper that was waiting for the
// beam, skipping or half way through a strobe came back stopped. It is 15 words (patch 0095, p6_custom_copper_get and p6_custom_copper_set): the state and its two companions,
// the compare values of the wait, the strobe in flight, the latched COP1LC, the flag that lets the Copper run this line and the two cycle stamps that the strobe and the DMA enable
// are compared with. Values only: the Copper does not run, advance or drain when it is applied, and COPR comes after CHSL, which it overrides.
//
// AUDP is the fourth (patch 0099, p6_audio_phase_get and p6_audio_phase_set): restore_audio rounds the period, copies dat into dat2 and zeroes what AUDn does not carry, and
// restore_audio_finish sets last_cycles to the cycle of the load, so a restored Paula restarted its resampler phase, its filters and its interpolation from zero and lost
// the DMA latches of its channels. 89 words: the resampler phase (next_sample_evtime and the cycles not yet integrated, 64 bits), whether audio was activated, the LED filter and
// the five RC stages of the four output filters, and 16 words per channel (the DMA latches, the exact period, dat2, the fast-CPU latches, the interpolation integrator,
// the ADKCON mask and, since patch 0250, the volume the mixer uses: loadper latches AUDxVOL into mixvol when a period is loaded and restore_audio sets it to the current register, so
// a state taken between a volume write and the next period load played the new volume where the original still played the old one). Applied as values after restore_audio_finish; Paula does not run on load. The profiles that would need more (sinc interpolation, volume counters,
// delayed crossfeed) make both calls refuse, and the save fails with the body's length.
//
// BLTP is the third: restore_blitter_new puts back the registers, the shifters, the counters and the pipeline of the cycle-exact blitter and drops what keeps a blit in flight
// on the same cycles as the saved one (patch 0097, p6_blitter_phase_get and p6_blitter_phase_set): blit_queued, blit_count_done, blt_change_cycles, the held data words, the
// line's D latch, the pointers a copper write of the same cycle restores, the flag of a pending BLTCON or modulo write, and blit_dmacount and blit_cyclecount, which BLTX
// carries but its reader discards. 18 words, applied as values after restore_blitter_finish (which sets the pending flag); the blitter does not run, drain or finish on load,
// and a blit that the live machine had in flight is replaced by the state's, not cancelled.
//
// What the engine derives from the registers and stores nowhere (the plane count and the fetch sequence of setup_fmodes, the first and last line of each sprite, the sprite
// width) is recomputed once the bodies of CHIP are in, by p6_custom_rebuild_derived (patch 0098): WinUAE's own load gets it from custom_reset, which is never run here. It
// writes no register, schedules nothing and runs nothing.
//
// DNSP (the fifth body of ours) (patch 0100, p6_denise_pipe_get and p6_denise_pipe_set): the four-pixel output pipeline of Denise, dtbuf and dtgbuf, two halves of four pixels each. Every colour
// clock draws into one half and copies out the other, so the first pixels of a line leave from what the previous clock left, which no body carries (BPLX has the shift registers).
// 16 words: the eight 32-bit pixels, then the eight 8-bit pixel numbers of the genlock buffer. Applied as values after BPLX; nothing is drawn.
//
// GEOM (the sixth) (patch 0100, p6_frame_geometry_get and p6_frame_geometry_set): the size of the raster the machine has settled on. The engine follows the frame it measures
// (the lines and colour clocks of the last frames, which BEAM carries) and adopts a new size only after the same measure has held for a few frames; the size of the picture
// (in and out width and height, extras and sync offsets of the drawing buffer) is derived from what it adopted and is in no body, so a machine that had adopted another frame kept
// it and a new image adopted the saved one units after the load, with a blank picture in the unit it switched. 19 words: the adopted counts, the candidate and its countdown, and the
// geometry of the drawing buffer. Applied as values after BPLX and DNSP; the presentation publishes the allocation as compute_framesync does. Patch 0103 adds three words: whether the
// presentation refused the mode as larger than its buffer (compute_framesync clamps the sizes afterwards, so the words above look like a mode that fits, and a machine that had not
// refused it handed over pictures the original never did), the allocation of the last mode it accepted and the flags of the drawing: the three requests to clear the buffer that wait
// for the first line of the next frame (display_redraw, erase_next_draw, center_y_erase) and whether the frame being drawn holds the buffer (thread_debug_lock): a machine that loaded a
// state without them wiped the other field of an interlaced weave that the original kept, kept it where a restart of its own had left a request, or drew lines in a refused mode.
//
// PIXL (the eighth) (patch 0103, p6_frame_raster): the rows of the drawing buffer, which the renderer writes only where it draws, so that a unit without drawing, or the other field of an
// interlaced picture, hands over what the history before the state left there. It has no length of its own: its width and height are the allocation GEOM names, the pixels travel as the
// 12 bits of the chipset colour in groups of literals and repeats (see visit_raster), and the bound is the one of the largest buffer the presentation can hold. On an AGA machine
// (T24c) the colours have 24 bits and a pixel travels as three bytes (R, G, B) in the same groups, with its own bound; DRNG (patch 0109), also AGA only, is the ring through which
// Agnus hands Denise its data, 4096 slots and five cursors, because a program that rewrites FMODE all the time keeps it full at the boundary.
//
// DSKX (the seventh) (patch 0101, p6_disk_phase_get, p6_disk_phase_set and p6_disk_newname_set): what the floppy controller keeps that DISK, DSKn and DSDn do not. DISK carries only
// the low byte of disk_hpos, so a machine that a CPU access to DSKBYTR had brought up to the middle of a line was brought up from the start of it after a load and read
// another word of the track; dsklen2 (the previous DSKLEN, which decides whether one write or two start a transfer) is in no body; restore_disk zeroes dskchange_time, so a
// delayed insert that was counting down never landed; the motor-off latch, the step limiter, the index decay and the wordsync edge flag are not carried either, and the geometry and bit counter of
// the track under the head are in DSDn only while a track is buffered (a drive that spins empty after an eject advances its head from the ones it kept). 62 words: disk_hpos, dsklen2,
// dsksync_on, indexdecay and prev_data (patch 0105: the last value written to CIA-B port B, from which DISK_select_set, which the apply calls, derives the drive mask, the step line,
// the direction and the side that restore_cia_finish would rebuild: without it the first write after a load was judged against the latch of the machine's own history, and the
// head stepped, or the shifter bits were drawn, differently from the original), then for each drive dskchange_time, motordelay, steplimit, the age of the last step, floppybitcounter, tracklen, trackspeed, skipoffset, indexoffset and (patch 0330, T57) prevtracklen, the length the head position was last scaled to: 0 right after
// an insert, and restore_disk copied it from the live machine's track, so a head that left a raw track of an extended ADF for one of another length was scaled by a ratio the original never saw, and (patch 0334, T57b)
// multi_revolution (the buffer holds one of several different revolutions of the track: an FDI whose track has weak bits), revolution_check (the lines before the next revolution may be fetched), fdirev and
// fditrack (which revolution of which track fdi2raw decoded last: the next decode is a function of both). Applied as values after DISK; nothing
// is inserted, ejected or scheduled. The image a countdown is waiting for is not a word: the bridge knows it from the medium the state says the drive holds (P6BR), and
// rebuild_disk, which runs after every chunk is applied, names it in the preferences and in the drive, which is also what makes a later mount of that medium count as a change.
//
// The border colour of Denise (bordercolor, bordercolor_ecs_shres) is derived the same way and is rebuilt by p6_denise_rebuild_derived (patch 0100) after CHIP: restore_custom
// puts the colour registers into both copies of the table, but runs its queue of BPLCON writes (which compute the border colour) before it rebuilds acolors.
//
// Save writes every body with the engine's save_* and changes nothing: STATE_DOSAVE keeps getjoystate from reading the host's input (maybe_read_input),
// the poll mask that POT0DAT and POT1DAT set is put back, and patch 0088 keeps DMACONR from writing dmacon.
//
// Validate reads each sub-body whole (a byte at a time through the archive, so that Validate and Apply see the same bytes) and refuses, before anything
// is applied: a tag out of its place, a length other than the one this build's save_* gives, and the values that end up indexing (a cylinder, a track length,
// a head position, the state of a channel, the channel count of a blit, the register of a slot...). It does not harden the engine's arithmetic against
// magnitudes this build never produces (ADR point 8). What belongs to the configuration is not adapted: the drive id and the disabled bit of a DSKn, the
// chipset mask and the video standard of CHIP are compared with the configuration of the image and a body that names others is refused; patches 0090 and
// 0091 remove the writes of restore_custom and restore_disk into the preferences. The medium that sits in a drive is not compared with the live drive's: the state
// names it (P6BR's drive_media indexes the manifest, which the identity compares, and DSKn carries the CRC of that image), disk_matches_manifest checks that the two agree
// before anything is applied, and the apply of P6DK puts into each drive the medium the state says it holds (see visit_disk).
//
// Apply runs the engine's restore_* on the live machine in upstream's file order, with savestate_state = STATE_RESTORE for exactly that long. It never calls
// custom_reset, m68k_reset2, memory_clear, restore_*_start, init_hz or audio_activate. The *_finish that put back what is not saved are restore_audio_finish
// (last_cycles, which is not in the body, is the cycle counter P6SC has just restored) and restore_blitter_finish (the pending modulo loads); not called:
// restore_custom_finish (it would turn Genlock on in the preferences for a body with ERSY set) and restore_disk_finish (it works from the preferences and
// the GUI: DISK_check_change would act on a medium change the state does not carry). The scheduler belongs to P6SC: whatever a restore_* or a *_finish
// writes into eventtab, eventtab2 and their indices (schedule_audio, events_schedule, event2_newevent_xx from a register setter) is put back when the body is
// done.
#include "state.h"
#include "media/cd_device.h"

#include <stddef.h>
#include <stdint.h>
#include <math.h>
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
#include "video_present.h"
#include "media.h"
#include "session.h"
#include "config.h"
#include "blob_io.h"
#include "overlay.h"
#include "drive_sound.h"

extern void p6_events_get(int *idx, int *next, int *cnt, int *restart, int *remain);       // events.cpp 0078
extern void p6_events_set(int idx, int next, int remain);
extern bool p6_custom_beam_get(int32_t *out, unsigned n);       // custom.cpp 0094
extern bool p6_custom_beam_set(const int32_t *in, unsigned n);
extern bool p6_lightpen_latch_get(int32_t out[4]);
extern void p6_lightpen_latch_set(const int32_t in[4]);
extern bool p6_custom_ciatod_get(int32_t *out, unsigned n);     // custom.cpp 0240
extern bool p6_custom_ciatod_set(const int32_t *in, unsigned n);
extern bool p6_custom_copper_get(int32_t *out, unsigned n);     // custom.cpp 0095
extern bool p6_custom_copper_set(const int32_t *in, unsigned n);
extern bool p6_blitter_phase_get(int32_t *out, unsigned n);     // blitter.cpp 0097
extern bool p6_blitter_phase_set(const int32_t *in, unsigned n);
extern void p6_custom_rebuild_derived(void);                    // custom.cpp 0098
extern void p6_denise_rebuild_derived(void);                    // drawing.cpp 0100
extern bool p6_denise_pipe_get(int32_t *out, unsigned n);
extern bool p6_denise_pipe_set(const int32_t *in, unsigned n);
extern bool p6_denise_ring_cursors_get(int32_t *out, unsigned n);       // custom.cpp 0109
extern bool p6_denise_ring_cursors_set(const int32_t *in, unsigned n);
extern bool p6_frame_geometry_get(int32_t *out, unsigned n);    // custom.cpp 0100
extern bool p6_frame_geometry_set(const int32_t *in, unsigned n);
extern bool p6_frame_raster(int *width, int *height, int *stride, uint8_t **first);     // custom.cpp 0103
extern bool p6_audio_phase_get(int32_t *out, unsigned n);       // audio.cpp 0099
extern bool p6_audio_phase_set(const int32_t *in, unsigned n);
extern uae_u32 p6_rng_get(void);                                // main.cpp 0076
extern void p6_rng_set(uae_u32 state);
extern bool p6_disk_phase_get(int32_t *out, unsigned n);        // disk.cpp 0101
extern bool p6_disk_phase_set(const int32_t *in, unsigned n);
extern bool p6_disk_newname_set(int num, const TCHAR *name);

namespace p6_state {

namespace {

// Words of the track buffer a state can hold (the buffer of disk.cpp is larger; the longest track the bridge lets in is 20000 bytes and the FDI reader writes at most 200000 bits). A DSDn body
// is 22 bytes plus a word for every 16 bits of the track and, with the timing of the track (an FDI: patch 0335), two bytes of timing for every 8 bits.
constexpr uint32_t kMfmWordsMax = 0x4000;
constexpr uint32_t kMfmBitsMax = kMfmWordsMax * 16;
constexpr uint32_t kDsdFixed = 4 + 2 + 4 + 2 + 4 + 4 + 2;
constexpr uint32_t kDsdMax = kDsdFixed + kMfmWordsMax * 6;
constexpr uint32_t kRingSlots = DENISE_RGA_SLOT_TOTAL, kRingSlotBytes = 22, kRingCursors = 5;
constexpr uint32_t kRingBytes = kRingCursors * 4 + kRingSlots * kRingSlotBytes;
constexpr size_t kWork = (kDsdMax > kRingBytes ? kDsdMax : kRingBytes) + 64;
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
    uint32_t chsl_flags;
    uint8_t bltx_state, bltx_dmacount, bltx_cyclecount;     // BLTX: idle, pending or running, and the channel counts of the blit
    uint16_t aud_period[4];                                 // AUDn: the period in colour clocks, 0 for PERIOD_MAX
    uint32_t drive_crc[4];                                  // DSKn: the CRC of the image in the drive, 0 for none
    struct { bool present; uint32_t bit_counter, track_length, speed, skip_offset, index_offset; } track[4];     // DSDn: the buffered track of a drive, if any
    uint8_t disk_hpos;                                      // DISK: the fraction of a colour clock disk_hpos had
    uint8_t disk_dma;                                       // DISK: the DMA state (0 off, 2 read, 3 write)
    uint16_t disk_length;                                   // DISK: the words left
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
    bool aga_only = false;          // a body of the AGA machines alone (T24c): the other machines have no such body, not an empty one
    bool mains_only = false;        // CTOD: mains-driven CIA-A or CD32's independent oscillator
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
uint8_t *produce_copr(int, size_t *len, uint8_t *w);
uint8_t *produce_bltp(int, size_t *len, uint8_t *w);
uint8_t *produce_audp(int, size_t *len, uint8_t *w);
uint8_t *produce_dnsp(int, size_t *len, uint8_t *w);
uint8_t *produce_geom(int, size_t *len, uint8_t *w);
uint8_t *produce_drng(int, size_t *len, uint8_t *w);
uint8_t *produce_dskx(int, size_t *len, uint8_t *w);
uint8_t *produce_bplx(int, size_t *len, uint8_t *w) { return save_custom_bpl(len, w); }
uint8_t *produce_caga(int, size_t *len, uint8_t *w) { return save_custom_agacolors(len, w); }

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
uint8_t *apply_copr(int, uint8_t *b);
uint8_t *apply_bltp(int, uint8_t *b);
uint8_t *apply_audp(int, uint8_t *b);
uint8_t *apply_dnsp(int, uint8_t *b);
uint8_t *apply_geom(int, uint8_t *b);
uint8_t *apply_drng(int, uint8_t *b);
uint8_t *apply_dskx(int, uint8_t *b);
uint8_t *apply_bplx(int, uint8_t *b) { return restore_custom_bpl(b); }
uint8_t *apply_caga(int, uint8_t *b) { return restore_custom_agacolors(b); }

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
    kPlfFirstLine = kLatches + 20, kPlfLastLine, kNeedVdiwCheck, kInterlaceSeen,      // patch 0103: the vertical display window, which calcvdiw derives and custom_reset does not, and the interlace
    kProgrammedH, kProgrammedV,        // patch 0104: whether a program has written the ECS registers that program the beam (restore_custom sets the horizontal one itself on an ECS Agnus)
    kNoSignalCount, kNoSignalStatus, kNoSignalTrigger, kLinesAfterBeamcon, kSyncsStopped,      // patch 0220: the monitor's no-signal countdown (two blank vertical blanks after a change of the programmed beam), its request and the stopped syncs (BPLCON0 ERSY)
    kRgaBltadd, kRgaConflictAddr = kRgaBltadd + 4,      // patch 0220: bltadd and conflictaddr of the four slots of the DMA pipeline (a blit D write in flight advances its pointer by the first)
    kBprun = kRgaConflictAddr + 4, kBprunCycle, kDdfStopping, kDdfEnableOn, kDdfLimitIn, kDdfLimit, kDdfStrtMatch, kHwiOld, kDdfStrtVal,      // patch 0261: the bitplane sequencer of Agnus (bprun, how far its fetch has gone, DDF passed, DDF enabled, the hard limits, the DDFSTRT match, the edge detector of "DMA and window and DDF" and the clock at which a fetch may start)
    kBeamWords
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

uint8_t *produce_lpen(int, size_t *len, uint8_t *w)
{
    int32_t words[4];
    p6_lightpen_latch_get(words);
    for (unsigned i = 0; i < 4; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = 16;
    return w;
}

uint8_t *apply_lpen(int, uint8_t *body)
{
    int32_t words[4];
    for (unsigned i = 0; i < 4; ++i) words[i] = int32_t(Body{body, 16}.u32(i * 4));
    p6_lightpen_latch_set(words);
    return body + 16;
}

const char *check_lpen(const Sub &, const Body &b, Seen &)
{
    const int32_t v = int32_t(b.u32(0));
    return v < -1 || v > 2047 || b.u32(4) > 255 || b.u32(8) > 65535 || b.u32(12) > 1 ? "optical beam latch is out of range" : nullptr;
}

// -- CTOD: where the TOD that the mains drives is in its count (patch 0240, T50) -------------------------------------------------------------------

// Two words of the machines whose CIA-A TOD counts the mains (the A1000 and the A2000s, cs_ciaatod 1 or 2): cia_hsync, the colour clocks to the next tick, and the bits of vblank_hz, the
// frame rate the length of a mains period is computed from (compute_framesync sets it from the frame the machine is in: short, long or interlaced). Each line lowers cia_hsync by its length,
// and the line in which it falls below that length ticks the TOD and adds the period (about 71000 clocks at 50 Hz): never negative, and a few periods at most. The machines whose TOD
// follows the vertical sync have no such body. CD32 also consumes cia_hsync:
// its dedicated branch adds 262/313 lines independently of cs_ciaatod.
constexpr uint32_t kCiaTodWords = 2, kCiaTodBytes = kCiaTodWords * 4;
constexpr int32_t kCiaTodMax = 1 << 20;

uint8_t *produce_ctod(int, size_t *len, uint8_t *w)
{
    int32_t words[kCiaTodWords];
    if (!p6_custom_ciatod_get(words, kCiaTodWords)) return nullptr;
    for (unsigned i = 0; i < kCiaTodWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kCiaTodBytes;
    return w;
}

uint8_t *apply_ctod(int, uint8_t *body)
{
    int32_t words[kCiaTodWords];
    for (unsigned i = 0; i < kCiaTodWords; ++i) words[i] = int32_t(Body{body, kCiaTodBytes}.u32(i * 4));
    return p6_custom_ciatod_set(words, kCiaTodWords) ? body + kCiaTodBytes : nullptr;
}

const char *check_ctod(const Sub &, const Body &b, Seen &)
{
    const int32_t count = int32_t(b.u32(0));
    if (count < 0 || count >= kCiaTodMax) return "the count to the next tick of the mains TOD is outside what a period can leave";
    // The rate is a frame rate of the profile's beam (PAL or NTSC, short, long or interlaced; 10 to 300 Hz is what init_hz accepts): a float of that range, not a NaN or an infinity.
    const uint32_t bits = b.u32(4);
    float rate;
    memcpy(&rate, &bits, sizeof rate);
    if (!(rate >= 10.0f && rate <= 300.0f)) return "the frame rate of the mains TOD is not a rate the engine can have";
    return nullptr;
}

// -- COPR: the Copper between two of its cycles (patch 0095) -------------------------------------------------------------------------------------

// The words of the body, in the order of p6_custom_copper_get.
enum CopperWord : unsigned {
    kCopState, kCopStatePrev, kCopStrobeNext, kCopStrobeIp, kCopVblankIp, kCopIgnoreNext, kCopVcmp, kCopHcmp, kCopStrobe, kCopStrobeType, kCopEnabledThisLine,
    kCopStrobeCyclesLow, kCopStrobeCyclesHigh, kCopDmaChangeLow, kCopDmaChangeHigh, kCopperWords
};
constexpr uint32_t kCopperBytes = kCopperWords * 4;
constexpr int32_t kCopStates = 24;             // COP_stop .. COP_strobe_extra of custom.cpp (the patch asserts it)
constexpr int32_t kCopRead1 = 2, kCopRead2 = 3, kCopStrobeDelayStartOdd = 19, kCopStrobeDelay1 = 20;       // the only states strobe_next takes besides COP_stop (0)

uint8_t *produce_copr(int, size_t *len, uint8_t *w)
{
    int32_t words[kCopperWords];
    if (!p6_custom_copper_get(words, kCopperWords)) return nullptr;
    for (unsigned i = 0; i < kCopperWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kCopperBytes;
    return w;
}

uint8_t *apply_copr(int, uint8_t *body)
{
    int32_t words[kCopperWords];
    for (unsigned i = 0; i < kCopperWords; ++i) words[i] = int32_t(Body{body, kCopperBytes}.u32(i * 4));
    return p6_custom_copper_set(words, kCopperWords) ? body + kCopperBytes : nullptr;
}

// -- BLTP: the phase of the blitter that BLTX does not carry (patch 0097) -----------------------------------------------------------------------------

// The words of the body, in the order of p6_blitter_phase_get.
enum BlitterWord : unsigned {
    kBltQueued, kBltCountDone, kBltChangeLow, kBltChangeHigh, kBltDelayedUpdate, kBltDmaCount, kBltCycleCount,
    kBltAHold2, kBltBHold2, kBltCHold2, kBltBHold, kBltDDatL, kBltDPtL, kBltLinePixel2, kBltAPtPrev, kBltBPtPrev, kBltCPtPrev, kBltDPtPrev, kBlitterWords
};
constexpr uint32_t kBlitterBytes = kBlitterWords * 4;
int32_t g_blitter_phase[kBlitterWords];     // read by apply_bltp, put in the engine by finish_chipset once restore_blitter_finish has run

uint8_t *produce_bltp(int, size_t *len, uint8_t *w)
{
    int32_t words[kBlitterWords];
    if (!p6_blitter_phase_get(words, kBlitterWords)) return nullptr;
    for (unsigned i = 0; i < kBlitterWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kBlitterBytes;
    return w;
}

// Nothing reaches the engine here: restore_blitter_finish, which runs after the last body of the chunk, sets blitter_delayed_update and would overwrite it, so the words wait
// in g_blitter_phase and finish_chipset puts them in.
uint8_t *apply_bltp(int, uint8_t *body)
{
    for (unsigned i = 0; i < kBlitterWords; ++i) g_blitter_phase[i] = int32_t(Body{body, kBlitterBytes}.u32(i * 4));
    return body + kBlitterBytes;
}

// -- AUDP: the Paula that AUDn does not carry (patch 0099) -----------------------------------------------------------------------------------------

// The words of the body, in the order of p6_audio_phase_get: the global ones, then kAudChannelWords per channel.
enum AudioWord : unsigned { kAudNextSample, kAudPendingLow, kAudPendingHigh, kAudWorkToDo, kAudLedFilter, kAudFilters, kAudChannels = kAudFilters + 4 * 5 };
enum AudioChannelWord : unsigned {
    kAudFlags, kAudIrqCheck, kAudPeriod, kAudDat2, kAudPtx, kAudDmaOffActive, kAudDmaOffCpuCount, kAudDmaOffPc, kAudMinPerLoop,
    kAudCurrentSample, kAudLastSample, kAudNewSample, kAudSampleAccum, kAudSampleAccumTime, kAudAdkMask, kAudMixVol, kAudChannelWords
};
constexpr unsigned kAudioWords = kAudChannels + 4 * kAudChannelWords;
// The longest sample period of the profile, in the engine's cycle units: the faster colour clock (NTSC, 3579545 Hz) at the slower rate (44100 Hz). A bound of the
// configuration, not of the live machine: a new image validates before its audio exists.
constexpr float kAudSamplePeriodMax = 3579545.0f * CYCLE_UNIT / 44100.0f;
constexpr uint32_t kAudioBytes = kAudioWords * 4;
int32_t g_audio_phase[kAudioWords];     // read by apply_audp, put in the engine by finish_audio once restore_audio_finish has run

uint8_t *produce_audp(int, size_t *len, uint8_t *w)
{
    int32_t words[kAudioWords];
    const bool owned = p6_session::session.config.machine == p6_bridge::kCd32Machine;
    if (!(owned ? p6_cd::device_phase_get(words, kAudioWords) : p6_audio_phase_get(words, kAudioWords))) return nullptr;
    for (unsigned i = 0; i < kAudioWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kAudioBytes;
    return w;
}

// restore_audio_finish sets last_cycles, so the words wait in g_audio_phase and finish_audio puts them in, as BLTP does.
uint8_t *apply_audp(int, uint8_t *body)
{
    for (unsigned i = 0; i < kAudioWords; ++i) g_audio_phase[i] = int32_t(Body{body, kAudioBytes}.u32(i * 4));
    return body + kAudioBytes;
}

// Presentation phases are independent of Paula. Keep them out of AUDn/AUDP and
// hardware digests; their future is checked against the delivered PCM.
constexpr uint32_t kDriveSoundBytes = p6_drive_sound::kWords * 4;
uint32_t g_drive_sound[p6_drive_sound::kWords];
uint8_t *produce_drive_sound(int, size_t *len, uint8_t *out)
{
    uint32_t words[p6_drive_sound::kWords];
    if (!p6_drive_sound::get(words, p6_drive_sound::kWords)) return nullptr;
    for (unsigned i = 0; i < p6_drive_sound::kWords; ++i)
        for (unsigned b = 0; b < 4; ++b) out[4 * i + b] = uint8_t(words[i] >> (24 - 8 * b));
    *len = kDriveSoundBytes;
    return out;
}
uint8_t *apply_drive_sound(int, uint8_t *body)
{
    for (unsigned i = 0; i < p6_drive_sound::kWords; ++i)
        g_drive_sound[i] = Body{body, kDriveSoundBytes}.u32(4 * i);
    return body + kDriveSoundBytes;
}
const char *check_drive_sound_body(Body b)
{
    uint32_t words[p6_drive_sound::kWords];
    for (unsigned i = 0; i < p6_drive_sound::kWords; ++i) words[i] = b.u32(4 * i);
    return p6_drive_sound::valid(words, p6_drive_sound::kWords) ? nullptr : "invalid mechanical sound phase";
}

// -- DNSP: Denise's output pipeline (patch 0100) ---------------------------------------------------------------------------------------------------------

// The words of the body, in the order of p6_denise_pipe_get: dtbuf[2][4], then dtgbuf[2][4].
constexpr unsigned kDenisePipeWords = 16, kDenisePixels = 8;
constexpr uint32_t kDenisePipeBytes = kDenisePipeWords * 4;

uint8_t *produce_dnsp(int, size_t *len, uint8_t *w)
{
    int32_t words[kDenisePipeWords];
    if (!p6_denise_pipe_get(words, kDenisePipeWords)) return nullptr;
    for (unsigned i = 0; i < kDenisePipeWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kDenisePipeBytes;
    return w;
}

uint8_t *apply_dnsp(int, uint8_t *body)
{
    int32_t words[kDenisePipeWords];
    for (unsigned i = 0; i < kDenisePipeWords; ++i) words[i] = int32_t(Body{body, kDenisePipeBytes}.u32(i * 4));
    return p6_denise_pipe_set(words, kDenisePipeWords) ? body + kDenisePipeBytes : nullptr;
}

// -- DRNG: the ring through which Agnus hands Denise its data, on an AGA machine (patch 0109) -----------------------------------------------------------------

// 5 words of cursors (the write cursor, the first slot of the line, the line counter that tags the slots, the two slot counts of the line), then the 4096 slots of 22 bytes: the data
// word (64 bits, of which the writers of a 16 or 32-bit access leave the upper half as it was), the register, the line it was written in, the flags and the pointer. The ring is
// not empty at a boundary when the program writes a Denise register all the time (DET02 aga-fmode-toggle), and the line being drawn reads what a slot still holds from the revolution
// before, which the position of the cursor selects: so the ring and its cursors are state. Not on the OCS and ECS machines, whose ring is idle there (their layout did not change).
uint8_t *produce_drng(int, size_t *len, uint8_t *w)
{
    int32_t cursors[kRingCursors];
    if (!p6_denise_ring_cursors_get(cursors, kRingCursors)) return nullptr;
    uint8_t *p = w;
    const auto put = [&p](uint64_t v, unsigned bytes) { for (unsigned b = 0; b < bytes; ++b) *p++ = uint8_t(v >> (8 * (bytes - 1 - b))); };
    for (int32_t c : cursors) put(uint32_t(c), 4);
    for (uint32_t i = 0; i < kRingSlots; ++i) {
        const denise_rga &r = rga_denise[i];
        put(r.v64, 8);
        put(r.rga, 2);
        put(r.line, 4);
        put(r.flags, 4);
        put(r.pt, 4);
    }
    *len = size_t(p - w);
    return w;
}

uint8_t *apply_drng(int, uint8_t *body)
{
    const Body b{body, kRingBytes};
    int32_t cursors[kRingCursors];
    for (uint32_t i = 0; i < kRingCursors; ++i) cursors[i] = int32_t(b.u32(i * 4));
    if (!p6_denise_ring_cursors_set(cursors, kRingCursors)) return nullptr;
    uint32_t at = kRingCursors * 4;
    for (uint32_t i = 0; i < kRingSlots; ++i, at += kRingSlotBytes) {
        denise_rga &r = rga_denise[i];
        r.v64 = uint64_t(b.u32(at)) << 32 | b.u32(at + 4);
        r.rga = uint16_t(b.u16(at + 8));
        r.line = b.u32(at + 10);
        r.flags = b.u32(at + 14);
        r.pt = b.u32(at + 18);
    }
    return body + kRingBytes;
}

// The cursors are indexes into the ring; a slot names a register of the chipset (even, up to 0x1FE, which also marks an empty slot) and the flags are the eight bits of the sync and
// line-length latches (DENISE_RGA_FLAG_*).
const char *check_drng(const Sub &, const Body &b, Seen &)
{
    if (b.u32(0) >= kRingSlots || b.u32(4) >= kRingSlots) return "a cursor of the Denise ring is beyond its last slot";
    if (int32_t(b.u32(12)) < 0 || int32_t(b.u32(16)) < 0) return "a slot count of the Denise ring is negative";
    for (uint32_t i = 0, at = kRingCursors * 4; i < kRingSlots; ++i, at += kRingSlotBytes) {
        if (b.u16(at + 8) > 0x1fe || (b.u16(at + 8) & 1)) return "a slot of the Denise ring names a register the chipset does not have";
        if (b.u32(at + 14) > 0xff) return "a slot of the Denise ring has flags the latches do not have";
    }
    return nullptr;
}

// -- GEOM: the raster the machine has settled on (patch 0100) ------------------------------------------------------------------------------------------

// The words of the body, in the order of p6_frame_geometry_get.
enum GeometryWord : unsigned {
    kGeoHpos, kGeoVpos, kGeoHposShort, kGeoVposNom, kGeoHposTemp, kGeoVposTemp, kGeoTempChange,
    kGeoInWidth, kGeoInHeight, kGeoInWidth2, kGeoInHeight2, kGeoOutWidth, kGeoOutHeight, kGeoExtraWidth, kGeoExtraHeight, kGeoXOffset, kGeoYOffset, kGeoInXOffset, kGeoInYOffset,
    kGeoRefused, kGeoAllocWidth, kGeoAllocHeight,        // patch 0103: the mode the presentation refused as too large and the allocation of the last one it accepted
    kGeoDisplayRedraw, kGeoEraseNext, kGeoCenterErase, kGeoLocked,      // and the flags of the drawing: the requests to clear the buffer, and whether the frame being drawn holds it
    kGeometryWords
};
constexpr uint32_t kGeometryBytes = kGeometryWords * 4;

uint8_t *produce_geom(int, size_t *len, uint8_t *w)
{
    int32_t words[kGeometryWords];
    if (!p6_frame_geometry_get(words, kGeometryWords)) return nullptr;
    for (unsigned i = 0; i < kGeometryWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kGeometryBytes;
    return w;
}

uint8_t *apply_geom(int, uint8_t *body)
{
    int32_t words[kGeometryWords];
    for (unsigned i = 0; i < kGeometryWords; ++i) words[i] = int32_t(Body{body, kGeometryBytes}.u32(i * 4));
    return p6_frame_geometry_set(words, kGeometryWords) ? body + kGeometryBytes : nullptr;
}

// -- PIXL: the rows of the drawing buffer (patch 0103) -----------------------------------------------------------------------------------------------

// The size of the allocation the raster of the state has, which GEOM names (check_geom keeps it while it validates, applies or saves the body): the rows of PIXL are that many
// pixels wide and that many rows tall, so the state does not say it twice and cannot disagree with itself.
struct RasterSize {
    uint32_t width = 0, height = 0;
};
RasterSize g_raster;

// Pixel storage belongs to the presentation port (ports/portable/video_present.cpp); the engine only names it (p6_frame_raster). The renderer writes the rows it draws, so a unit in
// which it draws none (a frame that spans two units, as the long frame does; the field of an interlaced picture that is not the one drawn) hands over what the units before left in
// the buffer, and a load that did not put the rows back showed the picture of the abandoned history or the black of a machine that had not drawn yet.
//
// Every pixel the profile draws is one of the 4096 colours of the chipset in the identity gamma (alloc_colors64k: a nibble n is the byte 17 * n, the byte above them is zero), so a
// pixel travels as 16 bits, the 12 of the colour; any other word (a profile that gamma-corrects or blends would have them) travels whole after the marker 0xffff and costs 6 bytes
// instead of 2, which is what makes the bound below a bound of the profile and not of every pixel. A row is a list of groups until it has its width: a byte below 0x80 says that
// that many pixels plus one follow (at most 128), one from 0x80 says that the next pixel repeats that many times plus one (at most 128). Rows run in order, the width is fixed, and
// nothing counts bytes, so a state cannot lie about its own length.
constexpr uint32_t kRasterGroup = 128;
constexpr uint64_t kRasterBoundOcsEcs = uint64_t(P6_VIDEO_MAX_HEIGHT) * (uint64_t(P6_VIDEO_MAX_WIDTH) * 2 + (P6_VIDEO_MAX_WIDTH + kRasterGroup - 1) / kRasterGroup);
// With AGA (T24c) the colours are 24 bits and almost none is one of the 4096 of the 12-bit code, so the picture travels as 3 bytes per pixel (R, G, B) in the same groups.
constexpr uint64_t kRasterBoundAga = uint64_t(P6_VIDEO_MAX_HEIGHT) * (uint64_t(P6_VIDEO_MAX_WIDTH) * 3 + (P6_VIDEO_MAX_WIDTH + kRasterGroup - 1) / kRasterGroup);
constexpr uint16_t kRasterRaw = 0xffff;

// The machine decides the code of the pixels (the identity carries the machine, so the code is never a choice of the state): AGA machines use the 24-bit one.
bool aga_machine() { return (currprefs.chipset_mask & CSMASK_AGA) != 0; }
uint64_t raster_bound() { return aga_machine() ? kRasterBoundAga : kRasterBoundOcsEcs; }

bool colour_code(uint32_t pixel, uint16_t *code)
{
    const uint32_t r = pixel >> 16 & 0xff, g = pixel >> 8 & 0xff, b = pixel & 0xff;
    if (pixel >> 24 || r % 17 || g % 17 || b % 17) return false;
    *code = uint16_t(r / 17 << 8 | g / 17 << 4 | b / 17);
    return true;
}

uint32_t colour_pixel(uint16_t code)
{
    return uint32_t(code >> 8) * 17 << 16 | uint32_t(code >> 4 & 15) * 17 << 8 | uint32_t(code & 15) * 17;
}

void fail_in(Archive &a, const char *tag, const char *why);

// The bytes the encoder writes, to the archive or to a count: one function describes the groups, so the length the body announces is the one it has.
struct CountSink {
    uint64_t bytes = 0;
    bool unencodable = false;       // a pixel with bits above the 24 of the colour on an AGA machine
    void put8(uint8_t) { bytes += 1; }
    void put16(uint16_t) { bytes += 2; }
    void put32(uint32_t) { bytes += 4; }
    void put_rgb(uint32_t pixel)
    {
        bytes += 3;
        if (pixel >> 24) unencodable = true;
    }
};
struct ArchiveSink {
    Archive &a;
    void put8(uint8_t v) { a.val<uint8_t>(v, "video.group"); }
    void put16(uint16_t v) { a.val<uint16_t>(v, "video.pixel"); }
    void put32(uint32_t v) { a.val<uint32_t>(v, "video.pixel_word"); }
    void put_rgb(uint32_t pixel)
    {
        a.val<uint8_t>(uint8_t(pixel >> 16), "video.pixel_r");
        a.val<uint8_t>(uint8_t(pixel >> 8), "video.pixel_g");
        a.val<uint8_t>(uint8_t(pixel), "video.pixel_b");
    }
};

template <class Sink> void put_pixel(Sink &sink, uint32_t pixel, bool wide)
{
    uint16_t code;
    if (wide) {
        sink.put_rgb(pixel);
    } else if (colour_code(pixel, &code)) {
        sink.put16(code);
    } else {
        sink.put16(kRasterRaw);
        sink.put32(pixel);
    }
}

template <class Sink> void encode_raster(Sink &sink, const uint8_t *first, int stride, uint32_t width, uint32_t height, bool wide)
{
    for (uint32_t y = 0; y < height; ++y) {
        const uint32_t *pixels = reinterpret_cast<const uint32_t *>(first + size_t(y) * size_t(stride));
        uint32_t x = 0;
        while (x < width) {
            uint32_t run = 1;
            while (x + run < width && run < kRasterGroup && pixels[x + run] == pixels[x]) ++run;
            if (run >= 3) {
                sink.put8(uint8_t(0x80 | (run - 1)));
                put_pixel(sink, pixels[x], wide);
                x += run;
                continue;
            }
            uint32_t count = 0;      // literals up to the next run of three, which is at least this pixel: a run shorter than three would cost more than the literals
            while (x + count < width && count < kRasterGroup &&
                   !(x + count + 2 < width && pixels[x + count] == pixels[x + count + 1] && pixels[x + count] == pixels[x + count + 2]))
                ++count;
            sink.put8(uint8_t(count - 1));
            for (uint32_t i = 0; i < count; ++i) put_pixel(sink, pixels[x + i], wide);
            x += count;
        }
    }
}

uint32_t get_pixel(Archive &a, bool wide)
{
    if (wide) {
        const uint32_t r = a.val<uint8_t>(0, "video.pixel_r"), g = a.val<uint8_t>(0, "video.pixel_g"), b = a.val<uint8_t>(0, "video.pixel_b");
        return r << 16 | g << 8 | b;
    }
    const uint16_t code = a.val<uint16_t>(0, "video.pixel");
    if (code < 0x1000) return colour_pixel(code);
    if (code == kRasterRaw) return a.val<uint32_t>(0, "video.pixel_word");
    a.fail("a pixel of the picture is neither one of the 4096 colours nor a whole word");
    return 0;
}

// The body is tagged and measured like the others (tag, length, bytes), and the length is checked against what the groups use.
void visit_raster(Archive &a)
{
    static const char kTag[4] = {'P', 'I', 'X', 'L'};
    if (a.mode() == Mode::Measure) {
        a.bytes(const_cast<char *>(kTag), 4, "PIXL");
        a.val<uint32_t>(0, "video.raster_length");
        a.bytes(nullptr, raster_bound(), "video.raster");
        return;
    }
    int live_width = 0, live_height = 0, stride = 0;
    uint8_t *first = nullptr;
    const bool have = a.mode() != Mode::Validate;
    if (have && !p6_frame_raster(&live_width, &live_height, &stride, &first)) {
        a.fail("PIXL: the engine draws into a buffer that is not the presentation's, or has none");
        return;
    }
    const uint32_t width = g_raster.width, height = g_raster.height;
    if (have) a.require(uint32_t(live_width) == width && uint32_t(live_height) == height, "PIXL: the picture is not the size of the buffer the machine has");
    if (!a.ok()) return;
    const bool wide = aga_machine();
    if (a.mode() == Mode::Save) {
        CountSink count;
        encode_raster(count, first, stride, width, height, wide);
        if (count.unencodable) {
            a.fail("PIXL: a pixel of the picture has bits above the 24 of the colour, which no AGA state carries");
            return;
        }
        a.bytes(const_cast<char *>(kTag), 4, "PIXL");
        a.val<uint32_t>(uint32_t(count.bytes), "video.raster_length");
        ArchiveSink sink{a};
        encode_raster(sink, first, stride, width, height, wide);
        return;
    }
    char tag[4];
    for (char &c : tag) c = char(a.val<uint8_t>(0, "video.raster_tag"));
    if (a.ok() && memcmp(tag, kTag, 4)) fail_in(a, "PIXL", "another body, or this one out of its place");
    const uint32_t length = a.val<uint32_t>(0, "video.raster_length");
    if (a.ok() && length > raster_bound()) fail_in(a, "PIXL", "the length is beyond the largest picture the presentation can hold");
    if (!a.ok()) return;
    const uint64_t start = a.position();
    const auto row = [&](uint32_t y) { return reinterpret_cast<uint32_t *>(first + size_t(y) * size_t(stride)); };
    for (uint32_t y = 0; y < height && a.ok(); ++y) {
        uint32_t x = 0;
        while (x < width && a.ok()) {
            const uint8_t token = a.val<uint8_t>(0, "video.group");
            const uint32_t count = (token & 0x7fu) + 1;
            if (count > width - x) {
                fail_in(a, "PIXL", "a group of pixels runs past the end of its row");
                return;
            }
            if (token & 0x80) {
                const uint32_t pixel = get_pixel(a, wide);
                if (a.applying() && a.ok())
                    for (uint32_t i = 0; i < count; ++i) row(y)[x + i] = pixel;
            } else {
                for (uint32_t i = 0; i < count && a.ok(); ++i) {
                    const uint32_t pixel = get_pixel(a, wide);
                    if (a.applying() && a.ok()) row(y)[x + i] = pixel;
                }
            }
            x += count;
        }
    }
    if (a.ok() && a.position() - start != length) fail_in(a, "PIXL", "the rows do not use the bytes the length says");
}

// -- DSKX: the floppy controller that DISK, DSKn and DSDn do not carry (patch 0101) ------------------------------------------------------------------------

// The words of the body, in the order of p6_disk_phase_get: the global ones, then kDiskDriveWords per drive.
enum DiskWord : unsigned { kDkHpos, kDkLen2, kDkSyncOn, kDkIndexDecay, kDkSelect, kDkLineCounter, kDkDrives, kDkDriveWords = 14 };
enum DiskDriveWord : unsigned { kDkChangeTime, kDkMotorDelay, kDkStepLimit, kDkStepAge, kDkBitCounter, kDkTrackLen, kDkTrackSpeed, kDkSkipOffset, kDkIndexOffset, kDkPrevTrackLen, kDkMultiRevolution, kDkRevolutionCheck, kDkFdiRevolution, kDkFdiTrack };
constexpr unsigned kDiskWords = kDkDrives + 4 * kDkDriveWords;
constexpr uint32_t kDiskPhaseBytes = kDiskWords * 4;
// The longest delay disk_insert_2 and DISK_check_change arm (2 * 50 * 312 lines), and one more line for each drive that setdskchangetime staggers it behind; the longest line
// the beam draws is 256 colour clocks of 256 units; the step limiter counts four lines.
constexpr int32_t kDiskChangeMax = 2 * 50 * 312 + 4, kDiskLineUnits = 256 << 8, kDiskStepLimitMax = 4;
bool g_disk_pending[4];     // set by apply_dskx, read by rebuild_disk once P6BR has said which medium each drive holds

uint8_t *produce_dskx(int, size_t *len, uint8_t *w)
{
    int32_t words[kDiskWords];
    if (!p6_disk_phase_get(words, kDiskWords)) return nullptr;
    for (unsigned i = 0; i < kDiskWords; ++i)
        for (unsigned b = 0; b < 4; ++b) w[i * 4 + b] = uint8_t(uint32_t(words[i]) >> (24 - 8 * b));
    *len = kDiskPhaseBytes;
    return w;
}

uint8_t *apply_dskx(int, uint8_t *body)
{
    int32_t words[kDiskWords];
    for (unsigned i = 0; i < kDiskWords; ++i) words[i] = int32_t(Body{body, kDiskPhaseBytes}.u32(i * 4));
    if (!p6_disk_phase_set(words, kDiskWords)) return nullptr;
    for (unsigned n = 0; n < 4; ++n) g_disk_pending[n] = words[kDkDrives + n * kDkDriveWords + kDkChangeTime] > 0;
    return body + kDiskPhaseBytes;
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
    seen.drive_crc[n] = b.u32(12);
    return nullptr;
}

// DSDn: mode u32 (1, or 3 with the timing of every byte of the track after the words), bit counter u16, track length u32, speed u16, skip offset u32, index offset u32, the words, the timing, revolutions u16.
const char *check_dsd(const Sub &sub, const Body &b, Seen &seen)
{
    if (!b.n) return nullptr;
    const int n = sub.index;
    if ((seen.drive_state[n] & 3) != 1) return "a buffered track for a drive that is off or does not exist";
    if (b.n < kDsdFixed) return "shorter than its own header";
    const uint32_t mode = b.u32(0), length = b.u32(6);
    if (mode != 1 && mode != 3) return "the mode is neither 1 nor 3";
    if (!length || length > kMfmBitsMax) return "the track length is outside the buffer";
    if (b.n != kDsdFixed + (length + 15) / 16 * 2 + (mode == 3 ? (length + 7) / 8 * 2 : 0)) return "the length does not follow from the track length in its header";
    if (seen.head_position[n] >= length) return "the head position of the drive is beyond the end of the track";
    seen.track[n] = {true, b.u16(4), length, b.u16(10), b.u32(12), b.u32(16)};
    return nullptr;
}

// DISK: word u16, bit offset u8, sync found u8, hpos u8, DMA state u8, 3 x (word u16, in use u8), length u16.
const char *check_disk(const Sub &, const Body &b, Seen &seen)
{
    seen.disk_hpos = uint8_t(b.u8(4));
    seen.disk_dma = uint8_t(b.u8(5));
    seen.disk_length = uint16_t(b.u16(15));
    if (b.u8(2) > 15) return "the bit offset is beyond 15";
    if (b.u8(3) > 1) return "the sync flag is not a flag";
    if (b.u8(5) != 0 && b.u8(5) != 2 && b.u8(5) != 3) return "the DMA state is not off, read or write";
    if (b.u8(8) > 2 || b.u8(11) > 2 || b.u8(14) > 2) return "a FIFO slot is in use in a way the controller never leaves it";
    // 0xffff is the -1 that a turbo read leaves (DSKLEN_2 counts the transfer down in one go); DSKX says whether the two lines of that transfer are what this state is in.
    if (b.u16(15) > 0x3fff && b.u16(15) != 0xffff) return "the DMA length is beyond 16383 words";
    return nullptr;
}

// DSKX: the words are counters and flags of the controller. The position within the line has the fraction DISK carries as its low byte and is less than a line; DSKLEN2 is a
// 16-bit register; the countdown of a delayed insert runs only in a drive that exists and holds no medium (disk_insert_2 ejects first), and the step limiter has an age
// only while it counts.
const char *check_dskx(const Sub &, const Body &b, Seen &seen)
{
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    if (at(kDkHpos) < 0 || at(kDkHpos) >= kDiskLineUnits) return "the position of the floppy controller in its line is not inside a line";
    if (uint8_t(at(kDkHpos)) != seen.disk_hpos) return "the position of the floppy controller in its line is not the fraction DISK carries";
    if (at(kDkLen2) < 0 || at(kDkLen2) > 0xffff) return "the previous DSKLEN is wider than the register";
    if (at(kDkSyncOn) < 0 || at(kDkSyncOn) > 1) return "the wordsync flag is not a flag";
    if (at(kDkIndexDecay) < 0 || at(kDkIndexDecay) > 2) return "the index pulse decay is beyond 2 lines";
    if (at(kDkSelect) < 0 || at(kDkSelect) > 0xff) return "the last write to the drive select lines is wider than the port";
    // The turbo floppy (T62) completes a transfer at once and counts two lines before the DSKBLK interrupt: in them the DMA is off, and a read leaves its length at -1.
    if (at(kDkLineCounter) < 0 || at(kDkLineCounter) > 2) return "the turbo transfer counter is beyond the two lines it counts";
    if (at(kDkLineCounter) && seen.disk_dma) return "a turbo transfer is counting down while the DMA of the disk is on";
    if (seen.disk_length == 0xffff && !at(kDkLineCounter)) return "the DMA length is the one a turbo read leaves, and no turbo transfer is counting down";
    for (unsigned n = 0; n < 4; ++n)        // the side is derived from that write (bit 2 of the port, active low), and every DSKn carries the one global side
        if (((seen.drive_state[n] & 16) != 0) != ((at(kDkSelect) & 4) == 0)) return "the side of a drive is not the one of the last write to the drive select lines";
    for (unsigned n = 0; n < 4; ++n) {
        const auto dk = [&at, n](unsigned word) { return at(kDkDrives + n * kDkDriveWords + word); };
        if (dk(kDkChangeTime) < 0 || dk(kDkChangeTime) > kDiskChangeMax) return "the delay of a disk change is beyond the longest one the engine arms";
        if (dk(kDkChangeTime) && ((seen.drive_state[n] & 2) || seen.drive_crc[n])) return "a disk change is counting down in a drive that is absent or holds a disk";
        if (dk(kDkMotorDelay) < 0 || dk(kDkMotorDelay) > 1) return "the motor delay of a drive is not a flag";
        if (dk(kDkStepLimit) < 0 || dk(kDkStepLimit) > kDiskStepLimitMax) return "the step limiter of a drive is beyond four lines";
        if (dk(kDkStepAge) < 0 || (!dk(kDkStepLimit) && dk(kDkStepAge))) return "a drive has the age of a step it is not limiting";
        if (dk(kDkBitCounter) < 0 || dk(kDkBitCounter) > 0xffff || dk(kDkTrackSpeed) < 0 || dk(kDkTrackSpeed) > 0xffff) return "the bit counter or the speed of a track is wider than DSDn writes it";
        if (dk(kDkTrackLen) < 0 || uint32_t(dk(kDkTrackLen)) > kMfmBitsMax) return "the track length is outside the buffer";
        if (dk(kDkSkipOffset) < -1 || dk(kDkSkipOffset) > int32_t(kMfmBitsMax) || dk(kDkIndexOffset) < 0 || dk(kDkIndexOffset) > int32_t(kMfmBitsMax))      // -1: no skip position
            return "an offset in the track is outside the buffer";
        if (dk(kDkPrevTrackLen) < 0 || uint32_t(dk(kDkPrevTrackLen)) > kMfmBitsMax) return "the length the head position was last scaled to is outside the buffer";
        if (dk(kDkMultiRevolution) < 0 || dk(kDkMultiRevolution) > 1) return "the several-revolutions flag of a track is not a flag";
        if (dk(kDkRevolutionCheck) < 0 || dk(kDkRevolutionCheck) > 2) return "the revolution check of a drive is beyond the two lines it counts";
        if (dk(kDkFdiRevolution) < 0 || dk(kDkFdiRevolution) > (1 << 24)) return "the revolution counter of an FDI track is beyond what a session can count";
        if (dk(kDkFdiTrack) < -1 || dk(kDkFdiTrack) >= 166) return "the FDI track a revolution counter belongs to is beyond the 166 tracks of a drive";
        const auto &track = seen.track[n];      // a drive that has a track buffered has these from DSDn: the two bodies describe one track
        if (track.present && (uint32_t(dk(kDkBitCounter)) != track.bit_counter || uint32_t(dk(kDkTrackLen)) != track.track_length || uint32_t(dk(kDkTrackSpeed)) != track.speed ||
                              uint32_t(dk(kDkSkipOffset)) != track.skip_offset || uint32_t(dk(kDkIndexOffset)) != track.index_offset))
            return "the track of a drive is not the one its buffer describes";
    }
    return nullptr;
}

// CHIP: the chipset flags u32, then the register image. The chipset and the video standard are the configuration; FMODE is zero without AGA and, with it, the bits the register
// has (the two fetch modes and the two scan-doubling flags: FMODE() masks the write with 0xC00F).
const char *check_chip(const Sub &, const Body &b, Seen &)
{
    if (b.u32(0) != (uint32_t(currprefs.chipset_mask) | 0x100)) return "the chipset is not the one of this configuration";
    if (b.u16(350) != (0x8000u | (currprefs.ntscmode ? 1u : 0u))) return "the video standard is not the one of this configuration";
    if (b.u16(352) & ~(aga_machine() ? 0xC00Fu : 0u)) return aga_machine() ? "FMODE has a bit the register does not" : "FMODE is set in a chipset that has none";
    return nullptr;
}

// CAGA: 256 colours, each 24 bits and the genlock bit in bit 31 (save_custom_agacolors).
const char *check_caga(const Sub &, const Body &b, Seen &)
{
    for (uint32_t at = 0; at < 256 * 4; at += 4)
        if (b.u32(at) & 0x7f000000) return "a colour of the AGA palette has bits between the 24 of the colour and the genlock bit";
    return nullptr;
}

// BLTX: the cycle-exact byte, the state, ... the channel mask blit_ch at 46 (BLTCON0's USEA..USED), blitfc and blitife at 51 and 52 (they index blit_filltable),
// the sentinel 0x1234 at 84 and 0x1235 at 130 (restore_blitter_new carries on past a wrong one).
const char *check_bltx(const Sub &, const Body &b, Seen &seen)
{
    if (b.u8(0) != (currprefs.blitter_cycle_exact ? 3u : 0u)) return "the blitter is not the one of this configuration";
    if (b.u8(1) > 2) return "the state of the blitter is not idle, pending or running";
    if (b.u8(46) > 15) return "the channel mask of the blit is beyond four bits";
    if (b.u8(51) > 1 || b.u8(52) > 1) return "a fill flag of the blit is not a flag";
    if (b.u16(84) != 0x1234 || b.u16(130) != 0x1235) return "a sentinel of the body is not where it must be";
    seen.bltx_state = uint8_t(b.u8(1));
    seen.bltx_dmacount = uint8_t(b.u8(47));
    seen.bltx_cyclecount = uint8_t(b.u8(48));
    return nullptr;
}

// BLIT: the flags u32 that restore_blitter reads (save_blitter writes 8|16, or 1|4|8|16 for an idle blitter).
const char *check_blit(const Sub &, const Body &b, Seen &)
{
    const uint32_t flags = b.u32(0);
    if (flags != (8 | 16) && flags != (1 | 4 | 8 | 16)) return "the flags are not the ones save_blitter writes";
    return nullptr;
}

// CHSL: flags u32 (1|8, plus 2 or 4 for a copper in its last move), the copper's pointer and two words, 8 zero words, 4 slots of 54 bytes (patch 0096: upstream saves the three it counts and the pipeline has four), the first offset u8.
const char *check_chsl(const Sub &, const Body &b, Seen &seen)
{
    const uint32_t flags = b.u32(0);
    if (flags != 9 && flags != 11 && flags != 13) return "the flags are not the ones save_custom_slots writes";
    for (uint32_t at = 12; at < 28; at += 2)
        if (b.u16(at)) return "a word that is always zero is not";
    for (uint32_t slot = 0; slot < 4; ++slot) {
        const uint32_t base = 28 + slot * 54;
        if (b.u16(base + 4) > 0x1fe) return "a slot names a register beyond 0x1FE";
        for (uint32_t at = base + 50; at <= base + 52; at += 2) {       // the registers of the pointers: getptfromreg takes anything else for a dummy
            const unsigned reg = b.u16(at);
            const bool pointer = reg == 0xFFFF || reg == 0x000 || reg == 0x070 || reg == 0x072 || reg == 0x074 || (reg >= 0x110 && reg <= 0x11a && !(reg & 1)) ||
                                 (reg >= 0x140 && reg <= 0x14e && !(reg & 1)) || reg == 0x08c || (reg >= 0x200 && reg <= 0x205);      // 0x200-0x205: patch 0092
            if (!pointer) return "a slot points at a register the engine has no pointer for";
        }
    }
    if (b.u8(244) > 3) return "the first slot offset is beyond 3";
    seen.chsl_flags = flags;
    return nullptr;
}

// SPRn: pointer u32, position u16, control u16, 8 data words, flags u8 (armed, fetch mode).
const char *check_spr(const Sub &, const Body &b, Seen &)
{
    if (b.u8(24) & 0xF8) return "unknown bits in the sprite flags";
    return nullptr;
}

// AUDn: state u8, volume u8, interrupt flag u8, flags u8 (1 request, 2 request pending, 0x80 always), length, wlength, period, data u16, lc, pt, evtime u32, hpos u8.
// The state is 0 to 5, or 0x13: a channel in state 3 whose DMA has been switched off (loadperm1) goes on with a "manual period" and marks the state with 0x10 until it reaches 2 (T24c: the
// tracker-style loop of DET02 audio-dma-wait switches the DMA off while a sample plays, which the 68000 scenarios never did; the engine's own restore_audio takes the byte as it is).
const char *check_aud(const Sub &sub, const Body &b, Seen &seen)
{
    if (b.u8(0) > 5 && b.u8(0) != 0x13) return "the state of the channel is beyond 5 and is not the 0x13 of a channel whose DMA was switched off";
    if (b.u8(2) > 1) return "the interrupt flag is not a flag";
    if ((b.u8(3) & 0xFC) != 0x80) return "the flags of the channel are not the ones save_audio writes";
    seen.aud_period[sub.index] = uint16_t(b.u16(8));
    return nullptr;
}

// AUDP: the floats are what the engine casts to integers (the resampler phase goes through floorf into an unsigned, the filters into the output sample), so they are
// finite, and the phase is in the range update_audio leaves it in between two calls: never below -0.5 and never more than the longest sample period ahead. The cycles not yet
// integrated are not negative. The flags, the interrupt check (-1, 0, 1), the masks (all or nothing) and the 16 and 8-bit latches have the range of their variables, and
// the exact period has to be the one AUDn rounds (PERIOD_MAX and PERIOD_MAX - 1, -1 and -2 as words, both save as 0): the two bodies are one channel.
const char *check_audp(const Sub &, const Body &b, Seen &seen)
{
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    const auto real = [&b](unsigned word) { const uint32_t bits = b.u32(word * 4); float f; memcpy(&f, &bits, sizeof f); return f; };
    const float phase = real(kAudNextSample);
    if (!isfinite(phase) || phase < -0.5f || phase > kAudSamplePeriodMax + 1.0f) return "the resampler phase is not between two samples";
    if (at(kAudPendingHigh) < 0) return "the cycles Paula has not integrated are negative";      // 64 bits: a boundary has 0, a partial load (--only) a long stretch
    if (at(kAudWorkToDo) < 0) return "the activity counter of the audio is negative";
    if (at(kAudLedFilter) < 0 || at(kAudLedFilter) > 1) return "the LED filter is not a flag";
    for (unsigned w = kAudFilters; w < kAudChannels; ++w)
        if (!isfinite(real(w))) return "a filter stage is not a finite number";
    for (unsigned n = 0; n < 4; ++n) {
        const auto ch = [&at, n](unsigned word) { return at(kAudChannels + n * kAudChannelWords + word); };
        if (ch(kAudFlags) & ~31) return "unknown bits in the flags of a channel";
        if (ch(kAudIrqCheck) < -1 || ch(kAudIrqCheck) > 1) return "the interrupt check of a channel is not -1, 0 or 1";
        if (ch(kAudDmaOffActive) < 0 || ch(kAudDmaOffActive) > 1) return "the DMA-off latch of a channel is not a flag";
        if (ch(kAudDat2) < 0 || ch(kAudDat2) > 0xffff || ch(kAudNewSample) < 0 || ch(kAudNewSample) > 0xff) return "a data latch of a channel is wider than its register";
        if (ch(kAudAdkMask) != 0 && ch(kAudAdkMask) != -1) return "the modulation mask of a channel is neither all nor nothing";
        if (ch(kAudMixVol) < 0 || ch(kAudMixVol) > 64) return "the volume the mixer uses is not a volume";
        const int32_t period = ch(kAudPeriod);
        const uint16_t rounded = period == -1 ? 0 : uint16_t(period / CYCLE_UNIT);       // save_audio's own arithmetic: int division
        if (rounded != seen.aud_period[n]) return "the exact period of a channel is not the one AUDn rounds";
    }
    return nullptr;
}

// BPLX: the version, then the bitplane data registers.
const char *check_bplx(const Sub &, const Body &b, Seen &)
{
    if (b.u32(0) != 1) return "the version of the body is not 1";
    return nullptr;
}

// DNSP: the pixels are colours of the engine's own table (any 32 bits), and the pixel numbers of the genlock buffer are bytes.
const char *check_dnsp(const Sub &, const Body &b, Seen &)
{
    for (unsigned w = kDenisePixels; w < kDenisePipeWords; ++w)
        if (b.u32(w * 4) > 0xff) return "a pixel number of the genlock buffer is wider than a byte";
    return nullptr;
}

// GEOM: the adopted counts are lengths of a raster, not negative and within 16 bits. The sizes of the picture index the drawing buffer (inwidth is the length of the line the
// engine clears, inheight the last row it writes; the presentation refuses only an out size that does not fit), so they are bounded by the buffer the port allocates,
// P6_VIDEO_MAX_WIDTH x P6_VIDEO_MAX_HEIGHT. The offsets and the extra width and height are signed distances the engine range-checks where it uses them, and the countdown of
// the candidate is the two frames the engine gives it.
const char *check_geom(const Sub &, const Body &b, Seen &)
{
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    for (unsigned w : {kGeoHpos, kGeoVpos, kGeoHposShort, kGeoVposNom, kGeoHposTemp, kGeoVposTemp})
        if (at(w) < 0 || at(w) > 0xffff) return "a size of the raster is negative or beyond 16 bits";
    for (unsigned w : {kGeoInWidth, kGeoInWidth2, kGeoOutWidth})
        if (at(w) < 0 || at(w) > P6_VIDEO_MAX_WIDTH) return "a width of the picture is negative or wider than the drawing buffer";
    for (unsigned w : {kGeoInHeight, kGeoInHeight2, kGeoOutHeight})
        if (at(w) < 0 || at(w) > P6_VIDEO_MAX_HEIGHT) return "a height of the picture is negative or taller than the drawing buffer";
    for (unsigned w : {kGeoExtraWidth, kGeoExtraHeight, kGeoXOffset, kGeoYOffset, kGeoInXOffset, kGeoInYOffset})
        if (at(w) < -0xffff || at(w) > 0xffff) return "an offset of the raster is beyond 16 bits";
    for (unsigned w : {kGeoRefused, kGeoDisplayRedraw, kGeoEraseNext, kGeoCenterErase, kGeoLocked})
        if (at(w) < 0 || at(w) > 1) return "a flag of the presentation is not a flag";
    if (at(kGeoAllocWidth) < 1 || at(kGeoAllocWidth) > P6_VIDEO_MAX_WIDTH || at(kGeoAllocHeight) < 1 || at(kGeoAllocHeight) > P6_VIDEO_MAX_HEIGHT)
        return "the allocation of the drawing buffer is empty or larger than the presentation buffer";
    g_raster = {uint32_t(at(kGeoAllocWidth)), uint32_t(at(kGeoAllocHeight))};
    if (at(kGeoTempChange) < 0 || at(kGeoTempChange) > 2) return "the countdown of the candidate raster is not between 0 and 2";
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
    for (unsigned w = kLatches; w < kPlfFirstLine; ++w)
        if (!within(w, 0, 1)) return "a sync latch is not a flag";
    if (!within(kPlfFirstLine, 0, 0xfff) || !within(kPlfLastLine, 0, 0xfff)) return "a line of the vertical display window is beyond what DIWSTRT, DIWSTOP and DIWHIGH can name";
    if (!within(kNeedVdiwCheck, 0, 1)) return "the flag that tests the vertical display window is not a flag";
    if (!within(kInterlaceSeen, 0, 1)) return "the interlace the display has seen is not a flag";
    if (!within(kProgrammedH, 0, 1) || !within(kProgrammedV, 0, 1)) return "a flag of the programmed beam registers is not a flag";
    if (!within(kNoSignalStatus, -1, 2) || !within(kNoSignalTrigger, 0, 1) || !within(kSyncsStopped, 0, 1)) return "the no-signal status is not one of -1 (ending), 0, 1 and 2, or its request or the stopped syncs are not flags";
    if (!within(kNoSignalCount, 0, 0xffff) || !within(kLinesAfterBeamcon, 0, 5)) return "a count of vertical blanks of the no-signal state is beyond what the engine sets";
    if (!within(kBprun, 0, 3) || !within(kDdfStopping, -1, 2) || !within(kBprunCycle, 0, 0xffff)) return "the bitplane sequencer is not in one of its states (BPRUN 0 to 3, DDF passed -1 to 2, a cycle count of its fetch)";
    if (!within(kDdfEnableOn, 0, 1) || !within(kDdfLimitIn, 0, 1) || !within(kDdfLimit, 0, 1) || !within(kDdfStrtMatch, 0, 1) || !within(kHwiOld, 0, 1)) return "a latch of the bitplane sequencer is not a flag";
    if (!within(kDdfStrtVal, 0, 0xff)) return "the colour clock at which a fetch may start is beyond the line";
    for (unsigned slot = 0; slot < 4; ++slot)
        if (!within(kRgaBltadd + slot, -2, 2) || (at(kRgaBltadd + slot) & 1)) return "the pointer increment of a slot of the DMA pipeline is not one the blitter sets (-2, 0 or 2)";
    if (at(kLastHsyncHigh) < 0 || at(kLastVsyncHigh) < 0) return "a time stamp of the display is negative";
    return nullptr;
}

// COPR: the state numbers index the Copper's switch and the compare values are what a WAIT or SKIP makes of its two words (vcmp is the line of the first word masked by the
// second, hcmp the colour clock, even), so they have the range of those words; the pointers are the even addresses the registers latch (the strobe pointer is also 0xffffffff
// while none is armed) and the engine masks them to the chip RAM it reads; the stamps are cycle counts and have no bound. CHSL says whether the Copper is in the last cycles of
// a MOVE, and COPR has to say the same: the two bodies are one machine.
const char *check_copr(const Sub &, const Body &b, Seen &seen)
{
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    const auto within = [&at](unsigned word, int low, int high) { return at(word) >= low && at(word) <= high; };
    if (!within(kCopState, 0, kCopStates - 1) || !within(kCopStatePrev, 0, kCopStates - 1)) return "a state of the Copper is not one of its states";
    const int32_t next = at(kCopStrobeNext);
    if (next != 0 && next != kCopStrobeDelayStartOdd && next != kCopStrobeDelay1) return "the state after the strobe is not one the strobe leads to";
    if (((seen.chsl_flags & 2) != 0) != (at(kCopState) == kCopRead1) || ((seen.chsl_flags & 4) != 0) != (at(kCopState) == kCopRead2))
        return "the state of the Copper is not the one CHSL says (last cycles of a MOVE)";
    if ((b.u32(kCopStrobeIp * 4) & 1) && b.u32(kCopStrobeIp * 4) != 0xffffffffu) return "the strobe pointer is odd";
    if (b.u32(kCopVblankIp * 4) & 1) return "the pointer latched at the vertical blank is odd";
    if (!within(kCopIgnoreNext, -1, 1)) return "the flag that skips the next register write is not -1, 0 or 1";
    if (!within(kCopVcmp, 0, 255) || !within(kCopHcmp, 0, 254) || (at(kCopHcmp) & 1)) return "a compare value of the Copper is beyond what a WAIT writes";
    if (!within(kCopStrobe, 0, 3) || !within(kCopStrobeType, -1, 2)) return "the strobe of the Copper is out of range";
    if (!within(kCopEnabledThisLine, 0, 1)) return "the flag that lets the Copper run this line is not a flag";
    return nullptr;
}

// BLTP: the queue holds at most the pipeline depth (BLITTER_MAX_PIPELINED_CYCLES, 4) and the counts of channels are what blit_bltset makes of four channels and two skips; the
// held words are 16 bits and the pending flag is a flag. BLTX says whether a blit is running, and BLTP has to agree: a running blit has not counted itself done and has its
// pipeline queued (the OCS blitter clears the running flag in the cycle that counts it done), and the two channel counts BLTX carries are the ones BLTP carries. The count of
// the cycles after the end is incremented while a bit is still in the four-stage shifter and has no other bound.
const char *check_bltp(const Sub &, const Body &b, Seen &seen)
{
    const auto at = [&b](unsigned word) { return int32_t(b.u32(word * 4)); };
    const auto within = [&at](unsigned word, int low, int high) { return at(word) >= low && at(word) <= high; };
    if (!within(kBltQueued, 0, 4)) return "the blitter queue is deeper than its pipeline";
    if (!within(kBltCountDone, 0, 15)) return "the count of cycles after the end of a blit is beyond what the shifter can hold";
    if (seen.bltx_state == 2 && (at(kBltCountDone) != 0 || at(kBltQueued) == 0)) return "BLTX says a blit is running and BLTP says it is done or has nothing queued";
    if (!within(kBltDelayedUpdate, 0, 1) || !within(kBltLinePixel2, 0, 1)) return "a flag of the blitter is not a flag";
    if (!within(kBltDmaCount, 0, 4) || !within(kBltCycleCount, 2, 4)) return "the channel counts of the blit are beyond four channels or two skips";
    if (uint32_t(at(kBltDmaCount)) != seen.bltx_dmacount || uint32_t(at(kBltCycleCount)) != seen.bltx_cyclecount) return "the channel counts are not the ones BLTX carries";
    for (unsigned word : {kBltAHold2, kBltBHold2, kBltCHold2, kBltBHold, kBltDDatL})
        if (!within(word, 0, 0xffff)) return "a held word of the blitter is wider than 16 bits";
    return nullptr;
}

// The lengths of this build: tests/state/test_state_upstream.py checks them against a real save, and a save that writes another length fails.
constexpr Sub kDiskBodies[] = {
    {"DSK0", 24, 24, produce_dsk, apply_dsk, check_dsk, 0, true}, {"DSK1", 24, 24, produce_dsk, apply_dsk, check_dsk, 1, true},
    {"DSK2", 24, 24, produce_dsk, apply_dsk, check_dsk, 2, true}, {"DSK3", 24, 24, produce_dsk, apply_dsk, check_dsk, 3, true},
    {"DSD0", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 0, true}, {"DSD1", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 1, true},
    {"DSD2", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 2, true}, {"DSD3", 0, kDsdMax, produce_dsd, apply_dsd, check_dsd, 3, true},
    {"DISK", 17, 17, produce_disk, apply_disk, check_disk, 0, true},
    {"DSKX", kDiskPhaseBytes, kDiskPhaseBytes, produce_dskx, apply_dskx, check_dskx, 0, true},
};
constexpr Sub kChipsetBodies[] = {
    {"CHIP", 360, 360, produce_chip, apply_chip, check_chip, 0, true}, {"CAGA", 1024, 1024, produce_caga, apply_caga, check_caga, 0, true, true},
    {"BLTX", 226, 226, produce_bltx, apply_bltx, check_bltx, 0, true},
    {"BLIT", 63, 63, produce_blit, apply_blit, check_blit, 0, false}, {"CHSL", 245, 245, produce_chsl, apply_chsl, check_chsl, 0, true},
    {"SPR0", 25, 25, produce_spr, apply_spr, check_spr, 0, true}, {"SPR1", 25, 25, produce_spr, apply_spr, check_spr, 1, true},
    {"SPR2", 25, 25, produce_spr, apply_spr, check_spr, 2, true}, {"SPR3", 25, 25, produce_spr, apply_spr, check_spr, 3, true},
    {"SPR4", 25, 25, produce_spr, apply_spr, check_spr, 4, true}, {"SPR5", 25, 25, produce_spr, apply_spr, check_spr, 5, true},
    {"SPR6", 25, 25, produce_spr, apply_spr, check_spr, 6, true}, {"SPR7", 25, 25, produce_spr, apply_spr, check_spr, 7, true},
    {"BEAM", kBeamBytes, kBeamBytes, produce_beam, apply_beam, check_beam, 0, true},
    {"CTOD", kCiaTodBytes, kCiaTodBytes, produce_ctod, apply_ctod, check_ctod, 0, true, false, true},
    {"COPR", kCopperBytes, kCopperBytes, produce_copr, apply_copr, check_copr, 0, true},
    {"BLTP", kBlitterBytes, kBlitterBytes, produce_bltp, apply_bltp, check_bltp, 0, true},
    {"LPEN", 16, 16, produce_lpen, apply_lpen, check_lpen, 0, true},
};
const char *check_drive_sound(const Sub &, const Body &b, Seen &) { return check_drive_sound_body(b); }
constexpr Sub kAudioBodies[] = {
    {"AUD0", 25, 25, produce_aud, apply_aud, check_aud, 0, true}, {"AUD1", 25, 25, produce_aud, apply_aud, check_aud, 1, true},
    {"AUD2", 25, 25, produce_aud, apply_aud, check_aud, 2, true}, {"AUD3", 25, 25, produce_aud, apply_aud, check_aud, 3, true},
    {"AUDP", kAudioBytes, kAudioBytes, produce_audp, apply_audp, check_audp, 0, true},
    {"DCLK", kDriveSoundBytes, kDriveSoundBytes, produce_drive_sound, apply_drive_sound, check_drive_sound, 0, true},
};
constexpr Sub kVideoBodies[] = {
    {"BPLX", 292, 292, produce_bplx, apply_bplx, check_bplx, 0, true},
    {"DNSP", kDenisePipeBytes, kDenisePipeBytes, produce_dnsp, apply_dnsp, check_dnsp, 0, true},
    {"DRNG", kRingBytes, kRingBytes, produce_drng, apply_drng, check_drng, 0, true, true},
    {"GEOM", kGeometryBytes, kGeometryBytes, produce_geom, apply_geom, check_geom, 0, true},
};

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
// the body is done, whatever a restore_* or a *_finish scheduled. The generator is P6CP's, applied before any of these bodies, and is put back the same way: restore_custom's
// write of DSKSYNC predicts the next disk event, and the prediction draws one random bit per bit of the line for a drive that reads an unformatted track.
struct ApplyScope {
    bool on;
    int state = 0, idx = 0, next = 1, count = 0, restart = 0, remain = 0;
    uae_u32 rng = 0;
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
        rng = p6_rng_get();
    }
    ~ApplyScope()
    {
        if (!on) return;
        memcpy(eventtab, main, sizeof main);
        memcpy(eventtab2, secondary, sizeof secondary);
        p6_events_set(idx, next, remain);
        nextevent = nextevent_before;
        p6_rng_set(rng);
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

template <size_t N> void visit_bodies(Archive &a, const Sub (&subs)[N], bool (*finish)())
{
    static uint8_t work[kWork];
    Seen seen = {};
    const ApplyScope scope(a.applying());
    for (const Sub &sub : subs) {
        if (sub.aga_only && !aga_machine()) continue;
        if (sub.mains_only && currprefs.cs_ciaatod == 0 && !currprefs.cs_cd32cd) continue;
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
    if (a.applying() && a.ok() && finish) a.require(finish(), "upstream derived state could not be restored");
}

bool finish_chipset()
{
    restore_blitter_finish();
    p6_blitter_phase_set(g_blitter_phase, kBlitterWords);
    p6_custom_rebuild_derived();
    p6_denise_rebuild_derived();      // after CHIP, whose restore_custom has rebuilt acolors: the border colour is derived from COLOR00
    return true;
}
bool finish_audio()
{
    restore_audio_finish();
    const bool owned = p6_session::session.config.machine == p6_bridge::kCd32Machine;
    const bool restored = owned ? p6_cd::device_phase_set(g_audio_phase, kAudioWords) : p6_audio_phase_set(g_audio_phase, kAudioWords);
    p6_drive_sound::set(g_drive_sound, p6_drive_sound::kWords);
    return restored;
}

}  // namespace

// The drives hold the media the state says before the bodies of DSKn, DSDn and DSKX overwrite the head, the track and the timing: restore_disk neither inserts nor
// ejects, so a drive that holds another medium (or none) than the state's would keep a disk the state knows nothing about, and a rewind across a disk change, or a
// state taken while a change counted down, could not be loaded. A drive whose content already is the state's (the same CRC, and the manifest index the bridge believes
// is in it) is left alone; any other is emptied and, if the state's holds a medium, receives it at once and write protected, the way the reset head inserts the boot
// media: no delayed change, nothing scheduled. The insert draws random bits for the position of the disk and the engine may schedule from a drive that moves; the scope
// puts the scheduler and the generator back, as it does for the bodies. Nothing here can refuse: disk_matches_manifest has already measured every medium it uses.
void put_media_in_drives()
{
    const MediaPlan *plan = loading_media();
    if (!plan) return;
    const ApplyScope scope(true);
    for (int n = 0; n < int(p6_bridge::resolve_floppy_drives(p6_session::session.config)); ++n) {
        uint8_t live[64];
        size_t produced = 0;
        {
            const PureSave pure;
            if (!save_disk(n, &produced, live, false) || produced != 24) return;
        }
        const uint32_t held = Body{live, 24}.u32(12);       // the CRC drive_insert measured, 0 for an empty drive
        uint32_t wanted = 0;
        if (plan->held[n] >= 0) p6_bridge::media_crc32(uint32_t(plan->held[n]), &wanted);
        if (held == wanted && (!wanted || p6_session::session.drive_media[n] == plan->held[n])) continue;
        if (held) p6_bridge::put_media_now(uint32_t(n), -1);
        if (wanted) p6_bridge::put_media_now(uint32_t(n), plan->held[n]);
    }
}

// The write overlay of the floppies (T27, P6_CONFIG_FLOPPY_WRITES): the sectors the guest wrote, which are machine state like Chip RAM. It follows the bodies above
// and exists only in an image that reserved the pool at load (the configuration flag is part of the identity, so a state and its image agree). u32 count, u64 of
// writes refused for lack of room, then per sector in the order they were first written: u8 medium (an index of the manifest the state had), u16 sector (0..1759) and
// the 512 bytes. The bound is the whole pool, whatever the state holds. A state names a sector at most once; a medium beyond its manifest or a sector beyond
// the disk is refused before anything is applied. The identity of a disk stays its base (the CRC in DSKn is the base's): the overlay is not part of the manifest.
void visit_overlay(Archive &a)
{
    p6_overlay *o = p6_blob_overlay();
    if (!o) return;
    static uint8_t seen[(P6_OVL_MEDIA * P6_OVL_DISK_SECTORS + 7) / 8];      // Validate and Apply: the sectors this body named so far
    memset(seen, 0, sizeof seen);
    uint32_t count = o->count;
    const uint32_t n = a.count(count, P6_OVL_SLOTS, "overlay.count");
    a.num(o->refused, "overlay.refused");
    for (uint32_t i = 0; i < n && a.ok(); ++i) {
        const uint32_t medium = a.val<uint8_t>(o->medium[i], 0, uint8_t(P6_OVL_MEDIA - 1), "overlay.medium");
        const uint32_t sector = a.val<uint16_t>(o->sector[i], 0, uint16_t(P6_OVL_DISK_SECTORS - 1), "overlay.sector");
        if (a.reading() && a.ok()) {
            a.require(medium < manifest_count(), "overlay: a sector of a medium the state's manifest does not have");
            const uint32_t at = medium * P6_OVL_DISK_SECTORS + sector;
            a.require(!(seen[at >> 3] & (1u << (at & 7))), "overlay: a sector is named twice");
            seen[at >> 3] = uint8_t(seen[at >> 3] | (1u << (at & 7)));
        }
        if (a.applying() && a.ok()) {
            o->medium[i] = uint8_t(medium);
            o->sector[i] = uint16_t(sector);
        }
        a.bytes(p6_overlay_sector(o, i), P6_OVL_SECTOR_BYTES, "overlay.data");
    }
    if (a.applying() && a.ok()) {
        o->count = n;
        a.require(p6_overlay_index(o, manifest_count()) == 0, "overlay: the sectors do not index");
    }
    // The raw tracks (a custom format the guest wrote, see overlay.h): u32 count (<= 16), then per track u8 medium, u8 track (cylinder * 2 + side), u32 length in bits and
    // the (bits + 15) / 16 words of 16 bits. Distinct (medium, track) pairs; a length of zero or beyond the slot is refused.
    uint32_t raw = o->raw_count;
    const uint32_t tracks = a.count(raw, P6_OVL_RAW_SLOTS, "overlay.raw_count");
    static uint8_t seen_raw[(P6_OVL_MEDIA * P6_OVL_TRACKS + 7) / 8];
    memset(seen_raw, 0, sizeof seen_raw);
    for (uint32_t i = 0; i < tracks && a.ok(); ++i) {
        const uint32_t medium = a.val<uint8_t>(o->raw_medium[i], 0, uint8_t(P6_OVL_MEDIA - 1), "overlay.raw_medium");
        const uint32_t track = a.val<uint8_t>(o->raw_track[i], 0, uint8_t(P6_OVL_TRACKS - 1), "overlay.raw_track");
        const uint32_t bits = a.val<uint32_t>(o->raw_bits[i], 1u, P6_OVL_RAW_WORDS * 16u, "overlay.raw_bits");
        if (a.reading() && a.ok()) {
            a.require(medium < manifest_count(), "overlay: a raw track of a medium the state's manifest does not have");
            const uint32_t at = medium * P6_OVL_TRACKS + track;
            a.require(!(seen_raw[at >> 3] & (1u << (at & 7))), "overlay: a raw track is named twice");
            seen_raw[at >> 3] = uint8_t(seen_raw[at >> 3] | (1u << (at & 7)));
        }
        const uint32_t words = a.mode() == Mode::Measure ? P6_OVL_RAW_WORDS : (bits + 15u) / 16u;      // the bound counts the longest track
        uint16_t *slot = p6_overlay_raw_slot(o, i);
        for (uint32_t w = 0; w < words && a.ok(); ++w) a.num(slot[w], "overlay.raw_words");
        if (a.applying() && a.ok()) {
            o->raw_medium[i] = uint8_t(medium);
            o->raw_track[i] = uint8_t(track);
            o->raw_bits[i] = bits;
        }
    }
    if (a.applying() && a.ok()) o->raw_count = tracks;
}

void visit_disk(Archive &a)
{
    if (a.applying()) put_media_in_drives();
    visit_bodies(a, kDiskBodies, nullptr);
    if (a.ok()) visit_hdf(a);               // T29c: the controller of the hard files and the overlay of their writes, while one is mounted (state_hdf.cpp)
    if (a.ok()) visit_overlay(a);
}

// The preferences and the drive name the medium the state says each drive holds, or is waiting for: the engine's own names (what disk_insert_2 and disk_eject set) are the
// live machine's history, and a mount of the medium the live machine was waiting for would otherwise be taken for the one already in the drive. Values only.
void rebuild_disk()
{
    const p6_session::Session &s = p6_session::session;
    for (uint32_t n = 0; n < p6_bridge::resolve_floppy_drives(s.config); ++n) {
        char id[p6_bridge::kMediaBlobIdBytes] = "";
        if (s.drive_media[n] >= 0) p6_bridge::media_blob_id(s.media_content_id[s.drive_media[n]], id);
        _tcscpy(changed_prefs.floppyslots[n].df, id);
        _tcscpy(currprefs.floppyslots[n].df, id);
        changed_prefs.floppyslots[n].forcedwriteprotect = currprefs.floppyslots[n].forcedwriteprotect = p6_bridge::media_forced_write_protect(id);
        if (g_disk_pending[n]) p6_disk_newname_set(int(n), id);
    }
}
void visit_chipset(Archive &a) { visit_bodies(a, kChipsetBodies, finish_chipset); }
void visit_audio(Archive &a) { visit_bodies(a, kAudioBodies, finish_audio); }
void visit_video(Archive &a)
{
    visit_bodies(a, kVideoBodies, nullptr);
    if (a.ok()) visit_raster(a);
}

// The media the state's drives hold are the ones its manifest names. P6BR says which manifest medium each drive holds (drive_media) and DSKn carries the CRC of the image
// that was in the drive: the one drive_insert measured, which is what media_crc32 measures over the registered blob. A drive that counts down a delayed change (DSKX) has
// no disk in it and DSKn says 0, whatever medium P6BR names as the one it waits for; so does a drive that holds none. Anything else is a state that no machine
// of this manifest could have written, and nothing has been touched when it is refused. The DSKn bodies are the first four sub-bodies of the chunk and DSKX the last
// (a u32 length after the tag, little endian, like every field of the archive); Validate has already checked their order and lengths. The plan says which medium each
// engine drive has to hold, for the apply.
const char *disk_matches_manifest(const uint8_t *body, uint64_t length, MediaPlan &plan)
{
    static char text[112];
    const uint8_t *dsk[4] = {}, *dskx = nullptr;
    uint64_t at = 0;
    for (size_t i = 0; i < sizeof kDiskBodies / sizeof kDiskBodies[0]; ++i) {
        if (length - at < 8) return "the disk chunk ends inside the header of a body";
        const uint64_t size = uint64_t(body[at + 4]) | uint64_t(body[at + 5]) << 8 | uint64_t(body[at + 6]) << 16 | uint64_t(body[at + 7]) << 24;
        if (length - at - 8 < size) return "the disk chunk ends inside a body";
        if (i < 4) dsk[i] = body + at + 8;
        if (i == sizeof kDiskBodies / sizeof kDiskBodies[0] - 1) dskx = body + at + 8;
        at += 8 + size;
    }
    // The hard files follow the bodies (state_hdf.cpp): the state has to name the base images this image registered.
    if (const char *why = hdf_matches_manifest(body + at, length - at)) return why;
    for (int n = 0; n < 4; ++n) {
        const bool countdown = int32_t(Body{dskx, kDiskPhaseBytes}.u32((kDkDrives + n * kDkDriveWords + kDkChangeTime) * 4)) > 0;
        const int32_t medium = plan.drive_media[n];
        plan.held[n] = countdown ? -1 : medium;
        uint32_t expected = 0;
        if (medium < 0 && countdown) {
            snprintf(text, sizeof text, "DSK%d: a disk change counts down in a drive that the manifest says is empty", n);
            return text;
        }
        if (plan.held[n] >= 0 && !p6_bridge::media_crc32(uint32_t(plan.held[n]), &expected)) {
            snprintf(text, sizeof text, "DSK%d: the CRC of medium %d of the manifest cannot be measured", n, int(medium));
            return text;
        }
        if (Body{dsk[n], 24}.u32(12) != expected) {
            snprintf(text, sizeof text, "DSK%d: the CRC of the disk in the drive is not the one of the medium the manifest gives it", n);
            return text;
        }
    }
    return nullptr;
}

}  // namespace p6_state
