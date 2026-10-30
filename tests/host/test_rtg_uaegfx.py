"""Guest uaegfx traps translate only the bounded portable RTG contract."""
from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest

from tests.host import ensure_generated


ROOT = Path(__file__).resolve().parents[2]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]

PROBE = r"""
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "rtgmodes.h"
#include "rtg_device.h"
#include "rtg_memory.h"
#include "rtg_uaegfx.h"

/* Read the pinned ABI header without its Windows-only render declarations. */
#define __DXWRAP_H__
#define MAX_AMIGAMONITORS 1
#define PICASSO96
#include "od-win32/picasso96_win.h"
#undef PICASSO96
#undef MAX_AMIGAMONITORS

#include <cassert>
#include <cstdint>
#include <cstring>

struct TrapContext {
	uae_u32 d[8]{};
	uae_u32 a[8]{};
	uint8_t memory[8192]{};
};

bool trap_valid_address(TrapContext *ctx, uaecptr address, uae_u32 size)
{
	return address <= sizeof ctx->memory && size <= sizeof ctx->memory - address;
}
uae_u32 trap_get_dreg(TrapContext *ctx, int reg) { return ctx->d[reg]; }
uae_u32 trap_get_areg(TrapContext *ctx, int reg) { return ctx->a[reg]; }
void trap_set_dreg(TrapContext *ctx, int reg, uae_u32 value) { ctx->d[reg] = value; }
void trap_set_areg(TrapContext *ctx, int reg, uae_u32 value) { ctx->a[reg] = value; }
void trap_put_long(TrapContext *ctx, uaecptr address, uae_u32 value)
{
	assert(trap_valid_address(ctx, address, 4));
	ctx->memory[address] = uint8_t(value >> 24);
	ctx->memory[address + 1] = uint8_t(value >> 16);
	ctx->memory[address + 2] = uint8_t(value >> 8);
	ctx->memory[address + 3] = uint8_t(value);
}
void trap_put_word(TrapContext *ctx, uaecptr address, uae_u16 value)
{
	assert(trap_valid_address(ctx, address, 2));
	ctx->memory[address] = uint8_t(value >> 8);
	ctx->memory[address + 1] = uint8_t(value);
}
void trap_put_byte(TrapContext *ctx, uaecptr address, uae_u8 value)
{
	assert(trap_valid_address(ctx, address, 1));
	ctx->memory[address] = value;
}
uae_u32 trap_get_long(TrapContext *ctx, uaecptr address)
{
	assert(trap_valid_address(ctx, address, 4));
	return (uae_u32(ctx->memory[address]) << 24)
		| (uae_u32(ctx->memory[address + 1]) << 16)
		| (uae_u32(ctx->memory[address + 2]) << 8) | ctx->memory[address + 3];
}
uae_u16 trap_get_word(TrapContext *ctx, uaecptr address)
{
	assert(trap_valid_address(ctx, address, 2));
	return uae_u16((uae_u16(ctx->memory[address]) << 8) | ctx->memory[address + 1]);
}
uae_u8 trap_get_byte(TrapContext *ctx, uaecptr address)
{
	assert(trap_valid_address(ctx, address, 1));
	return ctx->memory[address];
}

namespace {
constexpr uaecptr BI = 0x100;
constexpr uaecptr MODE = 0x800;
constexpr uaecptr BME = 0x900;
constexpr uaecptr RI = 0xa00;
constexpr uaecptr DRI = 0xb00;
constexpr uae_u32 BASE = 0x22000000;

uint64_t digest()
{
	uint64_t hash = UINT64_C(1469598103934665603);
	for (size_t i = 0; i < p6_rtg_vram_size(); ++i) {
		hash ^= p6_rtg_vram_data()[i];
		hash *= UINT64_C(1099511628211);
	}
	return hash;
}

void put_render_info(TrapContext &ctx, uaecptr at, uaecptr memory,
	uae_u16 stride, uae_u32 format)
{
	trap_put_long(&ctx, at + 0, memory);
	trap_put_word(&ctx, at + 4, stride);
	trap_put_word(&ctx, at + 6, 0);
	trap_put_long(&ctx, at + 8, format);
}

uint32_t pen_for(uint32_t format)
{
	if (format == P6_RTG_RGBFB_CLUT) return 3;
	if (format == P6_RTG_RGBFB_R5G6B5PC) return 0xf81f;
	return 0x7f112233;
}

uint32_t expected_for(uint32_t format)
{
	if (format == P6_RTG_RGBFB_CLUT) return 0x00112233;
	if (format == P6_RTG_RGBFB_R5G6B5PC) return 0x00ff00ff;
	return 0x00112233;
}

void configure(TrapContext &ctx, const p6_rtg_mode &mode)
{
	trap_put_word(&ctx, MODE + 18, uae_u16(mode.width));
	trap_put_word(&ctx, MODE + 20, uae_u16(mode.height));
	trap_put_byte(&ctx, MODE + 22, uae_u8(mode.bytes_per_pixel * 8));
	trap_put_byte(&ctx, MODE + 23, 0);
	ctx.a[0] = BI;
	ctx.a[1] = MODE;
	ctx.d[0] = 0;
	assert(p6_uaegfx_set_gc(&ctx) == 1);
	assert(trap_get_long(&ctx, BI + 558) == MODE);

	trap_put_long(&ctx, BI + 1354, BME);
	trap_put_word(&ctx, BME + 40, uae_u16(mode.width));
	trap_put_word(&ctx, BME + 42, uae_u16(mode.height));
	ctx.a[1] = BASE;
	ctx.d[0] = mode.width;
	ctx.d[1] = 0;
	ctx.d[2] = 0;
	ctx.d[7] = mode.format;
	assert(p6_uaegfx_set_panning(&ctx) == 1);
	assert(trap_get_word(&ctx, BI + 566) == 0 && trap_get_word(&ctx, BI + 568) == 0);
}
}

int main()
{
	static_assert(PSSO_BoardInfo_MemoryBase == 4 && PSSO_BoardInfo_MemorySize == 12);
	static_assert(PSSO_BoardInfo_ModeInfo == 558 && PSSO_BoardInfo_XOffset == 566);
	static_assert(PSSO_BoardInfo_YOffset == 568 && PSSO_BoardInfo_Border == 572);
	static_assert(PSSO_BoardInfo_CLUT == 578 && PSSO_BoardInfo_BitMapExtra == 1354);
	static_assert(PSSO_ModeInfo_Width == 18 && PSSO_ModeInfo_Height == 20);
	static_assert(PSSO_ModeInfo_Depth == 22 && PSSO_ModeInfo_Flags == 23);
	static_assert(PSSO_RenderInfo_Memory == 0 && PSSO_RenderInfo_BytesPerRow == 4);
	static_assert(PSSO_RenderInfo_RGBFormat == 8 && PSSO_RenderInfo_sizeof == 12);
	static_assert(PSSO_BitMapExtra_Width == 40 && PSSO_BitMapExtra_Height == 42);
	TrapContext ctx{};
	assert(p6_rtg_memory_enable() == P6_RTG_OK);
	assert(p6_rtg_memory_set_bus_base(BASE) == P6_RTG_OK);
	gfxmem_bank.flags |= ABFLAG_MAPPED;

	ctx.a[0] = BI;
	assert(p6_uaegfx_find_card(&ctx) == uae_u32(-1));
	assert(trap_get_long(&ctx, BI + 4) == BASE);
	assert(trap_get_long(&ctx, BI + 12) == P6_RTG_VRAM_BYTES);
	assert(p6_uaegfx_find_card(&ctx) == 0);
	assert(p6_uaegfx_get_compatible_formats(&ctx)
		== ((1u << P6_RTG_RGBFB_CLUT) | (1u << P6_RTG_RGBFB_R5G6B5PC)
			| (1u << P6_RTG_RGBFB_A8R8G8B8)));

	for (uint32_t index = 0; index < P6_RTG_MODE_COUNT; ++index) {
		p6_rtg_mode mode{};
		assert(p6_rtg_mode_at(index, &mode) == P6_RTG_OK);
		p6_uaegfx_reset();
		configure(ctx, mode);
		ctx.d[0] = mode.width;
		ctx.d[7] = mode.format;
		assert(p6_uaegfx_calculate_bytes_per_row(&ctx) == mode.width * mode.bytes_per_pixel);

		if (mode.format == P6_RTG_RGBFB_CLUT) {
			ctx.a[0] = BI;
			ctx.d[0] = 3;
			ctx.d[1] = 1;
			ctx.memory[BI + 578 + 9] = 0x11;
			ctx.memory[BI + 578 + 10] = 0x22;
			ctx.memory[BI + 578 + 11] = 0x33;
			assert(p6_uaegfx_set_color_array(&ctx) == 1);
		}

		put_render_info(ctx, RI, BASE, uae_u16(mode.width * mode.bytes_per_pixel), mode.format);
		ctx.a[1] = RI;
		ctx.d[0] = mode.width - 1;
		ctx.d[1] = mode.height - 1;
		ctx.d[2] = 1;
		ctx.d[3] = 1;
		ctx.d[4] = pen_for(mode.format);
		ctx.d[5] = 0xff;
		ctx.d[7] = mode.format;
		assert(p6_uaegfx_fill_rect(&ctx) == 1);

		ctx.d[0] = 1;
		assert(p6_uaegfx_set_switch(&ctx) == 0 && p6_uaegfx_selected());
		assert(p6_rtg_present() == P6_RTG_OK);
		p6_rtg_frame frame{};
		assert(p6_rtg_frame_get(&frame) == P6_RTG_OK);
		assert(frame.width == mode.width && frame.height == mode.height);
		assert(frame.pixels[uint64_t(mode.height - 1) * P6_RTG_MAX_WIDTH + mode.width - 1]
			== expected_for(mode.format));

		ctx.d[0] = 0;
		assert(p6_uaegfx_set_display(&ctx) == 1 && p6_rtg_present() == P6_RTG_OK);
		assert(p6_rtg_frame_get(&frame) == P6_RTG_OK && frame.pixels[0] == 0);
		ctx.d[0] = 1;
		assert(p6_uaegfx_set_display(&ctx) == 0);
	}

	/* Same-bitmap overlap follows memmove direction. */
	p6_uaegfx_reset();
	p6_rtg_mode mode{};
	assert(p6_rtg_mode_at(0, &mode) == P6_RTG_OK);
	configure(ctx, mode);
	put_render_info(ctx, RI, BASE, 640, P6_RTG_RGBFB_CLUT);
	uint8_t *vram = p6_rtg_vram_data();
	vram[0] = 1; vram[1] = 2; vram[2] = 3; vram[3] = 4; vram[4] = 5;
	ctx.a[1] = RI;
	ctx.d[0] = 0; ctx.d[1] = 0; ctx.d[2] = 1; ctx.d[3] = 0;
	ctx.d[4] = 4; ctx.d[5] = 1; ctx.d[6] = 0xff; ctx.d[7] = P6_RTG_RGBFB_CLUT;
	assert(p6_uaegfx_blit_rect(&ctx) == 1);
	assert(vram[0] == 1 && vram[1] == 1 && vram[2] == 2 && vram[3] == 3 && vram[4] == 4);

	/* Unsupported requests are fallbacks and must not mutate guest or VRAM state. */
	const uint64_t before = digest();
	ctx.d[5] = 0x0f;
	assert(p6_uaegfx_fill_rect(&ctx) == 0 && digest() == before);
	ctx.d[5] = 1; ctx.d[6] = 0x7f;
	assert(p6_uaegfx_blit_rect(&ctx) == 0 && digest() == before);
	put_render_info(ctx, RI, BASE - 1, 640, P6_RTG_RGBFB_CLUT);
	ctx.d[5] = 0xff;
	assert(p6_uaegfx_fill_rect(&ctx) == 0 && digest() == before);
	put_render_info(ctx, RI, BASE, 640, P6_RTG_RGBFB_CLUT);
	put_render_info(ctx, DRI, BASE + 640, 640, P6_RTG_RGBFB_CLUT);
	ctx.a[1] = RI; ctx.a[2] = DRI; ctx.d[6] = 6;
	assert(p6_uaegfx_blit_rect_nomask_complete(&ctx) == 0 && digest() == before);
	ctx.a[0] = 0xfffffff0u; ctx.d[0] = 0; ctx.d[1] = 1;
	assert(p6_uaegfx_set_color_array(&ctx) == 0 && digest() == before);
	ctx.d[7] = RGBFB_R8G8B8;
	assert(p6_uaegfx_calculate_bytes_per_row(&ctx) == 0);
	return 0;
}
"""


class RtgUaegfxTests(unittest.TestCase):
    def test_all_modes_guest_operations_and_rejections(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        includes = ["-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-rtg-uaegfx-") as directory:
            temporary = Path(directory)
            probe = temporary / "probe.cpp"
            probe.write_text(textwrap.dedent(PROBE), encoding="utf-8")
            for optimization in ("-O0", "-O2"):
                executable = temporary / ("probe" + optimization)
                subprocess.run([
                    "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math",
                    optimization, *SANITIZERS, *includes, str(probe),
                    str(ROOT / "ports/portable/rtg_device.cpp"),
                    str(ROOT / "ports/portable/rtg_memory.cpp"),
                    str(ROOT / "ports/portable/rtg_uaegfx.cpp"),
                    "-o", str(executable),
                ], check=True, timeout=180)
                result = subprocess.run(
                    [str(executable)], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(result.returncode, 0, result.stderr[-1600:])

    def test_trap_object_has_no_host_services(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-rtg-uaegfx-object-") as directory:
            obj = Path(directory) / "rtg_uaegfx.o"
            subprocess.run([
                "clang++", "-std=c++17", "-O2",
                "-I" + str(ROOT / "ports/portable"),
                "-I" + str(ROOT / "build/generated"),
                "-I" + str(tree / "include"), "-I" + str(tree),
                "-c", str(ROOT / "ports/portable/rtg_uaegfx.cpp"), "-o", str(obj),
            ], check=True, timeout=120, capture_output=True)
            imports = subprocess.run(
                ["nm", "-u", str(obj)], check=True, capture_output=True, text=True).stdout
            normalized = {line.split()[-1].lstrip("_") for line in imports.splitlines() if line.split()}
            for forbidden in (
                "malloc", "calloc", "realloc", "free", "operator new", "pthread_create",
                "clock_gettime", "gettimeofday", "time", "sleep", "usleep",
                "D3D11CreateDevice", "glTexImage2D", "VirtualAlloc", "GetWriteWatch",
            ):
                self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()
