#include "sysconfig.h"
#include "sysdeps.h"
#include "fmv_state.h"
#include "video_present.h"
#include "options.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include "memory.h"
#include "cd32_fmv.h"

#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <vector>

namespace {

constexpr size_t kGuard = 64;
constexpr uint8_t kCanary = 0xd7;

struct Case {
    uint32_t width, height, mpeg_width, mpeg_height, depth, scale;
    const char *name;
};

template<typename T>
void store(uint8_t *at, T value)
{
    std::memcpy(at, &value, sizeof value);
}

template<typename T>
T load(const uint8_t *at)
{
    T value;
    std::memcpy(&value, at, sizeof value);
    return value;
}

bool guarded(const std::vector<uint8_t> &bytes, size_t payload)
{
    for (size_t i = 0; i < kGuard; ++i)
        if (bytes[i] != kCanary || bytes[kGuard + payload + i] != kCanary) return false;
    return true;
}

bool run_case(const Case &c)
{
    auto image = std::make_unique<p6_fmv_genlock_state>();
    std::memset(image.get(), 0, sizeof *image);
    image->allocated = image->active = 1;
    image->width = c.mpeg_width;
    image->height = c.mpeg_height;
    image->depth = c.depth;
    image->border32 = 0x00123456;
    image->border16 = 0x2a55;

    for (uint32_t y = 0; y < c.mpeg_height; ++y) {
        for (uint32_t x = 0; x < c.mpeg_width; ++x) {
            const size_t at = (size_t(y) * c.mpeg_width + x) * c.depth;
            if (c.depth == 4)
                store<uint32_t>(image->pixels + at, 0x00010000u | ((y & 0xffu) << 8) | (x & 0xffu));
            else
                store<uint16_t>(image->pixels + at, uint16_t(((y & 31u) << 11) | ((x & 63u) << 5) | ((x + y) & 31u)));
        }
    }

    const uint32_t input_stride = c.width * c.depth + 16;
    const uint32_t output_stride = c.width * c.depth + 24;
    const size_t input_size = size_t(input_stride) * c.height;
    const size_t output_size = size_t(output_stride) * c.height;
    std::vector<uint8_t> input(kGuard + input_size + kGuard, kCanary);
    std::vector<uint8_t> output(kGuard + output_size + kGuard, kCanary);
    uint8_t *amiga = input.data() + kGuard;
    uint8_t *composed = output.data() + kGuard;

    for (uint32_t y = 0; y < c.height; ++y) {
        for (uint32_t x = 0; x < c.width; ++x) {
            const bool opaque = (x + 3 * y) % 17 == 0;
            uint8_t *at = amiga + size_t(y) * input_stride + size_t(x) * c.depth;
            if (c.depth == 4)
                store<uint32_t>(at, opaque ? 0x00ccaa33u : 0x00121110u);
            else
                store<uint16_t>(at, uint16_t((opaque ? 3u : 2u) << 11 | (x & 0x7ffu)));
        }
    }
    const std::vector<uint8_t> input_before = input;
    const p6_fmv_composite_layout layout{c.width, c.height, c.depth, input_stride, output_stride};
    if (p6_fmv_compose(&layout, image.get(), amiga, input_size, composed, output_size) != P6_FMV_COMPOSE_OK)
        return false;

    const int xoffset = (int(c.width / c.scale) - int(c.mpeg_width)) / 2;
    const int yoffset = (int(c.height / c.scale) - int(c.mpeg_height)) / 2;
    for (uint32_t y = 0; y < c.height; ++y) {
        const int sy = int(y / c.scale) - yoffset;
        for (uint32_t x = 0; x < c.width; ++x) {
            const int sx = int(x / c.scale) - xoffset;
            const uint8_t *a = amiga + size_t(y) * input_stride + size_t(x) * c.depth;
            const uint8_t *got = composed + size_t(y) * output_stride + size_t(x) * c.depth;
            if (c.depth == 4) {
                uint32_t expected = image->border32;
                if (sy >= 0 && sy < int(c.mpeg_height) && sx >= 0 && sx < int(c.mpeg_width))
                    expected = load<uint32_t>(image->pixels + (size_t(sy) * c.mpeg_width + size_t(sx)) * 4);
                if (a[0] >= 0x20) expected = load<uint32_t>(a);
                if (load<uint32_t>(got) != expected) return false;
            } else {
                uint16_t expected = uint16_t(image->border16);
                if (sy >= 0 && sy < int(c.mpeg_height) && sx >= 0 && sx < int(c.mpeg_width))
                    expected = load<uint16_t>(image->pixels + (size_t(sy) * c.mpeg_width + size_t(sx)) * 2);
                if ((load<uint16_t>(a) >> 11) >= 3) expected = load<uint16_t>(a);
                if (load<uint16_t>(got) != expected) return false;
            }
        }
        for (uint32_t x = c.width * c.depth; x < output_stride; ++x)
            if (composed[size_t(y) * output_stride + x] != kCanary) return false;
    }
    return input == input_before && guarded(input, input_size) && guarded(output, output_size);
}

bool hostile_rejections()
{
    auto image = std::make_unique<p6_fmv_genlock_state>();
    std::memset(image.get(), 0, sizeof *image);
    image->allocated = 1;
    image->width = image->height = 2;
    image->depth = 4;
    p6_fmv_composite_layout good{8, 8, 4, 40, 48};
    std::vector<uint8_t> input(size_t(good.amiga_stride_bytes) * good.height, 0x10);
    std::vector<uint8_t> output(size_t(good.output_stride_bytes) * good.height, 0xa5);

    auto rejected = [&](int expected, const p6_fmv_composite_layout *layout,
                        const p6_fmv_genlock_state *candidate, uint64_t input_bytes, uint64_t output_bytes) {
        const auto before = output;
        const int result = p6_fmv_compose(layout, candidate, input.data(), input_bytes,
                                          output.data(), output_bytes);
        return result == expected && output == before;
    };
    if (!rejected(P6_FMV_COMPOSE_ARGUMENT, nullptr, image.get(), input.size(), output.size())) return false;
    p6_fmv_genlock_state absent{};
    if (!rejected(P6_FMV_COMPOSE_ARGUMENT, &good, &absent, input.size(), output.size())) return false;
    p6_fmv_composite_layout bad = good;
    bad.width = P6_FMV_AUX_WIDTH + 1;
    if (!rejected(P6_FMV_COMPOSE_GEOMETRY, &bad, image.get(), input.size(), output.size())) return false;
    bad = good; bad.height = 0;
    if (!rejected(P6_FMV_COMPOSE_GEOMETRY, &bad, image.get(), input.size(), output.size())) return false;
    bad = good; bad.pixel_bytes = 2;
    if (!rejected(P6_FMV_COMPOSE_DEPTH, &bad, image.get(), input.size(), output.size())) return false;
    bad = good; bad.amiga_stride_bytes = good.width * good.pixel_bytes - 1;
    if (!rejected(P6_FMV_COMPOSE_GEOMETRY, &bad, image.get(), input.size(), output.size())) return false;
    const uint64_t input_need = uint64_t(good.height - 1) * good.amiga_stride_bytes
                              + uint64_t(good.width) * good.pixel_bytes;
    const uint64_t output_need = uint64_t(good.height - 1) * good.output_stride_bytes
                               + uint64_t(good.width) * good.pixel_bytes;
    if (!rejected(P6_FMV_COMPOSE_CAPACITY, &good, image.get(), input_need - 1, output.size())) return false;
    if (!rejected(P6_FMV_COMPOSE_CAPACITY, &good, image.get(), input.size(), output_need - 1)) return false;
    p6_fmv_genlock_state malformed = *image;
    malformed.width = 353;
    return rejected(P6_FMV_COMPOSE_ARGUMENT, &good, &malformed, input.size(), output.size());
}

bool presentation_handover()
{
    currprefs.gfx_resolution = RES_LORES;
    currprefs.gfx_vresolution = VRES_NONDOUBLE;
    currprefs.cs_cd32fmv = 1;
    if (p6_video_init() != P6_VIDEO_CFG_OK) return false;

    auto image = std::make_unique<p6_fmv_genlock_state>();
    std::memset(image.get(), 0, sizeof *image);
    image->allocated = image->active = 1;
    image->width = 4;
    image->height = 3;
    image->depth = 4;
    image->border32 = 0x00123456;
    for (uint32_t y = 0; y < image->height; ++y)
        for (uint32_t x = 0; x < image->width; ++x)
            store<uint32_t>(image->pixels + (size_t(y) * image->width + x) * 4,
                            0x00010000u | (y << 8) | x);
    if (!p6_fmv_genlock_apply(image.get())) return false;

    auto &vi = adisplays[0].gfxvidinfo;
    auto &input = vi.tempbuffer;
    auto &output = vi.drawbuffer;
    input.outwidth = 32;
    input.outheight = 24;
    vi.inbuffer = &input;
    vi.outbuffer = &output;
    if (!target_graphics_buffer_update(0, true)) return false;
    std::memset(input.bufmem, 0, size_t(input.rowbytes) * input.height_allocated);
    for (uint32_t y = 0; y < 24; ++y)
        for (uint32_t x = 0; x < 32; ++x)
            store<uint32_t>(input.bufmem + size_t(y) * input.rowbytes + size_t(x) * 4,
                            (x + 3 * y) % 17 == 0 ? 0x00ccaa33u : 0x00121110u);

    if (!lockscr(&output, false, false)) return false;
    cd32_fmv_genlock(&input, &output);
    unlockscr(&output, -1, -1);
    if (p6_video_status() != 0 || p6_video_present() != 1 || p6_video_present() != 0) return false;
    p6_video_frame_info frame{};
    if (p6_video_frame_get(&frame) || frame.width != 32 || frame.height != 24
        || frame.stride_bytes != P6_VIDEO_STRIDE_BYTES || frame.frame_counter != 1)
        return false;
    const auto *pixels = static_cast<const uint8_t *>(frame.pixels);
    for (uint32_t y = 0; y < frame.height; ++y) {
        const int sy = int(y / 4) - 1;
        for (uint32_t x = 0; x < frame.width; ++x) {
            const int sx = int(x / 4) - 2;
            const uint32_t amiga = load<uint32_t>(input.bufmem + size_t(y) * input.rowbytes + size_t(x) * 4);
            uint32_t expected = image->border32;
            if (sy >= 0 && sy < 3 && sx >= 0 && sx < 4)
                expected = load<uint32_t>(image->pixels + (size_t(sy) * 4 + size_t(sx)) * 4);
            if ((amiga & 0xff) >= 0x20) expected = amiga;
            if (load<uint32_t>(pixels + size_t(y) * frame.stride_bytes + size_t(x) * 4) != expected)
                return false;
        }
    }
    std::memset(image.get(), 0, sizeof *image);
    if (!p6_fmv_genlock_apply(image.get())) return false;
    graphics_leave();
    return true;
}

} // namespace

int main()
{
    const Case cases[] = {
        {352, 240, 352, 240, 4, 1, "vcd-ntsc-x1-32"},
        {756, 576, 352, 288, 4, 2, "pal-x2-32"},
        {756, 480, 352, 240, 4, 2, "ntsc-x2-32"},
        {32, 24, 4, 3, 4, 4, "bounded-x4-32"},
        {352, 288, 352, 288, 2, 1, "vcd-pal-x1-16"},
        {704, 576, 352, 288, 2, 2, "pal-x2-16"},
    };
    for (const auto &c : cases) {
        if (!run_case(c)) {
            std::cerr << "FMV compositor case failed: " << c.name << '\n';
            return 1;
        }
    }
    if (!hostile_rejections()) {
        std::cerr << "FMV compositor hostile rejection changed output\n";
        return 1;
    }
    if (!presentation_handover()) {
        std::cerr << "FMV composed presentation hand-over failed\n";
        return 1;
    }
    std::cout << "FMV compositor exact/canary/rejection/handover PASS cases="
              << sizeof cases / sizeof cases[0] << '\n';
    return 0;
}
