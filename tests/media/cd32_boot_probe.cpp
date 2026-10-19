// Real stock CD32 boot gate; user ROMs/CD stay private, no game-start claim.
#include "p6.h"
#include "config.h"
#include "session.h"
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "disk.h"
#include "memory.h"
#include "machdep/maccess.h"
#include "newcpu.h"
#include "events.h"
#include "akiko.h"
#include "rom_status.h"
#include "cd_image.h"
#include "cd_device.h"
#include "akiko_controller_state.h"
#include "diagnostics.h"
#include "sha256.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <vector>
using namespace p6_cd;
static std::vector<uint8_t> file(const char *path)
{
    FILE *f=fopen(path,"rb"); assert(f && fseek(f,0,SEEK_END)==0); const auto n=ftell(f);
    assert(n>0 && n<=1048576 && fseek(f,0,SEEK_SET)==0); std::vector<uint8_t> b(size_t(n),0);
    assert(fread(b.data(),1,b.size(),f)==b.size()); fclose(f); return b;
}
static p6_error error() { return {P6_ABI_VERSION,sizeof(p6_error)}; }
static p6_config config(uint32_t machine=p6_bridge::kCd32Machine)
{
    p6_config c{}; c.abi_version=P6_ABI_VERSION; c.struct_size=sizeof c; c.machine=machine;
    c.video_standard=P6_VIDEO_PAL; c.audio_rate_hz=48000; c.random_seed=1; c.rtc_epoch_seconds=946684800; return c;
}
static void call(p6_result result, const p6_error &e)
{
    if(result!=P6_OK) { fprintf(stderr,"result%d: %s\n",int(result),e.message); abort(); }
}
struct Count { Binding original; uint64_t reads=0; };
static bool counted(const void *ctx,uint64_t at,void *out,size_t size,size_t *got)
{
    auto &c=*const_cast<Count *>(static_cast<const Count *>(ctx)); ++c.reads;
    return c.original.read(c.original.context,at,out,size,got);
}
static void state_caps()
{
    uint64_t size=0; auto e=error(); call(p6_state_bound(&size,&e),e); assert(size>2097152);
    p6_caps caps{P6_ABI_VERSION,sizeof caps}; call(p6_query_caps(&caps,&e),e); assert(caps.flags&P6_CAP_STATE);
}
#ifndef P6_CD32_BOOT_FIXTURE_ONLY
int main(int argc,char **argv)
{
    assert(argc==5); auto mainrom=file(argv[1]),extrom=file(argv[2]),a1200=file(argv[4]);
    assert(mainrom.size()==524288 && extrom.size()==524288);
    std::vector<uint8_t> rom=extrom; rom.insert(rom.end(),mainrom.begin(),mainrom.end());
    auto c=config(); auto e=error();
    for(unsigned n=0;n<8;++n) {
        auto bad=c;
        switch(n) { case 0:bad.cpu_model=2;break;case 1:bad.cpu_compatibility=P6_CPU_NORMAL;break;
        case 2:bad.cpu_multiplier=8;break;case 3:bad.fast_kib=8192;break;case 4:bad.floppy_drives=1;break;
        case 5:bad.flags=P6_CONFIG_FLOPPY_WRITES;break;case 6:bad.chip_kib=1024;break;case 7:bad.hdf_controller=P6_HDF_CONTROLLER_IDE;break; }
        assert(p6_init(&bad,&e)==P6_ERROR_UNSUPPORTED);
    }
    call(p6_init(&c,&e),e);
    static const uint8_t readonly_bad[1048576]{};
    assert(p6_load({readonly_bad,sizeof readonly_bad},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA);
    for(unsigned n=0;n<5;++n) {
        auto bad=rom;
        if(n==0) bad.resize(524288);
        if(n==1) std::rotate(bad.begin(),bad.begin()+524288,bad.end());
        if(n==2) bad[0]^=1;
        if(n==3) bad[524288]^=1;
        if(n==4) bad.pop_back();
        auto *old=kickmem_bank.baseaddr; const auto cycle=get_cycles(); const auto model=currprefs.cpu_model;
        assert(p6_load({bad.data(),bad.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA);
        assert(kickmem_bank.baseaddr==old && get_cycles()==cycle && currprefs.cpu_model==model);
    }
    p6_media forbidden{}; assert(p6_load({rom.data(),rom.size()},&forbidden,1,&e)==P6_ERROR_UNSUPPORTED);
    p6_libretro::CdImage image; assert(image.open(argv[3])==Result::Ok);
    std::vector<Count> counters(image.sources()); std::vector<Binding> bindings(image.sources());
    for(unsigned n=0;n<image.sources();++n) { counters[n].original=image.bindings()[n]; bindings[n]={image.bindings()[n].identity,counted,&counters[n]}; }
    Disc disc; assert(disc.bind(*image.layout(),bindings.data(),image.sources())==Result::Ok && device_bind(&disc)==Result::Ok);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e);
    assert(currprefs.cpu_model==68020 && currprefs.address_space_24 && currprefs.cpu_cycle_exact && currprefs.cpu_clock_multiplier==1024);
    assert(currprefs.chipmem.size==2097152 && !currprefs.bogomem.size && !currprefs.fastmem[0].size && !currprefs.nr_floppies);
    for(auto &drive:currprefs.floppyslots) assert(drive.dfxtype==DRV_NONE);
    assert(currprefs.cs_compatible==CP_CD32 && currprefs.cs_cd32cd && currprefs.cs_cd32c2p && currprefs.cs_cd32nvram && currprefs.cs_cd32nvram_size==1024);
    assert(!currprefs.cs_ciaoverlay && !currprefs.cs_ide && !currprefs.cs_pcmcia && !currprefs.cs_cd32fmv && !currprefs.cs_ksmirror_e0 && currprefs.cs_ksmirror_a8);
    auto field=[](const char *name,uint64_t value) { printf("pref %s %llu\n",name,(unsigned long long)value); };
    field("cpu_model",currprefs.cpu_model);
    field("address_space_24",currprefs.address_space_24);
    field("cpu_cycle_exact",currprefs.cpu_cycle_exact);
    field("cpu_clock_multiplier",currprefs.cpu_clock_multiplier);
    field("chip_kib",currprefs.chipmem.size/1024);
    field("slow_kib",currprefs.bogomem.size/1024);
    field("fast_kib",currprefs.fastmem[0].size/1024);
    field("nr_floppies",currprefs.nr_floppies);
    field("cs_cd32cd",currprefs.cs_cd32cd);
    field("cs_cd32c2p",currprefs.cs_cd32c2p);
    field("cs_cd32nvram",currprefs.cs_cd32nvram);
    field("cs_cd32nvram_size",currprefs.cs_cd32nvram_size);
    field("cs_ciaoverlay",currprefs.cs_ciaoverlay);
    field("cs_ide",currprefs.cs_ide);
    field("cs_pcmcia",currprefs.cs_pcmcia);
    field("cs_cd32fmv",currprefs.cs_cd32fmv);
    field("cs_ksmirror_e0",currprefs.cs_ksmirror_e0);
    field("cs_ksmirror_a8",currprefs.cs_ksmirror_a8);
    assert(kickmem_bank.reserved_size==524288 && extendedkickmem_bank.reserved_size==524288);
    assert(!memcmp(kickmem_bank.baseaddr,mainrom.data(),524288) && !memcmp(extendedkickmem_bank.baseaddr,extrom.data(),524288));
    assert(get_long(AKIKO_BASE)==0xc0cacafe && get_long(0xe00000)==do_get_mem_long(extrom.data()) && get_long(0xf80000)==do_get_mem_long(mainrom.data()));
    uint8_t hash[32]; p6_sha256_hash(rom.data(),rom.size(),hash); assert(!memcmp(hash,p6_session::session.firmware_sha256,32)); state_caps();
    p6_input input{P6_ABI_VERSION,sizeof input}; p6_frame frame{P6_ABI_VERSION,sizeof frame}; uint64_t reads=0; unsigned frames=0;
    do {
        call(p6_run_frame(&input,P6_RENDER_VIDEO|P6_RENDER_AUDIO,&frame,&e),e);
        assert(frame.master_ticks && frame.audio_frames && frame.video_bytes); ++frames;
        reads=0; for(const auto &counter:counters) reads+=counter.reads;
    } while(frames<240 && (frames<30 || !reads));
    if (!reads) {
        p6_akiko_controller_state control{};
        const bool captured = p6_akiko_controller_get(&control);
        fprintf(stderr,"CD32 no payload: PC=%08x cycles=%llu open=%d controller=%d init=%d unit=%d flags=%08x command=%02x rx=%d/%d toc=%d media=%d/%d\n",
            m68k_getpc(),(unsigned long long)get_cycles(),sys_command_isopen(0),captured,control.cd_initialized,control.unitnum,
            control.cdrom_flags,control.cdrom_command,control.cdrom_receive_offset,control.cdrom_receive_length,
            control.toc.points,control.lastmediastate,control.mediachanged);
        p6_diagnostic diagnostic[32]; const auto count=p6_diag_read(diagnostic,32);
        for(int n=0;n<count;++n) fprintf(stderr,"CD32 diagnostic: %s\n",diagnostic[n].text);
    }
    state_caps(); assert(reads); printf("CD32 firmware executed frames=%u payload_reads=%llu; game boot not asserted\n",frames,(unsigned long long)reads);
    call(p6_reset(&e),e); call(p6_run_frame(&input,P6_RENDER_AUDIO,&frame,&e),e); call(p6_unload(&e),e); device_unbind(); disc=Disc{}; call(p6_shutdown(&e),e);
    c=config(P6_MACHINE_A1200); call(p6_init(&c,&e),e); assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA);
    call(p6_load({a1200.data(),a1200.size()},nullptr,0,&e),e); call(p6_run_frame(&input,P6_RENDER_AUDIO,&frame,&e),e);
    p6_caps caps{P6_ABI_VERSION,sizeof caps}; call(p6_query_caps(&caps,&e),e); assert(caps.flags&P6_CAP_STATE);
    assert(currprefs.cpu_model==68020 && !currprefs.cs_cd32cd && frame.master_ticks && frame.audio_frames); call(p6_shutdown(&e),e);
    puts("stock profile, firmware refusals/state caps and A1200 reload PASS");
}
#endif
