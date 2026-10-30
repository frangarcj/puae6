/* Fixed uaegfx.card resident and mode list for the bounded RTG profile. */
#include "sysconfig.h"
#include "sysdeps.h"

#include "options.h"
#include "memory.h"
#include "traps.h"
#include "autoconf.h"
#include "devices.h"
#include "rtgmodes.h"
#include "rtg_device.h"
#include "rtg_memory.h"
#include "rtg_uaegfx.h"

#include <cstring>

namespace {

enum : uae_u32 {
	CARD_PRIVATE_BYTES = 64,
	MODE_INFO_BYTES = 48,
	LIB_RESOLUTION_BYTES = 80,
	RESOLUTION_BYTES = MODE_INFO_BYTES * 3 + LIB_RESOLUTION_BYTES,
	RESOLUTION_STORAGE_BYTES = RESOLUTION_BYTES * 3,
	LIBRARY_BYTES = CARD_PRIVATE_BYTES + RESOLUTION_STORAGE_BYTES,
	BI_RESOLUTIONS_LIST = 158,
	BI_BOARD_TYPE = 170,
	BI_PALETTE_CHIP_TYPE = 174,
	BI_GRAPHICS_CONTROLLER_TYPE = 178,
	BI_BITS_PER_CANNON = 184,
	BI_FLAGS = 186,
	BI_RGB_FORMATS = 200,
	BI_MAX_HOR_VALUE = 202,
	BI_MAX_VER_VALUE = 212,
	BI_MAX_HOR_RESOLUTION = 222,
	BI_MAX_VER_RESOLUTION = 232,
	BI_MAX_MEMORY_SIZE = 242,
	BI_MAX_CHUNK_SIZE = 246,
	BI_MEMORY_CLOCK = 250,
	BI_PIXEL_CLOCK_COUNT = 254,
	BI_SET_SWITCH = 282,
	BI_SET_COLOR_ARRAY = 286,
	BI_SET_DAC = 290,
	BI_SET_GC = 294,
	BI_SET_PANNING = 298,
	BI_CALCULATE_BYTES_PER_ROW = 302,
	BI_CALCULATE_MEMORY = 306,
	BI_GET_COMPATIBLE_FORMATS = 310,
	BI_SET_DISPLAY = 314,
	BI_RESOLVE_PIXEL_CLOCK = 318,
	BI_GET_PIXEL_CLOCK = 322,
	BI_SET_CLOCK = 326,
	BI_WAIT_VERTICAL_SYNC = 346,
	BI_WAIT_BLITTER = 354,
	BI_FILL_RECT = 382,
	BI_FILL_RECT_DEFAULT = 386,
	BI_BLIT_RECT = 398,
	BI_BLIT_RECT_DEFAULT = 402,
	BI_BLIT_RECT_NOMASK = 430,
	BI_BLIT_RECT_NOMASK_DEFAULT = 434,
	LR_P96ID = 14,
	LR_NAME = 20,
	LR_DISPLAY_ID = 42,
	LR_WIDTH = 46,
	LR_HEIGHT = 48,
	LR_FLAGS = 50,
	LR_MODES = 52,
	LR_BOARD_INFO = 72,
	MI_ACTIVE = 16,
	MI_WIDTH = 18,
	MI_HEIGHT = 20,
	MI_DEPTH = 22,
	MI_FLAGS = 23,
	MI_HOR_TOTAL = 24,
	MI_HOR_BLANK_SIZE = 26,
	MI_HOR_SYNC_START = 28,
	MI_HOR_SYNC_SIZE = 30,
	MI_VER_TOTAL = 34,
	MI_VER_BLANK_SIZE = 36,
	MI_VER_SYNC_START = 38,
	MI_VER_SYNC_SIZE = 40,
	MI_NUMERATOR = 42,
	MI_DENOMINATOR = 43,
	MI_PIXEL_CLOCK = 44
};

struct ResidentCode {
	uaecptr rts;
	uaecptr function_table;
	uaecptr data_table;
	uaecptr open;
	uaecptr close;
	uaecptr expunge;
	uaecptr find_card;
	uaecptr init_card;
	uaecptr set_switch;
	uaecptr set_color_array;
	uaecptr set_dac;
	uaecptr set_gc;
	uaecptr set_panning;
	uaecptr calculate_bytes_per_row;
	uaecptr calculate_memory;
	uaecptr compatible_formats;
	uaecptr set_display;
	uaecptr resolve_pixel_clock;
	uaecptr get_pixel_clock;
	uaecptr fill_rect;
	uaecptr blit_rect;
	uaecptr blit_rect_nomask;
	bool ready;
};

ResidentCode code{};
uaecptr library_base;

uae_u32 REGPARAM2 library_open(TrapContext *ctx)
{
	if (!library_base || !trap_valid_address(ctx, library_base + 32, 2)) return 0;
	trap_put_word(ctx, library_base + 32, trap_get_word(ctx, library_base + 32) + 1);
	return library_base;
}

uae_u32 REGPARAM2 library_close(TrapContext *ctx)
{
	if (library_base && trap_valid_address(ctx, library_base + 32, 2))
		trap_put_word(ctx, library_base + 32, trap_get_word(ctx, library_base + 32) - 1);
	return 0;
}

uae_u32 REGPARAM2 library_expunge(TrapContext *) { return 0; }

uae_u32 REGPARAM2 set_dac(TrapContext *ctx)
{
	switch (trap_get_dreg(ctx, 7)) {
	case RGBFB_CLUT:
	case RGBFB_R5G6B5PC:
	case RGBFB_A8R8G8B8:
		return 1;
	default:
		return 0;
	}
}

uaecptr emit(TrapHandler handler)
{
	const uaecptr address = here();
	calltrap(deftrap2(handler, 0, _T("p6 uaegfx")));
	dw(RTS);
	return address;
}

uaecptr emit_fallback(TrapHandler handler, uae_u16 default_offset)
{
	const uaecptr address = here();
	dl(0x48e78000);             // movem.l d0,-(sp)
	calltrap(deftrap2(handler, 0, _T("p6 uaegfx fallback")));
	dw(0x4a80);                 // tst.l d0
	dl(0x4cdf0001);             // movem.l (sp)+,d0
	dw(0x6604);                 // bne.s supported
	dw(0x2f28); dw(default_offset); // move.l default(a0),-(sp)
	dw(RTS);
	return address;
}

void add_tail(TrapContext *ctx, uaecptr list, uaecptr node)
{
	trap_put_long(ctx, node, list + 4);
	trap_put_long(ctx, node + 4, trap_get_long(ctx, list + 8));
	trap_put_long(ctx, trap_get_long(ctx, list + 8), node);
	trap_put_long(ctx, list + 8, node);
}

void put_literal(TrapContext *ctx, uaecptr address, const char *text, uae_u32 capacity)
{
	const size_t length = std::strlen(text);
	trap_put_bytes(ctx, text, address, int(length < capacity ? length : capacity - 1));
}

void fill_mode(TrapContext *ctx, uaecptr address, uae_u32 width, uae_u32 height, uae_u32 depth)
{
	trap_set_bytes(ctx, address, 0, MODE_INFO_BYTES);
	trap_put_word(ctx, address + MI_ACTIVE, 1);
	trap_put_word(ctx, address + MI_WIDTH, uae_u16(width));
	trap_put_word(ctx, address + MI_HEIGHT, uae_u16(height));
	trap_put_byte(ctx, address + MI_DEPTH, uae_u8(depth));
	trap_put_byte(ctx, address + MI_FLAGS, 0);
	trap_put_word(ctx, address + MI_HOR_TOTAL, uae_u16(width + 8));
	trap_put_word(ctx, address + MI_HOR_BLANK_SIZE, 8);
	trap_put_word(ctx, address + MI_HOR_SYNC_START, 2);
	trap_put_word(ctx, address + MI_HOR_SYNC_SIZE, 2);
	trap_put_word(ctx, address + MI_VER_TOTAL, uae_u16(height + 8));
	trap_put_word(ctx, address + MI_VER_BLANK_SIZE, 8);
	trap_put_word(ctx, address + MI_VER_SYNC_START, 2);
	trap_put_word(ctx, address + MI_VER_SYNC_SIZE, 2);
	trap_put_byte(ctx, address + MI_NUMERATOR, 98);
	trap_put_byte(ctx, address + MI_DENOMINATOR, 14);
	trap_put_long(ctx, address + MI_PIXEL_CLOCK, width * height * 50);
}

void fill_board_info(TrapContext *ctx, uaecptr board_info)
{
	constexpr uae_u32 formats = RGBFF_CLUT | RGBFF_R5G6B5PC | RGBFF_A8R8G8B8;
	constexpr uae_u32 flags = (1u << 1) | (1u << 15) | (1u << 20) | (1u << 26);
	trap_put_long(ctx, board_info + BI_BOARD_TYPE, 14); // BT_uaegfx
	trap_put_long(ctx, board_info + BI_PALETTE_CHIP_TYPE, 0);
	trap_put_long(ctx, board_info + BI_GRAPHICS_CONTROLLER_TYPE, 0);
	trap_put_word(ctx, board_info + BI_BITS_PER_CANNON, 8);
	trap_put_long(ctx, board_info + BI_FLAGS, flags);
	trap_put_word(ctx, board_info + BI_RGB_FORMATS, uae_u16(formats));
	for (int mode : {1, 2, 4}) {
		trap_put_word(ctx, board_info + BI_MAX_HOR_VALUE + mode * 2, 0x4000);
		trap_put_word(ctx, board_info + BI_MAX_VER_VALUE + mode * 2, 0x4000);
		trap_put_word(ctx, board_info + BI_MAX_HOR_RESOLUTION + mode * 2, P6_RTG_MAX_WIDTH);
		trap_put_word(ctx, board_info + BI_MAX_VER_RESOLUTION + mode * 2, P6_RTG_MAX_HEIGHT);
		trap_put_long(ctx, board_info + BI_PIXEL_CLOCK_COUNT + mode * 4, 1);
	}
	trap_put_long(ctx, board_info + BI_MAX_MEMORY_SIZE, P6_RTG_VRAM_BYTES);
	trap_put_long(ctx, board_info + BI_MAX_CHUNK_SIZE, P6_RTG_VRAM_BYTES);
	trap_put_long(ctx, board_info + BI_MEMORY_CLOCK, 200000000);
	trap_put_long(ctx, board_info + BI_SET_SWITCH, code.set_switch);
	trap_put_long(ctx, board_info + BI_SET_COLOR_ARRAY, code.set_color_array);
	trap_put_long(ctx, board_info + BI_SET_DAC, code.set_dac);
	trap_put_long(ctx, board_info + BI_SET_GC, code.set_gc);
	trap_put_long(ctx, board_info + BI_SET_PANNING, code.set_panning);
	trap_put_long(ctx, board_info + BI_CALCULATE_BYTES_PER_ROW, code.calculate_bytes_per_row);
	trap_put_long(ctx, board_info + BI_CALCULATE_MEMORY, code.calculate_memory);
	trap_put_long(ctx, board_info + BI_GET_COMPATIBLE_FORMATS, code.compatible_formats);
	trap_put_long(ctx, board_info + BI_SET_DISPLAY, code.set_display);
	trap_put_long(ctx, board_info + BI_RESOLVE_PIXEL_CLOCK, code.resolve_pixel_clock);
	trap_put_long(ctx, board_info + BI_GET_PIXEL_CLOCK, code.get_pixel_clock);
	trap_put_long(ctx, board_info + BI_SET_CLOCK, code.rts);
	trap_put_long(ctx, board_info + BI_WAIT_VERTICAL_SYNC, code.rts);
	trap_put_long(ctx, board_info + BI_WAIT_BLITTER, code.rts);
	trap_put_long(ctx, board_info + BI_FILL_RECT, code.fill_rect);
	trap_put_long(ctx, board_info + BI_BLIT_RECT, code.blit_rect);
	trap_put_long(ctx, board_info + BI_BLIT_RECT_NOMASK, code.blit_rect_nomask);
}

void library_reset(int)
{
	library_base = 0;
	p6_uaegfx_reset();
}

} // namespace

int p6_uaegfx_install_code(uaecptr start)
{
	code = ResidentCode{};
	org(start);
	code.rts = here(); dw(RTS);
	code.open = emit(library_open);
	code.close = emit(library_close);
	code.expunge = emit(library_expunge);
	code.find_card = emit(p6_uaegfx_find_card);
	code.init_card = emit(p6_uaegfx_init_card);
	code.set_switch = emit(p6_uaegfx_set_switch);
	code.set_color_array = emit(p6_uaegfx_set_color_array);
	code.set_dac = emit(set_dac);
	code.set_gc = emit(p6_uaegfx_set_gc);
	code.set_panning = emit(p6_uaegfx_set_panning);
	code.calculate_bytes_per_row = emit(p6_uaegfx_calculate_bytes_per_row);
	code.calculate_memory = here(); dw(0x2009); dw(RTS); // move.l a1,d0
	code.compatible_formats = emit(p6_uaegfx_get_compatible_formats);
	code.set_display = emit(p6_uaegfx_set_display);
	code.resolve_pixel_clock = here();
	dl(0x2340002c); dw(0x7000); // move.l d0,44(a1); moveq #0,d0
	dl(0x137c0062); dw(0x002a); // numerator 98
	dl(0x137c000e); dw(0x002b); dw(RTS); // denominator 14
	code.get_pixel_clock = here(); dw(0x203c); dl(100227260); dw(RTS);
	code.fill_rect = emit_fallback(p6_uaegfx_fill_rect, BI_FILL_RECT_DEFAULT);
	code.blit_rect = emit_fallback(p6_uaegfx_blit_rect, BI_BLIT_RECT_DEFAULT);
	code.blit_rect_nomask = emit_fallback(
		p6_uaegfx_blit_rect_nomask_complete, BI_BLIT_RECT_NOMASK_DEFAULT);
	code.function_table = here();
	dl(code.open); dl(code.close); dl(code.expunge); dl(EXPANSION_nullfunc);
	dl(code.find_card); dl(code.init_card); dl(0xffffffffu);
	const uaecptr resname = ds_ansi("uaegfx.card");
	const uaecptr resid = ds_ansi("UAE Graphics Card 3.4");
	code.data_table = makedatatable(resid, resname, 0x09, -50, 3, 4);
	const uaecptr boundary = (start & 0xffff0000u) | RTAREA_TRAMPOLINE;
	if (here() > boundary) {
		code = ResidentCode{};
		return 0;
	}
	code.ready = true;
	device_add_reset(library_reset);
	return 1;
}

uaecptr p6_uaegfx_alloc(TrapContext *ctx)
{
	if (!ctx || !code.ready || library_base || !(gfxmem_bank.flags & ABFLAG_MAPPED))
		return library_base;
	const uaecptr exec = trap_get_long(ctx, 4);
	const uae_u32 saved_a2 = trap_get_areg(ctx, 2);
	trap_call_add_areg(ctx, 0, code.function_table);
	trap_call_add_areg(ctx, 1, code.data_table);
	trap_call_add_areg(ctx, 2, 0);
	trap_call_add_dreg(ctx, 0, LIBRARY_BYTES);
	trap_call_add_dreg(ctx, 1, 0);
	library_base = trap_call_lib(ctx, exec, -0x54); // MakeLibrary
	trap_set_areg(ctx, 2, saved_a2);
	if (!library_base || !trap_valid_address(ctx, library_base, LIBRARY_BYTES)) {
		library_base = 0;
		return 0;
	}
	trap_call_add_areg(ctx, 1, library_base);
	trap_call_lib(ctx, exec, -0x18c); // AddLibrary
	return library_base;
}

uaecptr p6_uaegfx_library_base(void) { return library_base; }

uae_u32 REGPARAM2 p6_uaegfx_init_card(TrapContext *ctx)
{
	if (!ctx || !code.ready || !library_base) return 0;
	const uaecptr board_info = trap_get_areg(ctx, 0);
	const uaecptr list = board_info + BI_RESOLUTIONS_LIST;
	const uaecptr storage = library_base + CARD_PRIVATE_BYTES;
	if (board_info > UINT32_MAX - 574 || !trap_valid_address(ctx, board_info, 574)
		|| !trap_valid_address(ctx, storage, RESOLUTION_STORAGE_BYTES)
		|| !trap_valid_address(ctx, list, 12))
		return 0;
	const uaecptr tail_pred = trap_get_long(ctx, list + 8);
	if (!trap_valid_address(ctx, tail_pred, 4)) return 0;
	trap_set_bytes(ctx, storage, 0, RESOLUTION_STORAGE_BYTES);
	fill_board_info(ctx, board_info);
	static constexpr uae_u16 widths[] = {640, 800, 1024};
	static constexpr uae_u16 heights[] = {480, 600, 768};
	static constexpr uae_u32 display_ids[] = {0x50031000, 0x50041000, 0x50051000};
	static constexpr const char *names[] = {"UAE:640x480", "UAE:800x600", "UAE:1024x768"};
	for (int i = 0; i < 3; ++i) {
		const uaecptr modes = storage + i * RESOLUTION_BYTES;
		const uaecptr resolution = modes + MODE_INFO_BYTES * 3;
		fill_mode(ctx, modes, widths[i], heights[i], 8);
		fill_mode(ctx, modes + MODE_INFO_BYTES, widths[i], heights[i], 16);
		fill_mode(ctx, modes + MODE_INFO_BYTES * 2, widths[i], heights[i], 32);
		trap_put_bytes(ctx, "P96-0:", resolution + LR_P96ID, 6);
		put_literal(ctx, resolution + LR_NAME, names[i], 22);
		trap_put_long(ctx, resolution + 10, resolution + LR_P96ID);
		trap_put_long(ctx, resolution + LR_DISPLAY_ID, display_ids[i]);
		trap_put_word(ctx, resolution + LR_WIDTH, widths[i]);
		trap_put_word(ctx, resolution + LR_HEIGHT, heights[i]);
		trap_put_word(ctx, resolution + LR_FLAGS, 2); // P96F_PUBLIC
		trap_put_long(ctx, resolution + LR_MODES + 1 * 4, modes);
		trap_put_long(ctx, resolution + LR_MODES + 2 * 4, modes + MODE_INFO_BYTES);
		trap_put_long(ctx, resolution + LR_MODES + 4 * 4, modes + MODE_INFO_BYTES * 2);
		trap_put_long(ctx, resolution + LR_BOARD_INFO, board_info);
		add_tail(ctx, list, resolution);
	}
	return 1;
}
