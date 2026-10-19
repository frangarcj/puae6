#ifndef P6_CD_SUBCHANNEL_H
#define P6_CD_SUBCHANNEL_H
#include "cd_disc.h"
namespace p6_cd {
// Synthetic P-W96 deinterleaved: P12, Q12, R-W72 zero. Never replaces stored/SBI.
// Track geometry is validated by the consumer. control/ADR are effective for
// this LBA: the consumer may pass a copy selecting the previous track's control
// in a long audio->data pregap. Number/indices still describe the current track.
// ModeA countdown (last INDEX00=00:00:01); pause is explicit, including postgap.
// Errors preserve output. No I/O, playback state or interpretation of stored data.
Result synth_subchannel(const Track &track, uint32_t number, uint32_t lba,
                        bool pause, void *out, size_t capacity);
}
#endif
