// Identity, validation and registration of the media passed to p6_load.
//
// The engine has no error path for the size or the format of an ADF, and a crafted image crashes it (research of
// T12: a 901120-byte file with the extended-ADF magic and a track count of 0xFFFF overruns the drive's track table),
// so this file is the only gatekeeper: an image reaches the engine only after it passed every check below.
#include "media.h"
#include "ide_units.h"
#include "error_util.h"
#include "session.h"
#include "sha256.h"
#include "blob_io.h"
#include "overlay.h"
#include "trackdisk.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "zfile.h"
#include "filesys.h"

#include <string.h>

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace p6_bridge {

void media_blob_id(const uint8_t content_id[P6_CONTENT_ID_SIZE], char out[kMediaBlobIdBytes])
{
    static const char digits[] = "0123456789abcdef";
    memcpy(out, "media-", 6);
    for (size_t i = 0; i < P6_CONTENT_ID_SIZE; ++i) {
        out[6 + 2 * i] = digits[content_id[i] >> 4];
        out[7 + 2 * i] = digits[content_id[i] & 15];
    }
    out[6 + 2 * P6_CONTENT_ID_SIZE] = 0;
}

p6_result validate_media(const p6_media *media, uint32_t count, p6_error *error)
{
    if (count > P6_MAX_MEDIA)
        return p6_fail(error, P6_ERROR_CAPACITY, "too many media");
    if (count && !media)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the media array is null");
    // Cheap checks on every entry first, so that hashing 900 KiB happens only for a list that is otherwise acceptable.
    uint32_t hard_files = 0;
    uint64_t floppy_bytes = 0;
    for (uint32_t i = 0; i < count; ++i) {
        const p6_media &m = media[i];
        if (m.type != P6_MEDIA_ADF && m.type != P6_MEDIA_HDF)
            return p6_fail(error, P6_ERROR_UNSUPPORTED, "only ADF and HDF media are supported");
        if (m.flags != 0)
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "media flags are reserved");
        if (!m.image.data)
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "a medium has no image");
        if (m.type == P6_MEDIA_HDF) {
            // Whatever else the file claims about itself is checked by the engine over the registered bytes (RDB and partition blocks, with bounds); here only
            // what makes it a block device at all.
            if (++hard_files > P6_MAX_HDF)
                return p6_fail(error, P6_ERROR_CAPACITY, "too many hard files");
            if (m.image.bytes < kHdfBlock || m.image.bytes % kHdfBlock != 0)
                return p6_fail(error, P6_ERROR_WRONG_MEDIA, "a hard file must be a whole number of 512-byte blocks, at least one");
        } else {
            // A plain ADF, or a track-level image the engine reads in memory (T57): classified by its bytes, and every number its header gives the engine is checked here.
            const p6_floppy::Verdict verdict = p6_floppy::classify_floppy(static_cast<const uint8_t *>(m.image.data), m.image.bytes);
            if (!verdict.ok)
                return p6_fail(error, P6_ERROR_WRONG_MEDIA, verdict.why);
            floppy_bytes += m.image.bytes;
        }
        for (uint32_t j = 0; j < i; ++j)
            if (!memcmp(media[j].content_id, m.content_id, P6_CONTENT_ID_SIZE))
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "two media share a content id");
    }
    // The floppies together fit what 16 plain disks take: the sealed heap of the Waterbox guest is sized for that, whatever formats they are.
    if (floppy_bytes > p6_floppy::kMaxFloppyBytes)
        return p6_fail(error, P6_ERROR_CAPACITY, "the floppy images together are larger than 16 plain disks");
    for (uint32_t i = 0; i < count; ++i) {
        uint8_t digest[32];
        p6_sha256_hash(media[i].image.data, media[i].image.bytes, digest);
        if (memcmp(digest, media[i].content_id, P6_CONTENT_ID_SIZE))
            return p6_fail(error, P6_ERROR_WRONG_MEDIA, "a content id is not the SHA-256 of its image");
    }
    return P6_OK;
}

uint8_t floppy_format(const p6_media &medium)
{
    return p6_floppy::classify_floppy(static_cast<const uint8_t *>(medium.image.data), medium.image.bytes).format;      // validate_media accepted it
}

p6_result register_media(const p6_media *media, uint32_t count, uint32_t first_index, p6_error *error)
{
    for (uint32_t i = 0; i < count; ++i) {
        char id[kMediaBlobIdBytes];
        media_blob_id(media[i].content_id, id);
        switch (p6_blob_register(id, media[i].type == P6_MEDIA_HDF ? P6_BLOB_HDF : P6_BLOB_ADF, media[i].image.data, size_t(media[i].image.bytes))) {
        case P6_BLOB_OK:
            break;
        case P6_BLOB_CAPACITY:
        case P6_BLOB_NO_MEMORY:
            return p6_fail(error, P6_ERROR_CAPACITY, "the media could not be registered: no room");
        default:
            return p6_fail(error, P6_ERROR_INTERNAL, "a medium could not be registered");
        }
        // With P6_CONFIG_FLOPPY_WRITES a plain ADF is writable over the overlay that p6_load reserved; its number is its index in the manifest. A track-level image (T57) is
        // read only whatever the flag says: the overlay is made of AmigaDOS sectors and raw tracks of the ADF geometry, which those formats do not have.
        if (media[i].type == P6_MEDIA_ADF && floppy_format(media[i]) == p6_floppy::kAdf && (session.config.flags & P6_CONFIG_FLOPPY_WRITES))
            if (p6_blob_overlay_bind(id, first_index + i) != P6_BLOB_OK)
                return p6_fail(error, P6_ERROR_INTERNAL, "a medium could not get its write overlay");
    }
    return P6_OK;
}

bool media_forced_write_protect(const char *id)
{
    return !p6_blob_overlay_writable(id);
}

uint32_t hard_file_count()
{
    const p6_session::Session &s = p6_session::session;
    uint32_t n = 0;
    for (uint32_t i = 0; i < s.media_count && i < P6_MAX_MEDIA; ++i)
        n += s.media_type[i] == P6_MEDIA_HDF;
    return n;
}

uint32_t hard_files_in(const p6_media *media, uint32_t count)
{
    uint32_t n = 0;
    for (uint32_t i = 0; i < count; ++i)
        n += media[i].type == P6_MEDIA_HDF;
    return n;
}

p6_result mount_hard_files(const p6_media *media, uint32_t count, bool ide, p6_error *error)
{
    uint32_t unit = 0;
    for (uint32_t i = 0; i < count; ++i) {
        if (media[i].type != P6_MEDIA_HDF)
            continue;
        const bool rdb = !memcmp(media[i].image.data, "RDSK", 4);      // a disk with its own RDB brings its geometry; any other is one partition
        uaedev_config_info ci;
        uci_set_defaults(&ci, rdb);
        ci.type = UAEDEV_HDF;
        ci.controller_type = ide ? HD_CONTROLLER_TYPE_IDE_AUTO : HD_CONTROLLER_TYPE_UAE;
        ci.readonly = false;                                             // writes go to the overlay of the image (P6_BLOB_HDF), never to the base
        ci.blocksize = int(kHdfBlock);
        media_blob_id(media[i].content_id, ci.rootdir);                  // hdf_open_target looks the image up by this id
        snprintf(ci.devname, sizeof ci.devname, "DH%u", unit);
        ci.controller_unit = int(unit);
        if (ide) {
            // T56: the drives of Gayle's IDE (unit j = master, slave, then the pair behind the splitter) are opened by gayle.cpp on every reset; there is no mount list, no UAEFS board and
            // no trap area behind them.
            if (!p6_gayle_unit(int(unit), &ci))
                return p6_fail(error, P6_ERROR_CAPACITY, "a hard file could not be attached to the IDE");
            ++unit;
            continue;
        }
        // What add_filesys_config does for a unit of the UAE controller, which cfgfile.cpp excludes together with the text configuration around it.
        if (currprefs.mountitems >= MOUNT_CONFIG_SIZE)
            return p6_fail(error, P6_ERROR_CAPACITY, "a hard file could not be mounted");
        uaedev_config_data &mount = currprefs.mountconfig[currprefs.mountitems++];
        mount.configoffset = -1;
        mount.unitnum = -1;
        memcpy(&mount.ci, &ci, sizeof ci);
        ++unit;
    }
    return P6_OK;
}

namespace {

struct MeasuredCrc {
    bool known;
    uint32_t crc;
} g_crc[P6_MAX_MEDIA];       // by manifest index; cleared with the identity

}  // namespace

void remember_identity(const p6_buffer &firmware, const p6_media *media, uint32_t count)
{
    p6_session::Session &s = p6_session::session;
    memset(g_crc, 0, sizeof g_crc);
    p6_sha256_hash(firmware.data, firmware.bytes, s.firmware_sha256);
    s.media_count = count;
    s.floppy_bytes = 0;
    memset(s.media_format, 0, sizeof s.media_format);
    for (uint32_t i = 0; i < count; ++i) {
        memcpy(s.media_content_id[i], media[i].content_id, P6_CONTENT_ID_SIZE);
        s.media_type[i] = uint8_t(media[i].type);
        if (media[i].type == P6_MEDIA_ADF) {
            s.media_format[i] = floppy_format(media[i]);
            s.floppy_bytes += media[i].image.bytes;
        }
    }
}

p6_result append_media(const p6_media &medium, uint32_t *index, p6_error *error)
{
    p6_session::Session &s = p6_session::session;
    if (const p6_result checked = validate_media(&medium, 1, error))
        return checked;
    if (medium.type != P6_MEDIA_ADF)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "only a disk can be appended: a hard file is part of the p6_load list");
    // A disk is its content: the same bytes under another name are the medium that is already registered, and the manifest (and the identity of every state)
    // does not grow for it.
    for (uint32_t i = 0; i < s.media_count; ++i) {
        if (!memcmp(s.media_content_id[i], medium.content_id, P6_CONTENT_ID_SIZE)) {
            *index = i;
            return P6_OK;
        }
    }
    if (s.media_count >= P6_MAX_MEDIA)
        return p6_fail(error, P6_ERROR_CAPACITY, "the media manifest is full");
    if (s.floppy_bytes + medium.image.bytes > p6_floppy::kMaxFloppyBytes)
        return p6_fail(error, P6_ERROR_CAPACITY, "the floppy images together would be larger than 16 plain disks");
    if (const p6_result registered = register_media(&medium, 1, s.media_count, error))
        return registered;               // register_media adds nothing when it fails
    const uint32_t at = s.media_count;
    memcpy(s.media_content_id[at], medium.content_id, P6_CONTENT_ID_SIZE);
    s.media_type[at] = P6_MEDIA_ADF;
    s.media_format[at] = floppy_format(medium);
    s.floppy_bytes += medium.image.bytes;
    g_crc[at] = MeasuredCrc{};           // measured when a state needs it, as for the media of p6_load
    s.media_count = at + 1;
    *index = at;
    return P6_OK;
}

void forget_media()
{
    p6_session::Session &s = p6_session::session;
    memset(s.firmware_sha256, 0, sizeof s.firmware_sha256);
    memset(s.media_content_id, 0, sizeof s.media_content_id);
    memset(s.media_type, 0, sizeof s.media_type);
    memset(s.media_format, 0, sizeof s.media_format);
    s.floppy_bytes = 0;
    memset(g_crc, 0, sizeof g_crc);
    s.media_count = 0;
    s.media_queued = 0;
    for (int32_t &m : s.drive_media) m = -1;
    s.last_sequence = 0;
    s.have_sequence = false;
    s.started = false;
    s.reset_queued = false;
}

namespace {

void set_prefs_media(uint32_t drive, const char *id)
{
    _tcscpy(changed_prefs.floppyslots[drive].df, id);
    _tcscpy(currprefs.floppyslots[drive].df, id);
    changed_prefs.floppyslots[drive].forcedwriteprotect = media_forced_write_protect(id);   // used by the engine's own change detection
}

}  // namespace

void apply_boot_media()
{
    p6_session::Session &s = p6_session::session;
    for (uint32_t i = 0; i < s.media_queued; ++i) {
        const p6_session::Session::MediaEvent &e = s.media_queue[i];
        char id[kMediaBlobIdBytes];
        if (e.type == P6_MEDIA_EVENT_MOUNT) {
            media_blob_id(s.media_content_id[e.media_index], id);
            set_prefs_media(e.drive, id);
            s.drive_media[e.drive] = int32_t(e.media_index);
        } else {
            set_prefs_media(e.drive, "");
            s.drive_media[e.drive] = -1;
        }
    }
    s.media_queued = 0;
    s.started = true;
}

void apply_media_events()
{
    p6_session::Session &s = p6_session::session;
    for (uint32_t i = 0; i < s.media_queued; ++i) {
        const p6_session::Session::MediaEvent &e = s.media_queue[i];
        if (e.type == P6_MEDIA_EVENT_MOUNT) {
            if (s.drive_media[e.drive] == int32_t(e.media_index))
                continue;                            // already the wanted disk: the engine would ignore it too
            char id[kMediaBlobIdBytes];
            media_blob_id(s.media_content_id[e.media_index], id);
            // A reset re-inserts what changed_prefs names, so it must name the new disk before the delayed swap lands;
            // currprefs is left alone because disk_insert ignores a name that is already there.
            _tcscpy(changed_prefs.floppyslots[e.drive].df, id);
            const bool protect = media_forced_write_protect(id);
            changed_prefs.floppyslots[e.drive].forcedwriteprotect = protect;
            disk_insert(int(e.drive), id, protect);
            s.drive_media[e.drive] = int32_t(e.media_index);
        } else if (s.drive_media[e.drive] >= 0) {
            disk_eject(int(e.drive));                // immediate; also forgets a swap that has not landed yet
            s.drive_media[e.drive] = -1;
        }
    }
    s.media_queued = 0;
}

bool media_crc32(uint32_t index, uint32_t *crc)
{
    const p6_session::Session &s = p6_session::session;
    if (index >= s.media_count || index >= P6_MAX_MEDIA) return false;
    MeasuredCrc &m = g_crc[index];
    if (!m.known) {
        char id[kMediaBlobIdBytes];
        media_blob_id(s.media_content_id[index], id);
        struct zfile *f = zfile_fopen(id, _T("rb"), 0);       // as DISK_validate_filename opens it
        if (!f) return false;
        m.crc = zfile_crc32(f);
        m.known = true;
        zfile_fclose(f);
    }
    if (!m.crc) return false;
    *crc = m.crc;
    return true;
}

void put_media_now(uint32_t drive, int32_t index)
{
    p6_session::Session &s = p6_session::session;
    if (index < 0) {
        disk_eject(int(drive));                  // also clears the names in the preferences and forgets a change that had not landed
        return;
    }
    char id[kMediaBlobIdBytes];
    media_blob_id(s.media_content_id[index], id);
    set_prefs_media(drive, id);                  // drive_insert examines the image the preferences name before it reads the one it is given
    disk_insert_force(int(drive), id, media_forced_write_protect(id));
}

}  // namespace p6_bridge

extern "C" p6_result p6_media_event_enqueue(const p6_media_event *event, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (!event || event->abi_version != P6_ABI_VERSION || event->struct_size != sizeof *event)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the event has the wrong ABI version or size");
    if (event->reserved0 || event->reserved[0] || event->reserved[1])
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "reserved event fields must be zero");
    if (event->type != P6_MEDIA_EVENT_MOUNT && event->type != P6_MEDIA_EVENT_EJECT)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "unknown media event type");
    if (event->drive > 3)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "drive number out of range");
    if (event->drive > 1)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "the A500 has only DF0 and DF1");
    if (event->type == P6_MEDIA_EVENT_MOUNT && event->media_index >= session.media_count)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the medium was not passed to p6_load");
    if (event->type == P6_MEDIA_EVENT_MOUNT && session.media_type[event->media_index] != P6_MEDIA_ADF)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "only a disk can be mounted in a drive: a hard file is mounted by p6_load");
    if (event->type == P6_MEDIA_EVENT_EJECT && event->media_index != P6_MEDIA_INDEX_EJECT)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "an eject event carries P6_MEDIA_INDEX_EJECT");
    if (session.have_sequence && event->sequence <= session.last_sequence)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "event sequence numbers must increase");
    if (session.media_queued == session.kMediaQueue)
        return p6_fail(error, P6_ERROR_CAPACITY, "too many media events queued for one boundary");
    session.media_queue[session.media_queued++] = {event->type, event->drive, event->media_index};
    session.last_sequence = event->sequence;
    session.have_sequence = true;
    return P6_OK;
}

extern "C" p6_result p6_media_append(const p6_media *medium, uint32_t *index, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");      // p6_run_frame holds the guard: a medium is never registered in the middle of a unit
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (!medium || !index)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the medium or the index is null");
    return p6_bridge::append_media(*medium, index, error);
}

extern "C" p6_result p6_media_write_status_get(p6_media_write_status *status, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (!status || status->abi_version != P6_ABI_VERSION || status->struct_size != sizeof *status)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the status has the wrong ABI version or size");
    if (status->reserved0 || status->reserved[0])
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "reserved status fields must be zero");
    if (status->media_index >= session.media_count)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the medium is not in the manifest");
    if (session.media_type[status->media_index] == P6_MEDIA_HDF) {
        // A hard file has an overlay of its own, always: the unit of the numbers is the 4 KiB chunk, and the room is the image's own size (it cannot run out).
        char id[p6_bridge::kMediaBlobIdBytes];
        p6_bridge::media_blob_id(session.media_content_id[status->media_index], id);
        uint64_t chunks = 0, dirty = 0;
        if (p6_blob_hdf_status(id, &chunks, &dirty) != P6_BLOB_OK)
            return p6_fail(error, P6_ERROR_INTERNAL, "the hard file is not registered");
        status->overlay_capacity = chunks;
        status->overlay_used = dirty;
        status->medium_sectors = dirty;
        status->refused_writes = 0;
        status->raw_tracks = 0;
        return P6_OK;
    }
    p6_overlay *overlay = p6_blob_overlay();
    if (!overlay)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "the image was loaded without P6_CONFIG_FLOPPY_WRITES");
    if (session.media_format[status->media_index] != p6_floppy::kAdf) {
        // A track-level image (T57) is read only: it has no part of the overlay, and nothing of it can be written.
        status->overlay_capacity = P6_OVL_SLOTS;
        status->overlay_used = overlay->count;
        status->medium_sectors = 0;
        status->refused_writes = overlay->refused;
        status->raw_tracks = 0;
        return P6_OK;
    }
    uint64_t own = 0, raw = 0;
    for (uint32_t i = 0; i < overlay->count; ++i)
        if (overlay->medium[i] == status->media_index) ++own;
    for (uint32_t i = 0; i < overlay->raw_count; ++i)
        if (overlay->raw_medium[i] == status->media_index) ++raw;
    status->overlay_capacity = P6_OVL_SLOTS;
    status->overlay_used = overlay->count;
    status->medium_sectors = own;
    status->refused_writes = overlay->refused;
    status->raw_tracks = raw;
    return P6_OK;
}

extern "C" p6_result p6_media_export(uint32_t media_index, void *buffer, uint64_t capacity, uint64_t *written, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok)
        return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");      // p6_run_frame holds the guard: an export never sees half a unit
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (!written)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "written is null");
    *written = 0;
    if (media_index < session.media_count && session.media_type[media_index] == P6_MEDIA_HDF) {
        char id[p6_bridge::kMediaBlobIdBytes];
        p6_bridge::media_blob_id(session.media_content_id[media_index], id);
        uint64_t chunks = 0, dirty = 0, bytes = 0;
        if (p6_blob_hdf_status(id, &chunks, &dirty) != P6_BLOB_OK || p6_blob_size(id, &bytes, nullptr) != P6_BLOB_OK)
            return p6_fail(error, P6_ERROR_INTERNAL, "the hard file is not registered");
        if (capacity < bytes)
            return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than the hard file");
        if (!buffer)
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the buffer is null");
        size_t copied = 0;
        if (p6_blob_hdf_export(id, buffer, size_t(capacity), &copied) != P6_BLOB_OK || copied != bytes)
            return p6_fail(error, P6_ERROR_INTERNAL, "the hard file could not be exported");
        *written = copied;
        return P6_OK;
    }
    if (!p6_blob_overlay())
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "the image was loaded without P6_CONFIG_FLOPPY_WRITES");
    if (media_index >= session.media_count)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the medium is not in the manifest");
    if (session.media_format[media_index] != p6_floppy::kAdf)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "an extended ADF or FDI image is read only: the guest cannot change it, so there is nothing to export");
    if (capacity < p6_bridge::kAdfBytes)
        return p6_fail(error, P6_ERROR_CAPACITY, "the buffer is smaller than a disk image (901120 bytes)");
    if (!buffer)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "the buffer is null");
    size_t bytes = 0;
    const int exported = p6_blob_overlay_export(media_index, buffer, size_t(capacity), &bytes);
    if (exported == P6_BLOB_UNSUPPORTED)
        return p6_fail(error, P6_ERROR_UNSUPPORTED, "the guest wrote a track in a format that is not AmigaDOS: an ADF image cannot hold it (the machine keeps it)");
    if (exported != P6_BLOB_OK || bytes != p6_bridge::kAdfBytes)
        return p6_fail(error, P6_ERROR_INTERNAL, "the disk could not be exported");
    *written = bytes;
    return P6_OK;
}
