/* Independent behavioural probe for ports/portable/rtg_device.cpp. */
#include "rtg_device.h"

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {

uint64_t vram_digest()
{
	uint64_t hash = UINT64_C(1469598103934665603);
	const uint8_t *bytes = p6_rtg_vram_data();
	for (size_t i = 0; i < p6_rtg_vram_size(); ++i) {
		hash ^= bytes[i];
		hash *= UINT64_C(1099511628211);
	}
	return hash;
}

bool same_view(const p6_rtg_view &a, const p6_rtg_view &b)
{
	return std::memcmp(&a, &b, sizeof a) == 0;
}

uint32_t expected_pixel(uint32_t format)
{
	switch (format) {
	case P6_RTG_RGBFB_CLUT: return 0x00112233;
	case P6_RTG_RGBFB_R5G6B5PC: return 0x00ff00ff;
	case P6_RTG_RGBFB_A8R8G8B8: return 0x00123456;
	default: assert(false); return 0;
	}
}

uint32_t encoded_pixel(uint32_t format)
{
	switch (format) {
	case P6_RTG_RGBFB_CLUT: return 3;
	case P6_RTG_RGBFB_R5G6B5PC: return 0xf81f;
	case P6_RTG_RGBFB_A8R8G8B8: return 0x7f123456;
	default: assert(false); return 0;
	}
}

void modes_and_formats()
{
	static constexpr uint32_t sizes[3][2] = {{640, 480}, {800, 600}, {1024, 768}};
	static constexpr uint32_t formats[3] = {
		P6_RTG_RGBFB_CLUT, P6_RTG_RGBFB_R5G6B5PC, P6_RTG_RGBFB_A8R8G8B8
	};
	static constexpr uint32_t bytes[3] = {1, 2, 4};
	for (uint32_t index = 0; index < P6_RTG_MODE_COUNT; ++index) {
		p6_rtg_mode mode{};
		assert(p6_rtg_mode_at(index, &mode) == P6_RTG_OK);
		assert(mode.width == sizes[index / 3][0] && mode.height == sizes[index / 3][1]);
		assert(mode.format == formats[index % 3] && mode.bytes_per_pixel == bytes[index % 3]);
	}
	p6_rtg_mode sentinel{1, 2, 3, 4};
	assert(p6_rtg_mode_at(P6_RTG_MODE_COUNT, &sentinel) == P6_RTG_BOUNDS);
	assert(sentinel.width == 1 && sentinel.height == 2 && sentinel.format == 3);
	assert(p6_rtg_mode_at(0, nullptr) == P6_RTG_BAD_ARGUMENT);

	for (uint32_t index = 0; index < P6_RTG_MODE_COUNT; ++index) {
		p6_rtg_mode mode{};
		assert(p6_rtg_mode_at(index, &mode) == P6_RTG_OK);
		p6_rtg_reset();
		if (mode.format == P6_RTG_RGBFB_CLUT) {
			const uint32_t color = 0xaa112233;
			assert(p6_rtg_set_palette(3, 1, &color) == P6_RTG_OK);
		}
		assert(p6_rtg_set_mode(mode.width, mode.height, mode.format) == P6_RTG_OK);
		p6_rtg_view view{};
		assert(p6_rtg_view_get(&view) == P6_RTG_OK && view.active && view.display_enabled);
		assert(view.surface.stride_bytes == mode.width * mode.bytes_per_pixel);
		assert(p6_rtg_fill(&view.surface, 0, 0, 1, 1, encoded_pixel(mode.format)) == P6_RTG_OK);
		assert(p6_rtg_fill(&view.surface, mode.width - 1, mode.height - 1, 1, 1,
			encoded_pixel(mode.format)) == P6_RTG_OK);
		assert(p6_rtg_present() == P6_RTG_OK);
		p6_rtg_frame frame{};
		assert(p6_rtg_frame_get(&frame) == P6_RTG_OK);
		assert(frame.width == mode.width && frame.height == mode.height);
		assert(frame.stride_bytes == P6_RTG_OUTPUT_STRIDE_BYTES && frame.frame_counter == 1);
		assert(frame.pixels[0] == expected_pixel(mode.format));
		assert(frame.pixels[uint64_t(mode.height - 1) * P6_RTG_MAX_WIDTH + mode.width - 1]
			== expected_pixel(mode.format));
		if (mode.width < P6_RTG_MAX_WIDTH)
			assert(frame.pixels[mode.width] == 0);
		if (mode.height < P6_RTG_MAX_HEIGHT)
			assert(frame.pixels[uint64_t(mode.height) * P6_RTG_MAX_WIDTH] == 0);

		assert(p6_rtg_set_display(0) == P6_RTG_OK && p6_rtg_present() == P6_RTG_OK);
		assert(p6_rtg_frame_get(&frame) == P6_RTG_OK && frame.frame_counter == 2);
		assert(frame.pixels[0] == 0);
		assert(p6_rtg_set_display(1) == P6_RTG_OK && p6_rtg_present() == P6_RTG_OK);
		assert(p6_rtg_frame_get(&frame) == P6_RTG_OK && frame.pixels[0] == expected_pixel(mode.format));
		assert(p6_rtg_rebind() == P6_RTG_OK);
	}
}

void byte_order_and_palette()
{
	p6_rtg_reset();
	assert(p6_rtg_set_mode(640, 480, P6_RTG_RGBFB_A8R8G8B8) == P6_RTG_OK);
	p6_rtg_view view{};
	p6_rtg_view_get(&view);
	assert(p6_rtg_fill(&view.surface, 2, 3, 1, 1, 0x7f123456) == P6_RTG_OK);
	const uint64_t argb = uint64_t(3) * view.surface.stride_bytes + 2 * 4;
	uint8_t *memory = p6_rtg_vram_data();
	assert(memory[argb + 0] == 0x7f && memory[argb + 1] == 0x12);
	assert(memory[argb + 2] == 0x34 && memory[argb + 3] == 0x56);

	assert(p6_rtg_set_mode(640, 480, P6_RTG_RGBFB_R5G6B5PC) == P6_RTG_OK);
	p6_rtg_view_get(&view);
	assert(p6_rtg_fill(&view.surface, 0, 0, 1, 1, 0xf81f) == P6_RTG_OK);
	assert(memory[0] == 0x1f && memory[1] == 0xf8);
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame frame{};
	p6_rtg_frame_get(&frame);
	assert(frame.pixels[0] == 0x00ff00ff);
	/* Bit replication, including a value where truncating 255*n/divisor differs. */
	memory[0] = 0x20; memory[1] = 0x08; /* R=1, G=1, B=0 */
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame_get(&frame);
	assert(frame.pixels[0] == 0x00080400);

	assert(p6_rtg_set_mode(640, 480, P6_RTG_RGBFB_CLUT) == P6_RTG_OK);
	p6_rtg_view_get(&view);
	const uint32_t colors[2] = {0xff010203, 0x00040506};
	assert(p6_rtg_set_palette(254, 2, colors) == P6_RTG_OK);
	assert(p6_rtg_fill(&view.surface, 0, 0, 1, 1, 254) == P6_RTG_OK);
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame_get(&frame);
	assert(frame.pixels[0] == 0x00010203);
	assert(p6_rtg_set_palette(255, 2, colors) == P6_RTG_BOUNDS);
	assert(p6_rtg_set_palette(0, 1, nullptr) == P6_RTG_BAD_ARGUMENT);
	assert(p6_rtg_set_palette(256, 0, nullptr) == P6_RTG_OK);
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame_get(&frame);
	assert(frame.pixels[0] == 0x00010203); /* failed writes were transactional */
}

void panning_and_bounds()
{
	p6_rtg_reset();
	assert(p6_rtg_set_mode(640, 480, P6_RTG_RGBFB_A8R8G8B8) == P6_RTG_OK);
	p6_rtg_surface virtual_surface{128, 800 * 4, 800, 600, P6_RTG_RGBFB_A8R8G8B8};
	assert(p6_rtg_fill(&virtual_surface, 10, 20, 1, 1, 0xffabcdef) == P6_RTG_OK);
	assert(p6_rtg_fill(&virtual_surface, 649, 499, 1, 1, 0xff123456) == P6_RTG_OK);
	assert(p6_rtg_set_panning(&virtual_surface, 10, 20) == P6_RTG_OK);
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame frame{};
	p6_rtg_frame_get(&frame);
	assert(frame.pixels[0] == 0x00abcdef);
	assert(frame.pixels[uint64_t(479) * P6_RTG_MAX_WIDTH + 639] == 0x00123456);

	p6_rtg_view before{};
	p6_rtg_view_get(&before);
	assert(p6_rtg_set_panning(&virtual_surface, 161, 20) == P6_RTG_BOUNDS);
	p6_rtg_view after{};
	p6_rtg_view_get(&after);
	assert(same_view(before, after));

	const uint64_t before_bytes = vram_digest();
	p6_rtg_surface too_small_stride{0, 639 * 4, 640, 480, P6_RTG_RGBFB_A8R8G8B8};
	p6_rtg_surface too_large_stride{0, P6_RTG_MAX_STRIDE_BYTES + 1, 640, 480,
		P6_RTG_RGBFB_A8R8G8B8};
	p6_rtg_surface foreign{P6_RTG_VRAM_BYTES, 4, 1, 1, P6_RTG_RGBFB_A8R8G8B8};
	p6_rtg_surface overflow{UINT32_MAX, P6_RTG_MAX_STRIDE_BYTES, 1024, 768,
		P6_RTG_RGBFB_A8R8G8B8};
	assert(p6_rtg_fill(&too_small_stride, 0, 0, 1, 1, 1) == P6_RTG_BOUNDS);
	assert(p6_rtg_fill(&too_large_stride, 0, 0, 1, 1, 1) == P6_RTG_BOUNDS);
	assert(p6_rtg_fill(&foreign, 0, 0, 1, 1, 1) == P6_RTG_BOUNDS);
	assert(p6_rtg_fill(&overflow, 0, 0, 1, 1, 1) == P6_RTG_BOUNDS);
	assert(p6_rtg_fill(&virtual_surface, 800, 600, 0, 0, 1) == P6_RTG_OK);
	assert(p6_rtg_fill(&virtual_surface, 800, 0, 1, 1, 1) == P6_RTG_BOUNDS);
	assert(vram_digest() == before_bytes);

	/* The last populated byte is accessible, but the rest of the Z3 aperture is not. */
	p6_rtg_surface tail{P6_RTG_VRAM_BYTES - 1, 1, 1, 1, P6_RTG_RGBFB_CLUT};
	assert(p6_rtg_fill(&tail, 0, 0, 1, 1, 0xa5) == P6_RTG_OK);
	assert(p6_rtg_vram_data()[P6_RTG_VRAM_BYTES - 1] == 0xa5);
	const uint8_t tail_value = p6_rtg_vram_data()[P6_RTG_VRAM_BYTES - 1];
	assert(p6_rtg_fill(&virtual_surface, 0, 0, 800, 600, 0x11223344) == P6_RTG_OK);
	assert(p6_rtg_vram_data()[P6_RTG_VRAM_BYTES - 1] == tail_value);

	p6_rtg_view stable{};
	p6_rtg_view_get(&stable);
	assert(p6_rtg_set_mode(641, 480, P6_RTG_RGBFB_A8R8G8B8) == P6_RTG_UNSUPPORTED);
	assert(p6_rtg_set_mode(640, 480, 2) == P6_RTG_UNSUPPORTED);
	assert(p6_rtg_set_display(2) == P6_RTG_BAD_ARGUMENT);
	p6_rtg_view_get(&after);
	assert(same_view(stable, after));
}

void overlapping_blits()
{
	p6_rtg_reset();
	p6_rtg_surface surface{0, 8, 8, 4, P6_RTG_RGBFB_CLUT};
	uint8_t *memory = p6_rtg_vram_data();
	for (uint32_t y = 0; y < 4; ++y)
		for (uint32_t x = 0; x < 8; ++x) memory[y * 8 + x] = uint8_t(y * 16 + x);
	assert(p6_rtg_blit(&surface, 0, 0, &surface, 1, 0, 7, 1) == P6_RTG_OK);
	const uint8_t right[] = {0, 0, 1, 2, 3, 4, 5, 6};
	assert(std::memcmp(memory, right, sizeof right) == 0);
	assert(p6_rtg_blit(&surface, 1, 0, &surface, 0, 0, 7, 1) == P6_RTG_OK);
	const uint8_t left[] = {0, 1, 2, 3, 4, 5, 6, 6};
	assert(std::memcmp(memory, left, sizeof left) == 0);

	for (uint32_t y = 0; y < 4; ++y) std::memset(memory + y * 8, int(y), 8);
	assert(p6_rtg_blit(&surface, 0, 0, &surface, 0, 1, 8, 3) == P6_RTG_OK);
	assert(memory[0] == 0 && memory[8] == 0 && memory[16] == 1 && memory[24] == 2);
	assert(p6_rtg_blit(&surface, 0, 1, &surface, 0, 0, 8, 3) == P6_RTG_OK);
	assert(memory[0] == 0 && memory[8] == 1 && memory[16] == 2 && memory[24] == 2);

	p6_rtg_surface other_stride{4, 9, 8, 3, P6_RTG_RGBFB_CLUT};
	const uint64_t before = vram_digest();
	assert(p6_rtg_blit(&surface, 0, 0, &other_stride, 0, 0, 8, 3) == P6_RTG_UNSUPPORTED);
	assert(vram_digest() == before);
	p6_rtg_surface nonoverlap{1024, 9, 8, 3, P6_RTG_RGBFB_CLUT};
	assert(p6_rtg_blit(&surface, 0, 0, &nonoverlap, 0, 0, 8, 3) == P6_RTG_OK);
	assert(std::memcmp(memory, memory + 1024, 8) == 0);
	assert(p6_rtg_blit(&surface, 8, 4, &surface, 8, 4, 0, 0) == P6_RTG_OK);
	assert(p6_rtg_blit(&surface, 7, 0, &surface, 0, 0, 2, 1) == P6_RTG_BOUNDS);
}

void reset_and_rebind()
{
	p6_rtg_reset();
	assert(p6_rtg_vram_size() == P6_RTG_VRAM_BYTES);
	assert(p6_rtg_present() == P6_RTG_BAD_STATE);
	p6_rtg_frame frame{};
	assert(p6_rtg_frame_get(&frame) == P6_RTG_BAD_STATE);
	assert(p6_rtg_frame_get(nullptr) == P6_RTG_BAD_ARGUMENT);
	assert(p6_rtg_set_display(1) == P6_RTG_BAD_STATE);
	assert(p6_rtg_rebind() == P6_RTG_OK);
	p6_rtg_view view{};
	assert(p6_rtg_view_get(&view) == P6_RTG_OK && !view.active);
	std::memset(p6_rtg_vram_data(), 0x5a, p6_rtg_vram_size());
	p6_rtg_reset();
	for (size_t i = 0; i < p6_rtg_vram_size(); ++i) assert(p6_rtg_vram_data()[i] == 0);
}

} // namespace

int main()
{
	static_assert(P6_RTG_VRAM_BYTES == 4194304, "fixed VRAM");
	static_assert(P6_RTG_APERTURE_BYTES == 16777216, "fixed Z3 aperture");
	static_assert(P6_RTG_MODE_COUNT == 9, "closed mode list");
	static_assert(P6_RTG_RGBFB_CLUT == 1 && P6_RTG_RGBFB_R5G6B5PC == 4
		&& P6_RTG_RGBFB_A8R8G8B8 == 6, "WinUAE RGBFTYPE values");
	reset_and_rebind();
	modes_and_formats();
	byte_order_and_palette();
	panning_and_bounds();
	overlapping_blits();
	reset_and_rebind();
	return 0;
}
