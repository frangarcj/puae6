#include "../../adapters/libretro/cd_ccd.h"
#include "cd_read.h"
#include "sha256.h"
#include "libretro.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <memory>
using namespace p6_cd;
using p6_libretro::CdCcd;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line%d: %s\n", __LINE__, #x); return 70; } } while (0)
struct retro_vfs_file_handle { FILE *file; bool sub; };
static int fault;
static retro_vfs_file_handle *RETRO_CALLCONV vopen(const char *path, unsigned, unsigned)
{
    FILE *file = fopen(path, "rb"); return file ? new retro_vfs_file_handle{file, strstr(path, ".sub") != nullptr} : nullptr;
}
static int RETRO_CALLCONV vclose(retro_vfs_file_handle *f) { const int status = fclose(f->file); delete f; return status; }
static int64_t RETRO_CALLCONV vtell(retro_vfs_file_handle *f) { return ftell(f->file); }
static int64_t RETRO_CALLCONV vsize(retro_vfs_file_handle *f) { const auto pos = ftell(f->file); fseek(f->file, 0, SEEK_END); const auto size = ftell(f->file); fseek(f->file, pos, SEEK_SET); return size; }
static int64_t RETRO_CALLCONV vseek(retro_vfs_file_handle *f, int64_t at, int origin) { return fseek(f->file, long(at), origin); }
static int64_t RETRO_CALLCONV vread(retro_vfs_file_handle *f, void *out, uint64_t length)
{
    if ((fault == 1 || fault == 2) && f->sub) return fault == 1 ? -1 : 0;
    if (fault == 3 && !f->sub && ftell(f->file) >= 2352) return -1;
    return fread(out, 1, size_t(length < 3 ? length : 3), f->file);
}
static void digest(const void *data, size_t length)
{
    uint8_t hash[32]; p6_sha256_hash(data, length, hash);
    for (auto value : hash) printf("%02x", value);
}
static bool untouched(const uint8_t *out, size_t length) { for (size_t n = 0; n < length; ++n) if (out[n] != 0xa5) return false; return true; }
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    retro_vfs_interface api{};
    api.open = vopen; api.close = vclose; api.size = vsize; api.tell = vtell; api.seek = vseek; api.read = vread;
    const auto *vfs = getenv("P6_CCD_PROBE_VFS") ? &api : nullptr;
    auto ccd = std::make_unique<CdCcd>(); const auto status = ccd->open(argv[1], vfs);
    if (status != Result::Ok) { CHECK(!ccd->layout() && !ccd->metadata() && !ccd->bindings() && !ccd->sources()); printf("error %u\n", unsigned(status)); return 2; }
    CHECK(ccd->open(argv[1], vfs) == Result::Invalid);
    Disc disc; CHECK(disc.bind(*ccd->layout(), ccd->bindings(), 1) == Result::Ok);
    const auto &id = ccd->bindings()[0].identity;
    printf("source %llu ", (unsigned long long)id.bytes); for (auto value : id.content_id) printf("%02x", value); puts("");
    const auto &layout = disc.layout(); printf("disc %u %u ", layout.tracks, layout.leadout); for (auto value : layout.content_id) printf("%02x", value); puts("");
    printf("rawindex0 %d\n", ccd->metadata()->track[0].index0);
    uint8_t out[2464]; size_t got = 123;
    for (uint64_t at : {UINT64_C(0), UINT64_C(2350), UINT64_C(2440), id.bytes - 20}) {
        const size_t length = size_t(id.bytes - at < 2448 ? id.bytes - at : 2448);
        CHECK(ccd->bindings()[0].read(ccd->bindings()[0].context, at, out, length, &got) && got == length);
        printf("bytes %llu %zu ", (unsigned long long)at, length); digest(out, length); puts("");
    }
    memset(out, 0xa5, sizeof out);
    CHECK(ccd->bindings()[0].read(ccd->bindings()[0].context, id.bytes, nullptr, 0, &got) && !got);
    CHECK(!ccd->bindings()[0].read(ccd->bindings()[0].context, id.bytes, out, 1, &got) && !got && untouched(out, sizeof out));
    CHECK(!ccd->bindings()[0].read(ccd->bindings()[0].context, UINT64_MAX, out, 1, &got) && !got);
    CHECK(!ccd->bindings()[0].read(ccd->bindings()[0].context, 0, out, 2449, &got) && !got && untouched(out, sizeof out));
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const auto &t = layout.track[n];
        printf("track %u %u %u %llu %u\n", t.index0, t.index1, t.end, (unsigned long long)t.offset, unsigned(t.mode));
        for (uint32_t lba : {t.index0, t.index1, t.end - 1}) for (unsigned view : {2048u, 2352u, 2448u}) {
            memset(out, 0xa5, sizeof out); const auto result = canonical_read(disc, lba, view, out + 8, view);
            CHECK(untouched(out, 8) && untouched(out + 8 + view, sizeof out - 8 - view));
            printf("sector %u %u %u ", lba, view, unsigned(result));
            if (result == Result::Ok) digest(out + 8, view); else CHECK(untouched(out, sizeof out));
            puts("");
            if (view == 2448) { CHECK(read_subchannel(disc, lba, out, 96) == Result::Ok); printf("sub %u ", lba); digest(out, 96); puts(""); }
        }
    }
    if (vfs) for (fault = 1; fault <= 3; ++fault) {
        memset(out, 0xa5, sizeof out);
        CHECK(!ccd->bindings()[0].read(ccd->bindings()[0].context, 2440, out + 8, 2448, &got) && !got && untouched(out, sizeof out));
        CHECK(canonical_read(disc, 1, 2448, out + 8, 2448) == Result::ReadError && untouched(out, sizeof out));
        CHECK(read_subchannel(disc, 1, out, 96) == Result::ReadError && untouched(out, sizeof out));
    }
    fault = 0;
    if (argc == 3) {
        auto fresh = std::make_unique<CdCcd>(); CHECK(fresh->open(argv[2], vfs) == Result::Ok);
        const auto result = disc.rebind(*fresh->layout(), fresh->bindings(), 1);
        if (result == Result::Ok) ccd.reset(); else fresh.reset();
        CHECK(canonical_read(disc, 0, 2448, out, sizeof out) == Result::Ok);
        printf("rebind %u ", unsigned(result)); digest(out, 2448); puts("");
    }
}
