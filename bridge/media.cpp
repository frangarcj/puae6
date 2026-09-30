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

#include <string.h>

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

void remember_identity(const p6_buffer &firmware, const p6_media *media, uint32_t count)
{
    p6_session::Session &s = p6_session::session;
    p6_sha256_hash(firmware.data, firmware.bytes, s.firmware_sha256);
    s.media_count = count;
    for (uint32_t i = 0; i < count; ++i)
        memcpy(s.media_content_id[i], media[i].content_id, P6_CONTENT_ID_SIZE);
}

void forget_identity()
{
    p6_session::Session &s = p6_session::session;
    memset(s.firmware_sha256, 0, sizeof s.firmware_sha256);
    memset(s.media_content_id, 0, sizeof s.media_content_id);
    s.media_count = 0;
}

}  // namespace p6_bridge
