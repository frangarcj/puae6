/* Static WinUAE memory-bank view of the bounded RTG device.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "sysconfig.h"
#include "sysdeps.h"

#include "options.h"
#include "memory.h"
#include "rtg_device.h"
#include "rtg_memory.h"

MEMORY_FUNCTIONS(gfxmem);

addrbank gfxmem_bank = {
	gfxmem_lget, gfxmem_wget, gfxmem_bget,
	gfxmem_lput, gfxmem_wput, gfxmem_bput,
	gfxmem_xlate, gfxmem_check, nullptr, _T("p6rtg"), _T("RTG RAM"),
	gfxmem_lget, gfxmem_wget,
	ABFLAG_RAM | ABFLAG_RTG | ABFLAG_THREADSAFE | ABFLAG_DIRECTACCESS
		| ABFLAG_CACHE_ENABLE_ALL | ABFLAG_NOALLOC,
	0, 0
};

addrbank *gfxmem_banks[4] = {&gfxmem_bank, nullptr, nullptr, nullptr};

namespace {

constexpr uae_u32 kZ3Limit = 0xf0000000u;

void bind_storage()
{
	gfxmem_bank.baseaddr = p6_rtg_vram_data();
	gfxmem_bank.baseaddr_direct_r = gfxmem_bank.baseaddr;
	gfxmem_bank.baseaddr_direct_w = gfxmem_bank.baseaddr;
	gfxmem_bank.mask = P6_RTG_VRAM_BYTES - 1;
	gfxmem_bank.reserved_size = P6_RTG_VRAM_BYTES;
	gfxmem_bank.allocated_size = P6_RTG_VRAM_BYTES;
}

} // namespace

int p6_rtg_memory_enable(void)
{
	p6_rtg_reset();
	gfxmem_bank.start = 0;
	gfxmem_bank.startmask = 0;
	gfxmem_bank.startaccessmask = 0;
	gfxmem_bank.flags &= ~ABFLAG_MAPPED;
	bind_storage();
	return p6_rtg_memory_rebind();
}

void p6_rtg_memory_disable(void)
{
	p6_rtg_reset();
	gfxmem_bank.flags &= ~ABFLAG_MAPPED;
	gfxmem_bank.start = 0;
	gfxmem_bank.startmask = 0;
	gfxmem_bank.startaccessmask = 0;
	gfxmem_bank.mask = 0;
	gfxmem_bank.allocated_size = 0;
	gfxmem_bank.reserved_size = 0;
	gfxmem_bank.baseaddr = nullptr;
	gfxmem_bank.baseaddr_direct_r = nullptr;
	gfxmem_bank.baseaddr_direct_w = nullptr;
}

int p6_rtg_memory_set_bus_base(uint32_t start)
{
	if (gfxmem_bank.allocated_size != P6_RTG_VRAM_BYTES || !gfxmem_bank.baseaddr)
		return P6_RTG_BAD_STATE;
	/* The board advertises a 16 MiB physical Z3 aperture with a 4 MiB
	 * populated subsize, so Autoconfig must assign an aperture-aligned base. */
	if (start < Z3BASE_UAE || start > kZ3Limit - P6_RTG_APERTURE_BYTES
		|| (start & (P6_RTG_APERTURE_BYTES - 1)) != 0)
		return P6_RTG_BOUNDS;
	gfxmem_bank.start = start;
	gfxmem_bank.startmask = start;
	gfxmem_bank.startaccessmask = start & gfxmem_bank.mask;
	return P6_RTG_OK;
}

int p6_rtg_memory_rebind(void)
{
	if (gfxmem_bank.allocated_size != P6_RTG_VRAM_BYTES
		|| gfxmem_bank.reserved_size != P6_RTG_VRAM_BYTES
		|| gfxmem_bank.mask != P6_RTG_VRAM_BYTES - 1)
		return P6_RTG_BAD_STATE;
	bind_storage();
	return p6_rtg_rebind();
}
