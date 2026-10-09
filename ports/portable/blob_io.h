#ifndef P6_BLOB_IO_H
#define P6_BLOB_IO_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { P6_BLOB_ASSETS = 32, P6_BLOB_STREAMS = 64, P6_BLOB_ID_BYTES = 80 };
enum { P6_BLOB_ROM = 1, P6_BLOB_ADF = 2, P6_BLOB_KEY = 3 };
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
struct p6_overlay *p6_blob_overlay(void);                           /* the pool, or NULL when none was reserved */
/* The merged image of a bound asset (base with the overlay over it): exactly the asset's size is written, nothing when `capacity` is smaller (CAPACITY). */
int p6_blob_overlay_export(uint32_t medium, void *out, size_t capacity, size_t *written);

#ifdef __cplusplus
}
#endif
#endif
