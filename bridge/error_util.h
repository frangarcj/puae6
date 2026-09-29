// Error plumbing shared by the bridge units: bounded messages and the optional p6_error argument.
#ifndef P6_BRIDGE_ERROR_UTIL_H
#define P6_BRIDGE_ERROR_UTIL_H
#include "p6.h"
#include <string.h>

// False when the caller passed an error struct we cannot write to (wrong ABI version or size).
static inline bool p6_error_usable(const p6_error *error)
{
    return !error || (error->abi_version == P6_ABI_VERSION && error->struct_size == sizeof *error);
}

static inline void p6_error_clear(p6_error *error)
{
    if (error) error->message[0] = 0;
}

static inline p6_result p6_fail(p6_error *error, p6_result code, const char *text)
{
    if (error) {
        size_t n = strlen(text);
        if (n >= P6_ERROR_TEXT_CAPACITY) n = P6_ERROR_TEXT_CAPACITY - 1;
        memcpy(error->message, text, n);
        error->message[n] = 0;
    }
    return code;
}
#endif
