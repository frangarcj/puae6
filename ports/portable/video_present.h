/* PUAE6 video presentation provider: the frame buffer belongs to the bridge.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Replaces the Direct3D/GDI half of od-win32 (win32gfx.cpp) for the deterministic
 * A500/CD32 profiles. The renderer draws into a statically allocated XRGB8888
 * buffer through the upstream presentation calls (lockscr,
 * unlockscr, render_screen, show_screen, target_graphics_buffer_update, ...); this
 * header is the small accessor surface the bridge uses to hand the result to a
 * frontend. CD32 FMV additionally owns one staging buffer which is never exposed:
 * drawing.cpp composes it into the normal buffer before unlock. Nothing here reads
 * a display, a refresh rate or a clock.
 *
 * Bridge call order (the counterpart of graphics_setup + graphics_init):
 *   1. effective prefs final (currprefs) and custom_init()/init_m68k() done;
 *   2. p6_video_init()      once per load, BEFORE the first m68k reset;
 *   3. every p6_run_frame:  the scheduler returns at the video-unit boundary, then
 *      p6_video_status() == 0 ? p6_video_present() + p6_video_frame_get() : error;
 *   4. unload: graphics_leave() (already called by do_leave_program()).
 * p6_video_clear() is the optional deterministic wipe for p6_reset; p6_video_rebind()
 * is required after every native state load (see the state inventory in
 * video_present.cpp): it re-derives the buffer pointer and drops the captured frame.
 *
 * Pixels are one host-endian uint32_t 0x00RRGGBB (P6_PIXEL_XRGB8888). On the
 * little-endian targets the memory bytes are B, G, R, X with X always zero.
 */
#ifndef P6_VIDEO_PRESENT_H
#define P6_VIDEO_PRESENT_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

struct uae_prefs;

/* Capacity of the raster. Derived from the engine's own output arithmetic
 * (custom.cpp compute_framesync: inwidth = hpos << (resolution + 1), inheight =
 * vpos << vresolution) for PAL and NTSC at superhires x vertical doubling:
 *   width  = (MAXHPOS_PAL + 1) << (RES_SUPERHIRES + 1)  = 228 << 3 = 1824
 *   height = (MAXVPOS_PAL + 1) << VRES_DOUBLE           = 313 << 1 =  626
 * The stride is fixed at the capacity so a mode change never restrides and each
 * row starts on a 64-byte boundary. Modes beyond it (ECS programmed beam modes,
 * interlaced multiscan) are rejected before anything is written; see
 * P6_VIDEO_ST_MODE_TOO_LARGE. video_present.cpp static_asserts these against the
 * engine constants. The bridge reports these two values in p6_caps. */
enum {
 P6_VIDEO_PIXEL_BYTES = 4,
 P6_VIDEO_MAX_WIDTH = 1824,
 P6_VIDEO_MAX_HEIGHT = 626,
 P6_VIDEO_STRIDE_BYTES = P6_VIDEO_MAX_WIDTH * P6_VIDEO_PIXEL_BYTES,
 P6_VIDEO_BUFFER_BYTES = P6_VIDEO_STRIDE_BYTES * P6_VIDEO_MAX_HEIGHT
};

/* Result of p6_video_validate_config / p6_video_init (0 = accepted). The options
 * named here would otherwise be honoured silently wrongly by this provider. */
enum {
 P6_VIDEO_CFG_OK = 0,
 P6_VIDEO_CFG_NULL = 1,
 P6_VIDEO_CFG_RESOLUTION = 2,     /* gfx_resolution lores..superhires, gfx_vresolution 0..1 */
 P6_VIDEO_CFG_AUTORESOLUTION = 3, /* engine would rewrite gfx_resolution / call graphics_reset */
 P6_VIDEO_CFG_FRAMESKIP = 4,      /* gfx_framerate > 1 skips the drawing of whole units */
 P6_VIDEO_CFG_AUX_BUFFER = 5,     /* colour burst, grayscale, generic genlock or monitor emulation */
 P6_VIDEO_CFG_COLOR_MATH = 6,     /* luminance/contrast/gamma reach libm pow(): not portable-exact */
 P6_VIDEO_CFG_INDICATOR = 7       /* refresh indicator overlay */
};

/* Current unsupported condition of the presentation (0 = none). Not sticky: it
 * reflects the engine's present mode, so a guest that leaves a too-large mode
 * recovers. While non-zero, p6_video_present() hands nothing over. */
enum {
 P6_VIDEO_ST_NOT_READY = 1u << 0,      /* before p6_video_init / after graphics_leave */
 P6_VIDEO_ST_MODE_TOO_LARGE = 1u << 1, /* engine asked for more than MAX_WIDTH x MAX_HEIGHT */
 P6_VIDEO_ST_AUX_BUFFER = 1u << 2      /* engine selected an unsupported, uncomposed auxiliary pass */
};

/* p6_video_frame_info.flags: facts captured when the drawing pass ended. */
enum {
 P6_VIDEO_FRAME_DISCARD = 1u << 0,     /* engine passed the display-reset marker (-2) to unlockscr:
                                          the pass is not meant to be shown (mode being reset) */
 P6_VIDEO_FRAME_INTERLACED = 1u << 1,  /* interlace_seen > 0 */
 P6_VIDEO_FRAME_LONG_FIELD = 1u << 2   /* lof_display set: the long field/frame (LOF) */
};

typedef struct p6_video_rect {
 uint32_t x, y, width, height;
} p6_video_rect;

/* Borrowed view of the last handed-over frame. The pointer is stable for the life of
 * the image and its content changes on the next mutating call; nothing is copied. */
typedef struct p6_video_frame_info {
 const void *pixels;          /* row 0, column 0 of the raster */
 uint64_t bytes;              /* addressable bytes from pixels: stride_bytes * height */
 uint32_t width, height;      /* engine output raster (outwidth x outheight) when the pass ended */
 uint32_t stride_bytes;       /* constant P6_VIDEO_STRIDE_BYTES; never width * 4 by contract */
 uint32_t flags;              /* P6_VIDEO_FRAME_* */
 p6_video_rect active;        /* today the whole raster; cropping is a bridge decision (T08) */
 uint64_t frame_counter;      /* hand-overs since p6_video_init, 1 for the first */
 uint64_t pass_counter;       /* drawing passes (buffer unlocks) ended when the frame was captured */
} p6_video_frame_info;

int p6_video_validate_config(const struct uae_prefs *prefs);
int p6_video_init(void);
void p6_video_clear(void);
void p6_video_rebind(void);
uint32_t p6_video_status(void);
/* 1 when a new frame was handed over (frame_counter advanced), else 0. Same effect as
 * the pair render_screen(0, 1, true) + show_screen(0, 0) the engine's full_redraw_all
 * uses; the engine never calls that pair by itself in this profile. */
int p6_video_present(void);
/* 0 and *out filled once a frame was handed over since the last p6_video_init,
 * p6_video_clear or p6_video_rebind; -1 for NULL or when there is none. */
int p6_video_frame_get(p6_video_frame_info *out);

#ifdef __cplusplus
}
#endif
#endif
