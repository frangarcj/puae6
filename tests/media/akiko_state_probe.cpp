#define P6_AKIKO_CONTROL_FIXTURE_ONLY
#include "akiko_controller_state_probe.cpp"
#include "state_io/akiko.h"
using Whole = p6_akiko_state;
static void whole_get(Whole &s) { assert(p6_akiko_state_get(&s)); }
// Independent semantic comparison: padding is excluded, every member/array is checked.
static void equal_fields(const Whole &a, const Whole &b)
{
#define E(path) assert(a.path == b.path)
#define A(path) assert(!memcmp(a.path, b.path, sizeof a.path))
    E(controller.config); E(controller.misc_initialized);
#define CONTROL_EQUAL(type, name) E(controller.name);
    P6_AKIKO_CONTROL_FIELDS(CONTROL_EQUAL)
#undef CONTROL_EQUAL
    A(controller.input); A(controller.result); A(controller.command);
    A(controller.response); A(controller.toc_bytes); A(controller.q);
    E(controller.toc.first_track); E(controller.toc.first_track_offset);
    E(controller.toc.last_track); E(controller.toc.last_track_offset);
    E(controller.toc.firstaddress); E(controller.toc.lastaddress);
    E(controller.toc.tracks); E(controller.toc.points);
    for (int i = 0; i < 103; ++i) {
        E(controller.toc.entry[i].adr); E(controller.toc.entry[i].control);
        E(controller.toc.entry[i].tno); E(controller.toc.entry[i].point);
        E(controller.toc.entry[i].track); E(controller.toc.entry[i].zero);
        A(controller.toc.entry[i].crc); E(controller.toc.entry[i].address); E(controller.toc.entry[i].paddress);
    }
    E(controller.clock.cycle); E(controller.clock.rate); E(controller.clock.sector);
    E(controller.clock.qcode); E(controller.clock.media); E(controller.clock.first);
    E(controller.clock.q_due); E(controller.clock.media_due); E(controller.clock.speed);
    E(controller.nvram.chip.state); E(controller.nvram.chip.last_data); E(controller.nvram.chip.last_clock);
    E(controller.nvram.chip.device_out); E(controller.nvram.chip.buffer); E(controller.nvram.chip.estate);
    E(controller.nvram.chip.current_addr); E(controller.nvram.chip.eeprom_addr); E(controller.nvram.chip.write_offset);
    A(controller.nvram.chip.memory); E(controller.nvram.direction); E(controller.nvram.scl);
    E(controller.nvram.sda); E(controller.nvram.reserved);
    E(transport.service); E(transport.mute_pending); E(transport.read); E(transport.write);
    E(transport.muted); A(transport.head); A(transport.words); A(transport.info); A(transport.sector);
    E(transport.sub_read); E(transport.sub_write); A(transport.sub_used); A(transport.sub);
    E(device.audio.delay); A(device.audio.pcm); A(device.audio.disc_id);
    E(device.audio.stream.active); E(device.audio.stream.paused); E(device.audio.stream.frames);
    E(device.audio.stream.offset); E(device.audio.stream.remaining); E(device.audio.stream.event_cycles);
    E(device.audio.stream.period); E(device.audio.stream.userdata); A(device.audio.stream.volume);
    for (int i = 0; i < 2; ++i) {
        E(device.audio.stream.channel[i].current); E(device.audio.stream.channel[i].previous);
        E(device.audio.stream.channel[i].accumulator); E(device.audio.stream.channel[i].cycles);
    }
    E(device.audio.bound); E(device.audio.running); E(device.audio.sector); E(device.audio.callbacks);
    E(device.audio.position); E(device.audio.end); E(device.audio.left); E(device.audio.right);
    E(device.audio.status); E(device.audio.error); A(device.disc_id); E(device.bound); E(device.inserted); E(device.opens);
    E(device.position); E(device.audio_head); E(device.terminal_ack); E(device.error);
#undef A
#undef E
}
struct WireOffsets { size_t cycle = 0, pcm = 0; };
static void record_wire(void *opaque, uint64_t offset, uint64_t width, const char *name)
{
    auto &map = *static_cast<WireOffsets *>(opaque);
    if (!strcmp(name, "clock.cycle")) { assert(width == 8); map.cycle = offset; }
    if (!strcmp(name, "audio.pcm") && !map.pcm) { assert(width == 2); map.pcm = offset; }
}
static std::vector<uint8_t> wire(Whole &s, WireOffsets *map = nullptr)
{
    auto measure = p6_state::Archive::measurer(); p6_state::akiko_candidate_fields(measure, s);
    assert(measure.ok()); std::vector<uint8_t> out(measure.position());
    auto save = p6_state::Archive::saver(out.data(), out.size());
    if (map) save.on_field(record_wire, map);
    p6_state::akiko_candidate_fields(save, s); assert(save.ok() && save.position() == out.size());
    return out;
}
static void codec_coverage()
{
    auto source = std::make_unique<Whole>(), decoded = std::make_unique<Whole>();
    // Every semantic byte differs from the destination sentinel, even deep in arrays.
    auto *bytes = reinterpret_cast<uint8_t *>(source.get());
    for (size_t i = 0; i < sizeof(Whole); ++i) bytes[i] = uint8_t(0x10 + i % 0x70);
    source->controller.config = 0x12345678; source->controller.clock.cycle = -2;
    source->device.audio.pcm[0] = -0x1234;
    auto copy = std::make_unique<Whole>(*source);
    WireOffsets map; const auto encoded = wire(*source, &map); equal_fields(*source, *copy);
    assert(encoded[0] == 0x78 && encoded[1] == 0x56 && encoded[2] == 0x34 && encoded[3] == 0x12);
    assert(map.cycle && encoded[map.cycle] == 0xfe);
    for (int i = 1; i < 8; ++i) assert(encoded[map.cycle + i] == 0xff);
    assert(map.pcm && encoded[map.pcm] == 0xcc && encoded[map.pcm + 1] == 0xed);
    // This deliberately out-of-domain pattern tests traversal, not M3 validity.
    for (bool applying : {false, true}) {
        memset(decoded.get(), 0xa5, sizeof(Whole));
        auto read = applying ? p6_state::Archive::applier(encoded.data(), encoded.size()) :
                               p6_state::Archive::validator(encoded.data(), encoded.size());
        p6_state::akiko_candidate_fields(read, *decoded); read.finish(); assert(read.ok());
        equal_fields(*source, *decoded); assert(wire(*decoded) == encoded);
    }
}
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
    int32_t phase[p6_cda::kPhaseWords]; assert(device_phase_get(phase, p6_cda::kPhaseWords));
    akiko_internal(); akiko_internal(); assert(cdrom_receive_length == 2 && cdrom_result_buffer[0] == 0);
    const auto encoded = wire(*cold);
    assert(p6_state::akiko_candidate_decode(encoded.data(), encoded.size(), *cold, context));
    set_cycles(context.cycle); assert(device_phase_set(phase, p6_cda::kPhaseWords));
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
    p6_pcm_reset(); int32_t phase[p6_cda::kPhaseWords], expected_phase[p6_cda::kPhaseWords];
    assert(device_phase_get(phase, p6_cda::kPhaseWords));
    auto cut = std::make_unique<Whole>(), end = std::make_unique<Whole>(), again = std::make_unique<Whole>();
    whole_get(*cut); p6_akiko_context context{cut->controller.config, cut->device.audio.stream.period, get_cycles(), &a.disc};
    assert(p6_akiko_state_validate(cut.get(), context));
    const auto encoded = wire(*cut);
    const auto saved_ram = ram; const auto saved_irqs = irqs; const auto calls = a.provider.calls;
    const auto expected = joint_future(mode & 1); const auto expected_pcm = pcm(); const auto expected_ram = ram;
    const auto expected_irqs = irqs; whole_get(*end); const auto expected_calls = a.provider.calls - calls;
    assert(device_phase_get(expected_phase, p6_cda::kPhaseWords));
    const auto end_wire = wire(*end); const auto untouched_cycle = get_cycles();
    assert(!p6_state::akiko_candidate_decode(nullptr, 0, *again, context));
    assert(!p6_state::akiko_candidate_decode(nullptr, encoded.size(), *again, context));
    const auto untouched_calls = a.provider.calls;
    auto reject = [&](const std::vector<uint8_t> &bad) {
        assert(!p6_state::akiko_candidate_decode(bad.data(), bad.size(), *again, context));
        whole_get(*again); assert(wire(*again) == end_wire);
        assert(ram == expected_ram && irqs == expected_irqs && get_cycles() == untouched_cycle);
        assert(a.provider.calls == untouched_calls);
    };
    // Truncate at scalar, cache payload and trailing-field boundaries; refuse extras.
    for (size_t size : {size_t(0), size_t(3), encoded.size() / 2, encoded.size() - 1})
        reject(std::vector<uint8_t>(encoded.begin(), encoded.begin() + size));
    auto extra = encoded; extra.push_back(0); reject(extra);
    // Valid early changes must not leak when a late component is corrupt.
    for (int n = 0; n < 6; ++n) {
        *again = *cut; again->controller.nvram.chip.memory[0] ^= 1; again->transport.sector[0][0][4] ^= 1;
        if (n == 0) again->device.audio.stream.offset = 589;
        if (n == 1) again->device.disc_id[31] ^= 1;
        if (n == 2) again->device.opens = 0;
        if (n == 3) again->device.audio.callbacks = 4;
        if (n == 4) again->transport.words[1] = 1;
        if (n == 5) again->controller.misc_initialized = 2;
        assert(!p6_akiko_state_apply(again.get(), context)); // Retain direct M3 atomic-rejection coverage.
        reject(wire(*again));
    }
    auto late = encoded; late[late.size() - 1] = 0x7f; reject(late); // device.error, final scalar
    end_owner(); update_cda_sound(1); // No destination owner and wrong live period.
    context.disc = &b.disc;
    assert(p6_state::akiko_candidate_decode(encoded.data(), encoded.size(), *again, context));
    equal_fields(*cut, *again);
    assert(!p6_akiko_state_preflight(cut.get(), context));
    setup(); begin_owner(b.disc); fixed_control(); nvram_read();
    set_cycles(context.cycle); assert(device_phase_set(phase, p6_cda::kPhaseWords));
    const auto now = get_cycles(); const auto count = b.provider.calls;
    assert(p6_akiko_state_apply(again.get(), context));
    assert(get_cycles() == now && b.provider.calls == count); // Restore is not device work.
    p6_pcm_reset();
    ram = saved_ram; irqs = saved_irqs;
    const auto repeated = joint_future(mode & 1); const auto repeated_pcm = pcm();
    if (repeated != expected || repeated_pcm != expected_pcm) {
        fprintf(stderr, "mode=%d replies=%d pcm=%d sizes=%zu/%zu\n", mode, repeated == expected, repeated_pcm == expected_pcm, repeated_pcm.size(), expected_pcm.size());
        for (size_t n = 0; n < repeated.size() && n < expected.size(); ++n) if (repeated[n] != expected[n]) { fprintf(stderr, "reply[%zu]=%u/%u\n", n, repeated[n], expected[n]); break; }
        for (size_t n = 0; n < repeated_pcm.size() && n < expected_pcm.size(); ++n) if (repeated_pcm[n] != expected_pcm[n]) { fprintf(stderr, "pcm[%zu]=%d/%d\n", n, repeated_pcm[n], expected_pcm[n]); break; }
    }
    assert(repeated == expected && repeated_pcm == expected_pcm);
    whole_get(*again); equal_fields(*again, *end); assert(wire(*again) == end_wire);
    int32_t repeated_phase[p6_cda::kPhaseWords]; assert(device_phase_get(repeated_phase, p6_cda::kPhaseWords));
    assert(!memcmp(repeated_phase, expected_phase, sizeof repeated_phase));
    assert(ram == expected_ram && irqs == expected_irqs && b.provider.calls - count == expected_calls);
    end_owner();
}
static std::vector<uint8_t> presence_future()
{
    std::vector<uint8_t> replies;
    auto take = [&] { while (cdrom_receive_length) replies.push_back(akiko_bget(AKIKO_BASE + 0x28)); };
    take(); // Preserve a reply already published before the tray action.
    assert(replies == std::vector<uint8_t>({0x0b, 0x70}));
    akiko_handler(false); take();
    assert(replies == std::vector<uint8_t>({0x0b, 0x70, 0x0a, 0, 0xf5}));
    assert(!cdrom_toc_cd_buffer.points && sys_command_ismedia(0, 1) == 0);
    const auto old_ram = ram; const auto old_irqs = irqs;
    cdrom_run_read(); // A previously armed PBX must not deliver the old cache.
    avline(); // Nor may the real subchannel hsync path deliver the queued old entry.
    assert(ram == old_ram && irqs == old_irqs);
    assert(p6_akiko_media_set(true));
    assert(!cdrom_toc_cd_buffer.points); // Only the ordinary handler refreshes it.
    akiko_handler(false); take();
    assert(replies == std::vector<uint8_t>({0x0b, 0x70, 0x0a, 0, 0xf5, 0x0a, 1, 0xf4}));
    assert(cdrom_toc_cd_buffer.tracks == 2 && cdrom_toc_cd_buffer.lastaddress == 4);
    assert(sys_command_cd_rawread(0, sector_buffer_1, 3, 1, 2352) == 2352);
    sector_buffer_sector_1 = 3; sector_buffer_info_1[0] = 3;
    cdrom_data_offset = 3; cdrom_pbx = 1;
    cdrom_run_read(); assert(!cdrom_pbx && ram[0x1004] != 0xa5);
    auto after = std::make_unique<Whole>(); whole_get(*after); const auto before_noop = wire(*after);
    assert(p6_akiko_media_set(true)); whole_get(*after); assert(wire(*after) == before_noop);
    advance(int(cda_evt * 20));
    return replies;
}
static void presence_cut(bool pending)
{
    setup(); Fixture a, b, c;
    assert(!p6_akiko_media_set(false));
    begin_owner(a.disc); fixed_control(); nvram_read(); assert(!get_cdrom_toc());
    cdrom_addressmisc = cdrx_address = 0x4000; cdtx_address = 0x4200; subcode_address = 0x4100;
    cdrom_flags = CDFLAG_ENABLE | CDFLAG_PBX | CDFLAG_SUBCODE | CDFLAG_RAW | CDFLAG_CAS;
    assert(sys_command_cd_play(0, 0, 2, 0, pending ? statusfunc : statusfunc_imm, subfunc));
    advance(int(cda_evt * 100)); pcm();
    sector_buffer_sector_1 = sector_buffer_sector_2 = 3;
    sector_buffer_info_1[0] = sector_buffer_info_2[0] = 3;
    memset(sector_buffer_1, 0xa5, 2352); memset(sector_buffer_2, 0xa5, 2352);
    cdrom_addressdata = 0x1000; cdrom_data_offset = 3; cdrom_sector_counter = 0; cdrom_pbx = 1;
    subcodebufferoffset = 35; subcodebufferoffsetw = 0; subcodebufferinuse[35] = 1;
    memset(subcodebuffer + 35 * SUB_CHANNEL_SIZE, 0xa5, SUB_CHANNEL_SIZE);
    requests.rdp = requests.wrp = 98;
    for (auto word : {0x0110u, 0u, 2u, 0u}) write_comm_pipe_u32(&requests, word, 0);
    cdrom_receive_length = 2; cdrom_receive_offset = 0;
    cdrom_result_buffer[0] = 0x0b; cdrom_result_buffer[1] = 0x70;
    auto before = std::make_unique<Whole>(), ejected = std::make_unique<Whole>(), after = std::make_unique<Whole>();
    whole_get(*before); assert(before->device.audio.running);
    assert(bool(before->device.audio.delay) == pending);
    const auto encoded_before = wire(*before);
    assert(p6_akiko_media_set(true)); whole_get(*after); assert(wire(*after) == encoded_before);
    const auto saved_ram = ram; const auto saved_irqs = irqs;
    const auto cycle = get_cycles(); const auto calls = a.provider.calls;
    int32_t phase_before[p6_cda::kPhaseWords], phase_ejected[p6_cda::kPhaseWords];
    assert(device_phase_get(phase_before, p6_cda::kPhaseWords));
    assert(p6_akiko_media_set(false));
    assert(get_cycles() == cycle && irqs == saved_irqs && ram == saved_ram && a.provider.calls == calls);
    assert(device_bound_disc() == &a.disc && sys_command_isopen(0) == 1 && !sys_command_ismedia(0, 1));
    whole_get(*ejected); const auto encoded_ejected = wire(*ejected);
    assert(!ejected->device.inserted && !ejected->device.audio.bound && !ejected->device.audio.stream.active);
    assert(ejected->controller.cdrom_door == before->controller.cdrom_door);
    assert(ejected->controller.clock.cycle == before->controller.clock.cycle);
    assert(ejected->controller.mediachanged && !ejected->controller.cdrom_disk && !request_count());
    assert(p6_akiko_media_set(false)); whole_get(*after); assert(wire(*after) == encoded_ejected);
    assert(device_phase_get(phase_ejected, p6_cda::kPhaseWords));
    const auto expected = presence_future(); const auto expected_pcm = pcm(); const auto expected_ram = ram;
    const auto expected_irqs = irqs; whole_get(*after); const auto expected_wire = wire(*after);
    const auto expected_reads = a.provider.calls - calls;
    end_owner();
    // Both cuts restore in new owners: an active/waiting play, or the empty unit.
    for (bool empty : {false, true}) {
        auto &f = empty ? c : b;
        setup(); begin_owner(f.disc); fixed_control(); nvram_read();
        const auto &encoded = empty ? encoded_ejected : encoded_before;
        const p6_akiko_context context{before->controller.config, before->device.audio.stream.period, cycle, &f.disc};
        assert(p6_state::akiko_candidate_decode(encoded.data(), encoded.size(), *after, context));
        set_cycles(cycle); assert(device_phase_set(empty ? phase_ejected : phase_before, p6_cda::kPhaseWords));
        const auto count = f.provider.calls;
        assert(p6_akiko_state_apply(after.get(), context) && f.provider.calls == count && get_cycles() == cycle);
        ram = saved_ram; irqs = saved_irqs; p6_pcm_reset();
        if (!empty) assert(p6_akiko_media_set(false));
        assert(presence_future() == expected && pcm() == expected_pcm);
        whole_get(*after); assert(wire(*after) == expected_wire && ram == expected_ram && irqs == expected_irqs);
        assert(f.provider.calls - count == expected_reads); end_owner();
    }
}
int main()
{
    codec_coverage(); for (int mode = 0; mode < 4; ++mode) exercise_joint(mode);
    for (bool pending : {false, true}) presence_cut(pending);
}
