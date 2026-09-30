#include "audio_drain.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "sounddep/sound.h"

#include <stddef.h>
#include <stdint.h>

namespace p6_bridge {

void discard_audio()
{
    int16_t scratch[2048];
    size_t written;
    do {
        written = 0;
        if (p6_pcm_read(scratch, sizeof scratch / (2 * sizeof scratch[0]), &written) != 0) break;
    } while (written);
}

}  // namespace p6_bridge
