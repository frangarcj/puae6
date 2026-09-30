// LIFE01 harness: many init / load / run / unload / shutdown cycles in one process, with different configurations, media,
// resets, failed loads and repeated shutdowns, while the process heap is watched for growth.
//   p6_cycles_probe ROM CYCLES
// Prints the bytes in use after each cycle at the end; the test compares the early and the late plateau.
#include "p6.h"
#include "blob_io.h"
#include "sha256.h"

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#if defined(__APPLE__)
#include <malloc/malloc.h>
#endif

namespace {

p6_error fresh_error()
{
    p6_error e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    return e;
}

std::vector<uint8_t> read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    std::vector<uint8_t> data;
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data.insert(data.end(), buffer, buffer + n);
    fclose(f);
    return data;
}

std::vector<uint8_t> make_adf(uint32_t label)
{
    std::vector<uint8_t> adf(1760u * 512u);
    uint32_t x = 0x9e3779b9u ^ (label * 2654435761u);
    for (uint8_t &b : adf) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; b = uint8_t(x >> 11); }
    adf[0] = 'D'; adf[1] = 'O'; adf[2] = 'S'; adf[3] = 0;
    return adf;
}

p6_media medium_of(const std::vector<uint8_t> &adf)
{
    p6_media m{};
    m.type = P6_MEDIA_ADF;
    p6_sha256_hash(adf.data(), adf.size(), m.content_id);
    m.image = p6_buffer{adf.data(), adf.size()};
    return m;
}

size_t heap_in_use()
{
#if defined(__APPLE__)
    malloc_statistics_t stats;
    malloc_zone_statistics(nullptr, &stats);
    return stats.size_in_use;
#else
    return 0;
#endif
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc != 3) { fprintf(stderr, "usage: %s ROM CYCLES\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const int cycles = atoi(argv[2]);
    const std::vector<uint8_t> disks[2] = {make_adf(1), make_adf(2)};
    const p6_media media[2] = {medium_of(disks[0]), medium_of(disks[1])};
    std::vector<uint8_t> broken = rom;
    broken[0xD2] ^= 0x10;                                   // right shape, wrong checksum

    std::vector<size_t> in_use;
    for (int i = 0; i < cycles; ++i) {
        p6_error e = fresh_error();
        p6_config c{};
        c.abi_version = P6_ABI_VERSION;
        c.struct_size = sizeof c;
        c.machine = P6_MACHINE_A500;
        c.video_standard = (i & 1) ? P6_VIDEO_NTSC : P6_VIDEO_PAL;
        c.audio_rate_hz = (i % 3) ? 48000 : 44100;
        c.random_seed = uint64_t(i) * 7919 + 1;
        c.rtc_epoch_seconds = 946684800 + i * 86400;
        assert(p6_init(&c, &e) == P6_OK);
        assert(p6_init(&c, &e) == P6_ERROR_BAD_STATE);                                  // a second init is refused
        if (i % 7 == 3) {                                                               // a load that fails halfway
            assert(p6_load(p6_buffer{broken.data(), broken.size()}, media, 2, &e) == P6_ERROR_WRONG_MEDIA);
            assert(p6_blob_exists("p6-firmware.rom") != P6_BLOB_OK);
        }
        const uint32_t count = (i % 4 == 1) ? 2 : (i % 4 == 2) ? 1 : 0;
        assert(p6_load(p6_buffer{rom.data(), rom.size()}, count ? media : nullptr, count, &e) == P6_OK);
        assert(p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &e) == P6_ERROR_BAD_STATE);   // one image per instance
        p6_input input{};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        p6_frame frame{};
        for (int unit = 0; unit < 3; ++unit) {
            if (count && unit == 0) {
                p6_media_event ev{};
                ev.abi_version = P6_ABI_VERSION; ev.struct_size = sizeof ev; ev.type = P6_MEDIA_EVENT_MOUNT; ev.media_index = 0; ev.sequence = 1;
                assert(p6_media_event_enqueue(&ev, &e) == P6_OK);
            }
            if (i % 5 == 4 && unit == 2) assert(p6_reset(&e) == P6_OK);
            frame = p6_frame{};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            assert(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &e) == P6_OK);
        }
        assert(p6_unload(&e) == P6_OK && p6_unload(&e) == P6_OK);
        assert(p6_run_frame(&input, 0, &frame, &e) == P6_ERROR_BAD_STATE);                 // nothing is loaded any more
        for (uint32_t k = 0; k < count; ++k) {
            char id[71];
            snprintf(id, sizeof id, "media-");
            for (int b = 0; b < 32; ++b) snprintf(id + 6 + 2 * b, 3, "%02x", media[k].content_id[b]);
            assert(p6_blob_exists(id) != P6_BLOB_OK);                                       // nothing registered survives unload
        }
        assert(p6_shutdown(&e) == P6_OK && p6_shutdown(&e) == P6_OK);                       // twice is safe
        in_use.push_back(heap_in_use());
    }
    for (size_t k = 0; k < in_use.size(); ++k) printf("cycle %zu in_use %zu\n", k, in_use[k]);
    puts("cycles ok");
    return 0;
}
