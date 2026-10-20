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
static void busy(int rev,int event) {
    owner(rev); move(0,7); move(1,9); regs.fpiar=0x123456;
    // MC68040UM fig9-10 and Motorola FPSP fpsp.h: byte offsets from header.
    Words w(25,0); w[0]=(unsigned(rev)<<24)|0x00600000;
    w[0x08/4]=0xfe5a0000; // CU_SAVEPC and dirty mask, NOT a request to commit WB.
    w[0x18/4]=0x40020000; w[0x1c/4]=0xa0000000; w[0x20/4]=0x12345678;
    w[0x24/4]=0x08000628; w[0x28/4]=0x1234000; w[0x30/4]=0x12345678;
    w[0x34/4]=0x01910000; w[0x38/4]=0x03450000;
    w[0x3c/4]=(4u<<29)|(1u<<28)|(1u<<26)|(5u<<23);
    w[0x40/4]=0x00220000; // CU FADD to FP0; WB command names a different FPn.
    w[0x44/4]=(4u<<29)|(1u<<28)|(1u<<20);
    bool e1=event!=0,e3=event!=1;
    w[0x48/4]=(unsigned(e1)<<26)|(unsigned(e3)<<25)|(1u<<24)|(1u<<23)|(1u<<21)|(1u<<20);
    w[0x4c/4]=0x3fff0000; w[0x50/4]=0x80000000;
    w[0x58/4]=0x40000000; w[0x5c/4]=0x80000000;
    fpp_set_fpcr(0x0600); fpp_set_fpsr(0x0600);
    auto before=capture(); install(w); fpuop_restore(0xf358);
    assert(m68k_areg(regs,0)==0x10064 && reads.size()==25);
    for(unsigned i=0;i<reads.size();i++) assert(reads[i]==0x10000+4*i);
    auto got=capture();
    const auto &f=got.frame;
    assert(f.cusavepc==0xfe && f.fpr_dirty==0x5a && f.fpsr_shadow==0x08000628);
    assert(f.fpiarcu==0x1234000 && f.cmdreg2b==0x12345678 && f.cmdreg3b==0x0191 && f.cmdreg1b==0x0022);
    assert(f.nmnexc==3 && f.nmcexc==0x45 && f.stag==4 && f.dtag==4 && f.grs==5);
    assert(f.ete15==1 && f.fpte15==1 && f.wbte15==1 && f.wbtm66==1);
    assert(f.e1==unsigned(e1) && f.e3==unsigned(e3) && f.s==1 && f.x==1 && f.u==1 && f.t==1);
    for(unsigned i=0;i<3;i++) assert(f.wbt[i]==w[6+i] && f.fpt[i]==w[19+i] && f.et[i]==w[22+i]);
    assert(got.fpcr==before.fpcr && got.fpsr==before.fpsr && got.fpiar==before.fpiar);
    for(unsigned i=0;i<8;i++) assert(got.high[i]==before.high[i] && got.low[i]==before.low[i]);
    assert(got.state==1 && got.exp_state==2 && got.exp_pend==unsigned(e3 ? 49:50));
    // FSAVE and a backend reinitialization must preserve the same pending frame.
    put_long(0x11064,0xa5a5a5a5); m68k_areg(regs,0)=0x11000; fpuop_save(0xf310);
    for(unsigned i=0;i<w.size();i++) assert(get_long(0x11000+4*i)==w[i]);
    assert(get_long(0x11064)==0xa5a5a5a5);
    owner(rev); assert(p6_fpu_state_apply(&got));
    m68k_areg(regs,0)=0x11000; fpuop_save(0xf310);
    for(unsigned i=0;i<w.size();i++) assert(get_long(0x11000+4*i)==w[i]);
    assert(p6_fpu_state_apply(&got));
    const unsigned vector=e3 ? 49:50; put_long(vector*4,0x2000);
    fpuop_arithmetic(0xf200,0x0422); // The pending exception precedes this FADD.
    assert(m68k_getpc()==0x2000 && regs.fp_exception && !regs.fp_exp_pend);
    for(unsigned i=0;i<8;i++) assert(regs.fp[i].fpx.high==before.high[i] && regs.fp[i].fpx.low==before.low[i]);
    printf("FRESTORE040 rev%x BUSY%d fields/bus/frame/vector PASS\n",rev,event);
}
static void busy_resume(int rev) {
    owner(rev); move(0,7); move(3,9);
    Words w(25,0); w[0]=(unsigned(rev)<<24)|0x00600000;
    w[2]=0xfe000000; // FPSP-style continuation, E1/E3 and WB destination dirty=0.
    w[6]=0x40020000; w[7]=0xa0000000; // WBT=10 differs from corrected FP3=9.
    w[13]=0x01910000; w[16]=0x00220000; // WB FP3, CU FADD to FP0.
    w[19]=0x3fff0000; w[20]=0x80000000; // FPTEMP=1.
    w[22]=0x40000000; w[23]=0x80000000; // ETEMP=2.
    install(w); fpuop_restore(0xf358);
    assert(m68k_areg(regs,0)==0x10064 && reads.size()==25);
    assert(regs.fp[0].fpx.high==0x4000 && regs.fp[0].fpx.low==0xc000000000000000ULL);
    assert(regs.fp[3].fpx.high==0x4002 && regs.fp[3].fpx.low==0x9000000000000000ULL);
    assert(!regs.fp_exp_pend && !regs.fp_unimp_pend && !regs.fpu_exp_state);
    printf("FRESTORE040 rev%x BUSY existing-FE/preserve-corrected-WB PASS\n",rev);
}
static void busy_new_exception(int rev) {
    owner(rev); move(0,7); regs.fpiar=0xaabbccdd;
    // FPSP round.S:nrm_set and res_func.S:wrap_div/fix_stk. Original
    // extended denorm mantissas1 and3 normalize with exponents-63/-62;
    // the inverse shifts are exact, and FDIV gives1/3 in extended nearest.
    Words w(25,0); w[0]=(unsigned(rev)<<24)|0x00600000;
    w[2]=0xfe000000; w[0x28/4]=0x4000; w[0x40/4]=0x00200000;
    w[0x3c/4]=w[0x44/4]=1u<<28; // ETE15/FPTE15, normalized tags.
    w[0x4c/4]=0x7fc10000; w[0x50/4]=0x80000000;
    w[0x58/4]=0x7fc20000; w[0x5c/4]=0xc0000000;
    fpp_set_fpcr(0x0200); fpp_set_fpsr(0); install(w); fpuop_restore(0xf358);
    assert(m68k_areg(regs,0)==0x10064 && reads.size()==25);
    assert(regs.fp[0].fpx.high==0x3ffd && regs.fp[0].fpx.low==0xaaaaaaaaaaaaaaabULL);
    assert(regs.fp_exp_pend==49 && regs.fpu_exp_state==2 && regs.fpiar==0x4000);
    // MC68040UM9.2.4: FPIAR names the cause; the pre-instruction exception
    // stack names the following instruction to retry, not that old cause.
    m68k_setpc_normal(0x1104); regs.instruction_pc=0x1100; regs.m=0;
    put_long(49*4,0x2000); fpuop_arithmetic(0xf200,0x0422);
    assert(m68k_getpc()==0x2000 && regs.fp_exception && !regs.fp_exp_pend && regs.fpiar==0x4000);
    assert(get_word(m68k_areg(regs,7)+6)==49*4 && get_long(m68k_areg(regs,7)+2)==0x1100);
    m68k_areg(regs,0)=0x11000; fpuop_save(0xf310);
    assert(get_long(0x11000)==((unsigned(rev)<<24)|0x00600000));
    assert(get_long(0x11028)==0x4000 && (get_long(0x11048)&(3u<<25))==(1u<<25));
    assert(get_long(0x11040)==0x00200000);
    printf("FRESTORE040 rev%x BUSY FE-denorm/new-exception-identity PASS\n",rev);
}
int main(int argc,char **argv) {
    assert(argc==2); std::ifstream file(argv[1],std::ios::binary);
    std::vector<uint8_t> rom{std::istreambuf_iterator<char>(file),std::istreambuf_iterator<char>()};
    p6_config c{}; c.abi_version=P6_ABI_VERSION; c.struct_size=sizeof c;
    c.machine=P6_MACHINE_A4030; c.cpu_compatibility=P6_CPU_NORMAL; c.video_standard=P6_VIDEO_PAL;
    c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800;
    p6_error e{P6_ABI_VERSION,sizeof e,{}}; assert(p6_init(&c,&e)==P6_OK && p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_OK);
    p6_input input{P6_ABI_VERSION,sizeof input}; p6_frame frame{P6_ABI_VERSION,sizeof frame}; assert(p6_run_frame(&input,0,&frame,&e)==P6_OK);
    for(int rev : {0x40,0x41}) {
        for(int event=0;event<3;event++) busy(rev,event);
        busy_resume(rev);
        busy_new_exception(rev);
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
