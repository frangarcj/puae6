// p6_memory_regions: the memory a host may look at or poke (docs/02-contracts.md, "Regiones de memoria").
//
// The regions are views of what the engine allocated, in the engine's own order (the 68000's, big endian), not copies: the
// host reads and writes the very bytes the CPU sees. The pointers are resolved at every call, never cached: the engine
// allocates and frees its RAM in memory_reset(), so a pointer kept across a reset could be left dangling. (T75 measured that the buffers do not move while the
// configuration is the same: an adapter that hands them to its frontend keeps them but asks again after every reset, state load and unit and tells its frontend if one differs.)
//
// The size is what the CPU reaches, not what was allocated (T75a): the engine allocates 2 MiB of Slow RAM for the 1.5 MiB and 1.75 MiB options (a power of two,
// bogomem_bank.reserved_size) but maps only currprefs.bogomem.size of it, so the tail is dead memory that no CPU access touches.
#include "error_util.h"
#include "session.h"

#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"

using p6_session::Guard;
using p6_session::State;
using p6_session::session;

namespace {

struct Source {
    uint32_t type;
    uint32_t flags;
    const addrbank *bank;
    uint64_t reach;  // 0: all that was allocated; otherwise the bytes of it that the CPU can address
};

}  // namespace

extern "C" p6_result p6_memory_regions(p6_memory_region *regions, uint32_t capacity, uint32_t *count, p6_error *error)
{
    if (!p6_error_usable(error)) return P6_ERROR_BAD_ARGUMENT;
    p6_error_clear(error);
    Guard guard;
    if (!guard.ok) return p6_fail(error, P6_ERROR_BUSY, "another call is in progress");
    if (session.state != State::Loaded)
        return p6_fail(error, P6_ERROR_BAD_STATE, session.state == State::Faulted ? "the image faulted; unload it" : "no image is loaded");
    if (!count) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "count is null");
    if (!regions != !capacity) return p6_fail(error, P6_ERROR_BAD_ARGUMENT, "regions and capacity disagree");

    const Source sources[] = {
        {P6_REGION_CHIP_RAM, P6_REGION_WRITABLE, &chipmem_bank, 0},
        {P6_REGION_SLOW_RAM, P6_REGION_WRITABLE, &bogomem_bank, currprefs.bogomem.size},
        {P6_REGION_FAST_RAM, P6_REGION_WRITABLE, &fastmem_bank[0], 0},
        {P6_REGION_Z3_FAST_RAM, P6_REGION_WRITABLE, &z3fastmem_bank[0], 0},
        {P6_REGION_ROM, 0, &kickmem_bank, 0},
    };
    p6_memory_region found[sizeof sources / sizeof sources[0]];
    uint32_t n = 0;
    for (const Source &source : sources) {
        uint64_t bytes = source.bank->allocated_size;
        if (source.reach && bytes > source.reach) bytes = source.reach;
        if (source.bank->baseaddr && bytes)
            found[n++] = p6_memory_region{source.type, source.flags, source.bank->baseaddr, bytes};
    }

    *count = n;
    if (!regions) return P6_OK;
    if (capacity < n) return p6_fail(error, P6_ERROR_CAPACITY, "regions is too small");
    for (uint32_t i = 0; i < n; ++i) regions[i] = found[i];
    return P6_OK;
}
