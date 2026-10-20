// Identity and capability reporting of the native PUAE6 ABI (bridge/include/p6.h).
// Nothing here touches the engine: the values describe what the linked core
// implements, never what is planned. Capability bits stay clear until the
// corresponding behaviour exists and has passed its gate; P6_CAP_STATE additionally
// follows the image: it is set only where bridge/state.cpp is linked.
#include "p6.h"
#include "error_util.h"
#include "video_geometry.h"
#include "pcm_out.h"
#include "config.h"

namespace p6_bridge {
// Set by the static initialisation of bridge/state.cpp (p6_state), which the headless runner, the frontend adapters and the Waterbox guest do not link.
bool state_linked = false;
bool profile_state_supported = true;
}

extern "C" uint32_t p6_abi_version(void)
{
    return P6_ABI_VERSION;
}

extern "C" p6_result p6_query_caps(p6_caps *caps, p6_error *error)
{
    if (!p6_error_usable(error))
        return P6_ERROR_BAD_ARGUMENT;      // no valid error struct to write to
    p6_error_clear(error);
    if (!caps) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "caps is null");
    if (caps->abi_version != P6_ABI_VERSION || caps->struct_size != sizeof *caps)
        return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "caps has the wrong ABI version or size");
    caps->flags = P6_CAP_HRTMON | P6_CAP_INPUT_V2 | P6_CAP_LAG_FLAG | P6_CAP_MEDIA_EVENTS | P6_CAP_MEMORY_REGIONS | P6_CAP_MEDIA_APPEND | P6_CAP_MEDIA_WRITE | P6_CAP_MEDIA_HDF | P6_CAP_MEDIA_EXT_ADF | P6_CAP_MEDIA_FDI | P6_CAP_MEDIA_IPF;
    if (p6_bridge::state_linked && p6_bridge::profile_state_supported) caps->flags |= P6_CAP_STATE;    // the native state (T15): STATE01, STATE02, DET02, DET03 and FUZZ01 passed
    caps->max_media = P6_MAX_MEDIA;        // the manifest holds this many disk images, whether p6_load or p6_media_append registered them (mount events: T12b)
    caps->max_video_width = p6_bridge::kBufferWidth;      // the delivered picture buffer, both standards
    caps->max_video_height = p6_bridge::kBufferHeightPal;
    caps->max_audio_frames = p6_bridge::kMaxAudioFrames;   // one call never returns more than the PCM queue holds
    return P6_OK;
}
