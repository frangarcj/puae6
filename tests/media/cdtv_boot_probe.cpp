// Private stock CDTV firmware/map gate; no autoconfig, CD boot or state claim.
#include "p6.h"
#include "config.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "newcpu.h"
#include "events.h"
#include "media/cd_device.h"
#include "media/sha256.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <vector>
#ifndef _WIN32
#include <sys/mman.h>
#endif
static p6_error error{P6_ABI_VERSION, sizeof error};
static std::vector<uint8_t> file(const char *path)
{
    FILE *f = fopen(path, "rb"); assert(f); std::vector<uint8_t> data(262144);
    assert(fread(data.data(), 1, data.size(), f) == data.size() && fgetc(f) == EOF); fclose(f); return data;
}
static void ok(p6_result result) { if (result) { fprintf(stderr, "%d: %s\n", int(result), error.message); abort(); } }
static p6_config config(uint32_t machine = p6_bridge::kCdtvMachine)
{
    p6_config c{P6_ABI_VERSION, sizeof c}; c.machine = machine; c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000; c.random_seed = 1; c.rtc_epoch_seconds = 946684800; return c;
}
static p6_result load_readonly(const std::vector<uint8_t> &rom)
{
#ifndef _WIN32
    void *p = mmap(nullptr, rom.size(), PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); assert(p != MAP_FAILED);
    memcpy(p, rom.data(), rom.size()); assert(!mprotect(p, rom.size(), PROT_READ));
    auto result = p6_load({p, rom.size()}, nullptr, 0, &error);
    assert(!memcmp(p, rom.data(), rom.size()) && !munmap(p, rom.size())); return result;
#else
    auto copy = rom; auto result = p6_load({copy.data(), copy.size()}, nullptr, 0, &error); assert(copy == rom); return result;
#endif
}
static void available(bool started = false)
{
    p6_caps caps{P6_ABI_VERSION, sizeof caps}; ok(p6_query_caps(&caps, &error)); assert(caps.flags & P6_CAP_STATE);
    uint64_t bound = 9, written = 9; uint8_t data[64]; memset(data, 0xa5, sizeof data);
    assert(p6_state_bound(&bound, &error) == P6_OK && bound > 9);
    assert(p6_state_save(data, sizeof data, &written, &error) == (started ? P6_ERROR_CAPACITY : P6_ERROR_BAD_STATE) && !written);
    assert(p6_state_load(data, sizeof data, &error) == P6_ERROR_CORRUPT_STATE);
    for (auto b : data) assert(b == 0xa5);
}
static uint32_t frames()
{
    p6_input input{P6_ABI_VERSION, sizeof input}; p6_frame out{P6_ABI_VERSION, sizeof out};
    for (unsigned n = 1; n <= 4; ++n) { ok(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &out, &error)); assert(out.unit_index == n && out.master_ticks); }
    assert(out.video && out.video_width && out.video_height && out.audio_frames && m68k_getpc() != 0);
    return m68k_getpc();
}
static void maps(const std::vector<uint8_t> &main, const std::vector<uint8_t> &ext)
{
    assert(kickmem_bank.baseaddr && kickmem_bank.reserved_size == 262144 && kickmem_bank.mask == 262143);
    assert(extendedkickmem_bank.baseaddr && extendedkickmem_bank.reserved_size == 524288 && extendedkickmem_bank.mask == 524287);
    assert(extendedkickmem_bank.start == 0xf00000 && !memcmp(kickmem_bank.baseaddr, main.data(), main.size()));
    // Independent numeric oracle for read_kickstart's zero fill/footer/checksum.
    std::vector<uint8_t> expected(524288); std::copy(ext.begin(), ext.end(), expected.begin());
    for (unsigned n = 0; n < 8; ++n) expected[expected.size() - 15 + n * 2] = 0x18 + n;
    expected[expected.size() - 19] = 8;
    uint32_t sum = 0; for (size_t n = 0; n < expected.size(); n += 4) {
        uint32_t word = uint32_t(expected[n]) << 24 | uint32_t(expected[n+1]) << 16 | uint32_t(expected[n+2]) << 8 | expected[n+3];
        uint64_t total = uint64_t(sum) + word; sum = uint32_t(total) + uint32_t(total >> 32);
    }
    sum = ~sum; for (unsigned n = 0; n < 4; ++n) expected[expected.size() - 24 + n] = uint8_t(sum >> (24 - n * 8));
    assert(!memcmp(extendedkickmem_bank.baseaddr, expected.data(), expected.size()));
    for (unsigned page = 0xf0; page < 0xf8; ++page) assert(&get_mem_bank(page << 16) == &extendedkickmem_bank);
    for (unsigned page = 0xf8; page < 0x100; ++page) assert(&get_mem_bank(page << 16) == &kickmem_bank);
    assert(&get_mem_bank(0xe00000) != &extendedkickmem_bank && &get_mem_bank(0xe90000) != &extendedkickmem_bank);
}
struct BootImage {
    std::vector<uint8_t> bytes;
    unsigned reads = 0;
    static bool read(const void *context, uint64_t at, void *out, size_t n, size_t *got)
    {
        auto &image = *const_cast<BootImage *>(static_cast<const BootImage *>(context)); *got = 0;
        if (at > image.bytes.size() || n > image.bytes.size() - at) return false;
        memcpy(out, image.bytes.data() + at, n); ++image.reads; *got = n;
        fprintf(stderr, "ISO read%u LBA=%llu offset=%u bytes=%zu\n", image.reads,
                static_cast<unsigned long long>(at / 2048), unsigned(at % 2048), n); return true;
    }
};
static void original_boot(const std::vector<uint8_t> &rom, const char *path, const char *nonce_hex)
{
    BootImage image; FILE *f = fopen(path, "rb"); assert(f && !fseek(f, 0, SEEK_END));
    const long size = ftell(f); assert(size > 0 && size <= 1048576 && size % 2048 == 0); rewind(f);
    image.bytes.resize(size); assert(fread(image.bytes.data(), 1, size, f) == size); fclose(f);
    uint8_t nonce[16]; assert(strlen(nonce_hex) == 32);
    for (unsigned n = 0; n < 16; ++n) { char hex[3]{nonce_hex[n*2], nonce_hex[n*2+1], 0}; nonce[n] = uint8_t(strtoul(hex, nullptr, 16)); }
    p6_cd::Layout layout{}; layout.tracks = 1; layout.leadout = uint32_t(size / 2048);
    layout.track[0] = {0, 0, layout.leadout, 0, 2048, 0, p6_cd::Mode::Mode1_2048, 1, 4, true};
    p6_cd::Binding source{}; source.identity.bytes = size; source.identity.sector_bytes = 2048;
    p6_sha256_hash(image.bytes.data(), size, source.identity.content_id);
    memcpy(layout.content_id, source.identity.content_id, 32); source.read = BootImage::read; source.context = &image;
    p6_cd::Disc disc; assert(disc.bind(layout, &source, 1) == p6_cd::Result::Ok);
    auto c = config(); ok(p6_init(&c, &error)); ok(load_readonly(rom));
    assert(p6_cd::device_bind(&disc) == p6_cd::Result::Ok);
    const char signature[16] = "P6CDTV_BOOT_v1!";
    auto live = [&]() {
        unsigned found = 0; uint32_t counter = 0;
        const auto *ram = chipmem_bank.baseaddr;
        for (uint32_t at = 0; at + 36 <= chipmem_bank.allocated_size; at += 2)
            if (!memcmp(ram + at, signature, 16) && !memcmp(ram + at + 16, nonce, 16)) {
                const auto *p = ram + at + 32; const uint32_t value = uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
                if (value) { ++found; counter = value; }
            }
        assert(found <= 1); return counter;
    };
    p6_input input{P6_ABI_VERSION, sizeof input}; p6_frame out{P6_ABI_VERSION, sizeof out};
    uint32_t first = 0; unsigned unit = 0;
    for (; unit < 600 && !first; ++unit) {
        ok(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &out, &error)); first = live();
    }
    fprintf(stderr, "CDTV original boot units=%u reads=%u PC=%x marker=%u\n", unit, image.reads, m68k_getpc(), first);
    unsigned signatures = 0, zero = 0, positive = 0, matching = 0;
    for (uint32_t at = 0; at + 36 <= chipmem_bank.allocated_size; at += 2) {
        const auto *p = chipmem_bank.baseaddr + at;
        if (memcmp(p, signature, 16)) continue;
        ++signatures; bool count = p[32] || p[33] || p[34] || p[35]; count ? ++positive : ++zero;
        matching += !memcmp(p + 16, nonce, 16);
        fprintf(stderr, "HUNK signature RAM=%x counter=%02x%02x%02x%02x nonce=", at, p[32], p[33], p[34], p[35]);
        for (unsigned n = 16; n < 32; ++n) fprintf(stderr, "%02x", p[n]); fputc('\n', stderr);
    }
    fprintf(stderr, "HUNK signatures=%u zero=%u positive=%u expected_nonce=%u\n", signatures, zero, positive, matching);
    assert(first && image.reads);
    for (unsigned n = 0; n < 16; ++n) ok(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &out, &error));
    assert(live() > first); ok(p6_shutdown(&error)); p6_cd::device_unbind();
    puts("CDTV original ISO/HUNK nonce read and guest Delay counter advanced");
}
int main(int argc, char **argv)
{
    assert(argc == 3 || argc == 5); const auto main = file(argv[1]), ext = file(argv[2]);
    auto rom = ext; rom.insert(rom.end(), main.begin(), main.end());
    if (argc == 5) { original_boot(rom, argv[3], argv[4]); return 0; }
    for (unsigned n = 0; n < 11; ++n) {
        auto c = config();
        if (n == 0) c.chip_kib = 2048; if (n == 1) c.slow_kib = 512; if (n == 2) c.fast_kib = 1024;
        if (n == 3) c.cpu_model = 2; if (n == 4) c.cpu_compatibility = P6_CPU_NORMAL; if (n == 5) c.cpu_multiplier = 4;
        if (n == 6) c.floppy_drives = 1; if (n == 7) c.fpu_model = 68881; if (n == 8) c.z3_memory = 1;
        if (n == 9) c.flags = P6_CONFIG_FLOPPY_WRITES; if (n == 10) c.hdf_controller = P6_HDF_CONTROLLER_IDE;
        assert(p6_init(&c, &error) == P6_ERROR_UNSUPPORTED);
    }
    uint32_t pc = 0;
    for (unsigned repeat = 0; repeat < 2; ++repeat) {
        auto c = config(); ok(p6_init(&c, &error));
        for (unsigned n = 0; n < 5; ++n) {
            auto bad = rom;
            if (n == 0) bad.pop_back(); if (n == 1) bad.push_back(0);
            if (n == 2) std::rotate(bad.begin(), bad.begin()+262144, bad.end());
            if (n == 3) bad[99] ^= 1; if (n == 4) bad[262144+99] ^= 1;
            auto *old_main = kickmem_bank.baseaddr; auto *old_ext = extendedkickmem_bank.baseaddr; auto cycles = get_cycles();
            assert(load_readonly(bad) == P6_ERROR_WRONG_MEDIA);
            assert(kickmem_bank.baseaddr == old_main && extendedkickmem_bank.baseaddr == old_ext && get_cycles() == cycles);
        }
        ok(load_readonly(rom)); maps(main, ext); available();
        if (!repeat) {
#define PREF(name, value) printf("pref %s %u\n", name, unsigned(value))
            PREF("chip_kib", currprefs.chipmem.size/1024); PREF("slow_kib", currprefs.bogomem.size/1024); PREF("fast_kib", currprefs.fastmem[0].size/1024);
#define FIELD(name) PREF(#name, currprefs.name)
            FIELD(cpu_model); FIELD(address_space_24); FIELD(cpu_cycle_exact); FIELD(cpu_clock_multiplier); FIELD(chipset_mask); FIELD(nr_floppies);
            FIELD(cs_compatible); FIELD(keyboard_mode); FIELD(cs_resetwarning); FIELD(cs_cdtvcd); FIELD(cs_cdtvram); FIELD(cs_cdtvcr); FIELD(cs_rtc); FIELD(cs_ciaoverlay); FIELD(cs_ksmirror_e0); FIELD(cs_ide); FIELD(cs_pcmcia);
            assert(!currprefs.flashfile[0] && !currprefs.romextfile[0] && !currprefs.romextfile2[0]);
        }
        auto next = frames(); if (repeat) assert(pc == next); pc = next; maps(main, ext); available(true); ok(p6_shutdown(&error));
        assert(!kickmem_bank.baseaddr && !extendedkickmem_bank.baseaddr);
    }
    auto c = config(P6_MACHINE_A500); ok(p6_init(&c, &error)); ok(load_readonly(main)); frames();
    assert(!currprefs.cs_cdtvcd && !currprefs.cs_cdtvram && !extendedkickmem_bank.baseaddr);
    uint64_t bound = 0; ok(p6_state_bound(&bound, &error)); assert(bound); ok(p6_shutdown(&error));
    puts("CDTV firmware executed; maps/rejections/teardown and A500 reload ok; CD boot/autoconfig/state not asserted");
}
