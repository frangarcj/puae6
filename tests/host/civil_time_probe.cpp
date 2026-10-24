#include "sysdeps.h"
#include "civil_time.h"
#include <inttypes.h>
#include <locale.h>
#include <time.h>

static struct { uae_u32 seed; } currprefs;
[[maybe_unused]] static uae_u32 getlocaltime() { abort(); } // Any upstream host fallback fails this probe.
uae_u32 uaesetrandseed(uae_u32);
#include "rng_original.inc"

static bool same(const p6_clock_state &a, const p6_clock_state &b)
{
 return a.seconds == b.seconds && a.ticks_per_second == b.ticks_per_second
     && a.subsecond == b.subsecond && a.seed == b.seed && a.version == b.version;
}
int main(int argc, char **)
{
 assert(setlocale(LC_ALL, "")); tzset(); // Change real libc settings in the test process only.
 p6_clock_state s{}, other{}; p6_civil_time civil{};
 if (argc > 1) {
  int64_t epoch; int32_t adjustment;
  while (scanf("%" SCNd64 " %" SCNd32, &epoch, &adjustment) == 2) {
   assert(!p6_clock_init(&s, epoch, 1, 1) && !p6_clock_read(&s, adjustment, &civil));
   printf("%d %d %d %d %d %d %d %d\n", civil.year, civil.month, civil.day,
          civil.hour, civil.minute, civil.second, civil.weekday, civil.yearday);
  }
  return 0;
 }
 uint32_t seed = 99;
 assert(p6_clock_seed(&s, &seed) == P6_CLOCK_STATE && seed == 99);
 assert(p6_clock_advance(&s, 0) == P6_CLOCK_STATE);
 assert(!p6_clock_init(&s, P6_CIVIL_DEFAULT_EPOCH, 10, 0));
 assert(!p6_clock_read(&s, 0, &civil));
 assert(civil.year == 2000 && civil.month == 1 && civil.day == 1 && civil.weekday == 6 && civil.yearday == 0);
 assert(!p6_clock_seed(&s, &seed) && seed == 1);
 currprefs.seed = seed; uaerandomizeseed();
 const uint32_t expected[] = {270369, 67634689, 2647435461U, 307599695, 2398689233U};
 for (uint32_t value : expected) assert(uaerand() == value);
 assert(!p6_clock_advance(&s, 9));
 p6_clock_state saved = s;
 uae_u32 saved_seed = randseed, saved_rng = xorshiftstate;
 assert(uaerandgetseed() == 1 && xorshiftstate != 1); // Initial seed is not live RNG state.
 assert(!p6_clock_advance(&s, 32)); uae_u32 continuation = uaerand();
 assert(!p6_clock_restore(&other, &saved) && !p6_clock_advance(&other, 32) && same(s, other));
 randseed = saved_seed; xorshiftstate = saved_rng; assert(uaerand() == continuation);
 assert(!p6_clock_init(&other, P6_CIVIL_DEFAULT_EPOCH, 10, 0));
 assert(!p6_clock_advance(&other, 41) && same(s, other));
 saved = s;
 assert(p6_clock_init(&s, INT64_MAX, 1, 1) == P6_CLOCK_RANGE && same(s, saved));
 assert(p6_clock_init(&s, INT64_MIN, 1, 1) == P6_CLOCK_RANGE && same(s, saved));
 assert(p6_clock_init(&s, 0, 0, 1) == P6_CLOCK_ARGUMENT && same(s, saved));
 assert(p6_clock_advance(&s, UINT64_MAX) == P6_CLOCK_RANGE && same(s, saved));
 for (int field = 0; field < 6; ++field) {
  other = saved;
  if (field == 0) other.version = 2;
  if (field == 1) other.seed = 0;
  if (field == 2) other.ticks_per_second = 0;
  if (field == 3) other.subsecond = other.ticks_per_second;
  if (field == 4) other.seconds = P6_CIVIL_MIN_SECONDS - 1;
  if (field == 5) other.seconds = P6_CIVIL_MAX_SECONDS + 1;
  assert(p6_clock_restore(&s, &other) == P6_CLOCK_STATE && same(s, saved));
 }
 assert(!p6_clock_restore(&s, &s) && same(s, saved));
 assert(!p6_clock_init(&s, 0, UINT64_MAX, UINT32_MAX));
 assert(!p6_clock_advance(&s, UINT64_MAX - 1) && s.seconds == 0);
 assert(!p6_clock_advance(&s, UINT64_MAX - 1) && s.seconds == 1 && s.subsecond == UINT64_MAX - 2);
 assert(!p6_clock_advance(&s, 2) && s.seconds == 2 && !s.subsecond);
 assert(!p6_clock_seed(&s, &seed) && seed == UINT32_MAX);
 currprefs.seed = seed; uaerandomizeseed(); assert(uaerandgetseed() == UINT32_MAX);
 assert(!p6_clock_init(&s, P6_CIVIL_MAX_SECONDS, 10, 1) && !p6_clock_advance(&s, 9));
 saved = s;
 assert(p6_clock_advance(&s, 1) == P6_CLOCK_RANGE && same(s, saved));
 p6_civil_time before = civil;
 assert(p6_clock_read(&s, 1, &civil) == P6_CLOCK_RANGE && !memcmp(&civil, &before, sizeof civil));
 assert(!p6_clock_init(&s, P6_CIVIL_MIN_SECONDS, 1, 1));
 assert(p6_clock_read(&s, -1, &civil) == P6_CLOCK_RANGE && !memcmp(&civil, &before, sizeof civil));
 assert(!p6_clock_advance(&s, (uint64_t)(P6_CIVIL_MAX_SECONDS - P6_CIVIL_MIN_SECONDS)));
 assert(s.seconds == P6_CIVIL_MAX_SECONDS && !p6_clock_read(&s, 0, &civil));
 assert(civil.year == 9999 && civil.month == 12 && civil.day == 31 && civil.second == 59);
 assert(p6_clock_read(NULL, 0, &civil) == P6_CLOCK_ARGUMENT && p6_clock_read(&s, 0, NULL) == P6_CLOCK_ARGUMENT);
 assert(p6_clock_init(NULL, 0, 1, 1) == P6_CLOCK_ARGUMENT && p6_clock_advance(NULL, 1) == P6_CLOCK_ARGUMENT);
 assert(p6_clock_restore(&s, NULL) == P6_CLOCK_ARGUMENT && p6_clock_restore(NULL, &s) == P6_CLOCK_ARGUMENT);
 assert(p6_clock_seed(&s, NULL) == P6_CLOCK_ARGUMENT);
 return 0;
}
