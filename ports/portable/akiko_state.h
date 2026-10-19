// Private semantic aggregate, caller-heap storage; no machine wire format.
#pragma once
#include "akiko_controller_state.h"
#include "akiko_transport_state.h"
#include "media/cd_device_state.h"
#include "media/cd_device.h"
struct p6_akiko_state {
    p6_akiko_controller_state controller;
    p6_akiko_transport_state transport;
    p6_cd::DeviceState device;
};
struct p6_akiko_context {
    uint32_t config, cda_period; // Verified candidate configuration/Paula period.
    int64_t cycle;             // Candidate scheduler cycle, not live time.
    const p6_cd::Disc *disc;   // Borrowed verified identity; not serialized.
};
// Validate works without owners. Preflight/apply additionally require initialized
// destination owners and matching live CDA period. Restore external Paula,
// scheduler and RAM before advancing. No allocation/I/O/IRQ/callback/advance.
bool p6_akiko_state_get(p6_akiko_state *);
bool p6_akiko_state_validate(const p6_akiko_state *, const p6_akiko_context &);
bool p6_akiko_state_preflight(const p6_akiko_state *, const p6_akiko_context &);
bool p6_akiko_state_apply(const p6_akiko_state *, const p6_akiko_context &);
