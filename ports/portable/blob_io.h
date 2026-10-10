#ifndef P6_BLOB_IO_H
#define P6_BLOB_IO_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { P6_BLOB_ASSETS = 32, P6_BLOB_STREAMS = 64, P6_BLOB_ID_BYTES = 80 };
enum { P6_BLOB_ROM = 1, P6_BLOB_ADF = 2, P6_BLOB_KEY = 3, P6_BLOB_HDF = 4 };
enum { P6_BLOB_OK = 0, P6_BLOB_ARGUMENT = -1, P6_BLOB_NOT_FOUND = -2,
       P6_BLOB_UNSUPPORTED = -3, P6_BLOB_READ_ONLY = -4, P6_BLOB_CAPACITY = -5,
       P6_BLOB_NO_MEMORY = -6, P6_BLOB_HANDLE = -7, P6_BLOB_RANGE = -8,
       P6_BLOB_DUPLICATE = -9, P6_BLOB_NO_SPACE = -10 };
enum { P6_BLOB_SET = 0, P6_BLOB_CUR = 1, P6_BLOB_END = 2 };
typedef uint64_t p6_blob_handle;
typedef struct p6_blob_info {
 uint64_t bytes, position;
 uint32_t kind, reserved;
} p6_blob_info;

/* Internal single-threaded service, one store per image/sandbox. Register before
 * emulation/seal: copy caller bytes into guest heap. No host pointers retained.
 * IDs: 1..80 ASCII bytes, first alphanumeric, then alphanumeric/_/-/single dots.
 * No paths, devices, URI, environment, case folding or fallback search.
 * Kind declares raw data; media geometry, ROM checksums and content-ID validation
 * are the loader's responsibility (T12), not proved by successful registration.
 * Unsupported kinds (archives/devices/etc.) fail. Zero-byte assets are permitted
 * so the loader can diagnose truncation; they do not constitute valid firmware.
 */
int p6_blob_register(const char *id, uint32_t kind, const void *data, size_t bytes);
int p6_blob_exists(const char *id);
/* The base of an asset by offset, with no stream: no cursor to move, nothing the host keeps between two reads (the hard files of T29a are read this way, so a read
 * is a function of the registered bytes and the offset alone). size gives the length and kind of the asset (either may be NULL). pread copies the bytes of
 * [offset, offset + length) that lie inside the asset: *got is their number (0 at or beyond the end), P6_BLOB_RANGE is not used and a missing asset is NOT_FOUND. */
int p6_blob_size(const char *id, uint64_t *bytes, uint32_t *kind);
int p6_blob_pread(const char *id, uint64_t offset, void *out, size_t length, size_t *got);

/* The write overlay of a hard file (T29b): the base of a P6_BLOB_HDF asset is never written; a write goes to a copy of the 4 KiB chunk (P6_HDF_CHUNK, the
 * HARDDISK_CHUNK of MacHawk) that is made on its first write, and the chunk is marked in a bitmap. The memory for all of it (the size of the image plus its bitmap) is
 * reserved when the asset is registered, so a write never fails for lack of room and never allocates. hdf_read gives the bytes the guest sees (the overlay over the base),
 * hdf_write is all-or-nothing and RANGE (nothing stored) when it ends beyond the image, hdf_export copies the whole image as it is now (CAPACITY, nothing written, when the
 * buffer is smaller) and hdf_status says how many chunks the image has and how many differ from the base's. p6_blob_pread keeps answering the base. None of this is
 * state yet: a session with a hard file has no native state until T29c. */
enum { P6_HDF_CHUNK = 4096 };
int p6_blob_hdf_read(const char *id, uint64_t offset, void *out, size_t length, size_t *got);
int p6_blob_hdf_write(const char *id, uint64_t offset, const void *data, size_t length, size_t *done);
int p6_blob_hdf_export(const char *id, void *out, size_t capacity, size_t *written);
int p6_blob_hdf_status(const char *id, uint64_t *chunks, uint64_t *dirty);
/* "rb": the base, always. "r+b": the base with the write overlay of the asset on top (p6_blob_overlay_bind), READ_ONLY for an asset without
 * one. out is zeroed on failure; every successful open has its own cursor. */
int p6_blob_open(const char *id, const char *mode, p6_blob_handle *out);
int p6_blob_close(p6_blob_handle handle);
int p6_blob_stat(p6_blob_handle handle, p6_blob_info *out);
/* Complete elements only, matching zfile's memory path. Overflow and insufficient
 * destination capacity fail before writes/cursor changes. items is set to zero
 * on failure. Caller supplies valid, disjoint output ranges. */
int p6_blob_read(p6_blob_handle handle, void *out, size_t capacity,
                 size_t width, size_t count, size_t *items);
/* Valid origin: clamp out-of-range seeks to [0,size] and return RANGE, as zfile
 * does for memory streams. Invalid origin leaves cursor unchanged. */
int p6_blob_seek(p6_blob_handle handle, int64_t offset, int origin);
/* READ_ONLY for any handle that was not opened "r+b" (data/bytes are never inspected). On an overlay stream the bytes land in the overlay, never in the
 * base: the asset keeps its size (a write that ends beyond it is RANGE), it is all-or-nothing (a write that needs more sectors than the pool has free
 * is NO_SPACE, counts as refused and stores nothing), and a sector whose new content equals the base's uses no room. */
int p6_blob_write(p6_blob_handle handle, const void *data, size_t bytes);
/* Unload only: frees assets and invalidates all handles. Tokens are never reused
 * within this image's lifetime; overflow fails rather than wrapping. State is
 * ordinary guest memory; native serialization/rebinding remains a later gate. */
void p6_blob_clear(void);

/* The write overlay of the disk images (T27, media/overlay.h). The pool is reserved once per image, before the first disk is bound, and is part of
 * the machine's state: the bridge saves and restores it (bridge/state_upstream.cpp); p6_blob_clear releases it. `medium` is the index of the disk in the
 * session's manifest. */
struct p6_overlay;
int p6_blob_overlay_reserve(void);                                  /* NO_MEMORY when the pool cannot be had; DUPLICATE when already reserved */
int p6_blob_overlay_bind(const char *id, uint32_t medium);          /* the asset must be a 901120-byte ADF; one medium number per asset; NOT_FOUND, RANGE, DUPLICATE, UNSUPPORTED (no pool) */
int p6_blob_overlay_writable(const char *id);                       /* 1 when "r+b" works on the asset, else 0 */
int p6_blob_overlay_room(unsigned int sectors);                     /* 1 when the pool exists and has `sectors` slots free */
struct p6_overlay *p6_blob_overlay(void);
/* Raw tracks (a custom format the guest wrote; media/overlay.h), by the id of the disk image and the track (cylinder * 2 + side). The engine calls these where it would
 * decode a written track into sectors or build a read from them: raw_write keeps `bits` bits of MFM words (host order), raw_read copies them back (the number of words
 * is (bits + 15) / 16; 1 when the track has a raw copy, 0 when it has none or the asset has no overlay), raw_clear forgets the copy. raw_write returns 0, or
 * NO_SPACE (the track is longer than a slot, or every slot is taken; counted as a refused write). */
int p6_blob_raw_write(const char *id, unsigned int track, const unsigned short *words, unsigned int bits);
int p6_blob_raw_read(const char *id, unsigned int track, unsigned short *words, unsigned int *bits);
void p6_blob_raw_clear(const char *id, unsigned int track);                           /* the pool, or NULL when none was reserved */
/* The merged image of a bound asset (base with the overlay over it): exactly the asset's size is written, nothing when `capacity` is smaller (CAPACITY), and nothing
 * (UNSUPPORTED) when the disk holds a raw track, which an ADF cannot represent. */
int p6_blob_overlay_export(uint32_t medium, void *out, size_t capacity, size_t *written);

#ifdef __cplusplus
}
#endif
#endif
