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
#include <memory>
struct DiscFixture {
    uint64_t calls=0;
    Disc disc;
    static bool read(const void *opaque,uint64_t at,void *out,size_t n,size_t *got) {
        auto &self=*const_cast<DiscFixture *>(static_cast<const DiscFixture *>(opaque)); ++self.calls;
        static const uint8_t pcm[4]={0xd2,0x04,0xd7,0xf6}; // LE +1234/-2345.
        if(at>20*2352 || n>20*2352-at) return false;
        for(size_t i=0;i<n;++i) static_cast<uint8_t *>(out)[i]=pcm[(at+i)%4];
        *got=n; return true;
    }
    explicit DiscFixture(uint8_t id=42) {
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
static void start(const std::vector<uint8_t> &rom,DiscFixture &disc,bool ntsc)
{
    auto c=config(); c.video_standard=ntsc?P6_VIDEO_NTSC:P6_VIDEO_PAL; auto e=error();
    call(p6_init(&c,&e),e); assert(device_bind(&disc.disc)==Result::Ok);
    call(p6_load({rom.data(),rom.size()},nullptr,0,&e),e); state_caps();
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
static std::vector<uint8_t> future(DiscFixture &disc)
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
static void reject(const std::vector<uint8_t> &bad,p6_result result,DiscFixture &disc)
{
    const auto before=save(); const auto cycle=get_cycles(); const auto calls=disc.calls; auto e=error();
    assert(p6_state_load(bad.data(),bad.size(),&e)==result);
    assert(save()==before && get_cycles()==cycle && disc.calls==calls); map_matches();
}
int main(int argc,char **argv)
{
    assert(argc==3); auto rom=file(argv[2]); const auto main=file(argv[1]); rom.insert(rom.end(),main.begin(),main.end());
    for(int mode=0;mode<4;++mode) {
        DiscFixture a,b,alien(43); start(rom,a,mode&1); step();
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
        load(cut); assert(save()==cut); assert(future(a)==expected && save()==end);
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
        stop(); start(rom,alien,mode&1); step(); reject(cut,P6_ERROR_INCOMPATIBLE,alien); stop();
        start(rom,b,mode&1); set_cycles(17); update_cda_sound(1); // Validate before reset head, with different live time/period.
        load(cut); assert(save()==cut); assert(future(b)==expected && save()==end); stop();
        printf("native CD32 mode%d exact restore/new owner/identity/corrupt/map PASS\n",mode);
    }
}
