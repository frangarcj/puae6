// Private stock CDTV firmware/map gate; no autoconfig, CD boot or state claim.
#include "p6.h"
#include "config.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "newcpu.h"
#include "events.h"
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
int main(int argc, char **argv)
{
    assert(argc == 3); const auto main = file(argv[1]), ext = file(argv[2]);
    auto rom = ext; rom.insert(rom.end(), main.begin(), main.end());
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
