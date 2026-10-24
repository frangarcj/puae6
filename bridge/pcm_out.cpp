#include "pcm_out.h"
#include "error_util.h"
#include "session.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "audio.h"
#include "sounddep/sound.h"

#include <stddef.h>

static_assert(p6_bridge::kMaxAudioFrames == P6_PCM_CAPACITY_FRAMES, "caps must publish the capacity of the PCM queue");

namespace p6_bridge {

namespace {
int16_t out[size_t(P6_PCM_CAPACITY_FRAMES) * 2];
}

void sync_audio_to_boundary()
{
    update_audio();
}

p6_result take_audio(uint64_t *frames, p6_error *error)
{
    size_t n = 0;
    const int rc = p6_pcm_read(out, P6_PCM_CAPACITY_FRAMES, &n);
    *frames = 0;
    if (rc == P6_PCM_CAPACITY)
        return p6_fail(error, P6_ERROR_CAPACITY, "the PCM queue overflowed");
    if (rc != 0)
        return p6_fail(error, P6_ERROR_INTERNAL, "the PCM transport reported an inconsistency");
    p6_session::session.audio_frames_total += n;
    *frames = n;
    return P6_OK;
}

const int16_t *audio_samples()
{
    return out;
}

}  // namespace p6_bridge
