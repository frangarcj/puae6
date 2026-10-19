#include "../../adapters/libretro/cd_cue.h"
#include "sha256.h"
#include "libretro.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <memory>
using namespace p6_cd;
using p6_libretro::CdCue;
struct retro_vfs_file_handle { FILE *file; };
static retro_vfs_file_handle *RETRO_CALLCONV vopen(const char *path, unsigned, unsigned)
{
    FILE *file = fopen(path, "rb"); return file ? new retro_vfs_file_handle{file} : nullptr;
}
static int RETRO_CALLCONV vclose(retro_vfs_file_handle *f) { const int result = fclose(f->file); delete f; return result; }
static int64_t RETRO_CALLCONV vtell(retro_vfs_file_handle *f) { return ftell(f->file); }
static int64_t RETRO_CALLCONV vsize(retro_vfs_file_handle *f) { const auto pos = ftell(f->file); fseek(f->file, 0, SEEK_END); const auto size = ftell(f->file); fseek(f->file, pos, SEEK_SET); return size; }
static int64_t RETRO_CALLCONV vseek(retro_vfs_file_handle *f, int64_t at, int origin) { return fseek(f->file, long(at), origin); }
static int64_t RETRO_CALLCONV vread(retro_vfs_file_handle *f, void *out, uint64_t length) { return fread(out, 1, size_t(length < 3 ? length : 3), f->file); }
static void hex(const uint8_t *value) { for (unsigned n = 0; n < 32; ++n) printf("%02x", value[n]); }
int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) return 64;
    retro_vfs_interface api{};
    api.open = vopen; api.close = vclose; api.size = vsize; api.tell = vtell; api.seek = vseek; api.read = vread;
    const auto *vfs = getenv("P6_CUE_PROBE_VFS") ? &api : nullptr;
    auto cue = std::make_unique<CdCue>();
    const auto result = cue->open(argv[1], vfs);
    if (result != Result::Ok) {
        if (cue->layout() || cue->sources() || cue->bindings()) return 70;
        fprintf(stderr, "%s (%u)\n", result == Result::Unsupported ? "Unsupported CUE stride/type" : "Invalid CUE/source", unsigned(result)); return 2;
    }
    Disc disc;
    if (disc.bind(*cue->layout(), cue->bindings(), cue->sources()) != Result::Ok) return 71;
    const auto &layout = disc.layout();
    printf("disc %u %u %u ", layout.tracks, layout.leadout, cue->sources()); hex(layout.content_id); puts("");
    for (uint32_t n = 0; n < cue->sources(); ++n) {
        const auto &id = cue->bindings()[n].identity;
        printf("source %llu %u ", (unsigned long long)id.bytes, id.sector_bytes); hex(id.content_id); puts("");
    }
    for (uint32_t n = 0; n < layout.tracks; ++n) {
        const auto &t = layout.track[n];
        printf("track %u %u %u %u %u %llu %u %u %u %u %u %u\n", t.index0, t.index1, t.end, t.source, t.stride,
               (unsigned long long)t.offset, unsigned(t.mode), t.adr, t.control, t.stored_pregap, t.synthetic_pregap, t.postgap);
        const unsigned view = t.mode == Mode::Mode1_2048 ? 2048 : 2352;
        for (uint32_t lba : {t.index0, t.stored_pregap ? t.index0 + t.synthetic_pregap : t.index1, t.index1, t.end - t.postgap - 1, t.end - 1}) {
            uint8_t data[2448], hash[32];
            const auto status = disc.read_sector(lba, view, data, sizeof data);
            printf("sector %u %u %u ", lba, view, unsigned(status));
            if (status == Result::Ok) { p6_sha256_hash(data, view, hash); hex(hash); }
            puts("");
        }
    }
    if (argc == 3) {
        auto fresh = std::make_unique<CdCue>();
        if (fresh->open(argv[2], vfs) != Result::Ok) return 72;
        const auto status = disc.rebind(*fresh->layout(), fresh->bindings(), fresh->sources());
        if (status == Result::Ok) cue.reset(); else fresh.reset();
        uint8_t data[2352], hash[32];
        if (disc.read_sector(0, 2352, data, sizeof data) != Result::Ok) return 73;
        p6_sha256_hash(data, sizeof data, hash); printf("rebind %u ", unsigned(status)); hex(hash); puts("");
    }
}
