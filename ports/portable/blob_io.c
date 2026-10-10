#include "blob_io.h"
#include "alloc.h"
#include "media/overlay.h"
#include <string.h>

struct asset { char id[P6_BLOB_ID_BYTES + 1]; unsigned char *data; size_t bytes; uint32_t kind; uint32_t medium; /* overlay medium + 1, 0 = none */ };
struct stream { p6_blob_handle token; size_t asset, position; int overlay; };
static struct asset assets[P6_BLOB_ASSETS];
static struct stream streams[P6_BLOB_STREAMS];
static p6_blob_handle serial;
static p6_overlay overlay;       /* the write overlay of the image: storage is reserved by p6_blob_overlay_reserve and released by p6_blob_clear */
static void *overlay_storage;

static int alphanumeric(unsigned char c)
{
 return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
}
static int valid_id(const char *id)
{
 if (!id || !alphanumeric((unsigned char)id[0])) return 0;
 for (size_t i = 1; i <= P6_BLOB_ID_BYTES; ++i) {
  unsigned char c = (unsigned char)id[i];
  if (!c) return 1;
  if (i == P6_BLOB_ID_BYTES || (!alphanumeric(c) && c != '_' && c != '-' && c != '.')) return 0;
  if (c == '.' && id[i-1] == '.') return 0;
 }
 return 0;
}
static struct stream *lookup(p6_blob_handle token)
{
 if (token) for (size_t i = 0; i < P6_BLOB_STREAMS; ++i)
  if (streams[i].token == token) return &streams[i];
 return NULL;
}
static int asset_index(const char *id)
{
 for (int i = 0; i < P6_BLOB_ASSETS; ++i) if (!strcmp(assets[i].id, id)) return i;
 return -1;
}

int p6_blob_register(const char *id, uint32_t kind, const void *data, size_t bytes)
{
 if (!valid_id(id) || (!data && bytes)) return P6_BLOB_ARGUMENT;
 if (kind != P6_BLOB_ROM && kind != P6_BLOB_ADF && kind != P6_BLOB_KEY && kind != P6_BLOB_HDF) return P6_BLOB_UNSUPPORTED;
 if (bytes > INT64_MAX) return P6_BLOB_RANGE;
 if (asset_index(id) >= 0) return P6_BLOB_DUPLICATE;
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) if (!assets[i].id[0]) {
  unsigned char *copy = bytes ? (unsigned char *)p6_malloc_array(1, bytes) : NULL;
  if (bytes && !copy) return P6_BLOB_NO_MEMORY;
  if (bytes) memcpy(copy, data, bytes);
  strcpy(assets[i].id, id); /* valid_id bounded this string before allocation. */
  assets[i].data = copy; assets[i].bytes = bytes; assets[i].kind = kind; assets[i].medium = 0;
  return P6_BLOB_OK;
 }
 return P6_BLOB_CAPACITY;
}

int p6_blob_exists(const char *id)
{
 if (!valid_id(id)) return P6_BLOB_ARGUMENT;
 return asset_index(id) >= 0 ? P6_BLOB_OK : P6_BLOB_NOT_FOUND;
}

int p6_blob_size(const char *id, uint64_t *bytes, uint32_t *kind)
{
 if (!valid_id(id)) return P6_BLOB_ARGUMENT;
 int index = asset_index(id);
 if (index < 0) return P6_BLOB_NOT_FOUND;
 if (bytes) *bytes = assets[index].bytes;
 if (kind) *kind = assets[index].kind;
 return P6_BLOB_OK;
}

int p6_blob_pread(const char *id, uint64_t offset, void *out, size_t length, size_t *got)
{
 if (!got) return P6_BLOB_ARGUMENT;
 *got = 0;
 if (!valid_id(id) || (length && !out)) return P6_BLOB_ARGUMENT;
 int index = asset_index(id);
 if (index < 0) return P6_BLOB_NOT_FOUND;
 const struct asset *a = &assets[index];
 if (offset >= a->bytes || !length) return P6_BLOB_OK;
 size_t n = a->bytes - (size_t)offset < length ? a->bytes - (size_t)offset : length;
 memcpy(out, a->data + (size_t)offset, n);
 *got = n;
 return P6_BLOB_OK;
}

int p6_blob_open(const char *id, const char *mode, p6_blob_handle *out)
{
 if (!out) return P6_BLOB_ARGUMENT;
 *out = 0;
 if (!valid_id(id) || !mode) return P6_BLOB_ARGUMENT;
 int over = 0;
 if (!strcmp(mode, "r+b")) over = 1;
 else if (strcmp(mode, "rb"))
  return strchr(mode, '+') || strchr(mode, 'w') || strchr(mode, 'a') ? P6_BLOB_READ_ONLY : P6_BLOB_UNSUPPORTED;
 int index = asset_index(id);
 if (over && (index < 0 || !assets[index].medium)) return P6_BLOB_READ_ONLY;
 if (index < 0) return P6_BLOB_NOT_FOUND;
 if (serial == UINT64_MAX) return P6_BLOB_CAPACITY;
 for (size_t i = 0; i < P6_BLOB_STREAMS; ++i) if (!streams[i].token) {
  streams[i].token = ++serial; streams[i].asset = (size_t)index; streams[i].position = 0; streams[i].overlay = over;
  *out = streams[i].token;
  return P6_BLOB_OK;
 }
 return P6_BLOB_CAPACITY;
}

int p6_blob_close(p6_blob_handle handle)
{
 struct stream *s = lookup(handle);
 if (!s) return P6_BLOB_HANDLE;
 memset(s, 0, sizeof *s);
 return P6_BLOB_OK;
}
int p6_blob_stat(p6_blob_handle handle, p6_blob_info *out)
{
 struct stream *s = lookup(handle);
 if (!out) return P6_BLOB_ARGUMENT;
 if (!s) return P6_BLOB_HANDLE;
 out->bytes = assets[s->asset].bytes; out->position = s->position;
 out->kind = assets[s->asset].kind; out->reserved = 0;
 return P6_BLOB_OK;
}

int p6_blob_read(p6_blob_handle handle, void *out, size_t capacity, size_t width, size_t count, size_t *items)
{
 if (!items) return P6_BLOB_ARGUMENT;
 *items = 0;
 struct stream *s = lookup(handle);
 if (!s) return P6_BLOB_HANDLE;
 if (width && count > SIZE_MAX / width) return P6_BLOB_RANGE;
 if (!width || !count) return P6_BLOB_OK;
 struct asset *a = &assets[s->asset];
 size_t available = (a->bytes - s->position) / width;
 size_t n = count < available ? count : available;
 size_t bytes = n * width;
 if (bytes > capacity) return P6_BLOB_CAPACITY;
 if (!out && bytes) return P6_BLOB_ARGUMENT;
 if (bytes) memcpy(out, a->data + s->position, bytes);
 if (bytes && s->overlay) {
  /* The sectors the guest wrote replace the base's. */
  const uint32_t medium = a->medium - 1;
  for (size_t sec = s->position / P6_OVL_SECTOR_BYTES; sec <= (s->position + bytes - 1) / P6_OVL_SECTOR_BYTES; ++sec) {
   int32_t slot = p6_overlay_find(&overlay, medium, (uint32_t)sec);
   if (slot < 0) continue;
   size_t from = sec * P6_OVL_SECTOR_BYTES, to = from + P6_OVL_SECTOR_BYTES;
   size_t lo = from > s->position ? from : s->position, hi = to < s->position + bytes ? to : s->position + bytes;
   memcpy((unsigned char *)out + (lo - s->position), p6_overlay_sector(&overlay, (uint32_t)slot) + (lo - from), hi - lo);
  }
 }
 s->position += bytes; *items = n;
 return P6_BLOB_OK;
}

int p6_blob_seek(p6_blob_handle handle, int64_t offset, int origin)
{
 struct stream *s = lookup(handle);
 if (!s) return P6_BLOB_HANDLE;
 uint64_t size = assets[s->asset].bytes, base;
 if (origin == P6_BLOB_SET) base = 0;
 else if (origin == P6_BLOB_CUR) base = s->position;
 else if (origin == P6_BLOB_END) base = size;
 else return P6_BLOB_ARGUMENT;
 if (offset < 0) {
  uint64_t magnitude = (uint64_t)(-(offset + 1)) + 1;
  if (magnitude > base) { s->position = 0; return P6_BLOB_RANGE; }
  s->position = (size_t)(base - magnitude);
 } else {
  if ((uint64_t)offset > size - base) { s->position = (size_t)size; return P6_BLOB_RANGE; }
  s->position = (size_t)(base + (uint64_t)offset);
 }
 return P6_BLOB_OK;
}

/* The content of one sector as the stream sees it after `data` (the bytes at [position, position + bytes)) is written over it. */
static void merged_sector(const struct asset *a, size_t sector, size_t position, const unsigned char *data, size_t bytes, unsigned char *out)
{
 int32_t slot = p6_overlay_find(&overlay, a->medium - 1, (uint32_t)sector);
 size_t from = sector * P6_OVL_SECTOR_BYTES, to = from + P6_OVL_SECTOR_BYTES;
 memcpy(out, slot >= 0 ? p6_overlay_sector(&overlay, (uint32_t)slot) : a->data + from, P6_OVL_SECTOR_BYTES);
 size_t lo = from > position ? from : position, hi = to < position + bytes ? to : position + bytes;
 if (hi > lo) memcpy(out + (lo - from), data + (lo - position), hi - lo);
}

int p6_blob_write(p6_blob_handle handle, const void *data, size_t bytes)
{
 struct stream *s = lookup(handle);
 if (!s) return P6_BLOB_HANDLE;
 if (!s->overlay) return P6_BLOB_READ_ONLY;
 if (!bytes) return P6_BLOB_OK;
 if (!data) return P6_BLOB_ARGUMENT;
 struct asset *a = &assets[s->asset];
 if (bytes > a->bytes - s->position) return P6_BLOB_RANGE;      /* the medium never grows */
 const uint32_t medium = a->medium - 1;
 const size_t first = s->position / P6_OVL_SECTOR_BYTES, last = (s->position + bytes - 1) / P6_OVL_SECTOR_BYTES;
 unsigned char merged[P6_OVL_SECTOR_BYTES];
 uint32_t need = 0;
 for (size_t sec = first; sec <= last; ++sec) {
  merged_sector(a, sec, s->position, (const unsigned char *)data, bytes, merged);
  if (p6_overlay_find(&overlay, medium, (uint32_t)sec) < 0 && !p6_overlay_same(merged, a->data + sec * P6_OVL_SECTOR_BYTES)) ++need;
 }
 if (need > p6_overlay_free(&overlay)) { ++overlay.refused; return P6_BLOB_NO_SPACE; }
 for (size_t sec = first; sec <= last; ++sec) {
  merged_sector(a, sec, s->position, (const unsigned char *)data, bytes, merged);
  int32_t slot = p6_overlay_find(&overlay, medium, (uint32_t)sec);
  if (slot >= 0) memcpy(p6_overlay_sector(&overlay, (uint32_t)slot), merged, P6_OVL_SECTOR_BYTES);
  else if (!p6_overlay_same(merged, a->data + sec * P6_OVL_SECTOR_BYTES)) p6_overlay_add(&overlay, medium, (uint32_t)sec, merged);
 }
 s->position += bytes;
 return P6_BLOB_OK;
}

int p6_blob_overlay_reserve(void)
{
 if (overlay_storage) return P6_BLOB_DUPLICATE;
 overlay_storage = p6_malloc_array(1, P6_OVL_STORAGE_BYTES);
 if (!overlay_storage) return P6_BLOB_NO_MEMORY;
 p6_overlay_use(&overlay, overlay_storage);
 p6_overlay_reset(&overlay); /* only the index needs to be zero: a slot is read only below the count */
 return P6_BLOB_OK;
}
int p6_blob_overlay_bind(const char *id, uint32_t medium)
{
 if (!valid_id(id) || medium >= P6_OVL_MEDIA) return P6_BLOB_ARGUMENT;
 if (!overlay_storage) return P6_BLOB_UNSUPPORTED;
 int index = asset_index(id);
 if (index < 0) return P6_BLOB_NOT_FOUND;
 if (assets[index].kind != P6_BLOB_ADF || assets[index].bytes != (size_t)P6_OVL_DISK_SECTORS * P6_OVL_SECTOR_BYTES) return P6_BLOB_RANGE;
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) if (assets[i].medium == medium + 1) return P6_BLOB_DUPLICATE;
 if (assets[index].medium) return P6_BLOB_DUPLICATE;
 assets[index].medium = medium + 1;
 return P6_BLOB_OK;
}
int p6_blob_overlay_writable(const char *id)
{
 if (!valid_id(id)) return 0;
 int index = asset_index(id);
 return index >= 0 && assets[index].medium != 0;
}
static int bound_medium(const char *id)
{
 if (!overlay_storage || !valid_id(id)) return -1;
 const int index = asset_index(id);
 return index >= 0 && assets[index].medium ? (int)assets[index].medium - 1 : -1;
}
int p6_blob_raw_write(const char *id, unsigned int track, const unsigned short *words, unsigned int bits)
{
 const int medium = bound_medium(id);
 if (medium < 0 || !words) return P6_BLOB_ARGUMENT;
 if (p6_overlay_raw_put(&overlay, (uint32_t)medium, track, words, bits)) { ++overlay.refused; return P6_BLOB_NO_SPACE; }
 return P6_BLOB_OK;
}
int p6_blob_raw_read(const char *id, unsigned int track, unsigned short *words, unsigned int *bits)
{
 const int medium = bound_medium(id);
 if (medium < 0 || !words || !bits) return 0;
 const int32_t slot = p6_overlay_raw_find(&overlay, (uint32_t)medium, track);
 if (slot < 0) return 0;
 *bits = overlay.raw_bits[slot];
 memcpy(words, p6_overlay_raw_slot(&overlay, (uint32_t)slot), (size_t)((*bits + 15u) / 16u) * sizeof(uint16_t));
 return 1;
}
void p6_blob_raw_clear(const char *id, unsigned int track)
{
 const int medium = bound_medium(id);
 if (medium >= 0) p6_overlay_raw_remove(&overlay, (uint32_t)medium, track);
}
int p6_blob_overlay_room(unsigned int sectors) { return overlay_storage && p6_overlay_free(&overlay) >= sectors; }
struct p6_overlay *p6_blob_overlay(void) { return overlay_storage ? &overlay : NULL; }
int p6_blob_overlay_export(uint32_t medium, void *out, size_t capacity, size_t *written)
{
 if (!written) return P6_BLOB_ARGUMENT;
 *written = 0;
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) if (assets[i].medium == medium + 1 && medium < P6_OVL_MEDIA) {
  const struct asset *a = &assets[i];
  if (capacity < a->bytes) return P6_BLOB_CAPACITY;
  if (!out) return P6_BLOB_ARGUMENT;
  for (uint32_t k = 0; k < overlay.raw_count; ++k) if (overlay.raw_medium[k] == medium) return P6_BLOB_UNSUPPORTED;
  memcpy(out, a->data, a->bytes);
  for (uint32_t sec = 0; sec < P6_OVL_DISK_SECTORS; ++sec) {
   int32_t slot = p6_overlay_find(&overlay, medium, sec);
   if (slot >= 0) memcpy((unsigned char *)out + (size_t)sec * P6_OVL_SECTOR_BYTES, p6_overlay_sector(&overlay, (uint32_t)slot), P6_OVL_SECTOR_BYTES);
  }
  *written = a->bytes;
  return P6_BLOB_OK;
 }
 return P6_BLOB_NOT_FOUND;
}
void p6_blob_clear(void)
{
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) free(assets[i].data);
 memset(assets, 0, sizeof assets); memset(streams, 0, sizeof streams);
 free(overlay_storage);
 overlay_storage = NULL; memset(&overlay, 0, sizeof overlay);
}
