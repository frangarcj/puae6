// T33: the native benchmark probe. It drives the public ABI the way p6_headless does (p6_init, p6_load, p6_run_frame, p6_state_save/p6_state_load) and measures, with the host's monotonic
// clock read OUTSIDE the engine (the clock never feeds the emulation: the same ROM, seed and input give the same machine whatever the timings are), what each unit and each state
// operation costs. tools/bench.py builds this target in a Release tree and runs it; the program prints ONE JSON object with the raw samples, never a verdict.
//
//   p6_bench_probe ROM [--machine NAME] [--ntsc] [--disk ADF] [--seed S] [--render 0..3] [--warmup W] [--units N] [--state-rounds R] [--alloc-units A]
//
// Phases, in this order, in one process (so the numbers of one run share the warmth of its caches):
//   warmup    W units, not recorded except for their median and first sample (the first units are slower: caches, page faults);
//   units     N units, each timed twice: wall clock (steady clock) and the CPU time of the thread (so that a descheduled sample shows as wall >> cpu);
//   state     R rounds of p6_state_save, one unit, p6_state_load (back to the saved point) and the first unit after the load, on the machine as it is after the units;
//   alloc     A more units with the allocator's logger on, counting malloc/free calls and bytes per unit (the logger slows the units: they are counted, not timed).
// Memory: the resident size and the physical footprint before and after the timed units, the peak resident size of the process and the bytes the malloc zones hold.
// Not measured here: dirty pages (a native process has no dirty tracking; the state size is the stand-in) and the libretro/Waterbox layers (tools/bench.py runs those separately).
#include "p6.h"
#include "sha256.h"
#include <algorithm>
#include <chrono>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <vector>
#include <sys/resource.h>
#ifdef __APPLE__
#include <mach/mach.h>
#include <malloc/malloc.h>
extern "C" {
typedef void(malloc_logger_t)(uint32_t type, uintptr_t arg1, uintptr_t arg2, uintptr_t arg3, uintptr_t result, uint32_t num_hot_frames_to_skip);
extern malloc_logger_t *malloc_logger;
}
#endif

namespace {

p6_error make_error()
{
    p6_error error = {};
    error.abi_version = P6_ABI_VERSION;
    error.struct_size = sizeof error;
    return error;
}

bool read_file(const char *path, std::vector<uint8_t> *data)
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

uint64_t wall_ns()
{
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}

uint64_t thread_cpu_ns()
{
    timespec t;
    return clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t) == 0 ? uint64_t(t.tv_sec) * 1000000000u + uint64_t(t.tv_nsec) : 0;
}

struct Memory {
    uint64_t resident = 0, footprint = 0, peak_resident = 0, malloc_in_use = 0, malloc_allocated = 0;
};

Memory memory_now()
{
    Memory m;
    rusage usage = {};
    getrusage(RUSAGE_SELF, &usage);
#ifdef __APPLE__
    m.peak_resident = uint64_t(usage.ru_maxrss);       // bytes on macOS
    task_vm_info_data_t info = {};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &count) == KERN_SUCCESS) {
        m.resident = info.resident_size;
        m.footprint = info.phys_footprint;
    }
    malloc_statistics_t stats = {};
    malloc_zone_statistics(nullptr, &stats);
    m.malloc_in_use = stats.size_in_use;
    m.malloc_allocated = stats.size_allocated;
#else
    m.peak_resident = uint64_t(usage.ru_maxrss) * 1024;      // kilobytes on Linux
#endif
    return m;
}

#ifdef __APPLE__
// The allocator's logger: set while the alloc phase runs, it sees every malloc, calloc, realloc and free of the process (the engine's included).
struct AllocCount {
    uint64_t allocs = 0, frees = 0, bytes = 0;
} counted;
void count_allocation(uint32_t type, uintptr_t, uintptr_t arg2, uintptr_t arg3, uintptr_t, uint32_t)
{
    constexpr uint32_t kAlloc = 2, kDealloc = 4;
    if (type & kAlloc) {
        ++counted.allocs;
        counted.bytes += (type & kDealloc) ? arg3 : arg2;        // a realloc (alloc and dealloc together) names the new size in arg3
    }
    if (type & kDealloc) ++counted.frees;
}
#endif

struct Named {
    const char *name;
    uint32_t id;
};
const Named machines[] = {{"a500", P6_MACHINE_A500}, {"a600", P6_MACHINE_A600}, {"a1200", P6_MACHINE_A1200}, {"a500og", P6_MACHINE_A500OG}, {"a500plus", P6_MACHINE_A500PLUS},
                          {"a1000", P6_MACHINE_A1000}, {"a2000og", P6_MACHINE_A2000OG}, {"a2000", P6_MACHINE_A2000}};

void print_array(const char *key, const std::vector<uint64_t> &values, bool last = false)
{
    printf("\"%s\":[", key);
    for (size_t i = 0; i < values.size(); ++i) printf("%s%" PRIu64, i ? "," : "", values[i]);
    printf("]%s", last ? "" : ",");
}

uint64_t median_of(std::vector<uint64_t> values)
{
    if (values.empty()) return 0;
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

int fail(const char *what, p6_result result, const p6_error &error)
{
    fprintf(stderr, "%s failed (%d): %s\n", what, int(result), error.message);
    return 1;
}

}   // namespace

int main(int argc, char **argv)
{
    const char *rom_path = nullptr, *disk_path = nullptr;
    uint32_t machine = P6_MACHINE_A500, standard = P6_VIDEO_PAL, render = P6_RENDER_VIDEO | P6_RENDER_AUDIO;
    uint64_t seed = 1, warmup = 300, units = 1000, state_rounds = 20, alloc_units = 200;
    for (int i = 1; i < argc; ++i) {
        auto next = [&]() -> const char * { return i + 1 < argc ? argv[++i] : ""; };
        if (!strcmp(argv[i], "--machine")) {
            const char *name = next();
            bool found = false;
            for (const Named &m : machines)
                if (!strcmp(m.name, name)) machine = m.id, found = true;
            if (!found) {
                fprintf(stderr, "unknown machine %s\n", name);
                return 64;
            }
        } else if (!strcmp(argv[i], "--ntsc")) standard = P6_VIDEO_NTSC;
        else if (!strcmp(argv[i], "--disk")) disk_path = next();
        else if (!strcmp(argv[i], "--seed")) seed = strtoull(next(), nullptr, 10);
        else if (!strcmp(argv[i], "--render")) render = uint32_t(strtoul(next(), nullptr, 10));
        else if (!strcmp(argv[i], "--warmup")) warmup = strtoull(next(), nullptr, 10);
        else if (!strcmp(argv[i], "--units")) units = strtoull(next(), nullptr, 10);
        else if (!strcmp(argv[i], "--state-rounds")) state_rounds = strtoull(next(), nullptr, 10);
        else if (!strcmp(argv[i], "--alloc-units")) alloc_units = strtoull(next(), nullptr, 10);
        else if (argv[i][0] != '-' && !rom_path) rom_path = argv[i];
        else {
            fprintf(stderr, "usage: %s ROM [--machine NAME] [--ntsc] [--disk ADF] [--seed S] [--render 0..3] [--warmup W] [--units N] [--state-rounds R] [--alloc-units A]\n", argv[0]);
            return 64;
        }
    }
    if (!rom_path || !units) return 64;
    std::vector<uint8_t> rom, disk;
    if (!read_file(rom_path, &rom) || (disk_path && !read_file(disk_path, &disk))) return 1;

    p6_config config = {};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = machine;
    config.video_standard = standard;
    config.audio_rate_hz = 48000;
    config.random_seed = seed;
    config.rtc_epoch_seconds = 946684800;
    p6_error error = make_error();
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) return fail("p6_init", result, error);
    p6_media adf = {};
    if (disk_path) {
        adf.type = P6_MEDIA_ADF;
        adf.image = p6_buffer{disk.data(), disk.size()};
        p6_sha256_hash(disk.data(), disk.size(), adf.content_id);
    }
    result = p6_load(p6_buffer{rom.data(), rom.size()}, disk_path ? &adf : nullptr, disk_path ? 1 : 0, &error);
    if (result != P6_OK) {
        const int code = fail("p6_load", result, error);
        p6_shutdown(nullptr);
        return code;
    }
    if (disk_path) {
        p6_media_event mount = {};
        mount.abi_version = P6_ABI_VERSION;
        mount.struct_size = sizeof mount;
        mount.type = P6_MEDIA_EVENT_MOUNT;
        mount.drive = 0;
        mount.media_index = 0;
        mount.sequence = 1;
        result = p6_media_event_enqueue(&mount, &error);
        if (result != P6_OK) return fail("p6_media_event_enqueue", result, error);
    }
    p6_caps caps = {};
    caps.abi_version = P6_ABI_VERSION;
    caps.struct_size = sizeof caps;
    p6_query_caps(&caps, &error);

    p6_input input = {};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    p6_frame frame = {};
    uint64_t last_unit = 0, master_ticks = 0, audio_frames = 0, video_units = 0;
    uint32_t active_w = 0, active_h = 0, cadence_num = 0, cadence_den = 0;
    auto run_unit = [&](uint64_t *wall, uint64_t *cpu) -> bool {
        frame = {};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        const uint64_t c0 = cpu ? thread_cpu_ns() : 0, w0 = wall ? wall_ns() : 0;
        const p6_result r = p6_run_frame(&input, render, &frame, &error);
        if (wall) *wall = wall_ns() - w0;
        if (cpu) *cpu = thread_cpu_ns() - c0;
        if (r != P6_OK) {
            fail("p6_run_frame", r, error);
            return false;
        }
        last_unit = frame.unit_index;
        master_ticks = frame.master_ticks;
        audio_frames += frame.audio_frames;
        if (frame.video) ++video_units;
        if (frame.active.width) {
            active_w = frame.active.width;
            active_h = frame.active.height;
        }
        if (frame.cadence_num) {
            cadence_num = frame.cadence_num;
            cadence_den = frame.cadence_den;
        }
        return true;
    };

    std::vector<uint64_t> warm;
    warm.reserve(size_t(warmup));
    for (uint64_t u = 0; u < warmup; ++u) {
        uint64_t w = 0;
        if (!run_unit(&w, nullptr)) return 1;
        warm.push_back(w);
    }
    const Memory before = memory_now();
    std::vector<uint64_t> wall, cpu;
    wall.reserve(size_t(units));
    cpu.reserve(size_t(units));
    rusage ru0 = {}, ru1 = {};
    getrusage(RUSAGE_SELF, &ru0);
    const uint64_t window0 = wall_ns();
    for (uint64_t u = 0; u < units; ++u) {
        uint64_t w = 0, c = 0;
        if (!run_unit(&w, &c)) return 1;
        wall.push_back(w);
        cpu.push_back(c);
    }
    const uint64_t window = wall_ns() - window0;
    getrusage(RUSAGE_SELF, &ru1);
    const Memory after = memory_now();
    const uint64_t timed_audio = audio_frames;
    const uint64_t timed_video_units = video_units;
    const uint64_t end_unit = last_unit, end_ticks = master_ticks;      // where the timed window ended: the state phase runs a few more units

    // State: bytes, save, load and the first unit after a load.
    std::vector<uint64_t> save_ns, load_ns, first_after_load_ns;
    uint64_t state_bytes = 0, state_bound = 0;
    const int state_supported = (caps.flags & P6_CAP_STATE) != 0;
    if (state_supported && state_rounds) {
        result = p6_state_bound(&state_bound, &error);
        if (result != P6_OK) return fail("p6_state_bound", result, error);
        std::vector<uint8_t> blob(size_t(state_bound), 0);
        for (uint64_t r = 0; r < state_rounds; ++r) {
            uint64_t written = 0;
            const uint64_t s0 = wall_ns();
            result = p6_state_save(blob.data(), blob.size(), &written, &error);
            save_ns.push_back(wall_ns() - s0);
            if (result != P6_OK) return fail("p6_state_save", result, error);
            state_bytes = written;
            uint64_t unused = 0;
            if (!run_unit(&unused, nullptr)) return 1;       // the machine moves away from the saved point...
            const uint64_t l0 = wall_ns();
            result = p6_state_load(blob.data(), written, &error);
            load_ns.push_back(wall_ns() - l0);               // ...and the load brings it back
            if (result != P6_OK) return fail("p6_state_load", result, error);
            uint64_t first = 0;
            if (!run_unit(&first, nullptr)) return 1;
            first_after_load_ns.push_back(first);
        }
    }

    // Allocations per unit, with the logger on (slow: the units are counted, not timed).
    uint64_t alloc_calls = 0, free_calls = 0, alloc_bytes = 0;
    int alloc_supported = 0;
    bool logger_works = false;
#ifdef __APPLE__
    if (alloc_units) {
        alloc_supported = 1;
        counted = {};
        malloc_logger = count_allocation;
        void *volatile probe_block = malloc(4096);          // self-test: the logger has to see an allocation and a free, or its zeros would mean nothing
        free(probe_block);
        logger_works = counted.allocs >= 1 && counted.frees >= 1;
        counted = {};
        for (uint64_t u = 0; u < alloc_units; ++u) {
            frame = {};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            if (p6_run_frame(&input, render, &frame, &error) != P6_OK) {
                malloc_logger = nullptr;
                return fail("p6_run_frame", P6_ERROR_BAD_STATE, error);
            }
        }
        malloc_logger = nullptr;
        alloc_calls = counted.allocs;
        free_calls = counted.frees;
        alloc_bytes = counted.bytes;
    }
#endif
    p6_unload(nullptr);
    p6_shutdown(nullptr);

    printf("{\"tool\":\"p6_bench_probe\",\"rom_bytes\":%zu,\"disk_bytes\":%zu,\"machine\":%u,\"video_standard\":%u,\"render\":%u,\"seed\":%" PRIu64
           ",\"warmup_units\":%" PRIu64 ",\"units\":%" PRIu64 ",\"unit_index_at_window_end\":%" PRIu64 ",\"master_ticks_at_window_end\":%" PRIu64 ",\"video_units\":%" PRIu64
           ",\"audio_frames\":%" PRIu64 ",\"active\":[%u,%u],\"cadence\":[%u,%u],\"caps\":%" PRIu64 ",\"window_wall_ns\":%" PRIu64
           ",\"window_user_us\":%" PRIu64 ",\"window_sys_us\":%" PRIu64 ",\"warmup_median_ns\":%" PRIu64 ",\"warmup_first_ns\":%" PRIu64 ",",
           rom.size(), disk.size(), machine, standard, render, seed, warmup, units, end_unit, end_ticks, timed_video_units, timed_audio, active_w, active_h, cadence_num,
           cadence_den, uint64_t(caps.flags), window,
           uint64_t(ru1.ru_utime.tv_sec - ru0.ru_utime.tv_sec) * 1000000u + uint64_t(ru1.ru_utime.tv_usec - ru0.ru_utime.tv_usec),
           uint64_t(ru1.ru_stime.tv_sec - ru0.ru_stime.tv_sec) * 1000000u + uint64_t(ru1.ru_stime.tv_usec - ru0.ru_stime.tv_usec), median_of(warm),
           warm.empty() ? 0 : warm[0]);
    printf("\"memory\":{\"resident_before\":%" PRIu64 ",\"resident_after\":%" PRIu64 ",\"footprint_before\":%" PRIu64 ",\"footprint_after\":%" PRIu64 ",\"peak_resident\":%" PRIu64
           ",\"malloc_in_use_before\":%" PRIu64 ",\"malloc_in_use_after\":%" PRIu64 ",\"malloc_allocated_after\":%" PRIu64 "},",
           before.resident, after.resident, before.footprint, after.footprint, after.peak_resident, before.malloc_in_use, after.malloc_in_use, after.malloc_allocated);
    printf("\"state\":{\"supported\":%d,\"bound_bytes\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"rounds\":%zu,\"dirty_pages\":null},", state_supported, state_bound, state_bytes, save_ns.size());
    printf("\"alloc\":{\"supported\":%d,\"logger_works\":%d,\"units\":%" PRIu64 ",\"allocs\":%" PRIu64 ",\"frees\":%" PRIu64 ",\"bytes\":%" PRIu64 "},", alloc_supported, int(logger_works), alloc_supported ? alloc_units : 0,
           alloc_calls, free_calls, alloc_bytes);
    print_array("unit_wall_ns", wall);
    print_array("unit_cpu_ns", cpu);
    print_array("state_save_ns", save_ns);
    print_array("state_load_ns", load_ns);
    print_array("first_unit_after_load_ns", first_after_load_ns, true);
    printf("}\n");
    return 0;
}
