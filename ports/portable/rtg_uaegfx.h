/* Bounded Picasso96 uaegfx trap surface for the portable RTG device. */
#ifndef P6_RTG_UAEGFX_H
#define P6_RTG_UAEGFX_H

#include "traps.h"

enum {
	P6_UAEGFX_SNAPSHOT_VERSION = 1,
	P6_UAEGFX_RESIDENT_SNAPSHOT_VERSION = 1
};

typedef struct p6_uaegfx_snapshot {
	uae_u32 version;
	uae_u32 width;
	uae_u32 height;
	uae_u32 depth;
	uae_u32 card_found;
	uae_u32 selected;
} p6_uaegfx_snapshot;

typedef struct p6_uaegfx_resident_snapshot {
	uae_u32 version;
	uaecptr library_base;
} p6_uaegfx_resident_snapshot;

void p6_uaegfx_reset(void);
int p6_uaegfx_selected(void);
int p6_uaegfx_snapshot_get(p6_uaegfx_snapshot *out);
int p6_uaegfx_snapshot_validate(const p6_uaegfx_snapshot *snapshot);
int p6_uaegfx_snapshot_apply(const p6_uaegfx_snapshot *snapshot);
int p6_uaegfx_resident_snapshot_get(p6_uaegfx_resident_snapshot *out);
int p6_uaegfx_resident_snapshot_validate(const p6_uaegfx_resident_snapshot *snapshot);
int p6_uaegfx_resident_snapshot_apply(const p6_uaegfx_resident_snapshot *snapshot);
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
