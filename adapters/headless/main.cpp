// Headless PUAE6 runner. It links the A500, A600 and A1200 profiles without any windowing, audio or plugin
// dependency, reports the identity of the build and what the native ABI implements, and runs a
// firmware image for a number of video units, printing what p6_run_frame delivered for each.
//   p6_headless [--identity]
//   p6_headless --run ROM [--machine a500|a500og|a500plus|a600|a1000|a1200|a2000og|a2000] [--chip KIB] [--slow KIB|none] [--fast KIB] [--fpu 0|68881|68882]
//                         [--cpu auto|68000|68010|68020]
//                         [--sound-sep 0..100] [--sound-interp none|anti|rh|crux] [--sound-filter emulated|off|on] [--sound-type auto|a500|a1200]
//                         [--floppy-speed 100|200|400|800|turbo] [--resolution lores|hires|superhires] [--line-mode single|double]
//                         [--disk ADF] [--hdf IMAGE]... [--export-hdf FILE] [--units N] [--ntsc] [--seed S] [--dump DIR [--dump-from N]] [--dump-audio DIR]
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
    printf("machines: A500 A500OG A500PLUS A600 A1000 A1200 A2000OG A2000\n");
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

// Paula's output options (T73, T73a): the spellings after --sound-sep, --sound-interp, --sound-filter and --sound-type and the p6_config number each one is (config/schema/options.json
// names them; tests/config compares these tables with it). Zero is the profile's own setting, so a run that names none leaves the fields at zero and is the run it always was.
struct Named {
    const char *spelling;
    uint8_t abi;
};
static const Named sound_separation_names[] = {{"0", 1}, {"10", 2}, {"20", 3}, {"30", 4}, {"40", 5}, {"50", 6}, {"60", 7}, {"70", 0}, {"80", 9}, {"90", 10}, {"100", 11}};
static const Named sound_interpolation_names[] = {{"none", 1}, {"anti", 0}, {"rh", 4}, {"crux", 5}};
static const Named sound_filter_names[] = {{"emulated", 0}, {"off", 1}, {"on", 3}};
static const Named sound_filter_type_names[] = {{"auto", 0}, {"a500", 1}, {"a1200", 2}};
// The floppy drive speed (T62), after --floppy-speed: the percentage of the real drive, or turbo (upstream's floppy_speed 0). Zero is the profile's (100).
static const Named floppy_speed_names[] = {{"100", 0}, {"200", 2}, {"400", 3}, {"800", 4}, {"turbo", 5}};
// The raster (T72), after --resolution and --line-mode: the engine's gfx_resolution and gfx_vresolution. Zero is the profile's (hires, double lines).
static const Named video_resolution_names[] = {{"lores", 1}, {"hires", 0}, {"superhires", 3}};
static const Named video_line_mode_names[] = {{"single", 1}, {"double", 0}};
// The CPU (T52), after --cpu: p6_config.cpu_model as 1 + (model - 68000) / 10; zero (auto) is the machine's own. The bridge refuses the combinations a machine cannot have.
static const Named cpu_model_names[] = {{"auto", 0}, {"68000", 1}, {"68010", 2}, {"68020", 3}};

template <size_t N>
static bool pick_named(const Named (&table)[N], const char *spelling, uint8_t *abi)
{
    for (const Named &entry : table)
        if (!strcmp(entry.spelling, spelling)) {
            *abi = entry.abi;
            return true;
        }
    return false;
}

// audio-NNN.bin: the interleaved little-endian int16 stereo samples the unit handed over, to compare the samples of one adapter with another's.
static bool dump_audio(const char *directory, const p6_frame &f)
{
    char path[1024];
    snprintf(path, sizeof path, "%s/audio-%03llu.bin", directory, (unsigned long long)f.unit_index);
    FILE *out = fopen(path, "wb");
    if (!out) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        return false;
    }
    for (uint64_t i = 0; i < f.audio_frames * 2; ++i) {
        const int16_t sample = static_cast<const int16_t *>(f.audio)[i];
        const uint8_t bytes[2] = {uint8_t(sample & 0xff), uint8_t((sample >> 8) & 0xff)};
        fwrite(bytes, 1, 2, out);
    }
    return fclose(out) == 0;
}

static int fail(const char *what, p6_result result, const p6_error &error)
{
    fprintf(stderr, "%s failed (%d): %s\n", what, int(result), error.message);
    return 1;
}

static int run(int argc, char **argv)
{
    const char *rom_path = nullptr, *dump = nullptr, *dump_audio_dir = nullptr, *disk_path = nullptr;
    const char *export_hdf = nullptr;            // T29d: after the last unit, the first hard file as it is then (the base with the overlay of the guest's writes over it)
    std::vector<const char *> hdf_paths;         // T29d: hard files on the UAE controller, after the disk in the manifest
    uint32_t units = 1, standard = P6_VIDEO_PAL, machine = P6_MACHINE_A500;
    uint64_t seed = 1, dump_from = 1;
    uint32_t chip_kib = 0, slow_kib = 0, fast_kib = 0, fpu_model = 0;       // 0: the machine's own memory (T49); slow "none" is P6_MEMORY_NONE
    uint8_t sound_separation = 0, sound_interpolation = 0, sound_filter = 0, sound_filter_type = 0;     // 0: the profile's own setting (T73)
    uint8_t floppy_speed = 0;                                                                           // 0: the real drive (T62)
    uint8_t video_resolution = 0, video_line_mode = 0;                                                  // 0: the profile's raster, hires and double lines (T72)
    uint8_t cpu_model = 0;                                                                              // 0: the machine's own CPU (T52)
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--units") && i + 1 < argc) units = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--dump") && i + 1 < argc) dump = argv[++i];
        else if (!strcmp(argv[i], "--dump-audio") && i + 1 < argc) dump_audio_dir = argv[++i];
        else if (!strcmp(argv[i], "--dump-from") && i + 1 < argc) dump_from = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--disk") && i + 1 < argc) disk_path = argv[++i];
        else if (!strcmp(argv[i], "--export-hdf") && i + 1 < argc) export_hdf = argv[++i];
        else if (!strcmp(argv[i], "--hdf") && i + 1 < argc && hdf_paths.size() < P6_MAX_HDF) hdf_paths.push_back(argv[++i]);
        else if (!strcmp(argv[i], "--machine") && i + 1 < argc) {
            const char *name = argv[++i];
            if (!strcmp(name, "a500")) machine = P6_MACHINE_A500;
            else if (!strcmp(name, "a500og")) machine = P6_MACHINE_A500OG;
            else if (!strcmp(name, "a500plus")) machine = P6_MACHINE_A500PLUS;
            else if (!strcmp(name, "a600")) machine = P6_MACHINE_A600;
            else if (!strcmp(name, "a1000")) machine = P6_MACHINE_A1000;
            else if (!strcmp(name, "a1200")) machine = P6_MACHINE_A1200;
            else if (!strcmp(name, "a2000og")) machine = P6_MACHINE_A2000OG;
            else if (!strcmp(name, "a2000")) machine = P6_MACHINE_A2000;
            else return -1;
        }
        else if (!strcmp(argv[i], "--chip") && i + 1 < argc) chip_kib = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--slow") && i + 1 < argc) { const char *v = argv[++i]; slow_kib = !strcmp(v, "none") ? P6_MEMORY_NONE : uint32_t(strtoul(v, nullptr, 10)); }
        else if (!strcmp(argv[i], "--fast") && i + 1 < argc) fast_kib = uint32_t(strtoul(argv[++i], nullptr, 10));
        else if (!strcmp(argv[i], "--fpu") && i + 1 < argc) fpu_model = uint32_t(strtoul(argv[++i], nullptr, 10));       // T54: 0 (none), 68881 or 68882; the bridge refuses the rest
        else if (!strcmp(argv[i], "--sound-sep") && i + 1 < argc) { if (!pick_named(sound_separation_names, argv[++i], &sound_separation)) return -1; }
        else if (!strcmp(argv[i], "--sound-interp") && i + 1 < argc) { if (!pick_named(sound_interpolation_names, argv[++i], &sound_interpolation)) return -1; }
        else if (!strcmp(argv[i], "--sound-filter") && i + 1 < argc) { if (!pick_named(sound_filter_names, argv[++i], &sound_filter)) return -1; }
        else if (!strcmp(argv[i], "--sound-type") && i + 1 < argc) { if (!pick_named(sound_filter_type_names, argv[++i], &sound_filter_type)) return -1; }
        else if (!strcmp(argv[i], "--floppy-speed") && i + 1 < argc) { if (!pick_named(floppy_speed_names, argv[++i], &floppy_speed)) return -1; }
        else if (!strcmp(argv[i], "--resolution") && i + 1 < argc) { if (!pick_named(video_resolution_names, argv[++i], &video_resolution)) return -1; }
        else if (!strcmp(argv[i], "--line-mode") && i + 1 < argc) { if (!pick_named(video_line_mode_names, argv[++i], &video_line_mode)) return -1; }
        else if (!strcmp(argv[i], "--cpu") && i + 1 < argc) { if (!pick_named(cpu_model_names, argv[++i], &cpu_model)) return -1; }
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
    config.flags = 0;        // floppy writes are for the adapters that can export a disk
    config.random_seed = seed;
    config.rtc_epoch_seconds = 946684800;   // 2000-01-01T00:00:00Z, the contract's default
    config.chip_kib = chip_kib;
    config.slow_kib = slow_kib;
    config.fast_kib = fast_kib;
    config.fpu_model = fpu_model;
    config.sound_separation = sound_separation;
    config.sound_interpolation = sound_interpolation;
    config.sound_filter = sound_filter;
    config.sound_filter_type = sound_filter_type;
    config.floppy_speed = floppy_speed;
    config.video_resolution = video_resolution;
    config.video_line_mode = video_line_mode;
    config.cpu_model = cpu_model;
    p6_error error = make_error();
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) return fail("p6_init", result, error);
    std::vector<p6_media> media;
    if (disk_path) {
        p6_media adf = {};
        adf.type = P6_MEDIA_ADF;
        adf.image = p6_buffer{disk.data(), disk.size()};
        p6_sha256_hash(disk.data(), disk.size(), adf.content_id);   // the media identity is the SHA-256 of the image
        media.push_back(adf);
    }
    std::vector<std::vector<uint8_t>> hard_files(hdf_paths.size());
    for (size_t k = 0; k < hdf_paths.size(); ++k) {
        if (!read_file(hdf_paths[k], &hard_files[k])) return 1;
        p6_media hdf = {};
        hdf.type = P6_MEDIA_HDF;
        hdf.image = p6_buffer{hard_files[k].data(), hard_files[k].size()};
        p6_sha256_hash(hard_files[k].data(), hard_files[k].size(), hdf.content_id);
        media.push_back(hdf);
    }
    result = p6_load(p6_buffer{rom.data(), rom.size()}, media.empty() ? nullptr : media.data(), uint32_t(media.size()), &error);
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
        if (dump_audio_dir && !dump_audio(dump_audio_dir, frame)) status = 1;
    }
    if (!status && export_hdf) {
        if (hard_files.empty()) {
            fprintf(stderr, "--export-hdf needs a --hdf\n");
            status = 1;
        } else {
            std::vector<uint8_t> image(hard_files[0].size());
            uint64_t written = 0;
            result = p6_media_export(disk_path ? 1 : 0, image.data(), image.size(), &written, &error);
            if (result != P6_OK) {
                status = fail("p6_media_export", result, error);
            } else if (FILE *out = fopen(export_hdf, "wb")) {
                fwrite(image.data(), 1, size_t(written), out);
                if (fclose(out)) status = 1;
            } else {
                perror(export_hdf);
                status = 1;
            }
        }
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
    fprintf(stderr, "usage: %s [--identity]\n       %s --run ROM [--machine a500|a500og|a500plus|a600|a1000|a1200|a2000og|a2000] [--chip KIB] [--slow KIB|none] [--fast KIB] [--fpu 0|68881|68882]\n"
                    "                         [--cpu auto|68000|68010|68020]\n                         [--sound-sep 0..100] [--sound-interp none|anti|rh|crux] [--sound-filter emulated|off|on] [--sound-type auto|a500|a1200]\n                         [--floppy-speed 100|200|400|800|turbo]\n                         [--resolution lores|hires|superhires] [--line-mode single|double]\n"
                    "                         [--disk ADF] [--hdf IMAGE]... [--export-hdf FILE] [--units N] [--ntsc] [--seed S] [--dump DIR [--dump-from N]] [--dump-audio DIR]\n",
            argv[0], argv[0]);
    return 64;
}
