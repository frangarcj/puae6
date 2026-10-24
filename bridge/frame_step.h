// What frame_step.cpp offers the rest of the bridge besides p6_run_frame. The engine is advanced only from frame_step.cpp (tests/step), so the
// reset head that a state load needs is run from here.
#ifndef P6_BRIDGE_FRAME_STEP_H
#define P6_BRIDGE_FRAME_STEP_H

namespace p6_bridge {

// Runs the reset head of an image that has not started, exactly as the first p6_run_frame runs it (media of the power-on drives, then the
// head in APPLY), and nothing else: no unit is run, no input is applied. True when the head ran and the firmware is still there. Used by
// p6_state_load, which then overwrites what the head left (bridge/state.cpp).
bool run_reset_head();

}  // namespace p6_bridge
#endif
