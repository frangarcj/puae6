#include "sysdeps.h"
#include "zfile.h"
#include "zarchive.h"
#include "blob_io.h"
#include <errno.h>
#include <limits.h>

#ifdef P6_ZFILE_ALLOC_TEST
static int fail_after = -1, live_allocations;
static bool failing() { return fail_after >= 0 && fail_after-- == 0; }
extern "C" void *p6_test_calloc(size_t n, size_t width)
{ void *p = failing() ? NULL : calloc(n, width); if (p) ++live_allocations; return p; }
extern "C" void *p6_test_malloc(size_t size)
{ void *p = failing() ? NULL : malloc(size); if (p) ++live_allocations; return p; }
extern "C" void p6_test_free(void *p)
{ if (p) { assert(live_allocations > 0); --live_allocations; } free(p); }
#endif

int main()
{
 const uae_u8 bytes[] = {1, 2, 3, 4, 5, 6, 7};
 assert(!p6_blob_register("source.rom", P6_BLOB_ROM, bytes, sizeof bytes));
 zfile *source = zfile_fopen("source.rom", "rb", ZFD_CHECKONLY); assert(source);
 zfile *ram = zfile_fopen_empty(source, "tmp.rom", 7); assert(ram);
 assert(ram->size == 7 && ram->datasize == 7 && ram->allocsize == 7 && !ram->parent);
 assert(ram->zfdmask == ZFD_CHECKONLY && !ram->mode && !zfile_needwrite(ram));
 assert(zfile_iscompressed(ram) == 1 && zfile_gettype(ram) == ZFILE_ROM);
 for (int i = 0; i < 7; ++i) assert(ram->data[i] == 0);
 zfile_fclose(source);
 assert(!strcmp(zfile_getoriginalname(ram), "source.rom"));
 assert(zfile_fwrite(bytes, 1, 7, ram) == 7 && ram->seek == 7);
 // Aliasing during growth: source remains alive until both copies finish.
 assert(zfile_fwrite(ram->data, 1, 7, ram) == 7 && ram->size == 14);
 assert(!memcmp(ram->data, bytes, 7) && !memcmp(ram->data + 7, bytes, 7));
 assert(!zfile_fseek(ram, 1, SEEK_SET));
 assert(zfile_fwrite(ram->data, 1, 6, ram) == 6 && ram->data[1] == 1 && ram->data[6] == 6);
 uae_u8 output[20]; memset(output, 0xa5, sizeof output);
 assert(!zfile_fseek(ram, 0, SEEK_SET));
 assert(zfile_fread(output, 3, 5, ram) == 4 && ram->seek == 12 && output[12] == 0xa5);
 assert(!zfile_fread(output, 3, 1, ram) && !zfile_ferror(ram));
 assert(!zfile_fread(NULL, 0, SIZE_MAX, ram) && ram->seek == 12);
 assert(!zfile_fread(output, 2, SIZE_MAX, ram) && errno == ERANGE && ram->seek == 12);
 assert(!zfile_fread(NULL, 1, 1, ram) && errno == EINVAL && ram->seek == 12);
 assert(zfile_fseek(ram, INT64_MIN, SEEK_CUR) && ram->seek == 0);
 assert(zfile_fseek(ram, INT64_MAX, SEEK_END) && ram->seek == 14);
 assert(zfile_fseek(ram, 0, -1) && errno == EINVAL && ram->seek == 14);
 assert(!zfile_fwrite(bytes, SIZE_MAX, 2, ram) && errno == ERANGE && ram->size == 14);
 assert(!zfile_fwrite(bytes, 1, INT_MAX, ram) && errno == ERANGE && ram->size == 14);
 assert(!zfile_fwrite(NULL, 1, 1, ram) && errno == EINVAL && ram->seek == 14);
 assert(!zfile_truncate(ram, 15) && ram->size == 14);
 assert(!zfile_truncate(ram, -1) && ram->size == 14);
 assert(zfile_truncate(ram, 7) && ram->size == 7 && ram->datasize == 7 && ram->seek == 7);
 assert(!zfile_truncate(ram, 7) && ram->size == 7);
 size_t length = 99;
 assert(zfile_get_data_pointer(ram, &length) == ram->data && length == 7);
 assert(!zfile_get_data_pointer(ram, NULL) && errno == EINVAL);
 zfile *copy = zfile_fopen_load_zfile(ram); assert(copy && ram->seek == 7 && copy->seek == 0);
 assert(copy->data != ram->data && !memcmp(copy->data, ram->data, 7));
 zfile_fclose(ram); assert(copy->data[0] == 1); zfile_fclose(copy);

 uae_u8 caller[] = {9, 8, 7};
 ram = zfile_fopen_data("bytes", sizeof caller, caller); assert(ram);
 caller[0] = 0; assert(ram->data[0] == 9 && !ram->allocsize);
 assert(!zfile_fwrite(bytes, 1, 1, ram) && errno == EROFS && ram->seek == 0);
 assert(!strcmp(zfile_getoriginalname(ram), "bytes")); zfile_fclose(ram);
 assert(!zfile_fopen_data("bad", 1, NULL) && errno == EINVAL);
 assert(!zfile_fopen_empty(NULL, "large", (uae_u64)INT_MAX + 1) && errno == ERANGE);
 assert(!zfile_fopen_data("large", UINT64_MAX, bytes) && errno == ERANGE);
 char longname[82]; memset(longname, 'x', 81); longname[81] = 0;
 assert(!zfile_fopen_empty(NULL, longname) && errno == ENAMETOOLONG);
 assert(!zfile_fopen_empty(NULL, "\xc0\x80") && errno == EINVAL);
 ram = zfile_fopen_empty(NULL, NULL); assert(ram && ram->size == 0 && ram->allocsize == 1000);
 assert(!ram->originalname && !zfile_fwrite(NULL, 0, 100, ram));
 assert(zfile_fwrite(bytes, 1, 7, ram) == 7); zfile_fclose(ram);
 ram = zfile_fopen_data(NULL, 0, NULL); assert(ram && ram->data && ram->size == 0 && !ram->originalname); zfile_fclose(ram);
 source = zfile_fopen("source.rom", "rb"); assert(source);
 assert(!zfile_get_data_pointer(source, &length) && length == 0 && errno == ENOTSUP);
 assert(!zfile_truncate(source, 0) && errno == EROFS && source->size == 7);
#ifdef P6_ZFILE_ALLOC_TEST
 const int live_before = live_allocations;
 for (int step = 0; step < 2; ++step) {
  fail_after = step; assert(!zfile_fopen_empty(source, "fail", 7) && errno == ENOMEM);
  fail_after = step; assert(!zfile_fopen_data("fail", 7, bytes) && errno == ENOMEM);
  fail_after = step; assert(!zfile_fopen_load_zfile(source) && errno == ENOMEM && source->seek == 0);
  assert(live_allocations == live_before);
 }
 fail_after = -1;
 ram = zfile_fopen_empty(NULL, "growth", 7); assert(ram);
 assert(zfile_fwrite(bytes, 1, 7, ram) == 7);
 auto *before = ram->data;
 fail_after = 0; assert(!zfile_fwrite(bytes, 1, 7, ram) && errno == ENOMEM);
 assert(ram->data == before && ram->size == 7 && ram->allocsize == 7 && ram->seek == 7);
 assert(!memcmp(ram->data, bytes, 7));
 fail_after = -1; assert(zfile_fwrite(bytes, 1, 7, ram) == 7); zfile_fclose(ram);
#endif
 copy = zfile_fopen_load_zfile(source); assert(copy && source->seek == 7);
 zfile_fclose(source); p6_blob_clear(); assert(!memcmp(copy->data, bytes, 7));
 zfile_exit(); zfile_exit();
#ifdef P6_ZFILE_ALLOC_TEST
 assert(live_allocations == 0);
#endif
 return 0;
}
