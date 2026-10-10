/* Shifts the host clock of a process by P6_FAKE_TIME_OFFSET seconds (T25, DET06: a different date must not change what the machine does).
 *
 *   Linux:  cc -shared -fPIC -o faketime.so faketime.c -ldl      LD_PRELOAD=./faketime.so
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
    struct timespec ts;
    gettimeofday(&tv, NULL);
    clock_gettime(CLOCK_REALTIME, &ts);
    printf("time=%ld gettimeofday=%ld clock_gettime=%ld\n", (long)time(NULL), (long)tv.tv_sec, (long)ts.tv_sec);
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
#include <dlfcn.h>
time_t time(time_t *out)
{
    static time_t (*real)(time_t *);
    if (!real) real = (time_t (*)(time_t *))dlsym(RTLD_NEXT, "time");
    time_t t = real(NULL) + offset();
    if (out) *out = t;
    return t;
}
int gettimeofday(struct timeval *tv, void *tz)
{
    static int (*real)(struct timeval *, void *);
    if (!real) real = (int (*)(struct timeval *, void *))dlsym(RTLD_NEXT, "gettimeofday");
    int result = real(tv, tz);
    if (!result && tv) tv->tv_sec += offset();
    return result;
}
int clock_gettime(clockid_t id, struct timespec *ts)
{
    static int (*real)(clockid_t, struct timespec *);
    if (!real) real = (int (*)(clockid_t, struct timespec *))dlsym(RTLD_NEXT, "clock_gettime");
    int result = real(id, ts);
    if (!result && ts && id == CLOCK_REALTIME) ts->tv_sec += offset();
    return result;
}
#endif
