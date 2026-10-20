// Private monitor gate: real Kickstart initializes the machine, our RAM loop proves return.
#include "p6.h"
#include "config.h"
#include "state.h"
#include "session.h"
#include "blob_io.h"
#include "media/sha256.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "memory.h"
#include "machdep/maccess.h"
#include "newcpu.h"
#include "custom.h"
#include "ar.h"
#include "keyboard.h"
#include "p6_state_access.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <map>
#include <string>
static p6_error error() { return {P6_ABI_VERSION,sizeof(p6_error)}; }
static void call(p6_result r,const p6_error &e) { if(r) { fprintf(stderr,"result%d: %s\n",int(r),e.message); abort(); } }
static std::vector<uint8_t> file(const char *path)
{
    FILE *f=fopen(path,"rb"); assert(f && !fseek(f,0,SEEK_END)); const auto n=ftell(f);
    assert(n>0 && n<=128*1048576 && !fseek(f,0,SEEK_SET)); std::vector<uint8_t> out(size_t(n),0);
    assert(fread(out.data(),1,out.size(),f)==out.size()); fclose(f); return out;
}
static unsigned cpu_mode=P6_CPU_EXACT;
static p6_config config(uint32_t machine=P6_MACHINE_A1200)
{
    p6_config c{P6_ABI_VERSION,sizeof c}; c.machine=machine; c.video_standard=P6_VIDEO_PAL;
    c.cpu_compatibility=cpu_mode; c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800; return c;
}
static p6_sha256 observed;
static void observe(const void *data,size_t n) { p6_sha256_update(&observed,data,n); }
static void step(int units=1,int key=-1)
{
    p6_input input{P6_ABI_VERSION,sizeof input}; if(key>=0) input.keys[key/32]|=1u<<(key%32);
    auto e=error();
    while(units--) {
        p6_frame f{P6_ABI_VERSION,sizeof f}; call(p6_run_frame(&input,P6_RENDER_VIDEO|P6_RENDER_AUDIO,&f,&e),e);
        const uint32_t cpu[]={m68k_getpc(),uint32_t(regs.spcflags),uint32_t(hrtmon_flag),get_long(0x11000)};
        observe(cpu,sizeof cpu); observe(&f.master_ticks,sizeof f.master_ticks); observe(&f.unit_index,sizeof f.unit_index);
        observe(&f.flags,sizeof f.flags); observe(&f.audio_frames,sizeof f.audio_frames); observe(f.audio,size_t(f.audio_frames)*4);
        // Active pixels only: unused pitch/allocation bytes are not an output contract.
        for(uint32_t y=0;f.video && y<f.active.height;++y)
            observe(static_cast<const uint8_t *>(f.video)+(y+f.active.y)*f.video_stride_bytes+f.active.x*4,f.active.width*4);
        observe(&f.active,sizeof f.active); observe(chipmem_bank.baseaddr,currprefs.chipmem.size);
        if (p6_bridge::hrtmon_bound()) {
            p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h)); observe(&h,sizeof h); observe(p6_hrtmon_memory(),0x100000);
        }
    }
}
static void command(const char *text,bool enter=true)
{
    for(;*text;++text) {
        int key=*text=='o'?AK_O:*text=='x'?AK_X:*text=='a'?AK_A:*text==' '?AK_SPC:
            *text=='0'?AK_0:*text>='1'&&*text<='9'?*text-'0':-1;
        assert(key>=0); step(3,key); step(3);
    }
    if (enter) { step(3,AK_RET); step(12); }
}
static std::map<std::string,uint64_t> fields;
static void field(void *,uint32_t,uint64_t at,uint64_t,const char *name) { fields[name]=at; }
static std::vector<uint8_t> save()
{
    auto e=error(); uint64_t size=0,written=0; call(p6_state_bound(&size,&e),e);
    std::vector<uint8_t> out(size); p6_state::Options opts; opts.on_field=field; fields.clear();
    call(p6_state::save(out.data(),out.size(),&written,opts,&e),e); out.resize(written); return out;
}
static void load(const std::vector<uint8_t> &s) { auto e=error(); call(p6_state_load(s.data(),s.size(),&e),e); }
static uint64_t le64(const uint8_t *p) { uint64_t v=0; for(int n=7;n>=0;--n) v=(v<<8)|p[n]; return v; }
static void reseal(std::vector<uint8_t> &s,const p6_state::Identity &identity)
{
    p6_state::seal(s.data(),s.size()-p6_state::kHeaderSize,p6_state::chunk_count(),identity,le64(s.data()+24),le64(s.data()+32));
}
static void reject(const std::vector<uint8_t> &s,p6_result expected)
{
    const auto before=save(); const auto cycles=p6_session::session.master_ticks; auto e=error();
    assert(p6_state_load(s.data(),s.size(),&e)==expected); assert(save()==before && p6_session::session.master_ticks==cycles);
}
static void corrupt(const std::vector<uint8_t> &state,const char *name,uint32_t value)
{
    auto bad=state; const auto found=fields.find(name); assert(found!=fields.end());
    for(int n=0;n<4;++n) bad[found->second+n]=uint8_t(value>>(8*n));
    reseal(bad,p6_state::current_identity()); reject(bad,P6_ERROR_CORRUPT_STATE);
}
static void install_loop()
{
    // Original fixture: mask interrupts, private stack, increment a long, branch forever.
    const uint8_t program[]={0x46,0xfc,0x27,0x00,0x2e,0x7c,0x00,0x1f,0x00,0x00,
        0x52,0xb9,0x00,0x01,0x10,0x00,0x60,0xf8};
    for(size_t n=0;n<sizeof program;++n) put_byte(0x12000+n,program[n]);
    put_long(0x11000,0); put_long(0x10000,0);
    regs.sr=0x2700; MakeFromSR(); m68k_setpc_normal(0x12000); fill_prefetch();
}
static void start(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart)
{
    auto c=config(); auto e=error(); call(p6_init(&c,&e),e);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE);
    auto owned=cart; call(p6_bridge::bind_hrtmon({owned.data(),owned.size()},&e),e); owned.assign(owned.size(),0);
    assert(p6_bridge::bind_hrtmon({cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); assert(!p6_session::session.started);
    assert(!currprefs.cart_internal && !strcmp(currprefs.cartfile,"p6-cartridge.rom"));
    uint64_t size=0,written=123; uint8_t storage[32]; memset(storage,0xa5,sizeof storage);
    call(p6_state_bound(&size,&e),e); assert(size>3*1048576);
    assert(p6_state_save(storage,sizeof storage,&written,&e)==P6_ERROR_BAD_STATE && !written);
    for(auto byte:storage) assert(byte==0xa5);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE);
    uint8_t expected[32],actual[32]; p6_sha256_hash(cart.data(),cart.size(),expected);
    assert(p6_bridge::hrtmon_identity(actual) && !memcmp(expected,actual,32)); p6_sha256_init(&observed);
}
static void stop()
{
    auto e=error(); call(p6_shutdown(&e),e);
    assert(!p6_bridge::hrtmon_bound() && !hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE);
    assert(!(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    for(auto byte:ar_custom) assert(!byte); for(auto byte:ar_ciaa) assert(!byte); for(auto byte:ar_ciab) assert(!byte);
}
static std::array<uint8_t,32> future(unsigned cut)
{
    auto e=error(); p6_sha256_init(&observed);
    if(cut==4) { step(300); install_loop(); step(2); }
    if(cut!=1 && cut!=2) call(p6_bridge::freeze_hrtmon(&e),e);
    if(cut!=2) step(80);
    assert(hrtmon_flag==ACTION_REPLAY_ACTIVE); const auto counter=get_long(0x11000);
    step(8); assert(get_long(0x11000)==counter && p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE);
    if(cut==2) { step(3,AK_RET); step(12); } else command("o 10000 10004 5a");
    assert(get_long(0x10000)==0x5a5a5a5a && get_long(0x11000)==counter);
    command("x"); step(8); assert(hrtmon_flag==ACTION_REPLAY_IDLE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    assert(get_long(0x11000)>counter && m68k_getpc()>=0x1200a && m68k_getpc()<0x12012);
    const auto state=save(); observe(state.data(),state.size());
    std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data()); return digest;
}
static std::string state_path(const char *directory,unsigned cut) { return std::string(directory)+"/"+std::to_string(cpu_mode)+"-"+std::to_string(cut)+".state"; }
static void continuations(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,const char *directory,bool writing)
{
    for(cpu_mode=0;cpu_mode<=P6_CPU_NORMAL;++cpu_mode) {
        std::vector<std::vector<uint8_t>> cuts;
        if(writing) {
            start(rom,cart); step(300); assert(get_long(4)>0 && get_long(4)<currprefs.chipmem.size);
            assert(hrtmon_flag==ACTION_REPLAY_IDLE); install_loop(); step(2); assert(get_long(0x11000)>0);
            p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h) && !h.mapped && !h.triggered); cuts.push_back(save());
            auto e=error(); call(p6_bridge::freeze_hrtmon(&e),e); assert(hrtmon_flag==ACTION_REPLAY_ACTIVATE); cuts.push_back(save());
            step(80); command("o 10000 10004 5",false); step(3,AK_A); cuts.push_back(save());
            if(cpu_mode==P6_CPU_EXACT) {
                const std::pair<const char *,uint32_t> invalid[]={{"hrt.size",0},{"hrt.flag",0},{"hrt.triggered",0},{"hrt.mapped",0},{"cpu.spcflags",0}};
                for(const auto &entry:invalid) corrupt(cuts[2],entry.first,entry.second);
                auto foreign=cuts[2]; auto identity=p6_state::current_identity(); identity.media_manifest_hash[0]^=1;
                reseal(foreign,identity); reject(foreign,P6_ERROR_INCOMPATIBLE);
                step(3); step(3,AK_RET); step(12); command("x"); step(8); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
                assert(p6_hrtmon_get(&h) && h.mapped && h.triggered); cuts.push_back(save());
                call(p6_reset(&e),e); step(); assert(p6_hrtmon_get(&h) && !h.mapped && h.triggered); cuts.push_back(save());
            }
        }
        for(unsigned cut=0;cut<5;++cut) {
            if(cpu_mode!=P6_CPU_EXACT && cut!=2) continue;
            const auto path=state_path(directory,cut); auto state=writing?cuts[cut]:file(path.c_str());
            if(!writing) start(rom,cart);
            load(state); assert(save()==state); const auto first=future(cut);
            load(state); assert(save()==state); assert(first==future(cut));
            if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
            printf("HRTMon continuation %u/%u ",cpu_mode,cut); for(auto byte:first) printf("%02x",byte); puts("");
            if(!writing) stop();
        }
        if(writing) stop();
    }
    cpu_mode=P6_CPU_EXACT;
}
int main(int argc,char **argv)
{
    assert(argc==5); const bool writing=!strcmp(argv[3],"write");
    assert(writing || !strcmp(argv[3],"read")); const auto rom=file(argv[1]),cart=file(argv[2]); auto e=error();
    assert(p6_bridge::bind_hrtmon({cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e);
    assert(p6_bridge::bind_hrtmon({cart.data(),cart.size()},&e)==P6_ERROR_UNSUPPORTED); call(p6_shutdown(&e),e);
    c=config(); call(p6_init(&c,&e),e);
    for(size_t n:{size_t(0),size_t(16),cart.size()-1,cart.size()+1,size_t(0x80001)}) {
        auto bad=cart; bad.resize(n);
        assert(p6_bridge::bind_hrtmon({bad.data(),bad.size()},&e)==P6_ERROR_WRONG_MEDIA && !p6_bridge::hrtmon_bound());
    }
    auto bad=cart; bad[4]^=1; assert(p6_bridge::bind_hrtmon({bad.data(),bad.size()},&e)==P6_ERROR_WRONG_MEDIA);
    assert(!p6_bridge::hrtmon_bound());
    call(p6_bridge::bind_hrtmon({cart.data(),cart.size()},&e),e); call(p6_shutdown(&e),e);
    assert(!p6_bridge::hrtmon_bound());
    continuations(rom,cart,argv[4],writing);
    call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    p6_sha256_init(&observed); step(); const auto state=save(); load(state);
    auto foreign=file(state_path(argv[4],0).c_str()); const auto before=save();
    const auto refused=p6_state_load(foreign.data(),foreign.size(),&e); assert(refused==P6_ERROR_INCOMPATIBLE || refused==P6_ERROR_CORRUPT_STATE);
    assert(save()==before); corrupt(before,"cpu.spcflags",SPCFLAG_ACTION_REPLAY);
    assert(!hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE); call(p6_shutdown(&e),e);
    puts("HRTMon refusal/teardown and A1200 without cartridge PASS");
}
