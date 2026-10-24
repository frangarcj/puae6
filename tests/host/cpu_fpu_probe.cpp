// Runs a micro-ROM on the A1200 preset with a 68881 or 68882 (T54, SoftFloat in fpp.cpp) and prints what the program left in Chip RAM.
//   usage: p6_cpufpu_probe rom max_units fpu_model [dump_from dump_bytes]
//   fpu_model is 0, 68881 or 68882; with 0 the same program must meet the line F exception on every coprocessor instruction.
//
//   line "cpu <model> fpu <model> ..."   : what the engine settled on
//   line "units <n> sentinel <hex>"      : units run and the longword at $1FFC (the program writes $600DF00D when it is done)
//   line "mem <address> <hex bytes>"     : 32 bytes of Chip RAM per line, from dump_from for dump_bytes (default $10000, 0x8000)
//   line "fpr <n> <high> <low>"          : FP0..FP7 at the end (sign/exponent, 64 bit significand)
//   line "fpcr/fpsr/fpiar <hex>"         : control registers at the end
//   line "state pc|a7|instructions <n>"  : CPU at the end
// The chip RAM words are read straight from the bank (not through the bus), so nothing is changed by looking.
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
    if (argc != 4 && argc != 6) { fprintf(stderr, "usage: %s rom max_units fpu_model [dump_from dump_bytes]\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const int max_units = atoi(argv[2]);
    const uint32_t fpu = uint32_t(strtoul(argv[3], nullptr, 10));
    const uint32_t from = argc == 6 ? uint32_t(strtoul(argv[4], nullptr, 0)) : 0x10000, bytes = argc == 6 ? uint32_t(strtoul(argv[5], nullptr, 0)) : 0x8000;
    p6_error error{P6_ABI_VERSION, sizeof error, {}};
    p6_config config{};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = P6_MACHINE_A1200;
    config.video_standard = P6_VIDEO_PAL;
    config.audio_rate_hz = 48000;
    config.random_seed = 1;
    config.rtc_epoch_seconds = 946684800;
    config.fpu_model = fpu;
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
    printf("cpu %d fpu %d mmu %d 24bit %d cycle_exact %d fpu_mode %d\n", currprefs.cpu_model, currprefs.fpu_model, currprefs.mmu_model,
           int(currprefs.address_space_24), int(currprefs.cpu_cycle_exact), currprefs.fpu_mode);
    printf("units %d sentinel %08x\n", units, chip_long(0x1FFC));
    for (uint32_t at = from; at < from + bytes; at += 32) {
        printf("mem %x ", at);
        for (uint32_t i = 0; i < 32; ++i) printf("%02x", chipmem_bank.baseaddr[at + i]);
        printf("\n");
    }
    if (fpu) {
        for (int i = 0; i < 8; ++i) printf("fpr %d %04x %016" PRIx64 "\n", i, regs.fp[i].fpx.high, uint64_t(regs.fp[i].fpx.low));
        printf("fpcr %08x\nfpsr %08x\nfpiar %08x\n", regs.fpcr, regs.fpsr, regs.fpiar);
    }
    for (int i = 0; i < 8; ++i) printf("state d%d %08x\n", i, m68k_dreg(regs, i));
    printf("state pc %08x\n", m68k_getpc());
    printf("state a7 %08x\n", m68k_areg(regs, 7));
    printf("state instructions %" PRIu64 "\n", uint64_t(regs.instruction_cnt));
    p6_unload(&error);
    p6_shutdown(&error);
    return 0;
}
