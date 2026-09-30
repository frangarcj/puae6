// Contract checks of p6_abi_version / p6_query_caps (bridge/caps.cpp).
#include <cassert>
#include <cstdio>
#include <cstring>
#include "p6.h"

static p6_error fresh_error()
{
    p6_error e;
    std::memset(&e, 'x', sizeof e);          // prove that success clears the message
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    return e;
}
static p6_caps fresh_caps()
{
    p6_caps c;
    std::memset(&c, 0xa5, sizeof c);
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    return c;
}

int main()
{
    assert(p6_abi_version() == P6_ABI_VERSION && P6_ABI_VERSION == 1);
    { // success, with and without an error struct
        p6_caps caps = fresh_caps();
        p6_error error = fresh_error();
        assert(p6_query_caps(&caps, &error) == P6_OK && error.message[0] == 0);
        assert(caps.abi_version == P6_ABI_VERSION && caps.struct_size == sizeof caps);
        assert(caps.flags == (P6_CAP_LAG_FLAG | P6_CAP_MEDIA_EVENTS));  // capability bits describe implemented behaviour only
        assert(caps.max_media == P6_MAX_MEDIA && caps.max_video_width == 760 && caps.max_video_height == 576 &&
               caps.max_audio_frames == 8192);
        caps = fresh_caps();
        assert(p6_query_caps(&caps, nullptr) == P6_OK);
    }
    { // null output
        p6_error error = fresh_error();
        assert(p6_query_caps(nullptr, &error) == P6_ERROR_BAD_ARGUMENT);
        assert(error.message[0] != 0 && std::strlen(error.message) < P6_ERROR_TEXT_CAPACITY);
        assert(p6_query_caps(nullptr, nullptr) == P6_ERROR_BAD_ARGUMENT);
    }
    { // wrong version or size: rejected, output struct untouched
        for (int variant = 0; variant < 3; ++variant) {
            p6_caps caps = fresh_caps();
            if (variant == 0) caps.abi_version = 2;
            if (variant == 1) caps.struct_size = sizeof caps - 4;
            if (variant == 2) caps.struct_size = sizeof caps + 4;
            const p6_caps before = caps;
            p6_error error = fresh_error();
            assert(p6_query_caps(&caps, &error) == P6_ERROR_BAD_ARGUMENT);
            assert(std::memcmp(&caps, &before, sizeof caps) == 0 && error.message[0] != 0);
        }
    }
    { // an unusable error struct is a bad argument and is never written through
        p6_caps caps = fresh_caps();
        p6_error error = fresh_error();
        error.abi_version = 9;
        const p6_error before = error;
        assert(p6_query_caps(&caps, &error) == P6_ERROR_BAD_ARGUMENT);
        assert(std::memcmp(&error, &before, sizeof error) == 0);
        error = fresh_error();
        error.struct_size = 1;
        assert(p6_query_caps(&caps, &error) == P6_ERROR_BAD_ARGUMENT);
    }
    std::puts("bridge-caps-ok");
    return 0;
}
