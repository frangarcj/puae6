// Reference AGA-layout and P6 OCS-layout execute extracted production bodies
// in separate binaries, then compare their deterministic OCS observations.
#ifdef TEST_REFERENCE
#define AGA 1
#endif
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "uae.h"
#include "custom.h"
#include "xwin.h"
#include "drawing.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

bool aga_mode=false, ecs_denise=false, direct_rgb=false;
xcolnr xcolors[4096];
#ifdef AGA
unsigned int xredcolors[256], xgreencolors[256], xbluecolors[256];
#endif
color_entry denise_colors{};
int last_bpl_pix=0;
uae_u32 ham_lastcolor=0;
static unsigned border_updates=0;
static void update_bordercolor(void);
uae_u16 bplcoltable[256]{};
uae_u16 clxdat=0;
static uae_u32 *buf1;
static uae_u32 bplxdat[MAX_PLANES]{}, bplxdat2[MAX_PLANES]{};
static uae_u64 bplxdat_64[MAX_PLANES]{}, bplxdat2_64[MAX_PLANES]{};
static bool bpldat_copy[2]{}, denise_bplfmode64=false;
static int bpl1dat_trigger_offset=0, internal_pixel_cnt=0, debug_bpl_mask=0xff;

#include "drawing_ocs_under_test.h"
#include "ecs_fast_under_test.h"

static void update_bordercolor(void) { ++border_updates; }

static uae_u32 hash_word(uae_u32 h, uae_u32 v)
{ for (int i=0;i<4;i++) h=(h^((v>>(i*8))&255))*16777619u; return h; }

static void test_colors()
{
    uae_u32 hash=2166136261u;
    const uae_u16 probes[]={0x0000,0x0001,0x0123,0x0abc,0x0fff,0x8000,0x8123,0xffff};
    for (bool ecs : {false,true}) {
        ecs_denise=ecs;
        for (int reg=0;reg<32;reg++) for (uae_u16 value:probes) {
            update_color(reg,value,0,0);
            const uae_u16 expected=value&0x0fff;
            assert(denise_colors.color_regs_ecs[reg]==expected);
            assert(denise_colors.color_regs_genlock[reg]==(ecs && (value&0x8000)));
            assert(denise_colors.acolors[reg]==getxcolor(expected));
            hash=hash_word(hash,denise_colors.color_regs_ecs[reg]);
            hash=hash_word(hash,denise_colors.acolors[reg]);
            hash=hash_word(hash,denise_colors.color_regs_genlock[reg]);
        }
    }
    assert(border_updates==2*sizeof(probes)/sizeof(probes[0]));
    last_bpl_pix=19;
    denise_colors.color_regs_ecs[19]=0x05a3;
    setlasthamcolor();
    assert(ham_lastcolor==0x05a3);

    uae_u16 colors[256];
    for (unsigned i=0;i<256;i++) colors[i]=(uae_u16)((i*73u+0x321u)&0xfff);
    uae_u16 expected=0x0abc;
    ham_lastcolor=expected;
    for (unsigned pix=0;pix<64;pix++) {
        switch (pix&0x30) {
        case 0x00: expected=colors[pix]; break;
        case 0x10: expected=(expected&0xff0)|(pix&15); break;
        case 0x20: expected=(expected&0x0ff)|((pix&15)<<8); break;
        case 0x30: expected=(expected&0xf0f)|((pix&15)<<4); break;
        }
        const uae_u32 pixel=decode_ham_pixel_fast((uint8_t)pix,colors);
        assert(ham_lastcolor==expected && pixel==getxcolor(expected));
        hash=hash_word(hash,ham_lastcolor); hash=hash_word(hash,pixel);
    }
    std::printf("%08x ",hash);
}

static void test_generated_line()
{
    alignas(uae_u32) uae_u8 color_state[256*sizeof(uae_u32)+256*sizeof(uae_u16)]{};
    auto *acolors=(uae_u32*)color_state;
    for (unsigned i=0;i<256;i++) { acolors[i]=0x60000000u+i*0x101u; bplcoltable[i]=(uae_u16)(1u<<(i&15)); }
    linestate ls{}; ls.linecolorstate=color_state;
    uae_u8 src[16]; for(unsigned i=0;i<16;i++) src[i]=(uae_u8)(i+1);
    uae_u8 second[16]{}; uae_u8 *cp=src, *cp2=second;
    uae_u32 out[12]; for(auto &v:out) v=0xcafef00du;
    buf1=out+2; clxdat=0;
    lts_ecs_n_ilores_dlores_b1(0,8,0,1,3,6,2,1,0xdeadbeefu,&cp,&cp2,1,nullptr,1,&ls);
    assert(buf1==out+10 && cp==src+6 && cp2==second);
    assert(out[0]==0xcafef00d && out[1]==0xcafef00d && out[2]==0xcafef00d);
    assert(out[10]==0xcafef00d && out[11]==0xcafef00d);
    assert(out[3]==0xdeadbeefu && out[4]==0xdeadbeefu);
    assert(out[5]==acolors[2] && out[6]==acolors[3] && out[7]==acolors[4]);
    assert(out[8]==0xdeadbeefu && out[9]==0xdeadbeefu);
    assert(clxdat==(bplcoltable[2]|bplcoltable[3]|bplcoltable[4]));
    uae_u32 hash=2166136261u;
    for(auto v:out) hash=hash_word(hash,v);
    hash=hash_word(hash,clxdat); hash=hash_word(hash,(uae_u32)(cp-src));
    std::printf("%08x\n",hash);
}

static void test_bpldat_docopy()
{
    aga_mode=false;
    for (int wide=0;wide<2;wide++) {
        denise_bplfmode64=wide!=0; debug_bpl_mask=0xff;
        bpl1dat_trigger_offset=-1; internal_pixel_cnt=37;
        bpldat_copy[0]=bpldat_copy[1]=false;
        for(int i=0;i<MAX_PLANES;i++) {
            bplxdat[i]=0x10203040u+i; bplxdat2[i]=0;
            bplxdat_64[i]=0x1122334455667700ull+i; bplxdat2_64[i]=0;
        }
        bpldat_docopy();
        for(int i=0;i<6;i++) {
            assert(bplxdat2[i]==0x10203040u+i);
            assert(bplxdat2_64[i]==0);
        }
        assert(bpldat_copy[0] && bpldat_copy[1] && bpl1dat_trigger_offset==37);

        debug_bpl_mask=0x15; bpl1dat_trigger_offset=91;
        for(int i=0;i<MAX_PLANES;i++) {
            bplxdat2[i]=0xffffffffu; bplxdat2_64[i]=~(uae_u64)0;
        }
        bpldat_docopy();
        for(int i=0;i<6;i++) {
            const bool enabled=(debug_bpl_mask&(1<<i))!=0;
            assert(bplxdat2[i]==(enabled ? 0x10203040u+i : 0));
            assert(bplxdat2_64[i]==(enabled ? ~(uae_u64)0 : 0));
        }
        assert(bpldat_copy[0] && bpldat_copy[1] && bpl1dat_trigger_offset==91);
    }
}

int main()
{
    for(unsigned i=0;i<4096;i++) xcolors[i]=(xcolnr)(0x10000000u+i*7919u);
    test_colors(); test_generated_line(); test_bpldat_docopy();
}
