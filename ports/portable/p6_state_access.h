// Plain structs of fixed-width integers that the engine patches fill and read for the native state (bridge/state_devices.cpp, docs/adr/0009).
// No pointer, no bitfield, no engine type: the state encodes fields by name and never copies these as memory. Small values travel as uint32_t so
// that an accessor never narrows: the visitor range-checks them when it writes the chunk.
#ifndef P6_STATE_ACCESS_H
#define P6_STATE_ACCESS_H
#include <stdint.h>

// One CIA timer. `timer` is the raw counter in the epoch of eventtab[ev_cia].oldcycles (the scheduler's chunk restores that), not the visible value.
struct p6_cia_timer_raw {
    uint32_t timer, latch, inputpipe, loaddelay, preovfl, cr;
};

// One CIA (cia[num] of cia.cpp), without the caches (passed, timerval_prev).
struct p6_cia_raw {
    uint32_t pra, prb, dra, drb;
    p6_cia_timer_raw t[2];
    uint32_t tod, tol, alarm, tlatch, todon;
    int32_t tod_event_state, tod_offset;
    uint32_t icr1, icr2, icr_change, imask;
    uint32_t sdr, sdr_buf, sdr_load, sdr_cnt;
};

// What cia.cpp keeps outside the two CIAs: the keyboard handshake, the E-clock phase, the power LED and its brightness for the audio filter.
struct p6_cia_misc_raw {
    uint32_t cia_cycle_accurate;
    int32_t kbstate, kblostsynccnt, internaleclockphase;
    uint32_t led;
    int32_t led_old_brightness;
    int64_t led_cycle;                     // absolute, in the cycle epoch: the visitor writes it as an age against the live counter
    int32_t led_cycles_on, led_cycles_off;
    uint32_t powerled, powerled_brightness;
    uint32_t oldovl;
};
#endif
