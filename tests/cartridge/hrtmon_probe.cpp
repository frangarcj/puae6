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
#include <array>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
static p6_error error() { return {P6_ABI_VERSION,sizeof(p6_error)}; }
static void call(p6_result r,const p6_error &e) { if(r) { fprintf(stderr,"result%d: %s\n",int(r),e.message); abort(); } }
static std::vector<uint8_t> file(const char *path)
{
    FILE *f=fopen(path,"rb"); assert(f && !fseek(f,0,SEEK_END)); const auto n=ftell(f);
    assert(n>0 && n<=1048576 && !fseek(f,0,SEEK_SET)); std::vector<uint8_t> out(size_t(n),0);
    assert(fread(out.data(),1,out.size(),f)==out.size()); fclose(f); return out;
}
static p6_config config(uint32_t machine=P6_MACHINE_A1200)
{
    p6_config c{P6_ABI_VERSION,sizeof c}; c.machine=machine; c.video_standard=P6_VIDEO_PAL;
    c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800; return c;
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
        observe(chipmem_bank.baseaddr,currprefs.chipmem.size);
    }
}
static void command(const char *text)
{
    for(;*text;++text) {
        int key=*text=='o'?AK_O:*text=='x'?AK_X:*text=='a'?AK_A:*text==' '?AK_SPC:
            *text=='0'?AK_0:*text>='1'&&*text<='9'?*text-'0':-1;
        assert(key>=0); step(3,key); step(3);
    }
    step(3,AK_RET); step(12);
}
static void state_refused()
{
    auto e=error(); uint64_t bytes=123,written=123; uint8_t storage[32]; memset(storage,0xa5,sizeof storage);
    assert(p6_state_bound(&bytes,&e)==P6_ERROR_UNSUPPORTED && bytes==123);
    assert(p6_state_save(storage,sizeof storage,&written,&e)==P6_ERROR_UNSUPPORTED && !written);
    for(auto byte:storage) assert(byte==0xa5);
    assert(p6_state_load(storage,sizeof storage,&e)==P6_ERROR_UNSUPPORTED);
    p6_state::Options internal; internal.allow_placeholders=true;
    assert(p6_state::bound(&bytes,internal,&e)==P6_ERROR_UNSUPPORTED);
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
static std::array<uint8_t,32> scenario(const std::vector<uint8_t> &rom,const std::vector<uint8_t> &cart)
{
    auto c=config(); auto e=error(); call(p6_init(&c,&e),e);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE);
    auto owned=cart; call(p6_bridge::bind_hrtmon({owned.data(),owned.size()},&e),e); owned.assign(owned.size(),0);
    assert(p6_bridge::bind_hrtmon({cart.data(),cart.size()},&e)==P6_ERROR_BAD_STATE);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    assert(!currprefs.cart_internal && !strcmp(currprefs.cartfile,"p6-cartridge.rom")); state_refused();
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE); // No CPU reset head yet.
    p6_sha256_init(&observed); step(300); assert(hrtmem_size==0x100000 && hrtmon_flag==ACTION_REPLAY_IDLE);
    assert(get_long(4)>0 && get_long(4)<currprefs.chipmem.size); // Real Kickstart's ExecBase.
    install_loop(); step(2); assert(get_long(0x11000)>0);
    call(p6_bridge::freeze_hrtmon(&e),e); assert(hrtmon_flag==ACTION_REPLAY_ACTIVATE);
    step(80); assert(hrtmon_flag==ACTION_REPLAY_ACTIVE);
    const auto stopped=get_long(0x11000); step(8); assert(get_long(0x11000)==stopped);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE);
    command("o 10000 10004 5a"); assert(get_long(0x10000)==0x5a5a5a5a && get_long(0x11000)==stopped);
    command("x"); step(8);
    assert(hrtmon_flag==ACTION_REPLAY_IDLE && !(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    assert(get_long(0x11000)>stopped && m68k_getpc()>=0x1200a && m68k_getpc()<0x12012);
    state_refused(); std::array<uint8_t,32> digest{}; p6_sha256_final(&observed,digest.data());
    call(p6_shutdown(&e),e); assert(!p6_bridge::hrtmon_bound() && !hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE);
    assert(!(regs.spcflags&SPCFLAG_ACTION_REPLAY));
    for(auto byte:ar_custom) assert(!byte); for(auto byte:ar_ciaa) assert(!byte); for(auto byte:ar_ciab) assert(!byte);
    return digest;
}
int main(int argc,char **argv)
{
    assert(argc==3); const auto rom=file(argv[1]),cart=file(argv[2]); auto e=error();
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
    const auto first=scenario(rom,cart),second=scenario(rom,cart); assert(first==second);
    printf("HRTMon Freeze/command/return twins "); for(auto byte:first) printf("%02x",byte); puts("");
    call(p6_init(&c,&e),e); call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    p6_sha256_init(&observed); step(); uint64_t size=0; call(p6_state_bound(&size,&e),e); assert(size>2097152);
    std::vector<uint8_t> state(size); uint64_t written=0; call(p6_state_save(state.data(),state.size(),&written,&e),e);
    call(p6_state_load(state.data(),written,&e),e); assert(!hrtmem_size && hrtmon_flag==ACTION_REPLAY_INACTIVE);
    assert(p6_bridge::freeze_hrtmon(&e)==P6_ERROR_BAD_STATE); call(p6_shutdown(&e),e);
    puts("HRTMon refusal/teardown and A1200 without cartridge PASS");
}
