// T55c4: real fpp.cpp + SoftFloat on an initialized RAM bus. This component
// fixture does not expose a 040/060 CPU/board or use the TODO FRESTORE040 frames.
#include "p6.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "events.h"
#include "memory.h"
#include "newcpu.h"
#include "fpp.h"
#include "p6_fpu_state.h"
#include <cassert>
#include <cstdio>
#include <utility>
#include <vector>
using Words = std::vector<uint64_t>;
static Words values(const p6_fpu_state &s) {
    Words v{s.context.model,s.context.revision,s.context.flags,s.fpcr,s.fpsr,s.fpiar,
        s.state,s.exp_state,s.exp_pend,s.unimp_pend,s.ea,s.opword,s.flags};
    for(unsigned i=0;i<8;i++) { v.push_back(s.high[i]); v.push_back(s.low[i]); }
#define FIELD(n) v.push_back(s.frame.n);
    P6_FPU_FRAME_SCALARS(FIELD)
#undef FIELD
    for(auto a : {s.frame.eo,s.frame.fpt,s.frame.et,s.frame.wbt}) for(unsigned i=0;i<3;i++) v.push_back(a[i]);
    return v;
}
static p6_fpu_state capture() { p6_fpu_state s{}; assert(p6_fpu_state_get(&s)); return s; }
static void owner(int model,int revision) {
    currprefs.cpu_model=currprefs.fpu_model=model; currprefs.fpu_revision=revision;
    currprefs.mmu_model=0; currprefs.fpu_strict=true; currprefs.fpu_no_unimplemented=false;
    fpu_reset(); regs.t0=regs.t1=0; regs.s=1; regs.pcr=0;
    x_cp_put_long=put_long; x_cp_get_long=get_long; // Actual emulated RAM, no recording bus.
    m68k_setpc_normal(0x1002); m68k_areg(regs,0)=0x10000; m68k_areg(regs,7)=0x800;
}
static void move(int reg,int value) { m68k_dreg(regs,0)=value; fpuop_arithmetic(0xf200,0x4000|(reg<<7)); }
static Words future(int bytes,uint32_t header,bool mode_cut) {
    m68k_setpc_normal(0x1002); m68k_areg(regs,0)=0x10000;
    for(int i=0;i<104;i++) put_byte(0x10000+i,0xa5);
    fpuop_save(0xf310); assert(get_long(0x10000)==header);
    Words v; for(int i=0;i<bytes;i++) v.push_back(get_byte(0x10000+i));
    assert(get_long(0x10000+bytes)==0xa5a5a5a5); // Exact frame length, independently expected.
    if(mode_cut) {
        // Single precision, round toward +infinity: ceil((1/3)*2^25)*2^-25.
        // No FPCR setter here: apply itself must reconstruct SoftFloat's mode.
        fpuop_arithmetic(0xf200,0x0420);
        assert(regs.fp[0].fpx.high==0x3ffd && regs.fp[0].fpx.low==0xaaaaab0000000000ULL);
    } else {
        fpp_set_fpcr(regs.fpcr&0xff); // Pending cuts: disable traps before next FADD.
        fpuop_arithmetic(0xf200,0x0422);
    }
    auto tail=values(capture()); v.insert(v.end(),tail.begin(),tail.end()); return v;
}
static void disturb(const p6_fpu_state &saved) {
    // Exercise inactive frame words too: reset alone intentionally retains them.
    auto s=saved;
    for(unsigned i=0;i<8;i++) { s.high[i]^=0x1234; s.low[i]^=0xfedcba9876543210ULL; }
    s.fpcr^=0x30; s.fpsr^=0x08000000; s.fpiar^=0x56789abc; s.state^=1;
    s.exp_state=(s.exp_state+1)%3; s.exp_pend=55; s.unimp_pend=3;
    s.ea^=0x456789ab; s.opword^=0x3456; s.flags^=31;
#define FIELD(n) s.frame.n^=1;
    P6_FPU_FRAME_SCALARS(FIELD)
#undef FIELD
    for(auto a : {s.frame.eo,s.frame.fpt,s.frame.et,s.frame.wbt}) for(unsigned i=0;i<3;i++) a[i]^=0xabcdef01+i;
    assert(p6_fpu_state_apply(&s)); assert(values(capture())==values(s));
}
static void rejected(const p6_fpu_state *bad) {
    auto before=values(capture()); auto cycle=get_cycles(); auto ram=get_long(0x10000);
    assert(!p6_fpu_state_preflight(bad) && !p6_fpu_state_apply(bad));
    assert(values(capture())==before && get_cycles()==cycle && get_long(0x10000)==ram);
}
int main(int argc,char **argv) {
    assert(argc==2); FILE *f=fopen(argv[1],"rb"); assert(f);
    std::vector<uint8_t> rom(256*1024); assert(fread(rom.data(),1,rom.size(),f)==rom.size()); fclose(f);
    p6_error e{P6_ABI_VERSION,sizeof e,{}}; p6_config c{};
    c.abi_version=P6_ABI_VERSION; c.struct_size=sizeof c; c.machine=P6_MACHINE_A1200;
    c.video_standard=P6_VIDEO_PAL; c.audio_rate_hz=48000; c.random_seed=1; c.fpu_model=68882;
    c.rtc_epoch_seconds=946684800;
    assert(p6_init(&c,&e)==P6_OK && p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_OK);
    p6_input input{P6_ABI_VERSION,sizeof input}; p6_frame frame{P6_ABI_VERSION,sizeof frame};
    assert(p6_run_frame(&input,0,&frame,&e)==P6_OK);
    for(auto identity : {std::pair<int,int>{68040,0x40},{68040,0x41},{68060,0}}) {
        for(int cut=0;cut<4;cut++) {
            const int model=identity.first, rev=identity.second;
            owner(model,rev); fpuop_save(0xf310); for(int i=0;i<12;i++) put_byte(0x10000+i,0);
            fpuop_restore(0xf350); // Only the implemented NULL FRESTORE path.
            if(cut) {
                move(0,1); move(1,cut==2 ? 0:3);
                for(int i=2;i<8;i++) move(i,(1<<i)*(i%2 ? -1:1));
            }
            if(cut==1) fpp_set_fpcr(0x70);
            if(cut>=2) { fpp_set_fpcr((cut==2 ? 0x400:0x200)|0x70); fpuop_arithmetic(0xf200,0x0420); }
            auto saved=capture(); assert(saved.state==unsigned(cut!=0));
            if(cut) for(int i=2;i<8;i++) {
                assert(saved.high[i]==unsigned(0x3fff+i+(i%2 ? 0x8000:0)));
                assert(saved.low[i]==0x8000000000000000ULL);
            }
            assert(saved.exp_pend==unsigned(cut<2 ? 0:cut==2 ? 50:49));
            assert(saved.exp_state==unsigned(cut<2 ? 0:model==68060 || cut==3 ? 2:1));
            int bytes=model==68060 ? 12:cut<2 ? 4:cut==2 ? (rev==0x40 ? 44:52):100;
            uint32_t header=model==68060 ? (cut==0 ? 0:cut==1 ? 0x6000:0xe000|(saved.exp_pend-48))|
                (saved.frame.eo[0]&0xffff0000):cut==0 ? 0:(rev<<24)|((bytes-4)<<16);
            auto cycle=get_cycles(); auto ram=get_long(0x10000);
            assert(p6_fpu_state_validate(&saved,saved.context) && p6_fpu_state_preflight(&saved));
            assert(get_cycles()==cycle && get_long(0x10000)==ram);
            auto expected=future(bytes,header,cut==1); disturb(saved);
            owner(model==68040 ? 68060:68040,0x40); // Reinitialize backend/features, not just the POD.
            assert(p6_fpu_state_validate(&saved,saved.context)); rejected(&saved);
            owner(model,rev); cycle=get_cycles(); ram=get_long(0x10000);
            assert(p6_fpu_state_apply(&saved)); assert(values(capture())==values(saved));
            assert(get_cycles()==cycle && get_long(0x10000)==ram);
            assert(future(bytes,header,cut==1)==expected);
            owner(model,rev); assert(p6_fpu_state_apply(&saved)); rejected(nullptr);
            for(int field=0;field<19;field++) {
                auto bad=saved;
                switch(field) {
                case 0: bad.context.model=68882; break; case 1: bad.context.revision^=1; break;
                case 2: bad.context.flags^=1; break; case 3: bad.fpcr=0x10000; break;
                case 4: bad.fpsr=1; break; case 5: bad.state=2; break; case 6: bad.exp_state=3; break;
                case 7: bad.exp_pend=47; break; case 8: bad.unimp_pend=4; break; case 9: bad.flags=32; break;
                case 10: bad.frame.v=8; break; case 11: bad.frame.cmdreg1b=0x10000; break;
                case 12: bad.frame.stag=8; break; case 13: bad.frame.dtag=8; break;
                case 14: bad.frame.e1=2; break; case 15: bad.frame.e3=2; break;
                case 16: bad.frame.t=2; break; case 17: bad.frame.grs=8; break;
                case 18: bad.frame.wbtm66=2; break;
                }
                rejected(&bad);
            }
            auto low=saved; low.fpcr|=0xf;
            assert(p6_fpu_state_validate(&low,low.context)==(model==68040));
            if(model==68040) { owner(model,rev^1); rejected(&saved); }
            printf("internal-fpu %d revision %x cut %d frame %d PASS\n",model,rev,cut,bytes); fflush(stdout);
        }
    }
    assert(p6_unload(&e)==P6_OK && p6_shutdown(&e)==P6_OK);
}
