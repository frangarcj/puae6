// Private monitor gate: real Kickstart initializes the machine, our RAM loop proves return.
#include "p6.h"
#include "config.h"
#include "input.h"
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
#include "events.h"
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
static bool normal_only=false;
static p6_config config(uint32_t machine=P6_MACHINE_A1200)
{
    p6_config c{P6_ABI_VERSION,sizeof c}; c.machine=machine; c.video_standard=P6_VIDEO_PAL;
    c.cpu_compatibility=cpu_mode; c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800; return c;
}
static p6_sha256 observed;
static uint32_t command_level=0;
static bool input_v2=false;
static void observe(const void *data,size_t n) { p6_sha256_update(&observed,data,n); }
static void step(int units=1,int key=-1)
{
    p6_input_v2 packet{}; auto &input=packet.base; input.abi_version=P6_ABI_VERSION; input.struct_size=sizeof input;
    if(input_v2) { input.abi_version=P6_INPUT_VERSION_2; input.struct_size=sizeof packet; packet.commands=command_level; }
    if(key>=0) input.keys[key/32]|=1u<<(key%32);
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
        if (p6_bridge::ar_bound()) {
            p6_ar_raw h{}; assert(p6_ar_get(&h)); observe(&h,sizeof h); observe(p6_ar_memory(),0x10000);
        }
    }
}
static void command(const char *text,bool enter=true)
{
    for(;*text;++text) {
        int key=*text=='o'?AK_O:*text=='x'?AK_X:*text=='g'?AK_G:*text=='a'?AK_A:*text=='r'?AK_R:*text=='p'?AK_P:*text=='c'?AK_C:*text==' '?AK_SPC:
            *text=='0'?AK_0:*text>='1'&&*text<='9'?*text-'0':-1;
        assert(key>=0); step(3,key); step(3);
    }
    if (enter) { step(3,AK_RET); step(12); }
}
static std::map<std::string,uint64_t> fields;
static std::map<uint64_t,std::pair<uint64_t,std::string>> spans;
static void field(void *,uint32_t,uint64_t at,uint64_t size,const char *name) { fields[name]=at; spans[at]={size,name}; }
static std::vector<uint8_t> save()
{
    auto e=error(); uint64_t size=0,written=0; call(p6_state_bound(&size,&e),e);
    std::vector<uint8_t> out(size); p6_state::Options opts; opts.on_field=field; fields.clear(); spans.clear();
    call(p6_state::save(out.data(),out.size(),&written,opts,&e),e); out.resize(written); return out;
}
static void same_state(const std::vector<uint8_t> &expected)
{
    const auto actual=save();
    if(actual!=expected) {
        fprintf(stderr,"HRTMon state mismatch mode=%u expected_size=%zu actual_size=%zu\n",cpu_mode,expected.size(),actual.size());
        unsigned header=0,body=0;
        for(size_t at=0;at<expected.size() && at<actual.size();++at) {
            if(expected[at]==actual[at]) continue;
            if(at<p6_state::kHeaderSize) { if(header++>=4) continue; } else if(body++>=16) break;
            auto found=spans.upper_bound(at); const char *name="envelope/chunk header"; uint64_t relative=at;
            if(found!=spans.begin()) { --found; if(at-found->first<found->second.first) { name=found->second.second.c_str(); relative=at-found->first; } }
            fprintf(stderr,"  offset=%zu field=%s+%llu expected=%02x actual=%02x\n",at,name,(unsigned long long)relative,expected[at],actual[at]);
        }
        fflush(stderr);
    }
    assert(actual==expected);
}
static void check_mirrors()
{
    if(!p6_bridge::hrtmon_bound()) return;
    p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h));
    // The B1..B7 mirrors are outside HRTMon even while it is mapped.
    assert(&get_mem_bank(0xb10000)==&kickmem_bank && &get_mem_bank(0xb70000)==&kickmem_bank);
    assert(get_long(0xb10000)==get_long(0xf90000) && get_long(0xb7fffc)==get_long(0xfffffc));
    if(h.mapped==2) {
        assert(get_long(0xa80000)==get_long(0xf80000) && get_long(0xb00000)==get_long(0xf80000));
        assert(get_real_address(0xb00000)==kickmem_bank.baseaddr);
    }
}
static void load(const std::vector<uint8_t> &s) { auto e=error(); call(p6_state_load(s.data(),s.size(),&e),e); check_mirrors(); }
static uint64_t le64(const uint8_t *p) { uint64_t v=0; for(int n=7;n>=0;--n) v=(v<<8)|p[n]; return v; }
static void reseal(std::vector<uint8_t> &s,const p6_state::Identity &identity)
{
    p6_state::seal(s.data(),s.size()-p6_state::kHeaderSize,p6_state::chunk_count(),identity,le64(s.data()+24),le64(s.data()+32));
}
static void reject(const std::vector<uint8_t> &s,p6_result expected)
{
    const auto before=save(),input=s; const auto cycles=p6_session::session.master_ticks; auto e=error();
    assert(p6_state_load(s.data(),s.size(),&e)==expected); assert(s==input && save()==before && p6_session::session.master_ticks==cycles);
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
    for(cpu_mode=normal_only?P6_CPU_NORMAL:P6_CPU_EXACT;cpu_mode<=P6_CPU_NORMAL;++cpu_mode) {
        std::vector<std::vector<uint8_t>> cuts;
        if(writing) {
            start(rom,cart); step(300); assert(get_long(4)>0 && get_long(4)<currprefs.chipmem.size);
            assert(hrtmon_flag==ACTION_REPLAY_IDLE); install_loop(); step(2); assert(get_long(0x11000)>0);
            p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h) && h.mapped==2 && !h.triggered); check_mirrors(); cuts.push_back(save());
            auto e=error(); call(p6_bridge::freeze_hrtmon(&e),e); assert(hrtmon_flag==ACTION_REPLAY_ACTIVATE); cuts.push_back(save());
            step(80); command("o 10000 10004 5",false); step(3,AK_A); cuts.push_back(save());
            if(cpu_mode==P6_CPU_EXACT) {
                const std::pair<const char *,uint32_t> invalid[]={{"hrt.size",0},{"hrt.flag",0},{"hrt.triggered",0},{"hrt.mapped",0},{"hrt.mapped",2},{"hrt.mapped",3},{"cpu.spcflags",0}};
                for(const auto &entry:invalid) corrupt(cuts[2],entry.first,entry.second);
                auto foreign=cuts[2]; auto identity=p6_state::current_identity(); identity.media_manifest_hash[0]^=1;
                reseal(foreign,identity); reject(foreign,P6_ERROR_INCOMPATIBLE);
                step(3); step(3,AK_RET); step(12); command("x"); step(8); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
                assert(p6_hrtmon_get(&h) && h.mapped==1 && h.triggered); check_mirrors(); cuts.push_back(save());
                call(p6_reset(&e),e); step(); assert(p6_hrtmon_get(&h) && h.mapped==2 && h.triggered); check_mirrors(); cuts.push_back(save());
            }
        }
        for(unsigned cut=0;cut<5;++cut) {
            if(cpu_mode!=P6_CPU_EXACT && cut!=2) continue;
            const auto path=state_path(directory,cut); auto state=writing?cuts[cut]:file(path.c_str());
            if(!writing) start(rom,cart);
            load(state); same_state(state); const auto first=future(cut);
            load(state); same_state(state); assert(first==future(cut));
            if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
            printf("HRTMon continuation %u/%u ",cpu_mode,cut); for(auto byte:first) printf("%02x",byte); puts("");
            if(!writing) stop();
        }
        if(writing) stop();
    }
    cpu_mode=P6_CPU_EXACT;
}
static void level(uint32_t commands,int units=1) { input_v2=true; command_level=commands; step(units); }
static uint32_t held_command() { p6_applied_raw a{}; p6_bridge::applied_get(&a); return a.commands; }
static void reject_commands(uint32_t commands,p6_result expected)
{
    const auto before=save(); const auto ticks=p6_session::session.master_ticks;
    struct { uint64_t head; p6_input_v2 packet; uint64_t tail; } guarded{};
    guarded.head=guarded.tail=UINT64_C(0x123456789abcdef0); auto &input=guarded.packet;
    input.base.abi_version=P6_INPUT_VERSION_2; input.base.struct_size=sizeof input; input.commands=commands;
    uint8_t original[sizeof guarded]; memcpy(original,&guarded,sizeof guarded);
    p6_frame f; memset(&f,0xa5,sizeof f); f.abi_version=P6_ABI_VERSION; f.struct_size=sizeof f;
    uint8_t output[sizeof f]; memcpy(output,&f,sizeof f); auto e=error();
    assert(p6_run_frame(&input.base,3,&f,&e)==expected);
    assert(!memcmp(output,&f,sizeof f) && !memcmp(original,&guarded,sizeof guarded));
    assert(save()==before && p6_session::session.master_ticks==ticks);
}
static std::array<uint8_t,32> command_future()
{
    p6_sha256_init(&observed); assert(held_command()==1 && hrtmon_flag==ACTION_REPLAY_ACTIVE);
    const auto counter=get_long(0x11000); level(1,8); assert(get_long(0x11000)==counter);
    command("o 10000 10004 5a"); assert(get_long(0x10000)==0x5a5a5a5a && get_long(0x11000)==counter);
    command("x"); level(1,8); assert(hrtmon_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>counter);
    const auto held=save(); level(1,5); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
    auto e=error(); call(p6_reset(&e),e); level(1,6); assert(held_command()==1 && hrtmon_flag==ACTION_REPLAY_IDLE);
    load(held); same_state(held); level(1,5); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
    input_v2=false; step(); assert(!held_command()); // ABI1 releases the command without an extension read.
    level(1,80); assert(hrtmon_flag==ACTION_REPLAY_ACTIVE); // Release followed by press reactivates.
    command("x"); level(1,8); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
    const auto final=save(); observe(final.data(),final.size()); std::array<uint8_t,32> digest{};
    p6_sha256_final(&observed,digest.data()); return digest;
}
static void public_commands(const std::vector<uint8_t> &rom,const char *directory,bool writing)
{
    static_assert(sizeof(p6_input_v2)==108 && offsetof(p6_input_v2,commands)==104);
    auto c=config(); c.flags=P6_CONFIG_HRTMON; auto e=error(); call(p6_init(&c,&e),e);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); p6_sha256_init(&observed);
    const auto path=std::string(directory)+"/commands.state"; std::vector<uint8_t> state;
    if(writing) {
        level(1,300); assert(held_command()==1 && hrtmon_flag==ACTION_REPLAY_IDLE); // First boot press is consumed, never queued.
        install_loop(); level(1,2); assert(get_long(0x11000)>0 && hrtmon_flag==ACTION_REPLAY_IDLE);
        level(0); level(1,80); assert(hrtmon_flag==ACTION_REPLAY_ACTIVE);
        level(0); level(1,8); assert(held_command()==1 && hrtmon_flag==ACTION_REPLAY_ACTIVE); // Press within monitor is consumed.
        state=save(); FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f);
    } else { state=file(path.c_str()); load(state); input_v2=true; command_level=1; }
    same_state(state); reject_commands(2,P6_ERROR_BAD_ARGUMENT); reject_commands(3,P6_ERROR_BAD_ARGUMENT);
    auto bad=state; bad[fields.at("applied.commands")]=2;
    reseal(bad,p6_state::current_identity()); reject(bad,P6_ERROR_CORRUPT_STATE);
    const auto first=command_future(); load(state); same_state(state); assert(first==command_future());
    printf("HRTMon command continuation "); for(auto byte:first) printf("%02x",byte); puts(""); stop();
    c.flags=0; call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    level(0); reject_commands(1,P6_ERROR_UNSUPPORTED); reject_commands(2,P6_ERROR_BAD_ARGUMENT); stop();
    puts("HRTMon command edges/held/reset/ABI/refusals PASS");
}
static void public_builtin(const std::vector<uint8_t> &rom,const char *directory,bool writing)
{
    auto e=error();
    for(uint32_t machine:{P6_MACHINE_A500,P6_MACHINE_A600,P6_MACHINE_CD32,P6_MACHINE_A4030,P6_MACHINE_A4040}) {
        auto c=config(machine); c.flags=P6_CONFIG_HRTMON;
        assert(p6_init(&c,&e)==P6_ERROR_UNSUPPORTED && !p6_bridge::hrtmon_bound());
        assert(p6_session::session.state==p6_session::State::Uninitialized);
    }
    auto c=config(); c.flags=4; assert(p6_init(&c,&e)==P6_ERROR_BAD_ARGUMENT);
    // The two flag bits compose, and unload/reload recreates only an immutable owner.
    c.flags=P6_CONFIG_HRTMON|P6_CONFIG_FLOPPY_WRITES; call(p6_init(&c,&e),e);
    assert(p6_load({nullptr,0},nullptr,0,&e)==P6_ERROR_NEED_FIRMWARE && !p6_bridge::hrtmon_bound());
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    assert(p6_session::session.config.flags==3 && p6_bridge::hrtmon_bound());
    call(p6_unload(&e),e); assert(!p6_bridge::hrtmon_bound());
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); stop();
    c.flags=P6_CONFIG_HRTMON; call(p6_init(&c,&e),e);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    assert(!p6_session::session.started && !currprefs.cart_internal && !strcmp(currprefs.cartfile,"p6-cartridge.rom"));
    uint8_t raw[32]; assert(p6_bridge::hrtmon_identity(raw));
    const char *sha="15e3e106248822665cc64f5c9a3ed5f52a67c07f81b06eb7118da39099d171f5";
    for(unsigned i=0;i<32;++i) { char byte[3]; snprintf(byte,sizeof byte,"%02x",raw[i]); assert(!memcmp(byte,sha+2*i,2)); }
    const auto path=std::string(directory)+"/builtin.state";
    p6_sha256_init(&observed);
    auto state=writing?std::vector<uint8_t>{}:file(path.c_str());
    if(writing) {
        step(); state=save(); FILE *f=fopen(path.c_str(),"wb");
        assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f);
    } else load(state); // A cold owner, before its first p6_run_frame.
    same_state(state); assert(hrtmem_size==0x100000 && hrtmon_flag==ACTION_REPLAY_IDLE);
    p6_sha256_init(&observed); step(5); const auto final=save(); observe(final.data(),final.size());
    uint8_t digest[32]; p6_sha256_final(&observed,digest);
    printf("HRTMon builtin continuation "); for(auto byte:digest) printf("%02x",byte); puts(""); stop();
    c.flags=0; call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    p6_sha256_init(&observed); step(); reject(state,P6_ERROR_INCOMPATIBLE);
    assert(!p6_bridge::hrtmon_bound() && !hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE); stop();
    puts("HRTMon builtin config/identity/teardown PASS");
}
static void ar_state_contract()
{
    auto e=error(); uint64_t size=UINT64_C(0x123456789abcdef0),written=99;
    uint8_t bytes[32],before[32]; memset(bytes,0xa5,sizeof bytes); memcpy(before,bytes,sizeof bytes);
    const auto ticks=p6_session::session.master_ticks; const auto flag=regs.spcflags;
    const bool loaded=p6_session::session.state==p6_session::State::Loaded;
    if (loaded) { call(p6_state_bound(&size,&e),e); assert(size>65536); }
    else assert(p6_state_bound(&size,&e)==P6_ERROR_BAD_STATE && size==UINT64_C(0x123456789abcdef0));
    assert(p6_state_save(bytes,sizeof bytes,&written,&e)==P6_ERROR_BAD_STATE && !written);
    assert(p6_state_load(bytes,sizeof bytes,&e)==(loaded?P6_ERROR_CORRUPT_STATE:P6_ERROR_BAD_STATE) && !memcmp(bytes,before,sizeof bytes));
    assert(p6_session::session.master_ticks==ticks && regs.spcflags==flag);
}
static void ar_owner(const std::vector<uint8_t> &rom)
{
    auto e=error(); const char *id="p6-action-replay.rom";
    std::vector<uint8_t> image(262144);
    for(size_t n=0;n<image.size();n+=2) { image[n]=0x4e; image[n+1]=0x71; } // Original NOP cartridge, never executed.
    assert(p6_bridge::bind_action_replay(2,{image.data(),131072},&e)==P6_ERROR_BAD_STATE);
    for(unsigned bad=0;bad<7;++bad) {
        auto c=config(bad==0?P6_MACHINE_A1200:P6_MACHINE_A500);
        if(bad==1)c.cpu_model=2; if(bad==2)c.cpu_multiplier=4; if(bad==3)c.cpu_compatibility=P6_CPU_NORMAL;
        if(bad==4)c.chip_kib=1024; if(bad==5)c.fast_kib=2048; if(bad==6)c.flags=P6_CONFIG_FLOPPY_WRITES;
        call(p6_init(&c,&e),e);
        assert(p6_bridge::bind_action_replay(2,{image.data(),131072},&e)==P6_ERROR_UNSUPPORTED && !p6_bridge::ar_bound());
        call(p6_shutdown(&e),e);
    }
    for(unsigned scenario=0;scenario<5;++scenario) {
        const unsigned model=scenario==0||scenario==2?2:3; auto cart=image;
        const size_t length=model==2?131072:262144;
        if(scenario==2) { const uint32_t address=0x400000+uint32_t(length)-4; for(unsigned b=0;b<4;++b)cart[b]=uint8_t(address>>(24-8*b)); }
        if(scenario>=3) { const char *text=scenario==3?"(V3.17)":"(V3.17 AAAAAAAAAAAAAAAAAAAAAAAAAAAAA)"; memcpy(cart.data()+4,text,strlen(text)+1); }
        const size_t count=length; auto c=config(P6_MACHINE_A500); if(scenario==2)c.cpu_compatibility=P6_CPU_COMPATIBLE; call(p6_init(&c,&e),e);
        assert(p6_bridge::bind_action_replay(0,{image.data(),count},&e)==P6_ERROR_BAD_ARGUMENT);
        for(size_t bytes:{size_t(0),count-1,count+1})
            assert(p6_bridge::bind_action_replay(model,{image.data(),bytes},&e)==P6_ERROR_WRONG_MEDIA && !p6_bridge::ar_bound());
        assert(p6_bridge::bind_action_replay(model,{nullptr,count},&e)==P6_ERROR_WRONG_MEDIA);
        auto input=cart; call(p6_bridge::bind_action_replay(model,{input.data(),count},&e),e); input.assign(input.size(),0);
        assert(p6_bridge::ar_bound() && !p6_bridge::hrtmon_bound()); ar_state_contract();
        assert(p6_bridge::bind_action_replay(model,{image.data(),count},&e)==P6_ERROR_BAD_STATE);
        assert(p6_bridge::bind_hrtmon({nullptr,0},&e)==P6_ERROR_BAD_STATE);
        uint8_t copied[16]; size_t got=0; assert(p6_blob_pread(id,8,copied,sizeof copied,&got)==P6_BLOB_OK && got==sizeof copied);
        assert(!memcmp(copied,cart.data()+8,sizeof copied));
        call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
        assert(armodel==int(model) && !currprefs.cart_internal && !strcmp(currprefs.cartfile,id)); ar_state_contract();
        action_replay_reset(true,false); // Existing hardware reset maps the actual ROM/RAM banks; no cart execution.
        assert(!strcmp(get_mem_bank(0x400000).name,"Action Replay ROM") && !strcmp(get_mem_bank(0x440000).name,"Action Replay RAM"));
        for(uint32_t at=8;at<count;at+=254) assert(get_byte(0x400000+at)==cart[at]);
        assert(get_byte(0x400000+count-1)==cart[count-1] && get_long(0x440010)==0);
        put_long(0x440010,0x12345678); put_word(0x44fffe,0xabcd);
        assert(get_long(0x440010)==0x12345678 && get_word(0x44fffe)==0xabcd);
        put_byte(0x400100,0); assert(get_byte(0x400100)==cart[0x100]);
        put_byte(0x400001,2); assert(regs.spcflags&SPCFLAG_ACTION_REPLAY); ar_state_contract();
        memset(ar_custom,0xa5,sizeof ar_custom); memset(ar_ciaa,0xa5,sizeof ar_ciaa); memset(ar_ciab,0xa5,sizeof ar_ciab);
        call(p6_shutdown(&e),e);
        assert(!p6_bridge::ar_bound() && !armodel && action_replay_flag==ACTION_REPLAY_INACTIVE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
        assert(p6_blob_exists(id)==P6_BLOB_NOT_FOUND);
        for(auto byte:ar_custom) assert(!byte); for(auto byte:ar_ciaa) assert(!byte); for(auto byte:ar_ciab) assert(!byte);
    }
    auto c=config(); c.flags=P6_CONFIG_HRTMON; call(p6_init(&c,&e),e); call(p6_bridge::load_builtin_hrtmon(&e),e);
    assert(p6_bridge::bind_action_replay(2,{image.data(),131072},&e)==P6_ERROR_BAD_STATE); call(p6_shutdown(&e),e);
    for(unsigned malformed=0;malformed<2;++malformed) {
        auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e); std::vector<uint8_t> invalid(262144,0);
        if(malformed) invalid[4]=1; // AR3's last nonzero long precedes its checksum start0x7c.
        call(p6_bridge::bind_action_replay(3,{invalid.data(),invalid.size()},&e),e);
        assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA && !armodel && !p6_bridge::ar_bound());
        call(p6_shutdown(&e),e);
    }
    // The internal blob seam can simulate a corrupted/truncated owner before the engine adopts it.
    c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e); call(p6_bridge::bind_action_replay(2,{image.data(),131072},&e),e);
    p6_blob_clear(); assert(p6_blob_register(id,P6_BLOB_ROM,image.data(),8)==P6_BLOB_OK);
    assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA && !armodel && !p6_bridge::ar_bound());
    call(p6_shutdown(&e),e);
    call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    assert(!p6_bridge::ar_bound() && !armodel && !currprefs.cartfile[0]); call(p6_shutdown(&e),e);
    puts("AR2/3 copied owner/banks/refusals/cleanup PASS; activation tested separately");
}
static std::vector<uint8_t> ar_cart(unsigned model)
{
    std::vector<uint8_t> cart(model==2?131072:262144);
    for(size_t n=0;n<cart.size();n+=2) { cart[n]=0x4e; cart[n+1]=0x71; }
    const uint8_t vector[]={0,0x40,1,0}; memcpy(cart.data()+0x7c,vector,4);
    // Original IRQ7: count in cartridge RAM, wait on Chip RAM, optionally set
    // the mode latch, restore the old vector through400006 and RTE.
    const uint8_t irq[]={0x52,0xb9,0,0x44,1,0,0x4a,0x39,0,1,0x10,4,0x67,0xf8,
        0x4a,0x39,0,1,0x10,6,0x66,0x0a,0x13,0xf9,0,1,0x10,5,0,0x40,0,1,
        0x13,0xfc,0,0,0,0x40,0,6,0x4e,0x73};
    memcpy(cart.data()+0x100,irq,sizeof irq); return cart;
}
static void ar_start(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,unsigned model)
{
    auto e=error(); auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e);
    call(p6_bridge::bind_action_replay(model,{cart.data(),cart.size()},&e),e);
    ar_state_contract(); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); ar_state_contract();
    p6_sha256_init(&observed);
}
static p6_ar_raw ar_control()
{
    p6_ar_raw h{}; assert(p6_ar_get(&h)); return h;
}
static void ar_exit(bool breakpoint=false)
{
    put_byte(0x11006,breakpoint?1:0); put_byte(0x11004,1); step();
    assert(action_replay_flag==ACTION_REPLAY_IDLE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    assert(get_long(0x7c)==0x12340); put_byte(0x11004,0);
}
static std::array<uint8_t,32> ar_future()
{
    p6_sha256_init(&observed);
    if(action_replay_flag==ACTION_REPLAY_WAITRESET) { put_byte(0x1100c,1); put_long(0x11008,1); }
    if(action_replay_flag==ACTION_REPLAY_HIDE) ar_exit();
    if(action_replay_flag==ACTION_REPLAY_IDLE) {
        bool pending=false; for(const auto &e:eventtab2) pending=pending||(e.active && e.handler==p6_ar_cia_handler());
        if(!pending) {
            const auto mode=ar_control().mode_write;
            if(mode&2) action_replay_cia_access(false);
            else if(mode&1) action_replay_cia_access(true);
            else assert(action_replay_freeze());
        }
    }
    step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE);
    const auto entered=do_get_mem_long(p6_ar_memory()+0x100),main=get_long(0x11000);
    assert(entered>0 && get_long(0x7c)==0x400100); step(2);
    assert(do_get_mem_long(p6_ar_memory()+0x100)==entered && get_long(0x11000)==main);
    ar_exit(); assert(get_long(0x11000)>main);
    assert(action_replay_freeze()); step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE);
    assert(do_get_mem_long(p6_ar_memory()+0x100)==entered+1); ar_exit(); step(2);
    const auto state=save(); observe(state.data(),state.size());
    std::array<uint8_t,32> out{}; p6_sha256_final(&observed,out.data()); return out;
}
static void ar_continuations(const std::vector<uint8_t> &rom,const char *directory,bool writing)
{
    for(unsigned model:{2u,3u}) for(cpu_mode=P6_CPU_EXACT;cpu_mode<=P6_CPU_COMPATIBLE;++cpu_mode) {
        const auto cart=ar_cart(model); std::vector<std::vector<uint8_t>> cuts;
        if(writing) {
            ar_start(rom,cart,model); step(); assert(action_replay_flag==ACTION_REPLAY_WAITRESET);
            // The original ROM waits on1100c without word/long RAM writes.
            // Configure only byte data; PC/prefetch advance solely in p6_run_frame.
            for(unsigned n=0;n<16;++n) put_byte(0x11000+n,0);
            put_byte(0x11005,0xfe); put_byte(0x12340,0x4e); put_byte(0x12341,0x73);
            const uint8_t vector[]={0,1,0x23,0x40}; for(unsigned n=0;n<4;++n) put_byte(0x7c+n,vector[n]);
            for(unsigned n=0;n<512;++n)ar_custom[n]=uint8_t(n*7);
            memset(ar_ciaa,0x39,16); memset(ar_ciab,0xb6,16);
            step(); assert(action_replay_flag==ACTION_REPLAY_WAITRESET); cuts.push_back(save());
            put_byte(0x1100c,1); put_long(0x11008,1); assert(action_replay_flag==ACTION_REPLAY_DORESET); cuts.push_back(save());
            step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE); cuts.push_back(save());
            ar_exit(); cuts.push_back(save()); assert(action_replay_freeze()); cuts.push_back(save());
            step(); put_byte(0x400001,0xfe); assert(action_replay_flag==ACTION_REPLAY_HIDE); cuts.push_back(save());
            ar_exit(); action_replay_cia_access(false); cuts.push_back(save());
            if(cpu_mode==P6_CPU_EXACT) {
                uint64_t pending=0;
                for(const auto &span:spans) if(span.second.second=="ev2.handler" && cuts.back()[span.first]==15) {
                    pending=span.first;
                }
                assert(pending); auto bad=cuts.back(); bad[pending+9]=2;
                reseal(bad,p6_state::current_identity()); reject(bad,P6_ERROR_CORRUPT_STATE);
            } else assert(action_replay_flag==ACTION_REPLAY_ACTIVATE);
            step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE); ar_exit(true);
            const auto hidden=ar_control(); assert(hidden.hidden && hidden.banks && !hidden.hide); cuts.push_back(save());
            assert(action_replay_freeze()); step(); put_byte(0x11005,0xfd); ar_exit();
            action_replay_cia_access(true); cuts.push_back(save());
            if(cpu_mode==P6_CPU_EXACT) {
                load(cuts[2]); same_state(cuts[2]);
                const std::pair<const char *,uint32_t> bad[]={{"ar.size",0},{"ar.flag",0},{"ar.flag",uint32_t(-2)},
                    {"ar.flag",6},{"ar.mapped",2},{"ar.mapped",uint32_t(-1)},{"ar.banks",0},{"ar.banks",2},
                    {"ar.mode_read",4},{"ar.mode_write",256},{"ar.hardreset",2},
                    {"ar.hide",1},{"ar.hide",2},{"ar.hidden",1},{"ar.hidden",2},{"cpu.spcflags",0}};
                for(const auto &v:bad) corrupt(cuts[2],v.first,v.second);
                auto foreign=cuts[2]; auto id=p6_state::current_identity(); id.media_manifest_hash[0]^=1;
                reseal(foreign,id); reject(foreign,P6_ERROR_INCOMPATIBLE);
            }
            auto e=error(); call(p6_shutdown(&e),e);
        }
        for(unsigned cut=0;cut<9;++cut) {
            const auto path=std::string(directory)+"/ar-"+std::to_string(model)+"-"+std::to_string(cpu_mode)+"-"+std::to_string(cut)+".state";
            const auto state=writing?cuts[cut]:file(path.c_str()); ar_start(rom,cart,model);
            // A rejected state must not start a cold owner or touch its input bytes.
            auto foreign=state; auto id=p6_state::current_identity(); id.media_manifest_hash[0]^=1; reseal(foreign,id);
            const auto unchanged=foreign; auto e=error();
            assert(p6_state_load(foreign.data(),foreign.size(),&e)==P6_ERROR_INCOMPATIBLE && foreign==unchanged && !p6_session::session.started);
            load(state); same_state(state); const auto first=ar_future();
            load(state); same_state(state); assert(first==ar_future());
            if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
            printf("AR continuation %u/%u/%u ",model,cpu_mode,cut); for(auto byte:first)printf("%02x",byte); puts("");
            call(p6_shutdown(&e),e); assert(!p6_bridge::ar_bound() && !armodel && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
        }
        // Different immutable bytes/model are not interchangeable, even before first run.
        auto other=cart; other[0x2000]^=1; ar_start(rom,other,model);
        const auto path=std::string(directory)+"/ar-"+std::to_string(model)+"-"+std::to_string(cpu_mode)+"-2.state";
        const auto state=file(path.c_str()); auto e=error();
        assert(p6_state_load(state.data(),state.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started);
        call(p6_shutdown(&e),e);
        if(model==3) {
            ar_start(rom,cart,model);
            const auto other=file((std::string(directory)+"/ar-2-"+std::to_string(cpu_mode)+"-2.state").c_str());
            assert(p6_state_load(other.data(),other.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started);
            call(p6_shutdown(&e),e);
        }
    }
    cpu_mode=P6_CPU_EXACT;
}
// AR1's original fixture owns vector0x60 and the byte/word strobes; the host
// only changes ordinary RAM signals. No read of F00000, PC edit or synthetic NMI.
static void ar1_start(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,bool checks=false)
{
    auto e=error(); auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e);
    if(checks) {
        assert(p6_cartridge_bind(0,{cart.data(),cart.size()},&e)==P6_ERROR_BAD_ARGUMENT);
        p6_error unusable{}; unusable.abi_version=99; unusable.message[0]='x'; const auto before=unusable;
        assert(p6_bridge::bind_ar1({cart.data(),cart.size()},&unusable)==P6_ERROR_BAD_ARGUMENT && !memcmp(&before,&unusable,sizeof before));
        assert(p6_bridge::bind_ar1({nullptr,cart.size()},&e)==P6_ERROR_WRONG_MEDIA);
        for(size_t n:{cart.size()-1,cart.size()+1})
            assert(p6_bridge::bind_ar1({cart.data(),n},&e)==P6_ERROR_WRONG_MEDIA && !p6_bridge::ar_bound());
        { p6_session::Guard busy; assert(busy.ok); assert(p6_bridge::bind_ar1({cart.data(),cart.size()},&e)==P6_ERROR_BUSY); }
    }
    auto copy=cart; call(p6_bridge::bind_ar1({copy.data(),copy.size()},&e),e); copy.assign(copy.size(),0);
    uint8_t bytes[8]; size_t got=0;
    assert(p6_blob_pread("p6-action-replay.rom",0x100,bytes,8,&got)==P6_BLOB_OK && got==8 && !memcmp(bytes,cart.data()+0x100,8));
    assert(p6_bridge::bind_ar1({cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    assert(p6_bridge::bind_hrtmon({nullptr,0},&e)==P6_ERROR_BAD_STATE);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); p6_sha256_init(&observed);
    assert(armodel==1 && !p6_ar_cia_handler());
}
static void ar1_banks()
{
    const auto *rom=get_mem_bank(0xf00000).name,*ram=get_mem_bank(0x9f0000).name;
    assert(rom && ram && !strcmp(rom,"Action Replay ROM") && !strcmp(ram,"Action Replay RAM"));
}
static void ar1_load(const std::vector<uint8_t> &state) { load(state); ar1_banks(); }
static void ar1_stop()
{
    // p6_load adopts the owner; its original hardware reset runs on first run/loadstate.
    const bool mapped=p6_session::session.started; if(mapped) ar1_banks();
    const auto *rom=&get_mem_bank(0xf00000),*ram=&get_mem_bank(0x9f0000); stop();
    assert(!p6_bridge::ar_bound() && !armodel);
    if(mapped) assert(&get_mem_bank(0xf00000)!=rom && &get_mem_bank(0x9f0000)!=ram);
}
static void ar1_release()
{
    put_byte(0x11011,1); put_byte(0x11012,0); put_byte(0x11014,1); step();
}
static std::array<uint8_t,32> ar1_future()
{
    p6_sha256_init(&observed); put_byte(0x11010,1); ar1_release(); ar1_release();
    put_byte(0x11011,0); put_byte(0x11014,0);
    assert(action_replay_flag==ACTION_REPLAY_IDLE && p6_bridge::request_cartridge_freeze()); step();
    assert(action_replay_flag==ACTION_REPLAY_ACTIVE && ar_control().wait_pop==1 && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    const auto entered=get_long(0x9f0100); assert(entered && get_long(0x7c)==0xf00200);
    ar1_release(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE && !ar_control().wait_pop);
    ar1_release(); assert(action_replay_flag==ACTION_REPLAY_IDLE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    assert(get_long(0x9f0100)==entered+1 && get_long(0x11000)>0); step(2);
    const auto state=save(); observe(state.data(),state.size()); std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data()); return digest;
}
static void ar1_continuations(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,const char *directory,bool writing)
{
    for(unsigned machine:{P6_MACHINE_A1200,P6_MACHINE_A500}) {
        auto e=error(); auto c=config(machine); if(machine==P6_MACHINE_A500) c.cpu_compatibility=P6_CPU_NORMAL;
        call(p6_init(&c,&e),e); assert(p6_bridge::bind_ar1({cart.data(),cart.size()},&e)==P6_ERROR_UNSUPPORTED && !p6_bridge::ar_bound()); stop();
    }
    for(cpu_mode=P6_CPU_EXACT;cpu_mode<=P6_CPU_COMPATIBLE;++cpu_mode) {
        std::vector<std::vector<uint8_t>> cuts; auto e=error();
        if(writing) {
            ar1_start(rom,cart,true); step(); ar1_banks();
            put_long(0x9f4000,0x89abcdef); put_long(0x9ffffc,0x1234567);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE && !ar_control().wait_pop && !(regs.spcflags&SPCFLAG_ACTION_REPLAY)); cuts.push_back(save());
            put_byte(0x11010,1); step(); assert(action_replay_flag==ACTION_REPLAY_IDLE); cuts.push_back(save());
            assert(p6_bridge::request_cartridge_freeze()); assert(action_replay_flag==ACTION_REPLAY_ACTIVATE && ar_control().wait_pop); cuts.push_back(save());
            step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY)); cuts.push_back(save());
            put_byte(0x11011,1); step(); assert(action_replay_flag==ACTION_REPLAY_WAIT_PC && !ar_control().wait_pop);
            assert(ar_control().wait_pc>=0xf80000 && ar_control().wait_pc<0xfc0000); cuts.push_back(save());
            ar1_load(cuts[3]); put_byte(0x11012,1); put_byte(0x11011,1); step();
            assert(action_replay_flag==ACTION_REPLAY_IDLE && !ar_control().wait_pop && (regs.spcflags&SPCFLAG_ACTION_REPLAY)); cuts.push_back(save());
            ar1_load(cuts[4]); put_byte(0x11014,1); step(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE && !ar_control().wait_pop); cuts.push_back(save());
            ar1_release(); assert(action_replay_flag==ACTION_REPLAY_IDLE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY)); cuts.push_back(save());
            ar1_load(cuts[3]); same_state(cuts[3]);
            for(const auto &bad:std::initializer_list<std::pair<const char *,uint32_t>>{{"ar.wait_pop",2},{"ar.flag",0},{"ar.flag",4},
                {"ar.flag",uint32_t(-1)},{"ar.banks",0},{"ar.mapped",0},{"ar.hidden",1},{"ar.hide",2},{"ar.mode_write",1},{"cpu.spcflags",SPCFLAG_ACTION_REPLAY}})
                corrupt(cuts[3],bad.first,bad.second);
            ar1_load(cuts[4]); same_state(cuts[4]); corrupt(cuts[4],"cpu.spcflags",0);
            // ID15 names the AR2/3 CIA callback. It is invalid even with an AR1 owner.
            auto bad=cuts[4]; const auto slot=fields.at("ev2.active"); bad[slot]=1; bad[slot+1]=15;
            const uint64_t when=get_cycles()+CYCLE_UNIT; for(unsigned n=0;n<8;++n) bad[slot+2+n]=uint8_t(when>>(8*n));
            reseal(bad,p6_state::current_identity()); reject(bad,P6_ERROR_CORRUPT_STATE); ar1_stop();
        }
        for(unsigned cut=0;cut<8;++cut) {
            const auto path=std::string(directory)+"/ar1-"+std::to_string(cpu_mode)+"-"+std::to_string(cut)+".state";
            const auto state=writing?cuts[cut]:file(path.c_str()); ar1_start(rom,cart);
            auto foreign=state; auto id=p6_state::current_identity(); id.media_manifest_hash[0]^=1; reseal(foreign,id); const auto original=foreign;
            assert(p6_state_load(foreign.data(),foreign.size(),&e)==P6_ERROR_INCOMPATIBLE && foreign==original && !p6_session::session.started);
            ar1_load(state); same_state(state); assert(get_long(0x9f4000)==0x89abcdef && get_long(0x9ffffc)==0x1234567); const auto first=ar1_future();
            ar1_load(state); same_state(state); assert(first==ar1_future());
            if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
            printf("AR1 continuation %u/%u ",cpu_mode,cut); for(auto byte:first) printf("%02x",byte); puts(""); ar1_stop();
        }
        const auto state=file((std::string(directory)+"/ar1-"+std::to_string(cpu_mode)+"-3.state").c_str());
        auto other=cart; other[0x500]^=1; uint32_t checksum=0;
        for(size_t n=0;n<other.size()-4;n+=2) checksum+=int16_t(do_get_mem_word(other.data()+n));
        do_put_mem_long(other.data()+other.size()-4,checksum); ar1_start(rom,other);
        assert(p6_state_load(state.data(),state.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started); ar1_stop();
        ar_start(rom,ar_cart(2),2); assert(p6_state_load(state.data(),state.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started); stop();
        auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e); std::vector<uint8_t> invalid(65536);
        call(p6_bridge::bind_ar1({invalid.data(),invalid.size()},&e),e);
        assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA && !p6_bridge::ar_bound() && !armodel); stop();
        call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
        assert(!p6_bridge::ar_bound() && !armodel && !currprefs.cartfile[0]); stop();
    }
    cpu_mode=P6_CPU_EXACT; puts("AR1 private owner/cuts/refusals/teardown PASS");
}
static void public_ar_start(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,unsigned model,bool negatives=false)
{
    input_v2=false; command_level=0; auto e=error(); auto c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e);
    if(negatives) {
        for(unsigned bad:{0u,1200u}) assert(p6_cartridge_bind(bad,{cart.data(),cart.size()},&e)==P6_ERROR_BAD_ARGUMENT);
        assert(p6_cartridge_bind(model,{nullptr,cart.size()},&e)==P6_ERROR_WRONG_MEDIA);
        assert(p6_cartridge_bind(model,{cart.data(),cart.size()-1},&e)==P6_ERROR_WRONG_MEDIA);
        p6_error unusable{}; unusable.abi_version=99; unusable.message[0]='x'; const auto before=unusable;
        assert(p6_cartridge_bind(model,{cart.data(),cart.size()},&unusable)==P6_ERROR_BAD_ARGUMENT && !memcmp(&before,&unusable,sizeof before));
        { p6_session::Guard busy; assert(busy.ok); assert(p6_cartridge_bind(model,{cart.data(),cart.size()},&e)==P6_ERROR_BUSY); }
    }
    auto copy=cart; call(p6_cartridge_bind(model,{copy.data(),copy.size()},model==3?nullptr:&e),e); copy.assign(copy.size(),0);
    uint8_t bytes[16]; size_t got=0; assert(p6_blob_pread("p6-action-replay.rom",0,bytes,sizeof bytes,&got)==P6_BLOB_OK && got==16 && !memcmp(bytes,cart.data(),16));
    assert(p6_cartridge_bind(model,{cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    assert(p6_bridge::bind_hrtmon({nullptr,0},&e)==P6_ERROR_BAD_STATE);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); p6_sha256_init(&observed);
}
static void public_ar_stop()
{
    if(armodel==1) ar1_stop(); else { auto e=error(); call(p6_shutdown(&e),e); }
    input_v2=false; command_level=0;
    assert(!p6_bridge::ar_bound() && !armodel && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
}
static void ar_packet_refusals(const std::vector<uint8_t> &state)
{
    same_state(state); reject_commands(2,P6_ERROR_BAD_ARGUMENT); reject_commands(3,P6_ERROR_BAD_ARGUMENT);
    auto bad=state; bad[fields.at("applied.commands")]=2;
    reseal(bad,p6_state::current_identity()); reject(bad,P6_ERROR_CORRUPT_STATE);
}
static void public_ar_own_setup()
{
    for(unsigned n=0;n<16;++n)put_byte(0x11000+n,0);
    put_byte(0x11005,0xfe); const uint8_t vector[]={0,1,0x23,0x40};
    for(unsigned n=0;n<4;++n)put_byte(0x7c+n,vector[n]);
    put_byte(0x12340,0x4e); put_byte(0x12341,0x73);
}
static void public_ar_own(const std::vector<uint8_t> &rom,const char *directory,bool writing)
{
    auto e=error(); const auto first=ar_cart(2);
    assert(p6_cartridge_bind(2,{first.data(),first.size()},&e)==P6_ERROR_BAD_STATE);
    for(unsigned model:{2u,3u}) {
        const auto cart=ar_cart(model); public_ar_start(rom,cart,model,true);
        const auto path=std::string(directory)+"/ar-public-own-"+std::to_string(model)+".state";
        std::vector<uint8_t> state;
        if(writing) {
            level(1,2); assert(held_command()==1 && action_replay_flag==ACTION_REPLAY_WAITRESET);
            public_ar_own_setup();
            put_byte(0x1100c,1); put_long(0x11008,1); level(1);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE); ar_exit(); level(1,3);
            assert(action_replay_flag==ACTION_REPLAY_IDLE); level(0); level(1);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE); state=save();
            FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f);
        } else { state=file(path.c_str()); load(state); }
        ar_packet_refusals(state);
        std::array<uint8_t,32> twin{};
        for(unsigned replay=0;replay<2;++replay) {
            load(state); same_state(state); input_v2=true; command_level=1; assert(held_command()==1);
            p6_sha256_init(&observed); const auto counter=get_long(0x11000); level(1,4); assert(get_long(0x11000)==counter);
            ar_exit(); level(1,4); assert(action_replay_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>counter);
            input_v2=false; step(); assert(!held_command()); level(1);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE); ar_exit(); level(1,3);
            const auto final=save(); observe(final.data(),final.size()); std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data());
            if(replay) assert(digest==twin); else twin=digest;
        }
        printf("AR public continuation own/%u/0 ",model); for(auto byte:twin)printf("%02x",byte); puts("");
        if(writing) {
            call(p6_reset(&e),e); level(1,2); assert(held_command()==1 && action_replay_flag==ACTION_REPLAY_WAITRESET);
            public_ar_own_setup(); put_byte(0x1100c,1); put_long(0x11008,1); level(1);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE && ar_control().mode_read==3); ar_exit(); level(1,3);
            assert(action_replay_flag==ACTION_REPLAY_IDLE && held_command()==1);
        }
        public_ar_stop();
    }
    auto c=config(P6_MACHINE_A1200); call(p6_init(&c,&e),e);
    assert(p6_cartridge_bind(2,{first.data(),first.size()},&e)==P6_ERROR_UNSUPPORTED); call(p6_shutdown(&e),e);
    c=config(P6_MACHINE_A500); call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    p6_sha256_init(&observed); level(0); reject_commands(1,P6_ERROR_UNSUPPORTED); reject_commands(2,P6_ERROR_BAD_ARGUMENT); public_ar_stop();
    puts("AR public bind/copy/commands/held/reset/ABI/refusals PASS");
}
static bool real_ar_editor_ready()
{
    const auto h=ar_control();
    if(h.flag!=ACTION_REPLAY_ACTIVE || h.mode_read!=0) return false;
    const auto ram=p6_ar_memory(); assert(ram && (armodel==2 || armodel==3));
    // Fixed ROMs checked in test_hrtmon.py. Freeze resets A7 to $450000
    // (AR2 $400892 / AR3 $40104a); init calls overwrite its old top frames.
    // The interpreter then saves A7 at $44f56a/$44f568 and calls the editor
    // at $401946/$4024d6. Its live return is $40194a/$4024da, below which
    // the editor saves D0 and calls its key reader or renderer. A saved SP
    // or a PC inside the ROM alone also occurs while credits are printing.
    const uint32_t saved=do_get_mem_long(ram+(armodel==2?0xf56a:0xf568));
    const uint32_t sp=m68k_areg(regs,7);
    if(saved!=0x44fffc || sp<0x440000 || sp>saved-12 || (sp&1)) return false;
    if(do_get_mem_long(ram+(saved-0x440000-4))!=(armodel==2?0x40194a:0x4024da)) return false;
    const auto inner=do_get_mem_long(ram+(saved-0x440000-12));
    if(inner!=(armodel==2?0x4018a0:0x40240c) && inner!=(armodel==2?0x4018c8:0x402444)) return false;
    // Both editors use 80-column rows, font codes in AR2 and ASCII in AR3.
    // Require the current empty input row, not the preceding printed prompt.
    if(do_get_mem_word(ram+0xf29a)!=0) return false;
    const uint64_t row=uint64_t(do_get_mem_long(ram+0xf2a2))+80u*do_get_mem_word(ram+0xf29c);
    if(row<0x440000 || row+80>0x450000) return false;
    for(unsigned n=0;n<80;++n) if(ram[row-0x440000+n]!=(armodel==2?0x46:0x20)) return false;
    return true;
}
static void real_ar_wait_editor()
{
    // A fail bound, not a delay or a readiness oracle. Advance only emulated units.
    for(unsigned elapsed=0;elapsed<600;++elapsed) {
        if(real_ar_editor_ready()) return;
        level(1);
    }
    if(real_ar_editor_ready()) return;
    const auto ram=p6_ar_memory(); assert(ram);
    fprintf(stderr,"AR editor not ready model=%d flag=%d PC=%08x SP=%08x savedSP=%08x x=%u y=%u after 600 units\n",
        armodel,action_replay_flag,m68k_getpc(),m68k_areg(regs,7),
        do_get_mem_long(ram+(armodel==2?0xf56a:0xf568)),do_get_mem_word(ram+0xf29a),do_get_mem_word(ram+0xf29c));
    abort();
}
static void real_ar_loop()
{
    // Loaded while the real monitor is paused; execution starts only through its G command.
    const uint8_t program[]={0x52,0xb9,0,1,0x10,0,0x60,0xf8};
    for(unsigned n=0;n<4;++n)put_byte(0x11000+n,0);
    for(unsigned n=0;n<sizeof program;++n)put_byte(0x12000+n,program[n]);
    real_ar_wait_editor(); command("g 12000");
    if(action_replay_flag!=ACTION_REPLAY_IDLE || !get_long(0x11000))
        fprintf(stderr,"AR G failed model=%d flag=%d PC=%08x counter=%u\n",armodel,action_replay_flag,m68k_getpc(),get_long(0x11000));
    assert(action_replay_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>0);
}
static void real_ar_boot()
{
    auto phase=[](const char *name) {
        const auto h=ar_control();
        fprintf(stderr,"AR %s model=%d flag=%d PC=%08x mode=%u hidden=%u banks=%u held=%u counter=%u\n",
            name,armodel,action_replay_flag,m68k_getpc(),h.mode_read,h.hidden,h.banks,held_command(),get_long(0x11000));
    };
    level(1,300); phase("boot");
    // F1 is the primary ROM's "continue booting" command, only for its reset menu.
    // A cart which already returned to Kickstart needs an explicit Freeze edge.
    if(action_replay_flag!=ACTION_REPLAY_IDLE) {
        assert(action_replay_flag==ACTION_REPLAY_ACTIVE || action_replay_flag==ACTION_REPLAY_HIDE);
        step(3,AK_F1); step(24); phase("continue");
    }
    assert(action_replay_flag==ACTION_REPLAY_IDLE);
    level(0); level(1); real_ar_wait_editor(); phase("freeze");
    assert(action_replay_flag==ACTION_REPLAY_ACTIVE && ar_control().mode_read==0);
    real_ar_loop(); phase("G");
    level(1,4); assert(action_replay_flag==ACTION_REPLAY_IDLE); // Held boot level was consumed.
}
static void public_ar_real(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,unsigned model,const char *directory,bool writing)
{
    std::vector<std::vector<uint8_t>> cuts; auto e=error();
    if(writing) {
        public_ar_start(rom,cart,model); real_ar_boot(); level(0); level(1); real_ar_wait_editor();
        assert(action_replay_flag==ACTION_REPLAY_ACTIVE && held_command()==1); cuts.push_back(save());
        command("x",false); cuts.push_back(save()); public_ar_stop();
    }
    for(unsigned cut=0;cut<2;++cut) {
        const auto path=std::string(directory)+"/ar-public-real-"+std::to_string(model)+"-"+std::to_string(cut)+".state";
        const auto state=writing?cuts[cut]:file(path.c_str()); public_ar_start(rom,cart,model);
        load(state); ar_packet_refusals(state); std::array<uint8_t,32> twin{};
        for(unsigned replay=0;replay<2;++replay) {
            load(state); same_state(state); input_v2=true; command_level=1; assert(held_command()==1);
            p6_sha256_init(&observed); const auto counter=get_long(0x11000); level(1,8);
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE && get_long(0x11000)==counter);
            if(cut) { step(3,AK_RET); step(12); } else { real_ar_wait_editor(); command("x"); }
            level(1,5); assert(action_replay_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>counter);
            input_v2=false; step(); assert(!held_command()); level(1); real_ar_wait_editor();
            assert(action_replay_flag==ACTION_REPLAY_ACTIVE); command("x"); step(8);
            assert(action_replay_flag==ACTION_REPLAY_IDLE);
            const auto final=save(); observe(final.data(),final.size()); std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data());
            if(replay) assert(twin==digest); else twin=digest;
        }
        if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
        printf("AR public continuation real/%u/%u ",model,cut); for(auto byte:twin)printf("%02x",byte); puts(""); public_ar_stop();
    }
    if(writing) {
        public_ar_start(rom,cart,model); load(cuts[0]); input_v2=true; command_level=1;
        call(p6_reset(&e),e); real_ar_boot(); assert(held_command()==1);
        level(0); level(1); real_ar_wait_editor(); assert(action_replay_flag==ACTION_REPLAY_ACTIVE); command("x"); step(8);
        assert(action_replay_flag==ACTION_REPLAY_IDLE); public_ar_stop();
    }
}
// AR1 v1.00, SHA1 pinned by the Python gate. Its editor is not AR2/3's.
static bool real_ar1_editor_ready()
{
    const auto h=ar_control(); const auto ram=p6_ar_memory(); assert(ram && armodel==1);
    if(h.flag!=ACTION_REPLAY_ACTIVE || h.wait_pop || !h.wait_pc || (regs.spcflags&SPCFLAG_ACTION_REPLAY)) return false;
    // Freeze first runs F058C8: vector7c=F00006, BYTE strobe, RTE, WAIT_PC
    // and one-instruction reentry. Only then does F00014 open this editor.
    if(get_long(0x7c)!=0xc5f00006) return false;
    // The C5 vector tag survives in 32-bit PC/BSR frames (newcpu Exception_ce000).
    const uint32_t saved=do_get_mem_long(ram+0xe0fa),sp=m68k_areg(regs,7);
    if(saved!=0x9feffc || sp<0x9fe000 || sp>saved-12 || (sp&1)) return false;
    if(do_get_mem_long(ram+0xeffc)!=0xc5f0018c || do_get_mem_long(ram+0xeff8)!=0xc5f012ba) return false;
    const auto inner=do_get_mem_long(ram+0xeff0);
    if(inner!=0xc5f0126a && inner!=0xc5f0126e) return false;
    // F012A0 clears this marker before collecting a line. Forty font-coded
    // columns use46 for space; require the live empty row, not printed credits.
    if(do_get_mem_word(ram+0xe088) || do_get_mem_word(ram+0xe076)) return false;
    const uint32_t y=do_get_mem_word(ram+0xe078); if(y>24) return false;
    const uint64_t row=uint64_t(do_get_mem_long(ram+0xe07e))+40u*y;
    if(row<0x9f0000 || row+40>0xa00000) return false;
    for(unsigned n=0;n<40;++n) if(ram[row-0x9f0000+n]!=0x46) return false;
    return true;
}
static void real_ar1_wait(bool editor)
{
    for(unsigned elapsed=0;elapsed<600;++elapsed) {
        if(editor?real_ar1_editor_ready():action_replay_flag==ACTION_REPLAY_IDLE) return;
        level(1);
    }
    fprintf(stderr,"AR1 wait failed editor=%d flag=%d PC=%08x SP=%08x pop=%u waitPC=%08x x=%u y=%u\n",
        editor,action_replay_flag,m68k_getpc(),m68k_areg(regs,7),ar_control().wait_pop,ar_control().wait_pc,
        do_get_mem_word(p6_ar_memory()+0xe076),do_get_mem_word(p6_ar_memory()+0xe078)); abort();
}
static void real_ar1_boot()
{
    // AR1 returns to Kickstart itself; F1 is Clr+Home, never Continue Boot.
    level(1,300); real_ar1_wait(false); assert(held_command()==1);
    const uint32_t sysbase=get_long(4); const auto &bank=get_mem_bank(sysbase);
    assert(sysbase && !(sysbase&1) && (&bank==&chipmem_bank || &bank==&bogomem_bank) && bank.baseaddr &&
        sysbase>=bank.start && uint64_t(sysbase)-bank.start+2<=bank.allocated_size);
}
static void real_ar1_loop()
{
    real_ar1_wait(true);
    const uint8_t program[]={0x52,0xb9,0,1,0x10,0,0x60,0xf8};
    for(unsigned n=0;n<4;++n) put_byte(0x11000+n,0);
    for(unsigned n=0;n<sizeof program;++n) put_byte(0x12000+n,program[n]);
    // G handler F01930 writes the saved return PC; X F0198E keeps it.
    command("g 12000"); real_ar1_wait(false); level(1,5);
    assert(get_long(0x11000)>0 && action_replay_flag==ACTION_REPLAY_IDLE && held_command()==1);
}
static void public_ar1_real(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,const char *directory,bool writing)
{
    auto e=error();
    for(cpu_mode=P6_CPU_EXACT;cpu_mode<=P6_CPU_COMPATIBLE;++cpu_mode) {
        std::vector<std::vector<uint8_t>> cuts;
        if(writing) {
            public_ar_start(rom,cart,1,true); real_ar1_boot();
            level(0); level(1); real_ar1_loop(); // Real first Freeze handoff and G, not host PC/IRQ changes.
            level(0); level(1); real_ar1_wait(true); cuts.push_back(save());
            command("x",false); cuts.push_back(save()); public_ar_stop();
        }
        for(unsigned cut=0;cut<2;++cut) {
            const auto path=std::string(directory)+"/ar1-public-"+std::to_string(cpu_mode)+"-"+std::to_string(cut)+".state";
            const auto state=writing?cuts[cut]:file(path.c_str()); public_ar_start(rom,cart,1);
            auto foreign=state; auto id=p6_state::current_identity(); id.media_manifest_hash[0]^=1; reseal(foreign,id);
            const auto before=foreign; const auto ticks=p6_session::session.master_ticks;
            assert(p6_state_load(foreign.data(),foreign.size(),&e)==P6_ERROR_INCOMPATIBLE && foreign==before &&
                !p6_session::session.started && p6_session::session.master_ticks==ticks);
            ar1_load(state); ar_packet_refusals(state); std::array<uint8_t,32> twin{};
            for(unsigned replay=0;replay<2;++replay) {
                ar1_load(state); same_state(state); input_v2=true; command_level=1; assert(held_command()==1);
                p6_sha256_init(&observed); const auto counter=get_long(0x11000); level(1,8);
                assert(action_replay_flag==ACTION_REPLAY_ACTIVE && get_long(0x11000)==counter);
                if(cut) { step(3,AK_RET); step(12); } else { real_ar1_wait(true); command("x"); }
                real_ar1_wait(false); level(1,5); assert(get_long(0x11000)>counter && action_replay_flag==ACTION_REPLAY_IDLE);
                input_v2=false; step(); assert(!held_command()); level(1); real_ar1_wait(true);
                assert(get_long(0x11000)>counter); command("x"); real_ar1_wait(false); level(1,5);
                assert(action_replay_flag==ACTION_REPLAY_IDLE && held_command()==1);
                const auto final=save(); observe(final.data(),final.size()); std::array<uint8_t,32> digest{};
                p6_sha256_final(&observed,digest.data()); if(replay) assert(twin==digest); else twin=digest;
            }
            if(writing) { FILE *f=fopen(path.c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
            printf("AR1 public continuation %u/%u ",cpu_mode,cut); for(auto byte:twin) printf("%02x",byte); puts(""); public_ar_stop();
        }
        const auto state=file((std::string(directory)+"/ar1-public-"+std::to_string(cpu_mode)+"-0.state").c_str());
        auto other=cart; other[0x8000]^=1; uint32_t checksum=0;
        for(size_t n=0;n<other.size()-4;n+=2) checksum+=int16_t(do_get_mem_word(other.data()+n));
        do_put_mem_long(other.data()+other.size()-4,checksum); public_ar_start(rom,other,1);
        assert(p6_state_load(state.data(),state.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started); public_ar_stop();
        if(writing) {
            public_ar_start(rom,cart,1); ar1_load(cuts[0]); input_v2=true; command_level=1;
            call(p6_reset(&e),e); real_ar1_boot(); // Held level survived reset without queuing Freeze.
            level(0); level(1); real_ar1_loop(); public_ar_stop();
        }
    }
    for(unsigned bad=0;bad<2;++bad) {
        auto c=config(bad?P6_MACHINE_A500:P6_MACHINE_A1200); if(bad) c.cpu_compatibility=P6_CPU_NORMAL;
        call(p6_init(&c,&e),e); assert(p6_cartridge_bind(1,{cart.data(),cart.size()},&e)==P6_ERROR_UNSUPPORTED && !p6_bridge::ar_bound()); public_ar_stop();
    }
    cpu_mode=P6_CPU_EXACT; puts("AR1 public real commands/cold state/held/reset/refusals PASS");
}
static void ar1200_start(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,bool checks=false)
{
    input_v2=false; command_level=0; auto e=error(); auto c=config(); call(p6_init(&c,&e),e);
    if(checks) {
        ar_state_contract(); p6_error bad{}; bad.abi_version=99; const auto before=bad;
        assert(p6_bridge::bind_ar1200({cart.data(),cart.size()},&bad)==P6_ERROR_BAD_ARGUMENT && !memcmp(&bad,&before,sizeof bad));
        assert(p6_bridge::bind_ar1200({nullptr,cart.size()},&e)==P6_ERROR_WRONG_MEDIA);
        for(size_t n:{cart.size()-1,cart.size()+1}) assert(p6_bridge::bind_ar1200({cart.data(),n},&e)==P6_ERROR_WRONG_MEDIA);
        auto invalid=cart; invalid[0]^=1;
        assert(p6_bridge::bind_ar1200({invalid.data(),invalid.size()},&e)==P6_ERROR_WRONG_MEDIA);
        { p6_session::Guard busy; assert(busy.ok); assert(p6_bridge::bind_ar1200({cart.data(),cart.size()},&e)==P6_ERROR_BUSY); }
    }
    auto copy=cart; call(p6_bridge::bind_ar1200({copy.data(),copy.size()},&e),e); copy.assign(copy.size(),0);
    assert(p6_bridge::ar1200_bound() && p6_bridge::hrtmon_bound() && !p6_bridge::ar_bound());
    assert(p6_bridge::bind_ar1200({cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    assert(p6_bridge::bind_hrtmon({nullptr,0},&e)==P6_ERROR_BAD_STATE);
    assert(p6_bridge::bind_action_replay(2,{nullptr,0},&e)==P6_ERROR_BAD_STATE);
    if(checks) ar_state_contract();
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); p6_sha256_init(&observed);
    assert(armodel==1200 && hrtmem_start==0x800000 && !p6_ar_cia_handler());
    assert(!currprefs.cart_internal && !strcmp(currprefs.cartfile,"p6-cartridge.rom"));
    if(checks) ar_state_contract();
}
static void ar1200_immutable(const std::vector<uint8_t> &cart)
{
    const auto ram=p6_hrtmon_memory(); assert(ram && !memcmp(ram,cart.data(),cart.size()));
    for(unsigned n=cart.size();n<0x80000;++n) assert(ram[n]==0xff);
    p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h) && h.mapped<=1);
    if(h.mapped) {
        for(uint32_t at:{0x800000u,0x83fffcu,0x840000u,0x87fffcu}) {
            const auto before=get_long(at); put_byte(at,0); put_word(at,0); put_long(at,0); assert(get_long(at)==before);
        }
        for(uint32_t at:{0x880000u,0x88fffcu,0x8ffffcu}) assert(&get_mem_bank(at)==&get_mem_bank(0x800000));
    }
}
static void ar1200_ram_pattern(bool write)
{
    // Independent oracle over every writable byte; untouched cold allocations
    // cannot pass by serializing a convenient subset or a save/load roundtrip.
    for(uint32_t n=0;n<0x80000;++n) {
        if((n>=0x100 && n<0x104) || n==0x40000 || n==0x40001) continue;
        const uint8_t value=uint8_t(n*37+(n>>8));
        if(write) put_byte(0x880000+n,value);
        else assert(p6_hrtmon_memory()[0x80000+n]==value);
    }
}
static void ar1200_stop()
{
    const auto *bank=&get_mem_bank(0x800000); const bool mapped=p6_hrtmon_memory() && bank->baseaddr==p6_hrtmon_memory();
    stop(); assert(!armodel && !p6_bridge::ar1200_bound());
    if(mapped) for(unsigned n=0;n<16;++n) assert(&get_mem_bank(0x800000+(n<<16))!=bank);
}
static void ar1200_ready()
{
    // SHA1-pinned AR1200: $80038a calls the editor after initialization, with
    // A7=$88fffc and return $80038e. $8008e2 STOP #$2000 is its empty-key wait.
    for(unsigned elapsed=0;elapsed<600;++elapsed) {
        const auto ram=p6_hrtmon_memory(); assert(ram);
        if(hrtmon_flag==ACTION_REPLAY_ACTIVE && regs.stopped && m68k_getpc()==0x8008e6 && m68k_areg(regs,7)==0x88fffc &&
            do_get_mem_long(ram+0x8fffc)==0x80038e && !ram[0x802d2] && !ram[0x802d7] && !ram[0x80307] && !do_get_mem_word(ram+0x8030e)) return;
        step();
    }
    fprintf(stderr,"AR1200 READY failed flag=%d PC=%08x SP=%08x stopped=%d\n",hrtmon_flag,m68k_getpc(),m68k_areg(regs,7),regs.stopped); abort();
}
static void ar1200_freeze()
{
    auto e=error(); call(p6_bridge::freeze_hrtmon(&e),e); assert(hrtmon_flag==ACTION_REPLAY_ACTIVATE);
}
static std::array<uint8_t,32> ar1200_future(unsigned cut,bool real)
{
    p6_sha256_init(&observed);
    if(!cut) ar1200_freeze();
    if(real) {
        if(cut!=3) ar1200_ready();
        // Load our loop while the monitor is paused; only R PC and X execute it.
        const uint8_t code[]={0x52,0xb9,0,1,0x10,0,0x60,0xf8};
        for(unsigned n=0;n<sizeof code;++n) put_byte(0x12000+n,code[n]);
        for(unsigned n=0;n<4;++n) put_byte(0x11000+n,0);
        if(cut==3) command("28"); // Continue the saved partial `r pc 737`, no empty-row wait.
        else command("r pc 73728");
        assert(get_long(0x88004a)==0x12000); ar1200_ready(); command("x"); step(8);
        assert(hrtmon_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>0);
        ar1200_freeze(); ar1200_ready(); const auto frozen=get_long(0x11000); step(4); assert(get_long(0x11000)==frozen);
        command("x"); step(8); assert(hrtmon_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>frozen);
    } else {
        if(cut<2) step();
        assert(hrtmon_flag==ACTION_REPLAY_ACTIVE && get_word(0x8c0000)==4);
        const auto frozen=get_long(0x11000),vector=get_long(regs.vbr+8); step(2); assert(get_long(0x11000)==frozen);
        put_byte(0x1100c,1); step(2); assert(hrtmon_flag==ACTION_REPLAY_IDLE && get_long(0x11000)>frozen && get_long(regs.vbr+8)==vector);
        put_byte(0x1100c,0); ar1200_freeze(); step(); assert(hrtmon_flag==ACTION_REPLAY_ACTIVE);
        put_byte(0x1100c,1); step(2); assert(hrtmon_flag==ACTION_REPLAY_IDLE && get_long(0x880100)==2);
    }
    const auto state=save(); observe(state.data(),state.size()); std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data()); return digest;
}
static void ar1200_continuations(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart,const char *directory,bool writing,bool real)
{
    auto e=error(); std::vector<std::vector<uint8_t>> cuts; const unsigned count=real?4:3;
    const auto path=[&](unsigned cut){return std::string(directory)+"/ar1200-"+(real?"real-":"own-")+std::to_string(cut)+".state";};
    if(writing) {
        ar1200_start(rom,cart,!real); step(real?300:1); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
        ar1200_immutable(cart);
        cuts.push_back(save()); ar1200_freeze(); cuts.push_back(save());
        if(real) ar1200_ready(); else step();
        assert(hrtmon_flag==ACTION_REPLAY_ACTIVE); ar1200_immutable(cart);
        if(!real) ar1200_ram_pattern(true);
        cuts.push_back(save());
        const auto unchanged=cuts.back(); p6_hrtmon_raw h{}; assert(p6_hrtmon_get(&h)); same_state(unchanged);
        for(const auto &v:std::initializer_list<std::pair<const char *,uint32_t>>{{"hrt.size",0},{"hrt.flag",0},{"hrt.triggered",0},{"hrt.mapped",0},{"hrt.mapped",2},{"cpu.spcflags",0}}) corrupt(unchanged,v.first,v.second);
        if(real) { command("r pc 737",false); cuts.push_back(save()); }
        ar1200_stop();
    }
    for(unsigned cut=0;cut<count;++cut) {
        const auto state=writing?cuts[cut]:file(path(cut).c_str()); ar1200_start(rom,cart);
        auto foreign=state; auto id=p6_state::current_identity(); id.media_manifest_hash[0]^=1; reseal(foreign,id); const auto before=foreign;
        assert(p6_state_load(foreign.data(),foreign.size(),&e)==P6_ERROR_INCOMPATIBLE && foreign==before && !p6_session::session.started);
        load(state); same_state(state); ar1200_immutable(cart); if(!real && cut==2) ar1200_ram_pattern(false);
        assert(spans.at(fields.at("hrt.memory")).first==0x80000); const auto twin=ar1200_future(cut,real);
        load(state); same_state(state); ar1200_immutable(cart); assert(twin==ar1200_future(cut,real));
        if(writing) { FILE *f=fopen(path(cut).c_str(),"wb"); assert(f && fwrite(state.data(),1,state.size(),f)==state.size()); fclose(f); }
        printf("AR1200 continuation %s/%u ",real?"real":"own",cut); for(auto b:twin) printf("%02x",b); puts(""); ar1200_stop();
    }
    auto other=cart; other[0x20000]^=1; ar1200_start(rom,other);
    const auto state=file(path(2).c_str()); assert(p6_state_load(state.data(),state.size(),&e)==P6_ERROR_INCOMPATIBLE && !p6_session::session.started); ar1200_stop();
    if(writing) {
        ar1200_start(rom,cart); load(cuts[2]); call(p6_reset(&e),e); step(real?300:1); assert(hrtmon_flag==ACTION_REPLAY_IDLE);
        ar1200_freeze(); if(real) { ar1200_ready(); command("x"); step(8); } else { step(); put_byte(0x1100c,1); step(2); }
        assert(hrtmon_flag==ACTION_REPLAY_IDLE); ar1200_stop();
    }
    if(writing && !real) {
        for(unsigned bad:{P6_MACHINE_A500,P6_MACHINE_A1200}) {
            auto c=config(bad); if(bad==P6_MACHINE_A1200) c.flags=P6_CONFIG_HRTMON;
            call(p6_init(&c,&e),e); assert(p6_bridge::bind_ar1200({cart.data(),cart.size()},&e)==P6_ERROR_UNSUPPORTED); stop();
        }
        auto c=config(); call(p6_init(&c,&e),e); call(p6_bridge::bind_ar1200({cart.data(),cart.size()},&e),e);
        p6_blob_clear(); assert(p6_blob_register("p6-cartridge.rom",P6_BLOB_ROM,cart.data(),16)==P6_BLOB_OK);
        assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA && !armodel && !p6_bridge::hrtmon_bound()); stop();
        ar1200_start(rom,cart); step(); ar1200_freeze(); step(); const auto *bank=&get_mem_bank(0x800000);
        action_replay_cleanup(); assert(!p6_hrtmon_memory() && &get_mem_bank(0x800000)!=bank); ar1200_stop();
    }
}
int main(int argc,char **argv)
{
    const char *debug=getenv("P6_HRTMON_DEBUG_NORMAL"); normal_only=debug && !strcmp(debug,"1");
    assert(argc==5);
    if(!strcmp(argv[3],"ar1-public-write") || !strcmp(argv[3],"ar1-public-read")) {
        public_ar1_real(file(argv[1]),file(argv[2]),argv[4],!strcmp(argv[3],"ar1-public-write")); return 0;
    }
    if(!strncmp(argv[3],"ar1200-",7)) {
        const std::string mode=argv[3];
        ar1200_continuations(file(argv[1]),file(argv[2]),argv[4],mode.find("write")!=std::string::npos,mode.find("real")!=std::string::npos); return 0;
    }
    if(!strncmp(argv[3],"ar-public-",10)) {
        const std::string mode=argv[3]; const bool writing=mode.size()>=5 && mode.substr(mode.size()-5)=="write";
        if(mode.find("own")!=std::string::npos) public_ar_own(file(argv[1]),argv[4],writing);
        else public_ar_real(file(argv[1]),file(argv[2]),mode.find("real2")!=std::string::npos?2:3,argv[4],writing);
        return 0;
    }

    if(!strcmp(argv[3],"ar1-write") || !strcmp(argv[3],"ar1-read")) {
        ar1_continuations(file(argv[1]),file(argv[2]),argv[4],!strcmp(argv[3],"ar1-write")); return 0;
    }
    if(!strcmp(argv[3],"ar-owner")) { ar_owner(file(argv[1])); return 0; }
    if(!strcmp(argv[3],"ar-write") || !strcmp(argv[3],"ar-read")) {
        ar_continuations(file(argv[1]),argv[4],!strcmp(argv[3],"ar-write")); return 0;
    }
    if(!strcmp(argv[3],"commands-write") || !strcmp(argv[3],"commands-read")) {
        public_commands(file(argv[1]),argv[4],!strcmp(argv[3],"commands-write")); return 0;
    }
    if(!strcmp(argv[3],"builtin-write") || !strcmp(argv[3],"builtin-read")) {
        public_builtin(file(argv[1]),argv[4],!strcmp(argv[3],"builtin-write")); return 0;
    }
    const bool writing=!strcmp(argv[3],"write");
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
    cpu_mode=normal_only?P6_CPU_NORMAL:P6_CPU_EXACT;
    auto foreign=file(state_path(argv[4],normal_only?2:0).c_str()); cpu_mode=P6_CPU_EXACT; const auto before=save();
    const auto refused=p6_state_load(foreign.data(),foreign.size(),&e); assert(refused==P6_ERROR_INCOMPATIBLE || refused==P6_ERROR_CORRUPT_STATE);
    assert(save()==before); corrupt(before,"cpu.spcflags",SPCFLAG_ACTION_REPLAY);
    assert(!hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE); call(p6_shutdown(&e),e);
    puts("HRTMon refusal/teardown and A1200 without cartridge PASS");
}
