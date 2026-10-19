/* Shifts the host clock of a process by P6_FAKE_TIME_OFFSET seconds (T25, DET06: a different date must not change what the machine does).
 *
 *   Linux:  cc -shared -fPIC -o faketime.so faketime.c          LD_PRELOAD=./faketime.so
 *   macOS:  cc -dynamiclib -o faketime.dylib faketime.c          DYLD_INSERT_LIBRARIES=./faketime.dylib
 *
 * It wraps time(), gettimeofday() and clock_gettime() for the realtime clock only; the monotonic clock is left alone because a process
 * that sees it jump has nothing to do with the date. A second build of this file with -DFAKETIME_CHECK is a program that prints what those
 * three functions return, to show that the shift reaches a process before anything is concluded from a run that used it.
 * Not part of the product: a test tool, written here because libfaketime is not available on the machines of the matrix. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

static long offset(void)
{
    const char *text = getenv("P6_FAKE_TIME_OFFSET");
    return text ? strtol(text, NULL, 10) : 0;
}

#ifdef FAKETIME_CHECK
int main(void)
{
    struct timeval tv;
    struct timespec ts, mono;
    gettimeofday(&tv, NULL);
    clock_gettime(CLOCK_REALTIME, &ts);
    clock_gettime(CLOCK_MONOTONIC, &mono);
    printf("time=%ld gettimeofday=%ld clock_gettime=%ld monotonic=%ld\n", (long)time(NULL), (long)tv.tv_sec, (long)ts.tv_sec, (long)mono.tv_sec);
    return 0;
}
#elif defined(__APPLE__)
/* dyld interposing: the replacement functions call the real ones, which the interposed binding does not redirect inside this image. */
static time_t fake_time(time_t *out)
{
    time_t t = time(NULL) + offset();
    if (out) *out = t;
    return t;
}
static int fake_gettimeofday(struct timeval *tv, void *tz)
{
    int result = gettimeofday(tv, tz);
    if (!result && tv) tv->tv_sec += offset();
    return result;
}
static int fake_clock_gettime(clockid_t id, struct timespec *ts)
{
    int result = clock_gettime(id, ts);
    if (!result && ts && id == CLOCK_REALTIME) ts->tv_sec += offset();
    return result;
}
#define INTERPOSE(replacement, original) \
    __attribute__((used)) static struct { const void *replacement_; const void *original_; } interpose_##original \
    __attribute__((section("__DATA,__interpose"))) = {(const void *)(replacement), (const void *)(original)}
INTERPOSE(fake_time, time);
INTERPOSE(fake_gettimeofday, gettimeofday);
INTERPOSE(fake_clock_gettime, clock_gettime);
#else
#include <sys/syscall.h>
#include <unistd.h>
/* ASan calls these during initialization. dlsym(RTLD_NEXT) can reenter the
 * loader's error handling through ASan and double-free its error buffer.
 * Linux syscalls do not allocate or resolve symbols; the Mac interposer above
 * continues to use dyld's original bindings. */
time_t time(time_t *out)
{
    struct timespec ts;
    if (syscall(SYS_clock_gettime, CLOCK_REALTIME, &ts)) return (time_t)-1;
    time_t t = ts.tv_sec + offset();
    if (out) *out = t;
    return t;
}
int gettimeofday(struct timeval *tv, void *tz)
{
    int result = syscall(SYS_gettimeofday, tv, tz);
    if (!result && tv) tv->tv_sec += offset();
    return result;
}
int clock_gettime(clockid_t id, struct timespec *ts)
{
    int result = syscall(SYS_clock_gettime, id, ts);
    if (!result && ts && id == CLOCK_REALTIME) ts->tv_sec += offset();
    return result;
}
#endif
