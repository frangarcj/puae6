// Host input of p6_input applied to the engine at the unit boundary (docs/02-contracts.md, "Entrada").
#ifndef P6_BRIDGE_INPUT_H
#define P6_BRIDGE_INPUT_H
#include "p6.h"

namespace p6_bridge {

// Pure checks of a p6_input; nothing of the machine is touched, so a rejected packet changes nothing.
p6_result validate_input(const p6_input &input, p6_error *error);

// Applies `input` after the reset of the boundary and before the unit runs. Only differences from what was applied
// before reach the engine: key presses are not idempotent there and mouse deltas add up. Order: key releases
// (ascending), key presses (ascending), port 0, port 1. Returns how many key edges did not fit the guest's keyboard
// buffer; they are sent at the next unit.
unsigned apply_input(const p6_input &input);

// Nothing is held any more (a load, a reset): the next packet applies every held key and button again.
void forget_input();

}  // namespace p6_bridge
#endif
