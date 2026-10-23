// Real CIA RTC handlers, chips, scheduler and calendar. Other memory-bank hooks
// abort if reached: this probe does not pretend to implement an Amiga chipset.
#include <cia.cpp>
#include "rtc_time.h"
uae_prefs currprefs{};
ev eventtab[ev_max]{};
ev2 eventtab2[ev2_max]{};
int pissoff;
uae_u32 dummy_get(uaecptr, int, bool, uae_u32) { abort(); }
uae_u32 dummy_get_safe(uaecptr, int, bool, uae_u32) { abort(); }
void dummy_put(uaecptr, int, uae_u32) { abort(); }
uae_u8 *default_xlate(uaecptr) { abort(); }
int default_check(uaecptr, uae_u32) { abort(); }
uae_u32 dummy_lgeti(uaecptr) { abort(); }
uae_u32 dummy_wgeti(uaecptr) { abort(); }
void IRQ_forced(int, int) { abort(); }
void event_doint_delay_do_ext(uae_u32) { abort(); }
int do_cycles_cck(int) { abort(); }
uae_u8 cdtv_battram_read(int) { abort(); }
void cdtv_battram_write(int, int) { abort(); }
// Config parsing is outside this probe. RTC file ID only, no host resolution.
void cfgfile_resolve_path_out_load(const TCHAR *p, TCHAR *out, int size, int type)
{
 assert(!strcmp(p, "battery") && size == MAX_DPATH && type == PATH_ROM);
 strcpy(out, p);
}
static unsigned read_reg(int n) { return clock_bget(n * 4 + 1); }
static void write_reg(int n, unsigned v) { clock_bput(n * 4 + 1, v); }
static void advance(int ticks) { do_cycles_normal(ticks); }
static void check_date(bool ricoh, const unsigned *expected)
{
 for (int n = 0; n < 13; ++n) {
  assert(read_reg(n) == expected[n]);
  // Also execute rtc.cpp's null-tm path, where other upstream devices enter.
  assert((ricoh ? get_clock_ricoh(&rtc_ricoh, n, nullptr)
                : get_clock_msm(&rtc_msm, n, nullptr)) == expected[n]);
 }
}
int main()
{
 assert(!currprefs.cs_cdtvram); // CDTV battery RAM is outside this RTC scenario.
 currprefs.cs_rtc = 1; currprefs.cs_fatgaryrev = -1;
 set_cycles(512); events_schedule();
 assert(read_reg(0) == 0xff && p6_rtc_status() == P6_CLOCK_STATE);
 assert(get_clock_msm(&rtc_msm, 13, nullptr) == 0xff);
 assert(get_clock_ricoh(&rtc_ricoh, 14, nullptr) == 0xff);
 assert(read_reg(13) == 0xff && p6_rtc_status() == P6_CLOCK_STATE);
 for (int clock : {CHIPSET_CLOCK_PAL, CHIPSET_CLOCK_NTSC}) {
  const int rate = clock * CYCLE_UNIT;
  assert(p6_rtc_init(946684799, rate, 7) == 0); // 1999-12-31 23:59:59 UTC.
  for (int chip : {1, 2}) {
   currprefs.cs_rtc = chip; rtc_hardreset();
   assert(p6_rtc_init(946684799, rate, 7) == 0);
   const unsigned msm[] = {9,5,9,5,3,2,1,3,2,1,9,9,5};
   const unsigned ricoh[] = {9,5,9,5,3,2,5,1,3,2,1,9,9};
   check_date(chip == 2, chip == 2 ? ricoh : msm);
   advance(rate - 1); assert(read_reg(0) == 9);
   p6_rtc_state saved{}; assert(p6_rtc_save(&saved) == 0);
   assert(saved.clock.subsecond == unsigned(rate - 1));
   advance(1);
   const unsigned msm_next[] = {0,0,0,0,0,0,1,0,1,0,0,10,6};
   const unsigned ricoh_next[] = {0,0,0,0,0,0,6,1,0,1,0,0,0};
   check_date(chip == 2, chip == 2 ? ricoh_next : msm_next);
   rtc_hardreset(); assert(read_reg(0) == 0); // Reset preserves epoch/time.
   assert(p6_rtc_restore(&saved) == P6_CLOCK_STATE); // Scheduler mismatched.
   set_cycles(saved.cycle); assert(p6_rtc_restore(&saved) == 0);
   assert(read_reg(0) == 9); advance(1); assert(read_reg(0) == 0);
   currprefs.cs_rtc_adjust = -1; assert(read_reg(0) == 9);
   currprefs.cs_rtc_adjust = 3600; assert(read_reg(4) == 1);
   currprefs.cs_rtc_adjust = 0; assert(read_reg(4) == 0);
   write_reg(0, 7); assert(read_reg(0) == 0); // No invented date-setting path.
   write_reg(13, 15);
   assert(read_reg(13) == (chip == 2 ? 15u : 9u));
   if (chip == 2) {
    write_reg(13, 2); write_reg(0, 5);
    write_reg(13, 3); write_reg(0, 10);
    assert(rtc_ricoh.rtc_memory[0] == 0xa5 && read_reg(0) == 10);
    write_reg(13, 1); write_reg(3, 15); assert(read_reg(3) == 7);
    write_reg(0, 15); assert(read_reg(0) == 0);
    assert(rtc_ricoh.delayed_write == -1);
   } else assert(rtc_msm.delayed_write == -1);
   rtc_hardreset(); assert(read_reg(0) == 0);
   advance(rate / 2); assert(p6_rtc_rebase(0) == 0); set_cycles(0);
   advance(rate / 2); assert(read_reg(0) == 1);
   assert(p6_rtc_save(&saved) == 0);
   auto bad = saved; bad.clock.subsecond = rate;
   assert(p6_rtc_restore(&bad) == P6_CLOCK_STATE && read_reg(0) == 1);
   bad = saved; ++bad.clock.ticks_per_second;
   assert(p6_rtc_restore(&bad) == P6_CLOCK_STATE && read_reg(0) == 1);
   set_cycles(saved.cycle - 1); assert(read_reg(0) == 0xff);
   set_cycles(saved.cycle); assert(p6_rtc_status() == P6_CLOCK_STATE);
   assert(p6_rtc_restore(&saved) == 0 && read_reg(0) == 1);
  }
 }
 assert(p6_rtc_init(P6_CIVIL_MAX_SECONDS, 2, 7) == 0);
 struct tm untouched{}; untouched.tm_sec = 42;
 assert(p6_rtc_read(&untouched, 1) == P6_CLOCK_RANGE && untouched.tm_sec == 42);
 assert(p6_rtc_status() == P6_CLOCK_RANGE);
 assert(p6_rtc_init(P6_CIVIL_MAX_SECONDS, 2, 7) == 0);
 advance(2); assert(read_reg(0) == 0xff && p6_rtc_status() == P6_CLOCK_RANGE);
 p6_rtc_unload(); assert(read_reg(0) == 0xff && p6_rtc_status() == P6_CLOCK_STATE);
 assert(p6_rtc_init(0, 0, 7) == P6_CLOCK_ARGUMENT);
 assert(p6_rtc_init(0, 512, 7) == 0);
 // The production immutable VFS rejects battery exports; no disk is written.
 strcpy(currprefs.rtcfile, "battery"); write_battclock();
 assert(p6_rtc_status() == 0);
}
