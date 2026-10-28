/* PUAE6 video presentation provider. SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Defines the presentation symbols that drawing.cpp, custom.cpp, devices.cpp,
 * gfxutil.cpp, inputdevice.cpp and main.cpp import from the host graphics layer
 * (od-win32/win32gfx.cpp upstream). The normal drawbuffer and the CD32 FMV
 * staging tempbuffer are fixed-size XRGB8888 guest allocations. A CD32 FMV pass
 * is drawn into the staging buffer and composed into the normal buffer before
 * hand-over; unsupported auxiliary consumers remain refused. No window, texture,
 * display size, refresh rate, clock or thread exists here.
 *
 * Engine expectations this file satisfies (file:line in build/upstream):
 *  - drawing.cpp:264,5415  lockscr(drawbuffer,...) must return non-zero or the
 *    line is dropped and the screen is declared lost (notice_screen_contents_lost).
 *  - drawing.cpp:5443      unlockscr(drawbuffer, y0, y1) once per drawing pass
 *    (end_draw_denise, from vsync_display_render at custom.cpp:5214); it can be
 *    called again on an unlocked buffer (drawing.cpp:2157 after denise_lock),
 *    so it is idempotent.
 *  - drawing.cpp:1730-1745 get_row(): rows come from inbuffer->bufmem + line *
 *    rowbytes, valid only for line < height_allocated and outwidth <=
 *    width_allocated; anything else is diverted to a scratch row.
 *  - drawing.cpp:1315      init_row_map() abort()s if height_allocated >
 *    max_uae_height, hence max_uae_height is exactly the allocation capacity.
 *  - drawing.cpp:1770      setxlinebuffer(): rows >= max_uae_height are discarded.
 *  - custom.cpp:1599-1791  compute_framesync(): set_drawbuffer() picks the
 *    inbuffer, the engine writes inwidth/outwidth/inheight/outheight on it, then
 *    target_graphics_buffer_update(0,false) must publish the allocation, and the
 *    engine clamps its sizes to width_allocated/height_allocated afterwards.
 *  - custom.cpp:2292       updatedisplayarea(0) after a refresh-rate change.
 *  - drawing.cpp:2254      vsync_handle_check() polls check_prefs_changed_gfx()
 *    every vsync; non-zero makes the engine reset_drawing().
 *  - gfxutil.cpp:50,146    flashscreen / gfx_hdr select colour-table variants.
 *  - inputdevice.cpp:2693 get_mouse_position() (and, before patch 0056, tablet_lightpen /
 *    mouseedge) call getgfxoffset() to map pointer coordinates.
 *  - devices.cpp:435       graphics_leave() at unload.
 *  - main.cpp:181,186      max_uae_width/height clamp the size preferences.
 */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include "video_present.h"
#include "fmv_state.h"

/* Capacity against the engine's own limits. */
static_assert(sizeof(xcolnr) == P6_VIDEO_PIXEL_BYTES, "XRGB8888 pixel is one 32-bit word");
static_assert(RES_MAX == RES_SUPERHIRES && VRES_MAX == VRES_DOUBLE, "resolution range");
static_assert(P6_VIDEO_MAX_WIDTH >= ((MAXHPOS_PAL + 1) << (RES_SUPERHIRES + 1)),
              "PAL line at superhires");
static_assert(MAXHPOS_NTSC <= MAXHPOS_PAL && MAXVPOS_NTSC <= MAXVPOS_PAL,
              "PAL is the larger standard");
static_assert(P6_VIDEO_MAX_HEIGHT >= ((MAXVPOS_PAL + 1) << VRES_DOUBLE), "PAL long frame, doubled");
static_assert(P6_VIDEO_MAX_WIDTH <= MAX_PIXELS_PER_LINE,
              "drawing.cpp row_tmp8 (scratch row) is MAX_PIXELS_PER_LINE pixels");
static_assert(P6_VIDEO_MAX_HEIGHT <= (MAXVPOS + 1) * 2, "drawing.cpp line maps are (MAXVPOS + 1) * 2");
static_assert(P6_VIDEO_STRIDE_BYTES % 64 == 0, "rows start on a cache line");

/* Read by main.cpp (preference clamps) and drawing.cpp (init_row_map abort, setxlinebuffer). */
int max_uae_width = P6_VIDEO_MAX_WIDTH;
int max_uae_height = P6_VIDEO_MAX_HEIGHT;
/* Read by gfxutil.cpp. Nothing writes them in this profile: constants. */
int flashscreen = 0;
bool gfx_hdr = false;

/* The frame buffer is ordinary guest storage: a Waterbox snapshot carries it and
 * its address never changes; it is not moved to invisible memory. */
alignas(64) static uae_u32 pixels[(P6_VIDEO_BUFFER_BYTES) / P6_VIDEO_PIXEL_BYTES];
alignas(64) static uae_u32 aux_pixels[(P6_VIDEO_BUFFER_BYTES) / P6_VIDEO_PIXEL_BYTES];

namespace {

/* What the renderer told us when it finished a drawing pass (an unlock). */
struct Pass {
 uint32_t width = 0, height = 0, flags = 0;
 uint64_t index = 0;
};

/* State inventory for save/restore (T15, Waterbox WB03).
 *  Causal, must be saved or reconstructed exactly:
 *   - the pixel buffer, rows [0, height_allocated) x [0, width_allocated): an interlaced
 *     weave keeps the other field's lines here, and everything outside it is zero;
 *   - adisplays[0].gfxvidinfo.drawbuffer geometry and lock state (width_allocated,
 *     height_allocated, in/out sizes, offsets, locked): engine-owned, listed here because
 *     their meaning is defined by this file;
 *   - State: ready, mode_valid, mode_w, mode_h (decide when a mode update clears pixels),
 *     mode_too_large (lockscr refusal), reset_pending (a drawing reset owed to the engine).
 *  Derived, recompute: drawbuffer.bufmem/bufmemend/rowbytes/pixbytes (p6_video_rebind:
 *   a saved absolute pointer is invalid in another process), xcolors[] and the other
 *   colour tables (alloc_colors64k is a pure function of the preferences).
 *  Presentation only, never read by the hardware model: render_ok, have_pass, have_frame,
 *   pass, shown, passes, frames, reset_requests, aux_buffer. p6_video_rebind() drops them;
 *   the bridge's own video-unit counter is authoritative across a restore. */
struct State {
 bool ready = false;
 bool mode_valid = false;      // an accepted mode exists (mode_w x mode_h)
 bool mode_too_large = false;  // the last requested mode exceeded the capacity
 bool aux_buffer = false;      // the last pass used an unsupported, uncomposed auxiliary buffer
 bool reset_pending = false;   // graphics_reset() asked for a drawing reset
 bool render_ok = false;       // render_screen() succeeded, show_screen() may commit
 bool have_pass = false, have_frame = false;
 int mode_w = 0, mode_h = 0;
 uint64_t passes = 0, frames = 0, reset_requests = 0;
 Pass pass;                    // last drawing pass
 Pass shown;                   // pass captured by the last hand-over
};
State g;

vidbuf_description &info()
{
 return adisplays[0].gfxvidinfo;
}

vidbuffer &draw()
{
 return info().drawbuffer;
}

/* The buffer address and layout the engine expects while (and after) locking. */
void bind(vidbuffer &vb)
{
 const bool auxiliary = &vb == &info().tempbuffer;
 vb.bufmem = reinterpret_cast<uae_u8 *>(auxiliary ? aux_pixels : pixels);
 vb.bufmemend = vb.bufmem + P6_VIDEO_BUFFER_BYTES - P6_VIDEO_STRIDE_BYTES;
 // drawing.cpp uses this field as the capability bit for its auxiliary pass.
 // Keep the normal drawbuffer's historical contract (static bufmem only).
 vb.bufmem_allocated = auxiliary ? vb.bufmem : nullptr;
 vb.rowbytes = P6_VIDEO_STRIDE_BYTES;
 vb.pixbytes = P6_VIDEO_PIXEL_BYTES;
 vb.monitor_id = 0;
 vb.initialized = true;
 vb.vram_buffer = true;  // freevidbuffer() must never xfree() static storage
}

void clear_rows(int width, int height)
{
 for (int y = 0; y < height; ++y)
  memset(reinterpret_cast<uae_u8 *>(pixels) + (size_t)y * P6_VIDEO_STRIDE_BYTES, 0,
         (size_t)width * P6_VIDEO_PIXEL_BYTES);
}

/* Zero everything of the allocation outside the raster the pass reports: the columns
 * [w, stride) of the rows [0, h) and every column of the rows [h, allocated). The renderer
 * checks only that the width it was told fits the allocation (drawing.cpp get_row), not that
 * the line it is drawing does: while the guest has just reprogrammed the beam (HTOTAL, BEAMCON0)
 * the first lines of the new geometry are longer than the outwidth the engine still holds
 * (T23g measured 30 pixels of row 1, columns 756..785, on an NTSC A600 programmed for 150
 * clocks of SuperHires). They land inside the row of the array, never outside it, and the
 * contract says everything outside `active` is zero. Cost: one memset of the tail of each row,
 * (stride - w) * 4 bytes, about 4 KiB per row of a 756 pixel raster. */
void clear_outside(int width, int height, int allocated_height)
{
 uae_u8 *base = reinterpret_cast<uae_u8 *>(pixels);
 for (int y = 0; y < height; ++y)
  memset(base + (size_t)y * P6_VIDEO_STRIDE_BYTES + (size_t)width * P6_VIDEO_PIXEL_BYTES, 0,
         (size_t)(P6_VIDEO_MAX_WIDTH - width) * P6_VIDEO_PIXEL_BYTES);
 if (allocated_height > height)
  memset(base + (size_t)height * P6_VIDEO_STRIDE_BYTES, 0,
         (size_t)(allocated_height - height) * P6_VIDEO_STRIDE_BYTES);
}

/* A supported raster whose drawing pass ended after the last hand-over. */
bool presentable()
{
 return g.ready && !g.mode_too_large && !g.aux_buffer && g.have_pass
  && g.pass.width > 0 && g.pass.height > 0 && g.pass.index != g.shown.index;
}

void teardown()
{
 vidbuf_description &vi = info();
 // drawing.cpp:2333 assigns these two engine-owned hooks (never called); keep them.
 auto *lock = vi.drawbuffer.lockscr;
 auto *unlock = vi.drawbuffer.unlockscr;
 memset(&vi.drawbuffer, 0, sizeof vi.drawbuffer);
 memset(&vi.tempbuffer, 0, sizeof vi.tempbuffer);
 vi.drawbuffer.lockscr = lock;
 vi.drawbuffer.unlockscr = unlock;
 vi.inbuffer = vi.outbuffer = nullptr;
 g = State();
}

} // namespace

int p6_video_validate_config(const struct uae_prefs *p)
{
 if (!p) return P6_VIDEO_CFG_NULL;
 if (p->gfx_resolution < RES_LORES || p->gfx_resolution > RES_SUPERHIRES
     || p->gfx_vresolution < VRES_NONDOUBLE || p->gfx_vresolution > VRES_DOUBLE)
  return P6_VIDEO_CFG_RESOLUTION;
 // init_drawing_frame() rewrites changed_prefs.gfx_resolution, which nothing here applies.
 if (p->gfx_autoresolution || p->gfx_autoresolution_vga) return P6_VIDEO_CFG_AUTORESOLUTION;
 // count_frame(): framecnt != 0 units are not drawn at all.
 if (p->gfx_framerate > 1) return P6_VIDEO_CFG_FRAMESKIP;
 // CD32 FMV owns and composes its bounded tempbuffer. Other auxiliary consumers
 // remain outside this profile.
 if (p->cs_color_burst || p->gfx_grayscale || p->genlock || p->genlock_effects
     || p->monitoremu || p->monitoremu_mon)
  return P6_VIDEO_CFG_AUX_BUFFER;
 if (p->gfx_luminance || p->gfx_contrast || p->gfx_gamma || p->gfx_gamma_ch[0]
     || p->gfx_gamma_ch[1] || p->gfx_gamma_ch[2])
  return P6_VIDEO_CFG_COLOR_MATH;
 if (p->refresh_indicator) return P6_VIDEO_CFG_INDICATOR;
 return P6_VIDEO_CFG_OK;
}

/* graphics_setup + graphics_init + init_colors of win32gfx.cpp, without windows. */
int p6_video_init(void)
{
 const int cfg = p6_video_validate_config(&currprefs);
 if (cfg) return cfg;
 teardown();
 memset(pixels, 0, sizeof pixels);
 memset(aux_pixels, 0, sizeof aux_pixels);

 vidbuf_description &vi = info();
 vidbuffer &vb = draw();
 bind(vb);
 // Like win32gfx.cpp allocsoftbuffer("draw"): start at the full allocation; the first
 // target_graphics_buffer_update() narrows it to the engine's mode.
 vb.width_allocated = P6_VIDEO_MAX_WIDTH;
 vb.height_allocated = P6_VIDEO_MAX_HEIGHT;
 vb.hardwiredpositioning = false;
 if (currprefs.cs_cd32fmv) {
  vidbuffer &aux = vi.tempbuffer;
  bind(aux);
  aux.width_allocated = P6_VIDEO_MAX_WIDTH;
  aux.height_allocated = P6_VIDEO_MAX_HEIGHT;
  aux.hardwiredpositioning = false;
 }
 vi.inbuffer = vi.outbuffer = &vb;
 // v6 upstream: the maximum is always available. drawing.cpp reads these only in the
 // autoresolution paths, which the config check excludes.
 vi.gfx_resolution_reserved = RES_MAX;
 vi.gfx_vresolution_reserved = VRES_MAX;

 // XRGB8888: red at bit 16, green at 8, blue at 0, no alpha bits, no byte swap.
 alloc_colors64k(0, 8, 8, 8, 16, 8, 0, 0, 0, 0, 0);
 notice_new_xcolors();
 g.ready = true;
 return P6_VIDEO_CFG_OK;
}

/* Deterministic wipe for p6_reset. Locking state and the negotiated mode are the
 * engine's business and stay; only pixels and the hand-over bookkeeping go. */
void p6_video_clear(void)
{
 memset(pixels, 0, sizeof pixels);
 memset(aux_pixels, 0, sizeof aux_pixels);
 g.have_pass = g.have_frame = g.render_ok = false;
 g.pass = g.shown = Pass();
}

/* After a state load: bufmem is an absolute pointer inside engine state and is only
 * valid in the process that wrote it, so re-derive it; and whatever frame was captured
 * belongs to the world that was replaced, so drop the hand-over bookkeeping (the next
 * unit hands over a new frame). Lock state, geometry and pixels are not touched. */
void p6_video_rebind(void)
{
 if (!g.ready) return;
 bind(draw());
 if (currprefs.cs_cd32fmv) bind(info().tempbuffer);
 g.have_pass = g.have_frame = g.render_ok = false;
 g.pass = g.shown = Pass();
}

uint32_t p6_video_status(void)
{
 uint32_t s = 0;
 if (!g.ready) s |= P6_VIDEO_ST_NOT_READY;
 if (g.mode_too_large) s |= P6_VIDEO_ST_MODE_TOO_LARGE;
 if (g.aux_buffer) s |= P6_VIDEO_ST_AUX_BUFFER;
 return s;
}

bool p6_fmv_aux_validate(const p6_fmv_aux_state *s)
{
 if (!s || s->selected > 1 || s->initialized > 1 || s->locked > 1 || s->hardwired > 1)
  return false;
 if (s->width_allocated < 1 || s->width_allocated > P6_VIDEO_MAX_WIDTH ||
     s->height_allocated < 1 || s->height_allocated > P6_VIDEO_MAX_HEIGHT ||
     s->pixel_width < 1 || s->pixel_width > P6_VIDEO_MAX_WIDTH ||
     s->pixel_height < 1 || s->pixel_height > P6_VIDEO_MAX_HEIGHT)
  return false;
 for (int32_t v : {s->outwidth, s->inwidth, s->inwidth2}) if (v < 0 || v > P6_VIDEO_MAX_WIDTH) return false;
 for (int32_t v : {s->outheight, s->inheight, s->inheight2}) if (v < 0 || v > P6_VIDEO_MAX_HEIGHT) return false;
 for (int32_t v : {s->extrawidth, s->extraheight, s->xoffset, s->yoffset, s->inxoffset, s->inyoffset})
  if (v < -0xffff || v > 0xffff) return false;
 return s->monitor_id == 0 && s->last_drawn_line >= -1;
}

bool p6_fmv_aux_get(p6_fmv_aux_state *out)
{
 if (!out || !g.ready || !currprefs.cs_cd32fmv) return false;
 const vidbuf_description &vi = info(); const vidbuffer &aux = vi.tempbuffer, &output = vi.drawbuffer;
 p6_fmv_aux_state s{};
 s.selected = vi.inbuffer == &vi.tempbuffer; s.initialized = aux.initialized; s.locked = aux.locked;
 s.hardwired = aux.hardwiredpositioning; s.width_allocated = aux.width_allocated; s.height_allocated = aux.height_allocated;
 s.outwidth = aux.outwidth; s.outheight = aux.outheight; s.inwidth = aux.inwidth; s.inheight = aux.inheight;
 s.inwidth2 = aux.inwidth2; s.inheight2 = aux.inheight2; s.extrawidth = aux.extrawidth; s.extraheight = aux.extraheight;
 s.xoffset = aux.xoffset; s.yoffset = aux.yoffset; s.inxoffset = aux.inxoffset; s.inyoffset = aux.inyoffset;
 s.monitor_id = aux.monitor_id; s.last_drawn_line = aux.last_drawn_line;
 s.pixel_width = uint32_t(output.width_allocated); s.pixel_height = uint32_t(output.height_allocated);
 if (!p6_fmv_aux_validate(&s) || !aux.bufmem || aux.rowbytes != P6_VIDEO_STRIDE_BYTES || aux.pixbytes != 4) return false;
 for (uint32_t y = 0; y < s.pixel_height; ++y)
  memcpy(s.pixels + size_t(y) * s.pixel_width, aux.bufmem + size_t(y) * aux.rowbytes, size_t(s.pixel_width) * 4);
 *out = s; return true;
}

bool p6_fmv_aux_can_apply(const p6_fmv_aux_state *s)
{
 if (!p6_fmv_aux_validate(s) || !g.ready || !currprefs.cs_cd32fmv) return false;
 const vidbuf_description &vi = info();
 return vi.drawbuffer.width_allocated == int(s->pixel_width) && vi.drawbuffer.height_allocated == int(s->pixel_height) &&
        vi.tempbuffer.bufmem && vi.tempbuffer.rowbytes == P6_VIDEO_STRIDE_BYTES && vi.tempbuffer.pixbytes == 4;
}

bool p6_fmv_aux_apply(const p6_fmv_aux_state *s)
{
 if (!p6_fmv_aux_can_apply(s)) return false;
 vidbuf_description &vi = info(); vidbuffer &aux = vi.tempbuffer;
 aux.initialized = s->initialized; aux.locked = s->locked; aux.hardwiredpositioning = s->hardwired;
 aux.width_allocated = s->width_allocated; aux.height_allocated = s->height_allocated;
 aux.outwidth = s->outwidth; aux.outheight = s->outheight; aux.inwidth = s->inwidth; aux.inheight = s->inheight;
 aux.inwidth2 = s->inwidth2; aux.inheight2 = s->inheight2; aux.extrawidth = s->extrawidth; aux.extraheight = s->extraheight;
 aux.xoffset = s->xoffset; aux.yoffset = s->yoffset; aux.inxoffset = s->inxoffset; aux.inyoffset = s->inyoffset;
 aux.monitor_id = s->monitor_id; aux.last_drawn_line = s->last_drawn_line;
 memset(aux_pixels, 0, sizeof aux_pixels);
 for (uint32_t y = 0; y < s->pixel_height; ++y)
  memcpy(aux.bufmem + size_t(y) * aux.rowbytes, s->pixels + size_t(y) * s->pixel_width, size_t(s->pixel_width) * 4);
 vi.inbuffer = s->selected ? &aux : &vi.drawbuffer; vi.outbuffer = &vi.drawbuffer;
 return true;
}

int p6_video_present(void)
{
 const uint64_t before = g.frames;
 if (render_screen(0, 1, true)) show_screen(0, 0);
 return g.frames != before;
}

int p6_video_frame_get(p6_video_frame_info *out)
{
 if (!out || !g.ready || !g.have_frame) return -1;
 memset(out, 0, sizeof *out);
 out->pixels = pixels;
 out->width = g.shown.width;
 out->height = g.shown.height;
 out->stride_bytes = P6_VIDEO_STRIDE_BYTES;
 out->bytes = (uint64_t)P6_VIDEO_STRIDE_BYTES * g.shown.height;
 out->flags = g.shown.flags;
 out->active = { 0, 0, g.shown.width, g.shown.height };
 out->frame_counter = g.frames;
 out->pass_counter = g.shown.index;
 return 0;
}

/* Unload. The window/texture teardown of upstream has no counterpart; the buffer
 * and every field the engine could still read go back to the pre-init state. */
void graphics_leave(void)
{
 teardown();
}

/* Only reachable through the autoresolution path (gfx_resolution_reserved is at its
 * maximum, so drawing.cpp:1571 never asks) and refused by the config check. If it
 * is reached anyway the effect is the upstream one: a full drawing reset at the next
 * vsync, reported by check_prefs_changed_gfx(). The forced flag chose the window
 * rebuild strategy, which does not exist here. */
void graphics_reset(bool forced)
{
 (void)forced;
 ++g.reset_requests;
 g.reset_pending = true;
}

/* No host-side preference (window, filter, vsync, api) can change. The only change
 * ever reported is the engine's own graphics_reset() request, once. */
int check_prefs_changed_gfx(void)
{
 if (!g.reset_pending) return 0;
 g.reset_pending = false;
 return 1;
}

/* Only the drawbuffer exists. The buffer keeps its content across locks (the D3D11
 * staging texture does too); fullupdate only permitted the driver to discard it and
 * skip selected the lag-free texture swap, both host optimisations. While the engine's
 * mode is refused (target_graphics_buffer_update) the lock fails, exactly like a lost
 * device: the renderer then draws nothing, so no pixel is written for that mode. */
int lockscr(struct vidbuffer *vb, bool fullupdate, bool skip)
{
 (void)fullupdate;
 (void)skip;
 if (!g.ready || g.mode_too_large || vb != &draw() || vb->locked) return 0;
 bind(*vb);
 vb->locked = true;
 return 1;
}

void unlockscr(struct vidbuffer *vb, int y_start, int y_end)
{
 if (!g.ready || vb != &draw() || !vb->locked) return;
 vb->locked = false;

 vidbuf_description &vi = info();
 const vidbuffer &src = vi.inbuffer ? *vi.inbuffer : *vb;
 const bool auxiliary_input = vi.inbuffer && vi.inbuffer != vb;
 const bool composed_fmv = auxiliary_input && currprefs.cs_cd32fmv && vi.outbuffer == vb;
 g.aux_buffer = auxiliary_input && !composed_fmv;
 int w = src.outwidth < vb->width_allocated ? src.outwidth : vb->width_allocated;
 int h = src.outheight < vb->height_allocated ? src.outheight : vb->height_allocated;
 if (w < 0) w = 0;
 if (h < 0) h = 0;
 if (!g.aux_buffer && !g.mode_too_large) {
  const int allocated = vb->height_allocated < P6_VIDEO_MAX_HEIGHT ? vb->height_allocated : P6_VIDEO_MAX_HEIGHT;
  clear_outside(w < P6_VIDEO_MAX_WIDTH ? w : P6_VIDEO_MAX_WIDTH, h, allocated);
 }

 ++g.passes;
 g.pass.width = (uint32_t)w;
 g.pass.height = (uint32_t)h;
 g.pass.index = g.passes;
 // y_start/y_end only bound the dirty lines for a texture upload; -2 is the engine's
 // "display reset in progress, do not show this pass" (drawing.cpp:2157, D3D11 skips
 // the copy). Whole-frame (-1,-1) and line ranges are equivalent for shared memory.
 g.pass.flags = ((y_start < -1 || y_end < -1) ? P6_VIDEO_FRAME_DISCARD : 0u)
  | (interlace_seen > 0 ? P6_VIDEO_FRAME_INTERLACED : 0u)
  | (lof_display ? P6_VIDEO_FRAME_LONG_FIELD : 0u);
 g.have_pass = true;
}

/* D3D_renderframe: converts the texture for the back buffer. Here it decides whether
 * a completed, supported raster exists. Neither mode nor immediate matters. */
bool render_screen(int monid, int mode, bool immediate)
{
 (void)mode;
 (void)immediate;
 g.render_ok = monid == 0 && presentable();
 return g.render_ok;
}

/* D3D_showframe: the hand-over. modes 2..4 only drive LightBoost black insertion. */
void show_screen(int monid, int mode)
{
 if (monid != 0 || mode == 2 || mode == 3 || mode == 4) return;
 if (mode >= 0 ? !g.render_ok : !presentable()) return;
 g.shown = g.pass;
 g.have_frame = true;
 ++g.frames;
 g.render_ok = false;
}

/* Called for a genuine mode change (the engine has just written outwidth/outheight
 * on its inbuffer) and returns whether the allocation changed, like the D3D texture
 * re-creation it replaces. */
bool target_graphics_buffer_update(int monid, bool force)
{
 if (monid != 0 || !g.ready) return false;
 vidbuf_description &vi = info();
 vidbuffer &out = draw();
 const vidbuffer *in = vi.inbuffer;
 if (!in) return false;
 const int w = in->outwidth, h = in->outheight;
 if (w <= 0 || h <= 0) return false;  // no mode yet (same early exit as upstream)

 if (w > P6_VIDEO_MAX_WIDTH || h > P6_VIDEO_MAX_HEIGHT) {
  // Controlled refusal before anything is written: lockscr() fails from now on (no
  // drawing), the allocation stays as it was and p6_video_status() reports it.
  if (!g.mode_too_large)
   write_log(_T("P6 video: mode %dx%d exceeds the %dx%d presentation buffer\n"), w, h,
             (int)P6_VIDEO_MAX_WIDTH, (int)P6_VIDEO_MAX_HEIGHT);
  g.mode_too_large = true;
  return false;
 }
 g.mode_too_large = false;
 if (!force && g.mode_valid && w == g.mode_w && h == g.mode_h) return false;

 // A new texture is black. Pixels outside the current raster stay zero as an invariant:
 // clear what either the old or the new mode could have written.
 clear_rows(g.mode_w > w ? g.mode_w : w, g.mode_h > h ? g.mode_h : h);
 out.width_allocated = w;
 out.height_allocated = h;
 g.mode_w = w;
 g.mode_h = h;
 g.mode_valid = true;
 // A mode change invalidates what was captured for the old raster.
 g.have_pass = g.render_ok = false;
 return true;
}

/* win32gfx.cpp: set_custom_limits(-1,...) resets the engine's visible-area limits and
 * D3D_refresh redraws the presented texture. Only the first half has an effect here. */
void updatedisplayarea(int monid)
{
 (void)monid;
 set_custom_limits(-1, -1, -1, -1, false);
}

/* Identity mapping: the pointer coordinate is the raster coordinate. The float
 * results are the exact constants 0 and 1, so they are host-independent. */
void getgfxoffset(int monid, float *dxp, float *dyp, float *mxp, float *myp)
{
 (void)monid;
 if (dxp) *dxp = 0.0f;
 if (dyp) *dyp = 0.0f;
 if (mxp) *mxp = 1.0f;
 if (myp) *myp = 1.0f;
}
