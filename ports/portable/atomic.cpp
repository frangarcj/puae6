#include "sysconfig.h"
#include "sysdeps.h"

// Match WinUAE's InterlockedAnd/Or old-value contract without a host runtime.
// No ABI/layout change to ordinary guest state; this does not enable workers.
static_assert(sizeof(uae_atomic) == 4 && __atomic_always_lock_free(4, nullptr),
              "P6 requires lock-free 32-bit flag operations");
uae_atomic atomic_and(volatile uae_atomic *p, uae_u32 value)
{
 return __atomic_fetch_and(p, value, __ATOMIC_SEQ_CST);
}
uae_atomic atomic_or(volatile uae_atomic *p, uae_u32 value)
{
 return __atomic_fetch_or(p, value, __ATOMIC_SEQ_CST);
}

// Interrupt request bits raised from outside the emulated CPU (native-to-Amiga calls,
// the UAE board and accelerator boards). None of those producers is built in the A500
// profile, so nothing sets a bit and the consumers in custom, devices and newcpu see a
// constant zero. It is ordinary engine state, owned by the single emulation thread
// (ADR 0002); a future producer must go through atomic_or/atomic_and above.
// autoconf.h declares it with this exact type for every consumer.
volatile uae_atomic uae_int_requested = 0;
