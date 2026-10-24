// Exercises the complete upstream events unit with instrumented chipset hooks.
// These hooks count calls; they are not an Amiga hardware implementation.
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "custom.h"
#include <stdlib.h>

uae_prefs currprefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff;
static unsigned cck_calls;
struct observation { unsigned id; evt_t cycle; };
static observation trace[64];
static unsigned trace_count;

static void record(unsigned id)
{
 assert(trace_count < sizeof trace / sizeof trace[0]);
 trace[trace_count++] = {id, get_cycles()};
}
void gui_message(const TCHAR *, ...) { abort(); }
void write_log(const TCHAR *, ...) { abort(); }
void event_doint_delay_do_ext(uae_u32 data) { record(data); }

int do_cycles_cck(int cycles)
{
 assert(cycles >= CYCLE_UNIT);
 ++cck_calls;
 ++currcycle_cck;
 currcycle += CYCLE_UNIT;
 return CYCLE_UNIT; // Exercise repeated dispatch, as when custom_fastmode exits.
}

static void reset()
{
 currprefs.cpu_thread = false;
 currprefs.cachesize = 0;
 currprefs.cpu_memory_cycle_exact = true;
 currcycle = 0; currcycle_cck = 0; pissoff = 0; trace_count = 0; cck_calls = 0;
 is_syncline = 0; event_wait = false;
 clear_events();
 eventtab[ev_misc].handler = MISC_handler;
}
static void primary_sync()
{
 record(1);
 if (currcycle == CYCLE_UNIT) eventtab[ev_sync].evtime += CYCLE_UNIT;
 else eventtab[ev_sync].active = false;
}
static void primary_cia() { record(2); eventtab[ev_cia].active = false; }
static void primary_misc() { record(3); eventtab[ev_misc].active = false; }
static void primary_audio() { record(4); eventtab[ev_audio].active = false; }
static void secondary(uae_u32 data)
{
 record(data);
 if (data == 10) event2_newevent_xx(-1, CYCLE_UNIT, 20, secondary);
}
static void check(unsigned index, unsigned id, evt_t cycle)
{
 assert(index < trace_count && trace[index].id == id && trace[index].cycle == cycle);
}

static void primary_order()
{
 reset();
 evfunc handlers[] = {primary_sync, primary_cia, primary_misc, primary_audio};
 for (int i = 0; i < ev_max; ++i)
  eventtab[i] = {true, CYCLE_UNIT, 0, handlers[i]};
 events_schedule();
 assert(nextevent == CYCLE_UNIT);
 do_cycles_normal(3 * CYCLE_UNIT);
 assert(currcycle == 3 * CYCLE_UNIT && nextevent == EVT_MAX && trace_count == 5);
 for (unsigned i = 0; i < 4; ++i) check(i, i + 1, CYCLE_UNIT);
 check(4, 1, 2 * CYCLE_UNIT);
 assert(!cck_calls);
 clear_events();
 for (int i = 0; i < ev_max; ++i)
  assert(!eventtab[i].active && eventtab[i].oldcycles == currcycle);
}

static void secondary_queue()
{
 reset();
 event2_newevent_xx(3, 2 * CYCLE_UNIT, 42, secondary);
 event2_newevent_xx(4, CYCLE_UNIT, 99, secondary);
 do_cycles_normal(3 * CYCLE_UNIT);
 assert(trace_count == 2 && nextevent == EVT_MAX);
 check(0, 99, CYCLE_UNIT); check(1, 42, 2 * CYCLE_UNIT);
 reset();
 event2_newevent_xx(-1, CYCLE_UNIT, 10, secondary);
 do_cycles_normal(3 * CYCLE_UNIT);
 assert(trace_count == 2);
 check(0, 10, CYCLE_UNIT); check(1, 20, 2 * CYCLE_UNIT);
 reset();
 event2_newevent_xx(-1, 4 * CYCLE_UNIT, 30, secondary);
 assert(event2_newevent_x_exists(secondary));
 event2_newevent_x_replace(1, 31, secondary);
 do_cycles_normal(5 * CYCLE_UNIT);
 assert(trace_count == 1); check(0, 31, CYCLE_UNIT);
 event2_newevent_xx(-1, CYCLE_UNIT, 32, secondary);
 event2_newevent_x_remove(secondary);
 do_cycles_normal(CYCLE_UNIT);
 assert(trace_count == 1 && !event2_newevent_x_exists(secondary));
}

static void cycle_partition_and_pacing()
{
 // Every former host-pacing mode must produce the same cycle accounting.
 for (int mode : {0, -1, -2, -3, -10, -11, -100, 50}) {
  reset(); is_syncline = mode; is_syncline_end = 123456;
  event_wait = true; vsyncmintime = 765432;
  pissoff = 2 * CYCLE_UNIT;
  do_cycles_slow(CYCLE_UNIT);
  assert(!currcycle && !cck_calls && pissoff == CYCLE_UNIT);
  do_cycles_slow(CYCLE_UNIT);
  assert(!currcycle && !cck_calls && !pissoff);
  // Exercise carry across 1536 tiny calls, not nominal per-frame increments.
  for (int i = 1; i <= 3 * CYCLE_UNIT; ++i) {
   do_cycles_slow(1);
   assert(currcycle == (i / CYCLE_UNIT) * CYCLE_UNIT);
   assert(currcycle_cck == (unsigned)(i / CYCLE_UNIT));
  }
  assert(cck_calls == 3 && !pissoff);
  pissoff = 17; events_reset_syncline();
  assert(!is_syncline && !pissoff); // Preserve upstream cycles_do_special.
 }
 reset();
 do_extra_cycles(2 * CYCLE_UNIT);
 do_cycles_slow(CYCLE_UNIT);
 assert(currcycle == 3 * CYCLE_UNIT && cck_calls == 3 && !pissoff);
 // The ev_sync path uses the actual primary dispatcher, not the CCK hook.
 reset();
 eventtab[ev_sync] = {true, CYCLE_UNIT, 0, primary_sync};
 events_schedule();
 do_cycles_slow(3 * CYCLE_UNIT);
 assert(currcycle == 3 * CYCLE_UNIT && trace_count == 2 && !cck_calls);
 check(0, 1, CYCLE_UNIT); check(1, 1, 2 * CYCLE_UNIT);
}

int main()
{
 primary_order(); secondary_queue(); cycle_partition_and_pacing();
 return 0;
}
