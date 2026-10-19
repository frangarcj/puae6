// The hard-file section of the disk chunk P6DK (T29c, docs/adr/0009-state-format.md): the state of the UAE controller (uaehf.device, the UAEFS board, the trap area and the EXTER
// queue) and the overlay of the writes of the guest to its hard files. It exists only while a hard file is mounted (p6_bridge::hard_file_count() > 0), after the bodies of the disks and
// before the write overlay of the floppies, so that a machine without a hard file keeps the chunk it always had, byte for byte.
//
// Why not upstream's save_filesys: it writes host paths (the root directory of the unit, the file system image) and names helpers of savestate.cpp that the profile does not
// build, and it rebuilds the units by mounting them again. Here the units are rebuilt from the mount configuration by every load, like the rest of the machine, and the section
// carries what the guest and the boot code did to them since. Everything is values and guest bytes: no pointer of the host, no path, no string.
//
// Layout (all little endian; N is the number of hard files, 1 to 4, the same as the live image):
//   u32 N, then N times the 32 bytes of the content id of the base image                       (hdf_matches_manifest compares them with the registered ones BEFORE the machine is touched)
//   the controller:
//     17 words   the globals of filesys.cpp (patch 0181, p6_filesys_state_get)
//     N times    43 words of a UnitInfo, u32 size and the bytes of the file system handler the RDB brought and the guest has not taken yet (at most 262144),
//                6 words of hardfpd, u32 count of unfinished requests and per request u8 slot, request, type, data and the 48 bytes of its IORequest
//     5 words and the 65536 bytes of the trap area (patch 0181, p6_rtarea_state_get), 4 words and the 65536 bytes of the UAEFS board (patch 0182, p6_uaefs_state_get)
//     the autoconfig chain (visit_expansion with no Fast RAM board) when the machine has no Fast RAM: with one, P6RM carries it, then the 11520 bytes of the autoconfig image of the card the
//     chain is at beyond its first 256 (patch 0221, p6_expamem_image_get): the DiagArea and the code the UAEFS board copies into its memory when the guest configures it
//     u32 count and the values of the EXTER queue (patch 0180, p6_n2a_state_get)
//   the overlay, for each hard file: u32 count of dirty chunks, then per chunk (in ascending order) its u32 index and its bytes (4096, less for the last chunk of an image that is not a
//   multiple of it). The base is never in the state: a chunk that is not named is the base's.
// The bound counts every chunk of every image as dirty (the image size plus four bytes a chunk), as it must be an upper bound that does not change until the image is unloaded.
//
// On Gayle's IDE (T56, p6_config.hdf_controller = IDE: bridge/config.h ide_attached) the controller is another one and so is the middle of the section; the identity and the overlay are the same:
//   u32 N and the content ids as above
//   the controller: 4 words of Gayle (gayle.cpp, patch 0321: IDE interrupt status and enable, the ID sequence, the splitter), then for each of the four IDE slots (master and slave of the
//     channel, then the pair behind the splitter; a slot without a drive has its registers all the same) 43 words of the drive (ide.cpp, patch 0320: registers, the state of the
//     transfer in hand, the geometry the guest set), u32 count of the bytes of its sector buffer that are in hand (at most 131584) and the bytes;
//     the autoconfig chain (visit_expansion with no Fast RAM board) when the machine has no Fast RAM: with one, P6RM carries it. No UAEFS board, no trap area, no EXTER queue: they do not exist.
//   the overlay, for each hard file, as above.
#include "state.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#include "config.h"
#include "media.h"
#include "session.h"
#include "blob_io.h"

extern bool p6_n2a_state_get(uint32_t *values, unsigned capacity, unsigned *count);          // native2amiga.cpp 0180
extern bool p6_n2a_state_set(const uint32_t *values, unsigned count);
extern bool p6_filesys_state_get(uint32_t *g, unsigned n);                                  // filesys.cpp 0181
extern bool p6_filesys_state_set(const uint32_t *g, unsigned n);
extern bool p6_filesys_unit_state_get(unsigned unit, uint32_t *w, unsigned n, uint8_t *store, uint32_t *store_size);
extern bool p6_filesys_unit_state_set(unsigned unit, const uint32_t *w, unsigned n, const uint8_t *store, uint32_t store_size);
extern bool p6_hardfile_state_get(unsigned unit, uint32_t *w, unsigned n, uint32_t *request, uint8_t *iobuf);       // hardfile.cpp 0181
extern bool p6_hardfile_state_set(unsigned unit, const uint32_t *w, unsigned n, const uint32_t *request, const uint8_t *iobuf);
extern bool p6_rtarea_state_get(uint32_t *w, unsigned n, uint8_t *bytes);                    // autoconf.cpp 0181
extern bool p6_rtarea_state_set(const uint32_t *w, unsigned n, const uint8_t *bytes);
extern bool p6_uaefs_state_get(uint32_t *w, unsigned n, uint8_t *bytes);                     // expansion.cpp 0182
extern bool p6_uaefs_state_set(const uint32_t *w, unsigned n, const uint8_t *bytes);
extern bool p6_expamem_image_get(uint8_t *bytes, unsigned n);                                // expansion.cpp 0221
extern bool p6_expamem_image_set(const uint8_t *bytes, unsigned n);

#include "ide_units.h"

using p6_session::session;

namespace p6_state {
namespace {

// The sizes the engine accessors work with (the comments of the patches list the words).
constexpr uint32_t kGlobalWords = 17, kUnitWords = 43, kHardfileWords = 6, kRtareaWords = 5, kUaefsWords = 4;
constexpr uint32_t kRequests = 50, kIobuf = 48, kRtareaBytes = 65536, kUaefsBytes = 65536, kStoreMax = 262144, kQueueMax = 299, kUnits = 50, kExpamemBytes = 0x3000 - 256;

uint8_t g_rtarea[kRtareaBytes], g_uaefs[kUaefsBytes], g_store[kStoreMax], g_expamem[kExpamemBytes];       // one machine at a time: scratch for Save and Apply
uint8_t g_chunk[P6_HDF_CHUNK];

bool measuring(const Archive &a) { return a.mode() == Mode::Measure; }

// The manifest index of the j-th hard file (the order of the p6_load array is the order of the units).
int hdf_index(uint32_t j)
{
    uint32_t seen = 0;
    for (uint32_t i = 0; i < session.media_count && i < P6_MAX_MEDIA; ++i)
        if (session.media_type[i] == P6_MEDIA_HDF && seen++ == j) return int(i);
    return -1;
}

void word_run(Archive &a, uint32_t *w, uint32_t n, const char *name)
{
    for (uint32_t i = 0; i < n; ++i) w[i] = a.val<uint32_t>(w[i], name);
}

// Values of the EXTER queue come in messages: the first value of each says how many follow (the same table as native2amiga.cpp, patch 0180).
uint32_t message_length(uint32_t cmd)
{
    switch (cmd) {
    case 0: case 1: case 4: return 3;
    case 2: case 3: case 6: return 2;
    default: return 0;
    }
}

void visit_identity(Archive &a, uint32_t files)
{
    uint32_t n = files;
    a.val<uint32_t>(n, files, files, "hdf.files");
    for (uint32_t j = 0; j < files && a.ok(); ++j) {
        uint8_t id[P6_CONTENT_ID_SIZE] = {};
        const int index = hdf_index(j);
        if (!measuring(a) && index >= 0) memcpy(id, session.media_content_id[index], sizeof id);
        a.bytes(id, sizeof id, "hdf.content_id");     // compared by hdf_matches_manifest, before anything is applied
    }
}

void visit_globals(Archive &a)
{
    uint32_t g[kGlobalWords] = {};
    if (a.mode() == Mode::Save) a.require(p6_filesys_state_get(g, kGlobalWords), "the file system of the hard files is not in a state that can be saved");
    word_run(a, g, kGlobalWords, "hdf.filesys_global");
    if (measuring(a)) return;
    const int32_t automount = int32_t(g[2]), walk = int32_t(g[4]);
    a.require(automount >= -2 && automount < int32_t(kUnits) && g[3] <= 1 && walk >= 0 && walk <= int32_t(kUnits) && g[5] <= 1 && (g[5] != 0) == (g[3] > 0),
              "hdf: the globals of the file system are outside what the machine can have");
    if (a.applying() && a.ok()) a.require(p6_filesys_state_set(g, kGlobalWords), "hdf: the globals of the file system do not fit the machine");
}

void visit_unit(Archive &a, uint32_t unit)
{
    uint32_t w[kUnitWords] = {};
    uint32_t size = 0;
    if (a.mode() == Mode::Save) a.require(p6_filesys_unit_state_get(unit, w, kUnitWords, g_store, &size), "a hard file unit of the file system is not in a state that can be saved");
    word_run(a, w, kUnitWords, "hdf.unit_word");
    size = a.val<uint32_t>(size, 0, kStoreMax, "hdf.fs_store_size");
    a.bytes(g_store, measuring(a) ? kStoreMax : size, "hdf.fs_store");
    if (!measuring(a)) {
        a.require(size == w[8] && w[4] <= 0x7fffffffu && w[5] <= 0x7fffffffu && w[6] <= 0x7fffffffu && w[7] <= 0x7fffffffu, "hdf: a unit of the file system is outside what the machine can have");
        if (a.applying() && a.ok()) a.require(p6_filesys_unit_state_set(unit, w, kUnitWords, g_store, size), "hdf: a unit of the file system does not fit the machine");
    }
}

void visit_hardfile(Archive &a, uint32_t unit)
{
    uint32_t w[kHardfileWords] = {};
    uint32_t request[kRequests * 3] = {};
    static uint8_t iobuf[kRequests * kIobuf];
    memset(iobuf, 0, sizeof iobuf);
    if (a.mode() == Mode::Save) a.require(p6_hardfile_state_get(unit, w, kHardfileWords, request, iobuf), "a uaehf.device unit is not in a state that can be saved");
    word_run(a, w, kHardfileWords, "hdf.device_word");
    uint32_t used = 0;
    for (uint32_t i = 0; i < kRequests; ++i) used += request[3 * i] != 0;
    const uint32_t count = a.count(used, kRequests, "hdf.device_requests");
    if (a.reading()) { memset(request, 0, sizeof request); memset(iobuf, 0, sizeof iobuf); }
    uint32_t slot = 0;                       // Save: the next slot to look at; reading: the first slot a request may take
    for (uint32_t k = 0; k < count && a.ok(); ++k) {
        uint32_t at = slot;
        if (a.mode() == Mode::Save) {
            while (at < kRequests && !request[3 * at]) ++at;
            a.require(at < kRequests, "a uaehf.device request vanished while it was saved");
        }
        at = a.val<uint8_t>(uint8_t(at), 0, uint8_t(kRequests - 1), "hdf.device_slot");
        if (!measuring(a)) a.require(at >= slot || (a.mode() == Mode::Save), "hdf: the requests of a unit are not in order");
        if (!a.ok()) break;
        uint32_t *r = request + 3 * (at < kRequests ? at : 0);
        r[0] = a.val<uint32_t>(r[0], "hdf.device_request");
        r[1] = a.val<uint32_t>(r[1], "hdf.device_type");
        r[2] = a.val<uint32_t>(r[2], "hdf.device_data");
        a.bytes(iobuf + kIobuf * (at < kRequests ? at : 0), kIobuf, "hdf.device_iobuf");
        if (!measuring(a)) {
            a.require(r[0] != 0 && (r[1] == 1 || r[1] == 10), "hdf: a request of a unit is outside what the machine can have");
            for (uint32_t j = 0; j < at && a.ok(); ++j) a.require(request[3 * j] != r[0], "hdf: a request is named twice");
        }
        slot = at + 1;
    }
    if (!measuring(a)) {
        a.require(w[3] <= 1 && w[4] <= 1 && w[5] <= 1, "hdf: a unit of uaehf.device is outside what the machine can have");
        if (a.applying() && a.ok()) a.require(p6_hardfile_state_set(unit, w, kHardfileWords, request, iobuf), "hdf: a unit of uaehf.device does not fit the machine");
    }
}

void visit_trap_area(Archive &a)
{
    uint32_t w[kRtareaWords] = {};
    if (a.mode() == Mode::Save) a.require(p6_rtarea_state_get(w, kRtareaWords, g_rtarea), "the trap area is not in a state that can be saved");
    word_run(a, w, kRtareaWords, "hdf.rtarea_word");
    a.bytes(g_rtarea, kRtareaBytes, "hdf.rtarea");
    if (measuring(a)) return;
    a.require(w[0] < kRtareaBytes && w[1] < kRtareaBytes && w[2] < kRtareaBytes && w[3] <= 1 && w[4] <= 1, "hdf: the cursors of the trap area are outside what the machine can have");
    if (a.applying() && a.ok()) a.require(p6_rtarea_state_set(w, kRtareaWords, g_rtarea), "hdf: the trap area does not fit the machine");
}

void visit_board(Archive &a)
{
    uint32_t w[kUaefsWords] = {};
    if (a.mode() == Mode::Save) a.require(p6_uaefs_state_get(w, kUaefsWords, g_uaefs), "the UAEFS board is not in a state that can be saved");
    word_run(a, w, kUaefsWords, "hdf.board_word");
    a.bytes(g_uaefs, kUaefsBytes, "hdf.board");
    if (measuring(a)) return;
    const uint32_t start = w[0];
    const bool shut = start == 0xffffffffu;
    a.require(w[1] <= 1 && w[2] <= w[1] && (!w[2] || (!(start & 0xffff) && ((start >= 0x200000 && start < 0xa00000) || (start >= 0xe90000 && start < 0xf00000)))) && (!w[1] || !start || !(start & 0xffff) || shut) &&
              (w[1] || !start || shut) && (w[1] ? (w[3] >= 0x4000 && w[3] <= kUaefsBytes) : w[3] == 0), "hdf: the UAEFS board is outside what a board can be");
    if (a.applying() && a.ok()) a.require(p6_uaefs_state_set(w, kUaefsWords, g_uaefs), "hdf: the UAEFS board does not fit the machine");
}

// The autoconfig image of the card the chain is at, beyond the 256 bytes P6RM carries (patch 0221): any bytes are a possible image.
void visit_expamem_image(Archive &a)
{
    if (a.mode() == Mode::Save) a.require(p6_expamem_image_get(g_expamem, kExpamemBytes), "the autoconfig image is not in a state that can be saved");
    a.bytes(g_expamem, kExpamemBytes, "hdf.expamem");
    if (a.applying() && a.ok()) a.require(p6_expamem_image_set(g_expamem, kExpamemBytes), "hdf: the autoconfig image does not fit the machine");
}

void visit_queue(Archive &a)
{
    uint32_t values[kQueueMax] = {};
    uint32_t count = 0;
    if (a.mode() == Mode::Save) {
        unsigned n = 0;
        a.require(p6_n2a_state_get(values, kQueueMax, &n), "the EXTER queue holds a message that cannot be saved");
        count = n;
    }
    count = a.count(count, kQueueMax, "hdf.queue_count");
    for (uint32_t i = 0; i < count && a.ok(); ++i) values[i] = a.val<uint32_t>(values[i], "hdf.queue_value");
    if (measuring(a)) return;
    for (uint32_t at = 0; at < count && a.ok();) {
        const uint32_t length = message_length(values[at]);
        a.require(length && length <= count - at, "hdf: the EXTER queue holds a message the producers cannot write");
        at += length;
    }
    if (a.applying() && a.ok()) a.require(p6_n2a_state_set(values, count), "hdf: the EXTER queue does not fit the machine");
}

// The overlay of the j-th hard file: the chunks written since the load, nothing of the base.
void visit_overlay_of(Archive &a, uint32_t j)
{
    const int index = hdf_index(j);
    a.require(index >= 0, "hdf: the hard file is not in the manifest");
    if (!a.ok()) return;
    char id[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(session.media_content_id[index], id);
    uint64_t bytes = 0, chunks = 0, dirty = 0;
    uint32_t kind = 0;
    a.require(p6_blob_size(id, &bytes, &kind) == P6_BLOB_OK && kind == P6_BLOB_HDF && p6_blob_hdf_status(id, &chunks, &dirty) == P6_BLOB_OK && chunks <= 0xffffffffu,
              "hdf: the hard file is not registered");
    if (!a.ok()) return;
    uint32_t count = uint32_t(dirty);
    const uint32_t n = a.count(count, uint32_t(chunks), "hdf.dirty_chunks");
    if (measuring(a)) { a.bytes(nullptr, 4 * chunks + bytes, "hdf.chunks"); return; }       // the bound: every chunk dirty
    if (a.applying()) a.require(p6_blob_hdf_state_clear(id) == P6_BLOB_OK, "hdf: the overlay cannot be cleared");
    uint64_t next = 0;                       // Save: where to look for the next dirty chunk; reading: the first index the next chunk may have
    for (uint32_t k = 0; k < n && a.ok(); ++k) {
        uint64_t chunk = next;
        if (a.mode() == Mode::Save) a.require(p6_blob_hdf_next_dirty(id, next, &chunk) == P6_BLOB_OK && chunk < chunks, "the overlay has fewer dirty chunks than it counted");
        chunk = a.val<uint32_t>(uint32_t(chunk), 0, uint32_t(chunks - 1), "hdf.chunk_index");
        if (!a.ok()) break;
        a.require(chunk >= next, "hdf: the dirty chunks are not in ascending order");
        size_t length = 0;
        a.require(p6_blob_hdf_chunk_bytes(id, chunk, &length) == P6_BLOB_OK, "hdf: a chunk beyond the image");
        if (!a.ok()) break;
        const void *data = nullptr;
        if (a.mode() == Mode::Save) a.require(p6_blob_hdf_chunk_data(id, chunk, &data) == P6_BLOB_OK, "a dirty chunk has no bytes");
        if (a.mode() == Mode::Save && a.ok()) a.bytes(const_cast<void *>(data), length, "hdf.chunk");
        else a.bytes(g_chunk, length, "hdf.chunk");
        if (a.applying() && a.ok()) a.require(p6_blob_hdf_state_put(id, chunk, g_chunk) == P6_BLOB_OK, "hdf: a chunk cannot be put in the overlay");
        next = chunk + 1;
    }
    if (a.mode() == Mode::Save && a.ok()) {
        uint64_t beyond = 0;
        a.require(p6_blob_hdf_next_dirty(id, next, &beyond) == P6_BLOB_OK && beyond >= chunks, "the overlay has more dirty chunks than it counted");
    }
}

// The IDE port of Gayle and its four drive slots (the section of a machine on Gayle's IDE, see the layout at the top).
void visit_ide(Archive &a)
{
    static uint32_t drive[4][kIdeWords];
    static uint8_t buffer[4][kIdeSecbufMax];
    uint32_t chip[kGayleWords] = {}, length[4] = {};
    memset(drive, 0, sizeof drive);
    if (a.mode() == Mode::Save) {
        a.require(p6_gayle_state_get(chip, kGayleWords), "Gayle is not in a state that can be saved");
        for (unsigned unit = 0; unit < 4 && a.ok(); ++unit)
            a.require(p6_gayle_drive_state_get(unit, drive[unit], kIdeWords, buffer[unit], &length[unit]), "an IDE drive is not in a state that can be saved");
    }
    word_run(a, chip, kGayleWords, "ide.gayle_word");
    for (unsigned unit = 0; unit < 4 && a.ok(); ++unit) {
        word_run(a, drive[unit], kIdeWords, "ide.drive_word");
        length[unit] = a.val<uint32_t>(length[unit], 0, kIdeSecbufMax, "ide.buffer_length");
        a.bytes(buffer[unit], measuring(a) ? kIdeSecbufMax : length[unit], "ide.buffer");
    }
    if (measuring(a) || !a.ok()) return;
    // What the machine can have, from the number of hard files alone (unit j is slot j): the engine's own pure functions judge the values, so that applying cannot fail.
    const uint32_t files = p6_bridge::ide_attached(session.config, p6_bridge::hard_file_count()) ? p6_bridge::hard_file_count() : 0;
    a.require(p6_gayle_words_valid(chip, kGayleWords, files > 2), "ide: the registers of Gayle are outside what the chip can have");
    for (unsigned unit = 0; unit < 4 && a.ok(); ++unit) {
        const bool present = unit < files;
        a.require(p6_ide_words_valid(drive[unit], kIdeWords, present), "ide: a drive is outside what a drive can have");
        a.require(length[unit] == p6_ide_live_bytes(drive[unit], present), "ide: the sector buffer is not the one the transfer in hand says");
    }
    // The two drives of a channel are selected together.
    a.require(drive[0][30] == drive[1][30] && drive[2][30] == drive[3][30], "ide: the drives of a channel are not on the same select line");
    if (a.applying() && a.ok()) {
        a.require(p6_gayle_state_set(chip, kGayleWords), "ide: the registers of Gayle do not fit the machine");
        for (unsigned unit = 0; unit < 4 && a.ok(); ++unit)
            a.require(p6_gayle_drive_state_set(unit, drive[unit], kIdeWords, buffer[unit], length[unit]), "ide: a drive does not fit the machine");
    }
}

}  // namespace

void visit_hdf(Archive &a)
{
    const uint32_t files = p6_bridge::hard_file_count();
    const bool motherboard = p6_bridge::motherboard_ide(session.config);
    if (!files && !motherboard) return;
    visit_identity(a, files);
    if (!a.ok()) return;
    if (p6_bridge::ide_attached(session.config, files)) {
        visit_ide(a);
        if (a.ok() && p6_bridge::resolve_memory(session.config).fast_kib == 0) visit_expansion(a, 0);
        for (uint32_t j = 0; j < files && a.ok(); ++j) visit_overlay_of(a, j);
        return;
    }
    if (motherboard) {
        visit_ide(a);
        if (!files) {
            if (a.ok() && p6_bridge::resolve_memory(session.config).fast_kib == 0) visit_expansion(a, 0);
            return;
        }
    }
    visit_globals(a);
    for (uint32_t j = 0; j < files && a.ok(); ++j) {
        visit_unit(a, j);
        if (a.ok()) visit_hardfile(a, j);
    }
    if (a.ok()) visit_trap_area(a);
    if (a.ok()) visit_board(a);
    if (a.ok() && p6_bridge::resolve_memory(session.config).fast_kib == 0) visit_expansion(a, 0);
    if (a.ok()) visit_expamem_image(a);          // after the chain: putting the card's first 256 bytes back rebuilds the buffer (expamem_next)
    if (a.ok()) visit_queue(a);
    for (uint32_t j = 0; j < files && a.ok(); ++j) visit_overlay_of(a, j);
}

// The hard files the state was taken with are the ones the image registered: same number, same content id, in the same order. The section starts with them, so this reads the
// first bytes of what follows the bodies of the disks. The manifest hash of the header already says as much; this is the check of the body itself, before anything is applied.
const char *hdf_matches_manifest(const uint8_t *section, uint64_t length)
{
    static char text[112];
    const uint32_t files = p6_bridge::hard_file_count();
    if (!files) return nullptr;
    if (length < 4 + uint64_t(files) * P6_CONTENT_ID_SIZE) return "the disk chunk ends inside the identity of its hard files";
    const uint32_t n = uint32_t(section[0]) | uint32_t(section[1]) << 8 | uint32_t(section[2]) << 16 | uint32_t(section[3]) << 24;
    if (n != files) {
        snprintf(text, sizeof text, "the state was taken with %u hard files and this image has %u", unsigned(n), unsigned(files));
        return text;
    }
    for (uint32_t j = 0; j < files; ++j) {
        const int index = hdf_index(j);
        if (index < 0 || memcmp(section + 4 + size_t(j) * P6_CONTENT_ID_SIZE, session.media_content_id[index], P6_CONTENT_ID_SIZE)) {
            snprintf(text, sizeof text, "hard file %u: the state was taken with another base image (content id)", unsigned(j));
            return text;
        }
    }
    return nullptr;
}

}  // namespace p6_state
