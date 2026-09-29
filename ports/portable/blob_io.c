#include "blob_io.h"
#include "alloc.h"
#include <string.h>

struct asset { char id[P6_BLOB_ID_BYTES + 1]; unsigned char *data; size_t bytes; uint32_t kind; };
struct stream { p6_blob_handle token; size_t asset, position; };
static struct asset assets[P6_BLOB_ASSETS];
static struct stream streams[P6_BLOB_STREAMS];
static p6_blob_handle serial;

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
 if (kind != P6_BLOB_ROM && kind != P6_BLOB_ADF && kind != P6_BLOB_KEY) return P6_BLOB_UNSUPPORTED;
 if (bytes > INT64_MAX) return P6_BLOB_RANGE;
 if (asset_index(id) >= 0) return P6_BLOB_DUPLICATE;
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) if (!assets[i].id[0]) {
  unsigned char *copy = bytes ? (unsigned char *)p6_malloc_array(1, bytes) : NULL;
  if (bytes && !copy) return P6_BLOB_NO_MEMORY;
  if (bytes) memcpy(copy, data, bytes);
  strcpy(assets[i].id, id); /* valid_id bounded this string before allocation. */
  assets[i].data = copy; assets[i].bytes = bytes; assets[i].kind = kind;
  return P6_BLOB_OK;
 }
 return P6_BLOB_CAPACITY;
}

int p6_blob_exists(const char *id)
{
 if (!valid_id(id)) return P6_BLOB_ARGUMENT;
 return asset_index(id) >= 0 ? P6_BLOB_OK : P6_BLOB_NOT_FOUND;
}

int p6_blob_open(const char *id, const char *mode, p6_blob_handle *out)
{
 if (!out) return P6_BLOB_ARGUMENT;
 *out = 0;
 if (!valid_id(id) || !mode) return P6_BLOB_ARGUMENT;
 if (strcmp(mode, "rb"))
  return strchr(mode, '+') || strchr(mode, 'w') || strchr(mode, 'a') ? P6_BLOB_READ_ONLY : P6_BLOB_UNSUPPORTED;
 int index = asset_index(id);
 if (index < 0) return P6_BLOB_NOT_FOUND;
 if (serial == UINT64_MAX) return P6_BLOB_CAPACITY;
 for (size_t i = 0; i < P6_BLOB_STREAMS; ++i) if (!streams[i].token) {
  streams[i].token = ++serial; streams[i].asset = (size_t)index; streams[i].position = 0;
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

int p6_blob_write(p6_blob_handle handle, const void *data, size_t bytes)
{
 (void)data; (void)bytes;
 return lookup(handle) ? P6_BLOB_READ_ONLY : P6_BLOB_HANDLE;
}
void p6_blob_clear(void)
{
 for (size_t i = 0; i < P6_BLOB_ASSETS; ++i) free(assets[i].data);
 memset(assets, 0, sizeof assets); memset(streams, 0, sizeof streams);
}
