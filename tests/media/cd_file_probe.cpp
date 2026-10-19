#include "../../adapters/libretro/cd_file.h"
#include <initializer_list>
using namespace p6_cd;
using p6_libretro::CdFile;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
struct retro_vfs_file_handle { uint64_t bytes = 0, position = 0; int fault = 0, reads = 0, closes = 0; bool seek_zero = false; };
static retro_vfs_file_handle fake;
static uint8_t value(uint64_t at) { return uint8_t((at ^ (at >> 32)) * 17); }
static retro_vfs_file_handle *RETRO_CALLCONV open_file(const char *, unsigned mode, unsigned)
{
    return mode == RETRO_VFS_FILE_ACCESS_READ ? &fake : nullptr;
}
static int RETRO_CALLCONV close_file(retro_vfs_file_handle *f) { ++f->closes; return 0; }
static int64_t RETRO_CALLCONV file_size(retro_vfs_file_handle *f) { return f->fault == 4 ? -1 : int64_t(f->bytes); }
static int64_t RETRO_CALLCONV tell_file(retro_vfs_file_handle *f) { return int64_t(f->position); }
static int64_t RETRO_CALLCONV seek_file(retro_vfs_file_handle *f, int64_t at, int origin)
{
    if (at < 0 || origin != RETRO_VFS_SEEK_POSITION_START || f->fault == 7) return -1;
    f->position = uint64_t(at) + (f->fault == 3);
    return f->seek_zero ? 0 : int64_t(f->position);
}
static int64_t RETRO_CALLCONV read_file(retro_vfs_file_handle *f, void *out, uint64_t length)
{
    ++f->reads;
    if (f->fault == 1 || f->position >= f->bytes) return 0;
    if (f->fault == 2) return -1;
    if (f->position == 3 && (f->fault == 8 || f->fault == 9)) return f->fault == 8 ? 0 : -1;
    uint64_t got = length < f->bytes - f->position ? length : f->bytes - f->position;
    if ((f->fault == 6 || f->fault == 8 || f->fault == 9) && got > 3) got = 3;
    for (size_t n = 0; n < got; ++n) static_cast<uint8_t *>(out)[n] = value(f->position + n);
    f->position += got;
    return f->fault == 5 ? int64_t(length) + 1 : int64_t(got);
}
static void hex(const uint8_t *bytes) { for (unsigned n = 0; n < 32; ++n) printf("%02x", bytes[n]); }
static int vfs_tests()
{
    retro_vfs_interface api{};
    api.open = open_file; api.close = close_file; api.size = file_size; api.seek = seek_file; api.tell = tell_file; api.read = read_file;
    fake.bytes = (UINT64_C(1) << 32) + 8193;
    uint8_t out[32]; memset(out, 0xa5, sizeof out);
    {
        CdFile file;
        CHECK(file.open("virtual", &api) && file.size() == fake.bytes);
        CHECK(file.read_at(fake.bytes - 8, out + 8, 8));
        for (unsigned n = 0; n < 32; ++n) CHECK(out[n] == (n >= 8 && n < 16 ? value(fake.bytes - 16 + n) : 0xa5));
        const auto calls = fake.reads;
        CHECK(!file.read_at(UINT64_MAX, out, 1) && !file.read_at(fake.bytes, out, 1) && fake.reads == calls);
        CHECK(!file.read_at(0, nullptr, 1) && !file.read_at(0, out, 65537) && fake.reads == calls);
        CHECK(file.read_at(fake.bytes, nullptr, 0));
        fake.fault = 6;
        for (bool zero : {false, true}) {
            fake.seek_zero = zero;
            CHECK(file.read_at(fake.bytes - 8, out + 8, 8));
            for (unsigned n = 0; n < 8; ++n) CHECK(out[8 + n] == value(fake.bytes - 8 + n));
        }
        for (int fault : {1, 2, 3, 5, 7, 8, 9}) {
            fake.fault = fault; memset(out, 0xa5, sizeof out);
            CHECK(!file.read_at(0, out + 8, 8));
            for (uint8_t byte : out) CHECK(byte == 0xa5);
        }
    }
    CHECK(fake.closes == 1);
    fake.fault = 4;
    { CdFile file; CHECK(!file.open("bad-size", &api)); }
    CHECK(fake.closes == 2);
    auto incomplete = api; incomplete.read = nullptr;
    { CdFile missing; CHECK(!missing.open("no-fallback", &incomplete)); }
    incomplete = api; incomplete.tell = nullptr;
    { CdFile missing; CHECK(!missing.open("no-tell", &incomplete)); }
    fake.bytes = 131073; fake.fault = 1;
    CdFile file;
    Binding binding{}; binding.identity.content_id[0] = 0xa5;
    CHECK(file.open("small", &api));
    CHECK(file.bind(Encoding::Opaque, &binding) == Result::ReadError && binding.identity.content_id[0] == 0xa5);
    fake.fault = 0;
    CHECK(file.bind(Encoding::Opaque, &binding) == Result::Ok);
    CHECK(file.bind(Encoding::Iso2048, &binding) == Result::Invalid);
    hex(binding.identity.content_id); puts("");
    return 0;
}
int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "vfs")) return vfs_tests();
    if (argc != 3) return 64;
    CdFile file;
    if (!file.open(argv[2])) { fprintf(stderr, "cannot open seekable input\n"); return 2; }
    const auto encoding = !strcmp(argv[1], "iso") ? Encoding::Iso2048 : !strcmp(argv[1], "wave") ? Encoding::AudioPcmS16Le : Encoding::Opaque;
    Binding binding{};
    const auto result = file.bind(encoding, &binding);
    if (result != Result::Ok) { fprintf(stderr, "invalid or unsupported CD source (%u)\n", unsigned(result)); return 2; }
    Source source;
    CHECK(source.bind(binding.identity, binding.read, binding.context) == Result::Ok);
    const size_t sector = encoding == Encoding::Iso2048 ? 2048 : encoding == Encoding::AudioPcmS16Le ? 2352 : 1;
    uint8_t sample[2352], digest[32];
    printf("bytes=%llu offset=%llu encoding=%u id=", (unsigned long long)binding.identity.bytes, (unsigned long long)file.slice().offset, unsigned(encoding)); hex(binding.identity.content_id);
    for (unsigned n = 0; n < 2; ++n) {
        const uint64_t at = n ? binding.identity.bytes - sector : 0;
        CHECK((encoding == Encoding::Iso2048 ? source.read_sector(at / 2048, sample, sizeof sample) : source.read_at(at, sample, sector)) == Result::Ok);
        p6_sha256_hash(sample, sector, digest); printf(" %s=", n ? "last" : "first"); hex(digest);
    }
    puts("");
    return 0;
}
