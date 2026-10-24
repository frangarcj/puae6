// Host input of p6_input applied to the engine at the unit boundary (docs/02-contracts.md, "Entrada").
#ifndef P6_BRIDGE_INPUT_H
#define P6_BRIDGE_INPUT_H
#include "p6.h"
#include "p6_state_access.h"

namespace p6_bridge {

// Pure checks of a p6_input; nothing of the machine is touched, so a rejected packet changes nothing.
p6_result validate_input(const p6_input &input, p6_error *error);

// Applies `input` after the reset of the boundary and before the unit runs. Only differences from what was applied
// before reach the engine: key presses are not idempotent there and mouse deltas add up. Order: key releases
// (ascending), key presses (ascending), port 0, port 1. Returns how many key edges did not fit the guest's keyboard
// buffer; they are sent at the next unit.
unsigned apply_input(const p6_input &input);

// The lag of a unit (docs/02-contracts.md): the emulated CPU read none of JOY0DAT, JOY1DAT, POT0DAT, POT1DAT, POTGOR,
// CIA-A PRA (fire buttons and disk lines share it) or CIA-A SDR (the keyboard byte) since begin_input_unit().
// CIA-A PRB and CIA-B PRA count while the parallel adapter is attached. A poll by the
// frontend says nothing: only the guest's reads count.
void begin_input_unit();
bool guest_polled_input();

// Nothing is held any more (a load, a reset): the next packet applies every held key and button again.
void forget_input();

// The edge memory of the host (what was applied), as plain integers for the native state (bridge/state_devices.cpp). Setting it sends nothing to the engine: the
// engine's own edge memory comes back with the same state, and the two are checked against each other when it is written.
void applied_get(p6_applied_raw *out);
void applied_set(const p6_applied_raw &in);

// Pure encoding of CD32 button levels to WinUAE's seven hardware bits (not host-device widgets).
uint32_t cd32_buttons(uint32_t buttons);
uint32_t parallel_directions(uint32_t buttons);

// The keyboard restarts with a reset requested through the API: the engine's key table and reset chord are cleared, and every key
// the host still holds goes down again once, after the keyboard's power-up stream.
void reset_keyboard();

}  // namespace p6_bridge
#endif
