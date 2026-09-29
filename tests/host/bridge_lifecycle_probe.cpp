// Drives p6_init / p6_load / p6_unload / p6_shutdown against the real engine with a micro-ROM.
// The probe inspects engine state directly (it is test code); the bridge sources never call
// m68k_go_unit for the frame boundary except through p6_run_frame (T07c).
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "newcpu.h"
#include "rom_status.h"
#include "sounddep/sound.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

extern int in_m68k_go;  // defined in newcpu.cpp, not declared by any header

static p6_error make_error()
{
    p6_error e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    return e;
}

static p6_config make_config(uint32_t standard = P6_VIDEO_PAL, uint64_t seed = 1)
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = P6_MACHINE_A500;
    c.video_standard = standard;
    c.audio_rate_hz = 48000;
    c.random_seed = seed;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

static std::vector<uint8_t> read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    std::vector<uint8_t> data;
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data.insert(data.end(), buffer, buffer + n);
    fclose(f);
    return data;
}

static p6_buffer buffer_of(const std::vector<uint8_t> &v) { return p6_buffer{v.data(), v.size()}; }

static p6_result load(const std::vector<uint8_t> &rom, p6_error *e = nullptr)
{
    return p6_load(buffer_of(rom), nullptr, 0, e);
}

static void expect_load_error(const std::vector<uint8_t> &rom, p6_result code)
{
    p6_error e = make_error();
    const p6_result got = load(rom, &e);
    if (got != code) fprintf(stderr, "load returned %d (%s), expected %d\n", int(got), e.message, int(code));
    assert(got == code);
    assert(e.message[0] != 0 && strlen(e.message) < P6_ERROR_TEXT_CAPACITY);
    // A failed load releases what it took: the CPU loop is not entered and a good load works next.
    assert(in_m68k_go == 0 && quit_program == 0);
}

static void rejects_bad_calls_before_load(const std::vector<uint8_t> &rom)
{
    p6_error e = make_error();
    assert(load(rom, &e) == P6_ERROR_BAD_STATE);
    assert(p6_unload(&e) == P6_OK && p6_shutdown(&e) == P6_OK && e.message[0] == 0);
    assert(p6_init(nullptr, &e) == P6_ERROR_BAD_ARGUMENT);

    p6_config c = make_config();
    c.abi_version = 2;       assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.struct_size = 8;                assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.machine = 2;                    assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(3);                                  assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.audio_rate_hz = 22050;          assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.flags = 1;                      assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.reserved[3] = 1;                assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.rtc_epoch_seconds = INT64_MIN;  assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.rtc_epoch_seconds = INT64_MAX;  assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    assert(e.message[0] != 0);
    p6_error bad{};                                      // an error struct we cannot write to
    c = make_config();                                   assert(p6_init(&c, &bad) == P6_ERROR_BAD_ARGUMENT);
    assert(load(rom, &e) == P6_ERROR_BAD_STATE);         // none of the rejected inits initialized anything
}

static void rejects_bad_firmware(const std::vector<uint8_t> &good)
{
    p6_error e = make_error();
    // Rejections before the engine: the preferences of an earlier run stay exactly as they were.
    currprefs.seed = 0x5eed1234;
    assert(p6_load(p6_buffer{nullptr, 0}, nullptr, 0, &e) == P6_ERROR_NEED_FIRMWARE);
    const std::vector<uint8_t> oversized(2 * 1024 * 1024, 0);
    assert(load(oversized, &e) == P6_ERROR_WRONG_MEDIA && e.message[0] != 0);
    assert(currprefs.seed == 0x5eed1234 && in_m68k_go == 0);
    p6_media media{};
    assert(p6_load(buffer_of(good), &media, 1, &e) == P6_ERROR_UNSUPPORTED);
    assert(in_m68k_go == 0);

    expect_load_error(std::vector<uint8_t>(100, 0), P6_ERROR_WRONG_MEDIA);           // not a ROM size
    expect_load_error(std::vector<uint8_t>(good.size(), 0), P6_ERROR_WRONG_MEDIA);   // right size, no header
    std::vector<uint8_t> flipped = good;
    flipped[0xD2] ^= 0x10;                                                            // structure fine, sum broken
    expect_load_error(flipped, P6_ERROR_WRONG_MEDIA);
    std::vector<uint8_t> wrong_length = good;
    wrong_length[good.size() - 17] ^= 1;
    expect_load_error(wrong_length, P6_ERROR_WRONG_MEDIA);
}

struct Snapshot {
    uint32_t pc;
    evt_t cycles;
    unsigned chip_digest;
};

static Snapshot applied_state()
{
    assert(m68k_go_unit(M68K_UNIT_APPLY) == M68K_UNIT_APPLIED);
    unsigned digest = 0;
    for (uint32_t i = 0; i < chipmem_bank.allocated_size; ++i) digest = digest * 31 + chipmem_bank.baseaddr[i];
    return {m68k_getpc(), get_cycles(), digest};
}

static Snapshot boot_once(const std::vector<uint8_t> &rom, uint32_t standard, uint64_t seed)
{
    p6_error e = make_error();
    p6_config c = make_config(standard, seed);
    assert(p6_init(&c, &e) == P6_OK);
    assert(p6_init(&c, &e) == P6_ERROR_BAD_STATE);
    rejects_bad_firmware(rom);

    const p6_result loaded = load(rom, &e);
    if (loaded != P6_OK) fprintf(stderr, "good load returned %d (%s)\n", int(loaded), e.message);
    assert(loaded == P6_OK && e.message[0] == 0);
    assert(load(rom, &e) == P6_ERROR_BAD_STATE);
    assert(in_m68k_go == 1 && quit_program == UAE_RESET_HARD);
    assert(p6_memory_rom_status() == P6_ROM_LOAD_OK);
    // The fixed A500 preset.
    assert(currprefs.cpu_model == 68000 && currprefs.cpu_cycle_exact && currprefs.address_space_24);
    assert(currprefs.chipset_mask == CSMASK_OCS && !currprefs.fastmem[0].size);
    assert(currprefs.chipmem.size == 0x80000 && currprefs.bogomem.size == 0x80000);
    assert(currprefs.ntscmode == (standard == P6_VIDEO_NTSC) && currprefs.sound_freq == 48000);
    assert(currprefs.reset_delay == 0 && currprefs.crash_auto_reset == 0);
    assert(kickmem_bank.reserved_size == 256 * 1024 && !memcmp(kickmem_bank.baseaddr, rom.data(), rom.size()));

    const Snapshot s = applied_state();
    // The reset head ran: the reset vector was read, the CPU is in supervisor mode with IRQs masked.
    assert(regs.s && regs.intmask == 7 && !regs.halted);
    assert(currprefs.produce_sound == 3 && p6_sound_status() == P6_PCM_OK);   // the head opened the transport
    assert(!(regs.spcflags & SPCFLAG_UNIT_END));
    assert(p6_unload(&e) == P6_OK && p6_unload(&e) == P6_OK);   // repeated unload is safe
    assert(in_m68k_go == 0 && quit_program == 0);
    assert(p6_shutdown(&e) == P6_OK && p6_shutdown(&e) == P6_OK);
    return s;
}

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: %s boot.rom\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    assert(rom.size() == 256 * 1024);

    rejects_bad_calls_before_load(rom);

    const Snapshot first = boot_once(rom, P6_VIDEO_PAL, 1);
    assert(first.pc == 0xF800D2);
    // A second load in the same process starts from the same state: same seed, same delay, same memory.
    const Snapshot again = boot_once(rom, P6_VIDEO_PAL, 1);
    assert(again.pc == first.pc && again.cycles == first.cycles && again.chip_digest == first.chip_digest);
    // The start-up delay is random but seeded: another seed gives another delay, NTSC is a different clock.
    const Snapshot other = boot_once(rom, P6_VIDEO_PAL, 2);
    assert(other.pc == first.pc && other.cycles != first.cycles);
    const Snapshot ntsc = boot_once(rom, P6_VIDEO_NTSC, 1);
    assert(ntsc.pc == first.pc);
    puts("bridge lifecycle ok");
    return 0;
}
