#define main transport_probe_main
#include "akiko_transport_state_probe.cpp"
#undef main
#include "akiko_controller_state.h"
using Control = p6_akiko_controller_state;
static void pins(int clock, int data) { akiko_bput(AKIKO_BASE + 0x30, (clock ? 0x80 : 0) | (data ? 0x40 : 0)); }
static int bit(int value) { pins(0, value); pins(1, value); return (akiko_bget(AKIKO_BASE + 0x30) >> 6) & 1; }
static void send_byte(unsigned value) { for (int n = 7; n >= 0; --n) bit((value >> n) & 1); assert(!bit(1)); }
static Control snapshot() { Control s{}; assert(p6_akiko_controller_get(&s)); return s; }
static std::vector<uint32_t> future(bool rx)
{
    std::vector<uint32_t> out;
    if (!rx) akiko_bput(AKIKO_BASE + 0x28, 0xff); // Finish the saved NOP/checksum.
    for (int n = 0; n < (rx ? 1 : 2); ++n) out.push_back(akiko_bget(AKIKO_BASE + 0x28));
    assert(!cdrom_receive_length && cdrom_receive_offset == 2); // Legal PIO residual cursor.
    for (int n = 0; n < 16; ++n) out.push_back(akiko_bget(AKIKO_BASE + 0x38));
    for (int n = 4; n >= 0; --n) bit(0); // Remainder of EEPROM device byte0xa0.
    assert(!bit(1)); send_byte(0x12); send_byte(0x6b); pins(0, 0); pins(1, 0); pins(1, 1);
    assert(cd32_nvram[0x12] == 0x6b);
    for (int n = 0; n < 7; ++n) line();
    return out;
}
static void exercise(bool ntsc, bool rx)
{
    fixed_control(); currprefs.ntscmode = ntsc; akiko_clock_reset(); akiko_clock.first = false;
    cdrom_command_length = cdrom_command_active = cdrom_receive_length = cdrom_receive_offset = 0;
    cdrom_intena = CDINTERRUPT_DRIVEXMIT | CDINTERRUPT_DRIVERECV; cdrom_intreq = CDINTERRUPT_SUBCODE;
    cdrom_addressmisc = 0x4000; cdrx_address = 0x4000; cdtx_address = 0x4200; subcode_address = 0x4100;
    nvram_read(); akiko_c2p_precalculate();
    for (int n = 0; n < 8; ++n) akiko_lput(AKIKO_BASE + 0x38, 0x12345678u + n);
    akiko_bget(AKIKO_BASE + 0x38); // Preserve a partially consumed C2P sequence.
    akiko_bput(AKIKO_BASE + 0x32, 0xdf); pins(1, 1); pins(1, 0);
    bit(1); bit(0); bit(1); // EEPROM captured after only three device bits.
    akiko_bput(AKIKO_BASE + 0x28, 0);
    if (rx) { akiko_bput(AKIKO_BASE + 0x28, 0xff); assert(akiko_bget(AKIKO_BASE + 0x28) == 0); }
    set_cycles(123 * CYCLE_UNIT); akiko_clock.cycle = get_cycles() - 10;
    akiko_clock.sector = akiko_clock.rate / 3; akiko_clock.qcode = 17; akiko_clock.media = 29;
    cdrom_speed = 2; // Requested speed differs legitimately until the next hsync.
    last_play_pos = -150; last_play_end = 999999; cdrom_data_offset = 999999;
    cdrom_current_sector = -1; cdrom_seek_delay = 37; cdrom_audiotimeout = 0;
    qcode_valid = 1; for (unsigned n = 0; n < 16; ++n) qcode_buf[n] = uae_u8(255 - n);
    const auto cut = snapshot(); const auto cycle = get_cycles();
    const auto expected = future(rx); const auto end = snapshot(); const auto expected_ram = ram;
    const auto expected_irqs = irqs; const auto now = get_cycles();
    assert(now > cycle && p6_akiko_controller_preflight(&cut, cycle));
    assert(p6_akiko_controller_apply(&cut, cycle) && get_cycles() == now && irqs == expected_irqs);
    set_cycles(cycle); ram.fill(0); irqs = rx ? 2 : 1; // External RAM/IRQ fixture at cut.
    // Compare only IRQs raised by the continuation, independently of setup traffic.
    const auto initial_irqs = irqs; const auto repeated = future(rx); const auto again = snapshot();
    assert(repeated == expected && ram == expected_ram && !memcmp(&again, &end, sizeof end));
    assert(irqs - initial_irqs == expected_irqs - (rx ? 2 : 1));
    for (unsigned which = 0; which < 19; ++which) {
        auto bad = cut;
        switch (which) {
        case 0: bad.config ^= 8; break; case 1: bad.clock.cycle = cycle + 1; break;
        case 2: bad.clock.sector = bad.clock.rate; break; case 3: bad.clock.first = 2; break;
        case 4: bad.cdrom_command_length = 14; break; case 5: bad.cdrom_receive_offset = 33; break;
        case 6: bad.akiko_read_offset = 8; break; case 7: bad.cdrom_speed = 3; break;
        case 8: bad.toc.points = 103; break; case 9: bad.cdrom_addressmisc |= 1; break;
        case 10: bad.nvram.reserved = 1; break; case 11: bad.nvram.chip.device_out = 2; break;
        case 12: bad.nvram.chip.last_data ^= 1; break; case 13: bad.clock.media = bad.clock.rate * 2; break;
        case 14: bad.toc.last_track_offset = 102; break; case 15: bad.toc.lastaddress = INT_MAX; break;
        case 16: bad.toc.entry[0].paddress = INT_MAX; break; case 17: bad.cdrom_current_sector = INT_MAX; break;
        case 18: bad.toc.entry[101].control = 4; break;
        }
        bad.nvram.chip.memory[0] ^= 1; const auto count = irqs;
        assert(!p6_akiko_controller_preflight(&bad, cycle) && !p6_akiko_controller_apply(&bad, cycle));
        const auto intact = snapshot(); assert(!memcmp(&intact, &end, sizeof end) && irqs == count);
    }
}
int main()
{
    setup(); Fixture f; assert(device_bind(&f.disc) == Result::Ok);
    unitnum = get_standard_cd_unit(CD_STANDARD_UNIT_CD32); assert(unitnum == 0);
    currprefs.cs_cd32cd = currprefs.cs_cd32c2p = currprefs.cs_cd32nvram = 1; currprefs.cs_cd32nvram_size = 1024;
    akiko_inited = true; uae_sem_init(&akiko_sem, 0, 1); uae_sem_init(&sub_sem, 0, 1);
    sector_buffer_1 = new uae_u8[128 * 2352]; sector_buffer_2 = new uae_u8[128 * 2352];
    sector_buffer_info_1 = new uae_u8[128]{}; sector_buffer_info_2 = new uae_u8[128]{};
    sector_buffer_sector_1 = sector_buffer_sector_2 = -1; assert(akiko_service_lifecycle(1));
    fixed_control(); nvram_read(); cdrom_toc_cd_buffer = {}; cdrom_toc_counter = 0;
    const auto cold = snapshot(); assert(!cold.toc.points && p6_akiko_controller_apply(&cold, get_cycles()));
    assert(!get_cdrom_toc());
    for (bool ntsc : {false, true}) for (bool rx : {false, true}) exercise(ntsc, rx);
    const auto saved = snapshot(); const auto cycle = get_cycles();
    assert(akiko_service_lifecycle(0)); akiko_nvram_free();
    assert(p6_akiko_controller_validate(&saved, saved.config, cycle));
    assert(p6_cd32_eeprom_validate(&saved.nvram.chip) && p6_akiko_nvram_validate(&saved.nvram));
    assert(!p6_akiko_controller_preflight(&saved, cycle) && !p6_akiko_controller_apply(&saved, cycle));
    delete[] sector_buffer_1; delete[] sector_buffer_2; delete[] sector_buffer_info_1; delete[] sector_buffer_info_2;
    sys_command_close(unitnum); device_unbind();
}
