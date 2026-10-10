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

// The counters and the test-and-clear of the trap area and the filesystem queue (T29a). Windows' Interlocked* answers the new value of an increment or a
// decrement and the old state of the bit; the same here.
uae_atomic atomic_inc(volatile uae_atomic *p)
{
 return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST);
}
uae_atomic atomic_dec(volatile uae_atomic *p)
{
 return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST);
}
uae_u32 atomic_bit_test_and_reset(volatile uae_atomic *p, uae_u32 bit)
{
 const uae_u32 mask = uae_u32(1) << (bit & 31);
 return (uae_u32(__atomic_fetch_and(p, uae_atomic(~mask), __ATOMIC_SEQ_CST)) & mask) != 0;
}

// Interrupt request bits raised from outside the emulated CPU (native-to-Amiga calls,
// the UAE board and accelerator boards). The producers that exist are native2amiga (the EXTER queue of the
// filesystem) and the trap area, present only while a hard file is mounted (T29a); without one nothing sets a
// bit and the consumers in custom, devices and newcpu see a constant zero. It is ordinary engine state, owned by
// the single emulation thread (ADR 0002); a producer goes through atomic_or/atomic_and above.
// autoconf.h declares it with this exact type for every consumer; autoconf.cpp's own definition is excluded (patch 0164).
volatile uae_atomic uae_int_requested = 0;
