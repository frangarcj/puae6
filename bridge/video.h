// Video delivery of the bridge: the hand-over of each unit's picture and the video fields of p6_frame.
#ifndef P6_BRIDGE_VIDEO_H
#define P6_BRIDGE_VIDEO_H
#include "p6.h"

namespace p6_bridge {

// Hands over the picture the renderer finished for the unit that just ended. Repeating the call before the
// renderer finishes another pass changes nothing, so the STEP01 boundary observer (once per boundary, also the
// swallowed ones) and p6_run_frame (after the unit) can both call it.
void present_unit();

// Marks the start of a unit: forgets whether a picture was handed over.
void begin_unit();

// Fills the video fields of `frame` from the last hand-over (video pointer only when `deliver` is set;
// geometry and timing are always reported). Returns P6_OK, or an error before anything is written.
p6_result describe_video(p6_frame &frame, bool deliver, p6_error *error);

}  // namespace p6_bridge
#endif
