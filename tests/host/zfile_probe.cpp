#include "sysdeps.h"
#include "zfile.h"
#include "zarchive.h"
#include "blob_io.h"
#include <errno.h>
#include <limits.h>

#ifdef P6_ZFILE_ALLOC_TEST
static bool fail_wrapper_allocation;
extern "C" void *p6_test_calloc(size_t n, size_t width)
{
 return fail_wrapper_allocation ? NULL : calloc(n, width);
}
#endif

int main(void)
{
 static_assert(ZFD_NORMAL == 0 && ZFD_DISKHISTORY == 0, "profile excludes auto-unpack/history");
 const unsigned char data[] = { 1, 2, 3, 4, 5, 6, 7 };
 p6_blob_clear(); assert(p6_blob_register("disk.adf", P6_BLOB_ADF, data, sizeof data) == 0);
 assert(p6_blob_register("rom", P6_BLOB_ROM, data, sizeof data) == 0);
 assert(p6_blob_register("key", P6_BLOB_KEY, data, sizeof data) == 0);
#ifdef P6_ZFILE_ALLOC_TEST
 fail_wrapper_allocation = true;
 for (int i = 0; i < P6_BLOB_STREAMS + 1; ++i)
  assert(!zfile_fopen("rom", "rb") && errno == ENOMEM);
 fail_wrapper_allocation = false; /* Every failed wrapper must release its blob handle. */
#endif
 /* Same open/retry flags and modes as DISK_validate_filename. */
 assert(!zfile_fopen("disk.adf", "r+b", ZFD_NORMAL | ZFD_DISKHISTORY) && errno == EROFS);
 zfile *a = zfile_fopen("disk.adf", "rb", ZFD_NORMAL | ZFD_DISKHISTORY);
 zfile *b = zfile_fopen("disk.adf", "rb"); assert(a && b && a != b);
 assert(a->f == NULL && a->data == NULL && a->zfileread == NULL && a->zfileseek == NULL);
 assert(zfile_size(a) == 7 && zfile_size32(a) == 7 && zfile_ftell(a) == 0 && a->size == 7);
 assert(!zfile_needwrite(a) && !zfile_iscompressed(a) && zfile_gettype(a) == ZFILE_DISKIMAGE);
 assert(strcmp(zfile_getname(a), "disk.adf") == 0 && strcmp(zfile_getoriginalname(a), "disk.adf") == 0);
 assert(strcmp(zfile_getfilename(a), "disk.adf") == 0);
 unsigned char out[9]; memset(out, 0xa5, sizeof out);
 assert(zfile_fread(out, 2, 4, a) == 3 && zfile_ftell32(a) == 6 && a->seek == 6);
 for (int i = 0; i < 6; ++i) assert(out[i] == i + 1);
 assert(out[6] == 0xa5 && out[8] == 0xa5 && zfile_ftell(b) == 0);
 assert(zfile_fread(out, 2, 1, a) == 0 && zfile_ftell(a) == 6 && zfile_ferror(a) == 0);
 assert(zfile_fread(out, 1, 1, a) == 1 && out[0] == 7);
 assert(zfile_fread(out, 1, 1, a) == 0 && zfile_ferror(a) == 0);
 assert(zfile_fread32(out, 1, SIZE_MAX, b) == 7); /* Clip before int32 result bound. */
 assert(zfile_fseek(b, 0, SEEK_SET) == 0);
 assert(zfile_fread(out, 2, SIZE_MAX, b) == 0 && errno == ERANGE && zfile_ftell(b) == 0);
 assert(zfile_ferror(b) == ERANGE);
 assert(zfile_fwrite(data, 1, sizeof data, a) == 0 && zfile_ferror(a) == EROFS && zfile_ftell(a) == 7);
 assert(zfile_fseek(a, INT64_MIN, SEEK_CUR) == 1 && zfile_ftell(a) == 0);
 assert(zfile_fseek(a, INT64_MAX, SEEK_END) == 1 && zfile_ftell(a) == 7);
 assert(zfile_fseek(a, -3, SEEK_END) == 0 && zfile_ftell(a) == 4);
 assert(zfile_fseek(a, 0, -1) == 1 && errno == EINVAL && zfile_ftell(a) == 4);
 assert(zfile_fread(NULL, 1, 1, a) == 0 && zfile_ferror(a) == EINVAL && zfile_ftell(a) == 4);
 zfile_fclose(a); zfile_fclose(b); zfile_fclose(NULL);
 assert(zfile_exists("disk.adf") == 1);
 assert(zfile_exists("../disk.adf") == 0 && errno == EINVAL);
 assert(!zfile_fopen("missing", "rb") && errno == ENOENT);
 assert(!zfile_fopen("/tmp/disk.adf", "rb") && errno == EINVAL);
 const int excluded[] = { ZFD_ARCHIVE, ZFD_ADF, ZFD_HD, ZFD_UNPACK, ZFD_RAWDISK,
                         ZFD_CD, ZFD_DELAYEDOPEN, ZFD_NORECURSE, ZFD_ALL,
                         ZFD_RAWDISK_AMIGA, ZFD_RAWDISK_PC, INT_MIN };
 for (int flags : excluded) assert(!zfile_fopen("rom", "rb", flags) && errno == ENOTSUP);
 assert(!zfile_fopen("rom", "rb", 0, 1) && errno == ENOTSUP);
 a = zfile_fopen("rom", "rb", ZFD_CHECKONLY); assert(a && zfile_gettype(a) == ZFILE_ROM);
 b = zfile_fopen("key", "rb"); assert(b && zfile_gettype(b) == ZFILE_KEY);
 zfile_exit(); zfile_exit(); assert(zfile_exists("rom") == 1); /* Assets survive closing streams. */
 for (int cycle = 0; cycle < 3; ++cycle) {
  for (int i = 0; i < P6_BLOB_STREAMS; ++i) assert(zfile_fopen("rom", "rb"));
  assert(!zfile_fopen("rom", "rb") && errno == EMFILE); zfile_exit();
 }
 a = zfile_fopen("rom", "rb"); assert(a); p6_blob_clear();
 assert(zfile_fread(out, 1, 1, a) == 0 && zfile_ferror(a) == EBADF);
 zfile_exit(); p6_blob_clear();
 assert(zfile_ftell(NULL) == -1 && zfile_ferror(NULL) == EBADF);
 return 0;
}
