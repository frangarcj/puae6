// Full native CD32 machine, synthetic immutable audio CD; private ROMs stay local.
#define P6_CD32_BOOT_FIXTURE_ONLY
#include "cd32_boot_probe.cpp"
#include "state.h"
#include "akiko_state.h"
#include "cd32_nvram.h"
#include "p6_state_access.h"
#include "audio.h"
#include "cd_audio.h"
#include "cd_audio_state.h"
#include "cd_nrg.h"
#include "cd_akiko.h"
#include "cd_read.h"
#include <string>
#include <memory>
#include <algorithm>
static_assert(kDeviceMaxDiscs == P6_MAX_MEDIA, "CD catalog and frontend media capacity");
struct DiscFixture {
    uint64_t calls=0;
    uint8_t tag;
    Disc disc;
    static bool read(const void *opaque,uint64_t at,void *out,size_t n,size_t *got) {
        auto &self=*const_cast<DiscFixture *>(static_cast<const DiscFixture *>(opaque)); ++self.calls;
        static const uint8_t pcm[4]={0xd2,0x04,0xd7,0xf6}; // LE +1234/-2345.
        if(at>20*2352 || n>20*2352-at) return false;
        for(size_t i=0;i<n;++i) static_cast<uint8_t *>(out)[i]=pcm[(at+i)%4]^self.tag;
        *got=n; return true;
    }
    explicit DiscFixture(uint8_t id=42) : tag(uint8_t(id^42)) {
        Layout layout{}; layout.tracks=1; layout.leadout=20; layout.content_id[0]=id;
        layout.track[0]={0,0,20,0,2352,0,Mode::Audio,1,0,false};
        Binding binding{{{id},20*2352,1},read,this}; assert(disc.bind(layout,&binding,1)==Result::Ok);
    }
};
static p6_input input{P6_ABI_VERSION,sizeof input};
static p6_frame step()
{
    auto e=error(); p6_frame frame{P6_ABI_VERSION,sizeof frame};
    call(p6_run_frame(&input,P6_RENDER_VIDEO|P6_RENDER_AUDIO,&frame,&e),e); return frame;
}
static void pio_command(std::initializer_list<uint8_t> bytes)
{
    p6_akiko_controller_state s{}; assert(p6_akiko_controller_get(&s));
    assert(!s.cdrom_command_active && !s.cdrom_command_length);
    for(int n=s.cdrom_receive_offset;n<s.cdrom_receive_length;++n) get_byte(AKIKO_BASE+0x28);
    uint8_t sum=0xff;
    for(auto byte:bytes) { put_byte(AKIKO_BASE+0x28,byte); sum-=byte; }
    put_byte(AKIKO_BASE+0x28,sum); step();
}
static std::vector<uint8_t> save()
{
    auto e=error(); uint64_t bound=0,written=0; call(p6_state_bound(&bound,&e),e);
    std::vector<uint8_t> out(bound); call(p6_state_save(out.data(),out.size(),&written,&e),e);
    assert(written<=bound); out.resize(written); return out;
}
static void load(const std::vector<uint8_t> &state)
{
    auto e=error(); call(p6_state_load(state.data(),state.size(),&e),e);
}
template<class Fixture>
static void start(const std::vector<uint8_t> &rom,Fixture &disc,bool ntsc,bool fast=false,Fixture *other=nullptr)
{
    auto c=config(); c.video_standard=ntsc?P6_VIDEO_NTSC:P6_VIDEO_PAL; auto e=error();
    c.fast_kib=fast?8192:0;
    call(p6_init(&c,&e),e);
    const Disc *catalog[]={&disc.disc,other?&other->disc:nullptr};
    assert(device_catalog_bind(catalog,other?2:1)==Result::Ok);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); state_caps();
    p6_memory_region regions[8]; uint32_t count=0;
    call(p6_memory_regions(regions,8,&count,&e),e);
    const auto found=std::find_if(regions,regions+count,[](const auto &r){ return r.type==P6_REGION_NVRAM; });
    assert(found!=regions+count && found->bytes==1024 && (found->flags&P6_REGION_WRITABLE));
    assert(found->data==p6_akiko_nvram_data()); // Backing already exists before the first unit.
    auto *bytes=static_cast<uint8_t *>(const_cast<void *>(found->data)); bytes[13]=0x41;
    p6_cd32_nvram_state nv{}; assert(p6_akiko_nvram_get(&nv) && nv.chip.memory[13]==0x41);
}
static void stop()
{
    auto e=error(); call(p6_shutdown(&e),e); device_unbind();
}
static void map_matches()
{
    p6_cd32_nvram_state nv{}; assert(p6_akiko_nvram_get(&nv));
    extern void p6_cia_misc_get(p6_cia_misc_raw *);
    p6_cia_misc_raw cia{}; p6_cia_misc_get(&cia);
    const auto *bank=cia.oldovl?kickmem_bank.baseaddr:chipmem_bank.baseaddr;
    assert(get_long(0)==do_get_mem_long(bank));
}
template<class Fixture>
static std::vector<uint8_t> future(Fixture &disc)
{
    p6_sha256 hash; p6_sha256_init(&hash); const auto before=disc.calls;
    for(int n=0;n<5;++n) {
        const auto f=step();
        p6_sha256_update(&hash,&f.unit_index,sizeof f.unit_index);
        p6_sha256_update(&hash,&f.master_ticks,sizeof f.master_ticks);
        p6_sha256_update(&hash,&f.audio_frames,sizeof f.audio_frames);
        p6_sha256_update(&hash,f.audio,size_t(f.audio_frames)*4);
        p6_sha256_update(&hash,f.video,size_t(f.video_bytes)); map_matches();
    }
    uint8_t digest[32]; p6_sha256_final(&hash,digest);
    std::vector<uint8_t> out(digest,digest+32); const auto reads=disc.calls-before;
    for(unsigned n=0;n<8;++n) out.push_back(uint8_t(reads>>(8*n)));
    return out;
}
static uint64_t le64(const uint8_t *p) { uint64_t v=0; for(int n=7;n>=0;--n) v=(v<<8)|p[n]; return v; }
static void reseal(std::vector<uint8_t> &s)
{
    p6_state::seal(s.data(),s.size()-p6_state::kHeaderSize,12,p6_state::current_identity(),le64(s.data()+24),le64(s.data()+32));
}
template<class Fixture>
static void reject(const std::vector<uint8_t> &bad,p6_result result,Fixture &disc)
{
    const auto before=save(); const auto cycle=get_cycles(); const auto calls=disc.calls; auto e=error();
    assert(p6_state_load(bad.data(),bad.size(),&e)==result);
    assert(save()==before && get_cycles()==cycle && disc.calls==calls); map_matches();
}
static void change_cd(uint32_t selected)
{
    const auto opens=sys_command_isopen(0);
    assert(p6_akiko_media_set(false) && device_select(selected)==Result::Ok && p6_akiko_media_set(true));
    assert(sys_command_isopen(0)==opens);
}
static void catalog_state(const std::vector<uint8_t> &rom)
{
    DiscFixture a,b(43),fresh_a,fresh_b(43),alien(44);
    start(rom,a,false,false,&b); step();
    assert(sys_command_cd_play(0,0,20,0,nullptr,nullptr));
    const auto cut_a=save(), expected_a=future(a),end_a=save();
    change_cd(1); assert(device_bound_disc()==&b.disc);
    uint8_t raw[2352]; assert(sys_command_cd_rawread(0,raw,0,1,2352)==2352 && raw[0]==0xd3);
    assert(sys_command_cd_play(0,0,20,0,nullptr,nullptr)); step();
    assert(p6_akiko_media_set(false)); const auto cut_empty_b=save();
    assert(!memcmp(cut_a.data()+208,cut_empty_b.data()+208,32)); // Selection is not media identity.
    change_cd(0);
    const auto a_calls=a.calls,b_calls=b.calls;
    load(cut_a); assert(a.calls==a_calls && b.calls==b_calls && save()==cut_a);
    assert(device_bound_disc()==&a.disc && device_catalog_disc(1)==&b.disc);
    assert(future(a)==expected_a && save()==end_a);
    load(cut_empty_b); assert(save()==cut_empty_b && device_bound_disc()==&b.disc && !device_inserted());
    assert(device_catalog_size()==2 && sys_command_isopen(0)==1);
    assert(p6_akiko_media_set(true) && sys_command_cd_play(0,0,20,0,nullptr,nullptr));
    const auto expected_b=future(b),end_b=save(); stop();
    start(rom,fresh_a,false,false,&fresh_b); step(); change_cd(1); step();
    const auto fresh_calls=fresh_a.calls+fresh_b.calls;
    load(cut_a); assert(save()==cut_a && fresh_a.calls+fresh_b.calls==fresh_calls);
    assert(device_bound_disc()==&fresh_a.disc && future(fresh_a)==expected_a && save()==end_a);
    load(cut_empty_b); assert(save()==cut_empty_b && device_bound_disc()==&fresh_b.disc && !device_inserted());
    assert(p6_akiko_media_set(true) && sys_command_cd_play(0,0,20,0,nullptr,nullptr));
    assert(future(fresh_b)==expected_b && save()==end_b); stop();
    start(rom,a,false,false,&alien); step(); const auto alien_calls=alien.calls;
    reject(cut_a,P6_ERROR_INCOMPATIBLE,a); assert(alien.calls==alien_calls); stop();
    start(rom,b,false,false,&a); step(); const auto other_calls=a.calls;
    reject(cut_a,P6_ERROR_INCOMPATIBLE,b); assert(a.calls==other_calls); stop();
    start(rom,a,false); step(); reject(cut_a,P6_ERROR_INCOMPATIBLE,a); stop();
    printf("native CD catalogue A/B empty/new owner/exact future/identity PASS\n");
}
// The owner and all counted callbacks outlive the machine; no shared context from another owner.
struct NrgFixture {
    p6_libretro::CdNrg owner;
    Disc disc;
    uint64_t calls=0;
    struct Reader { NrgFixture *self; Binding original; } reader[kMaxSources]{};
    static bool read(const void *opaque,uint64_t at,void *out,size_t n,size_t *got) {
        const auto &r=*static_cast<const Reader *>(opaque); ++r.self->calls;
        return r.original.read(r.original.context,at,out,n,got);
    }
    explicit NrgFixture(const char *path) {
        assert(owner.open(path,nullptr,p6_libretro::NrgSessionPolicy::ExplicitPrivate)==Result::Ok);
        Binding bindings[kMaxSources];
        for(unsigned n=0;n<owner.sources();++n) {
            reader[n]={this,owner.bindings()[n]}; bindings[n]={reader[n].original.identity,read,&reader[n]};
        }
        assert(disc.bind(*owner.layout(),bindings,owner.sources())==Result::Ok);
        assert(disc.layout().sessions==2 && disc.layout().tracks==3);
    }
};
static void cold_reject(const std::vector<uint8_t> &bad,NrgFixture &disc)
{
    auto cold_save=[] {
        uint8_t buffer[64]; memset(buffer,0xa5,sizeof buffer); uint64_t written=123; auto e=error();
        assert(!p6_session::session.started);
        assert(p6_state_save(buffer,sizeof buffer,&written,&e)==P6_ERROR_BAD_STATE && !written);
        assert(std::all_of(buffer,buffer+sizeof buffer,[](uint8_t b){ return b==0xa5; }));
    };
    auto witness=[&] {
        std::vector<uint64_t> out; auto add=[&](uint64_t value){ out.push_back(value); };
        add(get_cycles()); add(disc.calls); add(p6_session::session.started);
        add(p6_session::session.unit_index); add(p6_session::session.master_ticks);
        for(auto value:regs.regs) add(value);
        add(regs.pc); add(reinterpret_cast<uintptr_t>(regs.pc_p)); add(reinterpret_cast<uintptr_t>(regs.pc_oldp));
        add(regs.instruction_pc); add(regs.ir); add(regs.irc); add(regs.sr); add(regs.spcflags);
        add(regs.s); add(regs.m); add(regs.stopped); add(regs.halted); add(regs.exception); add(regs.intmask);
        add(regs.ipl[0]); add(regs.ipl[1]); add(regs.ipl_pin); add(regs.ipl_pin_p);
        add(regs.ipl_pin_change_evt); add(regs.ipl_pin_change_evt_p); add(regs.ipl_evt); add(regs.ipl_evt_pre);
        add(reinterpret_cast<uintptr_t>(chipmem_bank.baseaddr)); add(chipmem_bank.allocated_size);
        if(chipmem_bank.baseaddr && chipmem_bank.allocated_size) {
            uint8_t digest[32]; p6_sha256_hash(chipmem_bank.baseaddr,chipmem_bank.allocated_size,digest);
            for(auto byte:digest) add(byte);
        }
        add(reinterpret_cast<uintptr_t>(device_bound_disc())); add(device_open_count()); add(device_inserted());
        return out;
    };
    cold_save(); const auto before=witness(); auto e=error();
    assert(p6_state_load(bad.data(),bad.size(),&e)==P6_ERROR_INCOMPATIBLE);
    assert(witness()==before); cold_save();
}
static std::vector<uint8_t> state_file(const std::string &path,const std::vector<uint8_t> *data=nullptr)
{
    FILE *f=fopen(path.c_str(),data?"wb":"rb"); assert(f);
    std::vector<uint8_t> bytes;
    if(data) assert(fwrite(data->data(),1,data->size(),f)==data->size());
    else {
        assert(!fseek(f,0,SEEK_END)); const auto n=ftell(f); assert(n>0 && n<64*1024*1024);
        assert(!fseek(f,0,SEEK_SET)); bytes.resize(size_t(n)); assert(fread(bytes.data(),1,bytes.size(),f)==bytes.size());
    }
    assert(!fclose(f)); return bytes;
}
static std::vector<uint8_t> nrg_future(NrgFixture &disc)
{
    auto result=future(disc); p6_sha256 hash; p6_sha256_init(&hash);
    cd_toc_head toc{}; assert(sys_command_cd_toc(0,&toc));
    assert(toc.tracks==3 && toc.lastaddress==39 && toc.toc[3].paddress==32 && toc.toc[5].paddress==39);
    for(unsigned ordinal=1;ordinal<=2;++ordinal) {
        SessionToc session{}; assert(akiko_session_toc(disc.disc,ordinal,&session)==Result::Ok);
        const auto &s=session.session;
        assert(s.first_track==(ordinal==1?1:3) && s.last_track==(ordinal==1?2:3));
        assert(s.format==(ordinal==1?0:32) && s.cue0==(ordinal==1?-150:20) && s.a2==(ordinal==1?11:39));
        const auto &t=session.toc;
        for(int n=0;n<t.points;++n) {
            p6_sha256_update(&hash,&t.toc[n].point,1);
            p6_sha256_update(&hash,&t.toc[n].paddress,sizeof t.toc[n].paddress);
        }
    }
    uint8_t raw[2352],q[SUBQ_SIZE];
    for(int lba : {0,32}) {
        assert(sys_command_cd_rawread(0,raw,lba,1,sizeof raw)==sizeof raw);
        assert(sys_command_cd_qcode(0,q,lba,false));
        p6_sha256_update(&hash,raw,sizeof raw); p6_sha256_update(&hash,q,sizeof q);
    }
    const auto before=disc.calls;
    for(uint32_t lba : {11u,20u,29u}) {
        memset(raw,0xa5,sizeof raw);
        assert(canonical_read(disc.disc,lba,sizeof raw,raw,sizeof raw)==Result::Range);
        assert(std::all_of(raw,raw+sizeof raw,[](uint8_t b){ return b==0xa5; }));
    }
    assert(disc.calls==before);
    uint8_t digest[32]; p6_sha256_final(&hash,digest); result.insert(result.end(),digest,digest+32); return result;
}
static void nrg_state(const std::vector<uint8_t> &rom,const char *mode,const char *path,
                      const char *catalog_other,const char *payload_other,const char *prefix)
{
    const bool writer=!strcmp(mode,"write"); assert(writer || !strcmp(mode,"read"));
    for(unsigned cut=0;cut<2;++cut) {
        const auto stem=std::string(prefix)+std::to_string(cut);
        NrgFixture disc(path); start(rom,disc,false);
        std::vector<uint8_t> state,expected,end;
        if(writer) {
            step(); put_long(AKIKO_BASE+0x24,0); pio_command({7}); // Populate the cache through the real INFO command.
            auto component=std::make_unique<p6_akiko_state>(); assert(p6_akiko_state_get(component.get()));
            assert(component->controller.toc.tracks==3 && component->controller.toc.lastaddress==39);
            if(!cut) assert(sys_command_cd_play(0,0,4,0,nullptr,nullptr));
            else {
                uint8_t sector[2352]; sys_command_cd_stop(0);
                assert(sys_command_cd_rawread(0,sector,32,1,sizeof sector)==sizeof sector && device_position()==33);
            }
            assert(p6_akiko_state_get(component.get()));
            assert(cut?!component->device.audio.running:component->device.audio.running);
            state=save(); expected=nrg_future(disc); end=save();
            state_file(stem+".state",&state); state_file(stem+".future",&expected); state_file(stem+".end",&end);
        } else {
            state=state_file(stem+".state"); expected=state_file(stem+".future"); end=state_file(stem+".end");
        }
        // Reader reaches Apply before its first frame, with newly allocated sources and contexts.
        const auto calls=disc.calls; load(state); assert(save()==state && disc.calls==calls);
        assert(nrg_future(disc)==expected && save()==end); stop();
        for(const char *foreign : {catalog_other,payload_other}) {
            NrgFixture alien(foreign); start(rom,alien,false);
            cold_reject(state,alien); // A configuration before its first unit cannot be saved.
            step(); reject(state,P6_ERROR_INCOMPATIBLE,alien); stop(); // Separate started-destination fullsave oracle.
        }
        printf("native NRG multi cut%u %s exact/future/fullstate/identity PASS\n",cut,mode);
    }
}
int main(int argc,char **argv)
{
    assert(argc==3 || argc==8); auto rom=file(argv[2]); const auto main=file(argv[1]); rom.insert(rom.end(),main.begin(),main.end());
    if(argc==8) { nrg_state(rom,argv[3],argv[4],argv[5],argv[6],argv[7]); return 0; }
    for(int mode=0;mode<5;++mode) {
        const bool fast=mode==4;
        DiscFixture a,b,alien(43); start(rom,a,mode&1,fast); step();
        if(fast) {
            assert(currprefs.fastmem[0].size==8*1024*1024 && !currprefs.cs_ide && !currprefs.cs_pcmcia);
            put_byte(0xe8004a,0); put_byte(0xe80048,0x20); // The real Zorro II autoconfig writes.
            assert(&get_mem_bank(0x200000)==&fastmem_bank[0]);
            put_long(0x200100,0x1234abcd); put_long(0x9ffffc,0xa9876543);
            assert(do_get_mem_long(fastmem_bank[0].baseaddr+0x100)==0x1234abcd);
            assert(do_get_mem_long(fastmem_bank[0].baseaddr+8*1024*1024-4)==0xa9876543);
        }
        if(mode!=2) {
            assert(sys_command_cd_play(0,0,mode==3?1:20,0,nullptr,nullptr));
            if(mode==1) assert(sys_command_cd_pause(0,1)>=0);
            if(mode==3) step();
        } else {
            // Real Akiko play request supplies its canonical ten-sector delay.
            put_long(AKIKO_BASE+0x24,0);
            pio_command({7}); // Consume startup media status and query INFO before PLAY.
            pio_command({4,0,2,0,0,2,0x20,0,0,0,0,0});
        }
        auto component=std::make_unique<p6_akiko_state>(); assert(p6_akiko_state_get(component.get()));
        const auto &audio=component->device.audio;
        if(mode==0) assert(audio.running && audio.stream.active && !audio.stream.paused);
        if(mode==1) assert(audio.running && audio.stream.paused);
        if(mode==2) assert(audio.delay && audio.running);
        if(mode==3) assert(!audio.running && audio.status==AUDIO_STATUS_PLAY_COMPLETE);
        auto nv=component->controller.nvram; nv.chip.memory[13]=0x6d; assert(p6_akiko_nvram_set(&nv));
        put_long(AKIKO_BASE+0x38,0x12345678); // Partial C2P input must survive the native codec.
        const auto cut=save(); assert(cut[48]==12); map_matches();
        const auto expected=future(a),end=save();
        load(cut); assert(save()==cut);
        if(fast) assert(get_long(0x200100)==0x1234abcd && get_long(0x9ffffc)==0xa9876543);
        assert(future(a)==expected && save()==end);
        if(!mode) {
            sys_command_cd_stop(0); AudioPlayer foreign;
            assert(foreign.play(a.disc,0,20,nullptr,nullptr)==Result::Ok);
            AudioPlayerState before{},after{}; assert(foreign.capture(&before));
            uint8_t ram[32],again[32]; p6_sha256_hash(chipmem_bank.baseaddr,chipmem_bank.allocated_size,ram);
            int32_t phase[p6_cda::kPhaseWords],phase_after[p6_cda::kPhaseWords];
            assert(foreign.phase_get(phase,p6_cda::kPhaseWords));
            const auto cycle=get_cycles(); const auto pc=m68k_getpc(); const auto calls=a.calls; auto e=error();
            assert(p6_state_load(cut.data(),cut.size(),&e)==P6_ERROR_BAD_STATE);
            assert(foreign.capture(&after) && !memcmp(&before,&after,sizeof before));
            assert(foreign.phase_get(phase_after,p6_cda::kPhaseWords) && !memcmp(phase,phase_after,sizeof phase));
            p6_sha256_hash(chipmem_bank.baseaddr,chipmem_bank.allocated_size,again);
            assert(!memcmp(ram,again,32) && get_cycles()==cycle && m68k_getpc()==pc && a.calls==calls);
            foreign.stop(); load(end);
        }
        auto corrupt=cut; corrupt.back()=0x7f; reseal(corrupt); reject(corrupt,P6_ERROR_CORRUPT_STATE,a);
        stop(); start(rom,alien,mode&1,fast); step(); reject(cut,P6_ERROR_INCOMPATIBLE,alien); stop();
        if(fast) {
            start(rom,a,false); step();
            // The FR payload exceeds the stock bound, checked before config identity (envelope.cpp).
            reject(cut,P6_ERROR_CORRUPT_STATE,a); stop();
        }
        start(rom,b,mode&1,fast); set_cycles(17); update_cda_sound(1); // Validate before reset head, with different live time/period.
        load(cut); assert(save()==cut);
        if(fast) assert(get_long(0x200100)==0x1234abcd && get_long(0x9ffffc)==0xa9876543);
        assert(future(b)==expected && save()==end); stop();
        printf("native CD32 mode%d exact restore/new owner/identity/corrupt/map PASS\n",mode);
    }
    catalog_state(rom);
}
