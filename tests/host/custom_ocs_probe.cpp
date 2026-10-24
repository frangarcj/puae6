// Reference and portable variants run in separate executables: their real
// color_entry layouts differ, and must never share an ODR type definition.
#ifdef TEST_REFERENCE
#define AGA 1
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
static color_entry agnus_colors;
static bool color_table_changed;
uae_u16 bplcon2, bplcon3;
#include "reference_colors.h"
#else
#include "custom.cpp"
#include "diagnostics.h"
#endif
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>

bool aga_mode, ecs_denise, direct_rgb;
xcolnr xcolors[4096];
unsigned int xredcolors[256], xgreencolors[256], xbluecolors[256];
color_entry denise_colors;
static unsigned random_calls;
uae_u32 uaerand() { return ++random_calls * 2654435761u; }
#include "reset_color_loop.h"

int main()
{
    for (unsigned i = 0; i < 4096; ++i) xcolors[i] = 0xa5000000u | (i * 7919u);
    unsigned hash = 2166136261u;
    for (bool ecs : {false, true}) {
        ecs_denise = ecs;
        for (int reg = 0; reg < 32; ++reg) {
            for (unsigned value = 0; value < 65536; ++value) {
                color_table_changed = false;
                COLOR_WRITE(value, reg);
                assert(color_table_changed);
                assert(agnus_colors.color_regs_ecs[reg] == (value & 0xfff));
                assert(agnus_colors.color_regs_genlock[reg] == (ecs && (value & 0x8000)));
                assert(agnus_colors.acolors[reg] == xcolors[value & 0xfff]);
                assert(color_reg_get(&agnus_colors, reg) == (value & 0xfff));
                uae_u8 r, g, b;
                assert(get_custom_color_reg(reg, &r, &g, &b));
                assert(r == ((value >> 8) & 15) * 17);
                assert(g == ((value >> 4) & 15) * 17);
                assert(b == (value & 15) * 17);
                hash = (hash ^ (r | (g << 8) | (b << 16))) * 16777619u;
            }
        }
    }
    for (int reg : {-1, 32, 255, 256, 65536}) {
        uae_u8 r = 1, g = 2, b = 3;
        assert(!get_custom_color_reg(reg, &r, &g, &b));
        assert(r == 1 && g == 2 && b == 3);
    }
    // Exact reset loop extracted from each version; retain its PRNG sequence
    // even when there are no AGA arrays to initialize in the portable layout.
    reset_color_loop();
    assert(random_calls == 510);
    assert(uaerand() == 511u * 2654435761u);
#ifndef TEST_REFERENCE
    p6_diag_reset();
    uae_abort("literal: %s", "%s %n 100%");
    p6_diagnostic d{};
    assert(p6_diag_read(&d, 1) == 1 && d.kind == P6_DIAG_ERROR);
    assert(!strcmp(d.text, "literal: %s %n 100%"));
    char large[2048]; memset(large, 'A', sizeof large - 1); large[2047] = 0;
    uae_abort("%s", large);
    assert(p6_diag_read(&d, 1) == 1 && d.kind == P6_DIAG_LOG);
    assert(d.flags & P6_DIAG_TRUNCATED);
    assert(d.length == P6_DIAG_TEXT_CAPACITY - 1 && d.text[d.length] == 0);
#endif
    std::printf("%u %u\n", hash, random_calls);
}
