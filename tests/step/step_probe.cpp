// STEP01 harness: advances a micro-ROM by N video units through p6_run_frame and prints, for every
// unit boundary, a digest of the complete emulated state. The engine is built with P6_UNIT_TRACE:
//   steps      one p6_run_frame per unit, returning to the caller at every boundary;
//   continuous ONE p6_run_frame that swallows the first N-1 boundaries (p6_unit_skip) and runs on.
// The observer runs at the same instruction boundary in both modes, so identical output means that
// returning to the caller and resuming changed nothing.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "newcpu.h"
#include "video_present.h"

#include <cassert>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

int p6_unit_skip;

namespace {

struct Hash {
    uint64_t value = UINT64_C(1469598103934665603);
    void bytes(const void *data, size_t size)
    {
        const uint8_t *p = static_cast<const uint8_t *>(data);
        for (size_t i = 0; i < size; ++i) { value ^= p[i]; value *= UINT64_C(1099511628211); }
    }
    template <class T> void add(const T &v) { bytes(&v, sizeof v); }
};

struct Record {
    uint32_t pc, opcode, d0, d1, sr;
    int64_t cycles;
    int line, unit_hpos;
    int halted, stopped;
    uint32_t vw, vh, vflags;
    uint64_t frames, passes;   // presentation provider: frame hand-overs and drawing passes so far
    uint64_t digest;
};

std::vector<Record> trace;

uint64_t state_digest()
{
    Hash h;
    for (uae_u32 r : regs.regs) h.add(r);
    MakeSR();
    h.add(m68k_getpc()); h.add(regs.sr); h.add(regs.usp); h.add(regs.isp);
    h.add(regs.ir); h.add(regs.irc); h.add(regs.ird); h.add(regs.opcode);
    h.add(regs.stopped); h.add(regs.halted); h.add(regs.intmask); h.add(regs.lastipl);
    h.add(regs.ipl[0]); h.add(regs.ipl[1]); h.add(regs.ipl_pin);
    h.add(uae_u32(regs.spcflags & ~SPCFLAG_UNIT_END));
    h.add(get_cycles()); h.add(vpos); h.add(agnus_hpos); h.add(linear_vpos);
    h.add(hsync_counter); h.add(vsync_counter);
    h.add(dmacon); h.add(intena); h.add(intreq); h.add(maxvpos); h.add(maxhpos);
    h.add(lof_store); h.add(lof_display);
    for (const ev &e : eventtab) { h.add(e.active); h.add(e.evtime); h.add(e.oldcycles); }
    for (int i = 0; i < ev2_max; ++i) { h.add(eventtab2[i].active); h.add(eventtab2[i].evtime); h.add(eventtab2[i].data); }
    h.bytes(chipmem_bank.baseaddr, chipmem_bank.allocated_size);
    // The presentation state is part of the machine's future: a drawing pass closed too early shows here.
    p6_video_frame_info video{};
    const int have = p6_video_frame_get(&video);
    h.add(have); h.add(video.frame_counter); h.add(video.pass_counter); h.add(video.width); h.add(video.height);
    if (have == 0) h.bytes(video.pixels, size_t(video.bytes));
    return h.value;
}

}  // namespace

extern "C++" void p6_unit_observe(void)
{
    // What T08 will do after every unit: hand the finished raster over. It sits here, at the boundary itself,
    // so both modes present at the same moments and the picture becomes part of the compared state.
    p6_video_present();
    Record r{};
    r.pc = m68k_getpc();
    r.opcode = regs.opcode;
    r.d0 = regs.regs[0];
    r.d1 = regs.regs[1];
    MakeSR();
    r.sr = regs.sr;
    r.cycles = int64_t(get_cycles());
    r.line = vpos;
    r.unit_hpos = agnus_hpos;
    r.halted = regs.halted;
    r.stopped = regs.stopped;
    p6_video_frame_info video{};
    if (p6_video_frame_get(&video) == 0) { r.frames = video.frame_counter; r.passes = video.pass_counter; r.vw = video.width; r.vh = video.height; r.vflags = video.flags; }
    r.digest = state_digest();
    trace.push_back(r);
}

namespace {

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

p6_config make_config(uint32_t standard, uint64_t seed)
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

p6_input empty_input()
{
    p6_input in{};
    in.abi_version = P6_ABI_VERSION;
    in.struct_size = sizeof in;
    return in;
}

void check(p6_result r, const char *what)
{
    if (r != P6_OK) { fprintf(stderr, "%s failed: %d\n", what, int(r)); exit(3); }
}

// Every unit must be shorter than two whole frames of the running video standard: a unit that is not
// is a boundary that was lost (the watchdog in emulated time, not wall-clock time).
int64_t unit_limit_cycles()
{
    return int64_t(2) * 313 * 227 * CYCLE_UNIT;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc != 5) { fprintf(stderr, "usage: %s rom units steps|continuous seed\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const int units = atoi(argv[2]);
    const bool continuous = !strcmp(argv[3], "continuous");
    if (!continuous && strcmp(argv[3], "steps")) return 64;
    if (units < 1) return 64;

    p6_error error{P6_ABI_VERSION, sizeof(p6_error), {}};
    const p6_config config = make_config(P6_VIDEO_PAL, strtoull(argv[4], nullptr, 10));
    check(p6_init(&config, &error), "p6_init");
    check(p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &error), "p6_load");

    p6_frame frame{};
    frame.abi_version = P6_ABI_VERSION;
    frame.struct_size = sizeof frame;
    const p6_input input = empty_input();
    uint64_t last_ticks = 0;
    if (continuous) {
        p6_unit_skip = units - 1;
        check(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error), "p6_run_frame");
        if (frame.unit_index != 1) { fprintf(stderr, "unit_index %llu\n", (unsigned long long)frame.unit_index); return 4; }
        last_ticks = frame.master_ticks;
    } else {
        for (int unit = 1; unit <= units; ++unit) {
            const int64_t before = trace.empty() ? 0 : trace.back().cycles;
            check(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error), "p6_run_frame");
            if (frame.unit_index != uint64_t(unit) || frame.master_ticks <= last_ticks) return 4;
            if (int64_t(get_cycles()) - before > unit_limit_cycles()) { fprintf(stderr, "unit %d is too long\n", unit); return 5; }
            last_ticks = frame.master_ticks;
        }
    }
    if (trace.size() != size_t(units)) {
        MakeSR();
        fprintf(stderr, "%zu boundaries observed, %d expected (pc=%06x sr=%04x spcflags=%x halted=%d stopped=%d cycles=%lld vpos=%d)\n",
                trace.size(), units, m68k_getpc(), regs.sr, unsigned(regs.spcflags), regs.halted, regs.stopped,
                (long long)get_cycles(), vpos);
        fprintf(stderr, "  d0=%08x d1=%08x a0=%08x a7=%08x usp=%08x isp=%08x intena=%04x intreq=%04x\n", regs.regs[0], regs.regs[1],
                regs.regs[8], regs.regs[15], regs.usp, regs.isp, intena, intreq);
        fprintf(stderr, "  vectors:");
        for (int a = 0x60; a < 0x70; a += 4) fprintf(stderr, " %08x", (chipmem_bank.baseaddr[a] << 24) | (chipmem_bank.baseaddr[a + 1] << 16) | (chipmem_bank.baseaddr[a + 2] << 8) | chipmem_bank.baseaddr[a + 3]);
        fprintf(stderr, "\n  stack:");
        for (int a = 0x3e8; a < 0x400; a += 2) fprintf(stderr, " %04x", (chipmem_bank.baseaddr[a] << 8) | chipmem_bank.baseaddr[a + 1]);
        fprintf(stderr, "\n");
        return 6;
    }

    for (size_t i = 0; i < trace.size(); ++i) {
        const Record &r = trace[i];
        printf("unit %zu pc=%06x op=%04x d0=%08x d1=%08x sr=%04x line=%d hpos=%d cycles=%" PRId64
               " halted=%d stopped=%d video=%" PRIu64 "/%" PRIu64 " size=%ux%u/%u digest=%016" PRIx64 "\n",
               i + 1, r.pc, r.opcode, r.d0, r.d1, r.sr, r.line, r.unit_hpos, r.cycles, r.halted, r.stopped,
               r.frames, r.passes, r.vw, r.vh, r.vflags, r.digest);
    }
    printf("final digest=%016" PRIx64 " ticks=%" PRIu64 "\n", state_digest(), last_ticks);
    check(p6_unload(&error), "p6_unload");
    check(p6_shutdown(&error), "p6_shutdown");
    return 0;
}
