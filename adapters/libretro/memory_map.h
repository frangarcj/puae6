// The memory the frontend may look at (T75): RETRO_ENVIRONMENT_SET_MEMORY_MAPS, RETRO_MEMORY_SYSTEM_RAM and RetroAchievements.
//
// The regions come from p6_memory_regions (bridge/include/p6.h): views of what the engine allocated, never copies, with the bytes in the order of the
// 68000 (big endian; offset n of a region is byte n of that memory). This header only turns them into libretro's descriptors. Nothing here calls the
// engine or the frontend.
//
// What is decided here (docs/02-contracts.md, "Regiones de memoria"):
//   * The descriptors keep the order Chip, Slow, Fast, ROM, with the real Amiga bus addresses: Chip at $000000; Slow (Ranger/trapdoor) at $C00000; the first Zorro II
//     board at $200000 (the address Kickstart gives the first board, which is where the autoconfig chain puts the only board the profile has: before the Kickstart
//     has configured it the board is not mapped, and the descriptor names where it will be); the ROM at the top of the 24-bit space, so that its last byte is
//     $FFFFFF (the engine reports the 512 KiB window $F80000-$FFFFFF for every machine, a 256 KiB Kickstart mirrored in it). Mirrors are not described. A region whose size is not a power of two (1.5 and 1.75 MiB of Slow RAM) is
//     given a select mask for the power of two above it, as libretro asks.
//   * RAM carries RETRO_MEMDESC_SYSTEM_RAM and every descriptor RETRO_MEMDESC_BIGENDIAN. A frontend that has no console table for the Amiga (rcheevos
//     concatenates the SYSTEM_RAM descriptors in the order they were announced) reads Chip, then Slow, then Fast: the order is part of the contract. The ROM is
//     RETRO_MEMDESC_CONST and not system RAM.
//   * RETRO_MEMORY_SYSTEM_RAM is the Chip RAM alone, as PUAE5 has it (and BizHawk's main memory domain, the one its RetroAchievements uses for the Amiga).
//   * The pointers are those of the engine's own buffers. The engine allocates them when the image loads and keeps them while the configuration is the same (memory_reset
//     only reallocates when a size changes, and the sizes are fixed by the options for the whole load), so a reset, a state load or a rewind restores the content in
//     place. The adapter does not rely on that blindly: it asks again after the load, after every unit and after every state load, and announces the map once more if
//     a pointer or a size ever differs (the frontend has to be told, since it keeps the pointers).
//   * A write of the frontend to these buffers (a cheat, a debugger) is a host poke: input of the frontend that the machine did not do, exactly as in BizHawk. It
//     is not part of any state or replay, and it must not be made during a run-ahead branch (the branch is thrown away by the state that follows it, which takes the
//     poke back with it).
#ifndef P6_LIBRETRO_MEMORY_MAP_H
#define P6_LIBRETRO_MEMORY_MAP_H

#include "libretro.h"
#include "p6.h"

#include <stddef.h>
#include <stdint.h>

namespace p6_libretro {

constexpr size_t kMaxMemoryDescriptors = 5;                 // Chip, Slow, Z2 Fast, Z3 Fast, ROM
constexpr size_t kChipStart = 0x000000, kSlowStart = 0xC00000, kFastStart = 0x200000, kAddressSpaceEnd = 0x1000000;

struct MemoryMap {
    retro_memory_descriptor descriptor[kMaxMemoryDescriptors] = {};
    unsigned count = 0;
    const void *system_ram = nullptr;                       // RETRO_MEMORY_SYSTEM_RAM: the Chip RAM
    size_t system_ram_bytes = 0;

    bool same_as(const MemoryMap &other) const
    {
        if (count != other.count || system_ram != other.system_ram || system_ram_bytes != other.system_ram_bytes) return false;
        for (unsigned i = 0; i < count; ++i)
            if (descriptor[i].ptr != other.descriptor[i].ptr || descriptor[i].len != other.descriptor[i].len || descriptor[i].start != other.descriptor[i].start ||
                descriptor[i].flags != other.descriptor[i].flags)
                return false;
        return true;
    }
};

// The smallest power of two that holds `bytes`.
inline size_t power_of_two_above(size_t bytes)
{
    size_t power = 1;
    while (power < bytes) power <<= 1;
    return power;
}

inline retro_memory_descriptor describe_region(const p6_memory_region &region, size_t start, const char *name, uint64_t flags)
{
    retro_memory_descriptor descriptor = {};
    descriptor.flags = flags | RETRO_MEMDESC_BIGENDIAN;
    descriptor.ptr = const_cast<void *>(region.data);
    descriptor.start = start;
    descriptor.len = size_t(region.bytes);
    // A power of two is mapped exactly once (select 0); any other size is the head of the power of two above it.
    const size_t block = power_of_two_above(size_t(region.bytes));
    descriptor.select = block == region.bytes ? 0 : (kAddressSpaceEnd - 1) & ~(block - 1);
    descriptor.addrspace = name;
    return descriptor;
}

// Builds the map from what p6_memory_regions reported. Returns false when there is no Chip RAM to offer (no machine). The regions already carry the size the CPU reaches (T75a: the
// engine allocates 2 MiB for 1.5 MiB of Slow RAM and the bridge reports 1.5), so they are announced as they are and a frontend that concatenates the RAM, like rcheevos, finds
// the Fast RAM where it counts.
inline bool build_memory_map(const p6_memory_region *regions, uint32_t count, MemoryMap *out)
{
    *out = MemoryMap();
    for (uint32_t i = 0; i < count && out->count < kMaxMemoryDescriptors; ++i) {
        const p6_memory_region &region = regions[i];
        if (!region.data || !region.bytes) continue;
        switch (region.type) {
        case P6_REGION_CHIP_RAM:
            out->descriptor[out->count++] = describe_region(region, kChipStart, "CHIP", RETRO_MEMDESC_SYSTEM_RAM);
            out->system_ram = region.data;
            out->system_ram_bytes = size_t(region.bytes);
            break;
        case P6_REGION_SLOW_RAM:
            out->descriptor[out->count++] = describe_region(region, kSlowStart, "SLOW", RETRO_MEMDESC_SYSTEM_RAM);
            break;
        case P6_REGION_FAST_RAM:
            out->descriptor[out->count++] = describe_region(region, kFastStart, "FAST", RETRO_MEMDESC_SYSTEM_RAM);
            break;
        case P6_REGION_Z3_FAST_RAM:
            if (region.flags & P6_REGION_BUS_ADDRESS_VALID)
                out->descriptor[out->count++] = describe_region(region, P6_REGION_BUS_ADDRESS(region.flags), "Z3", RETRO_MEMDESC_SYSTEM_RAM);
            break;
        case P6_REGION_ROM:
            if (region.bytes <= kAddressSpaceEnd) out->descriptor[out->count++] = describe_region(region, kAddressSpaceEnd - size_t(region.bytes), "ROM", RETRO_MEMDESC_CONST);
            break;
        default:
            break;
        }
    }
    return out->system_ram != nullptr;
}

}  // namespace p6_libretro

#endif
