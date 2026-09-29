// Identity and capability reporting of the native PUAE6 ABI (bridge/include/p6.h).
// Nothing here touches the engine: the values describe what the linked core
// implements, never what is planned. Capability bits stay clear until the
// corresponding behaviour exists and has passed its gate.
#include "p6.h"
#include "error_util.h"
#include "video_geometry.h"

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
    caps->flags = 0;                       // state, memory regions, media events and lag flag: not implemented
    caps->max_media = 0;                   // media are not supported yet (T12)
    caps->max_video_width = p6_bridge::kBufferWidth;      // the delivered picture buffer, both standards
    caps->max_video_height = p6_bridge::kBufferHeightPal;
    caps->max_audio_frames = 0;
    return P6_OK;
}
