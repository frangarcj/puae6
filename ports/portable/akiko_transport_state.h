// Private transport component, not a wire format or complete CD32 state.
#pragma once
#include <stdint.h>
struct p6_akiko_transport_state {
    uint32_t service, mute_pending, read, write;
    int32_t muted, head[2];
    uint32_t words[100];
    uint8_t info[2][128], sector[2][128][2352];
    uint32_t sub_read, sub_write;
    uint8_t sub_used[36], sub[36][96];
};
// Caller supplies ordinary heap storage and a quiescent, initialized transport.
// Invalid sector/subcode payload and unused FIFO slots are canonical zeroes;
// heads, info (including 0/ff), occupied words and cursors are preserved.
// All three endpoints do no I/O, allocation, callbacks, IRQ or hardware advance.
// Preflight checks the existing owner too. Apply rechecks before any mutation;
// controller, disc/player, clock, scheduler and RAM are separate components.
bool p6_akiko_transport_get(p6_akiko_transport_state *);
bool p6_akiko_transport_validate(const p6_akiko_transport_state *);
bool p6_akiko_transport_preflight(const p6_akiko_transport_state *);
bool p6_akiko_transport_apply(const p6_akiko_transport_state *);
