// T29a harness: loads a firmware with zero to four hard files (P6_MEDIA_HDF) and runs it through p6_run_frame, then dumps Chip RAM so that the test can read what the
// guest saw through uaehf.device (Chip RAM, then Slow RAM when the machine has it).
//   p6_hdf_probe ROM a500|a600|a1200 pal|ntsc SEED UNITS DUMP [HDF...]
// Output: one line per unit (registers and a digest of the whole machine, so that two processes can be compared), the checks on the caller's buffers and, when the
// images or the load are refused, the refusal. Exit 0 when the load and every unit succeeded, 3 when the load was refused (the message is printed), 4 for a failed unit.
// With P6_PROBE_STATE=1 the probe also asks for the state bound and a save and prints the answers (a session with a hard file has none).
#include "p6.h"
#include "media.h"
#include "sha256.h"
#include "blob_io.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "newcpu.h"

#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#if defined(__APPLE__)
#include <malloc/malloc.h>
#elif defined(__GLIBC__)
#include <malloc.h>
#endif

namespace {

// Bytes the C library holds for the process right now (0 when the platform has no cheap answer): the leak check compares it between cycles.
size_t heap_in_use()
{
#if defined(__APPLE__)
    malloc_statistics_t stats;
    malloc_zone_statistics(nullptr, &stats);
    return stats.size_in_use;
#elif defined(__GLIBC__) && defined(__GLIBC_PREREQ) && __GLIBC_PREREQ(2, 33)
    return mallinfo2().uordblks;
#else
    return 0;
#endif
}

struct Hash {
    uint64_t value = UINT64_C(1469598103934665603);
    void bytes(const void *data, size_t size)
    {
        const uint8_t *p = static_cast<const uint8_t *>(data);
        for (size_t i = 0; i < size; ++i) { value ^= p[i]; value *= UINT64_C(1099511628211); }
    }
    template <class T> void add(const T &v) { bytes(&v, sizeof v); }
};

uint64_t state_digest()
{
    Hash h;
    for (uae_u32 r : regs.regs) h.add(r);
    MakeSR();
    h.add(m68k_getpc()); h.add(regs.sr); h.add(regs.usp); h.add(regs.isp);
    h.add(get_cycles()); h.add(vpos); h.add(agnus_hpos);
    h.add(dmacon); h.add(intena); h.add(intreq);
    for (const ev &e : eventtab) { h.add(e.active); h.add(e.evtime); }
    h.bytes(chipmem_bank.baseaddr, chipmem_bank.allocated_size);
    return h.value;
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

p6_media hard_file(const std::vector<uint8_t> &image)
{
    p6_media m{};
    m.type = P6_MEDIA_HDF;
    p6_sha256_hash(image.data(), image.size(), m.content_id);
    m.image = p6_buffer{image.data(), image.size()};
    return m;
}

bool blob_equals(const p6_media &m, const std::vector<uint8_t> &bytes)
{
    char id[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(m.content_id, id);
    std::vector<uint8_t> copy(bytes.size());
    size_t got = 0;
    return p6_blob_pread(id, 0, copy.data(), copy.size(), &got) == P6_BLOB_OK && got == bytes.size() && copy == bytes;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc < 7) { fprintf(stderr, "usage: %s ROM a500|a600|a1200 pal|ntsc SEED UNITS DUMP [HDF...]\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const uint32_t machine = !strcmp(argv[2], "a1200") ? P6_MACHINE_A1200 : !strcmp(argv[2], "a600") ? P6_MACHINE_A600 : P6_MACHINE_A500;
    const uint32_t standard = !strcmp(argv[3], "ntsc") ? P6_VIDEO_NTSC : P6_VIDEO_PAL;
    const uint64_t seed = strtoull(argv[4], nullptr, 10);
    const int units = atoi(argv[5]);
    std::vector<std::vector<uint8_t>> images, originals;
    std::vector<p6_media> media;
    for (int i = 7; i < argc; ++i) images.push_back(read_file(argv[i]));
    originals = images;
    for (const auto &image : images) media.push_back(hard_file(image));

    p6_error error{P6_ABI_VERSION, sizeof(p6_error), {}};
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = machine;
    c.video_standard = standard;
    c.audio_rate_hz = 48000;
    c.random_seed = seed;
    c.rtc_epoch_seconds = 946684800;
    if (p6_init(&c, &error) != P6_OK) { fprintf(stderr, "init: %s\n", error.message); return 3; }
    if (const char *cycles = getenv("P6_PROBE_CYCLES")) {
        // Load, run two units and unload again and again; what the heap holds after each cycle must not grow.
        for (int cycle = 0; cycle < atoi(cycles); ++cycle) {
            if (p6_load(p6_buffer{rom.data(), rom.size()}, media.data(), uint32_t(media.size()), &error) != P6_OK) { printf("load: %s\n", error.message); return 3; }
            p6_input input{};
            input.abi_version = P6_ABI_VERSION;
            input.struct_size = sizeof input;
            for (int unit = 0; unit < 2; ++unit) {
                p6_frame frame{};
                frame.abi_version = P6_ABI_VERSION;
                frame.struct_size = sizeof frame;
                if (p6_run_frame(&input, 0, &frame, &error) != P6_OK) { printf("run: %s\n", error.message); return 4; }
            }
            p6_unload(&error);
            printf("cycle %d heap=%zu\n", cycle, heap_in_use());
            fflush(stdout);
        }
        p6_shutdown(&error);
        return 0;
    }
    const p6_result loaded = p6_load(p6_buffer{rom.data(), rom.size()}, media.data(), uint32_t(media.size()), &error);
    if (loaded != P6_OK) { printf("load refused: %d %s\n", int(loaded), error.message); return 3; }

    p6_input in{};
    in.abi_version = P6_ABI_VERSION;
    in.struct_size = sizeof in;
    for (int unit = 1; unit <= units; ++unit) {
        p6_frame frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        if (p6_run_frame(&in, 0, &frame, &error) != P6_OK) { printf("run: %s\n", error.message); return 4; }
        printf("unit %d d0=%08x d1=%08x pc=%06x digest=%016" PRIx64 "\n", unit, regs.regs[0], regs.regs[1], m68k_getpc(), state_digest());
    }
    printf("final digest=%016" PRIx64 "\n", state_digest());
    printf("regs");
    for (int r = 0; r < 16; ++r) printf(" %s%d=%08x", r < 8 ? "d" : "a", r & 7, regs.regs[r]);
    MakeSR();
    printf(" sr=%04x halted=%d\n", regs.sr, int(regs.halted));
    for (size_t i = 0; i < images.size(); ++i)
        printf("hdf %zu buffer_unchanged=%d blob_equal=%d\n", i, int(images[i] == originals[i]), int(blob_equals(media[i], originals[i])));
    if (getenv("P6_PROBE_STATE")) {
        uint64_t bound = 0, written = 0;
        p6_result r = p6_state_bound(&bound, &error);
        printf("state_bound=%d %s\n", int(r), r == P6_OK ? "" : error.message);
        std::vector<uint8_t> buffer(1 << 20);
        r = p6_state_save(buffer.data(), buffer.size(), &written, &error);
        printf("state_save=%d %s\n", int(r), r == P6_OK ? "" : error.message);
    }

    FILE *out = fopen(argv[6], "wb");
    if (!out) { perror(argv[6]); return 5; }
    fwrite(chipmem_bank.baseaddr, 1, chipmem_bank.allocated_size, out);
    if (bogomem_bank.baseaddr)                       // Slow RAM follows Chip RAM in the dump: a Kickstart allocates there first on an A500
        fwrite(bogomem_bank.baseaddr, 1, bogomem_bank.allocated_size, out);
    fclose(out);
    p6_unload(&error);
    p6_shutdown(&error);
    return 0;
}
