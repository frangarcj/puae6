/* Probe the real WinUAE addrbank ABI over PUAE6's static RTG storage. */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "rtg_device.h"
#include "rtg_memory.h"

#include <cassert>
#include <cstdint>
#include <cstring>

int main()
{
	static_assert(P6_RTG_VRAM_BYTES == 0x00400000, "populated subsize");
	static_assert(P6_RTG_APERTURE_BYTES == 0x01000000, "physical aperture");
	assert(gfxmem_banks[0] == &gfxmem_bank);
	assert(!gfxmem_banks[1] && !gfxmem_banks[2] && !gfxmem_banks[3]);

	p6_rtg_memory_disable();
	assert(!gfxmem_bank.baseaddr && !gfxmem_bank.allocated_size && !gfxmem_bank.reserved_size);
	assert(p6_rtg_memory_set_bus_base(0x22000000) == P6_RTG_BAD_STATE);
	assert(p6_rtg_memory_enable() == P6_RTG_OK);
	assert(gfxmem_bank.baseaddr == p6_rtg_vram_data());
	assert(gfxmem_bank.baseaddr_direct_r == gfxmem_bank.baseaddr);
	assert(gfxmem_bank.baseaddr_direct_w == gfxmem_bank.baseaddr);
	assert(gfxmem_bank.allocated_size == P6_RTG_VRAM_BYTES);
	assert(gfxmem_bank.reserved_size == P6_RTG_VRAM_BYTES);
	assert(gfxmem_bank.mask == P6_RTG_VRAM_BYTES - 1);
	assert((gfxmem_bank.flags & (ABFLAG_RAM | ABFLAG_RTG | ABFLAG_DIRECTACCESS | ABFLAG_NOALLOC))
		== (ABFLAG_RAM | ABFLAG_RTG | ABFLAG_DIRECTACCESS | ABFLAG_NOALLOC));

	addrbank before = gfxmem_bank;
	const uint64_t first = *reinterpret_cast<const uint64_t *>(p6_rtg_vram_data());
	for (uint32_t invalid : {0u, 0x0f000000u, 0x22010000u, 0xef100000u, 0xff000000u}) {
		assert(p6_rtg_memory_set_bus_base(invalid) == P6_RTG_BOUNDS);
		assert(std::memcmp(&before, &gfxmem_bank, sizeof before) == 0);
		assert(*reinterpret_cast<const uint64_t *>(p6_rtg_vram_data()) == first);
	}

	constexpr uint32_t base = 0x22000000;
	assert(p6_rtg_memory_set_bus_base(base) == P6_RTG_OK);
	assert(gfxmem_bank.start == base && gfxmem_bank.startmask == base);
	assert(gfxmem_bank.startaccessmask == (base & (P6_RTG_VRAM_BYTES - 1)));
	assert(gfxmem_bank.check(base, 4));
	assert(gfxmem_bank.check(base + P6_RTG_VRAM_BYTES - 1, 1));
	assert(!gfxmem_bank.check(base + P6_RTG_VRAM_BYTES - 1, 2));
	assert(gfxmem_bank.xlateaddr(base) == p6_rtg_vram_data());
	assert(gfxmem_bank.xlateaddr(base + P6_RTG_VRAM_BYTES - 1)
		== p6_rtg_vram_data() + P6_RTG_VRAM_BYTES - 1);

	gfxmem_bank.lput(base, 0x11223344);
	gfxmem_bank.wput(base + 4, 0x5566);
	gfxmem_bank.bput(base + 6, 0x77);
	const uint8_t *bytes = p6_rtg_vram_data();
	const uint8_t expected[] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77};
	assert(std::memcmp(bytes, expected, sizeof expected) == 0);
	assert(gfxmem_bank.lget(base) == 0x11223344);
	assert(gfxmem_bank.wget(base + 4) == 0x5566);
	assert(gfxmem_bank.bget(base + 6) == 0x77);
	assert(gfxmem_bank.lgeti(base) == 0x11223344);
	assert(gfxmem_bank.wgeti(base + 4) == 0x5566);

	const uint32_t tail = base + P6_RTG_VRAM_BYTES - 4;
	gfxmem_bank.lput(tail, 0xa1b2c3d4);
	assert(gfxmem_bank.lget(tail) == 0xa1b2c3d4);
	assert(bytes[P6_RTG_VRAM_BYTES - 4] == 0xa1 && bytes[P6_RTG_VRAM_BYTES - 1] == 0xd4);

	/* Rebinding derives every direct pointer from the module-owned allocation
	 * while preserving bus metadata and contents. */
	gfxmem_bank.baseaddr = reinterpret_cast<uae_u8 *>(uintptr_t(0x1234));
	gfxmem_bank.baseaddr_direct_r = nullptr;
	gfxmem_bank.baseaddr_direct_w = nullptr;
	assert(p6_rtg_memory_rebind() == P6_RTG_OK);
	assert(gfxmem_bank.baseaddr == p6_rtg_vram_data());
	assert(gfxmem_bank.start == base && gfxmem_bank.lget(tail) == 0xa1b2c3d4);

	p6_rtg_memory_disable();
	assert(!gfxmem_bank.baseaddr && !gfxmem_bank.allocated_size && !gfxmem_bank.start);
	assert(!(gfxmem_bank.flags & ABFLAG_MAPPED));
	for (size_t i = 0; i < p6_rtg_vram_size(); ++i) assert(p6_rtg_vram_data()[i] == 0);
	return 0;
}
