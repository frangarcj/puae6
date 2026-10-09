/* Copy-on-write layer over immutable ADF images (T27, docs/02-contracts.md "ROMs, discos y persistencia").
 *
 * The base of a disk is never written. What the guest writes lands in this overlay, in units of one 512-byte sector: the engine's
 * ADF path decodes the MFM track the guest wrote into sectors and writes them through zfile, so a sector is the unit in which the
 * engine itself changes an ADF. (A track that does not decode is not a sector write at all: see the raw tracks below.) The pool is one fixed array shared by every medium, reserved when the image is loaded, so its size is a bound
 * of the state and a write that does not fit is refused as a whole before any byte of it is stored.
 *
 * A track the guest wrote that does not decode as eleven AmigaDOS sectors (a custom format) is not a sector write and an ADF has nowhere to put it: the engine used to
 * drop it. It is kept exactly as the guest wrote it, as the MFM words of the track, in a second small pool (P6_OVL_RAW_SLOTS tracks, shared by all media) and the
 * drive reads it back from there. Such a disk cannot be exported as an ADF (the export says so instead of leaving the track out).
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
#define P6_OVL_TRACKS 160u                            /* cylinders x heads */
#define P6_OVL_RAW_SLOTS 16u                          /* raw tracks, shared by all media */
#define P6_OVL_RAW_WORDS 6392u                        /* 12784 bytes: an NTSC revolution, the longest track the profile writes (PAL: 6334 words) */

typedef struct p6_overlay {
    uint32_t count;                                   /* slots in use: 0..P6_OVL_SLOTS-1, in the order they were first written */
    uint64_t refused;                                 /* writes refused for lack of room since the load (part of the state) */
    uint16_t *slot_of;                                /* [P6_OVL_MEDIA * P6_OVL_DISK_SECTORS]: slot + 1, 0 = the sector is the base's */
    uint8_t *medium;                                  /* [P6_OVL_SLOTS] */
    uint16_t *sector;                                 /* [P6_OVL_SLOTS] */
    uint8_t *data;                                    /* [P6_OVL_SLOTS * P6_OVL_SECTOR_BYTES] */
    uint32_t raw_count;                               /* raw tracks in use, in the order they were first written */
    uint8_t *raw_medium;                              /* [P6_OVL_RAW_SLOTS] */
    uint8_t *raw_track;                               /* [P6_OVL_RAW_SLOTS]: cylinder * 2 + side */
    uint32_t *raw_bits;                               /* [P6_OVL_RAW_SLOTS]: length of the track in bits, 1..P6_OVL_RAW_WORDS * 16 */
    uint16_t *raw_words;                              /* [P6_OVL_RAW_SLOTS * P6_OVL_RAW_WORDS]: the MFM words as the guest wrote them */
} p6_overlay;

/* Bytes of storage one overlay needs, and its layout inside a block of that size (zero it first). */
#define P6_OVL_STORAGE_BYTES (sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS + P6_OVL_SLOTS * (sizeof(uint8_t) + sizeof(uint16_t) + P6_OVL_SECTOR_BYTES) + \
                              P6_OVL_RAW_SLOTS * (2 * sizeof(uint8_t) + sizeof(uint32_t) + sizeof(uint16_t) * P6_OVL_RAW_WORDS))

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
    p += (size_t)P6_OVL_SLOTS * P6_OVL_SECTOR_BYTES;
    o->raw_words = (uint16_t *)(void *)p;
    p += sizeof(uint16_t) * (size_t)P6_OVL_RAW_SLOTS * P6_OVL_RAW_WORDS;
    o->raw_bits = (uint32_t *)(void *)p;
    p += sizeof(uint32_t) * P6_OVL_RAW_SLOTS;
    o->raw_medium = p;
    p += P6_OVL_RAW_SLOTS;
    o->raw_track = p;
}

/* Every sector is the base's again (a load, or the start of applying a state). The storage stays. */
static inline void p6_overlay_reset(p6_overlay *o)
{
    memset(o->slot_of, 0, sizeof(uint16_t) * P6_OVL_MEDIA * P6_OVL_DISK_SECTORS);
    memset(o->medium, 0, P6_OVL_SLOTS);                  /* a slot is read only below the count, but the bound of a state is measured over all of them */
    memset(o->sector, 0, sizeof(uint16_t) * P6_OVL_SLOTS);
    memset(o->raw_medium, 0, P6_OVL_RAW_SLOTS);
    memset(o->raw_track, 0, P6_OVL_RAW_SLOTS);
    memset(o->raw_bits, 0, sizeof(uint32_t) * P6_OVL_RAW_SLOTS);
    o->count = 0;
    o->refused = 0;
    o->raw_count = 0;
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

/* The raw slot of (medium, track), or -1. */
static inline int32_t p6_overlay_raw_find(const p6_overlay *o, uint32_t medium, uint32_t track)
{
    for (uint32_t i = 0; i < o->raw_count; ++i)
        if (o->raw_medium[i] == medium && o->raw_track[i] == track) return (int32_t)i;
    return -1;
}

static inline uint16_t *p6_overlay_raw_slot(const p6_overlay *o, uint32_t slot) { return o->raw_words + (size_t)slot * P6_OVL_RAW_WORDS; }

/* Keeps `bits` bits (the first (bits + 15) / 16 words) of `words` as the raw track (medium, track): in its slot when it has one, else in a new one. 0, or -1 when the
 * track is longer than a slot, empty, or all slots are taken (the caller counts that as a refused write). */
static inline int p6_overlay_raw_put(p6_overlay *o, uint32_t medium, uint32_t track, const uint16_t *words, uint32_t bits)
{
    if (!bits || bits > P6_OVL_RAW_WORDS * 16u || medium >= P6_OVL_MEDIA || track >= P6_OVL_TRACKS) return -1;
    int32_t slot = p6_overlay_raw_find(o, medium, track);
    if (slot < 0) {
        if (o->raw_count >= P6_OVL_RAW_SLOTS) return -1;
        slot = (int32_t)o->raw_count++;
        o->raw_medium[slot] = (uint8_t)medium;
        o->raw_track[slot] = (uint8_t)track;
    }
    o->raw_bits[slot] = bits;
    memcpy(p6_overlay_raw_slot(o, (uint32_t)slot), words, (size_t)((bits + 15u) / 16u) * sizeof(uint16_t));
    return 0;
}

/* The guest wrote the track as AmigaDOS sectors again: the raw copy is gone. Later slots move down one place, so the order stays the order of first writing. */
static inline void p6_overlay_raw_remove(p6_overlay *o, uint32_t medium, uint32_t track)
{
    const int32_t slot = p6_overlay_raw_find(o, medium, track);
    if (slot < 0) return;
    for (uint32_t i = (uint32_t)slot + 1; i < o->raw_count; ++i) {
        o->raw_medium[i - 1] = o->raw_medium[i];
        o->raw_track[i - 1] = o->raw_track[i];
        o->raw_bits[i - 1] = o->raw_bits[i];
        memcpy(p6_overlay_raw_slot(o, i - 1), p6_overlay_raw_slot(o, i), (size_t)P6_OVL_RAW_WORDS * sizeof(uint16_t));
    }
    --o->raw_count;
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
