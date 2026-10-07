// Identity, validation and registration of the media passed to p6_load.
//
// The engine has no error path for the size or the format of an ADF, and a crafted image crashes it (research of
// T12: a 901120-byte file with the extended-ADF magic and a track count of 0xFFFF overruns the drive's track table),
// so this file is the only gatekeeper: an image reaches the engine only after it passed every check below.
#include "media.h"
#include "error_util.h"
#include "session.h"
#include "sha256.h"
#include "blob_io.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "zfile.h"

#include <string.h>

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace p6_bridge {

namespace {

// First bytes that make the engine pick another loader than the plain one: the extended-ADF magics, and the header
// of an Amiga executable, which the engine would convert into a disk.
bool has_special_header(const uint8_t *data)
{
    static const uint8_t ext1[8] = {'U', 'A', 'E', '-', '-', 'A', 'D', 'F'};
    static const uint8_t ext2[8] = {'U', 'A', 'E', '-', '1', 'A', 'D', 'F'};
    static const uint8_t hunk[8] = {0x00, 0x00, 0x03, 0xF3, 0x00, 0x00, 0x00, 0x00};
    return !memcmp(data, ext1, 8) || !memcmp(data, ext2, 8) || !memcmp(data, hunk, 8);
}

}  // namespace

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
    for (uint32_t i = 0; i < count; ++i) {
        const p6_media &m = media[i];
        if (m.type != P6_MEDIA_ADF)
            return p6_fail(error, P6_ERROR_UNSUPPORTED, "only ADF media are supported");
        if (m.flags != 0)
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "media flags are reserved");
        if (!m.image.data)
            return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "a medium has no image");
        if (m.image.bytes != kAdfBytes)
            return p6_fail(error, P6_ERROR_WRONG_MEDIA, "a disk image must be exactly 901120 bytes");
        if (has_special_header(static_cast<const uint8_t *>(m.image.data)))
            return p6_fail(error, P6_ERROR_WRONG_MEDIA, "extended ADF and executable images are not supported");
        for (uint32_t j = 0; j < i; ++j)
            if (!memcmp(media[j].content_id, m.content_id, P6_CONTENT_ID_SIZE))
                return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "two media share a content id");
    }
    for (uint32_t i = 0; i < count; ++i) {
        uint8_t digest[32];
        p6_sha256_hash(media[i].image.data, media[i].image.bytes, digest);
        if (memcmp(digest, media[i].content_id, P6_CONTENT_ID_SIZE))
            return p6_fail(error, P6_ERROR_WRONG_MEDIA, "a content id is not the SHA-256 of its image");
    }
    return P6_OK;
}

p6_result register_media(const p6_media *media, uint32_t count, p6_error *error)
{
    for (uint32_t i = 0; i < count; ++i) {
        char id[kMediaBlobIdBytes];
        media_blob_id(media[i].content_id, id);
        switch (p6_blob_register(id, P6_BLOB_ADF, media[i].image.data, size_t(media[i].image.bytes))) {
        case P6_BLOB_OK:
            break;
        case P6_BLOB_CAPACITY:
        case P6_BLOB_NO_MEMORY:
            return p6_fail(error, P6_ERROR_CAPACITY, "the media could not be registered: no room");
        default:
            return p6_fail(error, P6_ERROR_INTERNAL, "a medium could not be registered");
        }
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
    for (uint32_t i = 0; i < count; ++i)
        memcpy(s.media_content_id[i], media[i].content_id, P6_CONTENT_ID_SIZE);
}

void forget_media()
{
    p6_session::Session &s = p6_session::session;
    memset(s.firmware_sha256, 0, sizeof s.firmware_sha256);
    memset(s.media_content_id, 0, sizeof s.media_content_id);
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
    changed_prefs.floppyslots[drive].forcedwriteprotect = true;   // used by the engine's own change detection
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
            changed_prefs.floppyslots[e.drive].forcedwriteprotect = true;
            disk_insert(int(e.drive), id, true);
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
    disk_insert_force(int(drive), id, true);
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
