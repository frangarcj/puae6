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
static void ar_states_refused()
{
    auto e=error(); uint64_t size=UINT64_C(0x123456789abcdef0),written=99;
    uint8_t bytes[32],before[32]; memset(bytes,0xa5,sizeof bytes); memcpy(before,bytes,sizeof bytes);
    const auto ticks=p6_session::session.master_ticks; const auto flag=regs.spcflags;
    assert(p6_state_bound(&size,&e)==P6_ERROR_UNSUPPORTED && size==UINT64_C(0x123456789abcdef0));
    assert(p6_state_save(bytes,sizeof bytes,&written,&e)==P6_ERROR_UNSUPPORTED && !written);
    assert(p6_state_load(bytes,sizeof bytes,&e)==P6_ERROR_UNSUPPORTED && !memcmp(bytes,before,sizeof bytes));
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
        assert(p6_bridge::bind_action_replay(1,{image.data(),count},&e)==P6_ERROR_BAD_ARGUMENT);
        for(size_t bytes:{size_t(0),count-1,count+1})
            assert(p6_bridge::bind_action_replay(model,{image.data(),bytes},&e)==P6_ERROR_WRONG_MEDIA && !p6_bridge::ar_bound());
        assert(p6_bridge::bind_action_replay(model,{nullptr,count},&e)==P6_ERROR_WRONG_MEDIA);
        auto input=cart; call(p6_bridge::bind_action_replay(model,{input.data(),count},&e),e); input.assign(input.size(),0);
        assert(p6_bridge::ar_bound() && !p6_bridge::hrtmon_bound()); ar_states_refused();
        assert(p6_bridge::bind_action_replay(model,{image.data(),count},&e)==P6_ERROR_BAD_STATE);
        assert(p6_bridge::bind_hrtmon({nullptr,0},&e)==P6_ERROR_BAD_STATE);
        uint8_t copied[16]; size_t got=0; assert(p6_blob_pread(id,8,copied,sizeof copied,&got)==P6_BLOB_OK && got==sizeof copied);
        assert(!memcmp(copied,cart.data()+8,sizeof copied));
        call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
        assert(armodel==int(model) && !currprefs.cart_internal && !strcmp(currprefs.cartfile,id)); ar_states_refused();
        action_replay_reset(true,false); // Existing hardware reset maps the actual ROM/RAM banks; no cart execution.
        assert(!strcmp(get_mem_bank(0x400000).name,"Action Replay ROM") && !strcmp(get_mem_bank(0x440000).name,"Action Replay RAM"));
        for(uint32_t at=8;at<count;at+=254) assert(get_byte(0x400000+at)==cart[at]);
        assert(get_byte(0x400000+count-1)==cart[count-1] && get_long(0x440010)==0);
        put_long(0x440010,0x12345678); put_word(0x44fffe,0xabcd);
        assert(get_long(0x440010)==0x12345678 && get_word(0x44fffe)==0xabcd);
        put_byte(0x400100,0); assert(get_byte(0x400100)==cart[0x100]);
        put_byte(0x400001,2); assert(regs.spcflags&SPCFLAG_ACTION_REPLAY); ar_states_refused();
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
    puts("AR2/3 copied owner/banks/refusals/cleanup PASS; activation and codec not asserted");
}
int main(int argc,char **argv)
{
    const char *debug=getenv("P6_HRTMON_DEBUG_NORMAL"); normal_only=debug && !strcmp(debug,"1");
    assert(argc==5);
    if(!strcmp(argv[3],"ar-owner")) { ar_owner(file(argv[1])); return 0; }
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
