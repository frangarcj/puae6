// State shared by the bridge units: one native instance per loaded image (docs/02-contracts.md).
#ifndef P6_BRIDGE_SESSION_H
#define P6_BRIDGE_SESSION_H
#include "p6.h"
#include <stdint.h>

namespace p6_session {

// Faulted: a unit failed after load (CPU loop left, clock or audio service error). Only unload leaves it.
enum class State { Uninitialized, Initialized, Loaded, Faulted };

struct Session {
    State state = State::Uninitialized;
    p6_config config{};
    bool active = false;          // an API call is in progress: calls are not reentrant
    uint64_t unit_index = 0;      // video units returned since load
    uint64_t master_ticks = 0;    // scheduler ticks (CYCLE_UNIT) executed since load
    int64_t cycles_seen = 0;      // engine cycle counter at the end of the previous call
    bool handed_over = false;     // the renderer handed a new picture over during the current unit
};

extern Session session;

// Rejects a call made while another is running; the destructor releases it.
struct Guard {
    bool ok;
    Guard() : ok(!session.active) { session.active = true; }
    ~Guard() { if (ok) session.active = false; }
    Guard(const Guard &) = delete;
    Guard &operator=(const Guard &) = delete;
};

}  // namespace p6_session
#endif
