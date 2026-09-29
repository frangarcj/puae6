// Whole CIA source; instrument event requests/IRQ delivery at its boundary.
#include <cia.cpp>
#ifdef TOD_HACK
#error "CIA probe must use emulated TOD"
#endif
static_assert(INT_MAX == 2147483647 && sizeof(evt_t) == 8);
uae_prefs currprefs{};
static const uae_prefs empty_prefs{}; // Avoid a multi-megabyte stack temporary.
evt_t currcycle;
ev eventtab[ev_max]{};
uae_u8 agnus_hpos;
int maxhpos = 227;
struct request { evt_t when; uae_u32 data; evfunc2 func; };
static request requests[4];
static unsigned requested, delivered, irq_count, schedules;
static bool irq_level6;
void event2_newevent_xx(int no, evt_t delay, uae_u32 data, evfunc2 func)
{
 assert(no == -1 && delay > 0 && requested < 4);
 requests[requested++] = {currcycle + delay, data, func};
}
void events_schedule() { ++schedules; }
void safe_interrupt_set(int source, int id, bool level6)
{
 assert(source == IRQ_SOURCE_CIA && id == 0);
 ++irq_count; irq_level6 = level6;
}
static void reset()
{
 memset(cia, 0, sizeof cia); memset(eventtab, 0, sizeof eventtab);
 currprefs = empty_prefs; currcycle = 0; agnus_hpos = 0;
 internaleclockphase = 0; cia_cycle_accurate = true; cia_set_eclockphase();
 requested = delivered = irq_count = schedules = 0;
}
static void fire()
{
 assert(delivered < requested);
 request r = requests[delivered++]; assert(r.when >= currcycle);
 currcycle = r.when; r.func(r.data);
}
static void tod_cases()
{
 for (int num = 0; num < 2; ++num) {
  reset(); cia[num].todon = 1; cia[num].tod = 0x123456;
  cia[num].alarm = 0xabcdef;
  // Even an invalid legacy preference cannot activate host pacing in P6.
  currprefs.tod_hack = true;
  if (num) CIAB_tod_handler(0); else CIAA_tod_handler(0);
  assert(cia[num].tod_event_state == 1 && cia[num].tod_offset == 75 && !requested);
  agnus_hpos = 74; currcycle = 74 * CYCLE_UNIT;
  CIA_tod_event_check(num); assert(cia[num].tod == 0x123456);
  agnus_hpos = 75; currcycle = 75 * CYCLE_UNIT;
  CIA_tod_event_check(num); assert(cia[num].tod == 0x123457);
  CIA_tod_event_check(num); assert(cia[num].tod == 0x123457 && !irq_count);

  reset(); cia[num].todon = 1; cia[num].tod = 0xffffff;
  cia[num].alarm = 0; cia[num].imask = ICR_ALARM;
  if (num) CIAB_tod_handler(0); else CIAA_tod_handler(0);
  assert(requested == 1 && requests[0].when == 75 * CYCLE_UNIT);
  assert(requests[0].data == unsigned(num) && requests[0].func == event_CIA_tod_inc_event);
  fire(); assert(cia[num].tod == 0 && !irq_count && requested == 2);
  assert(cia[num].icr2 == ICR_ALARM && requests[1].when == 83 * CYCLE_UNIT);
  fire(); assert(irq_count == 1 && irq_level6 == bool(num) && schedules == 1);
  assert((cia[num].icr1 & (0xe0 | ICR_ALARM)) == (0xe0 | ICR_ALARM));
  event_CIA_tod_inc_event(num); assert(cia[num].tod == 0 && irq_count == 1);

  reset(); cia[num].tod = 42; cia[num].alarm = 43;
  CIA_tod_inc(true, num); assert(cia[num].tod == 42 && !requested && !irq_count);
 }
 reset(); cia[0].todon = 1; cia[0].alarm = 100;
 CIAA_tod_handler(200); assert(cia[0].tod_event_state == 4);
 cia_delayed_tod(0); assert(cia[0].tod_event_state == 1 && !cia[0].tod);
 cia_delayed_tod(0); assert(cia[0].tod == 1 && !requested);
 reset(); cia[1].todon = 1; cia[1].alarm = 100;
 CIAB_tod_handler(200); assert(cia[1].tod_event_state == 0 && !requested);
}
static void timer_cases()
{
 for (unsigned ticks : {0u, 1u, 2u, 100u, 65535u}) {
  reset(); cia[0].t[0].cr = CR_START; cia[0].t[0].inputpipe = CIA_PIPE_ALL_MASK;
  cia[0].t[1].cr = CR_START; // Empty pipeline delays this timer by two ticks.
  eventtab[ev_cia].oldcycles = 512; currcycle = 512 + evt_t(ticks) * DIV10;
  compute_passed_time();
  assert(cia[0].t[0].passed == ticks);
  assert(cia[0].t[1].passed == (ticks > 2 ? ticks - 2 : 0));
  assert(!cia[1].t[0].passed && !cia[1].t[1].passed);
 }
 // Arithmetic clamp boundary, not a valid scheduler interval for a running CIA.
 for (evt_t delta : {evt_t(100 * DIV10), evt_t(INT_MAX), evt_t(INT_MAX) + DIV10, evt_t(1) << 40}) {
  reset(); cia[0].t[0].cr = CR_START; cia[0].t[0].inputpipe = CIA_PIPE_ALL_MASK;
  cia[0].t[0].timer = 65000;
  eventtab[ev_cia].oldcycles = 512; currcycle = 512 + delta;
  CIA_update_check();
  unsigned ticks = (delta > INT_MAX ? INT_MAX : delta) / DIV10;
  assert(cia[0].t[0].timer == ((65000 - ticks) & 0xffff));
  assert(!requested && !irq_count);
 }
}
int main() { tod_cases(); timer_cases(); }
