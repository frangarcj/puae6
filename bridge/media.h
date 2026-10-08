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

// p6_media_append (the manifest only grows at its end): after validate_media(medium, 1), the index of the medium whose content id this is, or a new one that is
// registered as p6_load registers its media. CAPACITY when the manifest is full and the medium is new; nothing changes in that case or on any other failure.
// Nothing of the machine is touched: no preference, no drive, no queued event.
p6_result append_media(const p6_media &medium, uint32_t *index, p6_error *error);

// Identity of what was loaded, kept for the state header (T14): SHA-256 of the firmware and the ordered content ids.
void remember_identity(const p6_buffer &firmware, const p6_media *media, uint32_t count);
// Forgets identity, queued events and drive contents; the next load starts from nothing.
void forget_media();

// Media events. Those queued before the first p6_run_frame only set the power-on contents of the drives (the reset
// head inserts them at once, /CHNG high); later ones start the engine's own delayed swap. Both are applied by
// p6_run_frame right after the reset head and before input, in the order they were accepted.
void apply_boot_media();
void apply_media_events();

// The CRC-32 that drive_insert measures for manifest medium `index` (DISK_validate_filename: zfile_crc32 over the registered blob), which is what DSKn of a state
// carries for a drive that holds it. Measured once per medium and kept until the next load. False for an index outside the manifest, a blob that cannot be read and
// an image whose CRC is 0: DSKn says 0 for an empty drive, so such a medium could not be told from no medium.
bool media_crc32(uint32_t index, uint32_t *crc);

// Puts manifest medium `index` (-1: none) in a drive at once and write protected: the forced insert the reset head uses, not the delayed change of
// apply_media_events. It names the medium in the preferences, which the engine reads, and changes nothing in the session or the scheduler; a state load uses it
// and its caller runs it inside the scope that puts the scheduler and the generator back (the insert draws random bits for the position of the disk).
void put_media_now(uint32_t drive, int32_t index);

}  // namespace p6_bridge
#endif
