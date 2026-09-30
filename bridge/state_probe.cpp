// State probe (T14b): one canonical digest per subsystem of the machine, computed field by field from what can be reached outside the
// engine, for the tests that will prove a restore (T15) and for comparing a reload with a clean process. It is a test tool, not part
// of the libraries: the CMake target p6_state_probe compiles it with the engine's headers.
//
// Canonical means: every value is written by name, in a fixed order, as a little-endian integer of a fixed width; no struct is hashed
// as memory (padding), no pointer or address is hashed, and what is scheduled (not what a dead event left behind) is what counts.
// Each digest is SHA-256 over "p6/probe/v1/<subsystem>" and a zero byte, then those bytes, so it cannot be mistaken for the digest of
// another subsystem, for an envelope hash (docs/adr/0009-state-format.md) or for the digest of a Waterbox snapshot.
//
// What a subsystem's digest does NOT see is the state of the engine that is file-local or function-local (the CIA registers, the floppy
// mechanics, Denise's input ring and its cursors, the copper, the audio filter, the event allocator...): manifests/state-inventory.json
// says, per component, whether a probe covers it or waits for an accessor (T15).
//
//   p6_state_probe ROM [--units N] [--every K] [--loads L] [--ntsc] [--port N:type:buttons:dx:dy]... [--dump SUBSYSTEM] [--list]
// Output, one line per digest: "digest UNIT SUBSYSTEM HEX BYTES", after "pass K" when there is more than one load. --dump also prints the
// first 4 KiB of the canonical bytes of one subsystem ("bytes UNIT SUBSYSTEM HEX"), to find what differs between two digests.
#include "p6.h"
#include "session.h"
#include "sha256.h"
#include "rtc_time.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "events.h"
#include "memory.h"
#include "custom.h"
#include "newcpu.h"
#include "video_present.h"
#include "inputdevice.h"

#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern uae_u8 *save_audio(int nr, size_t *len, uae_u8 *dstptr);       // audio.cpp: compiled in the profile, reads only
extern uae_u8 *save_input(size_t *len, uae_u8 *dstptr);               // inputdevice.cpp: the same, it saves pot_cap

namespace {

struct Canon {
    std::vector<uint8_t> bytes;
    template <class T> Canon &put(T value)
    {
        uint64_t v = uint64_t(static_cast<typename std::make_unsigned<T>::type>(value));
        for (size_t i = 0; i < sizeof(T); ++i) bytes.push_back(uint8_t(v >> (8 * i)));
        return *this;
    }
    Canon &raw(const void *data, size_t size)
    {
        const uint8_t *p = static_cast<const uint8_t *>(data);
        bytes.insert(bytes.end(), p, p + size);
        return *this;
    }
};

struct Probe {
    const char *subsystem;
    void (*collect)(Canon &);
};

void cpu(Canon &c)
{
    for (uae_u32 r : regs.regs) c.put(r);                  // D0-D7, A0-A7
    MakeSR();
    c.put(uae_u32(m68k_getpc())).put(regs.sr).put(regs.usp).put(regs.isp);
    c.put(regs.ir).put(regs.irc).put(regs.ird).put(regs.opcode).put(regs.db).put(regs.write_buffer).put(regs.read_buffer);
    c.put(regs.stopped).put(regs.halted).put(regs.intmask).put(regs.lastipl);
    c.put(regs.ipl[0]).put(regs.ipl[1]).put(regs.ipl_pin).put(regs.ipl_pin_p);
    c.put(int64_t(regs.ipl_evt)).put(int64_t(regs.ipl_evt_pre)).put(regs.ipl_evt_pre_mode);
    c.put(int64_t(regs.ipl_pin_change_evt)).put(int64_t(regs.ipl_pin_change_evt_p));
    c.put(uae_u32(regs.spcflags & ~SPCFLAG_UNIT_END));     // the unit-end request is how the boundary is reached, not machine state
}

void scheduler(Canon &c)
{
    c.put(int64_t(get_cycles()));
    // Only what is scheduled is state: the times of an event that is not active are what its last firing left behind.
    for (const ev &e : eventtab) {
        c.put(uint8_t(e.active != 0));
        if (e.active) c.put(int64_t(e.evtime)).put(int64_t(e.oldcycles));
    }
    for (int i = 0; i < ev2_max; ++i) {
        c.put(uint8_t(eventtab2[i].active != 0));
        if (eventtab2[i].active) c.put(int64_t(eventtab2[i].evtime)).put(uint64_t(eventtab2[i].data));
    }
}

void ram(Canon &c)
{
    c.put(uint32_t(chipmem_bank.allocated_size)).raw(chipmem_bank.baseaddr, chipmem_bank.allocated_size);
    c.put(uint32_t(bogomem_bank.allocated_size));
    if (bogomem_bank.baseaddr) c.raw(bogomem_bank.baseaddr, bogomem_bank.allocated_size);
}

void rtc(Canon &c)
{
    p6_rtc_state s{};
    const int status = p6_rtc_save(&s);
    c.put(int32_t(status));
    if (status == 0)
        c.put(s.clock.seconds).put(s.clock.ticks_per_second).put(s.clock.subsecond).put(s.clock.seed).put(s.clock.version).put(s.cycle);
    c.put(int32_t(p6_rtc_status()));
}

void chipset(Canon &c)
{
    // The beam and the registers a CPU or a DMA slot can read back; Denise's input ring is left out until its cursors have an accessor.
    c.put(int32_t(vpos)).put(int32_t(linear_vpos)).put(agnus_hpos).put(hsync_counter).put(vsync_counter);
    c.put(dmacon).put(intena).put(intreq).put(adkcon).put(clxdat);
    c.put(uint8_t(lof_store)).put(uint8_t(lof_display));
    c.put(int32_t(maxvpos)).put(int32_t(maxhpos)).put(beamcon0).put(new_beamcon0).put(htotal).put(vtotal);
    for (int i = 0; i < 256; ++i) c.put(custom_storage[i].value);        // the last value written to each register (not its writer's pc)
}

void audio(Canon &c)
{
    // What the upstream audio serializer knows of each channel (state, volume, length, period, data, pointers, event time): a subset.
    for (int channel = 0; channel < 4; ++channel) {
        uae_u8 buffer[128] = {};
        size_t length = 0;
        save_audio(channel, &length, buffer);
        c.put(uint32_t(length)).raw(buffer, length);
    }
}

void input(Canon &c)
{
    c.put(potgo_value);
    // pot_cap, the charge model of the pot lines, is all that save_input writes in the profile.
    uae_u8 buffer[64] = {};
    size_t length = 0;
    save_input(&length, buffer);
    c.put(uint32_t(length)).raw(buffer, length);
}

void video(Canon &c)
{
    // The presentation of the last unit: an output, but a picture that differs shows here.
    p6_video_frame_info info{};
    const int have = p6_video_frame_get(&info);
    // pass_counter counts drawing passes of the process's bookkeeping (one more after a reload of an interlaced guest, same pictures): not state.
    c.put(int32_t(have)).put(info.frame_counter).put(info.width).put(info.height).put(info.flags);
    if (have == 0) c.raw(info.pixels, size_t(info.bytes));
}

void bridge(Canon &c)
{
    const p6_session::Session &s = p6_session::session;
    c.put(int32_t(s.state)).put(s.unit_index).put(s.master_ticks).put(s.cycles_seen).put(s.audio_frames_total);
    c.put(uint8_t(s.handed_over)).put(uint8_t(s.reset_queued)).put(uint8_t(s.started)).put(uint8_t(s.have_sequence)).put(s.last_sequence);
    c.put(s.media_queued);
    for (uint32_t i = 0; i < s.media_queued && i < p6_session::Session::kMediaQueue; ++i)
        c.put(s.media_queue[i].type).put(s.media_queue[i].drive).put(s.media_queue[i].media_index);
    for (int32_t index : s.drive_media) c.put(index);
    c.put(s.media_count).raw(s.firmware_sha256, sizeof s.firmware_sha256);
    for (uint32_t i = 0; i < s.media_count && i < P6_MAX_MEDIA; ++i) c.raw(s.media_content_id[i], P6_CONTENT_ID_SIZE);
    c.put(s.config.machine).put(s.config.video_standard).put(s.config.audio_rate_hz).put(s.config.flags).put(s.config.random_seed)
        .put(s.config.rtc_epoch_seconds);
}

const Probe kProbes[] = {{"cpu", cpu}, {"scheduler", scheduler}, {"ram", ram}, {"rtc", rtc}, {"chipset", chipset},
                         {"audio", audio}, {"input", input}, {"video", video}, {"bridge", bridge}};

std::string hex(const uint8_t *data, size_t size)
{
    std::string text;
    char two[3];
    for (size_t i = 0; i < size; ++i) { snprintf(two, sizeof two, "%02x", data[i]); text += two; }
    return text;
}

const char *g_dump = nullptr;

void print_digests(uint64_t unit)
{
    for (const Probe &probe : kProbes) {
        Canon canon;
        canon.bytes.assign({'p', '6', '/', 'p', 'r', 'o', 'b', 'e', '/', 'v', '1', '/'});
        canon.raw(probe.subsystem, strlen(probe.subsystem));
        canon.put(uint8_t(0));
        const size_t header = canon.bytes.size();
        probe.collect(canon);
        uint8_t digest[32];
        p6_sha256_hash(canon.bytes.data(), canon.bytes.size(), digest);
        printf("digest %" PRIu64 " %s %s %zu\n", unit, probe.subsystem, hex(digest, 32).c_str(), canon.bytes.size() - header);
        if (g_dump && !strcmp(g_dump, probe.subsystem))        // the first 4 KiB: enough for every field, not for RAM or the raster
            printf("bytes %" PRIu64 " %s %s\n", unit, probe.subsystem, hex(canon.bytes.data() + header, std::min<size_t>(canon.bytes.size() - header, 4096)).c_str());
    }
}

bool read_file(const char *path, std::vector<uint8_t> *data)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return false; }
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data->insert(data->end(), buffer, buffer + n);
    fclose(f);
    return true;
}

int fail(const char *what, p6_result result, const p6_error &error)
{
    fprintf(stderr, "%s failed (%d): %s\n", what, int(result), error.message);
    return 1;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--list")) {
        for (const Probe &probe : kProbes) printf("%s\n", probe.subsystem);
        return 0;
    }
    const char *rom_path = nullptr;
    uint64_t units = 3, every = 1, loads = 1;
    uint32_t standard = P6_VIDEO_PAL;
    p6_port_input ports[2] = {{P6_PORT_JOYSTICK, 0, 0, 0, 0}, {P6_PORT_JOYSTICK, 0, 0, 0, 0}};
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--units") && i + 1 < argc) units = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--every") && i + 1 < argc) every = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--loads") && i + 1 < argc) loads = strtoull(argv[++i], nullptr, 10);
        else if (!strcmp(argv[i], "--ntsc")) standard = P6_VIDEO_NTSC;
        else if (!strcmp(argv[i], "--dump") && i + 1 < argc) g_dump = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) {
            unsigned port, type, buttons;
            int dx, dy;
            if (sscanf(argv[++i], "%u:%u:%u:%d:%d", &port, &type, &buttons, &dx, &dy) != 5 || port > 1) return 64;
            ports[port] = {type, buttons, int16_t(dx), int16_t(dy), 0};
        } else if (!rom_path && argv[i][0] != '-') rom_path = argv[i];
        else { fprintf(stderr, "usage: %s ROM [--units N] [--every K] [--loads L] [--ntsc] [--port N:type:buttons:dx:dy]... | --list\n", argv[0]); return 64; }
    }
    if (!rom_path || !every || !loads) return 64;
    std::vector<uint8_t> rom;
    if (!read_file(rom_path, &rom)) return 1;

    p6_config config = {};
    config.abi_version = P6_ABI_VERSION;
    config.struct_size = sizeof config;
    config.machine = P6_MACHINE_A500;
    config.video_standard = standard;
    config.audio_rate_hz = 48000;
    config.random_seed = 1;
    config.rtc_epoch_seconds = 946684800;
    p6_error error{P6_ABI_VERSION, sizeof error, {}};
    p6_result result = p6_init(&config, &error);
    if (result != P6_OK) return fail("p6_init", result, error);
    for (uint64_t pass = 1; pass <= loads; ++pass) {
        if (loads > 1) printf("pass %" PRIu64 "\n", pass);
        result = p6_load(p6_buffer{rom.data(), rom.size()}, nullptr, 0, &error);
        if (result != P6_OK) return fail("p6_load", result, error);
        print_digests(0);
        p6_input input = {};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        input.ports[0] = ports[0];
        input.ports[1] = ports[1];
        for (uint64_t unit = 1; unit <= units; ++unit) {
            p6_frame frame = {};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            result = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
            if (result != P6_OK) return fail("p6_run_frame", result, error);
            if (unit % every == 0 || unit == units) print_digests(unit);
        }
        result = p6_unload(&error);
        if (result != P6_OK) return fail("p6_unload", result, error);
    }
    p6_shutdown(nullptr);
    return 0;
}
