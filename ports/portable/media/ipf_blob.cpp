// open_blob of ipf_track.h: the IPF is a medium of the bridge's store (blob_io.h), read by offset. Apart from ipf_track.cpp so that the decoder links without the store (the
// tests and the fuzz target of the decoder have no engine and no store).
#include "ipf_track.h"

#include "blob_io.h"

#include <string.h>

namespace p6_ipf {

namespace {

bool read_blob(const void *context, uint64_t offset, void *out, size_t length)
{
    size_t got = 0;
    return p6_blob_pread(static_cast<const char *>(context), offset, out, length, &got) == P6_BLOB_OK && got == length;
}

}  // namespace

Image *open_blob(const char *id, const char **why)
{
    const char *unused;
    if (!why) why = &unused;
    *why = nullptr;
    uint64_t bytes = 0;
    if (!id || strlen(id) >= kContextBytes || p6_blob_size(id, &bytes, nullptr) != P6_BLOB_OK) {
        *why = "an IPF medium is not in the store";
        return nullptr;
    }
    return open(read_blob, id, bytes, why, strlen(id) + 1);        // the image keeps its own copy of the id
}

}  // namespace p6_ipf
