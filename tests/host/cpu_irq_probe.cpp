// Exercise the real CPU IRQ helpers with instrumented custom-chip boundaries.
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "custom.h"
#include "devices.h"
#include "newcpu.h"
#include "uae.h"
#include "autoconf.h"
#include <cassert>
#include <cstdint>

uae_prefs currprefs{};
regstruct regs{};
uae_u16 intreq;
volatile uae_atomic uae_int_requested;
int pissoff;
static unsigned intreq_int_calls, intreq0_calls, intreqf_calls, doint_calls;
static int last_num, last_delay;
static uae_u16 last_bits, intreqf_values[4];

void INTREQ_INT(int num, int delay)
{
 ++intreq_int_calls; last_num = num; last_delay = delay;
}
bool INTREQ_0(uae_u16 value)
{
 ++intreq0_calls; last_bits = value; return true;
}
void INTREQ_f(uae_u16 value)
{
 assert(intreqf_calls < 4);
 intreqf_values[intreqf_calls++] = value; last_bits = value;
}
void doint(void) { ++doint_calls; }

#include "cpu_irq.inc"

static void reset_calls()
{
 intreq_int_calls = intreq0_calls = intreqf_calls = doint_calls = 0;
 last_num = last_delay = -1; last_bits = 0;
 for (auto &value : intreqf_values) value = 0;
}

static void atomic_contract()
{
 const uae_u32 patterns[] = {0, UINT32_MAX, UINT32_C(0x80000000),
                             UINT32_C(0x55555555), UINT32_C(0xaaaaaaaa)};
 const uae_u32 masks[] = {0, UINT32_MAX, UINT32_C(0x80000000),
                          UINT32_C(0x55555555), UINT32_C(0xaaaaaaaa)};
 volatile uae_atomic value;
 for (uae_u32 pattern : patterns) {
  for (uae_u32 mask : masks) {
   value = (uae_atomic)pattern;
   uae_u32 expected = pattern & mask;
   uae_atomic old = atomic_and(&value, mask);
   assert((uae_u32)old == pattern && (uae_u32)value == expected);
   old = atomic_and(&value, mask);
   assert((uae_u32)old == expected && (uae_u32)value == expected);
   value = (uae_atomic)pattern;
   expected = pattern | mask;
   old = atomic_or(&value, mask);
   assert((uae_u32)old == pattern && (uae_u32)value == expected);
   old = atomic_or(&value, mask);
   assert((uae_u32)old == expected && (uae_u32)value == expected);
  }
 }
}

static void synchronous_path()
{
 currprefs.cs_compatible = CP_A500;
 for (bool level6 : {false, true}) {
  const int number = level6 ? 13 : 3;
  const uae_u16 mask = (uae_u16)(1u << number);
  for (int mode = 0; mode < 3; ++mode) {
   currprefs.cpu_cycle_exact = mode == 0;
   currprefs.cpu_compatible = mode == 1;
   intreq = mask; // Exact/compatible paths still submit; fast path suppresses.
   reset_calls();
   safe_interrupt_set(IRQ_SOURCE_CIA, 0, level6);
   if (mode < 2) {
    assert(intreq_int_calls == 1 && last_num == number && last_delay == 0);
    assert(intreq0_calls == 0);
   } else {
    assert(intreq_int_calls == 0 && intreq0_calls == 0);
    intreq = 0;
    safe_interrupt_set(IRQ_SOURCE_CIA, 0, level6);
    assert(intreq0_calls == 1 && last_bits == (uae_u16)(0x8000 | mask));
   }
   assert(intreqf_calls == 0 && doint_calls == 0);
   assert(regs.spcflags == 0 && uae_interrupt == 0);
   for (int i = 0; i < IRQ_SOURCE_MAX; ++i)
    assert(uae_interrupts2[i] == 0 && uae_interrupts6[i] == 0);
  }
 }
}

static void special_flags()
{
 regs.spcflags = SPCFLAG_TRACE | SPCFLAG_BRK; pissoff = 37;
 set_special_exter(SPCFLAG_UAEINT);
 assert(regs.spcflags == (SPCFLAG_TRACE | SPCFLAG_BRK | SPCFLAG_UAEINT));
 assert(pissoff == 37);
 unset_special(SPCFLAG_UAEINT);
 assert(regs.spcflags == (SPCFLAG_TRACE | SPCFLAG_BRK));
 assert(pissoff == 37);
 set_special(SPCFLAG_INT);
 assert(regs.spcflags == (SPCFLAG_TRACE | SPCFLAG_BRK | SPCFLAG_INT));
 assert(pissoff == 0);
 unset_special(SPCFLAG_INT);
 assert(regs.spcflags == (SPCFLAG_TRACE | SPCFLAG_BRK));
}

static void consume_queued_and_requested()
{
 reset_calls();
 uae_interrupt = 1; uae_interrupts2[0] = 1; uae_interrupts6[1] = 2;
 check_uae_int_request();
 assert(uae_interrupt == 0 && uae_interrupts2[0] == 0 && uae_interrupts6[1] == 0);
 assert(intreqf_calls == 2 && doint_calls == 1);
 assert(intreqf_values[0] == 0x8008 && intreqf_values[1] == 0xa000);
 reset_calls(); check_uae_int_request();
 assert(intreqf_calls == 0 && doint_calls == 0);

 reset_calls(); uae_int_requested = 0x0001 | 0x0100 | 0x010000;
 check_uae_int_request();
 assert(uae_int_requested == (0x0001 | 0x0100)); // IRQ levels remain pending.
 assert(intreqf_calls == 2 && doint_calls == 1);
 assert(intreqf_values[0] == 0x8008 && intreqf_values[1] == 0xa000);
}

int main()
{
 atomic_contract(); synchronous_path(); special_flags(); consume_queued_and_requested();
}
