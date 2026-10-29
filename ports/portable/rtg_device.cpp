/* Bounded software UAE RTG device primitives.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "rtg_device.h"

#include <cstring>

namespace {

alignas(16384) uint8_t vram[P6_RTG_VRAM_BYTES];
alignas(16384) uint32_t output[P6_RTG_MAX_WIDTH * P6_RTG_MAX_HEIGHT];

struct State {
	p6_rtg_view view{};
	uint32_t palette[P6_RTG_PALETTE_SIZE]{};
	uint64_t frames = 0;
	bool have_frame = false;
};

alignas(16384) State state;

constexpr uint32_t widths[] = {640, 800, 1024};
constexpr uint32_t heights[] = {480, 600, 768};
constexpr uint32_t formats[] = {
	P6_RTG_RGBFB_CLUT, P6_RTG_RGBFB_R5G6B5PC, P6_RTG_RGBFB_A8R8G8B8
};

static_assert(sizeof(vram) == P6_RTG_VRAM_BYTES, "the board has exactly 4 MiB populated");
static_assert(sizeof(output) == P6_RTG_OUTPUT_BYTES, "one bounded XRGB8888 output");
static_assert(sizeof(State) <= 16384, "RTG auxiliary state exceeds its one-group cap");
static_assert(P6_RTG_OUTPUT_BYTES % 16384 == 0, "output occupies whole Darwin groups");
static_assert(P6_RTG_VRAM_BYTES % 16384 == 0, "VRAM occupies whole Darwin groups");

uint32_t bytes_per_pixel(uint32_t format)
{
	switch (format) {
	case P6_RTG_RGBFB_CLUT: return 1;
	case P6_RTG_RGBFB_R5G6B5PC: return 2;
	case P6_RTG_RGBFB_A8R8G8B8: return 4;
	default: return 0;
	}
}

bool fixed_mode(uint32_t width, uint32_t height)
{
	for (uint32_t i = 0; i < 3; ++i)
		if (width == widths[i] && height == heights[i]) return true;
	return false;
}

int validate_surface(const p6_rtg_surface *surface)
{
	if (!surface) return P6_RTG_BAD_ARGUMENT;
	const uint64_t bpp = bytes_per_pixel(surface->format);
	if (!bpp) return P6_RTG_UNSUPPORTED;
	if (!surface->width || !surface->height || surface->width > P6_RTG_MAX_WIDTH
		|| surface->height > P6_RTG_MAX_HEIGHT)
		return P6_RTG_BOUNDS;
	const uint64_t row = uint64_t(surface->width) * bpp;
	if (surface->stride_bytes < row || surface->stride_bytes > P6_RTG_MAX_STRIDE_BYTES)
		return P6_RTG_BOUNDS;
	const uint64_t end = uint64_t(surface->offset)
		+ uint64_t(surface->height - 1) * surface->stride_bytes + row;
	return end <= P6_RTG_VRAM_BYTES ? P6_RTG_OK : P6_RTG_BOUNDS;
}

bool valid_rect(const p6_rtg_surface &surface, uint32_t x, uint32_t y,
	uint32_t width, uint32_t height)
{
	return x <= surface.width && y <= surface.height
		&& width <= surface.width - x && height <= surface.height - y;
}

uint64_t pixel_address(const p6_rtg_surface &surface, uint32_t x, uint32_t y)
{
	return uint64_t(surface.offset) + uint64_t(y) * surface.stride_bytes
		+ uint64_t(x) * bytes_per_pixel(surface.format);
}

void store_pixel(uint8_t *destination, uint32_t format, uint32_t pixel)
{
	switch (format) {
	case P6_RTG_RGBFB_CLUT:
		destination[0] = uint8_t(pixel);
		break;
	case P6_RTG_RGBFB_R5G6B5PC:
		/* The PC form is little-endian in guest memory: gggbbbbb rrrrrggg. */
		destination[0] = uint8_t(pixel);
		destination[1] = uint8_t(pixel >> 8);
		break;
	case P6_RTG_RGBFB_A8R8G8B8:
		/* Picasso96's ARGB word is stored in Amiga byte order. */
		destination[0] = uint8_t(pixel >> 24);
		destination[1] = uint8_t(pixel >> 16);
		destination[2] = uint8_t(pixel >> 8);
		destination[3] = uint8_t(pixel);
		break;
	}
}

uint32_t expand_565(const uint8_t *source)
{
	const uint32_t value = uint32_t(source[0]) | (uint32_t(source[1]) << 8);
	const uint32_t r5 = (value >> 11) & 31;
	const uint32_t g6 = (value >> 5) & 63;
	const uint32_t b5 = value & 31;
	const uint32_t r = (r5 << 3) | (r5 >> 2);
	const uint32_t g = (g6 << 2) | (g6 >> 4);
	const uint32_t b = (b5 << 3) | (b5 >> 2);
	return (r << 16) | (g << 8) | b;
}

uint32_t load_xrgb(const uint8_t *source, uint32_t format)
{
	switch (format) {
	case P6_RTG_RGBFB_CLUT:
		return state.palette[source[0]];
	case P6_RTG_RGBFB_R5G6B5PC:
		return expand_565(source);
	case P6_RTG_RGBFB_A8R8G8B8:
		return (uint32_t(source[1]) << 16) | (uint32_t(source[2]) << 8) | source[3];
	default:
		return 0;
	}
}

int validate_live_view(const p6_rtg_view &view)
{
	if (!view.active) return P6_RTG_OK;
	if (!fixed_mode(view.mode.width, view.mode.height)
		|| view.mode.bytes_per_pixel != bytes_per_pixel(view.mode.format)
		|| view.mode.format != view.surface.format)
		return P6_RTG_BAD_STATE;
	const int valid = validate_surface(&view.surface);
	if (valid != P6_RTG_OK) return P6_RTG_BAD_STATE;
	if (view.x_offset > view.surface.width || view.y_offset > view.surface.height
		|| view.mode.width > view.surface.width - view.x_offset
		|| view.mode.height > view.surface.height - view.y_offset)
		return P6_RTG_BAD_STATE;
	return view.display_enabled <= 1 ? P6_RTG_OK : P6_RTG_BAD_STATE;
}

} // namespace

void p6_rtg_reset(void)
{
	std::memset(vram, 0, sizeof vram);
	std::memset(output, 0, sizeof output);
	state = State{};
}

int p6_rtg_rebind(void)
{
	/* All addresses in semantic state are VRAM offsets. There is nothing to
	 * patch after a process or sandbox move. */
	return validate_live_view(state.view);
}

int p6_rtg_mode_at(uint32_t index, p6_rtg_mode *out)
{
	if (!out) return P6_RTG_BAD_ARGUMENT;
	if (index >= P6_RTG_MODE_COUNT) return P6_RTG_BOUNDS;
	const uint32_t size = index / 3;
	const uint32_t format = formats[index % 3];
	*out = {widths[size], heights[size], format, bytes_per_pixel(format)};
	return P6_RTG_OK;
}

int p6_rtg_set_mode(uint32_t width, uint32_t height, uint32_t format)
{
	const uint32_t bpp = bytes_per_pixel(format);
	if (!bpp) return P6_RTG_UNSUPPORTED;
	if (!fixed_mode(width, height)) return P6_RTG_UNSUPPORTED;
	p6_rtg_view next{};
	next.mode = {width, height, format, bpp};
	next.surface = {0, width * bpp, width, height, format};
	next.display_enabled = 1;
	next.active = 1;
	const int valid = validate_live_view(next);
	if (valid != P6_RTG_OK) return valid;
	state.view = next;
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_set_panning(const p6_rtg_surface *surface, uint32_t x, uint32_t y)
{
	if (!state.view.active) return P6_RTG_BAD_STATE;
	const int valid = validate_surface(surface);
	if (valid != P6_RTG_OK) return valid;
	if (surface->format != state.view.mode.format) return P6_RTG_UNSUPPORTED;
	if (x > surface->width || y > surface->height
		|| state.view.mode.width > surface->width - x
		|| state.view.mode.height > surface->height - y)
		return P6_RTG_BOUNDS;
	p6_rtg_view next = state.view;
	next.surface = *surface;
	next.x_offset = x;
	next.y_offset = y;
	if (validate_live_view(next) != P6_RTG_OK) return P6_RTG_BOUNDS;
	state.view = next;
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_set_display(uint32_t enabled)
{
	if (!state.view.active) return P6_RTG_BAD_STATE;
	if (enabled > 1) return P6_RTG_BAD_ARGUMENT;
	state.view.display_enabled = enabled;
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_set_palette(uint32_t first, uint32_t count, const uint32_t *xrgb8888)
{
	if (first > P6_RTG_PALETTE_SIZE || count > P6_RTG_PALETTE_SIZE - first)
		return P6_RTG_BOUNDS;
	if (count && !xrgb8888) return P6_RTG_BAD_ARGUMENT;
	for (uint32_t i = 0; i < count; ++i)
		state.palette[first + i] = xrgb8888[i] & 0x00ffffffu;
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_fill(const p6_rtg_surface *surface, uint32_t x, uint32_t y,
	uint32_t width, uint32_t height, uint32_t pixel)
{
	const int valid = validate_surface(surface);
	if (valid != P6_RTG_OK) return valid;
	if (!valid_rect(*surface, x, y, width, height)) return P6_RTG_BOUNDS;
	if (!width || !height) return P6_RTG_OK;
	const uint32_t bpp = bytes_per_pixel(surface->format);
	for (uint32_t row = 0; row < height; ++row) {
		uint8_t *destination = vram + pixel_address(*surface, x, y + row);
		for (uint32_t column = 0; column < width; ++column)
			store_pixel(destination + uint64_t(column) * bpp, surface->format, pixel);
	}
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_blit(const p6_rtg_surface *source, uint32_t source_x, uint32_t source_y,
	const p6_rtg_surface *destination, uint32_t destination_x, uint32_t destination_y,
	uint32_t width, uint32_t height)
{
	int valid = validate_surface(source);
	if (valid != P6_RTG_OK) return valid;
	valid = validate_surface(destination);
	if (valid != P6_RTG_OK) return valid;
	if (source->format != destination->format) return P6_RTG_UNSUPPORTED;
	if (!valid_rect(*source, source_x, source_y, width, height)
		|| !valid_rect(*destination, destination_x, destination_y, width, height))
		return P6_RTG_BOUNDS;
	if (!width || !height) return P6_RTG_OK;

	const uint64_t bpp = bytes_per_pixel(source->format);
	const uint64_t bytes = uint64_t(width) * bpp;
	const uint64_t source_first = pixel_address(*source, source_x, source_y);
	const uint64_t source_last = pixel_address(*source, source_x + width, source_y + height - 1);
	const uint64_t destination_first = pixel_address(*destination, destination_x, destination_y);
	const uint64_t destination_last = pixel_address(*destination, destination_x + width, destination_y + height - 1);
	const bool overlap = source_first < destination_last && destination_first < source_last;
	/* A single-row scratch would not make arbitrary differently-strided overlapping
	 * rectangles atomic. The uaegfx same-bitmap operation has equal strides; reject
	 * the wider operation explicitly instead of silently corrupting it. */
	if (overlap && source->stride_bytes != destination->stride_bytes)
		return P6_RTG_UNSUPPORTED;
	if (source_first == destination_first && source->stride_bytes == destination->stride_bytes)
		return P6_RTG_OK;

	if (overlap && destination_first > source_first) {
		for (uint32_t row = height; row-- > 0;) {
			uint8_t *dst = vram + pixel_address(*destination, destination_x, destination_y + row);
			const uint8_t *src = vram + pixel_address(*source, source_x, source_y + row);
			std::memmove(dst, src, size_t(bytes));
		}
	} else {
		for (uint32_t row = 0; row < height; ++row) {
			uint8_t *dst = vram + pixel_address(*destination, destination_x, destination_y + row);
			const uint8_t *src = vram + pixel_address(*source, source_x, source_y + row);
			std::memmove(dst, src, size_t(bytes));
		}
	}
	state.have_frame = false;
	return P6_RTG_OK;
}

int p6_rtg_present(void)
{
	const int valid = validate_live_view(state.view);
	if (valid != P6_RTG_OK || !state.view.active) return P6_RTG_BAD_STATE;
	std::memset(output, 0, sizeof output);
	if (state.view.display_enabled) {
		const uint32_t bpp = state.view.mode.bytes_per_pixel;
		for (uint32_t y = 0; y < state.view.mode.height; ++y) {
			const uint8_t *source = vram + pixel_address(state.view.surface,
				state.view.x_offset, state.view.y_offset + y);
			uint32_t *destination = output + uint64_t(y) * P6_RTG_MAX_WIDTH;
			for (uint32_t x = 0; x < state.view.mode.width; ++x)
				destination[x] = load_xrgb(source + uint64_t(x) * bpp, state.view.mode.format);
		}
	}
	++state.frames;
	state.have_frame = true;
	return P6_RTG_OK;
}

int p6_rtg_frame_get(p6_rtg_frame *out)
{
	if (!out) return P6_RTG_BAD_ARGUMENT;
	if (!state.have_frame || !state.view.active) return P6_RTG_BAD_STATE;
	*out = {output, state.view.mode.width, state.view.mode.height,
		P6_RTG_OUTPUT_STRIDE_BYTES, state.frames};
	return P6_RTG_OK;
}

int p6_rtg_view_get(p6_rtg_view *out)
{
	if (!out) return P6_RTG_BAD_ARGUMENT;
	*out = state.view;
	return P6_RTG_OK;
}

uint8_t *p6_rtg_vram_data(void)
{
	return vram;
}

size_t p6_rtg_vram_size(void)
{
	return sizeof vram;
}
