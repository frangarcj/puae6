// Plain structs of fixed-width integers that the engine patches fill and read for the native state (bridge/state_devices.cpp, docs/adr/0009).
// No pointer, no bitfield, no engine type: the state encodes fields by name and never copies these as memory. Small values travel as uint32_t so
// that an accessor never narrows: the visitor range-checks them when it writes the chunk.
#ifndef P6_STATE_ACCESS_H
#define P6_STATE_ACCESS_H
#include <stdint.h>

// T67a private test boundary: the real MMIO device advances only in devices_hsync.
struct p6_cdtv_probe_state {
    uint32_t acr, words, interrupt, queued, command_input, error, dma, finished, seek;
};
bool p6_cdtv_private_start(void);
bool p6_cdtv_private_request(uint32_t word);
p6_cdtv_probe_state p6_cdtv_private_status(void);

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

// The mouse/joystick part of one gameport (inputdevice.cpp): the two quadrature counters, the pending motion and the direction memory of the host.
struct p6_input_port_raw {
    uint32_t joybutton, joydir;                      // bit n = button n; DIR_* bits
    uint32_t oleft, oright, otop, obot;              // the previous direction of each axis (0..1)
    int32_t mouse_delta[4];                          // pending relative motion per axis (a short in the engine)
    uint32_t mouse_deltanoreset[4];                  // 0..1
    int32_t mouse_x, mouse_y, mouse_frame_x, mouse_frame_y;   // JOYxDAT is the low byte of x and y
};

// What inputdevice.cpp keeps for the two gameports, the pot lines, the host's edge memory and the key table (bridge/state_devices.cpp, docs/adr/0009).
struct p6_input_raw {
    uint32_t potgo_value;                            // POTGO as the guest wrote it, bits 8..15 only
    int32_t pot_cap[2][2];                           // charge of each pot line, 0..511
    uint32_t pot_dat[2][2], pot_dat_act[2][2];       // the counters and their state: 0 stopped, 1 discharging, 2 counting
    p6_input_port_raw port[2];
    uint32_t input_frame, prev_input_frame, prev_input_vpos;     // only their difference counts (readinput), but they are carried as they are
    uint32_t joy_buttonmask[2];                      // what the engine last got from the host, per joystick
    int32_t joy_states[2][2][9];                     // per joystick and axis: the state of each sub event and the last raw value (index 8)
    uint32_t mouse_buttonmask;                       // the same for the mouse on port 0
    uint8_t keys[16];                                // the engine's key table, one bit per Amiga key code 0..127
    int32_t reset_seq, reset_seq_mode;               // the keyboard reset chord: 0 none, 1 soft, 2 hard, 3 and 4 only with a reset warning
    uint32_t cd32_enabled[2], cd32_shifter[2], cd32_clock[2]; // protocol mode, bit index 0..8, previous CIA clock level
    uint32_t second_mouse_enabled;                  // derived from applied.type[1], never an extra serialized field
    uint32_t parallel_enabled, parallel_buttons[2], parallel_dirs[2];
    uint32_t pointer_enabled, pointer_port;
    int32_t pointer_cx, pointer_cy;                  // the beam position already converted at the preceding input boundary
};

// What the bridge remembers of what it sent the engine (bridge/input.cpp): the host's edge memory, so that only differences reach the engine.
struct p6_applied_raw {
    uint32_t commands;
    uint32_t keys[4];                                // bit n = Amiga key n is down as far as the engine was told
    uint32_t type[2];                                // P6_PORT_NONE, P6_PORT_JOYSTICK or P6_PORT_MOUSE
    uint32_t buttons[2];
    uint32_t parallel_type[2], parallel_buttons[2];
    int32_t axis[2][2];                              // joystick directions -1, 0, +1 (x, y) per port
};
// HRTMon/AR1200 controls and write shadows. The accessor names the allocated 1MiB owner;
// AR1200 maps at 800000 and has an immutable lower512KiB, unlike HRTMon at A10000.
struct p6_hrtmon_raw {
    uint32_t flag, triggered, mapped; // mapped:0 dummy,1 monitor,2 HRTMon-only dummy A1..A7 + Kick A8..B0.
    uint8_t custom[512], ciaa[16], ciab[16];
};
uint8_t *p6_hrtmon_memory();
bool p6_hrtmon_get(p6_hrtmon_raw *);
void p6_hrtmon_set(const p6_hrtmon_raw *);
// AR1/2/3. Map latch, visibility and actual banks are independent causal inputs.
struct p6_ar_raw {
    int32_t flag, mapped;
    uint32_t wait_pop, wait_pc; // AR1 only; appended to its semantic body, never to AR2/3.
    uint32_t mode_read, mode_write, hardreset, hide, hidden, banks;
    uint8_t vector[4], custom[512], ciaa[16], ciab[16];
};
uint8_t *p6_ar_memory();
bool p6_ar_get(p6_ar_raw *);
bool p6_ar_validate(const p6_ar_raw *, uint32_t cpu_spc);
void p6_ar_set(const p6_ar_raw *);
void (*p6_ar_cia_handler())(uint32_t);
#endif
