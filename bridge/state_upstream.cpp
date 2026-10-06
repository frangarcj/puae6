// Chunks P6DK (disk), P6AU (audio), P6CH (chipset) and P6VD (video) with the bodies WinUAE's own save_* functions write
// (docs/adr/0009-state-format.md, point 8). Each body travels as a sub-body: its tag of 4 characters (the tag upstream's container gives it), a u32 length and
// the bytes the engine wrote, with no ASF container and no zfile. The save_* come from the engine compiled with P6_STATE_BODIES (patches 0086-0089),
// SAVESTATE stays undefined, and what upstream also saves but another chunk owns (CYCS, CHPD, CHPX, CIAA/B, KEYB, CINP, the CPU, the RAMs) is not here.
//
//   P6DK  DSK0..DSK3 (24 bytes each), DSD0..DSD3 (absent: length 0; present: the engine's track buffer), DISK (17)
//   P6CH  CHIP (360), BLTX (226), BLIT (63), CHSL (191), SPR0..SPR7 (25 each)
//   P6AU  AUD0..AUD3 (25 each)
//   P6VD  BPLX (292)
//
// T15i1 saves and measures; Validate and Apply are T15i2. Until then these chunks are marked save_only in the registry: the loader neither validates nor applies
// them, and the public API stays UNSUPPORTED because complete() does not count them. A visitor asked to read fails, so a registry that forgets the flag cannot
// load these chunks as if they were restored.
//
// A save does not change the machine: STATE_DOSAVE keeps getjoystate from reading the host's input (maybe_read_input), and the poll mask that POT0DAT and
// POT1DAT set is put back. The other side effect of save_custom, the busy and zero bits of DMACONR stored into dmacon, is removed by patch 0088.
#include "state.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "custom.h"
#include "inputdevice.h"
#include "savestate.h"

namespace p6_state {

namespace {

// MAXMFMBUF of disk.cpp: words of the track buffer (DDHDMULT is 1: FLOPPY_DRIVE_HD is not in the profile). A DSDn body is 22 bytes plus a word, or a
// word and its timing, for every 16 bits of the track, plus 2.
constexpr uint32_t kMfmWordsMax = 0x4000;
constexpr uint32_t kDsdFixed = 4 + 2 + 4 + 2 + 4 + 4 + 2;
constexpr uint32_t kDsdMax = kDsdFixed + kMfmWordsMax * 4;
constexpr size_t kWork = kDsdMax + 64;

// One body: its tag, the length save_* gives it in this build (0 = not fixed) and the largest it can be.
struct Sub {
    const char *tag;
    uint32_t fixed;
    uint32_t maximum;
    uint8_t *(*produce)(int index, size_t *length, uint8_t *work);     // nullptr result: the body is absent (only DSDn)
    int index;
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
uint8_t *produce_bplx(int, size_t *len, uint8_t *w) { return save_custom_bpl(len, w); }

// The lengths of this build: tests/state/test_state_upstream.py checks them against a real save, and a save that writes another length fails.
constexpr Sub kDiskBodies[] = {
    {"DSK0", 24, 24, produce_dsk, 0}, {"DSK1", 24, 24, produce_dsk, 1}, {"DSK2", 24, 24, produce_dsk, 2}, {"DSK3", 24, 24, produce_dsk, 3},
    {"DSD0", 0, kDsdMax, produce_dsd, 0}, {"DSD1", 0, kDsdMax, produce_dsd, 1}, {"DSD2", 0, kDsdMax, produce_dsd, 2}, {"DSD3", 0, kDsdMax, produce_dsd, 3},
    {"DISK", 17, 17, produce_disk, 0},
};
constexpr Sub kChipsetBodies[] = {
    {"CHIP", 360, 360, produce_chip, 0}, {"BLTX", 226, 226, produce_bltx, 0}, {"BLIT", 63, 63, produce_blit, 0}, {"CHSL", 191, 191, produce_chsl, 0},
    {"SPR0", 25, 25, produce_spr, 0}, {"SPR1", 25, 25, produce_spr, 1}, {"SPR2", 25, 25, produce_spr, 2}, {"SPR3", 25, 25, produce_spr, 3},
    {"SPR4", 25, 25, produce_spr, 4}, {"SPR5", 25, 25, produce_spr, 5}, {"SPR6", 25, 25, produce_spr, 6}, {"SPR7", 25, 25, produce_spr, 7},
};
constexpr Sub kAudioBodies[] = {
    {"AUD0", 25, 25, produce_aud, 0}, {"AUD1", 25, 25, produce_aud, 1}, {"AUD2", 25, 25, produce_aud, 2}, {"AUD3", 25, 25, produce_aud, 3},
};
constexpr Sub kVideoBodies[] = {{"BPLX", 292, 292, produce_bplx, 0}};

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

template <size_t N> void visit_bodies(Archive &a, const Sub (&subs)[N])
{
    if (a.reading()) {
        a.fail("the upstream bodies of this chunk are saved but not yet validated or applied");
        return;
    }
    static uint8_t work[kWork];
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
                static char why[96];        // the first failure sticks and the archive keeps the pointer: one message at a time is enough
                snprintf(why, sizeof why, "upstream body %s is %zu bytes, this build's table says %u", sub.tag, produced, unsigned(sub.fixed ? sub.fixed : sub.maximum));
                a.fail(why);
                return;
            }
            length = uint32_t(produced);
        }
        a.bytes(const_cast<char *>(sub.tag), 4, sub.tag);
        a.val<uint32_t>(length, "upstream.length");
        a.bytes(work, length, sub.tag);
    }
}

}  // namespace

void visit_disk(Archive &a) { visit_bodies(a, kDiskBodies); }
void visit_chipset(Archive &a) { visit_bodies(a, kChipsetBodies); }
void visit_audio(Archive &a) { visit_bodies(a, kAudioBodies); }
void visit_video(Archive &a) { visit_bodies(a, kVideoBodies); }

}  // namespace p6_state
