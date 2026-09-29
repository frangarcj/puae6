#include "civil_time.h"

static int valid(const p6_clock_state *s)
{
 return s && s->version == 1 && s->seed && s->ticks_per_second && s->subsecond < s->ticks_per_second
     && s->seconds >= P6_CIVIL_MIN_SECONDS && s->seconds <= P6_CIVIL_MAX_SECONDS;
}
int p6_clock_init(p6_clock_state *s, int64_t epoch, uint64_t rate, uint32_t seed)
{
 if (!s || !rate) return P6_CLOCK_ARGUMENT;
 if (epoch < P6_CIVIL_MIN_SECONDS || epoch > P6_CIVIL_MAX_SECONDS) return P6_CLOCK_RANGE;
 p6_clock_state next = { epoch, rate, 0, seed ? seed : 1, 1 };
 *s = next; return P6_CLOCK_OK;
}
int p6_clock_restore(p6_clock_state *s, const p6_clock_state *saved)
{
 if (!s || !saved) return P6_CLOCK_ARGUMENT;
 if (!valid(saved)) return P6_CLOCK_STATE;
 *s = *saved; return P6_CLOCK_OK;
}
int p6_clock_advance(p6_clock_state *s, uint64_t ticks)
{
 if (!s) return P6_CLOCK_ARGUMENT;
 if (!valid(s)) return P6_CLOCK_STATE;
 uint64_t whole = ticks / s->ticks_per_second, part = ticks % s->ticks_per_second;
 uint64_t gap = s->ticks_per_second - s->subsecond;
 uint64_t carry = part >= gap;
 part = carry ? part - gap : s->subsecond + part;
 uint64_t remaining = (uint64_t)(P6_CIVIL_MAX_SECONDS - s->seconds);
 if (carry > remaining || whole > remaining - carry) return P6_CLOCK_RANGE;
 s->seconds += (int64_t)(whole + carry); s->subsecond = part;
 return P6_CLOCK_OK;
}
static int leap(int year) { return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0); }
static int days_before(int year)
{
 --year;
 return year * 365 + year / 4 - year / 100 + year / 400;
}
int p6_clock_read(const p6_clock_state *s, int32_t adjustment, p6_civil_time *out)
{
 if (!s || !out) return P6_CLOCK_ARGUMENT;
 if (!valid(s)) return P6_CLOCK_STATE;
 int64_t seconds = s->seconds + adjustment; // Valid state bounds make this safe.
 if (seconds < P6_CIVIL_MIN_SECONDS || seconds > P6_CIVIL_MAX_SECONDS) return P6_CLOCK_RANGE;
 int64_t days = seconds / 86400, daytime = seconds % 86400;
 if (daytime < 0) { --days; daytime += 86400; }
 int ordinal = (int)(days + 719162); // Jan 1 year 1 is ordinal zero.
 int low = 1, high = 10000;
 while (low + 1 < high) {
  int middle = low + (high - low) / 2;
  if (days_before(middle) <= ordinal) low = middle; else high = middle;
 }
 p6_civil_time result = {0,0,0,0,0,0,0,0};
 result.year = low; result.yearday = ordinal - days_before(low);
 int day = result.yearday, month = 1;
 static const int lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
 while (month < 12) {
  int length = lengths[month - 1] + (month == 2 && leap(low));
  if (day < length) break;
  day -= length; ++month;
 }
 result.month = month; result.day = day + 1;
 result.weekday = (int)((days + 4) % 7);
 if (result.weekday < 0) result.weekday += 7;
 result.hour = (int32_t)(daytime / 3600); result.minute = (int32_t)(daytime / 60 % 60);
 result.second = (int32_t)(daytime % 60);
 *out = result; return P6_CLOCK_OK;
}
int p6_clock_seed(const p6_clock_state *s, uint32_t *out)
{
 if (!s || !out) return P6_CLOCK_ARGUMENT;
 if (!valid(s)) return P6_CLOCK_STATE;
 *out = s->seed; return P6_CLOCK_OK;
}
