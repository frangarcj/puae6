// MED01 harness: runs the floppy micro-ROM of tests/scenarios/disk/disk.py through p6_run_frame with disk A in DF0 from
// power-on and disk B mounted by a media event before one unit, then dumps Chip RAM so that the test can decode what the
// guest saw on the drive lines and through disk DMA.
//   p6_med01_probe ROM pal|ntsc SEED UNITS MOUNT_UNIT A.adf B.adf DUMP
// Output: one line per unit (registers and a digest of the whole machine, so that two processes can be compared), then
// whether the caller's buffers and the registered copies of both images are still what was passed in.
#include "p6.h"
#include "media.h"
#include "sha256.h"
#include "blob_io.h"
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

uint64_t state_digest()
{
    Hash h;
    for (uae_u32 r : regs.regs) h.add(r);
    MakeSR();
    h.add(m68k_getpc()); h.add(regs.sr); h.add(regs.usp); h.add(regs.isp);
    h.add(get_cycles()); h.add(vpos); h.add(agnus_hpos);
    h.add(dmacon); h.add(intena); h.add(intreq);
    for (const ev &e : eventtab) { h.add(e.active); h.add(e.evtime); }
    h.bytes(chipmem_bank.baseaddr, chipmem_bank.allocated_size);
    return h.value;
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

p6_media medium_of(const std::vector<uint8_t> &adf)
{
    p6_media m{};
    m.type = P6_MEDIA_ADF;
    p6_sha256_hash(adf.data(), adf.size(), m.content_id);
    m.image = p6_buffer{adf.data(), adf.size()};
    return m;
}

bool blob_equals(const p6_media &m, const std::vector<uint8_t> &bytes)
{
    char id[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(m.content_id, id);
    p6_blob_handle h = 0;
    p6_blob_info info{};
    if (p6_blob_open(id, "rb", &h) != P6_BLOB_OK || p6_blob_stat(h, &info) != P6_BLOB_OK) return false;
    std::vector<uint8_t> copy(info.bytes);
    size_t items = 0;
    const bool ok = p6_blob_read(h, copy.data(), copy.size(), 1, copy.size(), &items) == P6_BLOB_OK && copy == bytes;
    p6_blob_close(h);
    return ok;
}

bool enqueue(uint32_t type, uint32_t drive, uint32_t index, uint64_t sequence)
{
    p6_media_event e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    e.type = type;
    e.drive = drive;
    e.media_index = index;
    e.sequence = sequence;
    p6_error error{P6_ABI_VERSION, sizeof(p6_error), {}};
    const p6_result r = p6_media_event_enqueue(&e, &error);
    if (r != P6_OK) fprintf(stderr, "enqueue: %d %s\n", int(r), error.message);
    return r == P6_OK;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc != 9) { fprintf(stderr, "usage: %s ROM pal|ntsc SEED UNITS MOUNT_UNIT A.adf B.adf DUMP\n", argv[0]); return 64; }
    const std::vector<uint8_t> rom = read_file(argv[1]);
    const uint32_t standard = !strcmp(argv[2], "ntsc") ? P6_VIDEO_NTSC : P6_VIDEO_PAL;
    const uint64_t seed = strtoull(argv[3], nullptr, 10);
    const int units = atoi(argv[4]), mount_unit = atoi(argv[5]);
    std::vector<uint8_t> a = read_file(argv[6]), b = read_file(argv[7]);
    const std::vector<uint8_t> a0 = a, b0 = b;
    const p6_media media[2] = {medium_of(a), medium_of(b)};

    p6_error error{P6_ABI_VERSION, sizeof(p6_error), {}};
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = P6_MACHINE_A500;
    c.video_standard = standard;
    c.audio_rate_hz = 48000;
    c.random_seed = seed;
    c.rtc_epoch_seconds = 946684800;
    if (p6_init(&c, &error) != P6_OK) { fprintf(stderr, "init: %s\n", error.message); return 3; }
    if (p6_load(p6_buffer{rom.data(), rom.size()}, media, 2, &error) != P6_OK) { fprintf(stderr, "load: %s\n", error.message); return 3; }
    if (!enqueue(P6_MEDIA_EVENT_MOUNT, 0, 0, 1)) return 3;         // disk A is in DF0 at power-on

    p6_input in{};
    in.abi_version = P6_ABI_VERSION;
    in.struct_size = sizeof in;
    for (int unit = 1; unit <= units; ++unit) {
        if (unit == mount_unit && !enqueue(P6_MEDIA_EVENT_MOUNT, 0, 1, 2)) return 3;   // swap to disk B
        p6_frame frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        if (p6_run_frame(&in, 0, &frame, &error) != P6_OK) { fprintf(stderr, "run: %s\n", error.message); return 4; }
        printf("unit %d d0=%08x d1=%08x d2=%08x d3=%08x pc=%06x digest=%016" PRIx64 "\n", unit, regs.regs[0], regs.regs[1],
               regs.regs[2], regs.regs[3], m68k_getpc(), state_digest());
    }
    printf("final digest=%016" PRIx64 "\n", state_digest());
    printf("adf A buffer_unchanged=%d blob_equal=%d\n", int(a == a0), int(blob_equals(media[0], a0)));
    printf("adf B buffer_unchanged=%d blob_equal=%d\n", int(b == b0), int(blob_equals(media[1], b0)));

    FILE *out = fopen(argv[8], "wb");
    if (!out) { perror(argv[8]); return 5; }
    fwrite(chipmem_bank.baseaddr, 1, 0x80000, out);
    fclose(out);
    p6_unload(&error);
    p6_shutdown(&error);
    return 0;
}
