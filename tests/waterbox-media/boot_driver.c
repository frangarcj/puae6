// T28a: libwaterboxhost without .NET, without the core and without BizHawk: dlopen the runtime, create a host from a guest file, destroy it, N times in one process.
//
//   clang -O1 -o /tmp/boot_driver tests/waterbox-media/boot_driver.c
//   /tmp/boot_driver build/ffi/bin/Debug/net8.0/libwaterboxhost.dylib build/waterbox/puae6/release/puae6.wbx 1
//
// One process is one trial: it ends with "ok" (exit 0), with a signal, or never (a hang: the SIGSEGV handler of the runtime waits for a lock its own thread holds). Run it a few hundred
// times (the report reports/tasks/T28a.md has the counts) and count the processes that do not exit 0; a process alive after 40 s is a hang (`sample PID` shows where).
// The layout is the one of Puae6Core.Heaps() without extra memory; every second host gets 8 MiB more of mmap heap (the heaps are not what matters).
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { size_t sbrk, sealed, invis, plain, mmap; } Layout;      // MemoryLayoutTemplate of cinterface.rs
typedef struct { uint8_t err[1024]; void *data; } Ret;                    // Return<T> of cinterface.rs with a pointer-sized T
typedef intptr_t (*ReadCb)(uintptr_t userdata, uint8_t *data, size_t size);
typedef void (*Create)(Layout *, const char *, ReadCb, uintptr_t, Ret *);
typedef void (*Destroy)(void *, Ret *);

static intptr_t reader(uintptr_t userdata, uint8_t *data, size_t size) { return (intptr_t)fread(data, 1, size, (FILE *)userdata); }

int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "usage: %s RUNTIME.dylib GUEST.wbx [HOSTS]\n", argv[0]); return 2; }
  void *lib = dlopen(argv[1], RTLD_NOW);
  if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 2; }
  Create create = (Create)dlsym(lib, "wbx_create_host");
  Destroy destroy = (Destroy)dlsym(lib, "wbx_destroy_host");
  int n = argc > 3 ? atoi(argv[3]) : 1;
  for (int i = 0; i < n; ++i) {
    FILE *f = fopen(argv[2], "rb");
    if (!f) { perror("guest"); return 2; }
    Layout l = { 4096 * 1024, 16384 * 1024, 16 * 1024, 16 * 1024, (size_t)(16384 + (i % 2) * 8192) * 1024 };
    Ret r; memset(&r, 0, sizeof r);
    create(&l, "puae6.wbx", reader, (uintptr_t)f, &r);
    fclose(f);
    if (r.err[0]) { fprintf(stderr, "create failed: %s\n", r.err); return 3; }
    Ret d; memset(&d, 0, sizeof d);
    destroy(r.data, &d);
    if (d.err[0]) { fprintf(stderr, "destroy failed: %s\n", d.err); return 4; }
  }
  printf("ok\n");
  return 0;
}
