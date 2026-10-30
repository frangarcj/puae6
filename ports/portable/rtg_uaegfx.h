/* Bounded Picasso96 uaegfx trap surface for the portable RTG device. */
#ifndef P6_RTG_UAEGFX_H
#define P6_RTG_UAEGFX_H

#include "traps.h"

void p6_uaegfx_reset(void);
int p6_uaegfx_selected(void);
int p6_uaegfx_install_code(uaecptr start);
uaecptr p6_uaegfx_alloc(TrapContext *ctx);
uaecptr p6_uaegfx_library_base(void);
uae_u32 REGPARAM2 p6_uaegfx_init_card(TrapContext *ctx);

uae_u32 REGPARAM2 p6_uaegfx_find_card(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_set_gc(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_set_panning(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_set_switch(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_set_display(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_set_color_array(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_calculate_bytes_per_row(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_get_compatible_formats(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_fill_rect(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_blit_rect(TrapContext *ctx);
uae_u32 REGPARAM2 p6_uaegfx_blit_rect_nomask_complete(TrapContext *ctx);

#endif
