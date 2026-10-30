/* Guest-facing bounded uaegfx operations; no Win32/Picasso host backend. */
#include "sysconfig.h"
#include "sysdeps.h"

#include "options.h"
#include "memory.h"
#include "rtgmodes.h"
#include "rtg_device.h"
#include "rtg_memory.h"
#include "rtg_uaegfx.h"

#include <cstdint>

namespace {

/* WinUAE 6.0.3 od-win32/picasso96_win.h ABI offsets.  The portable module
 * deliberately owns only the fields consumed by its supported traps. */
enum : uae_u32 {
	BI_MEMORY_BASE = 4,
	BI_MEMORY_SIZE = 12,
	BI_MODE_INFO = 558,
	BI_X_OFFSET = 566,
	BI_Y_OFFSET = 568,
	BI_BORDER = 572,
	BI_CLUT = 578,
	BI_BITMAP_EXTRA = 1354,
	MI_WIDTH = 18,
	MI_HEIGHT = 20,
	MI_DEPTH = 22,
	MI_FLAGS = 23,
	RI_MEMORY = 0,
	RI_BYTES_PER_ROW = 4,
	RI_RGB_FORMAT = 8,
	RI_SIZE = 12,
	BME_WIDTH = 40,
	BME_HEIGHT = 42
};

struct UaegfxState {
	uae_u32 width;
	uae_u32 height;
	uae_u32 depth;
	bool card_found;
	bool selected;
};

UaegfxState state{};

uae_u32 bytes_per_pixel(uae_u32 format)
{
	switch (format) {
	case RGBFB_CLUT: return 1;
	case RGBFB_R5G6B5PC: return 2;
	case RGBFB_A8R8G8B8: return 4;
	default: return 0;
	}
}

bool fixed_geometry(uae_u32 width, uae_u32 height)
{
	return (width == 640 && height == 480)
		|| (width == 800 && height == 600)
		|| (width == 1024 && height == 768);
}

int validate_snapshot(const p6_uaegfx_snapshot &snapshot)
{
	if (snapshot.version != P6_UAEGFX_SNAPSHOT_VERSION) return P6_RTG_UNSUPPORTED;
	if (snapshot.card_found > 1 || snapshot.selected > 1) return P6_RTG_BAD_STATE;
	if (!snapshot.width) {
		return snapshot.height || snapshot.depth || snapshot.selected
			? P6_RTG_BAD_STATE : P6_RTG_OK;
	}
	if (!fixed_geometry(snapshot.width, snapshot.height)
		|| (snapshot.depth != 8 && snapshot.depth != 16 && snapshot.depth != 32))
		return P6_RTG_BAD_STATE;
	return P6_RTG_OK;
}

bool depth_matches(uae_u32 format, uae_u32 depth)
{
	return (format == RGBFB_CLUT && depth == 8)
		|| (format == RGBFB_R5G6B5PC && depth == 16)
		|| (format == RGBFB_A8R8G8B8 && depth == 32);
}

bool guest_range(TrapContext *ctx, uaecptr address, uae_u32 bytes)
{
	return address && bytes && trap_valid_address(ctx, address, bytes);
}

bool guest_field(TrapContext *ctx, uaecptr base, uint64_t offset,
	uae_u32 bytes, uaecptr *address = nullptr)
{
	if (!base) return false;
	const uint64_t start = uint64_t(base) + offset;
	if (start > UINT32_MAX || !guest_range(ctx, uaecptr(start), bytes)) return false;
	if (address) *address = uaecptr(start);
	return true;
}

bool read_surface(TrapContext *ctx, uaecptr render_info, p6_rtg_surface *out)
{
	if (!out || !guest_range(ctx, render_info, RI_SIZE)) return false;
	const uaecptr memory = trap_get_long(ctx, render_info + RI_MEMORY);
	const int32_t stride = int16_t(trap_get_word(ctx, render_info + RI_BYTES_PER_ROW));
	const uae_u32 format = trap_get_long(ctx, render_info + RI_RGB_FORMAT);
	const uae_u32 bpp = bytes_per_pixel(format);
	if (!bpp || stride <= 0 || uae_u32(stride) > P6_RTG_MAX_STRIDE_BYTES
		|| uae_u32(stride) % bpp || memory < gfxmem_bank.start)
		return false;
	const uint64_t offset = uint64_t(memory) - gfxmem_bank.start;
	const uae_u32 width = uae_u32(stride) / bpp;
	if (offset >= P6_RTG_VRAM_BYTES || !width || width > P6_RTG_MAX_WIDTH)
		return false;
	const uint64_t remaining = P6_RTG_VRAM_BYTES - offset;
	const uae_u32 height = uae_u32(remaining / uae_u32(stride));
	if (!height) return false;
	*out = {uae_u32(offset), uae_u32(stride), width,
		height > P6_RTG_MAX_HEIGHT ? P6_RTG_MAX_HEIGHT : height, format};
	return true;
}

bool view_request(uae_u32 address, uae_u32 virtual_width, uae_u32 virtual_height,
	uae_u32 x, uae_u32 y, uae_u32 format, p6_rtg_surface *surface)
{
	const uae_u32 bpp = bytes_per_pixel(format);
	if (!surface || !bpp || !depth_matches(format, state.depth)
		|| !fixed_geometry(state.width, state.height)
		|| !virtual_width || virtual_width > P6_RTG_MAX_WIDTH
		|| !virtual_height || virtual_height > P6_RTG_MAX_HEIGHT
		|| x > virtual_width || y > virtual_height
		|| state.width > virtual_width - x || state.height > virtual_height - y
		|| address < gfxmem_bank.start)
		return false;
	const uint64_t stride = uint64_t(virtual_width) * bpp;
	const uint64_t offset = uint64_t(address) - gfxmem_bank.start;
	const uint64_t end = offset + uint64_t(virtual_height - 1) * stride
		+ uint64_t(virtual_width) * bpp;
	if (stride > P6_RTG_MAX_STRIDE_BYTES || end > P6_RTG_VRAM_BYTES)
		return false;
	*surface = {uae_u32(offset), uae_u32(stride), virtual_width, virtual_height, format};
	return true;
}

} // namespace

void p6_uaegfx_reset(void)
{
	state = UaegfxState{};
	p6_rtg_reset();
}

int p6_uaegfx_selected(void)
{
	return state.selected ? 1 : 0;
}

int p6_uaegfx_snapshot_get(p6_uaegfx_snapshot *out)
{
	if (!out) return P6_RTG_BAD_ARGUMENT;
	*out = {P6_UAEGFX_SNAPSHOT_VERSION, state.width, state.height, state.depth,
		state.card_found ? 1u : 0u, state.selected ? 1u : 0u};
	return P6_RTG_OK;
}

int p6_uaegfx_snapshot_validate(const p6_uaegfx_snapshot *snapshot)
{
	return snapshot ? validate_snapshot(*snapshot) : P6_RTG_BAD_ARGUMENT;
}

int p6_uaegfx_snapshot_apply(const p6_uaegfx_snapshot *snapshot)
{
	if (!snapshot) return P6_RTG_BAD_ARGUMENT;
	const int valid = validate_snapshot(*snapshot);
	if (valid != P6_RTG_OK) return valid;
	state.width = snapshot->width;
	state.height = snapshot->height;
	state.depth = snapshot->depth;
	state.card_found = snapshot->card_found != 0;
	state.selected = snapshot->selected != 0;
	return P6_RTG_OK;
}

uae_u32 REGPARAM2 p6_uaegfx_find_card(TrapContext *ctx)
{
	const uaecptr board_info = trap_get_areg(ctx, 0);
	if (state.card_found || !(gfxmem_bank.flags & ABFLAG_MAPPED)
		|| gfxmem_bank.allocated_size != P6_RTG_VRAM_BYTES
		|| !guest_field(ctx, board_info, BI_MEMORY_BASE, 12))
		return 0;
	trap_put_long(ctx, board_info + BI_MEMORY_BASE, gfxmem_bank.start);
	trap_put_long(ctx, board_info + BI_MEMORY_SIZE, P6_RTG_VRAM_BYTES);
	state.card_found = true;
	return uae_u32(-1);
}

uae_u32 REGPARAM2 p6_uaegfx_set_gc(TrapContext *ctx)
{
	const uaecptr board_info = trap_get_areg(ctx, 0);
	const uaecptr mode_info = trap_get_areg(ctx, 1);
	if (!guest_field(ctx, mode_info, MI_WIDTH, 6)
		|| !guest_field(ctx, board_info, BI_MODE_INFO, 18))
		return 0;
	const uae_u32 width = trap_get_word(ctx, mode_info + MI_WIDTH);
	const uae_u32 height = trap_get_word(ctx, mode_info + MI_HEIGHT);
	const uae_u32 depth = trap_get_byte(ctx, mode_info + MI_DEPTH);
	if (!fixed_geometry(width, height) || (depth != 8 && depth != 16 && depth != 32))
		return 0;
	trap_put_long(ctx, board_info + BI_MODE_INFO, mode_info);
	trap_put_word(ctx, board_info + BI_BORDER, uae_u16(trap_get_dreg(ctx, 0)));
	state.width = width;
	state.height = height;
	state.depth = depth;
	return 1;
}

uae_u32 REGPARAM2 p6_uaegfx_set_panning(TrapContext *ctx)
{
	const uaecptr board_info = trap_get_areg(ctx, 0);
	if (!guest_field(ctx, board_info, BI_BITMAP_EXTRA, 4)
		|| !guest_field(ctx, board_info, BI_X_OFFSET, 4))
		return 0;
	const uaecptr bitmap_extra = trap_get_long(ctx, board_info + BI_BITMAP_EXTRA);
	if (!guest_field(ctx, bitmap_extra, BME_WIDTH, 4)) return 0;
	const int32_t x = int16_t(trap_get_dreg(ctx, 1));
	const int32_t y = int16_t(trap_get_dreg(ctx, 2));
	if (x < 0 || y < 0) return 0;
	p6_rtg_surface surface{};
	if (!view_request(trap_get_areg(ctx, 1),
		trap_get_word(ctx, bitmap_extra + BME_WIDTH),
		trap_get_word(ctx, bitmap_extra + BME_HEIGHT), uae_u32(x), uae_u32(y),
		trap_get_dreg(ctx, 7), &surface))
		return 0;
	if (p6_rtg_set_mode(state.width, state.height, surface.format) != P6_RTG_OK
		|| p6_rtg_set_panning(&surface, uae_u32(x), uae_u32(y)) != P6_RTG_OK)
		return 0;
	trap_put_word(ctx, board_info + BI_X_OFFSET, uae_u16(x));
	trap_put_word(ctx, board_info + BI_Y_OFFSET, uae_u16(y));
	return 1;
}

uae_u32 REGPARAM2 p6_uaegfx_set_switch(TrapContext *ctx)
{
	const bool old = state.selected;
	const bool next = (trap_get_dreg(ctx, 0) & 0xffff) != 0;
	p6_rtg_view view{};
	if (!next || (p6_rtg_view_get(&view) == P6_RTG_OK && view.active))
		state.selected = next;
	return old ? 1 : 0;
}

uae_u32 REGPARAM2 p6_uaegfx_set_display(TrapContext *ctx)
{
	const uae_u32 enabled = trap_get_dreg(ctx, 0) ? 1 : 0;
	(void)p6_rtg_set_display(enabled);
	return enabled ? 0 : 1;
}

uae_u32 REGPARAM2 p6_uaegfx_set_color_array(TrapContext *ctx)
{
	const uae_u32 first = uae_u16(trap_get_dreg(ctx, 0));
	const uae_u32 count = uae_u16(trap_get_dreg(ctx, 1));
	uaecptr colors = 0;
	if (first > P6_RTG_PALETTE_SIZE || count > P6_RTG_PALETTE_SIZE - first
		|| (count && !guest_field(ctx, trap_get_areg(ctx, 0),
			uint64_t(BI_CLUT) + uint64_t(first) * 3, count * 3, &colors)))
		return 0;
	uint32_t palette[P6_RTG_PALETTE_SIZE];
	for (uae_u32 i = 0; i < count; ++i) {
		const uaecptr entry = colors + i * 3;
		palette[i] = (uae_u32(trap_get_byte(ctx, entry)) << 16)
			| (uae_u32(trap_get_byte(ctx, entry + 1)) << 8)
			| trap_get_byte(ctx, entry + 2);
	}
	return p6_rtg_set_palette(first, count, palette) == P6_RTG_OK ? 1 : 0;
}

uae_u32 REGPARAM2 p6_uaegfx_calculate_bytes_per_row(TrapContext *ctx)
{
	const uint64_t bytes = uint64_t(uae_u16(trap_get_dreg(ctx, 0)))
		* bytes_per_pixel(trap_get_dreg(ctx, 7));
	return bytes <= UINT16_MAX ? uae_u32(bytes) : 0;
}

uae_u32 REGPARAM2 p6_uaegfx_get_compatible_formats(TrapContext *)
{
	return RGBFF_CLUT | RGBFF_R5G6B5PC | RGBFF_A8R8G8B8;
}

uae_u32 REGPARAM2 p6_uaegfx_fill_rect(TrapContext *ctx)
{
	if (uae_u8(trap_get_dreg(ctx, 5)) != 0xff) return 0;
	p6_rtg_surface surface{};
	if (!read_surface(ctx, trap_get_areg(ctx, 1), &surface)
		|| surface.format != trap_get_dreg(ctx, 7))
		return 0;
	return p6_rtg_fill(&surface, uae_u16(trap_get_dreg(ctx, 0)),
		uae_u16(trap_get_dreg(ctx, 1)), uae_u16(trap_get_dreg(ctx, 2)),
		uae_u16(trap_get_dreg(ctx, 3)), trap_get_dreg(ctx, 4)) == P6_RTG_OK ? 1 : 0;
}

uae_u32 REGPARAM2 p6_uaegfx_blit_rect(TrapContext *ctx)
{
	if (uae_u8(trap_get_dreg(ctx, 6)) != 0xff) return 0;
	p6_rtg_surface surface{};
	if (!read_surface(ctx, trap_get_areg(ctx, 1), &surface)
		|| surface.format != trap_get_dreg(ctx, 7))
		return 0;
	return p6_rtg_blit(&surface, uae_u16(trap_get_dreg(ctx, 0)),
		uae_u16(trap_get_dreg(ctx, 1)), &surface, uae_u16(trap_get_dreg(ctx, 2)),
		uae_u16(trap_get_dreg(ctx, 3)), uae_u16(trap_get_dreg(ctx, 4)),
		uae_u16(trap_get_dreg(ctx, 5))) == P6_RTG_OK ? 1 : 0;
}

uae_u32 REGPARAM2 p6_uaegfx_blit_rect_nomask_complete(TrapContext *ctx)
{
	/* Picasso96 BLIT_SRC is opcode 12; every other raster op falls back. */
	if (uae_u8(trap_get_dreg(ctx, 6)) != 12) return 0;
	p6_rtg_surface source{}, destination{};
	if (!read_surface(ctx, trap_get_areg(ctx, 1), &source)
		|| !read_surface(ctx, trap_get_areg(ctx, 2), &destination)
		|| source.format != trap_get_dreg(ctx, 7) || destination.format != source.format)
		return 0;
	return p6_rtg_blit(&source, uae_u16(trap_get_dreg(ctx, 0)),
		uae_u16(trap_get_dreg(ctx, 1)), &destination, uae_u16(trap_get_dreg(ctx, 2)),
		uae_u16(trap_get_dreg(ctx, 3)), uae_u16(trap_get_dreg(ctx, 4)),
		uae_u16(trap_get_dreg(ctx, 5))) == P6_RTG_OK ? 1 : 0;
}
