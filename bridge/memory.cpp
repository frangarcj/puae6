// p6_memory_regions: the memory a host may look at or poke (docs/02-contracts.md, "Regiones de memoria").
//
// The regions are views of what the engine allocated, in the engine's own order (the 68000's, big endian), not copies: the
// host reads and writes the very bytes the CPU sees. The pointers are resolved at every call, never cached: the engine
// allocates and frees its RAM in memory_reset(), so a pointer kept across a reset could be left dangling.
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
        {P6_REGION_CHIP_RAM, P6_REGION_WRITABLE, &chipmem_bank},
        {P6_REGION_SLOW_RAM, P6_REGION_WRITABLE, &bogomem_bank},
        {P6_REGION_FAST_RAM, P6_REGION_WRITABLE, &fastmem_bank[0]},
        {P6_REGION_ROM, 0, &kickmem_bank},
    };
    p6_memory_region found[sizeof sources / sizeof sources[0]];
    uint32_t n = 0;
    for (const Source &source : sources)
        if (source.bank->baseaddr && source.bank->allocated_size)
            found[n++] = p6_memory_region{source.type, source.flags, source.bank->baseaddr, source.bank->allocated_size};

    *count = n;
    if (!regions) return P6_OK;
    if (capacity < n) return p6_fail(error, P6_ERROR_CAPACITY, "regions is too small");
    for (uint32_t i = 0; i < n; ++i) regions[i] = found[i];
    return P6_OK;
}
