/* Semantic RTG state: transactional validation and identical next output. */
#include "rtg_device.h"

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {

alignas(16384) uint8_t saved_vram[P6_RTG_VRAM_BYTES];

uint64_t digest(const void *data, size_t bytes)
{
	uint64_t hash = UINT64_C(1469598103934665603);
	const auto *p = static_cast<const uint8_t *>(data);
	for (size_t i = 0; i < bytes; ++i) {
		hash ^= p[i];
		hash *= UINT64_C(1099511628211);
	}
	return hash;
}

uint64_t output_digest(const p6_rtg_frame &frame)
{
	uint64_t hash = UINT64_C(1469598103934665603);
	for (uint32_t y = 0; y < frame.height; ++y) {
		const uint32_t *row = frame.pixels
			+ uint64_t(y) * frame.stride_bytes / sizeof(uint32_t);
		for (uint32_t x = 0; x < frame.width; ++x) {
			uint32_t value = row[x];
			for (unsigned byte = 0; byte < 4; ++byte) {
				hash ^= uint8_t(value >> (byte * 8));
				hash *= UINT64_C(1099511628211);
			}
		}
	}
	return hash;
}

void expect_refused(const p6_rtg_snapshot &bad, int result)
{
	p6_rtg_snapshot before{}, after{};
	assert(p6_rtg_snapshot_get(&before) == P6_RTG_OK);
	const uint64_t memory = digest(p6_rtg_vram_data(), p6_rtg_vram_size());
	assert(p6_rtg_snapshot_validate(&bad) == result);
	assert(p6_rtg_snapshot_apply(&bad) == result);
	assert(p6_rtg_snapshot_get(&after) == P6_RTG_OK);
	assert(std::memcmp(&before, &after, sizeof before) == 0);
	assert(digest(p6_rtg_vram_data(), p6_rtg_vram_size()) == memory);
}

} // namespace

int main()
{
	static_assert(P6_RTG_SNAPSHOT_VERSION == 1);
	static_assert(offsetof(p6_rtg_snapshot, frame_counter) == 1080);
	static_assert(sizeof(p6_rtg_snapshot) == 1088);

	p6_rtg_reset();
	const uint32_t color = 0x00abcdef;
	assert(p6_rtg_set_palette(7, 1, &color) == P6_RTG_OK);
	assert(p6_rtg_set_mode(640, 480, P6_RTG_RGBFB_CLUT) == P6_RTG_OK);
	p6_rtg_surface surface{128, 800, 800, 600, P6_RTG_RGBFB_CLUT};
	assert(p6_rtg_fill(&surface, 10, 20, 640, 480, 7) == P6_RTG_OK);
	assert(p6_rtg_set_panning(&surface, 10, 20) == P6_RTG_OK);
	assert(p6_rtg_present() == P6_RTG_OK);

	p6_rtg_snapshot saved{};
	assert(p6_rtg_snapshot_get(&saved) == P6_RTG_OK);
	assert(p6_rtg_snapshot_validate(&saved) == P6_RTG_OK);
	assert(saved.frame_counter == 1 && saved.palette[7] == color);
	std::memcpy(saved_vram, p6_rtg_vram_data(), sizeof saved_vram);

	/* Control future immediately after the cut. */
	assert(p6_rtg_present() == P6_RTG_OK);
	p6_rtg_frame frame{};
	assert(p6_rtg_frame_get(&frame) == P6_RTG_OK && frame.frame_counter == 2);
	const uint64_t expected = output_digest(frame);

	/* Poison both semantic state, VRAM and the derived output before restore. */
	assert(p6_rtg_set_mode(1024, 768, P6_RTG_RGBFB_A8R8G8B8) == P6_RTG_OK);
	p6_rtg_view poison{};
	assert(p6_rtg_view_get(&poison) == P6_RTG_OK);
	assert(p6_rtg_fill(&poison.surface, 0, 0, 1024, 768, 0xff123456) == P6_RTG_OK);
	assert(p6_rtg_present() == P6_RTG_OK);
	assert(p6_rtg_snapshot_apply(&saved) == P6_RTG_OK);
	std::memcpy(p6_rtg_vram_data(), saved_vram, sizeof saved_vram);
	assert(p6_rtg_rebind() == P6_RTG_OK);
	assert(p6_rtg_frame_get(&frame) == P6_RTG_BAD_STATE); /* derived cache was discarded */
	assert(p6_rtg_present() == P6_RTG_OK);
	assert(p6_rtg_frame_get(&frame) == P6_RTG_OK && frame.frame_counter == 2);
	assert(output_digest(frame) == expected);

	p6_rtg_snapshot good{};
	assert(p6_rtg_snapshot_get(&good) == P6_RTG_OK);
	p6_rtg_snapshot bad = good;
	bad.version = 2;
	expect_refused(bad, P6_RTG_UNSUPPORTED);
	bad = good; bad.view.active = 2;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.mode.width = 641;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.mode.bytes_per_pixel = 2;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.surface.format = P6_RTG_RGBFB_A8R8G8B8;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.surface.stride_bytes = 639;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.surface.offset = P6_RTG_VRAM_BYTES - 1;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.x_offset = 161;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.view.display_enabled = 2;
	expect_refused(bad, P6_RTG_BAD_STATE);
	bad = good; bad.palette[7] |= 0xff000000;
	expect_refused(bad, P6_RTG_BAD_STATE);

	p6_rtg_reset();
	assert(p6_rtg_snapshot_get(&good) == P6_RTG_OK);
	bad = good; bad.view.mode.width = 640;
	expect_refused(bad, P6_RTG_BAD_STATE);
	assert(p6_rtg_snapshot_get(nullptr) == P6_RTG_BAD_ARGUMENT);
	assert(p6_rtg_snapshot_validate(nullptr) == P6_RTG_BAD_ARGUMENT);
	assert(p6_rtg_snapshot_apply(nullptr) == P6_RTG_BAD_ARGUMENT);
	return 0;
}
