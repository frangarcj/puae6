// SPDX-License-Identifier: MIT
// Rejected fixture: compiler TLS that failed inside Waterbox (T09b). It must
// build like any guest and be rejected by the WB16 lint. Never execute it.
#include <cstdint>
#include "emulibc.h"

static thread_local uint64_t tls_value = 7;

ECL_EXPORT uint64_t tls_negative() { return ++tls_value; }
