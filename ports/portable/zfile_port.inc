/* Included only by the materialized zfile.cpp under P6_PORTABLE. The engine's
 * zfile API/layout stay upstream-owned; host paths/archives are not compiled.
 * Excluded archives and host services remain undefined, not success stubs. */
#include "zfile.h"
#include "zarchive.h"
#include "blob_io.h"
#include "crc32.h"
#include <errno.h>
#include <limits.h>
#include <type_traits>

struct p6_zfile_owner {
 zfile file; /* Actual upstream struct, first member. */
 p6_blob_handle blob;
 int error;
 uint32_t kind;
 char name[P6_BLOB_ID_BYTES + 1], original[P6_BLOB_ID_BYTES + 1], mode[4];
};
static_assert(std::is_standard_layout<p6_zfile_owner>::value && offsetof(p6_zfile_owner, file) == 0,
              "zfile must be the pointer-interconvertible first member");
static zfile *p6_zfiles;

static p6_zfile_owner *p6_owner(zfile *file)
{
 for (zfile *p = p6_zfiles; p; p = p->next)
  if (p == file) return reinterpret_cast<p6_zfile_owner *>(p);
 errno = EBADF;
 return NULL;
}
static int p6_zfail(p6_zfile_owner *owner, int status)
{
 int code = EIO;
 switch (status) {
 case P6_BLOB_ARGUMENT: code = EINVAL; break;
 case P6_BLOB_NOT_FOUND: code = ENOENT; break;
 case P6_BLOB_UNSUPPORTED: code = ENOTSUP; break;
 case P6_BLOB_READ_ONLY: code = EROFS; break;
 case P6_BLOB_CAPACITY: code = EMFILE; break;
 case P6_BLOB_NO_MEMORY: code = ENOMEM; break;
 case P6_BLOB_HANDLE: code = EBADF; break;
 case P6_BLOB_RANGE: code = ERANGE; break;
 case P6_BLOB_NO_SPACE: code = ENOSPC; break;
 default: break;
 }
 if (owner) owner->error = code;
 errno = code;
 return 1;
}
static bool p6_refresh(p6_zfile_owner *owner)
{
 if (owner->file.data) return true;
 if (owner->file.parent) {
  auto *parent = p6_owner(owner->file.parent);
  if (!parent || !p6_refresh(parent)) { p6_zfail(owner, P6_BLOB_HANDLE); return false; }
  return true;
 }
 p6_blob_info info;
 int result = p6_blob_stat(owner->blob, &info);
 if (result) { p6_zfail(owner, result); return false; }
 owner->file.size = owner->file.datasize = (uae_s64)info.bytes;
 owner->file.seek = (uae_s64)info.position;
 owner->kind = info.kind;
 return true;
}

zfile *zfile_fopen(const TCHAR *name, const TCHAR *mode, int mask, int index)
{
 if ((mask & ~ZFD_CHECKONLY) || index != 0) { errno = ENOTSUP; return NULL; }
 p6_blob_handle blob;
 int result = p6_blob_open(name, mode, &blob);
 if (result) { p6_zfail(NULL, result); return NULL; }
 auto *owner = xcalloc(p6_zfile_owner, 1);
 if (!owner) { p6_blob_close(blob); errno = ENOMEM; return NULL; }
 owner->blob = blob;
 if (!p6_refresh(owner)) { p6_blob_close(blob); xfree(owner); return NULL; }
 strcpy(owner->name, name); /* blob_open already bounded and validated this ID. */
 strcpy(owner->mode, mode[1] == '+' ? "r+b" : "rb"); /* blob_open accepted exactly one of the two */
 owner->file.name = owner->file.originalname = owner->name;
 owner->file.mode = owner->mode;
 owner->file.opencnt = 1; owner->file.dataseek = 1; owner->file.zfdmask = mask;
 owner->file.next = p6_zfiles; p6_zfiles = &owner->file;
 return &owner->file;
}
zfile *zfile_fopen(const TCHAR *name, const TCHAR *mode, int mask) { return zfile_fopen(name, mode, mask, 0); }
zfile *zfile_fopen(const TCHAR *name, const TCHAR *mode) { return zfile_fopen(name, mode, 0, 0); }
int zfile_exists(const TCHAR *name)
{
 int result = p6_blob_exists(name);
 if (result) { p6_zfail(NULL, result); return 0; }
 return 1;
}

void zfile_fclose(zfile *file)
{
 if (!file) return;
 zfile **link = &p6_zfiles;
 while (*link && *link != file) link = &(*link)->next;
 if (!*link) { errno = EBADF; return; }
 auto *owner = reinterpret_cast<p6_zfile_owner *>(file);
 if (--file->opencnt > 0) return;
 *link = file->next;
 zfile *parent = file->parent;
 if (file->data) xfree(file->data);
 else if (!parent) {
  int result = p6_blob_close(owner->blob);
  if (result) p6_zfail(NULL, result);
 }
 xfree(owner);
 if (parent) zfile_fclose(parent);
}
void zfile_exit(void) { while (p6_zfiles) zfile_fclose(p6_zfiles); }

uae_s64 zfile_ftell(zfile *file)
{
 auto *owner = p6_owner(file);
 return owner && p6_refresh(owner) ? file->seek : -1;
}
uae_s64 zfile_size(zfile *file)
{
 auto *owner = p6_owner(file);
 return owner && p6_refresh(owner) ? file->size : -1;
}
uae_s32 zfile_ftell32(zfile *file) { uae_s64 n = zfile_ftell(file); return n > INT_MAX ? INT_MAX : (uae_s32)n; }
uae_s32 zfile_size32(zfile *file) { uae_s64 n = zfile_size(file); return n > INT_MAX ? INT_MAX : (uae_s32)n; }
uae_s64 zfile_fseek(zfile *file, uae_s64 offset, int whence)
{
 auto *owner = p6_owner(file);
 if (!owner) return 1;
 int origin;
 if (whence == SEEK_SET) origin = P6_BLOB_SET;
 else if (whence == SEEK_CUR) origin = P6_BLOB_CUR;
 else if (whence == SEEK_END) origin = P6_BLOB_END;
 else return p6_zfail(owner, P6_BLOB_ARGUMENT);
 if (file->data || file->parent) {
  uae_s64 base = whence == SEEK_SET ? 0 : whence == SEEK_CUR ? file->seek : file->size;
  bool outside = offset < -base || offset > file->size - base;
  file->seek = offset < -base ? 0 : offset > file->size - base ? file->size : base + offset;
  return outside ? p6_zfail(owner, P6_BLOB_RANGE) : 0;
 }
 int result = p6_blob_seek(owner->blob, offset, origin);
 if (!p6_refresh(owner)) return 1;
 return result ? p6_zfail(owner, result) : 0;
}
size_t zfile_fread(void *out, size_t width, size_t count, zfile *file)
{
 auto *owner = p6_owner(file);
 if (!owner) return 0;
 if (file->data || file->parent) {
  if (file->parent && !p6_refresh(owner)) return 0;
  if (width && count > SIZE_MAX / width) { p6_zfail(owner, P6_BLOB_RANGE); return 0; }
  if (!width || !count) return 0;
  size_t available = (size_t)(file->size - file->seek) / width;
  if (count > available) count = available;
  if (count && !out) { p6_zfail(owner, P6_BLOB_ARGUMENT); return 0; }
  if (file->parent && count) {
   // Upstream views advance the root cursor as well as their own cursor.
   if (zfile_fseek(file->parent, (uae_s64)file->offset + file->seek, SEEK_SET)) {
    owner->error = errno; return 0;
   }
   size_t items = zfile_fread(out, width, count, file->parent);
   file->seek += width * items;
   if (items != count) p6_zfail(owner, P6_BLOB_RANGE);
   return items;
  }
  if (count) memmove(out, file->data + file->seek, width * count);
  file->seek += width * count;
  return count;
 }
 size_t items;
 /* Legacy API has no capacity argument: caller owns width*count writable bytes.
  * blob_read checks multiplication/EOF before touching the destination. */
 int result = p6_blob_read(owner->blob, out, SIZE_MAX, width, count, &items);
 if (result) { p6_zfail(owner, result); return 0; }
 if (!p6_refresh(owner)) return 0;
 return items;
}
uae_s32 zfile_fread32(void *out, size_t width, size_t count, zfile *file)
{
 auto *owner = p6_owner(file);
 if (!owner || !p6_refresh(owner)) return 0;
 if (width && count > INT_MAX && (uae_u64)(file->size - file->seek) / width > INT_MAX) {
  p6_zfail(owner, P6_BLOB_RANGE); return 0;
 }
 return (uae_s32)zfile_fread(out, width, count, file);
}
size_t zfile_fwrite(const void *data, size_t width, size_t count, zfile *file)
{
 auto *owner = p6_owner(file);
 if (!owner) return 0;
 if (!file->data && !file->parent && strchr(owner->mode, '+')) {
  /* A disk image with a write overlay (T27): the bytes go to the overlay, the base is never touched, the file does not grow, and the
   * write is all or nothing. The engine ignores the result of a track write, so a refusal is also counted by the overlay (p6_blob_write). */
  if (width && count > SIZE_MAX / width) { p6_zfail(owner, P6_BLOB_RANGE); return 0; }
  if (!width || !count) return 0;
  int result = p6_blob_write(owner->blob, data, width * count);
  if (result) { p6_zfail(owner, result); return 0; }
  return p6_refresh(owner) ? count : 0;
 }
 if (!file->data || !file->allocsize) { p6_zfail(owner, P6_BLOB_READ_ONLY); return 0; }
 if (width && count > SIZE_MAX / width) { p6_zfail(owner, P6_BLOB_RANGE); return 0; }
 size_t bytes = width * count;
 if (!bytes) return 0;
 if (!data) { p6_zfail(owner, P6_BLOB_ARGUMENT); return 0; }
 if (bytes > (size_t)(INT_MAX - file->seek)) { p6_zfail(owner, P6_BLOB_RANGE); return 0; }
 size_t end = (size_t)file->seek + bytes;
 if (end > (size_t)file->allocsize) {
  // Upstream growth policy, computed without its signed-int overflow.
  size_t increment = ((size_t)file->size / 2 + bytes + 7) & ~(size_t)3;
  if (increment < 10000) increment = 10000;
  size_t capacity = increment > (size_t)(INT_MAX - file->allocsize)
                  ? INT_MAX : (size_t)file->allocsize + increment;
  // Allocate before releasing: source may alias this stream's own data.
  auto *next = xmalloc(uae_u8, capacity);
  if (!next) { p6_zfail(owner, P6_BLOB_NO_MEMORY); return 0; }
  memcpy(next, file->data, (size_t)file->size);
  memmove(next + file->seek, data, bytes);
  xfree(file->data); file->data = next; file->allocsize = capacity;
 } else memmove(file->data + file->seek, data, bytes);
 file->seek = end;
 if (file->seek > file->size) file->size = file->datasize = file->seek;
 return count;
}

int zfile_ferror(zfile *file) { auto *owner = p6_owner(file); return owner ? owner->error : EBADF; }
bool zfile_needwrite(zfile *file)
{
 auto *owner = p6_owner(file);
 return owner && file->mode && (strchr(file->mode, '+') || strchr(file->mode, 'w') || strchr(file->mode, 'a'));
}
int zfile_iscompressed(zfile *file) { return p6_owner(file) ? (file->data != NULL) : -1; }
int zfile_gettype(zfile *file)
{
 auto *owner = p6_owner(file);
 if (!owner) return ZFILE_UNKNOWN;
 return owner->kind == P6_BLOB_ROM ? ZFILE_ROM : owner->kind == P6_BLOB_ADF ? ZFILE_DISKIMAGE
      : owner->kind == P6_BLOB_KEY ? ZFILE_KEY : ZFILE_UNKNOWN;
}
TCHAR *zfile_getname(zfile *file) { auto *owner = p6_owner(file); return owner ? owner->name : NULL; }
TCHAR *zfile_getoriginalname(zfile *file) { return p6_owner(file) ? file->originalname : NULL; }
TCHAR *zfile_getfilename(zfile *file)
{
 char *name = zfile_getname(file), *base = name;
 if (!name) return NULL;
 for (size_t i = 0; i <= P6_BLOB_ID_BYTES; ++i) {
  if (!name[i]) return base;
  if (name[i] == '/' || name[i] == '\\' || name[i] == ':') base = name + i + 1;
 }
 errno = ENAMETOOLONG; return NULL;
}

/* Names are bounded display labels, never paths to open. prev contributes only
 * copied metadata; it is not retained. INT_MAX is upstream's RAM size limit. */
static p6_zfile_owner *p6_new_owner(zfile *prev, const char *name, const char *original)
{
 if (prev && !p6_owner(prev)) return NULL;
 if (!name) name = "";
 size_t length = 0;
 while (length <= P6_BLOB_ID_BYTES && name[length]) ++length;
 if (length > P6_BLOB_ID_BYTES) { errno = ENAMETOOLONG; return NULL; }
 size_t original_length = 0;
 if (original) while (original_length <= P6_BLOB_ID_BYTES && original[original_length]) ++original_length;
 if (original_length > P6_BLOB_ID_BYTES) { errno = ENAMETOOLONG; return NULL; }
 auto *owner = xcalloc(p6_zfile_owner, 1);
 if (!owner) { errno = ENOMEM; return NULL; }
 if (p6_tchar_copy(owner->name, sizeof owner->name, name, length)) {
  xfree(owner); errno = EINVAL; return NULL;
 }
 zfile *file = &owner->file;
 file->name = owner->name;
 if (original) {
  if (p6_tchar_copy(owner->original, sizeof owner->original, original, original_length)) {
   xfree(owner); errno = EINVAL; return NULL;
  }
  file->originalname = owner->original;
 }
 file->zfdmask = prev ? prev->zfdmask : 0;
 owner->kind = prev ? p6_owner(prev)->kind : 0;
 file->opencnt = 1;
 return owner; // Not published until every allocation and reference is ready.
}
static zfile *p6_publish(p6_zfile_owner *owner)
{
 owner->file.next = p6_zfiles; p6_zfiles = &owner->file;
 return p6_zfiles;
}
static zfile *p6_ram_file(zfile *prev, const char *name, uae_u64 size, bool writable)
{
 if (prev && !p6_owner(prev)) return NULL;
 if (size > INT_MAX) { errno = ERANGE; return NULL; }
 auto *owner = p6_new_owner(prev, name, prev ? prev->originalname : writable ? NULL : name);
 if (!owner) return NULL;
 zfile *file = &owner->file;
 size_t capacity = size ? (size_t)size : 1000;
 file->data = xcalloc(uae_u8, capacity);
 if (!file->data) { xfree(owner); errno = ENOMEM; return NULL; }
 file->size = file->datasize = size;
 file->allocsize = writable ? capacity : 0;
 return p6_publish(owner);
}
zfile *zfile_fopen_empty(zfile *prev, const TCHAR *name, uae_u64 size)
{ return p6_ram_file(prev, name, size, true); }
zfile *zfile_fopen_empty(zfile *prev, const TCHAR *name)
{ return zfile_fopen_empty(prev, name, 0); }
zfile *zfile_fopen_data(const TCHAR *name, uae_u64 size, const uae_u8 *data)
{
 if (size && !data) { errno = EINVAL; return NULL; }
 zfile *file = p6_ram_file(NULL, name, size, false);
 if (file && size) memcpy(file->data, data, (size_t)size);
 return file;
}
zfile *zfile_fopen_load_zfile(zfile *source)
{
 uae_s64 size = zfile_size(source), position = zfile_ftell(source);
 if (size < 0 || position < 0) return NULL;
 zfile *copy = zfile_fopen_empty(source, source->name, (uae_u64)size);
 if (!copy) return NULL;
 if (zfile_fseek(source, 0, SEEK_SET) || zfile_fread(copy->data, 1, (size_t)size, source) != (size_t)size) {
  zfile_fseek(source, position, SEEK_SET); zfile_fclose(copy); errno = EIO; return NULL;
 }
 return copy; // Like upstream: successful load leaves source at EOF, copy at zero.
}
uae_u8 *zfile_get_data_pointer(zfile *file, size_t *length)
{
 if (!length) { errno = EINVAL; return NULL; }
 *length = 0;
 if (!p6_owner(file)) return NULL;
 if (!file->data) { errno = ENOTSUP; return NULL; }
 *length = (size_t)file->size;
 return file->data; // Borrowed until growth/close; never exposes registered assets.
}

int zfile_truncate(zfile *file, uae_s64 size)
{
 auto *owner = p6_owner(file);
 if (!owner) return 0;
 if (!file->data) { p6_zfail(owner, P6_BLOB_READ_ONLY); return 0; }
 if (size < 0 || size > file->size) { p6_zfail(owner, P6_BLOB_RANGE); return 0; }
 if (size == file->size) return 0;
 file->size = file->datasize = size;
 if (file->seek > size) file->seek = size;
 return 1;
}

zfile *zfile_fopen_parent(zfile *source, const TCHAR *name, uae_u64 offset, uae_u64 size)
{
 uae_s64 source_size = zfile_size(source);
 if (source_size < 0) return NULL;
 if (offset > (uae_u64)source_size || size > (uae_u64)source_size - offset) {
  errno = ERANGE; return NULL;
 }
 zfile *root = source->parent ? source->parent : source;
 offset += source->parent ? source->offset : 0; // Valid source range fits INT64_MAX.
 uae_s64 root_size = zfile_size(root);
 if (root_size < 0) return NULL;
 if (offset > (uae_u64)root_size || size > (uae_u64)root_size - offset) { errno = ERANGE; return NULL; }
 if (root->opencnt == INT_MAX) { errno = EMFILE; return NULL; }
 auto *owner = p6_new_owner(source, name ? name : source->name, source->originalname);
 if (!owner) return NULL;
 owner->file.parent = root; owner->file.useparent = 1; owner->file.offset = offset;
 owner->file.size = owner->file.datasize = size;
 ++root->opencnt;
 return p6_publish(owner);
}
zfile *zfile_dup(zfile *source)
{
 uae_s64 position = zfile_ftell(source);
 if (position < 0) return NULL;
 if (source->parent) return zfile_fopen_parent(source, source->name, 0, source->size);
 zfile *copy;
 if (source->data) {
  copy = p6_ram_file(source, source->name, source->size, false);
  if (copy && source->size) memcpy(copy->data, source->data, (size_t)source->size);
 } else copy = zfile_fopen(source->name, source->mode, source->zfdmask);
 if (copy && zfile_fseek(copy, position, SEEK_SET)) { zfile_fclose(copy); return NULL; }
 return copy;
}

uae_u8 *zfile_getdata(zfile *file, uae_s64 offset, int length, int *out_length)
{
 if (out_length) *out_length = 0;
 auto *owner = p6_owner(file);
 if (!owner || !p6_refresh(owner)) return NULL;
 uae_s64 count = length < 0 ? file->size : length;
 if (offset < 0 || offset > file->size || count > INT_MAX || count > file->size - offset) {
  p6_zfail(owner, P6_BLOB_RANGE); return NULL;
 }
 auto *data = xmalloc(uae_u8, count ? (size_t)count : 1);
 if (!data) { p6_zfail(owner, P6_BLOB_NO_MEMORY); return NULL; }
 uae_s64 position = file->seek, parent_position = file->parent ? zfile_ftell(file->parent) : 0;
 bool failed = zfile_fseek(file, offset, SEEK_SET) || zfile_fread(data, 1, (size_t)count, file) != (size_t)count;
 if (zfile_fseek(file, position, SEEK_SET)) failed = true;
 if (file->parent && zfile_fseek(file->parent, parent_position, SEEK_SET)) failed = true;
 if (failed) { xfree(data); p6_zfail(owner, P6_BLOB_RANGE); return NULL; }
 if (out_length) *out_length = (int)count;
 return data; // Owned copy, free with xfree; negative length means full size, as upstream.
}
uae_u32 zfile_crc32(zfile *file)
{
 auto *owner = p6_owner(file);
 if (!owner || !p6_refresh(owner)) return 0;
 if (file->data) return get_crc32(file->data, (int)file->size);
 if (!file->parent && strchr(owner->mode, '+')) {
  /* The identity of a disk is its base: the CRC a drive reports (and a state carries) does not change with what the guest wrote. */
  zfile *base = zfile_fopen(owner->name, "rb", 0);
  uae_u32 crc = base ? zfile_crc32(base) : 0;
  zfile_fclose(base);
  return crc;
 }
 int length;
 uae_u8 *data = zfile_getdata(file, 0, -1, &length);
 if (!data) return 0;
 uae_u32 crc = get_crc32(data, length);
 xfree(data); return crc;
}
int zfile_getc(zfile *file)
{
 uae_u8 byte;
 return zfile_fread(&byte, 1, 1, file) == 1 ? byte : -1;
}
int zfile_putc(int value, zfile *file)
{
 uae_u8 byte = (uae_u8)value;
 return zfile_fwrite(&byte, 1, 1, file) == 1 ? 1 : -1; // Upstream returns 1, not the byte.
}
size_t zfile_fputs(zfile *file, const TCHAR *text)
{
 auto *owner = p6_owner(file);
 if (!owner) return 0;
 if (!text) { p6_zfail(owner, P6_BLOB_ARGUMENT); return 0; }
 return zfile_fwrite(text, strlen(text), 1, file);
}
static char *p6_read_line(char *out, int capacity, zfile *file, bool tchar_line)
{
 auto *owner = p6_owner(file);
 if (!owner || !p6_refresh(owner)) return NULL;
 if (!out || capacity <= 0) { p6_zfail(owner, P6_BLOB_ARGUMENT); return NULL; }
 int used = 0;
 while (used < capacity - 1) {
  int byte = zfile_getc(file);
  if (byte < 0) {
   if (!used || file->seek < file->size) {
    if (used) out[used] = 0; // Partial I/O error is not a successful line.
    return NULL;
   }
   break;
  }
  if (tchar_line && !byte) { if (!used) return NULL; break; }
  out[used++] = (char)byte;
  if (byte == '\n') break;
 }
 out[used] = 0;
 // UTF-8 bytes, no locale conversion. Like upstream RAM: raw lines retain NUL;
 // TCHAR lines consume it as a terminator and return just past the final NUL.
 return tchar_line ? out + used + 1 : out;
}
char *zfile_fgetsa(char *out, int capacity, zfile *file) { return p6_read_line(out, capacity, file, false); }
TCHAR *zfile_fgets(TCHAR *out, int capacity, zfile *file) { return p6_read_line(out, capacity, file, true); }
