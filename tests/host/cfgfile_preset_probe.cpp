// Real preset initialization and A500 model path; configure_rom remains observed.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "custom.h"
#include "gfxboard.h"
#include "rommgr.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>

static struct uae_prefs prefs{};
static struct uae_prefs first_default{};
static int calls[8], call_count, configure_result = 1;

#include "cfgfile_preset_under_test.h"

// The pristine reference still calls target_default_options first; the patched file (0053)
// excludes that desktop host-defaults hook, so it must not be reached: no stub is provided.
#ifdef TEST_REFERENCE
constexpr int kHook = 1;
void target_default_options(struct uae_prefs *p, int type)
{ assert(p == &prefs && type == 1); calls[call_count++] = 1; }
#else
constexpr int kHook = 0;
#endif
void cfgfile_compatibility_romtype(struct uae_prefs *p)
{ assert(p == &prefs); calls[call_count++] = 2; }
int configure_rom(struct uae_prefs *p, const int *rom, int msg)
{
    assert(p == &prefs && msg == 0);
    assert(rom[0] == 6 && rom[1] == 32 && rom[2] == -1);
    calls[call_count++] = 3;
    return configure_result;
}

static bool boards_clear(const struct ramboard *a, const struct ramboard *b)
{
    const struct ramboard zero{};
    return memcmp(a, &zero, sizeof zero) == 0 && memcmp(b, &zero, sizeof zero) == 0;
}

static void require(bool ok, const char *message)
{
    if (!ok) { fprintf(stderr, "preset probe: %s\n", message); abort(); }
}

static uint64_t stable_digest(const struct uae_prefs *p)
{
    const uae_u32 values[] = {
        (uae_u32)p->cpu_model, (uae_u32)p->fpu_model, (uae_u32)p->cpu_frequency,
        (uae_u32)p->cpu_clock_multiplier, (uae_u32)p->m68k_speed,
        (uae_u32)p->cpu_compatible, (uae_u32)p->cpu_cycle_exact,
        (uae_u32)p->cpu_memory_cycle_exact, (uae_u32)p->blitter_cycle_exact,
        (uae_u32)p->chipset_mask, p->chipmem.size, p->bogomem.size,
        (uae_u32)p->ntscmode, p->seed, (uae_u32)p->config_version,
        (uae_u32)p->cs_compatible,
        (uae_u32)p->cs_rtc, (uae_u32)p->floppyslots[0].dfxtype,
        (uae_u32)p->floppyslots[1].dfxtype,
    };
    uint64_t hash = UINT64_C(1469598103934665603);
    for (uae_u32 value : values) {
        for (int i = 0; i < 4; ++i) {
            hash ^= (value >> (i * 8)) & 0xff;
            hash *= UINT64_C(1099511628211);
        }
    }
    return hash;
}

static void run_case(bool dirty, int rom_result, int ntsc)
{
    memset(&prefs, 0, sizeof prefs);
    prefs.ntscmode = ntsc;
    prefs.nr_floppies = 2;
    prefs.config_version = 0x12345678;
    prefs.seed = 0x87654321;
    for (int i = 0; i < MAX_RAM_BOARDS; ++i) {
        if (dirty) {
            memset(&prefs.fastmem[i], 0xa5, sizeof prefs.fastmem[i]);
            memset(&prefs.z3fastmem[i], 0x5a, sizeof prefs.z3fastmem[i]);
        }
    }
    for (int i = 0; i < MAX_RTG_BOARDS; ++i) {
        prefs.rtgboards[i].rtgmem_size = 0x12340000u + i;
        prefs.rtgboards[i].rtgmem_type = -100 - i;
    }
    for (int i = 0; i < MAX_EXPANSION_BOARDS; ++i)
        memset(&prefs.expansionboard[i], 0xa5, sizeof prefs.expansionboard[i]);

    call_count = 0;
    buildin_default_prefs(&prefs);
    require(call_count == 1 + kHook && (!kHook || calls[0] == 1) && calls[kHook] == 2,
            "default callbacks called in order with expected arguments");
    for (int i = 0; i < MAX_RAM_BOARDS; ++i)
        require(boards_clear(&prefs.fastmem[i], &prefs.z3fastmem[i]),
                "Fast/Z3 RAM slot remains populated");
    for (int i = 0; i < MAX_RTG_BOARDS; ++i)
        require(prefs.rtgboards[i].rtgmem_size == 0 &&
                prefs.rtgboards[i].rtgmem_type == GFXBOARD_UAE_Z3,
                "RTG preset defaults changed unexpectedly");
    for (int i = 0; i < MAX_EXPANSION_BOARDS; ++i) {
        const struct boardromconfig zero{};
        require(memcmp(&prefs.expansionboard[i], &zero, sizeof zero) == 0,
                "expansion ROM slot was not cleared");
    }
    require(prefs.ntscmode == ntsc && prefs.config_version == 0x12345678,
            "preset changed NTSC/config metadata");

    memcpy(&first_default, &prefs, sizeof prefs);
    call_count = 0;
    buildin_default_prefs(&prefs);
    require(call_count == 1 + kHook && (!kHook || calls[0] == 1) && calls[kHook] == 2 &&
            memcmp(&prefs, &first_default, sizeof prefs) == 0,
            "reapplying base preset changed stable state");
    for (int i = 0; i < MAX_RAM_BOARDS; ++i)
        require(boards_clear(&prefs.fastmem[i], &prefs.z3fastmem[i]),
                "reapplied preset retained Fast/Z3 RAM");

    configure_result = rom_result;
    int result = bip_a500(&prefs, 0, 0, 0);
    require(result == rom_result && call_count == 2 + kHook && calls[1 + kHook] == 3,
            "A500 ROM selection callback or result propagation mismatch");
    require(prefs.cpu_model == 68000 && prefs.cpu_cycle_exact &&
            prefs.cpu_memory_cycle_exact && prefs.blitter_cycle_exact,
            "A500 OCS cycle-exact 68000 settings mismatch");
    require(prefs.chipmem.size == 0x80000 && prefs.bogomem.size == 0x80000 &&
            prefs.chipset_mask == CSMASK_OCS && prefs.cs_compatible == CP_A500,
            "A500 512K Chip/Slow or chipset defaults mismatch");
    require(prefs.ntscmode == ntsc && prefs.floppyslots[0].dfxtype == DRV_35_DD &&
            prefs.floppyslots[1].dfxtype == DRV_35_DD,
            "A500 preset failed to preserve NTSC or floppy defaults");
    require(prefs.seed == 0x87654321,
            "A500 preset changed deterministic seed");

    const uint64_t digest = stable_digest(&prefs);
    printf("ntsc=%d result=%d chip=%u slow=%u cpu=%d cycle=%d/%d/%d chipset=%d seed=%08x config=%08x digest=%016" PRIx64 "\n",
           ntsc, result, prefs.chipmem.size, prefs.bogomem.size, prefs.cpu_model,
           prefs.cpu_cycle_exact, prefs.cpu_memory_cycle_exact,
           prefs.blitter_cycle_exact, prefs.chipset_mask, prefs.seed,
           prefs.config_version, digest);

    result = bip_a500(&prefs, 3, 0, 0);
    require(result == rom_result && call_count == 3 + kHook && calls[2 + kHook] == 3 &&
            prefs.bogomem.size == 0 && prefs.chipmem.size == 0x80000 &&
            prefs.cs_rtc == 0,
            "A500 no-Slow configuration inherited Fast/Z3 RAM or RTC state");
    require(prefs.ntscmode == ntsc && prefs.seed == 0x87654321,
            "A500 preset changed NTSC or deterministic seed");
}

int main(int argc, char **argv)
{
    const bool dirty = argc > 1 && strcmp(argv[1], "dirty") == 0;
    const int rom_result = argc > 2 && strcmp(argv[2], "rom-fail") == 0 ? 0 : 1;
    run_case(dirty, rom_result, 0);
    run_case(dirty, rom_result, 1);
    return 0;
}
