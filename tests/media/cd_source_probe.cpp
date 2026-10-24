// Synthetic immutable images: no image-sized allocation, file, engine or ROM.
#include "cd_source.h"
#include <initializer_list>
#include <stdio.h>
#include <string.h>
using namespace p6_cd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static_assert(sizeof(Source) <= 128, "source metadata must stay bounded");
struct Provider {
    uint64_t bytes, last_offset = 0;
    size_t calls = 0, last_length = 0, replacement_calls = 0;
    int fault = 0;
};
static uint8_t value(uint64_t at) { return uint8_t((at ^ (at >> 13) ^ (at >> 32)) * 17 + 29); }
static bool provide(const void *context, uint64_t at, void *out, size_t length, size_t *got)
{
    auto &p = *const_cast<Provider *>(static_cast<const Provider *>(context));
    ++p.calls;
    p.last_offset = at;
    p.last_length = length;
    *got = 0;
    if (length > kMaxReadBytes || at > p.bytes || length > p.bytes - at) return false;
    auto *data = static_cast<uint8_t *>(out);
    for (size_t n = 0; n < length; ++n) data[n] = value(at + n);
    *got = p.fault == 1 ? length - 1 : p.fault == 3 ? length + 1 : length;
    return p.fault != 2;  // error after writing all bytes, too
}
static bool replacement(const void *context, uint64_t at, void *out, size_t length, size_t *got)
{
    ++const_cast<Provider *>(static_cast<const Provider *>(context))->replacement_calls;
    return provide(context, at, out, length, got);
}
static Identity identity(uint64_t bytes)
{
    Identity id{};
    for (unsigned n = 0; n < sizeof id.content_id; ++n) id.content_id[n] = uint8_t(n + 1);
    id.bytes = bytes;
    id.sector_bytes = kIsoSectorBytes;
    return id;
}
static bool filled(const uint8_t *data, size_t length, uint8_t byte)
{
    for (size_t n = 0; n < length; ++n) if (data[n] != byte) return false;
    return true;
}
static int reads()
{
    for (uint64_t bytes : {UINT64_C(650) * 1024 * 1024, (UINT64_C(1) << 32) + 4 * kIsoSectorBytes}) {
        Provider p{bytes};
        Source source;
        Identity id = identity(bytes);
        CHECK(source.bind(id, provide, &p) == Result::Ok);
        id.bytes = 0;  // binding owns its descriptor copy
        uint8_t output[kMaxReadBytes + 16];
        const uint64_t lbas[] = {bytes / kIsoSectorBytes - 1, 0, 17, 1, bytes / kIsoSectorBytes - 1};
        for (uint64_t lba : lbas) {
            memset(output, 0xa5, sizeof output);
            CHECK(source.read_sector(lba, output + 8, kIsoSectorBytes) == Result::Ok);
            CHECK(p.last_offset == lba * kIsoSectorBytes && p.last_length == kIsoSectorBytes);
            CHECK(filled(output, 8, 0xa5) && filled(output + 8 + kIsoSectorBytes, sizeof output - 8 - kIsoSectorBytes, 0xa5));
            for (size_t n = 0; n < kIsoSectorBytes; ++n) CHECK(output[8 + n] == value(lba * kIsoSectorBytes + n));
        }
        CHECK(p.calls == 5);
        CHECK(source.read_at(bytes - kMaxReadBytes, output + 8, kMaxReadBytes) == Result::Ok);
        CHECK(p.last_offset == bytes - kMaxReadBytes && p.last_length == kMaxReadBytes);
        CHECK(filled(output, 8, 0xa5) && filled(output + 8 + kMaxReadBytes, 8, 0xa5));
        for (size_t n = 0; n < kMaxReadBytes; ++n) CHECK(output[8 + n] == value(bytes - kMaxReadBytes + n));
    }
    return 0;
}
static int failures()
{
    Source source;
    uint8_t output[kMaxReadBytes + 16];
    memset(output, 0xa5, sizeof output);
    CHECK(source.read_sector(0, output, sizeof output) == Result::Invalid);
    CHECK(source.rebind(identity(2048), provide, nullptr) == Result::Invalid);
    CHECK(source.bind(identity(2048), nullptr, nullptr) == Result::Invalid);
    for (uint64_t bytes : {UINT64_C(0), UINT64_C(2047), UINT64_MAX})
        CHECK(source.bind(identity(bytes), provide, nullptr) == Result::Invalid);
    Provider p{UINT64_MAX - 2047};  // largest aligned length, never allocated
    CHECK(source.bind(identity(p.bytes), provide, &p) == Result::Ok);
    CHECK(source.bind(identity(2048), provide, &p) == Result::Invalid);
    CHECK(source.read_at(UINT64_MAX, output, 1) == Result::Range);
    CHECK(source.read_at(p.bytes - 1, output, kMaxReadBytes) == Result::Range);
    CHECK(source.read_sector(UINT64_MAX, output, sizeof output) == Result::Range);
    CHECK(source.read_sector(p.bytes / kIsoSectorBytes, output, sizeof output) == Result::Range);
    CHECK(source.read_sector(0, output, kIsoSectorBytes - 1) == Result::Invalid);
    CHECK(source.read_at(0, output, kMaxReadBytes + 1) == Result::Invalid);
    CHECK(source.read_at(0, nullptr, 1) == Result::Invalid);
    CHECK(source.read_at(p.bytes, nullptr, 0) == Result::Ok);
    CHECK(source.read_at(p.bytes + 1, nullptr, 0) == Result::Range);
    CHECK(p.calls == 0 && filled(output, sizeof output, 0xa5));
    for (int fault = 1; fault <= 3; ++fault) {
        p.fault = fault;
        CHECK(source.read_at(0, output + 8, kMaxReadBytes) == Result::ReadError);
        CHECK(filled(output, sizeof output, 0xa5));
    }
    p.fault = 0;
    CHECK(source.read_sector(p.bytes / kIsoSectorBytes - 1, output + 8, kIsoSectorBytes) == Result::Ok);
    CHECK(p.last_offset == p.bytes - kIsoSectorBytes);
    for (size_t n = 0; n < kIsoSectorBytes; ++n) CHECK(output[8 + n] == value(p.bytes - kIsoSectorBytes + n));
    return 0;
}
static int rebinds()
{
    const Identity id = identity(UINT64_C(650) * 1024 * 1024);
    Provider a{id.bytes}, b{id.bytes};
    Source source;
    uint8_t first[kIsoSectorBytes], second[kIsoSectorBytes];
    CHECK(source.bind(id, provide, &a) == Result::Ok);
    CHECK(source.read_sector(123, first, sizeof first) == Result::Ok);
    for (unsigned change = 0; change < 4; ++change) {
        Identity bad = id;
        if (change == 0) bad.content_id[31] ^= 1;
        if (change == 1) bad.bytes += kIsoSectorBytes;
        if (change == 2) bad.sector_bytes = 2352;
        CHECK(source.rebind(bad, change == 3 ? nullptr : replacement, &b) != Result::Ok);
        CHECK(source.identity().bytes == id.bytes && source.identity().sector_bytes == id.sector_bytes);
        CHECK(!memcmp(source.identity().content_id, id.content_id, sizeof id.content_id));
        CHECK(source.read_sector(123, second, sizeof second) == Result::Ok && !memcmp(first, second, sizeof first));
        CHECK(a.calls == change + 2 && b.calls == 0 && a.replacement_calls == 0);
    }
    CHECK(source.rebind(id, replacement, &b) == Result::Ok);
    CHECK(source.read_sector(123, second, sizeof second) == Result::Ok && !memcmp(first, second, sizeof first));
    CHECK(a.calls == 5 && b.calls == 1 && b.replacement_calls == 1);  // old context/cache discarded
    Source restored;
    CHECK(restored.bind(source.identity(), replacement, &b) == Result::Ok);
    CHECK(restored.read_sector(123, second, sizeof second) == Result::Ok && !memcmp(first, second, sizeof first));
    return 0;
}
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "reads")) return reads();
    if (!strcmp(argv[1], "failures")) return failures();
    if (!strcmp(argv[1], "rebinds")) return rebinds();
    return 2;
}
