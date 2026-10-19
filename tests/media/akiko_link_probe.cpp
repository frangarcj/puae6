// Complete native engine; only the immutable CD bytes are a fixture.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "akiko.h"
#include "cd_device.h"
#include <cassert>
#include <cstdio>
#include <vector>

static bool image_read(const void *, uint64_t at, void *out, size_t length, size_t *got)
{
    auto *bytes = static_cast<uint8_t *>(out);
    for (size_t n = 0; n < length; ++n) bytes[n] = uint8_t((at + n) * 13);
    *got = length; return true;
}
static void utilities()
{
    assert(msf2lsn(0x000200) == 0 && lsn2msf(0) == 0x000200);
    assert(msf2lsn(0x010000) == 4350 && lsn2msf(4350) == 0x010000);
    uint8_t bcd[3]; tolongbcd(bcd, 0x12343b);
    assert(bcd[0] == 0x18 && bcd[1] == 0x52 && bcd[2] == 0x59);
    assert(fromlongbcd(bcd) == 0x12343b);
    uint8_t pw[96]{}, interleaved[96], restored[96];
    memset(pw, 0xff, 12); sub_to_interleaved(pw, interleaved);
    for (auto v : interleaved) assert(v == 0x80);
    sub_to_deinterleaved(interleaved, restored); assert(!memcmp(pw, restored, 96));
}
static void device()
{
    using namespace p6_cd;
    Layout layout{}; layout.tracks = 1; layout.leadout = 2;
    layout.track[0] = {0, 0, 2, 0, 2048, 0, Mode::Mode1_2048, 1, 4, false};
    Binding binding{{{0}, 4096, 2048}, image_read, nullptr};
    Disc disc; assert(disc.bind(layout, &binding, 1) == Result::Ok);
    assert(device_bind(&disc) == Result::Ok && sys_command_open(0) == 1);
    uint8_t data[2048]; assert(sys_command_cd_read(0, data, 1, 1));
    for (size_t n = 0; n < sizeof data; ++n) assert(data[n] == uint8_t((2048 + n) * 13));
    cd_toc_head toc{}; assert(sys_command_cd_toc(0, &toc) && toc.lastaddress == 2);
    sys_command_close(0); device_unbind();
}
static void c2p()
{
    // Thirty-two packed pixels 0..31, converted to five nonzero bitplanes.
    currprefs.cs_cd32c2p = true;
    map_banks(&akiko_bank, AKIKO_BASE >> 16, 1, 0);
    assert(get_long(AKIKO_BASE) == 0xc0cacafe);
    for (uint32_t n = 0; n < 8; ++n) {
        const uint32_t p = n * 4;
        put_long(AKIKO_BASE + 0x38, (p << 24) | ((p + 1) << 16) | ((p + 2) << 8) | (p + 3));
    }
    const uint32_t expected[] = {0x55555555, 0x33333333, 0x0f0f0f0f, 0x00ff00ff, 0x0000ffff, 0, 0, 0};
    for (auto plane : expected) assert(get_long(AKIKO_BASE + 0x38) == plane);
    currprefs.cs_cd32c2p = false;
}
int main(int argc, char **argv)
{
    assert(argc == 2); FILE *file = fopen(argv[1], "rb"); assert(file);
    assert(fseek(file, 0, SEEK_END) == 0); const long bytes = ftell(file);
    assert(bytes > 0 && fseek(file, 0, SEEK_SET) == 0);
    std::vector<uint8_t> rom(size_t(bytes), 0); assert(fread(rom.data(), 1, rom.size(), file) == rom.size()); fclose(file);
    utilities(); device();
    for (int session = 0; session < 2; ++session) {
        p6_config config{}; config.abi_version = P6_ABI_VERSION; config.struct_size = sizeof config;
        config.machine = P6_MACHINE_A1200; config.video_standard = P6_VIDEO_PAL;
        config.audio_rate_hz = 48000; config.random_seed = 1; config.rtc_epoch_seconds = 946684800;
        p6_error error{P6_ABI_VERSION, sizeof error};
        assert(p6_init(&config, &error) == P6_OK);
        assert(p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK);
        p6_input input{P6_ABI_VERSION, sizeof input}; p6_frame frame{P6_ABI_VERSION, sizeof frame};
        assert(p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error) == P6_OK);
        assert(frame.master_ticks && frame.audio_frames && frame.video_bytes);
        c2p();
        assert(p6_reset(&error) == P6_OK);
        assert(p6_run_frame(&input, P6_RENDER_AUDIO, &frame, &error) == P6_OK);
        assert(frame.master_ticks && frame.audio_frames);
        assert(p6_shutdown(&error) == P6_OK);
    }
    puts("real Akiko link, MMIO/C2P and A1200 reload/frame ok");
}
