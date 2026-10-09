/* Copy-on-write layer over immutable ADF images (T27, docs/02-contracts.md "ROMs, discos y persistencia").
 *
 * The base of a disk is never written. What the guest writes lands in this overlay, in units of one 512-byte sector: the engine's
 * ADF path decodes the MFM track the guest wrote into sectors and writes them through zfile, so a sector is the unit in which the
 * engine itself changes an ADF. (A track that does not decode is not a sector write at all: it is kept as a raw MFM track, see
 * media/raw_track.h.) The pool is one fixed array shared by every medium, reserved when the image is loaded, so its size is a bound
 * of the state and a write that does not fit is refused as a whole before any byte of it is stored.
 *
 * Pure data and pure functions (no allocation, no libc beyond memcpy/memset): the storage is handed in, so the same code serves the
 * blob store, the native state and the tests. Everything is static inline; blob_io.c owns the one instance of an image.
 */
#ifndef P6_MEDIA_OVERLAY_H
#define P6_MEDIA_OVERLAY_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define P6_OVL_SECTOR_BYTES 512u
#define P6_OVL_DISK_SECTORS 1760u                     /* 80 cylinders x 2 heads x 11 sectors */
#define P6_OVL_MEDIA 16u                              /* P6_MAX_MEDIA */
#define P6_OVL_SLOTS 3520u                            /* two disks' worth of sectors, shared by all media */
#define P6_OVL_TRACK_SECTORS 11u

typedef struct p6_overlay {
    uint32_t count;                                   /* slots in use: 0..P6_OVL_SLOTS-1, in the order they were first written */
    uint64_t refused;                                 /* writes refused for lack of room since the load (part of the state) */
    uint16_t *slot_of;                                /* [P6_OVL_MEDIA * P6_OVL_DISK_SECTORS]: slot + 1, 0 = the sector is the base's */
    uint8_t *medium;                                  /* [P6_OVL_SLOTS] */
    uint16_t *sector;                                 /* [P6_OVL_SLOTS] */
    uint8_t *data;                                    /* [P6_OVL_SLOTS * P6_OVL_SECTOR_BYTES] */
} p6_overlay;

/* Bytes of storage one overlay needs, and its layout inside a block of that size (zero it first). */
#define P6_OVL_STORAGE_BYTES (sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS + P6_OVL_SLOTS * (sizeof(uint8_t) + sizeof(uint16_t) + P6_OVL_SECTOR_BYTES))

static inline void p6_overlay_use(p6_overlay *o, void *storage)
{
    uint8_t *p = (uint8_t *)storage;
    memset(o, 0, sizeof *o);
    o->slot_of = (uint16_t *)(void *)p;
    p += sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS;
    o->sector = (uint16_t *)(void *)p;
    p += sizeof(uint16_t) * P6_OVL_SLOTS;
    o->medium = p;
    p += P6_OVL_SLOTS;
    o->data = p;
}

/* Every sector is the base's again (a load, or the start of applying a state). The storage stays. */
static inline void p6_overlay_reset(p6_overlay *o)
{
    memset(o->slot_of, 0, sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS);
    o->count = 0;
    o->refused = 0;
}

static inline uint32_t p6_overlay_free(const p6_overlay *o) { return P6_OVL_SLOTS - o->count; }

/* The slot that holds (medium, sector), or -1 when the sector is the base's. */
static inline int32_t p6_overlay_find(const p6_overlay *o, uint32_t medium, uint32_t sector)
{
    if (medium >= P6_OVL_MEDIA || sector >= P6_OVL_DISK_SECTORS) return -1;
    return (int32_t)o->slot_of[medium * P6_OVL_DISK_SECTORS + sector] - 1;
}

static inline uint8_t *p6_overlay_sector(const p6_overlay *o, uint32_t slot) { return o->data + (size_t)slot * P6_OVL_SECTOR_BYTES; }

/* A new slot for (medium, sector) holding `data`; the sector must not have one. -1 when the pool is full. */
static inline int32_t p6_overlay_add(p6_overlay *o, uint32_t medium, uint32_t sector, const uint8_t *data)
{
    if (o->count >= P6_OVL_SLOTS || medium >= P6_OVL_MEDIA || sector >= P6_OVL_DISK_SECTORS) return -1;
    const uint32_t slot = o->count++;
    o->medium[slot] = (uint8_t)medium;
    o->sector[slot] = (uint16_t)sector;
    memcpy(p6_overlay_sector(o, slot), data, P6_OVL_SECTOR_BYTES);
    o->slot_of[medium * P6_OVL_DISK_SECTORS + sector] = (uint16_t)(slot + 1);
    return (int32_t)slot;
}

/* After the slot arrays were filled from a state: rebuilds the index. 0 when every slot names a distinct sector of a medium of the
 * manifest (`media` of them), otherwise -1 and the index is empty. */
static inline int p6_overlay_index(p6_overlay *o, uint32_t media)
{
    memset(o->slot_of, 0, sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS);
    for (uint32_t i = 0; i < o->count; ++i) {
        const uint32_t m = o->medium[i], s = o->sector[i];
        if (m >= media || m >= P6_OVL_MEDIA || s >= P6_OVL_DISK_SECTORS || o->slot_of[m * P6_OVL_DISK_SECTORS + s]) {
            memset(o->slot_of, 0, sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS);
            return -1;
        }
        o->slot_of[m * P6_OVL_DISK_SECTORS + s] = (uint16_t)(i + 1);
    }
    return 0;
}

/* Compares two sectors without calling memcmp (the blob store links against nothing but the allocator and the string primitives). */
static inline int p6_overlay_same(const uint8_t *a, const uint8_t *b)
{
    uint64_t difference = 0;
    for (size_t i = 0; i < P6_OVL_SECTOR_BYTES; i += 8) {
        uint64_t x, y;
        memcpy(&x, a + i, 8);
        memcpy(&y, b + i, 8);
        difference |= x ^ y;
    }
    return difference == 0;
}

#endif
