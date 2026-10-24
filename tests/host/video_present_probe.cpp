// Probe of the video presentation provider (video_present.cpp) as the renderer and the
// bridge drive it. The real gfxutil.cpp (alloc_colors64k) and diagnostics.cpp are linked so
// the XRGB byte order and the rejection message are observed, not assumed. The engine
// symbols the provider reads (adisplays, currprefs, interlace_seen, lof_display) and the
// two engine functions it calls (set_custom_limits, notice_new_xcolors) are observations.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include "diagnostics.h"
#include "video_present.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

uae_prefs currprefs{}, changed_prefs{};
static const uae_prefs zero_prefs{};   // uae_prefs is far too large for a stack temporary
amigadisplay adisplays[MAX_AMIGADISPLAYS]{};
xcolnr xcolors[4096], fullblack;
unsigned int xredcolors[256], xgreencolors[256], xbluecolors[256];   // the 24-bit AGA guns: gfxutil.cpp fills them under AGA
int xredcolor_s, xredcolor_b, xredcolor_m, xgreencolor_s, xgreencolor_b, xgreencolor_m;
int xbluecolor_s, xbluecolor_b, xbluecolor_m;
int interlace_seen;
bool lof_store, lof_display;
int display_reset;
static int hook_fn(vidbuf_description *, vidbuffer *) { return 7; }

static struct { int calls, w, h, dx, dy; bool blank; } limits;
void set_custom_limits(int w, int h, int dx, int dy, bool blank)
{ ++limits.calls; limits.w = w; limits.h = h; limits.dx = dx; limits.dy = dy; limits.blank = blank; }
static int color_notices;
void notice_new_xcolors(void) { ++color_notices; }

static vidbuf_description &vi() { return adisplays[0].gfxvidinfo; }
static vidbuffer &draw() { return vi().drawbuffer; }
static uae_u8 *row(int y) { return draw().bufmem + (size_t)y * draw().rowbytes; }

static uint64_t fnv(uint64_t h, const void *p, size_t n)
{
    const uint8_t *b = static_cast<const uint8_t *>(p);
    for (size_t i = 0; i < n; ++i) h = (h ^ b[i]) * 1099511628211ull;
    return h;
}
static bool all_zero(const uae_u8 *p, size_t n)
{
    for (size_t i = 0; i < n; ++i) if (p[i]) return false;
    return true;
}
// Everything outside the w x h raster of the (constant-stride) buffer is zero.
static bool zero_outside(int w, int h)
{
    for (int y = 0; y < P6_VIDEO_MAX_HEIGHT; ++y) {
        const uae_u8 *r = row(y);
        const size_t from = y < h ? (size_t)w * 4 : 0;
        if (!all_zero(r + from, P6_VIDEO_STRIDE_BYTES - from)) return false;
    }
    return true;
}

// What compute_framesync + start_draw_denise do around the provider.
static void engine_mode(int w, int h)
{
    vidbuffer &in = *vi().inbuffer;
    in.inwidth = in.outwidth = w;
    in.inheight = in.outheight = h;
}
static bool engine_negotiate(int w, int h, bool force = false)
{
    engine_mode(w, h);
    return target_graphics_buffer_update(0, force);
}
static void paint(int w, int h, uae_u32 v)   // renderer writes every pixel of its raster
{
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) memcpy(row(y) + (size_t)x * 4, &v, 4);
}
static void pass(int w, int h, uae_u32 v, int y0 = 0, int y1 = -1)
{
    vidbuffer &vb = draw();
    vi().outbuffer = &vb;
    assert(lockscr(&vb, false, display_reset > 0));
    paint(w, h, v);
    unlockscr(&vb, y0, y1);
}
static uint64_t fingerprint()
{
    p6_video_frame_info f{};
    uint64_t h = 1469598103934665603ull;
    const bool has = p6_video_frame_get(&f) == 0;
    h = fnv(h, &has, sizeof has);
    if (has) {
        h = fnv(h, f.pixels, f.bytes);
        h = fnv(h, &f.width, sizeof f.width);       h = fnv(h, &f.height, sizeof f.height);
        h = fnv(h, &f.stride_bytes, sizeof f.stride_bytes);
        h = fnv(h, &f.flags, sizeof f.flags);       h = fnv(h, &f.active, sizeof f.active);
        h = fnv(h, &f.frame_counter, sizeof f.frame_counter);
        h = fnv(h, &f.pass_counter, sizeof f.pass_counter);
    }
    const uint32_t st = p6_video_status();
    return fnv(h, &st, sizeof st);
}

static uint64_t scripted_session()
{
    currprefs = zero_prefs;
    currprefs.gfx_resolution = RES_HIRES;
    currprefs.gfx_vresolution = VRES_DOUBLE;
    assert(p6_video_init() == P6_VIDEO_CFG_OK);
    uint64_t h = fingerprint();
    assert(engine_negotiate(908, 626));
    pass(908, 626, 0x00123456u);
    assert(p6_video_present() == 1);
    h = fnv(h, &draw().width_allocated, sizeof(int));
    h = fnv(h, &draw().height_allocated, sizeof(int));
    h ^= fingerprint();
    interlace_seen = 1; lof_display = true;
    assert(engine_negotiate(454, 313));
    pass(454, 313, 0x00abcdefu);
    assert(p6_video_present() == 1);
    interlace_seen = 0; lof_display = false;
    h = fnv(h, row(0), P6_VIDEO_BUFFER_BYTES);
    h ^= fingerprint() * 31;
    graphics_leave();
    return h;
}

int main()
{
    logging_init();
    static_assert(sizeof(p6_video_rect) == 16, "rect layout");
    static_assert(P6_VIDEO_STRIDE_BYTES == 7296 && P6_VIDEO_MAX_WIDTH == 1824 && P6_VIDEO_MAX_HEIGHT == 626,
                  "documented capacity");

    // --- Before p6_video_init: nothing works, and says so.
    assert(p6_video_status() == P6_VIDEO_ST_NOT_READY);
    assert(vi().drawbuffer.bufmem == nullptr && vi().inbuffer == nullptr);
    assert(lockscr(&draw(), false, false) == 0 && !draw().locked);
    unlockscr(&draw(), -1, -1);
    assert(!render_screen(0, 1, true) && !p6_video_present());
    show_screen(0, 0);
    assert(!target_graphics_buffer_update(0, false));
    p6_video_frame_info info;
    assert(p6_video_frame_get(&info) == -1 && p6_video_frame_get(nullptr) == -1);
    assert(check_prefs_changed_gfx() == 0);
    p6_video_rebind(); p6_video_clear();               // no-ops, no crash
    assert(draw().bufmem == nullptr);
    graphics_leave();                                   // idempotent before init

    // --- Symbols with fixed values the engine reads.
    assert(max_uae_width == P6_VIDEO_MAX_WIDTH && max_uae_height == P6_VIDEO_MAX_HEIGHT);
    assert(flashscreen == 0 && gfx_hdr == false);
    for (int monid : {0, 1, -1}) {
        float dx = 9, dy = 9, mx = 9, my = 9;
        getgfxoffset(monid, &dx, &dy, &mx, &my);
        assert(dx == 0.0f && dy == 0.0f && mx == 1.0f && my == 1.0f);
    }
    getgfxoffset(0, nullptr, nullptr, nullptr, nullptr);

    // --- Config gate: each option that would make the provider dishonest is refused.
    assert(p6_video_validate_config(nullptr) == P6_VIDEO_CFG_NULL);
    static uae_prefs ok, scratch;
    ok = zero_prefs;
    ok.gfx_resolution = RES_SUPERHIRES; ok.gfx_vresolution = VRES_DOUBLE; ok.gfx_framerate = 1;
    assert(p6_video_validate_config(&ok) == P6_VIDEO_CFG_OK);
    struct Bad { int code; void (*apply)(uae_prefs &); } bad[] = {
        { P6_VIDEO_CFG_RESOLUTION, [](uae_prefs &p) { p.gfx_resolution = RES_SUPERHIRES + 1; } },
        { P6_VIDEO_CFG_RESOLUTION, [](uae_prefs &p) { p.gfx_resolution = -1; } },
        { P6_VIDEO_CFG_RESOLUTION, [](uae_prefs &p) { p.gfx_vresolution = VRES_QUAD; } },
        { P6_VIDEO_CFG_AUTORESOLUTION, [](uae_prefs &p) { p.gfx_autoresolution = 10; } },
        { P6_VIDEO_CFG_AUTORESOLUTION, [](uae_prefs &p) { p.gfx_autoresolution_vga = true; } },
        { P6_VIDEO_CFG_FRAMESKIP, [](uae_prefs &p) { p.gfx_framerate = 2; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.cs_color_burst = true; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.gfx_grayscale = true; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.genlock = true; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.genlock_effects = 1; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.monitoremu = 1; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.monitoremu_mon = 1; } },
        { P6_VIDEO_CFG_AUX_BUFFER, [](uae_prefs &p) { p.cs_cd32fmv = true; } },
        { P6_VIDEO_CFG_COLOR_MATH, [](uae_prefs &p) { p.gfx_luminance = 1; } },
        { P6_VIDEO_CFG_COLOR_MATH, [](uae_prefs &p) { p.gfx_contrast = -1; } },
        { P6_VIDEO_CFG_COLOR_MATH, [](uae_prefs &p) { p.gfx_gamma = 5; } },
        { P6_VIDEO_CFG_COLOR_MATH, [](uae_prefs &p) { p.gfx_gamma_ch[2] = 5; } },
        { P6_VIDEO_CFG_INDICATOR, [](uae_prefs &p) { p.refresh_indicator = true; } },
    };
    for (const Bad &b : bad) {
        scratch = ok; b.apply(scratch);
        assert(p6_video_validate_config(&scratch) == b.code);
    }
    { // A refused init changes nothing observable.
        currprefs = ok; currprefs.gfx_gamma = 1;
        xcolors[1] = 0xdeadbeef; color_notices = 0;
        assert(p6_video_init() == P6_VIDEO_CFG_COLOR_MATH);
        assert(p6_video_status() == P6_VIDEO_ST_NOT_READY && xcolors[1] == 0xdeadbeef);
        assert(color_notices == 0 && draw().bufmem == nullptr && vi().inbuffer == nullptr);
    }

    // --- Init: layout, colours, zero fill.
    currprefs = zero_prefs;
    currprefs.gfx_resolution = RES_HIRES; currprefs.gfx_vresolution = VRES_DOUBLE;
    color_notices = 0;
    assert(p6_video_init() == P6_VIDEO_CFG_OK && p6_video_status() == 0 && color_notices == 1);
    {
        vidbuffer &vb = draw();
        assert(vb.bufmem && (uintptr_t)vb.bufmem % 64 == 0);
        assert(vb.rowbytes == P6_VIDEO_STRIDE_BYTES && vb.rowbytes == 7296 && vb.pixbytes == 4);
        assert(vb.width_allocated == 1824 && vb.height_allocated == 626);
        assert(!vb.locked && vb.initialized && vb.vram_buffer && vb.monitor_id == 0);
        assert(!vb.hardwiredpositioning && vb.bufmem_allocated == nullptr && vb.realbufmem == nullptr);
        assert(vb.bufmemend == vb.bufmem + P6_VIDEO_BUFFER_BYTES - P6_VIDEO_STRIDE_BYTES);
        assert(vi().inbuffer == &vb && vi().outbuffer == &vb);
        assert(vi().tempbuffer.bufmem == nullptr && vi().tempbuffer.bufmem_allocated == nullptr);
        assert(vi().gfx_resolution_reserved == RES_MAX && vi().gfx_vresolution_reserved == VRES_MAX);
        assert(all_zero(vb.bufmem, P6_VIDEO_BUFFER_BYTES));
    }
    // XRGB8888 colour table as gfxutil.cpp builds it for the engine's 12-bit colours.
    assert(xcolors[0x000] == 0x00000000u && xcolors[0xfff] == 0x00ffffffu);
    assert(xcolors[0xf00] == 0x00ff0000u && xcolors[0x0f0] == 0x0000ff00u && xcolors[0x00f] == 0x000000ffu);
    assert(xcolors[0x123] == 0x00112233u);
    for (int i = 0; i < 4096; ++i) assert((xcolors[i] >> 24) == 0);   // X byte is always zero
    // AGA: one 8-bit gun per table, already in its place of the XRGB8888 word (CONVERT_RGB ORs the three).
    for (unsigned i = 0; i < 256; ++i) assert(xredcolors[i] == i << 16 && xgreencolors[i] == i << 8 && xbluecolors[i] == i);
    assert(xredcolor_s == 16 && xgreencolor_s == 8 && xbluecolor_s == 0 && xredcolor_b == 8);
    assert(p6_video_frame_get(&info) == -1);                          // nothing handed over yet

    // --- Deterministic zero fill: dirty everything, unload, load again.
    memset(draw().bufmem, 0xa5, P6_VIDEO_BUFFER_BYTES);
    uae_u8 *const base = draw().bufmem;
    vi().drawbuffer.lockscr = hook_fn;
    graphics_leave();
    assert(p6_video_status() == P6_VIDEO_ST_NOT_READY && draw().bufmem == nullptr);
    assert(vi().inbuffer == nullptr && vi().outbuffer == nullptr && draw().rowbytes == 0);
    assert(draw().lockscr == hook_fn);                                 // engine-owned hook survives
    vi().drawbuffer.lockscr = nullptr;
    graphics_leave();                                                  // idempotent
    assert(p6_video_init() == P6_VIDEO_CFG_OK);
    assert(draw().bufmem == base && all_zero(draw().bufmem, P6_VIDEO_BUFFER_BYTES));

    // --- Lock protocol.
    vidbuffer &vb = draw();
    vidbuffer foreign{};
    foreign.bufmem = reinterpret_cast<uae_u8 *>(&foreign);
    assert(lockscr(&foreign, false, false) == 0 && !foreign.locked);   // not our buffer
    assert(lockscr(&vi().tempbuffer, true, true) == 0 && !vi().tempbuffer.locked);
    assert(lockscr(&vb, false, false) == 1 && vb.locked);
    assert(lockscr(&vb, true, true) == 0 && vb.locked);                // already locked: refused
    unlockscr(&foreign, -1, -1);
    assert(vb.locked);                                                 // foreign unlock ignored
    unlockscr(&vb, 0, -1);
    assert(!vb.locked && vb.bufmem == base);                           // pointer stays valid unlocked
    unlockscr(&vb, -1, -1);                                            // second unlock (drawing.cpp:2157)
    unlockscr(&vb, -2, -1);
    assert(!vb.locked);
    assert(!render_screen(0, 1, true));                                // no mode, an unlock without pixels
    // A pass ended, but the raster is not negotiated yet (outwidth 0): not presentable.
    assert(p6_video_present() == 0 && p6_video_frame_get(&info) == -1);

    // --- Mode negotiation and controlled refusal.
    assert(!engine_negotiate(0, 0));                                   // upstream early exit
    assert(vb.width_allocated == 1824 && vb.height_allocated == 626);
    assert(engine_negotiate(454, 313));
    assert(vb.width_allocated == 454 && vb.height_allocated == 313 && vb.rowbytes == 7296);
    assert(!engine_negotiate(454, 313));                               // unchanged
    assert(engine_negotiate(454, 313, true));                          // forced
    // A mode paints, then a larger/smaller one: pixels outside the raster stay zero.
    assert(engine_negotiate(908, 626));
    pass(908, 626, 0x00ffffffu);
    assert(!all_zero(row(625), 908 * 4) && zero_outside(908, 626));
    assert(engine_negotiate(454, 263));                                // NTSC lores
    assert(vb.width_allocated == 454 && vb.height_allocated == 263);
    assert(zero_outside(454, 263));                                    // old raster wiped
    assert(engine_negotiate(P6_VIDEO_MAX_WIDTH, P6_VIDEO_MAX_HEIGHT));
    pass(P6_VIDEO_MAX_WIDTH, P6_VIDEO_MAX_HEIGHT, 0x00010203u);
    uae_u32 last;                                                       // very last pixel of the buffer
    memcpy(&last, row(P6_VIDEO_MAX_HEIGHT - 1) + (P6_VIDEO_STRIDE_BYTES - 4), 4);
    assert(last == 0x00010203u);
    p6_diag_reset();
    assert(!engine_negotiate(P6_VIDEO_MAX_WIDTH + 1, 300));            // one CCK too wide
    assert(p6_video_status() == P6_VIDEO_ST_MODE_TOO_LARGE);
    assert(vb.width_allocated == P6_VIDEO_MAX_WIDTH && vb.height_allocated == P6_VIDEO_MAX_HEIGHT);
    assert(!engine_negotiate(600, P6_VIDEO_MAX_HEIGHT + 1));           // one line too tall
    assert(!engine_negotiate(4096, 4096));
    p6_diagnostic msg[4];
    assert(p6_diag_read(msg, 4) == 1);                                 // logged once per episode
    assert(strstr(msg[0].text, "1825x300") && strstr(msg[0].text, "1824x626"));
    assert(!render_screen(0, 1, true) && p6_video_present() == 0);     // refused while too large
    assert(lockscr(&vb, false, false) == 0 && !vb.locked);             // and nothing may be drawn
    assert(engine_negotiate(454, 313));                                // guest returns to a sane mode
    assert(p6_video_status() == 0 && zero_outside(454, 313));
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);   // drawing resumes

    // --- Pixel byte order through the real colour table.
    engine_negotiate(908, 626);
    const uae_u32 px = xcolors[0x123];
    const int X = 5, Y = 7;
    memcpy(row(Y) + X * 4, &px, 4);
    const uae_u8 *bytes = row(Y) + X * 4;
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    assert(bytes[0] == 0x33 && bytes[1] == 0x22 && bytes[2] == 0x11 && bytes[3] == 0x00);
#else
    assert(bytes[0] == 0x00 && bytes[1] == 0x11 && bytes[2] == 0x22 && bytes[3] == 0x33);
#endif
    assert(*reinterpret_cast<const uae_u32 *>(row(Y) + X * 4) == 0x00112233u);
    memset(row(Y) + X * 4, 0, 4);

    // --- Hand-over: pass -> render_screen -> show_screen.
    vi().outbuffer = &vb;
    assert(lockscr(&vb, false, false) == 1);                           // start_draw_denise
    paint(908, 626, xcolors[0xf00]);
    assert(!render_screen(0, 1, true));                                // mid-pass: nothing completed
    show_screen(0, 0);
    assert(p6_video_frame_get(&info) == -1);
    unlockscr(&vb, 0, -1);                                             // end_draw_denise
    assert(render_screen(0, 1, true));
    show_screen(0, 2); show_screen(0, 3); show_screen(0, 4);           // strobe modes never hand over
    assert(p6_video_frame_get(&info) == -1);
    show_screen(1, 0);                                                 // other monitors do not exist
    assert(p6_video_frame_get(&info) == -1);
    show_screen(0, 0);
    assert(p6_video_frame_get(&info) == 0);
    assert(info.pixels == base && info.width == 908 && info.height == 626);
    assert(info.stride_bytes == 7296 && info.stride_bytes != info.width * 4);
    assert(info.bytes == (uint64_t)7296 * 626);
    assert(info.active.x == 0 && info.active.y == 0 && info.active.width == 908 && info.active.height == 626);
    assert(info.flags == 0 && info.frame_counter == 1 && info.pass_counter >= 1);
    const uint64_t first_pass = info.pass_counter;
    assert(*reinterpret_cast<const uae_u32 *>(static_cast<const uae_u8 *>(info.pixels) + 3 * 7296 + 4 * 100)
           == 0x00ff0000u);
    assert(!render_screen(0, 1, true));                                // same pass again: not new
    show_screen(0, 0); show_screen(0, -1);
    assert(p6_video_present() == 0 && p6_video_frame_get(&info) == 0 && info.frame_counter == 1);
    assert(render_screen(1, 1, true) == false);
    // The engine unlocks again after denise_lock() (drawing.cpp:2157 then 5443): an unlock of
    // an unlocked buffer is not a new drawing pass and must not touch the captured facts.
    unlockscr(&vb, -2, -1); unlockscr(&vb, -1, -1);
    assert(!render_screen(0, 1, true) && p6_video_present() == 0);
    assert(p6_video_frame_get(&info) == 0 && info.frame_counter == 1 && info.flags == 0);

    // Second pass: lock again (the engine re-locks straight away) and finish it.
    assert(vb.locked == false);
    assert(lockscr(&vb, false, true) == 1);
    paint(908, 626, xcolors[0x0f0]);
    unlockscr(&vb, 10, 20);                                            // line-ranged unlock
    assert(p6_video_present() == 1);
    assert(p6_video_frame_get(&info) == 0 && info.frame_counter == 2 && info.pass_counter > first_pass);
    // mode -1 (the low-latency path) commits without render_screen, but only for a new pass.
    show_screen(0, -1);
    assert(p6_video_frame_get(&info) == 0 && info.frame_counter == 2);
    assert(lockscr(&vb, false, false) == 1);
    unlockscr(&vb, -1, -1);
    show_screen(0, -1);
    assert(p6_video_frame_get(&info) == 0 && info.frame_counter == 3);

    // Field facts are captured when the pass ends, not when the bridge asks.
    struct Field { int lace; bool lof; int y0; uint32_t flags; } fields[] = {
        { 0, false, -1, 0 },
        { 0, true, -1, P6_VIDEO_FRAME_LONG_FIELD },
        { 1, false, -1, P6_VIDEO_FRAME_INTERLACED },
        { 1, true, -1, P6_VIDEO_FRAME_INTERLACED | P6_VIDEO_FRAME_LONG_FIELD },
        { 0, false, -2, P6_VIDEO_FRAME_DISCARD },
        { 1, true, -2, P6_VIDEO_FRAME_DISCARD | P6_VIDEO_FRAME_INTERLACED | P6_VIDEO_FRAME_LONG_FIELD },
    };
    uint64_t frames = 3;
    for (const Field &f : fields) {
        interlace_seen = f.lace; lof_display = f.lof;
        assert(lockscr(&vb, false, f.y0 < -1) == 1);
        unlockscr(&vb, f.y0, -1);
        interlace_seen = -1; lof_display = !f.lof;                     // changed before the bridge looks
        assert(p6_video_present() == 1 && p6_video_frame_get(&info) == 0);
        assert(info.flags == f.flags && info.frame_counter == ++frames);
    }
    interlace_seen = 0; lof_display = false;

    // Engine sizes are clamped to the allocation when the pass ends.
    vb.outwidth = vb.width_allocated + 50; vb.outheight = vb.height_allocated + 50;
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(p6_video_present() == 1 && p6_video_frame_get(&info) == 0);
    assert(info.width == 908 && info.height == 626 && info.frame_counter == ++frames);
    vb.outwidth = 908; vb.outheight = 626;

    // A mode change invalidates the pending pass but not the frame already handed over.
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(engine_negotiate(454, 263));
    assert(p6_video_present() == 0);
    assert(p6_video_frame_get(&info) == 0 && info.width == 908 && info.frame_counter == frames);
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(p6_video_present() == 1);
    assert(p6_video_frame_get(&info) == 0 && info.width == 454 && info.height == 263);
    assert(info.frame_counter == ++frames && info.stride_bytes == 7296);

    // The engine drawing into the auxiliary buffer is reported, not presented.
    vi().inbuffer = &vi().tempbuffer;
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(p6_video_status() == P6_VIDEO_ST_AUX_BUFFER);
    assert(p6_video_present() == 0);
    vi().inbuffer = &vb;
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(p6_video_status() == 0 && p6_video_present() == 1);
    assert(p6_video_frame_get(&info) == 0 && info.frame_counter == ++frames);

    // --- p6_video_clear: pixels and hand-over go, lock state, mode and counter stay.
    assert(lockscr(&vb, false, false) == 1);
    paint(454, 263, 0x00777777u);
    p6_video_clear();
    assert(vb.locked && vb.width_allocated == 454 && vb.height_allocated == 263);
    assert(all_zero(base, P6_VIDEO_BUFFER_BYTES) && p6_video_frame_get(&info) == -1);
    unlockscr(&vb, -1, -1);
    assert(p6_video_present() == 1 && p6_video_frame_get(&info) == 0 && info.frame_counter == ++frames);

    // --- p6_video_rebind: a restored bufmem from another process is re-derived.
    vb.bufmem = reinterpret_cast<uae_u8 *>(0x1000); vb.rowbytes = 12; vb.pixbytes = 2;
    vb.bufmemend = nullptr;
    const bool locked_before = vb.locked; const int w_before = vb.width_allocated;
    p6_video_rebind();
    assert(vb.bufmem == base && vb.rowbytes == 7296 && vb.pixbytes == 4 && vb.locked == locked_before);
    assert(vb.width_allocated == w_before && vb.bufmemend == base + P6_VIDEO_BUFFER_BYTES - P6_VIDEO_STRIDE_BYTES);
    assert(p6_video_frame_get(&info) == -1);                           // the captured frame is dropped
    assert(lockscr(&vb, false, false) == 1); unlockscr(&vb, -1, -1);
    assert(p6_video_present() == 1 && p6_video_frame_get(&info) == 0 && info.frame_counter == ++frames);

    // --- Engine-requested display reset, and the fixed answers.
    assert(check_prefs_changed_gfx() == 0);
    graphics_reset(false);
    assert(check_prefs_changed_gfx() == 1 && check_prefs_changed_gfx() == 0);
    graphics_reset(true); graphics_reset(true);
    assert(check_prefs_changed_gfx() == 1 && check_prefs_changed_gfx() == 0);
    limits = {};
    updatedisplayarea(0); updatedisplayarea(-1);
    assert(limits.calls == 2 && limits.w == -1 && limits.h == -1 && limits.dx == -1 && limits.dy == -1
           && !limits.blank);

    // --- Unload returns to the pre-init state.
    graphics_leave();
    assert(p6_video_status() == P6_VIDEO_ST_NOT_READY && lockscr(&draw(), false, false) == 0);
    assert(p6_video_frame_get(&info) == -1 && !render_screen(0, 1, true));
    assert(check_prefs_changed_gfx() == 0);                            // pending state is gone too

    // --- Determinism: the same script, twice, in one process, ends in identical bytes.
    interlace_seen = 0; lof_display = false;
    const uint64_t a = scripted_session();
    const uint64_t b = scripted_session();
    assert(a == b);
    std::printf("video-present-ok %016llx\n", (unsigned long long)a);
    return 0;
}
