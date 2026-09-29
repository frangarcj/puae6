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
#endif

int main()
{
 const uae_u8 bytes[] = {0, 1, 2, 3, 4, 5, 6, 7};
 assert(!p6_blob_register("disk", P6_BLOB_ADF, bytes, 8));
 const bool choices[] = {false, true};
 for (bool ram : choices) {
  for (bool close_first : choices) {
   zfile *root = ram ? zfile_fopen_data("disk", 8, bytes) : zfile_fopen("disk", "rb"); assert(root);
   zfile *a = zfile_fopen_parent(root, "slice", 2, 5); assert(a);
   zfile *b = zfile_fopen_parent(a, NULL, 1, 3); assert(b);
   assert(a->parent == root && b->parent == root && b->offset == 3 && root->opencnt == 3);
   assert(!strcmp(zfile_getoriginalname(b), "disk") && !strcmp(zfile_getname(b), "slice"));
   assert(!b->data && !b->f && !b->mode && b->size == 3 && b->useparent);
   if (close_first) zfile_fclose(root);
   zfile_fclose(a); // Nested view holds the root, not the intermediate view.
   assert(root->opencnt == (close_first ? 1 : 2));
   uae_u8 out[5] = {0xaa, 0xaa, 0xaa, 0xaa, 0xaa};
   assert(zfile_fread(out, 2, 2, b) == 1 && out[0] == 3 && out[1] == 4 && out[2] == 0xaa);
   assert(zfile_ftell(b) == 2 && zfile_ftell(root) == 5);
   assert(!zfile_fread(out, 2, 1, b) && !zfile_ferror(b));
   zfile *c = zfile_dup(b); assert(c && c->parent == root && c->offset == 3 && c->seek == 0);
   assert(zfile_fread(out, 1, 3, c) == 3 && out[2] == 5 && b->seek == 2);
   assert(!zfile_fwrite(bytes, 1, 1, b) && errno == EROFS);
   assert(!zfile_truncate(b, 0) && errno == EROFS && b->size == 3);
   assert(zfile_fseek(b, INT64_MIN, SEEK_END) && b->seek == 0);
   assert(zfile_fseek(b, INT64_MAX, SEEK_CUR) && b->seek == 3);
   assert(!zfile_fseek(b, 0, SEEK_SET));
   assert(!zfile_fread(out, 2, SIZE_MAX, b) && errno == ERANGE && b->seek == 0);
   assert(!zfile_fread(NULL, 1, 1, b) && errno == EINVAL && b->seek == 0);
   zfile_fclose(b); zfile_fclose(c);
   if (!close_first) { assert(root->opencnt == 1); zfile_fclose(root); }
  }
 }
 zfile *root = zfile_fopen_empty(NULL, "ram", 8); assert(root);
 assert(zfile_fwrite(bytes, 1, 8, root) == 8);
 zfile *view = zfile_fopen_parent(root, NULL, 2, 4); assert(view);
 // Growth swaps the RAM allocation; a view must follow the live parent.
 assert(zfile_fwrite(bytes, 1, 8, root) == 8);
 uae_u8 out[10]; memset(out, 0xaa, sizeof out);
 assert(zfile_fread(out, 1, 4, view) == 4 && !memcmp(out, bytes + 2, 4));
 assert(!zfile_fseek(root, 3, SEEK_SET));
 zfile *copy = zfile_dup(root); assert(copy && copy->seek == 3 && copy->size == 16 && copy->allocsize == 0);
 assert(copy->data != root->data && !memcmp(copy->data, root->data, 16));
 root->data[0] = 99; assert(copy->data[0] == 0);
 assert(!zfile_fwrite(bytes, 1, 1, copy) && errno == EROFS); zfile_fclose(copy);
 assert(zfile_truncate(root, 3));
 assert(!zfile_fseek(view, 0, SEEK_SET));
 assert(zfile_fread(out, 1, 4, view) == 1 && view->seek == 1 && zfile_ferror(view) == ERANGE);
 assert(!zfile_fopen_parent(view, NULL, 0, 4) && errno == ERANGE);
 assert(zfile_truncate(root, 0));
 assert(!zfile_fseek(view, 0, SEEK_SET));
 assert(!zfile_fread(out, 1, 1, view) && view->seek == 0);
 zfile_fclose(root); zfile_fclose(view);

 root = zfile_fopen("disk", "rb"); assert(root);
 assert(!zfile_fseek(root, 3, SEEK_SET));
 copy = zfile_dup(root); assert(copy && copy->seek == 3 && copy->size == 8);
 assert(zfile_fread(out, 1, 1, copy) == 1 && out[0] == 3 && root->seek == 3);
 zfile_fclose(root); assert(zfile_fread(out, 1, 1, copy) == 1 && out[0] == 4);
 root = copy;
 zfile *named = zfile_fopen_load_zfile(root); assert(named);
 memset(zfile_getoriginalname(named), 'x', P6_BLOB_ID_BYTES + 1); // Mutable legacy label, no terminator.
 assert(!zfile_fopen_parent(named, "view", 0, 1) && errno == ENAMETOOLONG);
 assert(!zfile_fopen_empty(named, "tmp", 1) && errno == ENAMETOOLONG);
 strcpy(zfile_getoriginalname(named), "\xc0\x80");
 assert(!zfile_fopen_parent(named, "view", 0, 1) && errno == EINVAL);
 strcpy(zfile_getoriginalname(named), "disk"); zfile_fclose(named);
 assert(!zfile_fopen_parent(root, NULL, UINT64_MAX, 1) && errno == ERANGE);
 assert(!zfile_fopen_parent(root, NULL, 1, UINT64_MAX) && errno == ERANGE);
 assert(!zfile_fopen_parent(root, NULL, 8, 1) && errno == ERANGE);
 view = zfile_fopen_parent(root, NULL, 8, 0); assert(view && !zfile_fread(NULL, 1, 1, view));
 zfile_fclose(view);
 root->opencnt = INT_MAX;
 assert(!zfile_fopen_parent(root, NULL, 0, 1) && errno == EMFILE);
 root->opencnt = 1;
#ifdef P6_ZFILE_ALLOC_TEST
 int before = live;
 fail_after = 0; assert(!zfile_fopen_parent(root, NULL, 0, 1) && errno == ENOMEM);
 assert(root->opencnt == 1 && live == before);
 for (int i = 0; i < P6_BLOB_STREAMS + 1; ++i) {
  fail_after = 0; assert(!zfile_dup(root) && errno == ENOMEM && live == before);
 }
 fail_after = -1;
 zfile *ram = zfile_fopen_data("ram", 8, bytes); assert(ram);
 before = live;
 for (int i = 0; i < 2; ++i) {
  fail_after = i; assert(!zfile_dup(ram) && errno == ENOMEM && live == before && ram->seek == 0);
 }
 fail_after = -1; zfile_fclose(ram);
#endif
 view = zfile_fopen_parent(root, NULL, 0, 8); assert(view);
 p6_blob_clear(); assert(!zfile_fread(out, 1, 1, view) && zfile_ferror(view) == EBADF);
 assert(zfile_size(view) == -1 && zfile_ferror(view) == EBADF);
 zfile_exit(); zfile_exit();
 assert(!zfile_dup(NULL) && errno == EBADF);
 assert(!zfile_fopen_parent(NULL, NULL, 0, 0) && errno == EBADF);
#ifdef P6_ZFILE_ALLOC_TEST
 assert(live == 0);
#endif
 return 0;
}
