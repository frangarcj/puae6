// Motorola MC68040UM 9.8, figure 9-10: independent RAM frames for FRESTORE.
// No profile is enabled here; these are the real fpp.cpp and SoftFloat objects.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "newcpu.h"
#include "fpp.h"
#include "p6_fpu_state.h"
#include <cassert>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>
using Words=std::vector<uint32_t>;
static std::vector<uint32_t> reads;
static uae_u32 read_bus(uaecptr address) { reads.push_back(address); return get_long(address); }
static p6_fpu_state capture() { p6_fpu_state s{}; assert(p6_fpu_state_get(&s)); return s; }
static void owner(int revision) {
    currprefs.cpu_model=currprefs.fpu_model=68040; currprefs.fpu_revision=revision;
    currprefs.mmu_model=0; currprefs.fpu_strict=true; currprefs.fpu_no_unimplemented=false;
    fpu_reset(); regs.s=1; regs.t0=regs.t1=0; regs.pcr=0;
    x_cp_get_long=read_bus; x_cp_put_long=put_long;
    m68k_setpc_normal(0x1002); m68k_areg(regs,0)=0x10000; m68k_areg(regs,7)=0x800;
    put_long(0x10000,0); fpuop_restore(0xf350);
}
static void install(const Words &words) {
    for(unsigned i=0;i<words.size();i++) put_long(0x10000+4*i,words[i]);
    m68k_areg(regs,0)=0x10000; reads.clear();
}
static void move(int reg,int value) { m68k_dreg(regs,0)=value; fpuop_arithmetic(0xf200,0x4000|(reg<<7)); }
int main(int argc,char **argv) {
    assert(argc==2); std::ifstream file(argv[1],std::ios::binary);
    std::vector<uint8_t> rom{std::istreambuf_iterator<char>(file),std::istreambuf_iterator<char>()};
    p6_config c{}; c.abi_version=P6_ABI_VERSION; c.struct_size=sizeof c;
    c.machine=P6_MACHINE_A4030; c.cpu_compatibility=P6_CPU_NORMAL; c.video_standard=P6_VIDEO_PAL;
    c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800;
    p6_error e{P6_ABI_VERSION,sizeof e,{}}; assert(p6_init(&c,&e)==P6_OK && p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_OK);
    p6_input input{P6_ABI_VERSION,sizeof input}; p6_frame frame{P6_ABI_VERSION,sizeof frame}; assert(p6_run_frame(&input,0,&frame,&e)==P6_OK);
    for(int rev : {0x40,0x41}) {
        for(int event=0;event<5;event++) {
            owner(rev); move(0,1); move(1,2); regs.fpiar=0x123456;
            // Sizes/offsets are from the frame diagram, not from FSAVE output.
            Words words(unsigned(rev==0x40 ? 11:13),0);
            words[0]=(unsigned(rev)<<24)|((unsigned(words.size())*4-4)<<16);
            unsigned at=1; if(rev==0x41) { words[at++]=0x04220000; at++; }
            words[at++]=(5u<<29)|(1u<<26)|(5u<<23); // STAG/M66/GRS
            words[at++]=0x44200000; // CMDREG1B
            words[at++]=(3u<<29)|(1u<<20); // DTAG/E15
            const bool e1=event!=1 && event!=4, e3=event==1 || event==2;
            words[at++]=(unsigned(e1)<<26)|(unsigned(e3)<<25)|(1u<<20);
            for(uint32_t word : {0x40000000u,0xc0000000u,0x12345678u,0x3fff0000u,0x80000000u,0x76543210u}) words[at++]=word;
            assert(at==words.size());
            fpp_set_fpcr(event==3 ? 0x4000:0x0600); fpp_set_fpsr(event==3 ? 0x4000:0x0600);
            auto before=capture(); install(words); fpuop_restore(0xf358); // (A0)+
            assert(m68k_areg(regs,0)==0x10000+4*words.size());
            assert(reads.size()==words.size());
            for(unsigned i=0;i<reads.size();i++) assert(reads[i]==0x10000+4*i);
            auto got=capture(); const auto &f=got.frame;
            assert(f.cmdreg3b==unsigned(rev==0x41 ? 0x0422:0) && f.cmdreg1b==0x4420);
            assert(f.stag==5 && f.wbtm66==1 && f.grs==5 && f.dtag==3 && f.wbte15==1);
            assert(f.e1==unsigned(e1) && f.e3==unsigned(e3) && f.t==1);
            for(unsigned i=0;i<3;i++) assert(f.fpt[i]==words[words.size()-6+i] && f.et[i]==words[words.size()-3+i]);
            assert(got.fpcr==before.fpcr && got.fpsr==before.fpsr && got.fpiar==before.fpiar);
            for(unsigned i=0;i<8;i++) assert(got.high[i]==before.high[i] && got.low[i]==before.low[i]);
            assert(got.state==1 && got.exp_pend==unsigned(event==4 ? 0:event==3 ? 54:e3 ? 49:50));
            assert(got.exp_state==unsigned(event!=4));
            m68k_areg(regs,0)=0x11000; fpuop_save(0xf310);
            if(event!=4) for(unsigned i=0;i<words.size();i++) assert(get_long(0x11000+4*i)==words[i]);
            else assert(get_long(0x11000)==unsigned(rev)<<24);
            // With the exception serviced, arithmetic uses the caller's FP0/FP1.
            fpp_set_fpcr(0); fpuop_arithmetic(0xf200,0x0422);
            assert(regs.fp[0].fpx.high==0x4000 && regs.fp[0].fpx.low==0xc000000000000000ULL);
            printf("FRESTORE040 rev%x event%d fields/bus/frame/future PASS\n",rev,event);
        }
        for(bool null : {false,true}) {
            owner(rev); move(0,1); fpp_set_fpcr(0x70); regs.fp_exp_pend=50; regs.fp_unimp_pend=2;
            install({null ? 0u:unsigned(rev)<<24}); fpuop_restore(0xf358);
            assert(m68k_areg(regs,0)==0x10004 && reads.size()==1);
            assert(!regs.fp_exp_pend && !regs.fp_unimp_pend && !regs.fpu_exp_state && regs.fpu_state==int(!null));
            // NULL resets FPCR and SoftFloat's mode; IDLE preserves the mode.
            move(0,1); move(1,3); fpuop_arithmetic(0xf200,0x0420);
            assert(regs.fp[0].fpx.high==0x3ffd && regs.fp[0].fpx.low==
                (null ? 0xaaaaaaaaaaaaaaabULL:0xaaaaab0000000000ULL));
            printf("FRESTORE040 rev%x %s clears-pending PASS\n",rev,null ? "null":"idle");
        }
        owner(rev); move(0,1); put_long(14*4,0x2000);
        install({(unsigned(rev)<<24)|0x00100000}); auto before=capture(); fpuop_restore(0xf358);
        assert(m68k_getpc()==0x2000 && m68k_areg(regs,0)==0x10000 && reads.size()==1);
        auto got=capture(); assert(got.state==before.state && got.exp_state==before.exp_state && got.exp_pend==before.exp_pend);
        printf("FRESTORE040 rev%x invalid-format vector14 PASS\n",rev);
    }
    assert(p6_unload(&e)==P6_OK && p6_shutdown(&e)==P6_OK);
}
