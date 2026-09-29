#include "sysdeps.h"
#include "zfile.h"
#include "zarchive.h"
#include "blob_io.h"
#include <errno.h>
#include <limits.h>

#ifdef P6_ZFILE_ALLOC_TEST
static int fail_after = -1, live;
static bool failing() { return fail_after >= 0 && fail_after-- == 0; }
extern "C" void *p6_test_calloc(size_t n, size_t w)
{ void *p = failing() ? NULL : calloc(n, w); if (p) ++live; return p; }
extern "C" void *p6_test_malloc(size_t n)
{ void *p = failing() ? NULL : malloc(n); if (p) ++live; return p; }
extern "C" void p6_test_free(void *p)
{ if (p) { assert(live > 0); --live; } free(p); }
#define release p6_test_free
#else
#define release free
#endif

int main()
{
 const uae_u8 digits[] = "123456789";
 assert(!p6_blob_register("digits", P6_BLOB_ROM, digits, 9));
 assert(!p6_blob_register("empty", P6_BLOB_ROM, NULL, 0));
 for (int type = 0; type < 3; ++type) {
  zfile *root = zfile_fopen("digits", "rb"), *f = root; assert(root);
  if (type == 1) { f = zfile_fopen_data("ram", 9, digits); assert(f); }
  if (type == 2) { f = zfile_fopen_parent(root, NULL, 0, 9); assert(f); }
  assert(!zfile_fseek(f, 4, SEEK_SET));
  if (f != root) assert(!zfile_fseek(root, 7, SEEK_SET));
  assert(zfile_crc32(f) == 0xcbf43926 && zfile_ftell(f) == 4);
  if (f != root) assert(zfile_ftell(root) == 7);
  int length = -1;
  auto *data = zfile_getdata(f, 0, -1, &length);
  assert(data && length == 9 && !memcmp(data, digits, 9) && zfile_ftell(f) == 4);
  data[0] = 0; release(data);
  data = zfile_getdata(f, 2, 3, &length); assert(data && length == 3 && !memcmp(data, "345", 3)); release(data);
  assert(zfile_ftell(f) == 4);
  if (f != root) assert(zfile_ftell(root) == 7);
  data = zfile_getdata(f, 0, 1, NULL); assert(data && data[0] == '1'); release(data);
  data = zfile_getdata(f, 9, 0, &length); assert(data && length == 0); release(data);
  assert(!zfile_getdata(f, -1, 1, &length) && length == 0 && errno == ERANGE);
  assert(!zfile_getdata(f, 1, -1, &length) && length == 0 && errno == ERANGE);
  assert(!zfile_getdata(f, INT64_MAX, INT_MAX, &length) && length == 0 && errno == ERANGE);
  assert(!zfile_getdata(f, 8, 2, &length) && length == 0 && errno == ERANGE && zfile_ftell(f) == 4);
#ifdef P6_ZFILE_ALLOC_TEST
  int before = live;
  fail_after = 0; assert(!zfile_getdata(f, 0, 9, &length) && errno == ENOMEM && !length);
  assert(live == before && zfile_ftell(f) == 4);
  if (type != 1) { fail_after = 0; assert(zfile_crc32(f) == 0 && zfile_ferror(f) == ENOMEM); }
  fail_after = -1;
#endif
  if (f != root) zfile_fclose(f);
  zfile_fclose(root);
 }
 zfile *f = zfile_fopen("empty", "rb"); assert(f && !zfile_crc32(f) && !zfile_ferror(f)); zfile_fclose(f);
 zfile *root = zfile_fopen_empty(NULL, "root", 9); assert(root);
 f = zfile_fopen_parent(root, NULL, 2, 5); assert(f);
 assert(!zfile_fseek(f, 4, SEEK_SET) && zfile_truncate(root, 3));
 assert(!zfile_fseek(root, 1, SEEK_SET));
 int length = 99;
 assert(!zfile_getdata(f, 0, -1, &length) && !length && zfile_ferror(f));
 assert(f->seek == 4 && root->seek == 1);
 assert(!zfile_crc32(f) && f->seek == 4 && root->seek == 1);
 zfile_fclose(f); zfile_fclose(root);

 root = zfile_fopen_empty(NULL, "short", 5); assert(root);
 assert(zfile_fwrite("hello", 1, 5, root) == 5);
 f = zfile_fopen_parent(root, NULL, 0, 5); assert(f && zfile_truncate(root, 2));
 char partial[8]; memset(partial, 0x55, sizeof partial);
 assert(!zfile_fgetsa(partial, 8, f) && !strcmp(partial, "he") && partial[3] == 0x55);
 assert(f->seek == 2 && root->seek == 2 && zfile_ferror(f));
 assert(!zfile_fseek(f, 0, SEEK_SET)); memset(partial, 0x55, sizeof partial);
 assert(!zfile_fgets(partial, 8, f) && !strcmp(partial, "he") && partial[3] == 0x55);
 zfile_fclose(f); zfile_fclose(root);

 const uae_u8 text[] = {'A', '\r', '\n', 'B', 0, 'C', '\n', 0xff};
 assert(!p6_blob_register("text", P6_BLOB_ROM, text, sizeof text));
 for (int kind = 0; kind < 3; ++kind) {
  root = zfile_fopen("text", "rb"); assert(root);
  f = kind == 0 ? root : kind == 1 ? zfile_fopen_data("text", sizeof text, text)
                                 : zfile_fopen_parent(root, NULL, 0, sizeof text);
  assert(f);
  char out[12]; memset(out, 0x55, sizeof out);
  assert(zfile_fgetsa(out, 1, f) == out && !out[0] && f->seek == 0 && out[1] == 0x55);
  assert(!zfile_fgetsa(out, 0, f) && errno == EINVAL && !f->seek);
  assert(!zfile_fgets(NULL, 9, f) && errno == EINVAL && !f->seek);
  assert(zfile_fgetsa(out, 3, f) == out && !strcmp(out, "A\r") && f->seek == 2);
  assert(zfile_fgetsa(out, 12, f) == out && !strcmp(out, "\n"));
  assert(zfile_fgetsa(out, 12, f) == out && !memcmp(out, "B\0C\n\0", 5) && f->seek == 7);
  assert(zfile_getc(f) == 255 && zfile_getc(f) == -1);
  assert(!zfile_fgetsa(out, 12, f));
  assert(zfile_fgetsa(out, 1, f) == out && !out[0]);
  assert(!zfile_fseek(f, 0, SEEK_SET));
  assert(zfile_fgets(out, 12, f) == out + 4 && !strcmp(out, "A\r\n"));
  assert(zfile_fgets(out, 12, f) == out + 2 && !strcmp(out, "B") && f->seek == 5);
  assert(zfile_fgets(out, 12, f) == out + 3 && !strcmp(out, "C\n"));
  assert(zfile_fgets(out, 12, f) == out + 2 && (unsigned char)out[0] == 255 && !out[1]);
  assert(!zfile_fgets(out, 12, f));
  assert(zfile_fgets(out, 1, f) == out + 1 && !out[0]);
  assert(!zfile_fseek(f, 4, SEEK_SET)); out[0] = 'x';
  assert(!zfile_fgets(out, 12, f) && f->seek == 5 && out[0] == 'x');
  if (f != root) zfile_fclose(f);
  zfile_fclose(root);
 }
 f = zfile_fopen_empty(NULL, "dir/name\\item:config"); assert(f);
 assert(!strcmp(zfile_getfilename(f), "config"));
 assert(zfile_fputs(f, "hello\n") == 1 && zfile_putc(0x1ff, f) == 1);
 assert(!zfile_fputs(f, "") && !zfile_fputs(f, NULL) && errno == EINVAL);
 assert(!zfile_fseek(f, 0, SEEK_SET)); char out[8];
 assert(zfile_fgetsa(out, 8, f) == out && !strcmp(out, "hello\n") && zfile_getc(f) == 255);
 zfile_fclose(f); f = zfile_fopen("digits", "rb"); assert(f);
 assert(zfile_putc('x', f) == -1 && errno == EROFS && !zfile_fputs(f, "no"));
 p6_blob_clear(); length = 99;
 assert(!zfile_getdata(f, 0, -1, &length) && !length && zfile_ferror(f) == EBADF);
 assert(!zfile_fgets(out, 8, f) && zfile_ferror(f) == EBADF);
 zfile_exit(); zfile_exit();
#ifdef P6_ZFILE_ALLOC_TEST
 assert(live == 0);
#endif
 return 0;
}
