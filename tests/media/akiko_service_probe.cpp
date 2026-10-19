// Include the verified derived unit: private state is visible only to this probe.
#include "akiko.cpp"
#include "media/cd_device.h"
#include <array>
#include <cassert>
#include <vector>

uae_prefs currprefs{};
regstruct regs{};
int maxvpos = 312;
float vblank_hz = 50;
static std::array<uae_u8, 0x40000> ram;
static int irqs;
void safe_interrupt_set(int source, int, bool) { assert(source == IRQ_SOURCE_CD32CDTV); ++irqs; }
void dma_put_byte(uaecptr at, uae_u8 v) { assert(at < ram.size()); ram[at] = v; }
void dma_put_word(uaecptr at, uae_u16 v) { dma_put_byte(at, v >> 8); dma_put_byte(at + 1, v); }
uae_u32 memory_get_byte(uaecptr at) { assert(at < ram.size()); return ram[at]; }
void memory_put_byte(uaecptr at, uae_u32 v) { dma_put_byte(at, v); }
void memory_put_long(uaecptr at, uae_u32 v)
{
    for (unsigned n = 0; n < 4; ++n) dma_put_byte(at + n, v >> (24 - 8 * n));
}
// Explicit fixture traps: no TOC/subQ command packets, MSF/BCD conversion or
// DMA SUBCODE is exercised. Calling one must fail rather than fake a device.
[[noreturn]] static void unexercised(const char *name) { fprintf(stderr, "unexercised fixture: %s\n", name); abort(); }
int msf2lsn(int) { unexercised("msf2lsn"); }
int lsn2msf(int) { unexercised("lsn2msf"); }
uae_u8 tobcd(uae_u8) { unexercised("tobcd"); }
int fromlongbcd(uae_u8 *) { unexercised("fromlongbcd"); }
void sub_to_interleaved(const uae_u8 *, uae_u8 *) { unexercised("sub_to_interleaved"); }

struct Provider {
    std::vector<unsigned> reads;
    int fault = -1;
    static uae_u8 value(uint64_t at) { return at % 2352 == 15 ? 1 : uae_u8(at * 17 + at / 2352); }
    static bool read(const void *ctx, uint64_t at, void *out, size_t count, size_t *got)
    {
        auto &p = *const_cast<Provider *>(static_cast<const Provider *>(ctx));
        p.reads.push_back(unsigned(at / 2352));
        if (int(at / 2352) == p.fault) { *got = 0; return false; }
        for (size_t n = 0; n < count; ++n) static_cast<uae_u8 *>(out)[n] = value(at + n);
        *got = count; return at + count <= 262 * 2352;
    }
};
static void check_buffer(unsigned start, unsigned count)
{
    assert(sector_buffer_sector_1 == int(start));
    for (unsigned n = 0; n < 128; ++n) {
        assert((sector_buffer_info_1[n] != 0) == (n < count));
        if (n < count) for (unsigned b = 0; b < 2352; ++b)
            assert(sector_buffer_1[n * 2352 + b] == Provider::value(uint64_t(start + n) * 2352 + b));
    }
}
int main()
{
    using namespace p6_cd;
    Provider provider;
    Binding binding{{{1}, 262 * 2352, 1}, Provider::read, &provider};
    Layout layout{}; layout.content_id[0] = 1; layout.tracks = 2; layout.leadout = 262;
    layout.track[0] = {0, 0, 260, 0, 2352, 0, Mode::Mode1Raw, 1, 4, false};
    layout.track[1] = {260, 260, 262, 0, 2352, 260 * 2352, Mode::Audio, 1, 0, false};
    Disc disc;
    assert(disc.bind(layout, &binding, 1) == Result::Ok && device_bind(&disc) == Result::Ok);
    unitnum = get_standard_cd_unit(CD_STANDARD_UNIT_CD32); assert(unitnum == 0);
    assert(akiko_toc(disc, &cdrom_toc_cd_buffer) == Result::Ok);
    currprefs.cs_cd32cd = 1; currprefs.cd_speed = 1;
    akiko_inited = true; cd_initialized = 2; lastmediastate = cdrom_disk = 1;
    frame2counter = mediacheckcounter = subcodecounter = 100000;
    cdrom_toc_counter = -1; cdrom_intena = CDINTERRUPT_PBX;
    uae_sem_init(&akiko_sem, 0, 1); uae_sem_init(&sub_sem, 0, 1);
    sector_buffer_1 = new uae_u8[128 * 2352]{}; sector_buffer_2 = new uae_u8[128 * 2352]{};
    sector_buffer_info_1 = new uae_u8[128]{}; sector_buffer_info_2 = new uae_u8[128]{};
    sector_buffer_sector_1 = sector_buffer_sector_2 = -1;
    assert(akiko_service_lifecycle(1));
    // No I/O merely from enqueue/internal command processing; hsync fills and DMA consumes.
    cdrom_current_sector = cdrom_data_offset = 0; cdrom_addressdata = 0x10000;
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX | CDFLAG_RAW | CDFLAG_CAS; cdrom_pbx = 0x8000;
    akiko_internal(); assert(provider.reads.empty());
    AKIKO_hsync_handler(); assert(provider.reads.size() == 128 && !cdrom_pbx && irqs);
    assert(cdrom_sector_counter == 1 && ram[0x1f004] == Provider::value(4));
    for (unsigned b = 4; b < 2352; ++b) assert(ram[0x1f000 + b] == Provider::value(b));
    cdrom_flags = 0;
    auto calls = provider.reads.size(); AKIKO_hsync_handler(); assert(provider.reads.size() == calls);
    // A reused 38-sector prefix must survive the 90-sector read after it.
    cdrom_current_sector = 90; AKIKO_hsync_handler(); check_buffer(90, 128);
    assert(provider.reads.size() == calls + 90 && provider.reads[calls] == 128);
    cdrom_current_sector = 259; AKIKO_hsync_handler(); check_buffer(259, 1);
    assert(provider.reads.back() == 259); calls = provider.reads.size();
    cdrom_current_sector = 262; AKIKO_hsync_handler(); assert(provider.reads.size() == calls);
    // Transactional burst failure falls back per sector; failed bytes never become valid.
    provider.fault = 5; cdrom_current_sector = 0; AKIKO_hsync_handler();
    assert(sector_buffer_sector_1 == 0 && sector_buffer_info_1[4] && !sector_buffer_info_1[5] && sector_buffer_info_1[6]);
    provider.fault = -1;
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX; cdrom_data_offset = 5; cdrom_sector_counter = 0; cdrom_pbx = 1;
    cdrom_run_read(); assert(cdrom_pbx == 1 && sector_buffer_info_1[5] == 0xff);
    cdrom_flags = 0; AKIKO_hsync_handler(); check_buffer(5, 128);
    cdrom_current_sector = -1;
    // Wrapped, incomplete play stays wholly queued until its fourth word arrives.
    requests.rdp = requests.wrp = 98;
    write_comm_pipe_u32(&requests, 0x0110, 0);
    write_comm_pipe_u32(&requests, 260, 0); write_comm_pipe_u32(&requests, 262, 0);
    AKIKO_hsync_handler(); assert(requests.rdp == 98 && request_count() == 3);
    write_comm_pipe_u32(&requests, 0, 1); AKIKO_hsync_handler();
    assert(!request_count() && cdrom_audiostatus == AUDIO_STATUS_PLAY_ERROR && device_last_error() == Result::Unsupported);
    // Admission leaves space for a complete stop + play, even near ring capacity.
    cdrom_receive_length = cdrom_command_active = 0;
    for (int n = 0; n < 93; ++n) write_comm_pipe_u32(&requests, 0x0104, 1);
    assert(can_send_command()); cdaudiostop(); assert(cd_play_audio(260, 262, 0));
    assert(request_count() == 98 && !can_send_command());
    AKIKO_hsync_handler(); assert(!request_count() && device_last_error() == Result::Unsupported);
    // Saturation backpressures commands; mute has a coalesced pending intent.
    for (int n = 0; n < 99; ++n) write_comm_pipe_u32(&requests, 0x0104, 1);
    cdrom_receive_length = cdrom_command_active = 0;
    assert(!can_send_command()); akiko_mute(1); assert(mute_pending && request_count() == 99);
    AKIKO_hsync_handler(); assert(!request_count() && !mute_pending);
    cdrom_receive_length = cdrom_command_active = 0; assert(can_send_command());
    // Restore queues the complete immediate-play intent, without blocking or fake success.
    last_play_pos = 260; last_play_end = 262; cdrom_playing = 1; cdrom_paused = 1;
    restore_akiko_final(); assert(request_count() == 9);
    AKIKO_hsync_handler(); assert(!request_count() && !cdrom_playing && device_last_error() == Result::Unsupported);
    assert(cdrom_audiostatus == AUDIO_STATUS_PLAY_ERROR && (cdrom_result_buffer[1] & CDS_ERROR));
    assert(!akiko_service_lifecycle(1));
    assert(akiko_service_lifecycle(0) && requests.data == nullptr && !akiko_service_lifecycle(0));
    assert(akiko_service_lifecycle(1) && !request_count() && akiko_service_lifecycle(0));
    delete[] sector_buffer_1; delete[] sector_buffer_2; delete[] sector_buffer_info_1; delete[] sector_buffer_info_2;
    sys_command_close(unitnum); device_unbind();
}
