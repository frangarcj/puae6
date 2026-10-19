// Drives p6_init / p6_load / p6_unload / p6_shutdown against the real engine with a micro-ROM.
// The probe inspects engine state directly (it is test code); the bridge sources never call
// m68k_go_unit for the frame boundary except through p6_run_frame (T07c).
#include "p6.h"
#include <string.h>
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
    c = make_config(); c.machine = 0;                    assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.machine = 9;                    assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // T50 added 4 to 8: the first number that is no machine
    c = make_config(); c.machine = 0xffffffffu;          assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.machine = P6_MACHINE_A600;      assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);   // accepted, not UNSUPPORTED
    c = make_config(); c.machine = P6_MACHINE_A1200;     assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    for (uint32_t machine : {P6_MACHINE_A500OG, P6_MACHINE_A500PLUS, P6_MACHINE_A1000, P6_MACHINE_A2000OG, P6_MACHINE_A2000}) {      // T50: every preset is accepted
        c = make_config(); c.machine = machine;          assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        c = make_config(); c.machine = machine; c.fpu_model = 68881;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // all of them have a 68000
    }
    // The memory of the A1000 is its preset's; the ECS Agnus of the A500+ and the A2000 takes 2 MiB of Chip, the OCS one of the A500 OG and the A2000 OG does not.
    c = make_config(); c.machine = P6_MACHINE_A1000; c.chip_kib = 1024;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "memory of its preset"));
    c = make_config(); c.machine = P6_MACHINE_A1000; c.chip_kib = 512; c.slow_kib = P6_MEMORY_NONE; c.fast_kib = P6_MEMORY_NONE;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.machine = P6_MACHINE_A1000; c.slow_kib = 512;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.machine = P6_MACHINE_A1000; c.fast_kib = 1024;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    for (uint32_t machine : {P6_MACHINE_A500PLUS, P6_MACHINE_A2000}) {
        c = make_config(); c.machine = machine; c.chip_kib = 2048; c.fast_kib = 8192;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    for (uint32_t machine : {P6_MACHINE_A500OG, P6_MACHINE_A2000OG}) {
        c = make_config(); c.machine = machine; c.chip_kib = 2048;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
        c = make_config(); c.machine = machine; c.chip_kib = 1024; c.slow_kib = 1536;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    c = make_config(3);                                  assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.audio_rate_hz = 22050;          assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.flags = 2;                      assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);   // P6_CONFIG_FLOPPY_WRITES (1) is the only flag
    c = make_config(); c.flags = P6_CONFIG_FLOPPY_WRITES; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.reserved7[0] = 1;               assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    c = make_config(); c.reserved7[3] = 1;               assert(p6_init(&c, &e) == P6_ERROR_BAD_ARGUMENT);
    for (uint8_t mode : {P6_CPU_EXACT, P6_CPU_COMPATIBLE, P6_CPU_NORMAL}) {
        c = make_config(); c.cpu_compatibility = mode; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    c = make_config(); c.cpu_compatibility = 3; assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.cpu_compatibility = P6_CPU_NORMAL; c.cpu_model = 2; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.cpu_compatibility = P6_CPU_COMPATIBLE; c.machine = P6_MACHINE_A1200; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    // The controller of the hard files (T56): the UAE controller or the IDE port of Gayle, available on the A600, A1200 and A4030.
    c = make_config(); c.hdf_controller = 2;             assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "controller"));
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A500OG, P6_MACHINE_A500PLUS, P6_MACHINE_A1000, P6_MACHINE_A2000OG, P6_MACHINE_A2000}) {
        c = make_config(); c.machine = machine; c.hdf_controller = P6_HDF_CONTROLLER_IDE;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "no supported IDE port"));
        c = make_config(); c.machine = machine; c.hdf_controller = P6_HDF_CONTROLLER_UAE;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    for (uint32_t machine : {P6_MACHINE_A600, P6_MACHINE_A1200})
        for (uint8_t controller : {P6_HDF_CONTROLLER_UAE, P6_HDF_CONTROLLER_IDE}) {
            c = make_config(); c.machine = machine; c.hdf_controller = controller; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        }
    // The clock (T52): 0 is the machine's own, 1, 2, 4, 8, 10, 12 and 16 are PUAE5's multipliers on every machine, anything else is refused with the reason.
    for (uint8_t multiplier : {3, 5, 6, 7, 9, 11, 14, 17, 32, 255}) {
        c = make_config(); c.cpu_multiplier = multiplier; assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "cpu multiplier"));
    }
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A600, P6_MACHINE_A1200, P6_MACHINE_A2000})
        for (uint8_t multiplier : {0, 1, 2, 4, 8, 10, 12, 16}) {
            c = make_config(); c.machine = machine; c.cpu_multiplier = multiplier; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        }
    // The CPU (T52): 0 is the machine's own, 68010 is offered on the machines with a 68000, the A1200 has its soldered 68EC020 only, 68030 is restricted to A4030 and anything above 4 is refused with the reason.
    c = make_config(); c.cpu_model = 5;                  assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "cpu model"));
    c = make_config(); c.cpu_model = 4;                  assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "68000"));
    c = make_config(); c.cpu_model = 3;                  assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "68020"));
    c = make_config(); c.machine = P6_MACHINE_A1200; c.cpu_model = 2; assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "A1200"));
    c = make_config(); c.machine = P6_MACHINE_A1200; c.cpu_model = 1; assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "A1200"));
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A600, P6_MACHINE_A500OG, P6_MACHINE_A500PLUS, P6_MACHINE_A1000, P6_MACHINE_A2000OG, P6_MACHINE_A2000})
        for (uint8_t cpu : {0, 1, 2}) {
            c = make_config(); c.machine = machine; c.cpu_model = cpu; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        }
    for (uint8_t cpu : {0, 3}) {
        c = make_config(); c.machine = P6_MACHINE_A1200; c.cpu_model = cpu; assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    // The raster (T72): 0 is the profile's (hires, double lines), resolution 1 to 3 and line mode 1 and 2 are the options, anything above is refused with the reason, on every machine.
    c = make_config(); c.video_resolution = 4;           assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "resolution"));
    c = make_config(); c.video_line_mode = 3;            assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "line mode"));
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A600, P6_MACHINE_A1200})
        for (uint8_t resolution = 0; resolution <= 3; ++resolution)
            for (uint8_t mode = 0; mode <= 2; ++mode) {
                c = make_config(); c.machine = machine; c.video_resolution = resolution; c.video_line_mode = mode;
                assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
            }
    // The floppy speed (T62): 0 is the profile (the real drive), 1 to 5 are the steps (100, 200, 400, 800 %, turbo), anything above is refused with the reason, on every machine.
    c = make_config(); c.floppy_speed = 6;               assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "floppy speed"));
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A600, P6_MACHINE_A1200})
        for (uint8_t speed = 0; speed <= 5; ++speed) {
            c = make_config(); c.machine = machine; c.floppy_speed = speed;
            assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        }
    // Paula's output (T73): zero is the profile; sinc and what the fields do not have are refused with the reason, the rest is accepted on every machine.
    c = make_config(); c.sound_interpolation = 3;        assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED && strstr(e.message, "sinc"));
    c = make_config(); c.sound_interpolation = 6;        assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.sound_separation = 12;          assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.sound_filter = 4;               assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.sound_filter_type = 3;          assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    for (uint32_t machine : {P6_MACHINE_A500, P6_MACHINE_A600, P6_MACHINE_A1200, P6_MACHINE_A500OG, P6_MACHINE_A500PLUS, P6_MACHINE_A1000, P6_MACHINE_A2000OG, P6_MACHINE_A2000}) {
        c = make_config(); c.machine = machine; c.sound_separation = 1; c.sound_interpolation = 5; c.sound_filter = 3; c.sound_filter_type = 2;
        assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
        c = make_config(); c.machine = machine; c.sound_separation = 11; c.sound_interpolation = 1; c.sound_filter = 1; c.sound_filter_type = 1;
        assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    }
    // The FPU (T54, p6_config.fpu_model): a 68020 has the coprocessor interface, a 68000 does not, and the internal FPUs of the 68030/68040 are not machines of the profile yet.
    c = make_config(); c.fpu_model = 1;                  assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.fpu_model = 68040;              assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.fpu_model = 68881;              assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // the A500: a 68000
    c = make_config(); c.machine = P6_MACHINE_A600; c.fpu_model = 68882;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // the A600: a 68000
    c = make_config(); c.machine = P6_MACHINE_A1200; c.fpu_model = 68040;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.machine = P6_MACHINE_A1200; c.fpu_model = 68881;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.machine = P6_MACHINE_A1200; c.fpu_model = 68882;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.machine = P6_MACHINE_A1200; c.fpu_model = 0;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);   // no FPU is the default, and the old reserved32 = 0
    // Memory (T49): the sizes the machine can have are accepted, the others are UNSUPPORTED, and the preset's own sizes are the same config as zero.
    c = make_config(); c.chip_kib = 1024;                assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.chip_kib = 2048;                assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // OCS Agnus: no more than 1 MiB
    c = make_config(); c.chip_kib = 1536;                assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.chip_kib = 256;                 assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.machine = P6_MACHINE_A600; c.chip_kib = 2048; c.fast_kib = 8192;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.machine = P6_MACHINE_A1200; c.fast_kib = 8192; c.slow_kib = 1536;
                                                         assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.machine = P6_MACHINE_A1200; c.slow_kib = 1792;
                                                         assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);              // AGA maps 1.5 MiB of Slow RAM at most
    c = make_config(); c.slow_kib = P6_MEMORY_NONE;      assert(p6_init(&c, &e) == P6_OK && p6_shutdown(&e) == P6_OK);
    c = make_config(); c.slow_kib = 2048;                assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.fast_kib = 3072;                assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.fast_kib = 16384;               assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
    c = make_config(); c.fast_kib = 512;                 assert(p6_init(&c, &e) == P6_ERROR_UNSUPPORTED);
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

static void rejects_invalid_input_without_touching_the_machine(const std::vector<uint8_t> &rom)
{
    p6_error e = make_error();
    p6_config c = make_config();
    assert(p6_init(&c, &e) == P6_OK && load(rom, &e) == P6_OK);
    p6_frame frame{};
    frame.abi_version = P6_ABI_VERSION;
    frame.struct_size = sizeof frame;
    auto good = []() { p6_input in{}; in.abi_version = P6_ABI_VERSION; in.struct_size = sizeof in; return in; };
    struct Case { const char *what; p6_input input; p6_result code; };
    std::vector<Case> cases;
    auto add = [&](const char *what, p6_result code, auto edit) { p6_input in = good(); edit(in); cases.push_back({what, in, code}); };
    add("abi version", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.abi_version = 2; });
    add("struct size", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.struct_size = 8; });
    add("reserved", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.reserved[2] = 1; });
    add("key 0x68", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.keys[3] |= 1u << (0x68 - 96); });
    add("key 0x78 (reset warning)", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.keys[3] |= 1u << (0x78 - 96); });
    add("key 0x7f", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.keys[3] |= 1u << 31; });
    add("unknown port type", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[0].type = 9; });
    add("port reserved", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[1].reserved = 1; });
    add("none with buttons", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[0].buttons = P6_BUTTON_1; });
    add("none with motion", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[1].dy = 3; });
    add("joystick with motion", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[0].type = P6_PORT_JOYSTICK; in.ports[0].dx = 1; });
    add("joystick unknown button", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[1].type = P6_PORT_JOYSTICK; in.ports[1].buttons = 0x80; });
    add("mouse with a direction", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[0].type = P6_PORT_MOUSE; in.ports[0].buttons = P6_BUTTON_UP; });
    add("unknown second mouse buttons", P6_ERROR_BAD_ARGUMENT, [](p6_input &in) { in.ports[1].type = P6_PORT_MOUSE; in.ports[1].buttons = 8; });
    const evt_t before = get_cycles();
    const uint32_t pc_before = regs.pc;
    for (const Case &k : cases) {
        p6_error err = make_error();
        assert(p6_run_frame(&k.input, P6_RENDER_VIDEO, &frame, &err) == k.code);
        assert(err.message[0] != 0 && strlen(err.message) < P6_ERROR_TEXT_CAPACITY);
        assert(get_cycles() == before && regs.pc == pc_before && in_m68k_go == 1);   // nothing ran, nothing was applied
        (void)k.what;
    }
    assert(p6_run_frame(nullptr, P6_RENDER_VIDEO, &frame, &e) == P6_ERROR_BAD_ARGUMENT);
    const p6_input valid = good();
    for (unsigned volume = 101; volume <= 127; ++volume) {
        assert(p6_run_frame(&valid, volume << P6_RENDER_DRIVE_VOLUME_SHIFT, &frame, &e) == P6_ERROR_BAD_ARGUMENT);
        assert(get_cycles() == before && regs.pc == pc_before && in_m68k_go == 1);
    }
    assert(p6_run_frame(&valid, UINT32_C(1) << 7, &frame, &e) == P6_ERROR_BAD_ARGUMENT);
    assert(get_cycles() == before && regs.pc == pc_before && in_m68k_go == 1);
    p6_input full = good();
    full.ports[0].type = P6_PORT_MOUSE; full.ports[0].buttons = P6_BUTTON_1 | P6_BUTTON_3; full.ports[0].dx = -5;
    full.ports[1].type = P6_PORT_JOYSTICK; full.ports[1].buttons = P6_BUTTON_LEFT | P6_BUTTON_RIGHT | P6_BUTTON_UP;  // left+right: neutral
    full.keys[0] = 1u << 0x10;
    assert(p6_run_frame(&full, P6_RENDER_VIDEO, &frame, &e) == P6_OK && frame.unit_index == 1);
    assert(p6_unload(&e) == P6_OK && p6_shutdown(&e) == P6_OK);
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
    rejects_invalid_input_without_touching_the_machine(rom);

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
