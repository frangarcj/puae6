"""Fixed uaegfx.card resident, BoardInfo vectors and nine guest modes."""
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
#include "traps.h"
#include "autoconf.h"
#include "devices.h"
#include "rtgmodes.h"
#include "rtg_device.h"
#include "rtg_memory.h"
#include "rtg_uaegfx.h"

#define __DXWRAP_H__
#define MAX_AMIGAMONITORS 1
#define PICASSO96
#include "od-win32/picasso96_win.h"
#undef PICASSO96
#undef MAX_AMIGAMONITORS

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {
constexpr uaecptr RTBASE = 0x00f00000;
constexpr uaecptr LIBBASE = 0x00020000;
constexpr uaecptr BI = 0x00001000;
uint8_t rtarea[0x10000];
uae_u32 rtpos;
uae_u32 string_pos = 0x7f00;
TrapHandler handlers[64];
unsigned handler_count;
void (*reset_callback)(int);
uae_u32 table_resid, table_resname, table_type, table_priority, table_version, table_revision;
uae_u32 make_a0, make_a1, make_size;
int add_library_calls;
}

struct TrapContext {
	uae_u32 d[8]{};
	uae_u32 a[8]{};
	uint8_t memory[0x40000]{};
};

uaecptr EXPANSION_nullfunc = RTBASE + 0x0100;

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
void trap_put_bytes(TrapContext *ctx, const void *source, uaecptr address, int count)
{
	assert(count >= 0 && trap_valid_address(ctx, address, uae_u32(count)));
	std::memcpy(ctx->memory + address, source, size_t(count));
}
void trap_get_bytes(TrapContext *ctx, void *destination, uaecptr address, int count)
{
	assert(count >= 0 && trap_valid_address(ctx, address, uae_u32(count)));
	std::memcpy(destination, ctx->memory + address, size_t(count));
}
void trap_set_bytes(TrapContext *ctx, uaecptr address, uae_u8 value, int count)
{
	assert(count >= 0 && trap_valid_address(ctx, address, uae_u32(count)));
	std::memset(ctx->memory + address, value, size_t(count));
}

void trap_call_add_dreg(TrapContext *ctx, int reg, uae_u32 value) { ctx->d[reg] = value; }
void trap_call_add_areg(TrapContext *ctx, int reg, uae_u32 value) { ctx->a[reg] = value; }
uae_u32 trap_call_lib(TrapContext *ctx, uaecptr, uae_s16 offset)
{
	if (offset == -0x54) {
		make_a0 = ctx->a[0]; make_a1 = ctx->a[1]; make_size = ctx->d[0];
		std::memset(ctx->memory + LIBBASE, 0, make_size);
		return LIBBASE;
	}
	if (offset == -0x18c) {
		assert(ctx->a[1] == LIBBASE);
		++add_library_calls;
		return 0;
	}
	assert(false);
	return 0;
}

unsigned int define_trap(TrapHandler handler, int, const TCHAR *)
{
	assert(handler_count + 1 < 64);
	handlers[++handler_count] = handler;
	return handler_count;
}
void device_add_reset(DEVICE_INT callback) { reset_callback = callback; }

void org(uae_u32 address) { assert((address & 0xffff0000) == RTBASE); rtpos = address & 0xffff; }
uae_u32 here(void) { return RTBASE + rtpos; }
void dw(uae_u16 value)
{
	assert(rtpos + 2 <= sizeof rtarea);
	rtarea[rtpos++] = uint8_t(value >> 8); rtarea[rtpos++] = uint8_t(value);
}
void dl(uae_u32 value) { dw(uae_u16(value >> 16)); dw(uae_u16(value)); }
void calltrap(uae_u32 number) { dw(uae_u16(0xa000 + number)); }
uae_u32 ds_ansi(const uae_char *text)
{
	const size_t bytes = std::strlen(text) + 1;
	assert(bytes <= string_pos);
	string_pos -= uae_u32(bytes);
	std::memcpy(rtarea + string_pos, text, bytes);
	return RTBASE + string_pos;
}
uaecptr makedatatable(uaecptr resid, uaecptr resname, uae_u8 type,
	uae_s8 priority, uae_u16 version, uae_u16 revision)
{
	table_resid = resid; table_resname = resname; table_type = type;
	table_priority = uae_u8(priority); table_version = version; table_revision = revision;
	const uaecptr result = here();
	dl(resid); dl(resname); dw(version); dw(revision);
	return result;
}

namespace {
uae_u16 rtword(uaecptr address)
{
	const uae_u32 offset = address - RTBASE;
	assert(offset + 2 <= sizeof rtarea);
	return uae_u16((uae_u16(rtarea[offset]) << 8) | rtarea[offset + 1]);
}
uae_u32 rtlong(uaecptr address) { return (uae_u32(rtword(address)) << 16) | rtword(address + 2); }
uint64_t guest_digest(const TrapContext &ctx)
{
	uint64_t hash = UINT64_C(1469598103934665603);
	for (uint8_t value : ctx.memory) { hash ^= value; hash *= UINT64_C(1099511628211); }
	return hash;
}
}

int main()
{
	static_assert(PSSO_BoardInfo_ResolutionsList == 158);
	static_assert(PSSO_BoardInfo_BoardType == 170 && PSSO_BoardInfo_BitsPerCannon == 184);
	static_assert(PSSO_BoardInfo_Flags == 186 && PSSO_BoardInfo_RGBFormats == 200);
	static_assert(PSSO_BoardInfo_MaxHorResolution == 222);
	static_assert(PSSO_BoardInfo_MaxVerResolution == 232);
	static_assert(PSSO_BoardInfo_SetSwitch == 282 && PSSO_BoardInfo_SetColorArray == 286);
	static_assert(PSSO_BoardInfo_SetGC == 294 && PSSO_BoardInfo_SetPanning == 298);
	static_assert(PSSO_BoardInfo_CalculateBytesPerRow == 302);
	static_assert(PSSO_BoardInfo_GetCompatibleFormats == 310);
	static_assert(PSSO_BoardInfo_SetDisplay == 314);
	static_assert(PSSO_BoardInfo_FillRect == 382 && PSSO_BoardInfo_FillRectDefault == 386);
	static_assert(PSSO_BoardInfo_BlitRect == 398 && PSSO_BoardInfo_BlitRectDefault == 402);
	static_assert(PSSO_BoardInfo_BlitRectNoMaskComplete == 430);
	static_assert(PSSO_BoardInfo_BlitRectNoMaskCompleteDefault == 434);
	static_assert(PSSO_LibResolution_sizeof == 80 && PSSO_ModeInfo_sizeof == 48);
	static TrapContext ctx{};
	assert(p6_rtg_memory_enable() == P6_RTG_OK);
	assert(p6_rtg_memory_set_bus_base(0x22000000) == P6_RTG_OK);
	gfxmem_bank.flags |= ABFLAG_MAPPED;
	assert(p6_uaegfx_install_code(RTBASE + RTAREA_RTG) == 1);
	assert(here() <= RTBASE + RTAREA_TRAMPOLINE);
	assert(reset_callback && handler_count == 16);
	assert(table_type == 0x09 && table_priority == uae_u8(-50));
	assert(table_version == 3 && table_revision == 4);
	assert(table_resid >= RTBASE && table_resname >= RTBASE);

	trap_put_long(&ctx, 4, 0x100);
	ctx.a[2] = 0x12345678;
	assert(p6_uaegfx_alloc(&ctx) == LIBBASE);
	assert(p6_uaegfx_library_base() == LIBBASE && ctx.a[2] == 0x12345678);
	assert(make_size == 736 && add_library_calls == 1);
	assert(make_a0 >= RTBASE + RTAREA_RTG && make_a0 < RTBASE + RTAREA_TRAMPOLINE);
	assert(make_a1 >= RTBASE + RTAREA_RTG && make_a1 < RTBASE + RTAREA_TRAMPOLINE);
	for (int i = 0; i < 6; ++i) {
		const uae_u32 entry = rtlong(make_a0 + i * 4);
		if (i != 3) assert(entry >= RTBASE + RTAREA_RTG && entry < RTBASE + RTAREA_TRAMPOLINE);
	}
	assert(rtlong(make_a0 + 6 * 4) == 0xffffffffu);
	assert(p6_uaegfx_alloc(&ctx) == LIBBASE && add_library_calls == 1);

	const uaecptr list = BI + 158;
	trap_put_long(&ctx, list, list + 4);
	trap_put_long(&ctx, list + 4, 0);
	trap_put_long(&ctx, list + 8, list);
	ctx.a[0] = BI;
	assert(p6_uaegfx_init_card(&ctx) == 1);
	assert(trap_get_long(&ctx, BI + 170) == 14);
	assert(trap_get_word(&ctx, BI + 184) == 8);
	assert(trap_get_word(&ctx, BI + 200) == 0x52);
	assert(trap_get_word(&ctx, BI + 222 + 2) == 1024);
	assert(trap_get_word(&ctx, BI + 232 + 2) == 768);
	for (uaecptr offset : {282u, 286u, 290u, 294u, 298u, 302u, 310u, 314u,
		318u, 322u, 382u, 398u, 430u}) {
		const uaecptr pointer = trap_get_long(&ctx, BI + offset);
		assert(pointer >= RTBASE + RTAREA_RTG && pointer < RTBASE + RTAREA_TRAMPOLINE);
	}
	const uaecptr fill = trap_get_long(&ctx, BI + 382);
	const uaecptr blit = trap_get_long(&ctx, BI + 398);
	const uaecptr complete = trap_get_long(&ctx, BI + 430);
	assert(rtword(fill + 16) == 386 && rtword(blit + 16) == 402 && rtword(complete + 16) == 434);

	static constexpr uae_u16 widths[] = {640, 800, 1024};
	static constexpr uae_u16 heights[] = {480, 600, 768};
	static constexpr uae_u32 ids[] = {0x50031000, 0x50041000, 0x50051000};
	uaecptr node = trap_get_long(&ctx, list);
	for (int i = 0; i < 3; ++i) {
		const uaecptr expected = LIBBASE + 64 + i * 224 + 144;
		assert(node == expected);
		assert(std::memcmp(ctx.memory + node + 14, "P96-0:", 6) == 0);
		assert(trap_get_long(&ctx, node + 42) == ids[i]);
		assert(trap_get_word(&ctx, node + 46) == widths[i]);
		assert(trap_get_word(&ctx, node + 48) == heights[i]);
		for (int mode = 0; mode < 3; ++mode) {
			const int slot = mode == 0 ? 1 : mode == 1 ? 2 : 4;
			const uaecptr info = trap_get_long(&ctx, node + 52 + slot * 4);
			assert(info == LIBBASE + 64 + i * 224 + mode * 48);
			assert(trap_get_word(&ctx, info + 18) == widths[i]);
			assert(trap_get_word(&ctx, info + 20) == heights[i]);
			assert(trap_get_byte(&ctx, info + 22) == (mode == 0 ? 8 : mode == 1 ? 16 : 32));
		}
		node = trap_get_long(&ctx, node);
	}
	assert(node == list + 4);
	assert(trap_get_long(&ctx, list + 8) == LIBBASE + 64 + 2 * 224 + 144);

	const uint64_t before = guest_digest(ctx);
	ctx.a[0] = sizeof ctx.memory - 100;
	assert(p6_uaegfx_init_card(&ctx) == 0 && guest_digest(ctx) == before);
	reset_callback(1);
	assert(p6_uaegfx_library_base() == 0 && !p6_uaegfx_selected());
	return 0;
}
"""


class RtgUaegfxInstallTests(unittest.TestCase):
    def test_resident_library_vectors_and_exact_modes(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        includes = ["-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-rtg-install-") as directory:
            temporary = Path(directory)
            probe = temporary / "probe.cpp"
            probe.write_text(textwrap.dedent(PROBE), encoding="utf-8")
            executable = temporary / "probe"
            subprocess.run([
                "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math", "-O1",
                *SANITIZERS, *includes, str(probe),
                str(ROOT / "ports/portable/rtg_device.cpp"),
                str(ROOT / "ports/portable/rtg_memory.cpp"),
                str(ROOT / "ports/portable/rtg_uaegfx.cpp"),
                str(ROOT / "ports/portable/rtg_uaegfx_install.cpp"),
                "-o", str(executable),
            ], check=True, timeout=180)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=120,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_installer_object_has_no_allocator_or_host_graphics(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        with tempfile.TemporaryDirectory(prefix="p6-rtg-install-object-") as directory:
            obj = Path(directory) / "rtg_uaegfx_install.o"
            subprocess.run([
                "clang++", "-std=c++17", "-O2",
                "-I" + str(ROOT / "ports/portable"),
                "-I" + str(ROOT / "build/generated"),
                "-I" + str(tree / "include"), "-I" + str(tree),
                "-c", str(ROOT / "ports/portable/rtg_uaegfx_install.cpp"), "-o", str(obj),
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
