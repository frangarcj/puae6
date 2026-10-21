// Native state of a loaded image: the chunk registry, the identity of the image and p6_state_bound / p6_state_save / p6_state_load
// (docs/adr/0009-state-format.md). The container is ports/portable/state_io; each chunk is one visitor function (archive.h) that
// describes the state once for saving, measuring, validating and applying.
//
// A load is validated in full before the machine is touched: the envelope (length, magic, version, checksums, bound, identity, chunk
// table), then every chunk body by running its visitor in Validate mode, which reads the same bytes with the same ranges as Apply
// and writes nothing. Only then do the chunks apply. Validate and Apply share the code, so a failure while applying is an internal
// error, and the image is left Faulted rather than half restored without saying so.
#include "state.h"

#include "error_util.h"
#include "session.h"
#include "frame_step.h"
#include "config.h"
#include "media.h"
#include "media/cd_device.h"
#include "media/cd_read.h"
#include "audio_cda_state.h"

#include <stdio.h>
#include <string.h>

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "ar.h"
#include "p6_state_access.h"
#include "events.h"
#include "memory.h"
#include "machdep/maccess.h"
#include "newcpu.h"
#include "sounddep/sound.h"

extern void p6_mbres_get(uae_u8 *r);
extern void p6_mbres_set(const uae_u8 *r);

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

extern bool p6_expansion_state_get(uint32_t *w, unsigned n, uint8_t *autoconfig);      // expansion.cpp, patch 0111
extern bool p6_expansion_state_set(const uint32_t *w, unsigned n, const uint8_t *autoconfig);

namespace p6_state {

namespace {

// What the chunks of one payload share besides their own bytes: the counters of its validated header. They come from the state being loaded, never
// from the destination image, so load() sets the pointer around its validate and apply passes and clears it after.
struct VisitContext {
    uint64_t unit_index, master_ticks;
    MediaPlan media;
    int64_t scheduler_cycle = -1;
    uint32_t media_count;       // how many media the manifest of the state had: every medium its bodies name is below it (the live manifest may have grown since)
    uint32_t cdtv_phase = 0;
    uint32_t cpu_flags = 0;
};
VisitContext *g_context = nullptr;
struct ContextScope {
    explicit ContextScope(VisitContext *context) { g_context = context; }
    ~ContextScope() { g_context = nullptr; }
    ContextScope(const ContextScope &) = delete;
    ContextScope &operator=(const ContextScope &) = delete;
};

// The size of the manifest the state in hand was taken with: the loading state's (a prefix of the live manifest, recognised from its identity by load()) while a payload
// is validated or applied, the live one when the machine is saved or measured. Every medium index a body carries has to be below it.
}  // namespace
uint32_t manifest_count()
{
    return g_context ? g_context->media_count : session.media_count;
}
namespace {

// P6BR: the bookkeeping of the API session (bridge/session.h) that no engine chunk holds: the audio frame count, the delivery flag of the unit,
// the pending reset, the start flag, the media-event sequence guard, the drive contents the caller asked for and the ordered queue of media
// events not yet applied. 40 bytes without events, at most 136 with 16. Not here: the unit and tick counters (they travel in the header; cycles_seen
// is derived from the scheduler's counter and load() sets it to currcycle), the identity of the firmware, the configuration and the media (the envelope validates it, a state of
// another one is refused and never adapted; the media manifest only needs to START with the state's: every index here is below the count it had), the edge memory of the input (P6IN), the clock's anchor (P6RT) and the names of the floppies in
// the preferences (the engine's own disk body, when it exists). Applying neither consumes a queued event nor touches the engine: the events
// are applied by the next p6_run_frame, as they would have been.
void visit_bridge(Archive &a)
{
    auto &s = session;
    const uint64_t audio = a.val<uint64_t>(s.audio_frames_total, "bridge.audio_frames_total");
    const uint8_t handed = get<uint8_t>(a, s.handed_over, 0, 1, "bridge.handed_over");
    const uint8_t reset = get<uint8_t>(a, s.reset_queued, 0, 1, "bridge.reset_queued");
    get<uint8_t>(a, s.started, 1, 1, "bridge.started");            // a state exists only for an image that ran its reset head
    const uint8_t have = get<uint8_t>(a, s.have_sequence, 0, 1, "bridge.have_sequence");
    const uint64_t sequence = a.val<uint64_t>(s.last_sequence, "bridge.last_sequence");
    int32_t drives[4];
    for (uint32_t i = 0; i < 4; ++i)
        drives[i] = get<int32_t>(a, s.drive_media[i], -1, i < p6_bridge::resolve_floppy_drives(s.config) ? int32_t(manifest_count()) - 1 : -1, "bridge.drive_media");
    if (a.reading() && a.ok() && g_context) memcpy(g_context->media.drive_media, drives, sizeof drives);     // the disk chunk is checked against it before anything is applied
    uint32_t queued = s.media_queued;
    const uint32_t count = a.count(queued, s.kMediaQueue, "bridge.media_queued");
    p6_session::Session::MediaEvent events[p6_session::Session::kMediaQueue] = {};
    for (uint32_t i = 0; i < count && a.ok(); ++i) {
        const auto &live = s.media_queue[i];
        const uint32_t type = get<uint8_t>(a, live.type, P6_MEDIA_EVENT_MOUNT, P6_MEDIA_EVENT_EJECT, "bridge.event_type");
        const uint32_t drive = get<uint8_t>(a, live.drive, 0, p6_bridge::resolve_floppy_drives(s.config) - 1, "bridge.event_drive");
        const uint32_t index = a.val<uint32_t>(live.media_index, "bridge.event_index");
        if (a.mode() != Mode::Measure)
            a.require(type == P6_MEDIA_EVENT_MOUNT ? index < manifest_count() : index == P6_MEDIA_INDEX_EJECT, "bridge event: invalid media index for its type");
        events[i] = {type, drive, index};
    }
    if (a.mode() == Mode::Save)            // cycles_seen is not saved: a load sets it to currcycle, which is where a boundary has it
        a.require(s.cycles_seen == int64_t(currcycle), "the unit's tick accounting is not at the cycle counter");
    if (a.mode() != Mode::Measure)
        a.require(have || (!sequence && !count), "bridge: events or sequence without an accepted event");
    if (a.applying() && a.ok()) {
        a.require(g_context != nullptr, "bridge: no payload context");
        if (!a.ok()) return;
        s.unit_index = g_context->unit_index;
        s.master_ticks = g_context->master_ticks;
        s.audio_frames_total = audio;
        s.handed_over = handed != 0;
        s.reset_queued = reset != 0;
        s.started = true;
        s.have_sequence = have != 0;
        s.last_sequence = sequence;
        memcpy(s.drive_media, drives, sizeof drives);
        s.media_queued = count;
        memcpy(s.media_queue, events, sizeof events);
    }
}

// P6RM: Chip RAM and Slow RAM, byte for byte in the engine's own order. The sizes are written so that a state of another memory
// configuration is refused by the chunk even if the identity let it through; they are checked, not loaded, because the memory is
// allocated by p6_load from the configuration that is part of the identity. The block is never cleared on the way in
// (memory_clear belongs to a hard reset). Derived state (the overlay mapping, the bank map) is rebuilt by the chunks that own its
// inputs (docs: manifests/state-inventory.json, ram-overlay-state).
//
// A machine with a Zorro II Fast RAM board (T49) appends to the body: the board's size and bytes, then the autoconfig chain (patch 0111, the values
// listed in p6_expansion_state_get and the 256 bytes of the card's nibbles). A machine without one keeps the body it always had, so no state of the
// three presets changes by a byte. The size is the configured one, not the allocated one: the board is allocated by the reset head, and the bound
// (made before the first unit) and the validation of a load into an image that has not started it have to know it already.
constexpr unsigned kExpansionWords = 16;  // final three words exist only with Z3 (0350); legacy bodies keep their layout

}  // namespace

// The chain of a machine whose cards are the Fast RAM board (`fast` bytes, 0 for none) and, with a hard file (T29c), the UAEFS board and the UAE boot ROM after it. A machine with only
// those two has the same words with the Fast RAM ones at zero, and its chain is described by the hard-file section of P6DK (visit_hdf), which calls this with `fast` 0.
void visit_expansion(Archive &a, uint32_t fast)
{
    const uint32_t z3 = p6_bridge::z3_bytes(session.config);
    const unsigned words = z3 ? kExpansionWords : 13;
    // the hard file adds the UAEFS board and the UAE boot ROM (a card with no autoconfig, expamem_rtarea_init); on Gayle's IDE (T56) it adds one card instead, the IDE port itself (not autoconfig either)
    const bool ide = p6_bridge::ide_attached(session.config, p6_bridge::hard_file_count());
    const bool cdtv = session.config.machine == p6_bridge::kCdtvMachine;
    const uint32_t cards = (cdtv ? 1u : 0u) + (fast ? 1u : 0u) + (z3 ? 1u : 0u) + (ide ? 1u : (p6_bridge::hard_file_count() ? 2u : 0u) + (p6_bridge::motherboard_ide(session.config) ? 1u : 0u));
    uint32_t w[kExpansionWords] = {};
    uint8_t ac[256] = {};
    if (a.mode() == Mode::Save)
        a.require(fastmem_bank[0].allocated_size == fast && z3fastmem_bank[0].allocated_size == z3 && p6_expansion_state_get(w, words, ac), "the Fast RAM board or its autoconfig chain is not in a state that can be saved");
    const uint8_t ecard = get<uint8_t>(a, w[0], 0, cards, "ram.fast_ecard");
    get<uint8_t>(a, w[1], cards, cards, "ram.fast_cardno");
    const uint8_t chipdone = get<uint8_t>(a, w[2], 0, 1, "ram.fast_chipdone");
    const uint8_t map_set = get<uint8_t>(a, w[3], 0, 1, "ram.fast_map_set");
    const uint8_t bank_current = get<uint8_t>(a, w[4], 0, cdtv ? 2 : 1, "ram.fast_bank_current");
    get<uint8_t>(a, w[5], 0, 0, "ram.fast_autoconfig_mode");
    const uint16_t hi = get<uint16_t>(a, w[6], 0, 0xffff, "ram.fast_hi");
    const uint8_t lo = get<uint8_t>(a, w[7], 0, 0xff, "ram.fast_lo");
    const uint32_t pointer = a.val<uint32_t>(w[8], 0, 0xffffffffu, "ram.fast_board_pointer");
    // Z3 allocates a physical window of at least16MiB, with a smaller populated subsize.
    const uint32_t board_limit = z3 ? (z3 < 0x1000000u ? 0x1000000u : z3) : 0x800000u;
    const uint32_t size = a.val<uint32_t>(w[9], 0, board_limit, "ram.fast_board_size");
    const uint32_t start = a.val<uint32_t>(w[10], 0, fast ? 0xffffffffu : 0u, "ram.fast_start");
    const uint8_t mapped = get<uint8_t>(a, w[12], 0, fast ? 1 : 0, "ram.fast_mapped");
    if (cdtv) for (auto &byte : ac) byte = a.val<uint8_t>(byte, "ram.fast_autoconfig");
    else a.bytes(ac, sizeof ac, "ram.fast_autoconfig");
    if (z3) {
        w[13] = a.val<uint32_t>(w[13], "ram.z3_start");
        a.val<uint32_t>(z3, z3, z3, "ram.z3_allocated");
        w[14] = z3;
        w[15] = get<uint8_t>(a, w[15], 0, 1, "ram.z3_mapped");
        a.require(w[13] == 0xffffffff || !(w[13] & 0xffff), "Z3: unaligned base");
        a.require(!w[15] || (w[13] >= 0x10000000 && uint64_t(w[13]) + z3 <= 0xf0000000), "Z3: mapped base outside the bus");
    }
    if (a.mode() == Mode::Measure) return;
    if (cdtv) {
        a.require(!map_set && !hi && !lo && (ecard == 0 ? bank_current == 2 : bank_current == 0), "CDTV autoconfig phase is invalid");
        for (unsigned n = 0; n < 256; ++n) {
            const uint8_t expected = ecard ? 255 : n == 0 ? 0xc0 : n == 2 ? 0x10 : n == 6 ? 0xcf : n == 8 ? 0xbf : (n == 0x12 || n == 0x16) ? 0xdf : 255;
            a.require(ac[n] == expected, "CDTV autoconfig PROM is invalid");
        }
        if (a.reading() && a.ok() && g_context) g_context->cdtv_phase = ecard ? 2 : 1;
    }
    // The base of the board: none (0), a shut-up board ($FFFFFFFF) or an address in 64 KiB steps, the slot the chain suggests or the one the guest gave it. Where the board answers it
    // lies inside $200000-$9FFFFF (the guest decides; an 8 MiB board can only be at $200000).
    a.require(start == 0 || start == 0xffffffffu || !(start & 0xffff), "the Fast RAM board is at an address it cannot have");
    a.require(!mapped || (start >= 0x200000 && start + fast <= 0xa00000 && !(start & 0xffff)), "the Fast RAM board answers at an address it cannot have");
    a.require(ecard < cards || (!map_set && !bank_current), "a finished autoconfig chain still has a card");
    if (a.applying() && a.ok()) {
        w[0] = ecard; w[1] = cards; w[2] = chipdone; w[3] = map_set; w[4] = bank_current; w[5] = 0; w[6] = hi; w[7] = lo;
        w[8] = pointer; w[9] = size; w[10] = start; w[11] = fast; w[12] = mapped;
        a.require(p6_expansion_state_set(w, words, ac), "the autoconfig chain does not fit the machine");
    }
}

namespace {

void visit_hrtmon(Archive &a)
{
    if (!p6_bridge::hrtmon_bound()) return;
    p6_hrtmon_raw h{}; uint8_t *ram=p6_hrtmon_memory();
    if (a.mode()==Mode::Save) a.require(p6_hrtmon_get(&h), "HRTMon owner or bank map is invalid");
    a.val<uint32_t>(0x100000,0x100000,0x100000,"hrt.size");
    h.flag=a.val<uint32_t>(h.flag,1,3,"hrt.flag");
    h.triggered=a.val<uint32_t>(h.triggered,0,1,"hrt.triggered");
    h.mapped=a.val<uint32_t>(h.mapped,0,2,"hrt.mapped");
    if (a.mode()!=Mode::Measure) {
        a.require(ram, "HRTMon has no allocated owner");
        a.require((h.flag!=ACTION_REPLAY_IDLE)==bool(cpu_spc(a.mode())&SPCFLAG_ACTION_REPLAY), "HRTMon and CPU pending flags disagree");
        a.require(h.mapped!=1 || h.triggered, "mapped HRTMon was never triggered");
        a.require(h.flag!=ACTION_REPLAY_ACTIVE || (h.triggered && h.mapped==1), "active HRTMon has no trigger or mapping");
    }
    a.bytes(h.custom,sizeof h.custom,"hrt.custom"); a.bytes(h.ciaa,sizeof h.ciaa,"hrt.ciaa"); a.bytes(h.ciab,sizeof h.ciab,"hrt.ciab");
    a.bytes(ram,0x100000,"hrt.memory");
    if (a.applying() && a.ok()) p6_hrtmon_set(&h);
}
void rebuild_ram()
{
    // P6CP precedes the restored bank map. Rebind a Normal CPU without refilling its saved queue.
    if (p6_bridge::hrtmon_bound() || p6_bridge::ar_bound()) m68k_setpc_normal(m68k_getpc());
}

void visit_ar(Archive &a)
{
    if (!p6_bridge::ar_bound()) return;
    p6_ar_raw h{}; auto *ram=p6_ar_memory();
    if (a.mode()==Mode::Save) a.require(p6_ar_get(&h), "AR owner or bank shape is invalid");
    a.val<uint32_t>(0x10000,0x10000,0x10000,"ar.size");
    h.flag=a.val<int32_t>(h.flag,"ar.flag"); h.mapped=a.val<int32_t>(h.mapped,"ar.mapped");
    h.mode_read=a.val<uint32_t>(h.mode_read,"ar.mode_read"); h.mode_write=a.val<uint32_t>(h.mode_write,"ar.mode_write");
    h.hardreset=a.val<uint32_t>(h.hardreset,"ar.hardreset"); h.hide=a.val<uint32_t>(h.hide,"ar.hide");
    h.hidden=a.val<uint32_t>(h.hidden,"ar.hidden"); h.banks=a.val<uint32_t>(h.banks,"ar.banks");
    a.bytes(h.vector,sizeof h.vector,"ar.vector"); a.bytes(h.custom,sizeof h.custom,"ar.custom");
    a.bytes(h.ciaa,sizeof h.ciaa,"ar.ciaa"); a.bytes(h.ciab,sizeof h.ciab,"ar.ciab");
    if (a.mode()!=Mode::Measure) a.require(ram && p6_ar_validate(&h,cpu_spc(a.mode())), "AR control/map/CPU candidate is invalid");
    a.bytes(ram,0x10000,"ar.memory");
    if (a.applying() && a.ok()) p6_ar_set(&h);
}

void visit_ram(Archive &a)
{
    const uint32_t chip = chipmem_bank.allocated_size, slow = bogomem_bank.allocated_size;
    a.val<uint32_t>(chip, chip, chip, "ram.chip_size");
    a.val<uint32_t>(slow, slow, slow, "ram.slow_size");
    a.bytes(chipmem_bank.baseaddr, chip, "ram.chip");
    a.bytes(bogomem_bank.baseaddr, slow, "ram.slow");
    const uint32_t fast = p6_bridge::resolve_memory(session.config).fast_kib * 1024;
    if (p6_bridge::motherboard_ide(session.config)) {
        uint8_t r[4] = {};
        if (a.mode() == Mode::Save) p6_mbres_get(r);
        r[0] = a.val<uint8_t>(r[0], "mbres.ramsey_config");
        r[1] = a.val<uint8_t>(r[1], 0, 1, "mbres.gary_coldboot");
        r[2] = a.val<uint8_t>(r[2], 0, 1, "mbres.gary_timeout");
        r[3] = a.val<uint8_t>(r[3], 0, 1, "mbres.gary_toenb");
        if (a.applying() && a.ok()) p6_mbres_set(r);
    }
    visit_hrtmon(a);
    visit_ar(a);
    const uint32_t z3 = p6_bridge::z3_bytes(session.config);
    if (!fast && !z3 && session.config.machine != p6_bridge::kCdtvMachine) return;
    if (fast) {
        a.val<uint32_t>(fast, fast, fast, "ram.fast_size");
        if (a.mode() == Mode::Save || a.applying())
            a.require(fastmem_bank[0].allocated_size == fast && fastmem_bank[0].baseaddr, "the Fast RAM board is not allocated");
        a.bytes(fastmem_bank[0].baseaddr, fast, "ram.fast");
    }
    if (z3) {
        a.val<uint32_t>(z3, z3, z3, "ram.z3_size");
        if (a.mode() == Mode::Save || a.applying())
            a.require(z3fastmem_bank[0].allocated_size == z3 && z3fastmem_bank[0].baseaddr, "the Z3 board is not allocated");
        a.bytes(z3fastmem_bank[0].baseaddr, z3, "ram.z3");
    }
    visit_expansion(a, fast);
}

}  // namespace

uint32_t cdtv_bus_phase(Mode mode)
{
    if (session.config.machine != p6_bridge::kCdtvMachine) return 0;
    if (mode != Mode::Save) return g_context ? g_context->cdtv_phase : 0;
    uint32_t words[13]; uint8_t bytes[256];
    return p6_expansion_state_get(words, 13, bytes) ? (words[0] ? 2u : 1u) : UINT32_MAX;
}
void publish_cpu_spc(uint32_t spc) { if (g_context) g_context->cpu_flags=spc; }
uint32_t cpu_spc(Mode mode) { return mode==Mode::Save ? uint32_t(regs.spcflags) : g_context ? g_context->cpu_flags : 0; }

const MediaPlan *loading_media() { return g_context ? &g_context->media : nullptr; }

uint32_t chunk_count() { return currprefs.cs_cdtvcd || session.config.machine == p6_bridge::kCd32Machine ? kMaxChunkCount : kChunkCount; }
void publish_scheduler_cycle(int64_t cycle) { if (g_context) g_context->scheduler_cycle = cycle; }
int64_t scheduler_cycle(Mode mode)
{
    return mode == Mode::Save ? int64_t(currcycle) : g_context ? g_context->scheduler_cycle : -1;
}

const Chunk kChunks[kMaxChunkCount + 1] = {
    {"P6CP", "cpu", 1, visit_cpu, nullptr},
    {"P6SC", "scheduler", 1, visit_scheduler, rebuild_scheduler},
    {"P6RM", "ram", 1, visit_ram, rebuild_ram},
    {"P6RT", "rtc", 1, visit_rtc, nullptr},
    {"P6CH", "chipset", 1, visit_chipset, nullptr},
    {"P6VD", "video", 1, visit_video, nullptr},
    {"P6AU", "audio", 1, visit_audio, nullptr},
    {"P6CI", "cia", 1, visit_cia, rebuild_cia},
    {"P6DK", "disk", 1, visit_disk, rebuild_disk, disk_matches_manifest},
    {"P6IN", "input", 1, visit_input, nullptr},
    {"P6BR", "bridge", 1, visit_bridge, nullptr},
    {"P6AK", "akiko", 1, visit_akiko, nullptr},
    {"P6CT", "cdtv", 1, visit_cdtv, nullptr, cdtv_matches_destination},
};

namespace {

const Chunk &active_chunk(uint32_t i) { return kChunks[i == kChunkCount && currprefs.cs_cdtvcd ? kMaxChunkCount : i]; }

void put32(uint8_t *p, uint32_t v);

// The hash of the first `count` media of the live manifest, as media_manifest_hash defines it. current_identity() is the whole manifest (count == media_count).
void manifest_prefix_hash(uint32_t count, uint8_t out[32])
{
    uint32_t types[P6_MAX_MEDIA];
    const uint8_t *ids[P6_MAX_MEDIA];
    if (count > P6_MAX_MEDIA) count = P6_MAX_MEDIA;
    for (uint32_t i = 0; i < count; ++i) {
        types[i] = session.media_type[i];
        ids[i] = session.media_content_id[i];
    }
    media_manifest_hash(count, types, ids, out);
    uint8_t cartridge[32];
    if (p6_bridge::hrtmon_identity(cartridge)) {
        const uint8_t *parts[2]={out,cartridge}; const uint64_t lengths[2]={32,32};
        domain_hash("p6/media/hrtmon/v1",parts,lengths,2,out);
    }
    if (p6_bridge::ar_identity(cartridge)) {
        const uint8_t *parts[2]={out,cartridge}; const uint64_t lengths[2]={32,32};
        domain_hash("p6/media/ar23/v1",parts,lengths,2,out);
    }
    // The ordered CD catalog is fixed; which of its discs is selected is state.
    // Unbound legacy profiles preserve their existing identity byte for byte.
    const uint32_t discs = p6_cd::device_catalog_size();
    if (session.config.machine == p6_bridge::kCd32Machine || session.config.machine == p6_bridge::kCdtvMachine || discs) {
        uint8_t count_le[4]; put32(count_le, discs);
        const uint8_t *parts[p6_cd::kDeviceMaxDiscs + 2] = {out, count_le};
        uint64_t lengths[p6_cd::kDeviceMaxDiscs + 2] = {32, 4};
        for (uint32_t n = 0; n < discs; ++n) {
            parts[n + 2] = p6_cd::device_catalog_disc(n)->layout().content_id;
            lengths[n + 2] = 32;
        }
        domain_hash("p6/media/cd-catalog/v1", parts, lengths, discs + 2, out);
    }
}

// The manifest is append-only (p6_media_append), so a state taken before a medium was added is still a state of this image. The header already stores what is needed to say
// which prefix it was taken with: media_manifest_hash is the hash of the manifest the state had, and that hash includes the count (docs/adr/0009-state-format.md), so the
// count is the one live prefix, if any, that hashes to the stored value. Nothing is trusted from the header here: the field only chooses which prefix the envelope compares
// against, and the envelope then checks the header checksum and, for the identity, the same hash, so a header whose bytes were damaged is refused as before, and one that
// matches no prefix of this manifest is compared with the whole live manifest and refused as INCOMPATIBLE by name. Returns false when no prefix matches (or the header is
// too short to have the field).
bool recognise_manifest_prefix(const uint8_t *data, uint64_t bytes, uint32_t *count)
{
    constexpr uint64_t kManifestHashAt = 208;      // media_manifest_hash in the header (docs/adr/0009-state-format.md)
    if (!data || bytes < kHeaderSize) return false;
    // The hard files are mounted at p6_load and cannot be appended, so every prefix a state can have been taken with holds all of them (T29c): a prefix that leaves one out is a
    // machine without that controller, and the state of such a machine is not this image's.
    uint32_t hard_files = 0;
    for (uint32_t i = 0; i < session.media_count && i < P6_MAX_MEDIA; ++i) hard_files += session.media_type[i] == P6_MEDIA_HDF;
    uint32_t held = 0;
    for (uint32_t n = 0; n <= session.media_count && n <= P6_MAX_MEDIA; ++n) {
        if (n > 0) held += session.media_type[n - 1] == P6_MEDIA_HDF;
        if (held != hard_files) continue;
        uint8_t prefix[32];
        manifest_prefix_hash(n, prefix);
        if (!memcmp(prefix, data + kManifestHashAt, 32)) {
            *count = n;
            return true;
        }
    }
    return false;
}

}  // namespace

Identity current_identity()
{
    const BuildIdentity &build = build_identity();
    Identity identity = {};
    identity.profile_id = build.profile_id;
    memcpy(identity.core_build_id, build.core_build_id, 32);
    memcpy(identity.upstream_commit, build.upstream_commit, 20);
    memcpy(identity.patch_series_hash, build.patch_series_hash, 32);
    memcpy(identity.codegen_hash, build.codegen_hash, 32);
    const p6_config &c = session.config;
    config_hash(c.machine, c.video_standard, c.audio_rate_hz, p6_bridge::identity_flags(c), c.random_seed, c.rtc_epoch_seconds, identity.config_hash);
    // T73: Paula's output options. The hash of a config at the profile's setting is the one it always was; any other setting derives from it with the four resolved settings.
    uint8_t audio[4];
    if (p6_bridge::audio_identity(c, audio)) {
        const uint8_t *parts[2] = {identity.config_hash, audio};
        const uint64_t lengths[2] = {32, 4};
        domain_hash("p6/config/audio/v1", parts, lengths, 2, identity.config_hash);
    }
    // T52: the CPU, when it is not the machine's own. Derived after the audio word so that a config with neither keeps the hash it always had.
    uint8_t cpu[2];
    if (p6_bridge::cpu_identity(c, cpu)) {
        const uint8_t *parts[2] = {identity.config_hash, cpu};
        const uint64_t lengths[2] = {32, 2};
        domain_hash("p6/config/cpu/v1", parts, lengths, 2, identity.config_hash);
    }
    // T56: Gayle's IDE port, while a hard file is behind it (a config that asks for IDE and has no hard file is the default machine and keeps its hash).
    if (p6_bridge::ide_attached(c, p6_bridge::hard_file_count())) {
        const uint8_t ide[1] = {P6_HDF_CONTROLLER_IDE};
        const uint8_t *parts[2] = {identity.config_hash, ide};
        const uint64_t lengths[2] = {32, 1};
        domain_hash("p6/config/ide/v1", parts, lengths, 2, identity.config_hash);
    }
    // Accuracy is a restart option. Exact adds nothing, preserving all default config hashes.
    if (c.cpu_compatibility != P6_CPU_EXACT) {
        const uint8_t *parts[2] = {identity.config_hash, &c.cpu_compatibility};
        const uint64_t lengths[2] = {32, 1};
        domain_hash("p6/config/cpu-compatibility/v1", parts, lengths, 2, identity.config_hash);
    }
    if (p6_bridge::resolve_floppy_drives(c) != 2) {
        const uint8_t drives = uint8_t(p6_bridge::resolve_floppy_drives(c));
        const uint8_t *parts[2] = {identity.config_hash, &drives};
        const uint64_t lengths[2] = {32, 1};
        domain_hash("p6/config/floppy-drives/v1", parts, lengths, 2, identity.config_hash);
    }
    if (currprefs.cs_cdtvcd) {
        const uint8_t *parts[1] = {identity.config_hash}; const uint64_t lengths[1] = {32};
        domain_hash("p6/config/cdtv-private/v1", parts, lengths, 1, identity.config_hash);
    }
    const uint8_t collision = uint8_t(p6_bridge::resolve_collision_level(c));
    if (collision != 3) {
        const uint8_t *parts[2] = {identity.config_hash, &collision};
        const uint64_t lengths[2] = {32, 1};
        domain_hash("p6/config/collisions/v1", parts, lengths, 2, identity.config_hash);
    }
    manifest_prefix_hash(session.media_count, identity.media_manifest_hash);
    if (c.z3_memory) {
        const uint8_t *parts[2] = {identity.config_hash, &c.z3_memory};
        const uint64_t lengths[2] = {32, 1};
        domain_hash("p6/config/z3/v1", parts, lengths, 2, identity.config_hash);
    }
    memcpy(identity.firmware_sha256, session.firmware_sha256, 32);
    return identity;
}

bool complete(char *missing, size_t capacity)
{
    bool all = true;
    size_t used = 0;
    if (missing && capacity) missing[0] = 0;
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        if (chunk.visit) continue;
        all = false;
        if (missing && capacity > used + 1) {
            const int n = snprintf(missing + used, capacity - used, "%s%s", used ? ", " : "", chunk.name);
            if (n > 0) used = size_t(n) < capacity - used ? used + size_t(n) : capacity - 1;
        }
    }
    return all;
}

namespace {

// What the registry says about this build: the chunk table the envelope is checked against and the largest body of each chunk.
struct Plan {
    ChunkSpec specs[kMaxChunkCount];
    uint64_t bounds[kMaxChunkCount];
    bool valid = true;
    uint64_t total;         // header and every chunk at its largest
};

Plan make_plan()
{
    Plan plan = {};
    plan.total = kHeaderSize;
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        memcpy(plan.specs[i].tag, chunk.tag, 4);
        plan.specs[i].version = chunk.visit ? chunk.version : 0;    // version 0: the placeholder of a chunk that does not exist yet
        if (chunk.visit) {
            Archive measure = Archive::measurer();
            chunk.visit(measure);
            if (!measure.ok()) { plan.valid = false; return plan; }
            plan.bounds[i] = measure.position();
        }
        plan.total += kChunkHeaderSize + plan.bounds[i];
    }
    return plan;
}

// The checks every entry point makes: one call at a time, an image that is usable, and a registry that is complete (unless a test
// asked for placeholders).
p6_result gate(const Options &options, p6_error *error)
{
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (currprefs.cs_cdtvcd && session.config.machine != P6_MACHINE_A500 && session.config.machine != p6_bridge::kCdtvMachine)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "private CDTV state requires its A500 test owner");
    uint8_t cartridge[32];
    if (p6_bridge::hrtmon_bound() && !p6_bridge::hrtmon_identity(cartridge))
        return p6_fail(error, P6_ERROR_BAD_STATE, "HRTMon immutable owner is invalid");
    if (p6_bridge::ar_bound() && !p6_bridge::ar_identity(cartridge))
        return p6_fail(error, P6_ERROR_BAD_STATE, "Action Replay immutable owner is invalid");
    char missing[160];
    if (!options.allow_placeholders && !complete(missing, sizeof missing)) {
        char text[P6_ERROR_TEXT_CAPACITY];
        snprintf(text, sizeof text, "native state is not implemented for: %s", missing);
        return p6_fail(error, P6_ERROR_UNSUPPORTED, text);
    }
    if (!options.allow_placeholders && !kReleased)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "native state is not released");
    return P6_OK;
}

void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i)); }
void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; ++i) p[i] = uint8_t(v >> (8 * i)); }

// Hands the offset of a field in the chunk body to Options::on_field as an offset in the whole state.
struct FieldRelay {
    const Options *options;
    uint32_t chunk;
    uint64_t body_at;
};

void relay_field(void *context, uint64_t offset, uint64_t size, const char *name)
{
    const FieldRelay *relay = static_cast<const FieldRelay *>(context);
    relay->options->on_field(relay->options->on_field_context, relay->chunk, relay->body_at + offset, size, name);
}

p6_result refused(p6_error *error, const char *chunk, const char *why)
{
    char text[P6_ERROR_TEXT_CAPACITY];
    snprintf(text, sizeof text, "state refused: chunk %s: %s", chunk, why);
    return p6_fail(error, P6_ERROR_CORRUPT_STATE, text);
}

}  // namespace

p6_result bound(uint64_t *bytes, const Options &options, p6_error *error)
{
    if (const p6_result gated = gate(options, error)) return gated;
    if (!bytes) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "bytes is null");
    const Plan plan = make_plan();
    if (!plan.valid) return p6_fail(error, P6_ERROR_INTERNAL, "state measurement failed");
    *bytes = plan.total;
    return P6_OK;
}

p6_result save(void *buffer, uint64_t capacity, uint64_t *written, const Options &options, p6_error *error)
{
    if (written) *written = 0;
    if (const p6_result gated = gate(options, error)) return gated;
    if (!buffer || !written) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "buffer or written is null");
    // A state is a unit boundary of a machine that has run its reset head. Before the first unit the machine is a configuration, a
    // firmware and some media: the reset head has not built the CPU's tables or the scheduler's handlers, and the chipset, the audio and the
    // input are not those of a power-on yet (docs/adr/0009-state-format.md, point 6).
    if (!session.started)
        return p6_fail(error, P6_ERROR_BAD_STATE, "no state before the first unit: run p6_run_frame once");
    const Plan plan = make_plan();
    if (!plan.valid) return p6_fail(error, P6_ERROR_INTERNAL, "state measurement failed");
    uint8_t *out = static_cast<uint8_t *>(buffer);
    if (capacity < kHeaderSize + kChunkHeaderSize)
        return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than a state");

    // The body of each chunk is written after the header and its own 16 bytes, then the chunk header is filled in with the length it
    // came to. Nothing is written outside `capacity`: the archive refuses first.
    uint64_t at = kHeaderSize;
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        if (capacity - at < kChunkHeaderSize)
            return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than the state");
        uint64_t body = 0;
        if (chunk.visit) {
            Archive archive = Archive::saver(out + at + kChunkHeaderSize, capacity - at - kChunkHeaderSize);
            FieldRelay relay = {&options, i, at + kChunkHeaderSize};
            if (options.on_field) archive.on_field(relay_field, &relay);
            chunk.visit(archive);
            if (!archive.ok()) {
                // A buffer that could have held the chunk is not the problem: the machine is in a state that cannot be saved (an exception in
                // flight, an event that a state cannot name).
                const bool fits = capacity - at - kChunkHeaderSize >= plan.bounds[i];
                char text[P6_ERROR_TEXT_CAPACITY];
                snprintf(text, sizeof text, "chunk %s could not be saved: %s", chunk.name, archive.why());
                return p6_fail(error, fits ? P6_ERROR_BAD_STATE : P6_ERROR_CAPACITY, text);
            }
            body = archive.position();
        }
        memcpy(out + at, chunk.tag, 4);
        put32(out + at + 4, chunk.visit ? chunk.version : 0);
        put64(out + at + 8, body);
        at += kChunkHeaderSize + body;
    }
    seal(out, at - kHeaderSize, chunk_count(), current_identity(), session.unit_index, session.master_ticks);
    *written = at;
    return P6_OK;
}

p6_result load(const void *buffer, uint64_t bytes, const Options &options, p6_error *error)
{
    if (const p6_result gated = gate(options, error)) return gated;
    if (!buffer && bytes) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "buffer is null");
    const uint8_t *data = static_cast<const uint8_t *>(buffer);

    // (1)-(6) and the chunk table: the envelope, against what this build and this image are.
    const Plan plan = make_plan();
    if (!plan.valid) return p6_fail(error, P6_ERROR_INTERNAL, "state measurement failed");
    ChunkView views[kMaxChunkCount];
    Refusal refusal = {};
    uint64_t unit_index = 0, master_ticks = 0;
    // The identity the state has to match: this image, except that its manifest may have grown since the state was taken (append-only: a state names only media
    // inside the prefix it was taken with, and the prefix has to be the one this image starts with).
    Identity expected = current_identity();
    uint32_t state_media = session.media_count;
    if (uint32_t prefix = 0; recognise_manifest_prefix(data, bytes, &prefix)) {
        manifest_prefix_hash(prefix, expected.media_manifest_hash);
        state_media = prefix;
    }
    if (!validate(data, bytes, expected, plan.total - kHeaderSize, plan.specs, chunk_count(), plan.bounds, views, &unit_index, &master_ticks, &refusal)) {
        char text[P6_ERROR_TEXT_CAPACITY];
        snprintf(text, sizeof text, "state refused: %s: %s", refusal.field, refusal.message);
        return p6_fail(error, refusal.code, text);
    }

    VisitContext context = {unit_index, master_ticks, {}, -1, state_media};

    // (7) Every body, read exactly as it will be applied and not stored anywhere.
    const ContextScope scope(&context);
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        if (!chunk.visit) continue;      // a placeholder has bound 0, so the envelope has already refused a body
        Archive archive = Archive::validator(views[i].body, views[i].length);
        chunk.visit(archive);
        archive.finish();
        if (!archive.ok()) return refused(error, chunk.name, archive.why());
    }

    // (8) What the bodies say about the media, against the manifest the identity has already compared (the registered media, which the image has whether or not it
    // has started): the drives of the live machine are not consulted, so a state is loaded whatever they hold now. Last of the refusals: the next step starts to
    // mutate the image.
    const auto chosen = [&options](uint32_t i) { return !options.only_chunks || (options.only_chunks >> i & 1); };
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        if (!chunk.visit || !chunk.manifest || !chosen(i)) continue;
        if (const char *why = chunk.manifest(views[i].body, views[i].length, context.media)) {
            char text[P6_ERROR_TEXT_CAPACITY];
            snprintf(text, sizeof text, "state refused: chunk %s: %s", chunk.name, why);
            return p6_fail(error, P6_ERROR_INCOMPATIBLE, text);
        }
    }

    // Runtime ownership is separate from candidate validation. Refuse a foreign
    // stream before reset/Apply; the phase getter needs no initialized CDA owner
    // when there are no extra streams, including a cold destination.
    if (currprefs.cs_cdtvcd || session.config.machine == p6_bridge::kCd32Machine) {
        int32_t phase[p6_cda::kPhaseWords];
        if (!p6_cd::device_phase_get(phase, p6_cda::kPhaseWords))
            return p6_fail(error, P6_ERROR_BAD_STATE, "CD32 audio destination has an unsupported or foreign stream");
    }
    // (9) Nothing can refuse any more. An image that has not started has not run its reset head, which is what builds the CPU's dispatch
    // tables and the scheduler's handlers (build_cpufunctbl, init_eventtab): the head runs first, exactly as p6_run_frame runs it, and every
    // chunk then overwrites what it left. A failure of the head, like a failure of a visitor below, is a defect and not of the state: say so and
    // stop the image.
    if (!session.started) {
        const bool head_ran = p6_bridge::run_reset_head();
        if (!head_ran || options.fail_head) {
            session.state = State::Faulted;
            return p6_fail(error, P6_ERROR_INTERNAL, "the reset head did not run before the state was applied; the image is faulted");
        }
    }
    for (uint32_t i = 0; i < chunk_count(); ++i) {
        const Chunk &chunk = active_chunk(i);
        if (!chunk.visit || !chosen(i)) continue;
        Archive archive = Archive::applier(views[i].body, views[i].length);
        chunk.visit(archive);
        archive.finish();
        if (options.fail_apply) archive.fail("a failure injected by a test");
        if (!archive.ok()) {
            session.state = State::Faulted;
            return p6_fail(error, P6_ERROR_INTERNAL, "a chunk failed to apply after it was validated; the image is faulted");
        }
    }
    for (uint32_t i = 0; i < chunk_count(); ++i)
        if (active_chunk(i).visit && active_chunk(i).rebuild && chosen(i)) active_chunk(i).rebuild();
    // The samples this image produced and has not handed over are its own history, not the state's: the first unit after the load hands over what the saved
    // machine's next unit did. At a boundary the queue is empty (every unit drains it) except in an image that had not started, where the reset head that has just
    // run left its samples. The PCM transport is not state (docs/02-contracts.md); a sticky transport error stays for the next unit to report.
    static int16_t discarded[P6_PCM_STAGE_FRAMES * 2];
    for (size_t frames = 1; frames;)
        if (p6_pcm_read(discarded, P6_PCM_STAGE_FRAMES, &frames) != P6_PCM_OK) break;
    // cycles_seen follows the scheduler's counter, which is where a boundary has it: P6SC may have put currcycle back and the reset head of an image
    // that had not started restarted it. The ticks of that head are not the caller's: the header's counters replace them when P6BR is applied.
    session.cycles_seen = int64_t(currcycle);
    return P6_OK;
}

}  // namespace p6_state

// The capability is a fact about the image, not a promise: this initialiser exists only where this file is linked, which is where the functions below are.
namespace { [[maybe_unused]] const bool g_announced = (p6_bridge::state_linked = p6_state::kReleased, true); }

extern "C" p6_result p6_state_bound(uint64_t *bytes, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::bound(bytes, p6_state::Options{}, error);
}

extern "C" p6_result p6_state_save(void *buffer, uint64_t capacity, uint64_t *written, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::save(buffer, capacity, written, p6_state::Options{}, error);
}

extern "C" p6_result p6_state_load(const void *buffer, uint64_t bytes, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    return p6_state::load(buffer, bytes, p6_state::Options{}, error);
}
