#define P6_AKIKO_CONTROL_FIXTURE_ONLY
#include "akiko_controller_state_probe.cpp"
#include "akiko_state.h"
using Whole = p6_akiko_state;
static void whole_get(Whole &s) { assert(p6_akiko_state_get(&s)); }
static void avline() { advance(227 * CYCLE_UNIT); AKIKO_hsync_handler(); }
static void drain_response() { while (cdrom_receive_length) akiko_bget(AKIKO_BASE + 0x28); }
static std::vector<uint32_t> joint_future(bool rx)
{
    auto out = finish_inputs(rx);
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX | CDFLAG_RAW | CDFLAG_CAS;
    cdrom_addressdata = 0x1000; cdrom_data_offset = 3; cdrom_sector_counter = 0; cdrom_pbx = 1;
    cdrom_run_read(); assert(!cdrom_pbx && cdrom_sector_counter == 1);
    cdrom_flags = CDFLAG_SUBCODE; cdrom_current_sector = -1; cdrom_playing = 1;
    subcodecounter = 1; cdrom_subcodeoffset = 0xc2; avline();
    assert(!subcodebufferinuse[35] && ram[0x4160] == 0xff && ram[0x4161] == 0xff);
    // Complete the saved play prefix with unsupported scan; the real backend
    // preserves the existing stream, unpauses it and reports the request error.
    write_comm_pipe_u32(&requests, 1, 1);
    for (int n = 0; n < 300; ++n) avline();
    uint8_t q[16]; memset(q, 0xa5, sizeof q);
    const int ok = sys_command_cd_qcode(0, q, -1, false);
    if (!ok) { assert(device_position() == 4 && device_last_error() == Result::Range); for (auto v : q) assert(v == 0xa5); }
    out.push_back(ok); out.push_back(uint32_t(device_last_error()));
    for (auto v : q) out.push_back(v);
    return out;
}
static void cold_dma_cut(const Disc &disc)
{
    assert(!cdrom_addressmisc && !subcode_address && !cdrx_address && !cdtx_address);
    cdrom_flags = CDFLAG_TXD; cdcomtxinx = 0; cdcomtxcmp = 2;
    cdrom_command_length = cdrom_receive_length = 0;
    ram[0] = 0; ram[1] = 0xff; ram[0x200] = 0x0b;
    auto cold = std::make_unique<Whole>(); whole_get(*cold);
    const p6_akiko_context context{cold->controller.config, cold->device.audio.stream.period, get_cycles(), &disc};
    akiko_internal(); akiko_internal(); assert(cdrom_receive_length == 2 && cdrom_result_buffer[0] == 0);
    assert(p6_akiko_state_apply(cold.get(), context));
    akiko_internal(); akiko_internal(); assert(cdrom_receive_length == 2 && cdrom_result_buffer[0] == 0);
    fixed_control();
}
static void exercise_joint(int mode)
{
    setup(); Fixture a, b; begin_owner(a.disc); fixed_control(); nvram_read();
    if (!mode) cold_dma_cut(a.disc);
    assert(!get_cdrom_toc());
    cdrom_flags = CDFLAG_SUBCODE;
    assert(sys_command_cd_play(0, 0, mode == 3 ? 1 : 2, 0, statusfunc, subfunc));
    // Actual Akiko statusfunc requests ten sectors of startup delay. Service
    // every hsync while the real mixer/events consume that delay and the PCM.
    for (int n = 0; n < 3000; ++n) {
        advance(227 * CYCLE_UNIT);
        DeviceState audio{}; assert(device_capture(&audio));
        if (mode == 3 ? audio.audio.status == AUDIO_STATUS_PLAY_COMPLETE :
            mode == 2 ? audio.audio.status == AUDIO_STATUS_NOT_SUPPORTED && audio.audio.stream.offset >= 100 :
            audio.audio.status == AUDIO_STATUS_IN_PROGRESS && audio.audio.stream.offset >= 100) break;
        AKIKO_hsync_handler(); assert(n != 2999);
    }
    drain_response();
    if (mode == 1) {
        akiko_bput(AKIKO_BASE + 0x28, 2); akiko_bput(AKIKO_BASE + 0x28, 0xfd);
        avline(); drain_response();
    }
    DeviceState audio{}; assert(device_capture(&audio));
    assert(mode == 1 ? audio.audio.stream.paused : mode == 2 ? audio.audio.delay > 0 :
        mode == 3 ? audio.audio.status == AUDIO_STATUS_PLAY_COMPLETE : audio.audio.running);
    // Real offset reader supplies the cache; real subfunc supplies the wrapped ring.
    sector_buffer_sector_1 = 3; sector_buffer_info_1[0] = 3;
    assert(sys_command_cd_rawread(0, sector_buffer_1, 3, 1, 2352) == 2352);
    uint8_t raw[2448], sub[96]; assert(canonical_read(a.disc, 0, 2448, raw, sizeof raw) == Result::Ok);
    sub_to_interleaved(raw + 2352, sub); memset(subcodebufferinuse, 0, 36);
    subcodebufferoffset = subcodebufferoffsetw = 35; subfunc(sub, 1);
    cdrom_receive_length = cdrom_receive_offset = cdrom_command_length = 0;
    akiko_lput(AKIKO_BASE + 0x14, 0x4000);
    partial_inputs(mode & 1); requests.rdp = requests.wrp = 98;
    write_comm_pipe_u32(&requests, 0x0110, 0); write_comm_pipe_u32(&requests, 0, 0); write_comm_pipe_u32(&requests, 1, 0);
    p6_pcm_reset(); const float phase = next_sample_evtime;
    auto cut = std::make_unique<Whole>(), end = std::make_unique<Whole>(), again = std::make_unique<Whole>();
    whole_get(*cut); p6_akiko_context context{cut->controller.config, cut->device.audio.stream.period, get_cycles(), &a.disc};
    assert(p6_akiko_state_validate(cut.get(), context));
    const auto saved_ram = ram; const auto saved_irqs = irqs; const auto calls = a.provider.calls;
    const auto expected = joint_future(mode & 1); const auto expected_pcm = pcm(); const auto expected_ram = ram;
    const auto expected_irqs = irqs; whole_get(*end); const auto expected_calls = a.provider.calls - calls;
    // Corrupt the last component after changing valid controller/cache payload.
    for (int n = 0; n < 6; ++n) {
        *again = *cut; again->controller.nvram.chip.memory[0] ^= 1; again->transport.sector[0][0][4] ^= 1;
        if (n == 0) again->device.audio.stream.offset = 589;
        if (n == 1) again->device.disc_id[31] ^= 1;
        if (n == 2) again->device.opens = 0;
        if (n == 3) again->device.audio.callbacks = 4;
        if (n == 4) again->transport.words[1] = 1;
        if (n == 5) again->controller.misc_initialized = 2;
        assert(!p6_akiko_state_apply(again.get(), context)); whole_get(*again);
        assert(!memcmp(again.get(), end.get(), sizeof(Whole)) && ram == expected_ram && irqs == expected_irqs);
    }
    end_owner(); update_cda_sound(1); // No destination owner and wrong live period.
    context.disc = &b.disc; assert(p6_akiko_state_validate(cut.get(), context));
    assert(!p6_akiko_state_preflight(cut.get(), context));
    setup(); begin_owner(b.disc); fixed_control(); nvram_read();
    const auto now = get_cycles(); const auto count = b.provider.calls;
    assert(p6_akiko_state_apply(cut.get(), context));
    assert(get_cycles() == now && b.provider.calls == count); // Restore is not device work.
    set_cycles(context.cycle); last_cycles = get_cycles(); next_sample_evtime = phase; p6_pcm_reset();
    ram = saved_ram; irqs = saved_irqs;
    const auto repeated = joint_future(mode & 1); const auto repeated_pcm = pcm();
    if (repeated != expected || repeated_pcm != expected_pcm) {
        fprintf(stderr, "mode=%d replies=%d pcm=%d sizes=%zu/%zu\n", mode, repeated == expected, repeated_pcm == expected_pcm, repeated_pcm.size(), expected_pcm.size());
        for (size_t n = 0; n < repeated.size() && n < expected.size(); ++n) if (repeated[n] != expected[n]) { fprintf(stderr, "reply[%zu]=%u/%u\n", n, repeated[n], expected[n]); break; }
        for (size_t n = 0; n < repeated_pcm.size() && n < expected_pcm.size(); ++n) if (repeated_pcm[n] != expected_pcm[n]) { fprintf(stderr, "pcm[%zu]=%d/%d\n", n, repeated_pcm[n], expected_pcm[n]); break; }
    }
    assert(repeated == expected && repeated_pcm == expected_pcm);
    whole_get(*again); assert(!memcmp(again.get(), end.get(), sizeof(Whole)));
    assert(ram == expected_ram && irqs == expected_irqs && b.provider.calls - count == expected_calls);
    end_owner();
}
int main() { for (int mode = 0; mode < 4; ++mode) exercise_joint(mode); }
