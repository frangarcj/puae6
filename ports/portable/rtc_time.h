#ifndef P6_RTC_TIME_H
#define P6_RTC_TIME_H
#include "civil_time.h"
#include <time.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Internal single-emulation-thread service, one instance per image/sandbox.
 * Ordinary guest storage; native serialization must encode fields, not padding.
 * Load supplies epoch, seed and fixed scheduler ticks/second. For A500 this is
 * CHIPSET_CLOCK_PAL/NTSC * CYCLE_UNIT, independent of CPU speed/video frame rate.
 * RTC/Amiga resets do NOT initialize this service again. */
typedef struct p6_rtc_state {
 p6_clock_state clock;
 int64_t cycle;
} p6_rtc_state;
int p6_rtc_init(int64_t epoch, uint64_t rate, uint32_t seed);
void p6_rtc_unload(void);
int p6_rtc_read(struct tm *out, int32_t adjustment);
int p6_rtc_save(p6_rtc_state *out);
/* Caller validates session/config and restores scheduler to saved.cycle first.
 * Restore validates rate and fields before committing and clears latched faults.
 * Whole-machine restore must be transactional at the bridge boundary. */
int p6_rtc_restore(const p6_rtc_state *saved);
/* Only for an explicit counter rebase: accounts for old get_cycles(), then
 * caller must set_cycles(next) immediately, with no emulation in between.
 * Ordinary resets preserve the upstream counter. Never infer wrap/backtracking. */
int p6_rtc_rebase(int64_t next);
/* Reads cannot propagate errors through upstream byte-returning handlers.
 * They return 0xff on failure (also for control reads that request calendar
 * upstream; this sentinel is not a valid emulated frame). Bridge MUST check
 * this sticky status after engine execution and reject the frame.
 * No default date, abort or host clock.
 * Clear only at successful init/restore or unload. Not a public p6_* ABI. */
int p6_rtc_status(void);
#ifdef __cplusplus
}
#endif
#endif
