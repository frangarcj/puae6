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
#include "custom.h"
#include "p6_state_access.h"
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
// Failure diagnostics read owned RAM/ROM directly: no MMIO or guest callbacks.
static const uint8_t *diagnostic_bytes(uint32_t at, uint32_t size, bool rom=false)
{
    auto mapped=[&](addrbank &bank,uint32_t base) -> const uint8_t * {
        if(!bank.baseaddr || at<base || at-base>bank.allocated_size || size>bank.allocated_size-(at-base)) return nullptr;
        if(!size || get_mem_bank(at).baseaddr!=bank.baseaddr || get_mem_bank(at+size-1).baseaddr!=bank.baseaddr) return nullptr;
        return bank.baseaddr+(at-base);
    };
    if(auto p=mapped(chipmem_bank,0)) return p;
    if(rom) {
        if(auto p=mapped(kickmem_bank,0xf80000)) return p;
        if(auto p=mapped(extendedkickmem_bank,0xe00000)) return p;
    }
    return nullptr;
}
static bool diagnostic_name(uint32_t at,char (&name)[40])
{
    for(unsigned i=0;i<sizeof name;++i) {
        const auto p=diagnostic_bytes(at+i,1,true);
        if(!p || *p<0x20 || *p>0x7e) { name[i]=0; return p && !*p; }
        name[i]=char(*p);
    }
    name[sizeof name-1]=0; return false;
}
static const uint8_t *diagnostic_boot_context()
{
    const auto pc=m68k_getpc();
    const auto s=diagnostic_bytes(m68k_areg(regs,7),96);
    // Exact pinned-ROM call chain and level3 frame: no stack scanning/guessing.
    return s && pc>=0xfbfce2 && pc<0xfbfd52 && do_get_mem_long(s)==0xfc22e4 &&
        do_get_mem_long(s+28)==0xfc2514 && do_get_mem_long(s+44)==0xf817ce &&
        do_get_mem_word(s+94)==0x006c?s:nullptr;
}
static void diagnostic_irq(const p6_akiko_controller_state &control)
{
    const auto pc=m68k_getpc(), sp=m68k_areg(regs,7), a6=m68k_areg(regs,6);
    const auto stack=diagnostic_bytes(sp,96), resource=diagnostic_bytes(a6,128);
    // This pinned ROM's public SetICR has no stack adjustment before RTS.
    const bool seticr=pc>=0xfbfce2 && pc<0xfbfd52;
    fprintf(stderr,"CD32 CPU s=%u m=%u mask=%d ipl=%d D0=%08x D1=%08x A6=%08x A7=%08x seticr_return_valid=%d return=%08x resource_valid=%d cia_base=%08x exec_base=%08x INTREQ=%04x INTENA=%04x AkikoIRQ=%08x/%08x\n",
        unsigned(regs.s),unsigned(regs.m),regs.intmask,regs.ipl_pin,m68k_dreg(regs,0),m68k_dreg(regs,1),a6,sp,
        seticr && stack,stack && seticr?do_get_mem_long(stack):0,resource!=nullptr,
        resource?do_get_mem_long(resource+0x22):0,resource?do_get_mem_long(resource+0x7c):0,
        intreq,intena,control.cdrom_intreq,control.cdrom_intena);
    if(stack) {
        fprintf(stderr,"CD32 active stack words (not a backtrace):");
        for(unsigned n=0;n<96;n+=4) fprintf(stderr," %08x",do_get_mem_long(stack+n));
        fprintf(stderr,"\n");
    }
    extern void p6_cia_get(int,p6_cia_raw *);
    for(int n=0;n<2;++n) {
        p6_cia_raw cia{}; p6_cia_get(n,&cia); // Pure accessor, never reads MMIO ICR.
        fprintf(stderr,"CD32 CIA%c icr1=%02x icr2=%02x change=%02x mask=%02x TA=%04x/%04x cr=%02x TB=%04x/%04x cr=%02x\n",
            n?'B':'A',cia.icr1,cia.icr2,cia.icr_change,cia.imask,cia.t[0].timer,cia.t[0].latch,cia.t[0].cr,
            cia.t[1].timer,cia.t[1].latch,cia.t[1].cr);
    }
}
static void diagnostic_tasks(uint32_t base,const uint8_t *exec)
{
    auto task=[](const char *list,uint32_t node,bool active) {
        const auto p=node && !(node&1)?diagnostic_bytes(node,66):nullptr;
        if(!p || (p[8]!=1 && p[8]!=13)) return;
        char name[40]{}; const bool named=diagnostic_name(do_get_mem_long(p+10),name);
        const auto sp=active?m68k_areg(regs,7):do_get_mem_long(p+54);
        const auto lower=do_get_mem_long(p+58),upper=do_get_mem_long(p+62);
        const auto saved=!active && sp>=lower && sp<=upper && upper-sp>=78?diagnostic_bytes(sp+74,4):nullptr;
        // +74 follows the upstream debugger, but is only a candidate word on
        // this 020: e.g. cd.device keeps its library base there, not a PC.
        fprintf(stderr,"CD32 task %s base=%08x name=%s state=%u wait=%08x received=%08x sp=%08x stack=%08x..%08x stack_word74=%08x readable=%d\n",
            list,node,named?name:"<invalid>",p[15],do_get_mem_long(p+22),do_get_mem_long(p+26),sp,lower,upper,
            saved?do_get_mem_long(saved):0,saved!=nullptr);
    };
    task("current",do_get_mem_long(exec+276),true);
    for(unsigned offset:{406u,420u}) {
        uint32_t node=do_get_mem_long(exec+offset),seen[64]{}; unsigned count=0;
        while(node!=base+offset+4 && count<64) {
            const auto p=node && !(node&1)?diagnostic_bytes(node,4):nullptr;
            if(!p || std::find(seen,seen+count,node)!=seen+count) break;
            seen[count++]=node; task(offset==406?"ready":"waiting",node,false); node=do_get_mem_long(p);
        }
    }
}
static void diagnostic_device()
{
    const auto zero=diagnostic_bytes(4,4);
    const uint32_t base=zero?do_get_mem_long(zero):0;
    const auto exec=base && !(base&1)?diagnostic_bytes(base,434):nullptr;
    if(!exec) { fprintf(stderr,"CD32 ExecBase unavailable: %08x\n",base); return; }
    diagnostic_tasks(base,exec);
    const uint32_t list=base+350, sentinel=list+4;
    uint32_t node=do_get_mem_long(exec+350), seen[64]{}; unsigned count=0;
    while(node!=sentinel && count<64) {
        const auto p=!(node&1)?diagnostic_bytes(node,34):nullptr;
        if(!p || !node || std::find(seen,seen+count,node)!=seen+count) break;
        seen[count++]=node;
        char name[40]{};
        if(diagnostic_name(do_get_mem_long(p+10),name) && !strcmp(name,"cd.device")) {
            const auto size=do_get_mem_word(p+18);
            const auto device=p[8]==3 && size>=0x230?diagnostic_bytes(node,size):nullptr;
            fprintf(stderr,"CD32 cd.device base=%08x type=%u size=%u version=%u.%u open=%u valid=%d\n",
                node,p[8],size,do_get_mem_word(p+20),do_get_mem_word(p+22),do_get_mem_word(p+32),device!=nullptr);
            if(device) fprintf(stderr,"CD32 firmware Status=%04x track1=%02x toc_phase=%02x fatal=%02x table=%02x%02x%02x%02x%02x%02x%02x%02x\n",
                do_get_mem_word(device+0x4c),device[0x22e],device[0x133],device[0x135],device[0x228],device[0x229],
                device[0x22a],device[0x22b],device[0x22c],device[0x22d],device[0x22e],device[0x22f]);
            return;
        }
        node=do_get_mem_long(p);
    }
    fprintf(stderr,"CD32 cd.device absent/invalid: ExecBase=%08x nodes=%u next=%08x tail=%08x\n",base,count,node,sentinel);
}
int main(int argc,char **argv)
{
    assert(argc==5); auto mainrom=file(argv[1]),extrom=file(argv[2]),a1200=file(argv[4]);
    assert(mainrom.size()==524288 && extrom.size()==524288);
    std::vector<uint8_t> rom=extrom; rom.insert(rom.end(),mainrom.begin(),mainrom.end());
    auto c=config(); auto e=error();
    for(unsigned n=0;n<8;++n) {
        auto bad=c;
        switch(n) { case 0:bad.cpu_model=2;break;case 1:bad.cpu_compatibility=P6_CPU_NORMAL;break;
        case 2:bad.cpu_multiplier=8;break;case 3:bad.fast_kib=4096;break;case 4:bad.floppy_drives=1;break;
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
    // The cold ROM reads all1024 EEPROM bytes over I2C (E62238 -> E61980).
    // Keep the reset window plus one PAL unit per byte; a240-unit cut was mid-transfer.
    constexpr unsigned boot_limit=240+1024;
    struct ClockSample { unsigned frame; uint32_t pc,sp,tb,icr,a2,timehi,timelo,bootpc,a1,d6; } samples[6]{}; unsigned nsamples=0;
    do {
        call(p6_run_frame(&input,P6_RENDER_VIDEO|P6_RENDER_AUDIO,&frame,&e),e);
        assert(frame.master_ticks && frame.audio_frames && frame.video_bytes); ++frames;
        reads=0; for(const auto &counter:counters) reads+=counter.reads;
        if(frames==30 || frames==120 || frames==240 || frames==512 || frames==1024 || frames==boot_limit) {
            extern void p6_cia_get(int,p6_cia_raw *);
            p6_cia_raw cia{}; p6_cia_get(0,&cia);
            const auto a2=m68k_areg(regs,2); const auto timer=diagnostic_bytes(a2,0x50);
            const auto context=diagnostic_boot_context();
            samples[nsamples++]={frames,m68k_getpc(),m68k_areg(regs,7),cia.t[1].timer,cia.icr1,a2,
                timer?do_get_mem_long(timer+0x48):0,timer?do_get_mem_long(timer+0x4c):0,
                context?do_get_mem_long(context+90):0,context?do_get_mem_long(context+76):0,m68k_dreg(regs,6)};
        }
    } while(frames<boot_limit && (frames<30 || !reads));
    if (!reads) {
        for(unsigned n=0;n<nsamples;++n) {
            const auto &s=samples[n]; fprintf(stderr,"CD32 clock frame=%u PC=%08x SP=%08x TB=%04x icr=%02x A2=%08x words48=%08x/%08x bootPC=%08x savedA1=%08x liveD6=%08x\n",
                s.frame,s.pc,s.sp,s.tb,s.icr,s.a2,s.timehi,s.timelo,s.bootpc,s.a1,s.d6);
        }
        p6_akiko_controller_state control{};
        const bool captured = p6_akiko_controller_get(&control);
        fprintf(stderr,"CD32 no payload: PC=%08x cycles=%llu open=%d controller=%d init=%d unit=%d flags=%08x command=%02x rx=%d/%d toc=%d media=%d/%d\n",
            m68k_getpc(),(unsigned long long)get_cycles(),sys_command_isopen(0),captured,control.cd_initialized,control.unitnum,
            control.cdrom_flags,control.cdrom_command,control.cdrom_receive_offset,control.cdrom_receive_length,
            control.toc.points,control.lastmediastate,control.mediachanged);
        const auto &track=disc.layout().track[0];
        fprintf(stderr,"CD32 source track1 mode=%u control=%u adr=%u index1=%u\n",unsigned(track.mode),track.control,track.adr,track.index1);
        const int index=control.toc.first_track_offset;
        if(captured && index>=0 && index<103) fprintf(stderr,"CD32 controller track1 slot=%d point=%u control=%u adr=%u\n",
            index,control.toc.entry[index].point,control.toc.entry[index].control,control.toc.entry[index].adr);
        diagnostic_irq(control); diagnostic_device();
        p6_diagnostic diagnostic[32]; const auto count=p6_diag_read(diagnostic,32);
        for(int n=0;n<count;++n) fprintf(stderr,"CD32 diagnostic: %s\n",diagnostic[n].text);
    }
    state_caps(); assert(reads); printf("CD32 firmware executed frames=%u payload_reads=%llu; game boot not asserted\n",frames,(unsigned long long)reads);
    const auto old_cycles=get_cycles();
    call(p6_reset(&e),e); call(p6_run_frame(&input,P6_RENDER_AUDIO,&frame,&e),e);
    assert(get_cycles()<old_cycles); // Full-machine reset must also restart the registered Akiko clock.
    call(p6_unload(&e),e); device_unbind(); disc=Disc{}; call(p6_shutdown(&e),e);
    c=config(P6_MACHINE_A1200); call(p6_init(&c,&e),e); assert(p6_load({rom.data(),rom.size()},nullptr,0,&e)==P6_ERROR_WRONG_MEDIA);
    call(p6_load({a1200.data(),a1200.size()},nullptr,0,&e),e); call(p6_run_frame(&input,P6_RENDER_AUDIO,&frame,&e),e);
    p6_caps caps{P6_ABI_VERSION,sizeof caps}; call(p6_query_caps(&caps,&e),e); assert(caps.flags&P6_CAP_STATE);
    assert(currprefs.cpu_model==68020 && !currprefs.cs_cd32cd && frame.master_ticks && frame.audio_frames); call(p6_shutdown(&e),e);
    puts("stock profile, firmware refusals/state caps and A1200 reload PASS");
}
#endif
