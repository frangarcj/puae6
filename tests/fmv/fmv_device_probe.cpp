// FMV-B1: real CD32 firmware enumerates the authenticated CL450/L64111 board.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

static std::vector<uint8_t> read_file(const char *path)
{
    FILE *file = fopen(path, "rb"); assert(file && fseek(file, 0, SEEK_END) == 0);
    const long size = ftell(file); assert(size > 0 && fseek(file, 0, SEEK_SET) == 0);
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    assert(fread(bytes.data(), 1, bytes.size(), file) == bytes.size());
    fclose(file); return bytes;
}

static p6_error error() { return {P6_ABI_VERSION, sizeof(p6_error), {}}; }
static void ok(p6_result result, const p6_error &e)
{
    if (result != P6_OK) { fprintf(stderr, "result %d: %s\n", int(result), e.message); abort(); }
}
static p6_config config()
{
    p6_config c{}; c.abi_version = P6_ABI_VERSION; c.struct_size = sizeof c;
    c.machine = P6_MACHINE_CD32; c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000; c.random_seed = 1; c.rtc_epoch_seconds = 946684800;
    return c;
}
static bool fmv_mapped()
{
    const char *rom = get_mem_bank(0x200000).name;
    const char *io = get_mem_bank(0x240000).name;
    const char *ram = get_mem_bank(0x280000).name;
    return rom && io && ram && !strcmp(rom, "CD32 FMV ROM") &&
           !strcmp(io, "CD32 FMV IO") && !strcmp(ram, "CD32 FMV RAM");
}
static void run_until_mapped()
{
    p6_input input{P6_ABI_VERSION, sizeof input};
    p6_frame frame{P6_ABI_VERSION, sizeof frame}; auto e = error();
    for (unsigned n = 0; n < 300 && !fmv_mapped(); ++n)
        ok(p6_run_frame(&input, P6_RENDER_AUDIO, &frame, &e), e);
    assert(fmv_mapped());
}

int main(int argc, char **argv)
{
    assert(argc == 4);
    const auto ext = read_file(argv[1]), main = read_file(argv[2]), fmv = read_file(argv[3]);
    assert(ext.size() == 524288 && main.size() == 524288);
    std::vector<uint8_t> firmware; firmware.reserve(ext.size() + main.size());
    firmware.insert(firmware.end(), ext.begin(), ext.end());
    firmware.insert(firmware.end(), main.begin(), main.end());
    const uint8_t *canonical = fmv.data();
    assert(fmv.size() == 262144 || fmv.size() == 524288);
    if (fmv.size() == 524288) assert(!memcmp(canonical, canonical + 262144, 262144));

    // The stock profile stays board-free when no authenticated cartridge owner exists.
    auto c = config(); auto e = error(); ok(p6_init(&c, &e), e);
    ok(p6_load({firmware.data(), firmware.size()}, nullptr, 0, &e), e);
    assert(!currprefs.cs_cd32fmv && !fmv_mapped());
    p6_input input{P6_ABI_VERSION, sizeof input}; p6_frame frame{P6_ABI_VERSION, sizeof frame};
    for (unsigned n = 0; n < 4; ++n) ok(p6_run_frame(&input, P6_RENDER_AUDIO, &frame, &e), e);
    assert(!currprefs.cs_cd32fmv && !fmv_mapped());
    ok(p6_shutdown(&e), e);

    // With the owner, the real CD32 firmware performs Zorro-II Autoconfig at
    // 0x200000. Observe the production banks and their actual MMIO/RAM accessors.
    c = config(); e = error(); ok(p6_init(&c, &e), e);
    ok(p6_cartridge_bind(P6_CARTRIDGE_CD32_FMV, {fmv.data(), fmv.size()}, &e), e);
    ok(p6_load({firmware.data(), firmware.size()}, nullptr, 0, &e), e);
    assert(currprefs.cs_cd32fmv); run_until_mapped();
    for (unsigned n = 0; n < 32; ++n) assert(get_byte(0x200000 + n) == canonical[n]);
    const uae_u16 status = get_word(0x240000);
    fprintf(stderr, "FMV status=%04x\n", status);
    assert((status & 0x0800) == 0x0800 && !(status & 0x3000));
    put_long(0x280100, 0x1234abcd); assert(get_long(0x280100) == 0x1234abcd);

    e = error(); ok(p6_reset(&e), e);
    ok(p6_run_frame(&input, P6_RENDER_AUDIO, &frame, &e), e);
    run_until_mapped();
    for (unsigned n = 0; n < 32; ++n) assert(get_byte(0x200000 + n) == canonical[n]);
    assert(get_long(0x280100) == 0); // hard reset clears cartridge RAM causally
    ok(p6_shutdown(&e), e);
    puts("CD32 FMV Autoconfig/ROM/MMIO/RAM/reset PASS");
}
