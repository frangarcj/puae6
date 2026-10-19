// Real Akiko + CDA/events + Disc. Only RAM/IRQ and the declared source are fixtures.
#define P6_CD_PLAYER_FIXTURE_ONLY
#include "cd_audio_probe.cpp"
#include "akiko.cpp"
#include "media/cd_device.h"
#include <memory>
static std::array<uae_u8, 0x10000> ram;
static unsigned irqs;
int maxvpos = 312;
float vblank_hz = 50;
void safe_interrupt_set(int source, int, bool) { assert(source == IRQ_SOURCE_CD32CDTV); ++irqs; }
void dma_put_byte(uaecptr at, uae_u8 v) { assert(at < ram.size()); ram[at] = v; }
void dma_put_word(uaecptr at, uae_u16 v) { dma_put_byte(at, v >> 8); dma_put_byte(at + 1, v); }
uae_u32 memory_get_byte(uaecptr at) { assert(at < ram.size()); return ram[at]; }
void memory_put_byte(uaecptr at, uae_u32 v) { dma_put_byte(at, v); }
void memory_put_long(uaecptr at, uae_u32 v)
{
    for (unsigned n = 0; n < 4; ++n) dma_put_byte(at + n, v >> (24 - 8 * n));
}
using State = p6_akiko_transport_state;
static void capture(State &s) { assert(p6_akiko_transport_get(&s) && p6_akiko_transport_preflight(&s)); }
static void fixed_control()
{
    ram.fill(0); irqs = 0; set_cycles(0);
    cd_initialized = 2; mediachanged = 0; lastmediastate = cdrom_disk = 1;
    cdrom_toc_counter = -1; cdrom_command_active = cdrom_receive_length = 0;
    cdrom_current_sector = -1; cdrom_speed = 1; cdrom_playing = cdrom_paused = 0;
    cdrom_audiostatus = cdrom_audiotimeout = 0; cdrom_flags = cdrom_intreq = 0;
    cdrom_intena = CDINTERRUPT_PBX | CDINTERRUPT_SUBCODE;
    akiko_clock_reset(); akiko_clock.first = false;
}
static void line() { set_cycles(get_cycles() + 227 * CYCLE_UNIT); AKIKO_hsync_handler(); }
static void continuation()
{
    // Complete the saved wrapped play prefix. Unsupported scan is a real error,
    // not an active audio stream: CDDA/controller restoration belongs to M3/N2.
    write_comm_pipe_u32(&requests, 1, 1); line(); assert(!request_count());
    assert(cdrom_audiostatus == AUDIO_STATUS_PLAY_ERROR && !mute_pending);
    cdrom_data_offset = 3; cdrom_sector_counter = 0; cdrom_addressdata = 0x1000;
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX | CDFLAG_RAW | CDFLAG_CAS; cdrom_pbx = 1;
    cdrom_run_read(); assert(!cdrom_pbx && cdrom_sector_counter == 1);
    // Use the real hsync subcode DMA path, independently of CDDA production.
    cdrom_current_sector = -1; cdrom_flags = CDFLAG_SUBCODE; cdrom_playing = 1;
    subcode_address = 0x3000; cdrom_subcodeoffset = 0xc2; subcodecounter = 1;
    line(); assert(subcodebufferoffset == 0 && !subcodebufferinuse[35]);
    for (unsigned n = 0; n < 96; ++n) assert(ram[0x3000 + n] == uae_u8(n * 7));
    assert(ram[0x3060] == 0xff && ram[0x3061] == 0xff && irqs >= 2);
}
int main()
{
    setup(); Fixture f; assert(device_bind(&f.disc) == Result::Ok);
    unitnum = get_standard_cd_unit(CD_STANDARD_UNIT_CD32); assert(unitnum == 0);
    assert(akiko_toc(f.disc, &cdrom_toc_cd_buffer) == Result::Ok);
    currprefs.cs_cd32cd = 1; akiko_inited = true;
    uae_sem_init(&akiko_sem, 0, 1); uae_sem_init(&sub_sem, 0, 1);
    sector_buffer_1 = new uae_u8[128 * 2352]; sector_buffer_2 = new uae_u8[128 * 2352];
    sector_buffer_info_1 = new uae_u8[128]{}; sector_buffer_info_2 = new uae_u8[128]{};
    sector_buffer_sector_1 = sector_buffer_sector_2 = -1;
    assert(akiko_service_lifecycle(1)); fixed_control();
    auto cut = std::make_unique<State>(), other = std::make_unique<State>(), future = std::make_unique<State>();
    // Even metadata 1 under head=-1 cannot make its uninitialized bytes readable.
    sector_buffer_info_2[17] = 1; sector_buffer_info_2[18] = 0xff;
    memset(sector_buffer_1, 0xa5, 128 * 2352); memset(sector_buffer_2, 0xa5, 128 * 2352);
    memset(subcodebuffer, 0xa5, sizeof subcodebuffer); capture(*cut);
    assert(cut->head[1] == -1 && cut->info[1][17] == 1 && cut->info[1][18] == 0xff);
    memset(subcodebuffer, 0x5a, sizeof subcodebuffer);
    memset(sector_buffer_1, 0x5a, 128 * 2352); memset(sector_buffer_2, 0x5a, 128 * 2352);
    capture(*other); assert(!memcmp(cut.get(), other.get(), sizeof(State)));
    cdrom_data_offset = 0; cdrom_sector_counter = 0; cdrom_pbx = 1;
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX;
    cdrom_run_read(); const auto invalid_ram = ram; assert(cdrom_pbx == 1);
    assert(p6_akiko_transport_apply(cut.get()));
    cdrom_run_read(); assert(ram == invalid_ram && cdrom_pbx == 1);
    // Capacity99 and wrap retain every occupied word, not just the play prefix.
    requests.rdp = requests.wrp = 73;
    for (unsigned n = 0; n < 99; ++n) write_comm_pipe_u32(&requests, 0x0104, 1);
    capture(*other); requests.rdp = requests.wrp = 0;
    assert(p6_akiko_transport_apply(other.get()) && request_count() == 99);
    for (unsigned n = 0; n < 99; ++n) assert(read_comm_pipe_u32_blocking(&requests) == 0x0104);
    // Build a causal cut with valid cache, wrapped incomplete play and ring entry.
    fixed_control(); sector_buffer_sector_1 = 3; sector_buffer_info_1[0] = 3;
    for (unsigned n = 0; n < 2352; ++n) sector_buffer_1[n] = uae_u8(n * 13);
    requests.rdp = requests.wrp = 98;
    write_comm_pipe_u32(&requests, 0x0110, 0);
    write_comm_pipe_u32(&requests, 0, 0); write_comm_pipe_u32(&requests, 1, 0);
    subcodebufferoffset = subcodebufferoffsetw = 35;
    uae_u8 sub[96]; for (unsigned n = 0; n < 96; ++n) sub[n] = uae_u8(n * 7);
    cdrom_flags = CDFLAG_SUBCODE; subfunc(sub, 1); assert(subcodebufferoffsetw == 0);
    mute_pending = true; cdrom_muted = 1;
    capture(*cut); const auto at = get_cycles(); const auto calls = f.provider.calls;
    assert(p6_akiko_transport_apply(cut.get()));
    assert(get_cycles() == at && !irqs && f.provider.calls == calls && request_count() == 3);
    line(); assert(requests.rdp == 98 && request_count() == 3); // no partial packet consumption
    assert(p6_akiko_transport_apply(cut.get())); fixed_control();
    continuation(); const auto expected = ram; const auto expected_irqs = irqs;
    capture(*future);
    // Destination buffer addresses/physical order differ; logical state is identical.
    std::swap(sector_buffer_1, sector_buffer_2); std::swap(sector_buffer_info_1, sector_buffer_info_2);
    assert(p6_akiko_transport_apply(cut.get())); fixed_control(); continuation();
    capture(*other);
    assert(ram == expected && irqs == expected_irqs && !memcmp(other.get(), future.get(), sizeof(State)));
    *other = *cut; other->sector[0][0][4] ^= 1;
    assert(p6_akiko_transport_apply(other.get())); fixed_control(); continuation();
    assert(ram != expected); // Valid cache bytes really determine future DMA.
    assert(p6_akiko_transport_apply(cut.get())); capture(*future);
    for (unsigned which = 0; which < 13; ++which) {
        *other = *cut;
        switch (which) {
        case 0: other->service = 0; break; case 1: other->mute_pending = 2; break;
        case 2: other->read = 100; break; case 3: other->write = 100; break;
        case 4: other->words[1] = 1; break; case 5: other->head[0] = -2; break;
        case 6: other->info[0][0] = 4; break; case 7: other->sector[0][1][0] = 1; break;
        case 8: other->sub_read = 36; break; case 9: other->sub_write = 36; break;
        case 10: other->sub_used[0] = 2; break; case 11: other->sub[0][0] = 1; break;
        case 12: other->head[1] = INT_MAX; break;
        }
        assert(!p6_akiko_transport_preflight(other.get()) && !p6_akiko_transport_apply(other.get()));
        capture(*other); assert(!memcmp(other.get(), future.get(), sizeof(State)));
    }
    assert(!p6_akiko_transport_get(nullptr) && !p6_akiko_transport_apply(nullptr));
    requests.reader_waiting = 1; *other = *cut;
    assert(!p6_akiko_transport_get(other.get()) && !memcmp(other.get(), cut.get(), sizeof(State)));
    requests.reader_waiting = 0;
    assert(akiko_service_lifecycle(0)); assert(!p6_akiko_transport_preflight(cut.get()));
    delete[] sector_buffer_1; delete[] sector_buffer_2; delete[] sector_buffer_info_1; delete[] sector_buffer_info_2;
    sys_command_close(unitnum); device_unbind();
}
