// Runs a micro-ROM on the 68010 cycle-exact core (cpuemu_13 row of cputbls, patch 0310) of the real engine and prints what the program left in Chip RAM.
//   usage: p6_cpu010_probe rom max_units [--machine a500|a600|a1200] [--cpu 68000|68010|68020]
// The default is the A500 with p6_config.cpu_model = 68010 (T52); the reset head (prefs_changed_cpu / build_cpufunctbl / set_x_funcs) selects op_smalltbl_13 and m68k_run_1_ce.
// With --cpu 68000 the machine keeps its own 68000: the same program must then fail (the 68000 has none of its instructions). --machine a1200 loads the 68EC020 machine, whose CPU is its own
// unless --cpu names it (only 68020 is valid there).
//
//   line "cpu <model> ..."              : what the engine settled on
//   line "units <n> sentinel <hex>"     : units run and the longword at $1FFC (the program writes $600DF00D when it is done)
//   line "result <i> <hex>"             : 64 longwords from $2000 (the program's results)
//   line "frame <i> <hex>"              : 32 longwords from $2800 (the exception frames the handlers copied)
//   line "state <name> <hex>"           : registers at the end
#include "p6.h"
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

static uint32_t chip_long(uint32_t address)
{
    const uint8_t *p = chipmem_bank.baseaddr + address;
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

static void check(p6_result r, const p6_error &e, const char *what)
{
    if (r != P6_OK) { fprintf(stderr, "%s failed: %d %s\n", what, int(r), e.message); exit(3); }
}

int main(int argc, char **argv)
{
    if (argc < 3) { fprintf(stderr, "usage: %s rom max_units [--machine a500|a600|a1200] [--cpu 68000|68010|68020]\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const int max_units = atoi(argv[2]);
    uint32_t machine = P6_MACHINE_A500;
    uint8_t cpu_model = 2;                 // the 68010 of the A500, unless the machine or --cpu says otherwise
    bool cpu_named = false;
    for (int i = 3; i < argc; ++i) {
        if (!strcmp(argv[i], "--machine") && i + 1 < argc) {
            const char *name = argv[++i];
            machine = !strcmp(name, "a600") ? P6_MACHINE_A600 : !strcmp(name, "a1200") ? P6_MACHINE_A1200 : P6_MACHINE_A500;
        } else if (!strcmp(argv[i], "--cpu") && i + 1 < argc) {
            const int model = atoi(argv[++i]);
            cpu_model = uint8_t(1 + (model - 68000) / 10);
            cpu_named = true;
        } else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 64; }
    }
    if (machine == P6_MACHINE_A1200 && !cpu_named) cpu_model = 0;     // the soldered 68EC020
    p6_error error{P6_ABI_VERSION, sizeof error, {}};
    p6_config config{};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = machine;
    config.cpu_model = cpu_model;
    config.video_standard = P6_VIDEO_PAL;
    config.audio_rate_hz = 48000;
    config.random_seed = 1;
    config.rtc_epoch_seconds = 946684800;
    check(p6_init(&config, &error), error, "p6_init");
    check(p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &error), error, "p6_load");

    p6_input input{};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    int units = 0;
    for (; units < max_units; ++units) {
        p6_frame frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        check(p6_run_frame(&input, 0, &frame, &error), error, "p6_run_frame");
        if (chip_long(0x1FFC) == 0x600DF00Du) { ++units; break; }
    }
    printf("cpu %d fpu %d mmu %d 24bit %d cycle_exact %d memory_cycle_exact %d clock_multiplier %d\n", currprefs.cpu_model,
           currprefs.fpu_model, currprefs.mmu_model, int(currprefs.address_space_24), int(currprefs.cpu_cycle_exact),
           int(currprefs.cpu_memory_cycle_exact), currprefs.cpu_clock_multiplier);
    printf("units %d sentinel %08x\n", units, chip_long(0x1FFC));
    for (int i = 0; i < 64; ++i) printf("result %d %08x\n", i, chip_long(0x2000 + 4 * i));
    for (int i = 0; i < 32; ++i) printf("frame %d %08x\n", i, chip_long(0x2800 + 4 * i));
    printf("state vbr %08x\n", regs.vbr);
    printf("state sfc %08x\n", regs.sfc);
    printf("state dfc %08x\n", regs.dfc);
    printf("state loop_mode %08x\n", regs.loop_mode);
    printf("state pc %08x\n", m68k_getpc());
    printf("state a7 %08x\n", m68k_areg(regs, 7));
    printf("state instructions %" PRIu64 "\n", uint64_t(regs.instruction_cnt));
    p6_unload(&error);
    p6_shutdown(&error);
    return 0;
}
