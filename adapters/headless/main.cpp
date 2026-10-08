// Headless PUAE6 runner. It links the A500, A600 and A1200 profiles without any windowing, audio or plugin
// dependency, reports the identity of the build and what the native ABI implements, and runs a
// firmware image for a number of video units, printing what p6_run_frame delivered for each.
//   p6_headless [--identity]
//   p6_headless --run ROM [--machine a500|a600|a1200] [--disk ADF] [--units N] [--ntsc] [--seed S] [--dump DIR [--dump-from N]]
// Only micro-ROM programs have been run so far: no Kickstart compatibility is claimed.
#include "p6.h"
#include "sha256.h"
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#ifndef P6_PROFILE
#error "P6_PROFILE is provided by the CMake target"
#endif

static p6_error make_error()
{
    p6_error error = {};
    error.abi_version = P6_ABI_VERSION;
    error.struct_size = sizeof error;
    return error;
}

static int identity(void)
{
    printf("core: puae6-headless\n");
    printf("profile: %s\n", P6_PROFILE);
    printf("machines: A500 A600 A1200\n");
    printf("abi: %u\n", (unsigned)p6_abi_version());
    printf("upstream: WinUAE %s\n", P6_UPSTREAM_COMMIT);
    printf("patches: %s (%s)\n", P6_PATCH_COUNT, P6_PATCH_SERIES_SHA256);
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_error error = make_error();
    if (p6_query_caps(&caps, &error) != P6_OK) {
        fprintf(stderr, "p6_query_caps failed: %s\n", error.message);
        return 2;
    }
    printf("caps: 0x%llx\n", (unsigned long long)caps.flags);
    printf("video: up to %ux%u\n", caps.max_video_width, caps.max_video_height);
    printf("boot: micro-ROM programs only; Kickstart compatibility is not proven\n");
    return 0;
}

static bool read_file(const char *path, std::vector<uint8_t> *data)
{
    FILE *file = fopen(path, "rb");
    if (!file) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, file)) > 0;) data->insert(data->end(), buffer, buffer + n);
    fclose(file);
    return true;
}

// FNV-1a over the active pixels, row by row, without the padding of the stride.
static uint64_t picture_hash(const p6_frame &f)
{
    uint64_t hash = UINT64_C(1469598103934665603);
    if (!f.video) return 0;
    for (uint32_t y = f.active.y; y < f.active.y + f.active.height; ++y) {
        const uint8_t *row = static_cast<const uint8_t *>(f.video) + size_t(y) * f.video_stride_bytes + size_t(f.active.x) * 4;
        for (size_t i = 0; i < size_t(f.active.width) * 4; ++i) {
            hash ^= row[i];
            hash *= UINT64_C(1099511628211);
        }
    }
    return hash;
}

// frame-NNN.bin: four little-endian uint32 (active width, height, field parity, flags), then the active rows.
static bool dump_frame(const char *directory, const p6_frame &f)
{
    char path[1024];
    snprintf(path, sizeof path, "%s/frame-%03llu.bin", directory, (unsigned long long)f.unit_index);
    FILE *out = fopen(path, "wb");
    if (!out) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    const uint32_t header[4] = {f.active.width, f.active.height, f.field_parity, f.flags};
    fwrite(header, sizeof header, 1, out);
    for (uint32_t y = f.active.y; y < f.active.y + f.active.height; ++y)
        fwrite(static_cast<const uint8_t *>(f.video) + size_t(y) * f.video_stride_bytes + size_t(f.active.x) * 4, 4,
               f.active.width, out);
    return fclose(out) == 0;
}

static int fail(const char *what, p6_result result, const p6_error &error)
{
    fprintf(stderr, "%s failed (%d): %s\n", what, int(result), error.message);
    return 1;
}

static int run(int argc, char **argv)
{
    const char *rom_path = nullptr, *dump = nullptr, *disk_path = nullptr;
    uint32_t units = 1, standard = P6_VIDEO_PAL, machine = P6_MACHINE_A500;
    uint64_t seed = 1, dump_from = 1;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--units") && i + 1 < argc) units = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--dump") && i + 1 < argc) dump = argv[++i];
        else if (!strcmp(argv[i], "--dump-from") && i + 1 < argc) dump_from = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--disk") && i + 1 < argc) disk_path = argv[++i];
        else if (!strcmp(argv[i], "--machine") && i + 1 < argc) {
            const char *name = argv[++i];
            if (!strcmp(name, "a500")) machine = P6_MACHINE_A500;
            else if (!strcmp(name, "a600")) machine = P6_MACHINE_A600;
            else if (!strcmp(name, "a1200")) machine = P6_MACHINE_A1200;
            else return -1;
        }
        else if (!strcmp(argv[i], "--ntsc")) standard = P6_VIDEO_NTSC;
        else if (!rom_path && argv[i][0] != '-') rom_path = argv[i];
        else return -1;
    }
    if (!rom_path || units == 0) return -1;

    std::vector<uint8_t> rom;
    if (!read_file(rom_path, &rom)) return 1;
    std::vector<uint8_t> disk;
    if (disk_path && !read_file(disk_path, &disk)) return 1;

    p6_config config = {};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = machine;
    config.video_standard = standard;
    config.audio_rate_hz = 48000;
    config.random_seed = seed;
    config.rtc_epoch_seconds = 946684800;   // 2000-01-01T00:00:00Z, the contract's default
    p6_error error = make_error();
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) return fail("p6_init", result, error);
    p6_media media = {};
    media.type = P6_MEDIA_ADF;
    media.image = p6_buffer{disk.data(), disk.size()};
    p6_sha256_hash(disk.data(), disk.size(), media.content_id);   // the media identity is the SHA-256 of the image
    result = p6_load(p6_buffer{rom.data(), rom.size()}, disk_path ? &media : nullptr, disk_path ? 1 : 0, &error);
    if (result != P6_OK) {
        const int code = fail("p6_load", result, error);
        p6_shutdown(nullptr);
        return code;
    }

    if (disk_path) {   // p6_load only registers the disk: DF0 holds it from the first unit through a mount queued before it
        p6_media_event mount = {};
        mount.abi_version = P6_ABI_VERSION;
        mount.struct_size = sizeof mount;
        mount.type = P6_MEDIA_EVENT_MOUNT;
        mount.drive = 0;
        mount.media_index = 0;
        mount.sequence = 1;
        result = p6_media_event_enqueue(&mount, &error);
        if (result != P6_OK) {
            const int code = fail("p6_media_event_enqueue", result, error);
            p6_unload(nullptr);
            p6_shutdown(nullptr);
            return code;
        }
    }

    p6_input input = {};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    p6_frame frame = {};
    int status = 0;
    for (uint32_t unit = 1; unit <= units && !status; ++unit) {
        frame = {};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
        if (result != P6_OK) {
            status = fail("p6_run_frame", result, error);
            break;
        }
        printf("unit %" PRIu64 " ticks=%" PRIu64 " video=%d active=%ux%u parity=%u aspect=%u/%u cadence=%u/%u hash=%016" PRIx64
               " audio=%" PRIu64 "\n",
               frame.unit_index, frame.master_ticks, frame.video != nullptr, frame.active.width, frame.active.height,
               frame.field_parity, frame.aspect_num, frame.aspect_den, frame.cadence_num, frame.cadence_den, picture_hash(frame),
               frame.audio_frames);
        if (dump && frame.video && frame.unit_index >= dump_from && !dump_frame(dump, frame)) status = 1;
    }
    p6_unload(nullptr);
    p6_shutdown(nullptr);
    if (!status) printf("done units=%u\n", units);
    return status;
}

int main(int argc, char **argv)
{
    if (argc == 1 || (argc == 2 && !strcmp(argv[1], "--identity")))
        return identity();
    if (argc >= 3 && !strcmp(argv[1], "--run")) {
        const int code = run(argc, argv);
        if (code >= 0) return code;
    }
    fprintf(stderr, "usage: %s [--identity]\n       %s --run ROM [--machine a500|a600|a1200] [--disk ADF] [--units N] [--ntsc] [--seed S] [--dump DIR [--dump-from N]]\n",
            argv[0], argv[0]);
    return 64;
}
