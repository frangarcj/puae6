// Identity and capability reporting of the native PUAE6 ABI (bridge/include/p6.h).
// Nothing here touches the engine: the values describe what the linked core
// implements, never what is planned. Capability bits stay clear until the
// corresponding behaviour exists and has passed its gate.
#include "p6.h"
#include <string.h>

static void clear_error(p6_error *error)
{
    if (error) error->message[0] = 0;
}

static p6_result fail(p6_error *error, p6_result code, const char *text)
{
    if (error) {
        size_t n = strlen(text);
        if (n >= P6_ERROR_TEXT_CAPACITY) n = P6_ERROR_TEXT_CAPACITY - 1;
        memcpy(error->message, text, n);
        error->message[n] = 0;
    }
    return code;
}

extern "C" uint32_t p6_abi_version(void)
{
    return P6_ABI_VERSION;
}

extern "C" p6_result p6_query_caps(p6_caps *caps, p6_error *error)
{
    if (error && (error->abi_version != P6_ABI_VERSION || error->struct_size != sizeof *error))
        return P6_ERROR_BAD_ARGUMENT;      // no valid error struct to write to
    clear_error(error);
    if (!caps) return fail(error, P6_ERROR_BAD_ARGUMENT, "caps is null");
    if (caps->abi_version != P6_ABI_VERSION || caps->struct_size != sizeof *caps)
        return fail(error, P6_ERROR_BAD_ARGUMENT, "caps has the wrong ABI version or size");
    caps->flags = 0;                       // state, memory regions, media events and lag flag: not implemented
    caps->max_media = 0;                   // no media can be loaded before p6_load exists
    caps->max_video_width = 0;             // no frame is produced yet
    caps->max_video_height = 0;
    caps->max_audio_frames = 0;
    return P6_OK;
}
