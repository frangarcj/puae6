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
