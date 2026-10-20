// T55c6: actual P6CP visitor and real MOVEC/SoftFloat continuation. Component
// owners are initialized explicitly; this does not expose a public 040 profile.
#include "state.h"
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
#include <cstring>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>
using namespace p6_state;
using Bytes = std::vector<uint8_t>;
static std::map<std::string,std::pair<size_t,size_t>> fields;
static void field(void *,uint64_t at,uint64_t size,const char *name) { fields[name]={at,size}; }
static Bytes capture() {
    auto measure=Archive::measurer(); visit_cpu(measure); assert(measure.ok());
    Bytes bytes(measure.position(),0xa5); auto save=Archive::saver(bytes.data(),bytes.size());
    save.on_field(field,nullptr); visit_cpu(save); assert(save.ok() && save.position()==bytes.size()); return bytes;
}
static bool validate(const Bytes &b) { auto a=Archive::validator(b.data(),b.size()); visit_cpu(a); a.finish(); return a.ok(); }
static void apply(const Bytes &b) {
    assert(validate(b)); auto a=Archive::applier(b.data(),b.size()); visit_cpu(a); a.finish();
    if(!a.ok()) { fprintf(stderr,"apply: %s\n",a.why()); abort(); }
}
static void owner(int model,int revision) {
    currprefs.cpu_model=currprefs.fpu_model=model; currprefs.fpu_revision=revision; currprefs.cpu060_revision=3;
    currprefs.cpu_compatible=currprefs.cpu_cycle_exact=currprefs.cpu_memory_cycle_exact=currprefs.cpu_data_cache=false;
    currprefs.mmu_model=0; currprefs.fpu_strict=true; currprefs.fpu_no_unimplemented=false;
    fpu_reset(); regs.t0=regs.t1=regs.m=0; regs.s=1; regs.intmask=7;
    regs.pcr=model==68060 ? 0x04300300:0; regs.buscr=0;
    x_cp_put_long=put_long; x_cp_get_long=get_long;
    m68k_setpc_normal(0x1002); m68k_areg(regs,0)=0x10000;
    for(int i=0;i<12;i++) put_byte(0x10000+i,0);
    fpuop_restore(0xf350); // The implemented NULL FRESTORE path, not the 040 TODO.
}
static void move(int reg,int value) { m68k_dreg(regs,0)=value; fpuop_arithmetic(0xf200,0x4000|(reg<<7)); }
static void control(int reg,uint32_t value) { assert(m68k_move2c(reg,&value)); }
static std::vector<uint64_t> fpu_words() {
    p6_fpu_state s{}; assert(p6_fpu_state_get(&s));
    std::vector<uint64_t> v{s.context.model,s.context.revision,s.context.flags,s.fpcr,s.fpsr,s.fpiar,
        s.state,s.exp_state,s.exp_pend,s.unimp_pend,s.ea,s.opword,s.flags};
    for(unsigned i=0;i<8;i++) { v.push_back(s.high[i]); v.push_back(s.low[i]); }
#define FIELD(n) v.push_back(s.frame.n);
    P6_FPU_FRAME_SCALARS(FIELD)
#undef FIELD
    for(auto a : {s.frame.eo,s.frame.fpt,s.frame.et,s.frame.wbt}) for(unsigned i=0;i<3;i++) v.push_back(a[i]);
    return v;
}
static void disturb_fpu() {
    p6_fpu_state s{}; assert(p6_fpu_state_get(&s));
    for(unsigned i=0;i<8;i++) { s.high[i]^=0x1234; s.low[i]^=0xfedcba9876543210ULL; }
    s.fpcr^=0x30; s.fpsr^=0x08000000; s.fpiar^=0x56789abc; s.state^=1;
    s.exp_state=(s.exp_state+1)%3; s.exp_pend=55; s.unimp_pend=3; s.ea^=0x456789ab; s.opword^=0x3456; s.flags^=31;
#define FIELD(n) s.frame.n^=1;
    P6_FPU_FRAME_SCALARS(FIELD)
#undef FIELD
    for(auto a : {s.frame.eo,s.frame.fpt,s.frame.et,s.frame.wbt}) for(unsigned i=0;i<3;i++) a[i]^=0xabcdef01+i;
    assert(p6_fpu_state_apply(&s));
}
static Bytes future(int model) {
    Bytes output;
    for(int reg : {0,1,2,0x800,0x801}) {
        uint32_t value=0; assert(m68k_movec2(reg,&value));
        for(int i=0;i<4;i++) output.push_back(value>>(i*8));
    }
    if(model==68060) for(int reg : {8,0x808}) {
        uint32_t value=0; assert(m68k_movec2(reg,&value));
        for(int i=0;i<4;i++) output.push_back(value>>(i*8));
    }
    m68k_setpc_normal(0x1002); m68k_areg(regs,0)=0x10000;
    for(int i=0;i<104;i++) put_byte(0x10000+i,0xa5);
    fpuop_save(0xf310);
    for(int i=0;i<104;i++) output.push_back(get_byte(0x10000+i));
    fpp_set_fpcr(regs.fpcr&0xff); fpuop_arithmetic(0xf200,0x0422); // FP0 += FP1.
    auto tail=capture(); output.insert(output.end(),tail.begin(),tail.end()); return output;
}
static void corrupt(const Bytes &saved,const char *name,uint32_t value) {
    auto bad=saved; auto span=fields.at(name);
    for(size_t i=0;i<span.second;i++) bad[span.first+i]=uint8_t(uint64_t(value)>>(8*i));
    auto before=capture(); auto cycle=get_cycles(); auto ram=get_long(0x10000);
    assert(!validate(bad)); assert(capture()==before && cycle==get_cycles() && ram==get_long(0x10000));
}
int main(int argc,char **argv) {
    assert(argc==2); std::ifstream file(argv[1],std::ios::binary);
    Bytes rom{std::istreambuf_iterator<char>(file),std::istreambuf_iterator<char>()}; assert(!rom.empty());
    p6_config c{}; c.abi_version=P6_ABI_VERSION; c.struct_size=sizeof c; c.machine=P6_MACHINE_A4030;
    c.cpu_compatibility=P6_CPU_NORMAL; c.video_standard=P6_VIDEO_PAL; c.audio_rate_hz=48000;
    c.random_seed=1; c.rtc_epoch_seconds=946684800;
    p6_error e{P6_ABI_VERSION,sizeof e,{}};
    assert(p6_init(&c,&e)==P6_OK && p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_OK);
    p6_input input{P6_ABI_VERSION,sizeof input}; p6_frame frame{P6_ABI_VERSION,sizeof frame};
    assert(p6_run_frame(&input,0,&frame,&e)==P6_OK);
    for(int model : {68040,68060}) for(int revision : {0x40,0x41}) {
        if(model==68060 && revision==0x41) continue;
        for(int cut=0;cut<3;cut++) {
            owner(model,revision); regs.msp=0x6000; regs.instruction_cnt=-123456;
            control(0,5); control(1,3); control(0x801,0x4000); control(2,model==68040 ? 0x80008000:0xf8e0e000);
            if(model==68060) { control(8,0xa0000000); control(0x808,0x41); regs.stopped=2; }
            if(cut) { move(0,1); move(1,cut==2 ? 0:3); fpp_set_fpcr(cut==2 ? 0x470:0x70); }
            if(cut==2) fpuop_arithmetic(0xf200,0x0420); // Real pending divide-by-zero state.
            auto saved=capture(); auto saved_fpu=fpu_words(); assert(validate(saved));
            auto expected=future(model);
            owner(model==68040 ? 68060:68040,0x40); // Reinitialize the backend in the wrong owner first.
            assert(!validate(saved));
            owner(model,revision); control(0,1); control(1,7); control(2,0); control(0x801,0x8000);
            regs.msp=0x1234; regs.instruction_cnt=777; regs.stopped=0; disturb_fpu();
            auto before=capture(); auto cycle=get_cycles(); auto ram=get_long(0x10000);
            assert(validate(saved)); assert(capture()==before && cycle==get_cycles() && ram==get_long(0x10000));
            apply(saved); assert(capture()==saved && fpu_words()==saved_fpu); assert(future(model)==expected);
            apply(saved);
            corrupt(saved,"cpu.cacr",1); corrupt(saved,"cpu.sfc",8);
            corrupt(saved,"cpu.stopped",3); corrupt(saved,"fpu.internal.context.model",68882);
            corrupt(saved,"fpu.internal.context.revision",0x42); corrupt(saved,"fpu.internal.fpcr",0x10000);
            corrupt(saved,"fpu.internal.exp_pend",47); corrupt(saved,"fpu.internal.frame.stag",8);
            if(model==68060) { corrupt(saved,"cpu.buscr",1); corrupt(saved,"cpu.pcr",0); }
            before=capture(); auto short_body=saved; short_body.pop_back(); assert(!validate(short_body) && capture()==before);
            auto long_body=saved; long_body.push_back(0); assert(!validate(long_body) && capture()==before);
            printf("P6CP %d rev%x cut%d controls/FPU/future/rejection PASS\n",model,revision,cut);
        }
    }
    assert(p6_unload(&e)==P6_OK && p6_shutdown(&e)==P6_OK);
}
