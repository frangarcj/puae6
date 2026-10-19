#include "cd_wave.h"
#include <string.h>
namespace p6_cd {
static uint32_t le(const uint8_t *p, unsigned bytes)
{
    uint32_t value = 0;
    for (unsigned n = 0; n < bytes; ++n) value |= uint32_t(p[n]) << (n * 8);
    return value;
}
Result wave_slice(ReadFn read, const void *context, uint64_t size, Slice *out)
{
    if (!read || !out || size < 12) return Result::Invalid;
    auto exact = [&](uint64_t at, void *data, size_t length) {
        size_t got = 0;
        return read(context, at, data, length, &got) && got == length;
    };
    uint8_t header[16];
    if (!exact(0, header, 12)) return Result::ReadError;
    if (memcmp(header, "RIFF", 4) || memcmp(header + 8, "WAVE", 4)) return Result::Unsupported;
    const uint64_t end = uint64_t(le(header + 4, 4)) + 8;
    if (end != size || end < 12) return Result::Range;
    bool format = false, data = false;
    Slice slice{};
    uint64_t at = 12;
    for (unsigned chunks = 0; at < end; ++chunks) {
        if (chunks >= 1024 || end - at < 8) return Result::Invalid;
        if (!exact(at, header, 8)) return Result::ReadError;
        const uint32_t length = le(header + 4, 4);
        at += 8;
        const uint64_t padded = uint64_t(length) + (length & 1);
        if (padded > end - at) return Result::Range;
        if (!memcmp(header, "fmt ", 4)) {
            if (format || length < 16) return Result::Invalid;
            if (!exact(at, header, 16)) return Result::ReadError;
            if (le(header, 2) != 1 || le(header + 2, 2) != 2 || le(header + 4, 4) != 44100 ||
                le(header + 8, 4) != 176400 || le(header + 12, 2) != 4 || le(header + 14, 2) != 16) return Result::Unsupported;
            format = true;
        } else if (!memcmp(header, "data", 4)) {
            if (data || !length || length % 2352) return Result::Invalid;
            slice = {at, length, Encoding::AudioPcmS16Le}; data = true;
        }
        at += padded;
    }
    if (!format || !data) return Result::Invalid;
    *out = slice;
    return Result::Ok;
}
}
