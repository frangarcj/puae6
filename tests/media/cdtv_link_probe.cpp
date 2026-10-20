// Original stock controller, real common CD backend; only immutable bytes are synthetic.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "p6_state_access.h"
#include "media/cd_device.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <vector>
static p6_error error{P6_ABI_VERSION, sizeof error};
static uint64_t digest = 1469598103934665603ull;
static bool fail_read;
static unsigned reads;
static void observe(const void *ptr, size_t size, uint64_t &hash = digest)
{
    const auto *p = static_cast<const uint8_t *>(ptr);
    while (size--) hash = (hash ^ *p++) * 1099511628211ull;
}
static uint64_t frames(unsigned count = 1)
{
    uint64_t fingerprint = 1469598103934665603ull;
    p6_input in{P6_ABI_VERSION, sizeof in}; p6_frame out{P6_ABI_VERSION, sizeof out};
    while (count--) {
        assert(p6_run_frame(&in, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &out, &error) == P6_OK);
        observe(out.video, out.video_bytes); observe(out.audio, out.audio_frames * 4);
        observe(&out.master_ticks, sizeof out.master_ticks);
        fingerprint = 1469598103934665603ull;
        observe(&out.video_bytes, sizeof out.video_bytes, fingerprint);
        observe(&out.audio_frames, sizeof out.audio_frames, fingerprint);
        observe(out.video, out.video_bytes, fingerprint); observe(out.audio, out.audio_frames * 4, fingerprint);
        observe(&out.master_ticks, sizeof out.master_ticks, fingerprint);
    }
    return fingerprint;
}
static bool image_read(const void *, uint64_t offset, void *out, size_t size, size_t *got)
{
    ++reads; *got = 0; if (fail_read) return false;
    for (size_t n = 0; n < size; ++n) static_cast<uint8_t *>(out)[n] = uint8_t((offset + n) * 13);
    *got = size; return true;
}
static void reg_byte(unsigned reg, unsigned value) { put_byte(0xe90000 + reg, value); }
static unsigned reg_byte(unsigned reg) { return get_byte(0xe90000 + reg); }
static void command(std::initializer_list<unsigned> data)
{
    for (auto v : data) reg_byte(0xa1, v);
    frames();
}
static void response(std::initializer_list<unsigned> data)
{
    for (auto v : data) { auto actual = reg_byte(0xa1); assert(actual == v); observe(&actual, sizeof actual); }
}
static void start_dma(unsigned words = 1024)
{
    for (unsigned n = 0; n < 4; ++n) { reg_byte(0x80 + n, words >> (24 - 8 * n)); reg_byte(0x84 + n, 0x20000 >> (24 - 8 * n)); }
    reg_byte(0x43, 0x90); reg_byte(0xe0, 0); frames();
}
int main(int argc, char **argv)
{
    assert(argc == 2); FILE *file = fopen(argv[1], "rb"); assert(file);
    assert(!fseek(file, 0, SEEK_END)); long size = ftell(file); assert(size > 0); rewind(file);
    std::vector<uint8_t> rom(size); assert(fread(rom.data(), 1, rom.size(), file) == rom.size()); fclose(file);
    for (int owner = 0; owner < 2; ++owner) {
        p6_config config{}; config.abi_version = P6_ABI_VERSION; config.struct_size = sizeof config;
        config.machine = P6_MACHINE_A500; config.video_standard = P6_VIDEO_PAL; config.audio_rate_hz = 48000;
        config.random_seed = 1; config.rtc_epoch_seconds = 946684800;
        assert(p6_init(&config, &error) == P6_OK);
        assert(p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK); const uint64_t clean_frame = frames();
        assert(!p6_cdtv_private_start() && !currprefs.cs_cdtvcd && !changed_prefs.cs_cdtvcd);
        using namespace p6_cd;
        Layout layout{}; layout.tracks = 1; layout.leadout = 2;
        layout.track[0] = {0, 0, 2, 0, 2048, 0, Mode::Mode1_2048, 1, 4, false};
        Binding binding{{{0}, 4096, 2048}, image_read, nullptr}; Disc disc;
        assert(disc.bind(layout, &binding, 1) == Result::Ok && device_bind(&disc) == Result::Ok);
        assert(p6_cdtv_private_start() && !p6_cdtv_private_start());
        assert(device_open_count() == 1); frames(2);
        uint64_t bound = 0, written = 0; assert(p6_state_bound(&bound, &error) == P6_OK && bound);
        std::vector<uint8_t> state_bytes(bound);
        assert(p6_state_save(state_bytes.data(), bound, &written, &error) == P6_OK && written <= bound);
        assert(p6_state_load(state_bytes.data(), written, &error) == P6_OK);
        command({0x80, 0}); response({0xaa, 0x55}); command({0, 0}); response({0xaa, 0x55});
        command({0x83, 0, 0, 0, 0, 0, 0});
        for (char c : "MATSHITA0.96") if (c) assert(reg_byte(0xa1) == unsigned(c));
        command({0x84, 0, 8, 0, 0, 0, 0}); command({0x85}); response({8, 0});
        command({0x86}); response({0, 0, 1, 8, 0});
        command({0x8a, 0, 1, 0, 0, 0, 0}); response({0, 0x14, 1, 1, 0, 0, 0, 0});
        command({1, 0, 0, 0, 0, 0, 0}); assert(p6_cdtv_private_status().seek); frames(26);
        assert(!p6_cdtv_private_status().seek);
        for (unsigned n = 0; n < 2052; ++n) put_byte(0x1fffe + n, 0xcc);
        command({2, 0, 0, 1, 0, 1, 0}); start_dma(); frames(2);
        auto state = p6_cdtv_private_status(); assert(state.acr == 0x20800 && !state.words && !state.dma && !state.finished);
        assert((reg_byte(0x41) & 0x30) == 0x30); reg_byte(0xe4, 0); assert(!reg_byte(0x41));
        for (unsigned n = 0; n < 2048; ++n) { unsigned v = get_byte(0x20000 + n); assert(v == uint8_t((2048 + n) * 13)); observe(&v, sizeof v); }
        assert(get_word(0x1fffe) == 0xcccc && get_word(0x20800) == 0xcccc);
        unsigned before_reads = reads; command({2, 0xff, 0xff, 0xff, 0, 1, 0}); start_dma(1);
        assert(p6_cdtv_private_status().error && reads == before_reads);
        reg_byte(0x43, 0x40);
        fail_read = true; put_word(0x20000, 0xbeef);
        command({2, 0, 0, 0, 0, 1, 0}); start_dma(); assert(p6_cdtv_private_status().error && get_word(0x20000) == 0xbeef);
        fail_read = false; reg_byte(0x43, 0x40); assert(!p6_cdtv_private_status().error);
        assert(device_set_inserted(false) == Result::Ok); command({0x86}); assert(p6_cdtv_private_status().error);
        assert(device_set_inserted(true) == Result::Ok); reg_byte(0x43, 0x40);
        assert(p6_cdtv_private_request(0x110) && p6_cdtv_private_request(0)); frames();
        assert(p6_cdtv_private_status().queued >= 2);
        unsigned queued = 0; while (p6_cdtv_private_request(0x81)) assert(++queued <= 100);
        assert(p6_cdtv_private_status().error && !p6_cdtv_private_status().queued);
        reg_byte(0x43, 0x40); command({0x80, 0}); response({0xaa, 0x55}); frames(3);
        assert(p6_shutdown(&error) == P6_OK && !device_open_count()); device_unbind();
        assert(!p6_cdtv_private_request(0x100));
        assert(p6_init(&config, &error) == P6_OK && p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK);
        assert(!currprefs.cs_cdtvcd); assert(frames() == clean_frame); assert(p6_state_bound(&bound, &error) == P6_OK && bound > 123);
        assert(p6_shutdown(&error) == P6_OK);
    }
    printf("real CDTV DMAC commands, DMA, lifecycle and future %016llx\n", (unsigned long long)digest);
}
