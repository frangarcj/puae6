// DYLD interposer that audits virtual memory calls of a .NET process that loads libwaterboxhost.dylib (T28a).
//
// Every mmap/munmap goes through here, from any image (libwaterboxhost, CoreCLR, libmalloc if dyld interposes the shared cache): the live mappings are tracked with the image
// that created them. A munmap is reported ("CROSS") when it lands on a mapping that another image created and ("UNKNOWN") when libwaterboxhost unmaps something that was not tracked. Reports have a backtrace. An mprotect from libwaterboxhost outside its own mappings is reported too.
// MMAP_AUDIT_LOG is the log file (default stderr); MMAP_AUDIT_TRACE=N dumps the backtrace of the N-th mmap of libwaterboxhost (0: the first 40).
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MAXMAPS 262144
static struct { uintptr_t a, s; const char *owner; } maps[MAXMAPS];
static int nmaps;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int logfd = 2;
static long counts[6];   // mmap wbx, munmap wbx, mprotect wbx, mmap other, munmap other, reports
static long trace_at = -1;

__attribute__((constructor)) static void init(void) {
  const char *t = getenv("MMAP_AUDIT_TRACE");
  if (t) trace_at = atol(t);
  const char *p = getenv("MMAP_AUDIT_LOG");
  if (p) { int fd = open(p, O_WRONLY | O_CREAT | O_APPEND, 0644); if (fd >= 0) logfd = fd; }
}

static const char *image_of(void *ra) {      // a pointer into the image's own path string: stable, nothing is allocated
  Dl_info i;
  if (dladdr(ra, &i) && i.dli_fname) { const char *s = strrchr(i.dli_fname, '/'); return s ? s + 1 : i.dli_fname; }
  return "?";
}

static void report(const char *what, void *a, size_t l, const char *who, const char *owner) {
  char buf[300];
  ++counts[5];
  int n = snprintf(buf, sizeof buf, "%s addr=%p len=%zu by=%s owner=%s pid=%d\n", what, a, l, who, owner ? owner : "-", (int)getpid());
  write(logfd, buf, n);
  void *bt[24];
  int c = backtrace(bt, 24);
  backtrace_symbols_fd(bt, c, logfd);
}

// removes [a, a+l) from the tracked mappings; returns the owner of the first mapping it touched (NULL when none), and whether it was entirely inside one mapping
static const char *cut(uintptr_t a, size_t l, int *inside, int *split) {
  const char *owner = NULL;
  *inside = 0; *split = 0;
  uintptr_t e = a + l;
  for (int i = 0; i < nmaps; ++i) {
    uintptr_t ma = maps[i].a, me = ma + maps[i].s;
    if (me <= a || ma >= e) continue;
    if (!owner) owner = maps[i].owner;
    if (ma <= a && me >= e) *inside = 1;
    if (ma < a && me > e) {            // a hole in the middle: two pieces
      *split = 1;
      if (nmaps < MAXMAPS) { maps[nmaps].a = e; maps[nmaps].s = me - e; maps[nmaps].owner = maps[i].owner; ++nmaps; }
      maps[i].s = a - ma;
    } else if (ma < a) maps[i].s = a - ma;
    else if (me > e) { maps[i].a = e; maps[i].s = me - e; }
    else { maps[i] = maps[--nmaps]; --i; }
  }
  return owner;
}

static void *my_mmap(void *a, size_t l, int p, int f, int fd, off_t o) {
  void *ra = __builtin_return_address(0);
  void *r = mmap(a, l, p, f, fd, o);
  if (r == MAP_FAILED) return r;
  const char *who = image_of(ra);
  int wbx = strstr(who, "libwaterboxhost") != NULL;
  pthread_mutex_lock(&lock);
  int inside, split;
  cut((uintptr_t)r, l, &inside, &split);     // a fixed mapping replaces what was there
  if (nmaps < MAXMAPS) { maps[nmaps].a = (uintptr_t)r; maps[nmaps].s = l; maps[nmaps].owner = who; ++nmaps; }
  ++counts[wbx ? 0 : 3];
  long mine = counts[0];
  pthread_mutex_unlock(&lock);
  if (wbx && (mine == trace_at || (trace_at == 0 && mine <= 40))) {
    char buf[200];
    int n = snprintf(buf, sizeof buf, "MMAP #%ld addr=%p len=%zu prot=%d flags=0x%x fd=%d -> %p\n", mine, a, l, p, f, fd, r);
    write(logfd, buf, n);
    void *bt[12]; int c = backtrace(bt, 12); backtrace_symbols_fd(bt, c, logfd);
  }
  return r;
}

static int my_munmap(void *a, size_t l) {
  void *ra = __builtin_return_address(0);
  {
    const char *who = image_of(ra);
    int wbx = strstr(who, "libwaterboxhost") != NULL;
    pthread_mutex_lock(&lock);
    ++counts[wbx ? 1 : 4];
    int inside, split;
    const char *owner = cut((uintptr_t)a, l, &inside, &split);
    pthread_mutex_unlock(&lock);
    if (!owner) { if (wbx) report("UNKNOWN munmap", a, l, who, NULL); }
    else if (strcmp(owner, who) != 0) report("CROSS munmap", a, l, who, owner);
  }
  return munmap(a, l);
}

static int my_mprotect(void *a, size_t l, int p) {
  void *ra = __builtin_return_address(0);
  {
    const char *who = image_of(ra);
    if (strstr(who, "libwaterboxhost")) {
      pthread_mutex_lock(&lock);
      ++counts[2];
      int hit = 0;
      for (int i = 0; i < nmaps && !hit; ++i) if ((uintptr_t)a >= maps[i].a && (uintptr_t)a + l <= maps[i].a + maps[i].s) hit = strcmp(maps[i].owner, who) == 0 ? 1 : 2;
      pthread_mutex_unlock(&lock);
      if (hit != 1) report(hit ? "CROSS mprotect" : "UNKNOWN mprotect", a, l, who, NULL);
    }
  }
  return mprotect(a, l, p);
}

__attribute__((destructor)) static void fini(void) {
  char buf[300];
  int n = snprintf(buf, sizeof buf, "AUDIT pid=%d wbx(mmap=%ld munmap=%ld mprotect=%ld) other(mmap=%ld munmap=%ld) reports=%ld live=%d\n", (int)getpid(), counts[0], counts[1], counts[2],
                   counts[3], counts[4], counts[5], nmaps);
  write(logfd, buf, n);
}

#define INTERPOSE(rep, orig) __attribute__((used)) static struct { const void *r, *o; } _ip_##orig __attribute__((section("__DATA,__interpose"))) = { (const void *)(uintptr_t)&rep, (const void *)(uintptr_t)&orig }
INTERPOSE(my_mmap, mmap);
INTERPOSE(my_munmap, munmap);
INTERPOSE(my_mprotect, mprotect);
