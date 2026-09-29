#ifndef P6_CIVIL_TIME_H
#define P6_CIVIL_TIME_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define P6_CIVIL_MIN_SECONDS INT64_C(-62135596800)
#define P6_CIVIL_MAX_SECONDS INT64_C(253402300799)
#define P6_CIVIL_DEFAULT_EPOCH INT64_C(946684800)
enum { P6_CLOCK_OK = 0, P6_CLOCK_ARGUMENT = -1, P6_CLOCK_STATE = -2, P6_CLOCK_RANGE = -3 };
/* Internal guest state, not a serialized wire format. Gregorian years 1..9999,
 * Unix seconds, no leap seconds/DST. Copy fields for native state; Waterbox must
 * keep this in ordinary guest memory. ticks_per_second is fixed for a session;
 * its actual scheduler frequency is supplied by the bridge, never inferred from
 * frame count or wall time. subsecond is in [0,ticks_per_second).
 * seed is initial configuration, NOT the live upstream xorshift state. */
typedef struct p6_clock_state {
 int64_t seconds;
 uint64_t ticks_per_second, subsecond;
 uint32_t seed, version;
} p6_clock_state;
typedef struct p6_civil_time {
 int32_t year, month, day, hour, minute, second, weekday, yearday;
} p6_civil_time; /* month/day are 1-based, weekday Sunday=0, yearday Jan1=0. */
/* Errors leave destinations unchanged. Caller owns valid, disjoint ranges;
 * restore supports source==destination. init is for load, not soft reset. */
int p6_clock_init(p6_clock_state *state, int64_t epoch, uint64_t ticks_per_second, uint32_t seed);
int p6_clock_restore(p6_clock_state *state, const p6_clock_state *saved);
int p6_clock_advance(p6_clock_state *state, uint64_t elapsed_ticks);
/* Explicit fixed adjustment, including cs_rtc_adjust when integrated. No TZ,
 * locale, clock or callbacks. Convert year-1900/month-1 when supplying struct tm
 * to upstream; tm_isdst=0. CIA TOD remains driven by its original scheduler. */
int p6_clock_read(const p6_clock_state *state, int32_t adjustment_seconds, p6_civil_time *out);
/* Zero configuration maps to 1 like uaesetrandseed. Pass this nonzero value to
 * currprefs.seed before uaerandomizeseed to avoid its host-time fallback. */
int p6_clock_seed(const p6_clock_state *state, uint32_t *out);
#ifdef __cplusplus
}
#endif
#endif
