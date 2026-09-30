// Media of the loaded image, known by identity: the SHA-256 of their bytes, never by a host path
// (docs/02-contracts.md, "ROMs, discos y persistencia"; docs/adr/0008-media-identity.md).
#ifndef P6_BRIDGE_MEDIA_H
#define P6_BRIDGE_MEDIA_H
#include "p6.h"
#include <stddef.h>
#include <stdint.h>

namespace p6_bridge {

// An accepted image is a plain double-density ADF: 80 cylinders x 2 heads x 11 sectors x 512 bytes.
constexpr uint64_t kAdfBytes = 1760u * 512u;

// "media-" + the 64 lowercase hex digits of the content id + NUL. It has no dot, so the engine cannot recognise an
// extension in it and treat the image as some other disk format.
constexpr size_t kMediaBlobIdBytes = 6 + 2 * P6_CONTENT_ID_SIZE + 1;
void media_blob_id(const uint8_t content_id[P6_CONTENT_ID_SIZE], char out[kMediaBlobIdBytes]);

// Pure checks of the list p6_load received; the engine is not touched, so a rejected list costs nothing. Every image is
// hashed and its content_id must be the SHA-256 of its bytes.
p6_result validate_media(const p6_media *media, uint32_t count, p6_error *error);

// After validate_media: copies every image into the engine's blob store under its blob id. A store that cannot hold
// them all fails with CAPACITY; the caller unloads what was registered.
p6_result register_media(const p6_media *media, uint32_t count, p6_error *error);

// Identity of what was loaded, kept for the state header (T14): SHA-256 of the firmware and the ordered content ids.
void remember_identity(const p6_buffer &firmware, const p6_media *media, uint32_t count);
void forget_identity();

}  // namespace p6_bridge
#endif
