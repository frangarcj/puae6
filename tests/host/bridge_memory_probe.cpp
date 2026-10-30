// T20a: p6_memory_regions and P6_FRAME_DRIVE_LIGHT against the real engine.
//   p6_memory_probe CHIPCELL.rom DISK.rom A.adf
// CHIPCELL.rom is tests/waterbox-smoke/chipcell.s: it stores the marker 'P6MW' at $10000 and copies the word at $30000 into COLOR00 forever,
// so a host that overwrites that word changes the next picture. DISK.rom is the floppy guest of tests/scenarios/disk (motor on at power-on).
#include "p6.h"
#include "sha256.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "gui.h"
#include "memory.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

p6_error make_error()
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

p6_config make_config()
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = P6_MACHINE_A500;
    c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000;
    c.random_seed = 1;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

p6_frame run_unit(uint32_t render = P6_RENDER_VIDEO)
{
    p6_input input{};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    p6_frame frame{};
    frame.abi_version = P6_ABI_VERSION;
    frame.struct_size = sizeof frame;
    p6_error e = make_error();
    assert(p6_run_frame(&input, render, &frame, &e) == P6_OK);
    return frame;
}

uint32_t centre_pixel(const p6_frame &f)
{
    assert(f.video && f.video_format == P6_PIXEL_XRGB8888);
    const uint8_t *row = static_cast<const uint8_t *>(f.video) + size_t(f.active.y + f.active.height / 2) * f.video_stride_bytes;
    uint32_t pixel;
    memcpy(&pixel, row + size_t(f.active.x + f.active.width / 2) * 4, 4);
    return pixel & 0xFFFFFF;
}

struct Regions {
    p6_memory_region list[4];
    uint32_t count = 0;
    const p6_memory_region *find(uint32_t type) const
    {
        for (uint32_t i = 0; i < count; ++i) if (list[i].type == type) return &list[i];
        return nullptr;
    }
};

Regions ask()
{
    Regions r{};
    p6_error e = make_error();
    assert(p6_memory_regions(r.list, 4, &r.count, &e) == P6_OK && e.message[0] == 0);
    return r;
}

void load(const std::vector<uint8_t> &rom, const std::vector<uint8_t> *adf)
{
    p6_error e = make_error();
    p6_config c = make_config();
    assert(p6_init(&c, &e) == P6_OK);
    p6_media media{};
    if (adf) {
        media.type = P6_MEDIA_ADF;
        p6_sha256_hash(adf->data(), adf->size(), media.content_id);
        media.image = p6_buffer{adf->data(), adf->size()};
    }
    assert(p6_load(p6_buffer{rom.data(), rom.size()}, adf ? &media : nullptr, adf ? 1 : 0, &e) == P6_OK);
}

void load_config(const std::vector<uint8_t> &rom, const p6_config &c)
{
    p6_error e = make_error();
    assert(p6_init(&c, &e) == P6_OK);
    assert(p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &e) == P6_OK);
}

void unload()
{
    p6_error e = make_error();
    assert(p6_unload(&e) == P6_OK && p6_shutdown(&e) == P6_OK);
}

void refuses_before_a_load_and_after_an_unload(const std::vector<uint8_t> &rom)
{
    p6_error e = make_error();
    uint32_t count = 77;
    assert(p6_memory_regions(nullptr, 0, &count, &e) == P6_ERROR_BAD_STATE && e.message[0] != 0 && count == 77);
    p6_config c = make_config();
    assert(p6_init(&c, &e) == P6_OK);
    assert(p6_memory_regions(nullptr, 0, &count, &e) == P6_ERROR_BAD_STATE && count == 77);   // initialized, nothing loaded
    assert(p6_shutdown(&e) == P6_OK);
    load(rom, nullptr);
    assert(ask().count == 3);
    unload();
    assert(p6_memory_regions(nullptr, 0, &count, &e) == P6_ERROR_BAD_STATE && count == 77);
}

void reports_the_machine_memory(const std::vector<uint8_t> &rom)
{
    load(rom, nullptr);
    p6_error e = make_error();
    uint32_t count = 0;
    // Asking for the count: no output array, no partial output.
    assert(p6_memory_regions(nullptr, 0, &count, &e) == P6_OK && count == 3);
    // An array that cannot hold them all is refused without writing anything, and the count says what is needed.
    p6_memory_region two[2];
    memset(two, 0xA5, sizeof two);
    p6_memory_region before[2];
    memcpy(before, two, sizeof two);
    count = 0;
    assert(p6_memory_regions(two, 2, &count, &e) == P6_ERROR_CAPACITY && count == 3 && e.message[0] != 0);
    assert(!memcmp(two, before, sizeof two));
    // Bad arguments.
    p6_memory_region one{};
    assert(p6_memory_regions(&one, 0, &count, &e) == P6_ERROR_BAD_ARGUMENT);
    assert(p6_memory_regions(nullptr, 1, &count, &e) == P6_ERROR_BAD_ARGUMENT);
    assert(p6_memory_regions(&one, 1, nullptr, &e) == P6_ERROR_BAD_ARGUMENT);
    p6_error unusable{};
    assert(p6_memory_regions(nullptr, 0, &count, &unusable) == P6_ERROR_BAD_ARGUMENT);

    const Regions r = ask();
    const p6_memory_region *chip = r.find(P6_REGION_CHIP_RAM), *slow = r.find(P6_REGION_SLOW_RAM), *kick = r.find(P6_REGION_ROM);
    assert(chip && slow && kick);
    // Each region is the memory the engine really allocated: the same bytes, not a copy, with its real size.
    assert(chip->data == chipmem_bank.baseaddr && chip->bytes == 512 * 1024 && chip->bytes == chipmem_bank.allocated_size);
    assert(slow->data == bogomem_bank.baseaddr && slow->bytes == 512 * 1024);
    assert(kick->data == kickmem_bank.baseaddr && kick->bytes == kickmem_bank.allocated_size && kick->bytes >= rom.size());
    assert(chip->flags == P6_REGION_WRITABLE && slow->flags == P6_REGION_WRITABLE && kick->flags == 0);
    // The ROM is the firmware, byte for byte and in the order of the file (the 68000's).
    assert(!memcmp(kick->data, rom.data(), rom.size()));
    // Nothing is reported twice and nothing is empty.
    for (uint32_t i = 0; i < r.count; ++i) {
        assert(r.list[i].data && r.list[i].bytes);
        for (uint32_t j = i + 1; j < r.count; ++j) assert(r.list[i].type != r.list[j].type);
    }
    unload();
}

void the_host_sees_and_changes_what_the_cpu_does(const std::vector<uint8_t> &rom)
{
    load(rom, nullptr);
    const p6_frame first = run_unit();
    Regions r = ask();
    const uint8_t *chip = static_cast<const uint8_t *>(r.find(P6_REGION_CHIP_RAM)->data);
    // The guest stored the long $50364D57: the first byte at the lowest address, as on the 68000.
    assert(!memcmp(chip + 0x10000, "\x50\x36\x4D\x57", 4));
    assert(centre_pixel(first) == 0x000000);

    // A host write is a write to the memory the CPU reads: the next unit shows it. The word is big endian, $0F00 (red).
    uint8_t *writable = static_cast<uint8_t *>(const_cast<void *>(r.find(P6_REGION_CHIP_RAM)->data));
    writable[0x30000] = 0x0F;
    writable[0x30001] = 0x00;
    run_unit();
    const p6_frame red = run_unit();
    assert(centre_pixel(red) == 0xFF0000);

    // After a unit the regions are asked again (they are valid until the next call that changes the machine); a hard reset
    // gives the guest clean RAM, and the region still is the engine's memory.
    p6_error e = make_error();
    assert(p6_reset(&e) == P6_OK);
    run_unit();
    r = ask();
    assert(r.find(P6_REGION_CHIP_RAM)->data == chipmem_bank.baseaddr && r.find(P6_REGION_CHIP_RAM)->bytes == 512 * 1024);
    const uint8_t *after = static_cast<const uint8_t *>(r.find(P6_REGION_CHIP_RAM)->data);
    assert(!memcmp(after + 0x10000, "\x50\x36\x4D\x57", 4) && after[0x30000] == 0 && after[0x30001] == 0);
    assert(centre_pixel(run_unit()) == 0x000000);
    unload();
}

void the_drive_light_follows_the_led(const std::vector<uint8_t> &chipcell, const std::vector<uint8_t> &disk, const std::vector<uint8_t> &adf)
{
    // No disk and no motor: the light is never on.
    load(chipcell, nullptr);
    for (int i = 0; i < 40; ++i) {
        const p6_frame f = run_unit();
        assert(!(f.flags & P6_FRAME_DRIVE_LIGHT) && !(gui_ledstate & (0xFu << LED_DF0)));
    }

    // The ABI flag represents any emulated drive. Optical activity uses the
    // engine's own LED_CD bit and is cleared independently of the floppies.
    gui_led(LED_CD, 1, -1);
    assert(run_unit().flags & P6_FRAME_DRIVE_LIGHT);
    gui_led(LED_CD, 0, -1);
    assert(!(run_unit().flags & P6_FRAME_DRIVE_LIGHT));
    unload();

    // The floppy guest switches its motor on right away and leaves it on: the light comes on and stays on, and at every unit the
    // flag is what the engine's own LED bits say.
    load(disk, &adf);
    int first_lit = -1, lit = 0;
    for (int i = 0; i < 120; ++i) {
        const p6_frame f = run_unit(0);
        const bool engine_led = (gui_ledstate & (0xFu << LED_DF0)) != 0;
        assert(bool(f.flags & P6_FRAME_DRIVE_LIGHT) == engine_led);
        if (engine_led) { ++lit; if (first_lit < 0) first_lit = i; }
    }
    assert(first_lit >= 0 && first_lit < 40 && lit > 80);
    printf("drive light first lit at unit %d, lit in %d of 120 units\n", first_lit + 1, lit);
    unload();
}

}  // namespace

// T49: a Zorro II Fast RAM board is a region of its own at its real size, from the load on (it is allocated with the image, before the first unit), and a reload with another
// size or without the board leaves nothing of the previous one: not its region, not its bytes.
void reports_the_fast_ram_board(const std::vector<uint8_t> &rom)
{
    p6_config c = make_config();
    c.machine = P6_MACHINE_A1200;
    c.fast_kib = 8192;
    for (int round = 0; round < 3; ++round) {
        load_config(rom, c);
        Regions r = ask();
        assert(r.count == 3 && r.find(P6_REGION_CHIP_RAM) && r.find(P6_REGION_FAST_RAM) && r.find(P6_REGION_ROM) && !r.find(P6_REGION_SLOW_RAM));
        const p6_memory_region *fast = r.find(P6_REGION_FAST_RAM);
        assert(fast->bytes == 8u << 20 && fast->flags == P6_REGION_WRITABLE && r.find(P6_REGION_CHIP_RAM)->bytes == 2u << 20);
        const uint8_t *bytes = static_cast<const uint8_t *>(fast->data);
        for (size_t i = 0; i < fast->bytes; ++i) assert(bytes[i] == 0);        // a new board is zero, whatever the last one held
        const_cast<uint8_t *>(bytes)[12345] = 0x5a;
        run_unit();
        r = ask();
        assert(r.find(P6_REGION_FAST_RAM)->bytes == 8u << 20);
        unload();
    }
    // Another size, then none, then the board again.
    c.fast_kib = 1024;
    load_config(rom, c);
    assert(ask().find(P6_REGION_FAST_RAM)->bytes == 1u << 20);
    unload();
    c.fast_kib = 0;
    load_config(rom, c);
    assert(ask().count == 2 && !ask().find(P6_REGION_FAST_RAM));
    run_unit();
    assert(ask().count == 2);
    unload();
    c.fast_kib = 4096;
    c.machine = P6_MACHINE_A600;
    c.chip_kib = 2048;
    load_config(rom, c);
    Regions r = ask();
    assert(r.count == 3 && r.find(P6_REGION_CHIP_RAM)->bytes == 2u << 20 && r.find(P6_REGION_FAST_RAM)->bytes == 4u << 20);
    unload();
}

// T75a: the Slow RAM region is the size the CPU reaches for every option, not the size the engine allocated (2 MiB for 1.5 and 1.75 MiB). The bytes are the engine's own
// buffer, and what is allocated beyond the region is not touched by this call.
void reports_the_slow_ram_the_cpu_reaches(const std::vector<uint8_t> &rom)
{
    struct Case { uint32_t machine; uint32_t slow_kib; uint64_t reach; uint64_t allocated; };
    const Case cases[] = {
        {P6_MACHINE_A500, 0, 512u << 10, 512u << 10},                 // the machine's own
        {P6_MACHINE_A500, 512, 512u << 10, 512u << 10},
        {P6_MACHINE_A500, 1024, 1u << 20, 1u << 20},
        {P6_MACHINE_A500, 1536, 1536u << 10, 2u << 20},               // allocated as a power of two, reached up to 1.5 MiB
        {P6_MACHINE_A500, 1792, 1792u << 10, 2u << 20},
        {P6_MACHINE_A1200, 1536, 1536u << 10, 2u << 20},
        {P6_MACHINE_A1200, 1024, 1u << 20, 1u << 20},
        {P6_MACHINE_A500, P6_MEMORY_NONE, 0, 0},
    };
    for (const Case &test : cases) {
        p6_config c = make_config();
        c.machine = test.machine;
        c.slow_kib = test.slow_kib;
        load_config(rom, c);
        for (int round = 0; round < 2; ++round) {                    // before and after a unit
            const Regions r = ask();
            const p6_memory_region *slow = r.find(P6_REGION_SLOW_RAM);
            if (!test.reach) {
                assert(!slow);
            } else {
                assert(slow && slow->data == bogomem_bank.baseaddr && slow->flags == P6_REGION_WRITABLE);
                assert(slow->bytes == test.reach && bogomem_bank.allocated_size == test.allocated);
            }
            run_unit();
        }
        unload();
    }
}

// T50: the memory each preset comes with is what the engine allocates and reports, before and after a unit: Chip RAM of its size, the Slow RAM only where the preset has it.
void reports_the_memory_of_every_preset(const std::vector<uint8_t> &rom)
{
    struct Case { uint32_t machine; uint64_t chip; uint64_t slow; };
    const Case cases[] = {
        {P6_MACHINE_A500, 512u << 10, 512u << 10}, {P6_MACHINE_A500OG, 512u << 10, 0}, {P6_MACHINE_A500PLUS, 1u << 20, 0}, {P6_MACHINE_A600, 1u << 20, 0},
        {P6_MACHINE_A1000, 512u << 10, 0}, {P6_MACHINE_A1200, 2u << 20, 0}, {P6_MACHINE_A2000OG, 512u << 10, 512u << 10}, {P6_MACHINE_A2000, 1u << 20, 0},
    };
    for (const Case &test : cases) {
        p6_config c = make_config();
        c.machine = test.machine;
        load_config(rom, c);
        for (int round = 0; round < 2; ++round) {
            const Regions r = ask();
            const p6_memory_region *chip = r.find(P6_REGION_CHIP_RAM), *slow = r.find(P6_REGION_SLOW_RAM);
            assert(chip && chip->bytes == test.chip && chip->data == chipmem_bank.baseaddr && chip->flags == P6_REGION_WRITABLE);
            assert(r.find(P6_REGION_ROM) && !r.find(P6_REGION_FAST_RAM));
            if (test.slow) assert(slow && slow->bytes == test.slow && slow->data == bogomem_bank.baseaddr);
            else assert(!slow && r.count == 2);
            run_unit();
        }
        unload();
    }
}

int main(int argc, char **argv)
{
    if (argc != 4) { fprintf(stderr, "usage: %s CHIPCELL.rom DISK.rom A.adf\n", argv[0]); return 2; }
    const std::vector<uint8_t> chipcell = read_file(argv[1]), disk = read_file(argv[2]), adf = read_file(argv[3]);
    refuses_before_a_load_and_after_an_unload(chipcell);
    reports_the_machine_memory(chipcell);
    reports_the_fast_ram_board(chipcell);
    reports_the_slow_ram_the_cpu_reaches(chipcell);
    reports_the_memory_of_every_preset(chipcell);
    the_host_sees_and_changes_what_the_cpu_does(chipcell);
    the_drive_light_follows_the_led(chipcell, disk, adf);
    puts("bridge-memory-ok");
    return 0;
}
