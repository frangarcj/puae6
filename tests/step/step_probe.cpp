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
#include "video.h"
#include "input.h"
#include "inputdevice.h"
#include "pcm_out.h"

#include <cassert>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <set>
#include <vector>

int p6_unit_skip;

namespace {
std::vector<p6_input> script;   // P6_INPUT_SCRIPT: one packet per unit
std::set<int> reset_at;            // P6_RESET_AT: comma separated 1-based units that start with p6_reset
bool log_restarts;                 // set when the probe reset the machine: the next boundary's log window starts at $1000
bool observer_applies_input;    // continuous mode: the packet of the next unit is applied at the boundary
int script_units;
}  // namespace

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
    uint32_t a0;
    std::vector<uint16_t> logged;   // P6_DUMP_LOG: words the guest appended at (A0)+ since the previous boundary
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
    p6_bridge::present_unit();
    if (getenv("P6_POLL_NOISE")) p6_input_poll_mask ^= 0x5a5a5a5au;   // the mask is write-only for the machine
    // What p6_run_frame does after every unit, here also for the boundaries it swallows: bring Paula up to the boundary and take
    // (and drop) the samples, so that stepped and continuous runs flush at the same ticks and the queue never fills.
    p6_bridge::sync_audio_to_boundary();
    if (p6_unit_skip > 0) { uint64_t taken; p6_bridge::take_audio(&taken, nullptr); }   // this boundary is swallowed
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
    r.a0 = regs.regs[8];
    if (getenv("P6_DUMP_LOG")) {
        static uint32_t previous = 0x1000;
        // A guest that restarts its log (after a reset) starts again at $1000. The end pointer alone cannot say so when two
        // consecutive units are both the first frame after a reset (they end at the same pointer), so the caller says it.
        uint32_t from = r.a0 >= previous && !log_restarts ? previous : 0x1000;
        log_restarts = false;
        for (uint32_t a = from; a + 1 < r.a0 && a < 0x1000 + 2 * 4096; a += 2)
            r.logged.push_back(uint16_t((chipmem_bank.baseaddr[a] << 8) | chipmem_bank.baseaddr[a + 1]));
        previous = r.a0;
    }
    p6_video_frame_info video{};
    if (p6_video_frame_get(&video) == 0) { r.frames = video.frame_counter; r.passes = video.pass_counter; r.vw = video.width; r.vh = video.height; r.vflags = video.flags; }
    r.digest = state_digest();
    trace.push_back(r);
    if (observer_applies_input && int(trace.size()) < script_units) {
        // What p6_run_frame does at the head of the next call, done at the boundary the swallowed call crosses.
        const size_t next = std::min(trace.size(), script.size() - 1);
        p6_input packet = script[next];
        if (trace.size() >= script.size())
            for (p6_port_input &port : packet.ports) port.dx = port.dy = 0;
        p6_bridge::apply_input(packet);
    }
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
    c.audio_rate_hz = getenv("P6_AUDIO_RATE") ? uint32_t(atoi(getenv("P6_AUDIO_RATE"))) : 48000;
    c.random_seed = seed;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

// One line per unit: four hexadecimal key words, then per port: type buttons dx dy.
void load_script(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); exit(2); }
    char line[256];
    while (fgets(line, sizeof line, f)) {
        p6_input in{};
        in.abi_version = P6_ABI_VERSION;
        in.struct_size = sizeof in;
        unsigned k[4], t[2], b[2];
        int dx[2], dy[2];
        if (sscanf(line, "%x %x %x %x %u %u %d %d %u %u %d %d", &k[0], &k[1], &k[2], &k[3], &t[0], &b[0], &dx[0], &dy[0],
                   &t[1], &b[1], &dx[1], &dy[1]) != 12) { fprintf(stderr, "bad script line: %s", line); exit(2); }
        for (int i = 0; i < 4; ++i) in.keys[i] = k[i];
        for (int p = 0; p < 2; ++p) {
            in.ports[p].type = t[p];
            in.ports[p].buttons = b[p];
            in.ports[p].dx = int16_t(dx[p]);
            in.ports[p].dy = int16_t(dy[p]);
        }
        script.push_back(in);
    }
    fclose(f);
    if (script.empty()) { fprintf(stderr, "empty script\n"); exit(2); }
}

p6_input input_for(int unit)
{
    if (script.empty()) {
        p6_input in{};
        in.abi_version = P6_ABI_VERSION;
        in.struct_size = sizeof in;
        return in;
    }
    p6_input packet = script[std::min(size_t(unit - 1), script.size() - 1)];
    if (size_t(unit) > script.size())                 // beyond the script: buttons and keys stay, motion stops
        for (p6_port_input &port : packet.ports) port.dx = port.dy = 0;
    return packet;
}

p6_input empty_input()
{
    p6_input in{};
    in.abi_version = P6_ABI_VERSION;
    in.struct_size = sizeof in;
    return in;
}

// The canonical hash of a delivered picture: the active pixels only, row by row, without the padding of
// the stride or anything outside the active rectangle.
uint64_t picture_hash(const p6_frame &f)
{
    if (!f.video) return 0;
    Hash h;
    for (uint32_t y = f.active.y; y < f.active.y + f.active.height; ++y)
        h.bytes(static_cast<const uint8_t *>(f.video) + size_t(y) * f.video_stride_bytes + size_t(f.active.x) * 4,
                size_t(f.active.width) * 4);
    return h.value;
}

// Every byte of the delivered buffer outside the active rectangle (padding of the stride included) is zero.
bool outside_is_zero(const p6_frame &f)
{
    if (!f.video) return true;
    const uint8_t *base = static_cast<const uint8_t *>(f.video);
    for (uint32_t y = 0; y < f.video_height; ++y)
        for (uint32_t x = 0; x < f.video_stride_bytes / 4; ++x) {
            const bool inside = y >= f.active.y && y < f.active.y + f.active.height && x >= f.active.x &&
                                x < f.active.x + f.active.width;
            if (inside) continue;
            const uint8_t *px = base + size_t(y) * f.video_stride_bytes + size_t(x) * 4;
            if (px[0] | px[1] | px[2] | px[3]) return false;
        }
    return true;
}

// P6_FRAME_DUMP=<directory> writes each delivered picture as frame-<unit>.bin: four little-endian uint32
// (active width, active height, field parity, flags) and then the active rows, width * 4 bytes each.
void dump_frame(const p6_frame &f)
{
    const char *directory = getenv("P6_FRAME_DUMP");
    if (!directory || !f.video) return;
    char path[512];
    snprintf(path, sizeof path, "%s/frame-%03llu.bin", directory, (unsigned long long)f.unit_index);
    FILE *out = fopen(path, "wb");
    if (!out) { perror(path); exit(2); }
    const uint32_t header[4] = {f.active.width, f.active.height, f.field_parity, f.flags};
    fwrite(header, sizeof header, 1, out);
    for (uint32_t y = f.active.y; y < f.active.y + f.active.height; ++y)
        fwrite(static_cast<const uint8_t *>(f.video) + size_t(y) * f.video_stride_bytes + size_t(f.active.x) * 4, 4,
               f.active.width, out);
    fclose(out);
}

// P6_AUDIO_DUMP=<directory> writes each unit's delivered PCM as audio-<unit>.bin (int16 little endian, left then right).
void dump_audio(const p6_frame &f)
{
    const char *directory = getenv("P6_AUDIO_DUMP");
    if (!directory || !f.audio) return;
    char path[512];
    snprintf(path, sizeof path, "%s/audio-%03llu.bin", directory, (unsigned long long)f.unit_index);
    FILE *out = fopen(path, "wb");
    if (!out) { perror(path); exit(2); }
    fwrite(f.audio, 4, size_t(f.audio_frames), out);
    fclose(out);
}

void print_frame(const p6_frame &f)
{
    dump_frame(f);
    dump_audio(f);
    printf("frame %" PRIu64 " ticks=%" PRIu64 " video=%d bytes=%" PRIu64 " format=%u size=%ux%u stride=%u "
           "active=%u,%u,%u,%u aspect=%u/%u cadence=%u/%u parity=%u flags=%u outside=%s pixels=%016" PRIx64 " audio=%s%" PRIu64 "\n",
           f.unit_index, f.master_ticks, f.video != nullptr, f.video_bytes, f.video_format, f.video_width,
           f.video_height, f.video_stride_bytes, f.active.x, f.active.y, f.active.width, f.active.height,
           f.aspect_num, f.aspect_den, f.cadence_num, f.cadence_den, f.field_parity, f.flags,
           outside_is_zero(f) ? "zero" : "dirty", picture_hash(f), f.audio ? "" : "-", f.audio_frames);
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
    if (argc != 5 && argc != 6) {
        fprintf(stderr, "usage: %s rom units steps|continuous|frames|frames-norender seed [pal|ntsc]\n", argv[0]);
        return 64;
    }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const int units = atoi(argv[2]);
    const bool continuous = !strcmp(argv[3], "continuous");
    const bool frames = !strncmp(argv[3], "frames", 6);
    const char *pattern = getenv("P6_AUDIO_PATTERN");   // frames modes: '1' delivers audio in that unit, '0' does not; cycles
    const uint32_t base_render = !strcmp(argv[3], "frames-norender") ? P6_RENDER_AUDIO : (P6_RENDER_VIDEO | P6_RENDER_AUDIO);
    if (!continuous && !frames && strcmp(argv[3], "steps")) return 64;
    if (units < 1) return 64;
    const uint32_t standard = argc == 6 && !strcmp(argv[5], "ntsc") ? P6_VIDEO_NTSC : P6_VIDEO_PAL;
    if (const char *path = getenv("P6_INPUT_SCRIPT")) load_script(path);
    if (const char *list = getenv("P6_RESET_AT"))
        for (const char *c = list; *c;) { reset_at.insert(atoi(c)); while (*c && *c != ',') ++c; if (*c) ++c; }
    script_units = units;
    observer_applies_input = continuous && !script.empty();

    p6_error error{P6_ABI_VERSION, sizeof(p6_error), {}};
    const p6_config config = make_config(standard, strtoull(argv[4], nullptr, 10));
    check(p6_init(&config, &error), "p6_init");
    check(p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &error), "p6_load");

    p6_frame frame{};
    frame.abi_version = P6_ABI_VERSION;
    frame.struct_size = sizeof frame;
    uint64_t last_ticks = 0;
    if (continuous) {
        p6_unit_skip = units - 1;
        const p6_input first = input_for(1);
        check(p6_run_frame(&first, base_render, &frame, &error), "p6_run_frame");
        if (frame.unit_index != 1) { fprintf(stderr, "unit_index %llu\n", (unsigned long long)frame.unit_index); return 4; }
        last_ticks = frame.master_ticks;
    } else {
        for (int unit = 1; unit <= units; ++unit) {
            const int64_t before = trace.empty() ? 0 : trace.back().cycles;
            const p6_input input = input_for(unit);
            uint32_t render = base_render;
            if (pattern && *pattern && pattern[(unit - 1) % strlen(pattern)] == '0') render &= ~P6_RENDER_AUDIO;
            if (reset_at.count(unit)) {   // P6_RESET_AT: a hard reset starts this unit, and so does the guest's log
                check(p6_reset(&error), "p6_reset");
                log_restarts = unit > 1;   // before the first frame the power-on reset is still pending: nothing restarts
            }
            check(p6_run_frame(&input, render, &frame, &error), "p6_run_frame");
            if (getenv("P6_DEBUG_UNITS")) fprintf(stderr, "dbg unit %d cycles=%lld vpos=%d hpos=%d ticks=%llu\n", unit, (long long)get_cycles(), vpos, agnus_hpos, (unsigned long long)frame.master_ticks);
            if (frame.unit_index != uint64_t(unit) || frame.master_ticks <= last_ticks) return 4;
            if (frames) print_frame(frame);
            if (int64_t(get_cycles()) - before > unit_limit_cycles()) { fprintf(stderr, "unit %d is too long\n", unit); return 5; }
            last_ticks = frame.master_ticks;
        }
    }
    if (trace.size() != size_t(units)) {
        for (size_t i = 0; i < trace.size(); ++i)
            fprintf(stderr, "  boundary %zu pc=%06x cycles=%lld line=%d halted=%d\n", i + 1, trace[i].pc, (long long)trace[i].cycles,
                    trace[i].line, trace[i].halted);
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

    for (size_t i = 0; !frames && i < trace.size(); ++i) {
        const Record &r = trace[i];
        printf("unit %zu pc=%06x op=%04x d0=%08x d1=%08x sr=%04x line=%d hpos=%d cycles=%" PRId64
               " halted=%d stopped=%d video=%" PRIu64 "/%" PRIu64 " size=%ux%u/%u a0=%08x digest=%016" PRIx64 "\n",
               i + 1, r.pc, r.opcode, r.d0, r.d1, r.sr, r.line, r.unit_hpos, r.cycles, r.halted, r.stopped,
               r.frames, r.passes, r.vw, r.vh, r.vflags, r.a0, r.digest);
    }
    printf("final digest=%016" PRIx64 " ticks=%" PRIu64 "\n", state_digest(), last_ticks);
    if (getenv("P6_DUMP_LOG"))   // the words a micro-ROM logged during each unit
        for (size_t i = 0; i < trace.size(); ++i) {
            printf("logu %zu:", i + 1);
            for (uint16_t w : trace[i].logged) printf(" %04x", w);
            printf("\n");
        }
    check(p6_unload(&error), "p6_unload");
    check(p6_shutdown(&error), "p6_shutdown");
    return 0;
}
