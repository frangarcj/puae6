// Local component proof; P6SC/P6AU and full native restore remain T67b2.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "events.h"
#include "p6_state_access.h"
#include "cdtv_state.h"
#include "media/cd_device.h"
#include <cassert>
#include <cstdio>
#include <memory>
#include <vector>
static p6_error error{P6_ABI_VERSION, sizeof error};
static unsigned reads;
static bool image_read(const void *, uint64_t at, void *out, size_t size, size_t *got)
{
    ++reads; for (size_t n = 0; n < size; ++n) static_cast<uint8_t *>(out)[n] = uint8_t((at + n) * 13);
    *got = size; return true;
}
static void frames(unsigned count = 1)
{
    p6_input in{P6_ABI_VERSION, sizeof in}; p6_frame out{P6_ABI_VERSION, sizeof out};
    while (count--) assert(p6_run_frame(&in, P6_RENDER_AUDIO, &out, &error) == P6_OK);
}
static void local(const p6_cd::Disc &disc, unsigned mode)
{
    auto original = std::make_unique<p6_cdtv_state>(), candidate = std::make_unique<p6_cdtv_state>(), actual = std::make_unique<p6_cdtv_state>();
    assert(p6_cdtv_state_get(original.get()) && original->callback_mode == mode);
    p6_cdtv_context context{int64_t(get_cycles()), 0, 41179, &disc};
    ev2 slots[ev2_max]; memcpy(slots, eventtab2, sizeof slots);
    const auto callbacks = p6_cdtv_callback_observations(); const unsigned before_reads = reads;
    *candidate = *original; candidate->tp_a ^= 1; candidate->nvram[1] ^= 0xa5;
    candidate->cdrom_command_cnt_in = 1; candidate->input[0] = 1;
    candidate->cdrom_command_cnt_out = 0; candidate->cdrom_command_size_out = 1; candidate->output[0] = 0xaa;
    memset(candidate->queue, 0, sizeof candidate->queue); candidate->queue[0] = 0x81; candidate->request_size = 1;
    candidate->sub_used[0] = 1; candidate->sub[0][0] ^= 0x42; candidate->transfer[0] ^= 0x77;
    assert(p6_cdtv_state_validate(candidate.get(), context) && p6_cdtv_state_apply(candidate.get(), context));
    assert(p6_cdtv_state_get(actual.get()) && !memcmp(actual.get(), candidate.get(), sizeof *actual));
    for (unsigned n = 0; n < 9; ++n) {
        *candidate = *original;
        switch (n) {
        case 0: candidate->request_size = 101; break; case 1: candidate->subcodeoffset = 96; break;
        case 2: candidate->callback_mode = 3; break; case 3: candidate->device.disc_id[0] ^= 1; break;
        case 4: candidate->cdrom_offset |= 1; break; case 5: candidate->sub_used[1] = 2; break;
        case 6: candidate->cdrom_command_cnt_in = 16; break; case 7: candidate->toc.points = 104; break;
        case 8: candidate->device.audio.callbacks ^= 3; break;
        }
        assert(!p6_cdtv_state_validate(candidate.get(), context) && !p6_cdtv_state_apply(candidate.get(), context));
        assert(p6_cdtv_state_get(candidate.get()) && !memcmp(actual.get(), candidate.get(), sizeof *actual));
    }
    auto *bank = &get_mem_bank(0xe90000); map_banks(&chipmem_bank, 0xe9, 1, 0);
    assert(!p6_cdtv_state_apply(original.get(), context)); map_banks(bank, 0xe9, 1, 0);
    assert(p6_cdtv_state_apply(original.get(), context) && p6_cdtv_state_get(actual.get()));
    assert(!memcmp(actual.get(), original.get(), sizeof *actual) && get_cycles() == context.cycle);
    assert(reads == before_reads && p6_cdtv_callback_observations() == callbacks && !memcmp(slots, eventtab2, sizeof slots));
}
int main(int argc, char **argv)
{
    assert(argc == 3 && !strcmp(argv[2], "local")); FILE *f = fopen(argv[1], "rb"); assert(f);
    assert(!fseek(f, 0, SEEK_END)); long size = ftell(f); assert(size > 0); rewind(f);
    std::vector<uint8_t> rom(size); assert(fread(rom.data(), 1, rom.size(), f) == rom.size()); fclose(f);
    p6_config c{P6_ABI_VERSION, sizeof c}; c.machine = P6_MACHINE_A500; c.audio_rate_hz = 48000; c.video_standard = P6_VIDEO_PAL;
    c.random_seed = 1; c.rtc_epoch_seconds = 946684800; assert(p6_init(&c, &error) == P6_OK);
    assert(p6_load({rom.data(), rom.size()}, nullptr, 0, &error) == P6_OK); frames();
    p6_cd::Layout layout{}; layout.tracks = 1; layout.leadout = 32;
    layout.track[0] = {0, 0, 32, 0, 2352, 0, p6_cd::Mode::Audio, 1, 0, false};
    p6_cd::Binding binding{{{1}, 32 * 2352, 1}, image_read, nullptr}; p6_cd::Disc disc;
    assert(disc.bind(layout, &binding, 1) == p6_cd::Result::Ok && p6_cd::device_bind(&disc) == p6_cd::Result::Ok);
    assert(p6_cdtv_private_start()); frames(); local(disc, 0);
    for (unsigned mode = 1; mode <= 2; ++mode) {
        for (auto word : {0x104u, mode == 1 ? 0x110u : 0x111u, 0u, 32u, 0u}) assert(p6_cdtv_private_request(word));
        frames(); local(disc, mode); assert(p6_cdtv_private_request(0x102)); frames(); local(disc, mode);
    }
    uint64_t bound = 9; assert(p6_state_bound(&bound, &error) == P6_ERROR_UNSUPPORTED && bound == 9);
    assert(p6_shutdown(&error) == P6_OK && !p6_cd::device_open_count()); p6_cd::device_unbind();
    puts("CDTV local component roundtrip and transactional refusals ok");
}
