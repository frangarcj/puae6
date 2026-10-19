#include "cd_source.h"
#include <string.h>

namespace p6_cd {
static bool valid(const Identity &id, ReadFn read)
{
    return read && id.bytes && (id.sector_bytes == 1 ||
        (id.sector_bytes == kIsoSectorBytes && id.bytes % kIsoSectorBytes == 0));
}

Result Source::bind(const Identity &id, ReadFn read, const void *context)
{
    if (read_ || !valid(id, read)) return Result::Invalid;
    identity_ = id;
    read_ = read;
    context_ = context;
    return Result::Ok;
}

Result Source::rebind(const Identity &id, ReadFn read, const void *context)
{
    if (!read_ || !valid(id, read)) return Result::Invalid;
    if (id.bytes != identity_.bytes || id.sector_bytes != identity_.sector_bytes ||
        memcmp(id.content_id, identity_.content_id, sizeof id.content_id)) return Result::Incompatible;
    read_ = read;
    context_ = context;
    return Result::Ok;
}

Result Source::read_at(uint64_t offset, void *out, size_t length) const
{
    if (!read_ || length > kMaxReadBytes || (length && !out)) return Result::Invalid;
    if (offset > identity_.bytes || length > identity_.bytes - offset) return Result::Range;
    if (!length) return Result::Ok;
    uint8_t staging[kMaxReadBytes];
    size_t got = 0;
    if (!read_(context_, offset, staging, length, &got) || got != length) return Result::ReadError;
    memcpy(out, staging, length);
    return Result::Ok;
}

Result Source::read_sector(uint64_t lba, void *out, size_t capacity) const
{
    if (!read_ || identity_.sector_bytes != kIsoSectorBytes || capacity < kIsoSectorBytes || !out) return Result::Invalid;
    // Division before multiplication: even an LBA near UINT64_MAX cannot wrap.
    if (lba >= identity_.bytes / kIsoSectorBytes) return Result::Range;
    return read_at(lba * kIsoSectorBytes, out, kIsoSectorBytes);
}
} // namespace p6_cd
