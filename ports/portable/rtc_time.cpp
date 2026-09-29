#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "rtc_time.h"

static p6_rtc_state state;
static int fault;

static int fail(int error) { if (!fault) fault = error; return fault; }
int p6_rtc_status(void) { return fault ? fault : state.clock.version == 1 ? 0 : P6_CLOCK_STATE; }
void p6_rtc_unload(void) { state = {}; fault = 0; }
int p6_rtc_init(int64_t epoch, uint64_t rate, uint32_t seed)
{
 p6_rtc_state next{};
 if (get_cycles() < 0) return P6_CLOCK_STATE;
 int result = p6_clock_init(&next.clock, epoch, rate, seed);
 if (result) return result;
 next.cycle = get_cycles(); state = next; fault = 0;
 return P6_CLOCK_OK;
}
static int sync_clock(void)
{
 if (p6_rtc_status()) return fail(p6_rtc_status());
 int64_t now = get_cycles();
 if (now < state.cycle) return fail(P6_CLOCK_STATE);
 int result = p6_clock_advance(&state.clock, uint64_t(now - state.cycle));
 if (result) return fail(result);
 state.cycle = now;
 return P6_CLOCK_OK;
}
int p6_rtc_read(struct tm *out, int32_t adjustment)
{
 if (!out) return fail(P6_CLOCK_ARGUMENT);
 int result = sync_clock();
 if (result) return result;
 p6_civil_time civil;
 result = p6_clock_read(&state.clock, adjustment, &civil);
 if (result) return fail(result);
 struct tm next{};
 next.tm_year = civil.year - 1900; next.tm_mon = civil.month - 1;
 next.tm_mday = civil.day; next.tm_hour = civil.hour;
 next.tm_min = civil.minute; next.tm_sec = civil.second;
 next.tm_wday = civil.weekday; next.tm_yday = civil.yearday;
 *out = next;
 return P6_CLOCK_OK;
}
int p6_rtc_save(p6_rtc_state *out)
{
 if (!out) return P6_CLOCK_ARGUMENT;
 int result = sync_clock();
 if (result) return result;
 *out = state;
 return P6_CLOCK_OK;
}
int p6_rtc_restore(const p6_rtc_state *saved)
{
 if (!saved) return P6_CLOCK_ARGUMENT;
 if (saved->cycle < 0 || saved->cycle != get_cycles() || state.clock.version != 1
     || saved->clock.ticks_per_second != state.clock.ticks_per_second) return P6_CLOCK_STATE;
 p6_rtc_state next{};
 int result = p6_clock_restore(&next.clock, &saved->clock);
 if (result) return result;
 next.cycle = saved->cycle; state = next; fault = 0;
 return P6_CLOCK_OK;
}
int p6_rtc_rebase(int64_t next)
{
 if (next < 0) return P6_CLOCK_ARGUMENT;
 int result = sync_clock();
 if (result) return result;
 state.cycle = next;
 return P6_CLOCK_OK;
}
