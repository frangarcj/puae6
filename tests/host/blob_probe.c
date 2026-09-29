#include "blob_io.h"
#include <assert.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#ifdef P6_FAULT_INJECTION
static int fail_allocation;
#ifdef __cplusplus
extern "C"
#endif
void *p6_test_malloc(size_t bytes) { return fail_allocation ? NULL : malloc(bytes); }
#endif

static uint64_t position(p6_blob_handle handle)
{
 p6_blob_info info; assert(p6_blob_stat(handle, &info) == P6_BLOB_OK); return info.position;
}
int main(void)
{
 unsigned char data[] = { 1, 2, 3, 4, 5, 6, 7 }, out[10];
 size_t items = 42; p6_blob_handle a, b, stale;
 p6_blob_clear();
#ifdef P6_FAULT_INJECTION
 fail_allocation = 1;
 assert(p6_blob_register("alloc-failure", P6_BLOB_ROM, data, sizeof data) == P6_BLOB_NO_MEMORY);
 assert(p6_blob_exists("alloc-failure") == P6_BLOB_NOT_FOUND);
 fail_allocation = 0;
 assert(p6_blob_register("alloc-failure", P6_BLOB_ROM, data, sizeof data) == 0);
 p6_blob_clear();
#endif
 const char *bad[] = { "", "/tmp/disk.adf", "../disk", "a/b", "a\\b", "C:disk", "http:x", "$HOME", "a..b", "\xc3\xb1" };
 for (size_t i = 0; i < sizeof bad / sizeof *bad; ++i) {
  assert(p6_blob_register(bad[i], P6_BLOB_ADF, data, sizeof data) == P6_BLOB_ARGUMENT);
  a = 99; assert(p6_blob_open(bad[i], "rb", &a) == P6_BLOB_ARGUMENT && a == 0);
 }
 assert(p6_blob_register(NULL, P6_BLOB_ROM, data, 1) == P6_BLOB_ARGUMENT);
 assert(p6_blob_register("missing-data", P6_BLOB_ROM, NULL, 1) == P6_BLOB_ARGUMENT);
 assert(p6_blob_register("archive", 99, data, sizeof data) == P6_BLOB_UNSUPPORTED);
 assert(p6_blob_register("overflow", P6_BLOB_ROM, data, SIZE_MAX) == P6_BLOB_RANGE);
 assert(p6_blob_register("disk.adf", P6_BLOB_ADF, data, sizeof data) == 0);
 data[0] = 99; /* The store owns a copy, not this caller allocation. */
 assert(p6_blob_register("disk.adf", P6_BLOB_ADF, data, sizeof data) == P6_BLOB_DUPLICATE);
 assert(p6_blob_exists("disk.adf") == 0 && p6_blob_exists("DISK.adf") == P6_BLOB_NOT_FOUND);
 const char *modes[] = { "r+b", "rb+", "w", "wb", "ab", "w+b" };
 for (size_t i = 0; i < sizeof modes / sizeof *modes; ++i) {
  a = 99; assert(p6_blob_open("disk.adf", modes[i], &a) == P6_BLOB_READ_ONLY && a == 0);
 }
 assert(p6_blob_open("disk.adf", "rt", &a) == P6_BLOB_UNSUPPORTED);
 assert(p6_blob_open("disk.adf", NULL, &a) == P6_BLOB_ARGUMENT);
 assert(p6_blob_open("disk.adf", "rb", NULL) == P6_BLOB_ARGUMENT);
 assert(p6_blob_open("absent", "rb", &a) == P6_BLOB_NOT_FOUND && a == 0);
 assert(p6_blob_open("disk.adf", "rb", &a) == 0);
 assert(p6_blob_open("disk.adf", "rb", &b) == 0 && a != b);
 p6_blob_info info; assert(p6_blob_stat(a, &info) == 0 && info.bytes == 7 && info.kind == P6_BLOB_ADF);
 memset(out, 0xa5, sizeof out);
 assert(p6_blob_read(a, out, sizeof out, 2, 4, &items) == 0 && items == 3 && position(a) == 6);
 for (int i = 0; i < 6; ++i) assert(out[i] == i + 1);
 assert(out[6] == 0xa5 && position(b) == 0); /* Partial element stays unread. */
 assert(p6_blob_read(a, out, sizeof out, 2, 1, &items) == 0 && items == 0 && position(a) == 6);
 assert(p6_blob_read(a, out, 1, 1, 1, &items) == 0 && items == 1 && out[0] == 7);
 assert(p6_blob_read(a, NULL, 0, 1, 1, &items) == 0 && items == 0 && position(a) == 7);
 unsigned char before[sizeof out]; memcpy(before, out, sizeof out);
 assert(p6_blob_read(b, out, sizeof out, SIZE_MAX, 2, &items) == P6_BLOB_RANGE && items == 0);
 assert(p6_blob_read(b, out, 2, 1, 3, &items) == P6_BLOB_CAPACITY && items == 0);
 assert(p6_blob_read(b, NULL, 3, 1, 3, &items) == P6_BLOB_ARGUMENT && position(b) == 0);
 assert(p6_blob_read(b, NULL, 0, 0, SIZE_MAX, &items) == 0 && items == 0);
 assert(p6_blob_read(b, NULL, 0, SIZE_MAX, 0, &items) == 0 && items == 0);
 assert(p6_blob_read(b, out, sizeof out, 1, 1, NULL) == P6_BLOB_ARGUMENT && position(b) == 0);
 assert(memcmp(before, out, sizeof out) == 0);
 assert(p6_blob_write(b, data, sizeof data) == P6_BLOB_READ_ONLY && position(b) == 0);
 assert(p6_blob_write(b, NULL, SIZE_MAX) == P6_BLOB_READ_ONLY && position(b) == 0);
 assert(p6_blob_seek(b, -2, P6_BLOB_END) == 0 && position(b) == 5);
 assert(p6_blob_seek(b, -2, P6_BLOB_CUR) == 0 && position(b) == 3);
 assert(p6_blob_seek(b, 1, 99) == P6_BLOB_ARGUMENT && position(b) == 3);
 assert(p6_blob_seek(b, INT64_MIN, P6_BLOB_CUR) == P6_BLOB_RANGE && position(b) == 0);
 assert(p6_blob_seek(b, INT64_MAX, P6_BLOB_END) == P6_BLOB_RANGE && position(b) == 7);
 assert(p6_blob_seek(b, 0, P6_BLOB_SET) == 0);
 assert(p6_blob_read(b, out, sizeof out, 1, 7, &items) == 0 && items == 7 && out[0] == 1);
 stale = a; assert(p6_blob_close(a) == 0 && p6_blob_close(a) == P6_BLOB_HANDLE);
 assert(p6_blob_open("disk.adf", "rb", &a) == 0 && a != stale);
 assert(p6_blob_seek(stale, 0, P6_BLOB_SET) == P6_BLOB_HANDLE);
 assert(p6_blob_read(stale, out, sizeof out, 1, 1, &items) == P6_BLOB_HANDLE && items == 0);
 assert(p6_blob_write(0, data, 1) == P6_BLOB_HANDLE);
 stale = a; p6_blob_clear(); p6_blob_clear();
 assert(p6_blob_stat(stale, &info) == P6_BLOB_HANDLE);
 assert(p6_blob_register("empty", P6_BLOB_KEY, NULL, 0) == 0);
 assert(p6_blob_open("empty", "rb", &a) == 0 && a != stale);
 assert(p6_blob_read(a, NULL, 0, 1, 1, &items) == 0 && items == 0);
 assert(p6_blob_close(a) == 0);
 p6_blob_handle handles[P6_BLOB_STREAMS];
 for (int i = 0; i < P6_BLOB_STREAMS; ++i) assert(p6_blob_open("empty", "rb", &handles[i]) == 0);
 assert(p6_blob_open("empty", "rb", &a) == P6_BLOB_CAPACITY && a == 0);
 for (int i = 0; i < P6_BLOB_STREAMS; ++i) assert(p6_blob_close(handles[i]) == 0);
 assert(p6_blob_open("empty", "rb", &a) == 0); p6_blob_clear();
 char id[P6_BLOB_ID_BYTES + 2]; memset(id, 'a', sizeof id); id[P6_BLOB_ID_BYTES + 1] = 0;
 assert(p6_blob_register(id, P6_BLOB_KEY, NULL, 0) == P6_BLOB_ARGUMENT);
 id[P6_BLOB_ID_BYTES] = 0; assert(p6_blob_register(id, P6_BLOB_KEY, NULL, 0) == 0);
 for (int i = 1; i < P6_BLOB_ASSETS; ++i) {
  snprintf(id, sizeof id, "asset-%d", i); assert(p6_blob_register(id, P6_BLOB_KEY, NULL, 0) == 0);
 }
 assert(p6_blob_register("full", P6_BLOB_KEY, NULL, 0) == P6_BLOB_CAPACITY);
 p6_blob_clear(); assert(p6_blob_exists("full") == P6_BLOB_NOT_FOUND);
 return 0;
}
