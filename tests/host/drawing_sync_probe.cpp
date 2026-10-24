// Executes production Denise/RGA dispatch, metadata, overlap and lifecycle bodies.
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <vector>

int multithread_enabled = 1;
int vpos, linear_vpos;
bool lof_store;
uae_u8 *xlinebuffer, *xlinebuffer2, *xlinebuffer_genlock;
struct denise_rga rga_denise[DENISE_RGA_SLOT_TOTAL]{};
uae_prefs currprefs{};
struct amigadisplay adisplays[MAX_AMIGADISPLAYS]{};
static std::vector<int> calls;
static std::vector<unsigned> args;
// Test-owned geometry caches released by the real drawing_free (0412).
static int *amiga2aspect_line_map, *native2amiga_line_map;
static int native2amiga_line_map_height;
static void seen(int id, unsigned a=0);
static void refresh_indicator_init(void);
static void gen_pfield_tables(void);
static void gen_direct_drawing_table(void);
static void gfxbuffer_reset(int);
void draw_denise_border_line_fast(int, bool, nln_how, linestate *);
void draw_denise_bitplane_line_fast(int, nln_how, linestate *);
void denise_handle_quick_strobe(uae_u16, int, int);
void draw_denise_line(int, nln_how, uae_u32, int, int, int, int, int, int,
                      int, int, bool, int, bool, bool, linestate *);
void draw_denise_vsync(int);
void denise_update_reg(uae_u16, uae_u16, uae_u32);
void denise_store_registers(void);
void denise_restore_registers(void);
static void sprwrite(int r, uae_u32 v) { seen(9, (r<<16)|v); }

#include "drawing_sync_under_test.h"

static void seen(int id, unsigned a)
{
    if (id < 20) {
        assert(this_line == &temp_line && temp_line.vpos == vpos &&
               temp_line.linear_vpos == linear_vpos && temp_line.lof == lof_store);
    }
    calls.push_back(id); args.push_back(a);
}

void draw_denise_border_line_fast(int y, bool blank, nln_how how, linestate *ls)
{ assert(y==12 && blank && how==nln_normal && ls); seen(2,y); }
void draw_denise_bitplane_line_fast(int y, nln_how how, linestate *ls)
{ assert(y==13 && how==nln_normal && ls); seen(1,y); }
void denise_handle_quick_strobe(uae_u16 st, int pos, int end)
{ assert(st==0x1234 && pos==5); seen(4,end); }
void draw_denise_line(int y,nln_how h,uae_u32 n,int s,int sc,int ec,int sk,int sk2,
 int dt,int cs,int cl,bool lol,int hd,bool blank,bool fin,linestate *ls)
{ assert(y==14 && h==nln_normal && n==77 && s==2 && sc==3 && ec==4 && sk==5 && sk2==6 && dt==7 && cs==8 && cl==9 && lol && hd==10 && blank && ls); seen(0,fin); }
void draw_denise_vsync(int e) { assert(e==1); seen(5,e); }
void denise_update_reg(uae_u16 r,uae_u16 v,uae_u32 n)
{ assert(n==((r==0x20 || r==0x40) ? 55u : 88u)); seen(6,(r<<16)|v); }
void denise_store_registers(void) { seen(7); }
void denise_restore_registers(void) { seen(8); }
static void refresh_indicator_init(void) { seen(20); }
static void gen_pfield_tables(void) { seen(21); }
static void gen_direct_drawing_table(void) { seen(22); }
static void gfxbuffer_reset(int n) { assert(n==0); seen(23); }
void reset_drawing(void) { seen(24); }

int main()
{
    linestate ls{};
    vpos=31; linear_vpos=47; lof_store=1;
    for (int i=0;i<DENISE_RGA_SLOT_TOTAL;i++) rga_denise[i].line=0;
    multithread_enabled=1; // P6 must not re-enable worker dispatch from this legacy knob.
    draw_denise_border_line_fast_queue(12,true,nln_normal,&ls);
    ++vpos; ++linear_vpos; lof_store = false;
    draw_denise_bitplane_line_fast_queue(13,nln_normal,&ls);
    quick_denise_rga_queue(55,3,6);
    denise_handle_quick_strobe_queue(0x1234,5,10);
    draw_denise_line_queue(14,nln_normal,77,2,4,3,4,5,6,7,8,9,true,true,10,true,true,&ls);
    draw_denise_vsync_queue(1);
    denise_update_reg_queue(0x100,0x200,88);
    denise_store_restore_registers_queue(true,99);
    denise_store_restore_registers_queue(false,100);
    draw_denise_line_queue_flush();
    const std::vector<int> expected{2,1,4,0,5,6,7,8};
    assert(calls==expected);
    assert(this_line==&temp_line && temp_line.vpos==vpos && temp_line.linear_vpos==linear_vpos && temp_line.lof==lof_store);
    for (int i=0;i<=5;i++) {
        assert(rga_denise[(4-i)&DENISE_RGA_SLOT_MASK].line==1);
        assert(rga_denise[(10-i)&DENISE_RGA_SLOT_MASK].line==1);
    }
    draw_denise_line_queue(14,nln_normal,77,2,24,3,4,5,6,7,8,9,true,true,10,true,false,&ls);
    for (int i=0;i<=5;i++) assert(rga_denise[(24-i)&DENISE_RGA_SLOT_MASK].line==0);

    calls.clear(); args.clear();
    const int start=DENISE_RGA_SLOT_MASK-2;
    for (int i=0;i<DENISE_RGA_SLOT_TOTAL;i++) rga_denise[i]={};
    rga_denise[start].line=55; rga_denise[start].rga=0x20; rga_denise[start].v=0xabcd;
    rga_denise[(start+1)&DENISE_RGA_SLOT_MASK].line=55;
    rga_denise[(start+1)&DENISE_RGA_SLOT_MASK].rga=0x1fe;
    rga_denise[(start+2)&DENISE_RGA_SLOT_MASK].line=55;
    rga_denise[(start+2)&DENISE_RGA_SLOT_MASK].rga=0x3f;
    rga_denise[0].line=54; rga_denise[0].rga=0x20; // Wrong line, skipped after wrap.
    rga_denise[1].line=55; rga_denise[1].rga=0x40; rga_denise[1].v=0x1234;
    quick_denise_rga_queue(55,start,1);
    assert((calls==std::vector<int>{6,6}));
    assert(args[0]==((0x20u<<16)|0xabcdu) && args[1]==((0x40u<<16)|0x1234u));
    calls.clear(); args.clear();
    rga_denise[start].rga=0x40;
    spr_unalign_reg=0x142; spr_unalign_val=0xfeed;
    quick_denise_rga(55,start,start);
    assert((calls==std::vector<int>{6,9}));
    assert(args[0]==((0x40u<<16)|0xabcdu) && args[1]==((2u<<16)|0xfeedu));
    spr_unalign_reg=0;

    calls.clear(); args.clear();
    for (int i=0;i<DENISE_RGA_SLOT_TOTAL;i++) rga_denise[i].line=0;
    update_overlapped_cycles(DENISE_RGA_SLOT_MASK);
    for (int i=0;i<=5;i++) assert(rga_denise[(DENISE_RGA_SLOT_MASK-i)&DENISE_RGA_SLOT_MASK].line==1);
    for (int cycle=0; cycle<2; ++cycle) {
        calls.clear(); args.clear();
        drawing_init();
        amiga2aspect_line_map = static_cast<int *>(malloc(4 * sizeof(int)));
        native2amiga_line_map = static_cast<int *>(malloc(3 * sizeof(int)));
        assert(amiga2aspect_line_map && native2amiga_line_map);
        native2amiga_line_map_height = 3;
        drawing_free();
        assert(!amiga2aspect_line_map && !native2amiga_line_map);
        assert(native2amiga_line_map_height == 0);
        drawing_free(); // Cleanup is safe after its caches have been released.
        assert((calls==std::vector<int>{20,21,22,23,24}));
    }
}
