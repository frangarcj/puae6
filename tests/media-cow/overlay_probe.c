/* MED02, the store: the write overlay of ports/portable/blob_io.c and media/overlay.h without the engine. Exits 0 when every assertion holds.
 * What it pins: the base is never written; the size never grows; a write is all or nothing; a sector equal to the base's uses no room; a full pool
 * is refused and counted; the state-side helpers rebuild the index and refuse a duplicate or a medium beyond the manifest. */
#include "blob_io.h"
#include "media/overlay.h"
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { BYTES = P6_OVL_DISK_SECTORS * P6_OVL_SECTOR_BYTES };

static unsigned char base_a[BYTES], base_b[BYTES], copy_a[BYTES], buffer[BYTES];

static void fill(unsigned char *p, unsigned seed)
{
    for (size_t i = 0; i < BYTES; ++i) p[i] = (unsigned char)(i * 31u + seed + (i >> 9));
}

static p6_blob_handle open_rw(const char *id)
{
    p6_blob_handle h = 0;
    assert(p6_blob_open(id, "r+b", &h) == P6_BLOB_OK && h);
    return h;
}

static void write_at(p6_blob_handle h, size_t at, const void *data, size_t n)
{
    assert(p6_blob_seek(h, (int64_t)at, P6_BLOB_SET) == P6_BLOB_OK);
    assert(p6_blob_write(h, data, n) == P6_BLOB_OK);
}

static void read_at(p6_blob_handle h, size_t at, void *out, size_t n)
{
    size_t items = 0;
    assert(p6_blob_seek(h, (int64_t)at, P6_BLOB_SET) == P6_BLOB_OK);
    assert(p6_blob_read(h, out, n, 1, n, &items) == P6_BLOB_OK && items == n);
}

int main(void)
{
    fill(base_a, 1);
    fill(base_b, 77);
    memcpy(copy_a, base_a, BYTES);
    p6_blob_clear();

    /* Nothing is writable before a pool is reserved and a disk bound. */
    assert(p6_blob_register("disk-a", P6_BLOB_ADF, base_a, BYTES) == P6_BLOB_OK);
    assert(p6_blob_register("disk-b", P6_BLOB_ADF, base_b, BYTES) == P6_BLOB_OK);
    assert(p6_blob_register("disk-c", P6_BLOB_ADF, base_b, BYTES) == P6_BLOB_OK);
    assert(p6_blob_register("short", P6_BLOB_ADF, base_a, 1024) == P6_BLOB_OK);
    assert(p6_blob_register("rom", P6_BLOB_ROM, base_a, 1024) == P6_BLOB_OK);
    p6_blob_handle h = 0;
    assert(p6_blob_open("disk-a", "r+b", &h) == P6_BLOB_READ_ONLY && h == 0);
    assert(p6_blob_overlay() == NULL && !p6_blob_overlay_writable("disk-a") && !p6_blob_overlay_room(1));
    assert(p6_blob_overlay_bind("disk-a", 0) == P6_BLOB_UNSUPPORTED);
    assert(p6_blob_overlay_reserve() == P6_BLOB_OK && p6_blob_overlay_reserve() == P6_BLOB_DUPLICATE);
    assert(p6_blob_overlay_bind("nothing", 0) == P6_BLOB_NOT_FOUND);
    assert(p6_blob_overlay_bind("short", 0) == P6_BLOB_RANGE && p6_blob_overlay_bind("rom", 0) == P6_BLOB_RANGE);
    assert(p6_blob_overlay_bind("disk-a", 16) == P6_BLOB_ARGUMENT);
    assert(p6_blob_overlay_bind("disk-a", 0) == P6_BLOB_OK && p6_blob_overlay_bind("disk-a", 1) == P6_BLOB_DUPLICATE);
    assert(p6_blob_overlay_bind("disk-b", 0) == P6_BLOB_DUPLICATE && p6_blob_overlay_bind("disk-b", 1) == P6_BLOB_OK);
    assert(p6_blob_overlay_bind("disk-c", 2) == P6_BLOB_OK);
    assert(p6_blob_overlay_writable("disk-a") && p6_blob_overlay_writable("disk-b") && !p6_blob_overlay_writable("rom"));
    p6_overlay *o = p6_blob_overlay();
    assert(o && o->count == 0 && p6_overlay_free(o) == P6_OVL_SLOTS && p6_blob_overlay_room(P6_OVL_SLOTS) && !p6_blob_overlay_room(P6_OVL_SLOTS + 1));

    /* "rb" is the base, "r+b" the base with the overlay on top; an "rb" stream cannot write. */
    p6_blob_handle base = 0, rw = open_rw("disk-a");
    assert(p6_blob_open("disk-a", "rb", &base) == P6_BLOB_OK);
    assert(p6_blob_write(base, copy_a, 512) == P6_BLOB_READ_ONLY);
    unsigned char pattern[1500];
    for (size_t i = 0; i < sizeof pattern; ++i) pattern[i] = (unsigned char)(0xA0 ^ i);

    /* A write that starts and ends inside sectors keeps the rest of them, and crosses sector borders. */
    write_at(rw, 700, pattern, 1500);                       /* sectors 1 to 4 */
    assert(o->count == 4);
    read_at(rw, 0, buffer, BYTES);
    unsigned char want[BYTES];
    memcpy(want, copy_a, BYTES);
    memcpy(want + 700, pattern, 1500);
    assert(!memcmp(buffer, want, BYTES));
    read_at(base, 0, buffer, BYTES);
    assert(!memcmp(buffer, copy_a, BYTES));                 /* the base stream sees the base */
    assert(!memcmp(base_a, copy_a, BYTES));                 /* and so does the memory that was registered... */
    size_t exported = 1;
    assert(p6_blob_overlay_export(0, buffer, BYTES - 1, &exported) == P6_BLOB_CAPACITY && exported == 0);
    assert(p6_blob_overlay_export(0, buffer, BYTES, &exported) == P6_BLOB_OK && exported == BYTES && !memcmp(buffer, want, BYTES));
    assert(p6_blob_overlay_export(1, buffer, BYTES, &exported) == P6_BLOB_OK && !memcmp(buffer, base_b, BYTES));   /* another disk: untouched */
    assert(p6_blob_overlay_export(3, buffer, BYTES, &exported) == P6_BLOB_NOT_FOUND);

    /* The cursor moves like a file's; a write cannot grow the disk; a refused write moves and stores nothing. */
    p6_blob_info info;
    assert(p6_blob_stat(rw, &info) == P6_BLOB_OK && info.bytes == BYTES && info.position == BYTES); /* after the full read above */
    assert(p6_blob_seek(rw, BYTES - 10, P6_BLOB_SET) == P6_BLOB_OK);
    assert(p6_blob_write(rw, pattern, 11) == P6_BLOB_RANGE);
    assert(p6_blob_stat(rw, &info) == P6_BLOB_OK && info.bytes == BYTES && info.position == BYTES - 10 && o->count == 4);
    assert(p6_blob_write(rw, pattern, 10) == P6_BLOB_OK && o->count == 5);
    assert(p6_blob_write(rw, pattern, 0) == P6_BLOB_OK && p6_blob_write(rw, NULL, 1) == P6_BLOB_ARGUMENT && p6_blob_write(rw, pattern, 1) == P6_BLOB_RANGE);

    /* Writing what the base already has uses no room; writing it back over a sector that has a slot keeps the slot. */
    const uint32_t before = o->count;
    write_at(rw, 10 * 512, copy_a + 10 * 512, 512);
    assert(o->count == before);
    write_at(rw, 1 * 512, copy_a + 512, 512);               /* sector 1 has a slot: it becomes the base's content again, in its slot */
    assert(o->count == before && p6_overlay_find(o, 0, 1) >= 0);
    memcpy(want + 512, copy_a + 512, 512);
    read_at(rw, 0, buffer, 4096);
    assert(!memcmp(buffer, want, 4096));

    /* All or nothing: fill the pool up to 2 slots short, then a 3-sector write is refused and stores nothing while a 2-sector one fits. */
    uint32_t medium_b_sector = 0;
    p6_blob_handle rwb = open_rw("disk-b"), rwc = open_rw("disk-c");
    unsigned char sector[512];
    while (p6_overlay_free(o) > 2) {
        memset(sector, (int)(medium_b_sector + 1), sizeof sector);
        write_at(medium_b_sector < P6_OVL_DISK_SECTORS ? rwb : rwc, (size_t)(medium_b_sector % P6_OVL_DISK_SECTORS) * 512, sector, 512);
        ++medium_b_sector;
    }
    assert(p6_blob_overlay_room(2) && !p6_blob_overlay_room(3) && o->refused == 0);
    unsigned char three[3 * 512];
    memset(three, 0x5A, sizeof three);
    const uint32_t used = o->count;
    assert(p6_blob_seek(rw, 100 * 512, P6_BLOB_SET) == P6_BLOB_OK);
    assert(p6_blob_write(rw, three, sizeof three) == P6_BLOB_NO_SPACE);
    assert(o->count == used && o->refused == 1);
    read_at(rw, 100 * 512, buffer, sizeof three);
    assert(!memcmp(buffer, copy_a + 100 * 512, sizeof three));
    write_at(rw, 100 * 512, three, 2 * 512);
    assert(o->count == used + 2 && p6_overlay_free(o) == 0 && o->refused == 1);
    assert(p6_blob_write(rw, three, 512) == P6_BLOB_NO_SPACE && o->refused == 2);
    write_at(rw, 100 * 512, sector, 512);                   /* a sector that already has a slot still takes writes */
    assert(o->count == used + 2);

    /* The state side: a copy of the slot arrays comes back through p6_overlay_index; a duplicate or a medium beyond the manifest is refused. */
    uint16_t slot_of[P6_OVL_MEDIA * P6_OVL_DISK_SECTORS];
    memcpy(slot_of, o->slot_of, sizeof slot_of);
    p6_overlay_reset(o);
    assert(o->count == 0 && o->refused == 0 && p6_overlay_find(o, 0, 1) == -1);
    assert(p6_overlay_index(o, 2) == 0);                    /* an empty pool indexes */
    assert(p6_blob_overlay_export(0, buffer, BYTES, &exported) == P6_BLOB_OK && !memcmp(buffer, copy_a, BYTES));
    assert(p6_overlay_add(o, 3, 5, three) == 0 && p6_overlay_add(o, 16, 5, three) == -1 && p6_overlay_add(o, 0, 1760, three) == -1);
    assert(p6_overlay_index(o, 2) == -1 && p6_overlay_find(o, 3, 5) == -1);              /* medium 3 is beyond a manifest of two */
    assert(p6_overlay_index(o, 4) == 0 && p6_overlay_find(o, 3, 5) == 0);
    o->medium[0] = 0; o->sector[0] = 7; o->count = 2; o->medium[1] = 0; o->sector[1] = 7;
    assert(p6_overlay_index(o, 2) == -1);                   /* a sector named twice */
    (void)slot_of;

    /* Closing the streams and clearing the store frees the pool (the sanitizer's leak check is the assertion); the pool is gone afterwards. */
    assert(p6_blob_close(rw) == P6_BLOB_OK && p6_blob_close(rwb) == P6_BLOB_OK && p6_blob_close(rwc) == P6_BLOB_OK && p6_blob_close(base) == P6_BLOB_OK);
    p6_blob_clear();
    assert(p6_blob_overlay() == NULL && !p6_blob_overlay_writable("disk-a") && p6_blob_open("disk-a", "r+b", &h) == P6_BLOB_READ_ONLY);
    assert(!memcmp(base_a, copy_a, BYTES));
    puts("overlay ok");
    return 0;
}
