#define P6_CD_PLAYER_FIXTURE_ONLY
#include "cd_audio_probe.cpp"
#include "media/cd_device.h"
#include "media/cd_device_state.h"
static int mode_number, rebound_calls;
static void record_sub(uint8_t *bytes, int count)
{
    assert(count == 1); subs.emplace_back(); memcpy(subs.back().data(), bytes, 96);
}
static int rebound_status(int status, int lba) { ++rebound_calls; return status_callback(status, lba); }
static void rebound_sub(uint8_t *bytes, int count) { ++rebound_calls; record_sub(bytes, count); }
static DeviceState snapshot()
{
    DeviceState s{}; assert(device_capture(&s)); return s;
}
static void equal(const DeviceState &a, const DeviceState &b)
{
    if (memcmp(&a, &b, sizeof a)) {
        const auto *x = reinterpret_cast<const uint8_t *>(&a), *y = reinterpret_cast<const uint8_t *>(&b);
        for (size_t i = 0; i < sizeof a; ++i) if (x[i] != y[i]) {
            fprintf(stderr, "mode=%d offset=%zu values=%u/%u\n", mode_number, i, x[i], y[i]); break;
        }
        assert(false);
    }
}
static void rejects(const DeviceState &saved, const Disc &disc)
{
    const auto before = snapshot(); const auto cycles = get_cycles();
    for (int which = 0; which < 16; ++which) {
        auto s = saved;
        switch (which) {
        case 0: s.bound = 2; break; case 1: s.opens = uint32_t(INT_MAX) + 1; break;
        case 2: s.position = 5; break; case 3: s.audio_head = 2; break;
        case 4: s.terminal_ack = 2; break; case 5: s.error = 6; break;
        case 6: s.disc_id[31] ^= 1; break; case 7: s.terminal_ack = 1; break;
        case 8: s.audio_head = 0; break; case 9: s.opens = 0; break;
        case 10: s.audio.disc_id[31] ^= 1; break; case 11: s.audio.stream.offset = 589; break;
        case 12: s.audio.callbacks = 4; break; case 13: s.bound = 0; break;
        case 14: s.inserted = 2; break; case 15: s.inserted = 0; break;
        }
        assert(!device_can_restore(s, &disc, status_callback, record_sub));
        assert(!device_restore(s, &disc, status_callback, record_sub)); equal(snapshot(), before);
    }
    assert(!device_restore(saved, nullptr, status_callback, record_sub));
    assert(!device_restore(saved, &disc, nullptr, record_sub)); equal(snapshot(), before);
    assert(get_cycles() == cycles);
}
static std::vector<uint8_t> future(int mode)
{
    if (mode == 2) { advance(int(cda_evt * 20)); assert(sys_command_cd_pause(0, 0) == 1); }
    advance(int(cda_evt * (mode == 2 ? 680 : 700) + 53));
    std::vector<uint8_t> result;
    for (bool all : {false, false, true}) {
        uint8_t q[96]; memset(q, 0xA5, sizeof q);
        result.push_back(uint8_t(sys_command_cd_qcode(0, q, -1, all)));
        result.insert(result.end(), q, q + sizeof q);
        result.push_back(uint8_t(device_last_error()));
    }
    if (sys_command_isopen(0) == 2) sys_command_close(0);
    return result;
}
static void replay(int mode)
{
    mode_number = mode; setup(); Fixture a, b;
    assert(device_bind(&a.disc) == Result::Ok);
    if (mode) {
        assert(sys_command_open(0) == 1 && sys_command_open(0) == -1);
        if (mode == 3) ms_delay = sector_delay = 1;
        if (mode == 6 || mode == 7) a.provider.fail = b.provider.fail = 1;
        assert(sys_command_cd_play(0, 0, mode == 4 || mode == 5 || mode == 11 ? 1 : 2, 0, status_callback, record_sub));
        advance(int(cda_evt * (mode >= 4 && mode <= 7 || mode == 11 ? 588 : 100) + 137));
        if (mode == 2) { assert(sys_command_cd_pause(0, 1) == 0); advance(int(cda_evt * 3)); }
        uint8_t q[96];
        if (mode == 5 || mode == 7) assert(sys_command_cd_qcode(0, q, -1, false));
        if (mode == 7 || mode == 11) { uint8_t raw[2448]; assert(sys_command_cd_rawread(0, raw, 2, 1, 2448) == 2448); }
        if (mode == 8) { sys_command_close(0); sys_command_close(0); }
        if (mode == 9) device_unbind();
        if (mode == 10) { uint8_t raw[2448]; assert(!sys_command_cd_rawread(0, raw, 4, 1, 2448)); }
        if (mode >= 12) {
            const auto calls = a.provider.calls;
            assert(device_set_inserted(false) == Result::Ok);
            assert(device_bound_disc() == &a.disc && sys_command_isopen(0) == 2 && !sys_command_ismedia(0, 1));
            if (mode == 13) assert(device_set_inserted(true) == Result::Ok);
            assert(a.provider.calls == calls);
        }
    }
    pcm(); const auto saved = snapshot(); const float phase = next_sample_evtime;
    if (mode == 1) rejects(saved, a.disc);
    if (mode == 6) assert(saved.error == int(Result::Ok) && saved.audio.error == int(Result::ReadError));
    if (mode == 7) assert(saved.terminal_ack && !saved.audio_head);
    if (mode == 11) assert(!saved.terminal_ack && !saved.audio_head && saved.audio.status == AUDIO_STATUS_PLAY_COMPLETE);
    if (mode >= 12) assert(saved.bound && saved.inserted == uint32_t(mode == 13) && !saved.audio.bound);
    notices.clear(); subs.clear();
    const auto control_q = future(mode); const auto control_pcm = pcm(); const auto control = snapshot();
    const auto control_notices = notices; const auto control_subs = subs; const auto control_position = device_position();
    device_unbind(); const auto empty = snapshot();
    const auto calls = b.provider.calls; const auto cycles = get_cycles();
    Layout other = b.disc.layout(); other.content_id[31] = 7; Disc alien;
    Binding binding{{{1}, 4 * 2448, 1}, provide, &b.provider}; assert(alien.bind(other, &binding, 1) == Result::Ok);
    const auto status = saved.audio.callbacks & 1 ? rebound_status : nullptr;
    const auto sub = saved.audio.callbacks & 2 ? rebound_sub : nullptr;
    assert(!device_restore(saved, &alien, status, sub)); equal(snapshot(), empty);
    rebound_calls = 0; notices.clear(); subs.clear();
    const Disc *candidate = saved.bound ? &b.disc : nullptr;
    assert(device_can_restore(saved, candidate, status, sub) && device_restore(saved, candidate, status, sub));
    assert(get_cycles() == cycles && b.provider.calls == calls && !rebound_calls); equal(snapshot(), saved);
    last_cycles = get_cycles(); next_sample_evtime = phase; p6_pcm_reset();
    assert(future(mode) == control_q && pcm() == control_pcm); equal(snapshot(), control);
    assert(device_position() == control_position && notices == control_notices && subs == control_subs);
    assert(rebound_calls == int(control_notices.size() + control_subs.size())); device_unbind();
}
static void catalog_restore()
{
    setup(); Fixture a, b, fresh_a, fresh_b;
    auto other_layout = b.disc.layout(); other_layout.content_id[0] = 2;
    Disc second, fresh_second;
    Binding binding{{{2}, 4 * 2448, 1}, provide, &b.provider};
    assert(second.bind(other_layout, &binding, 1) == Result::Ok);
    binding.context = &fresh_b.provider; assert(fresh_second.bind(other_layout, &binding, 1) == Result::Ok);
    const Disc *items[] = {&a.disc, &second}, *fresh[] = {&fresh_a.disc, &fresh_second};
    assert(device_catalog_bind(items, 2) == Result::Ok && sys_command_open(0) == 1);
    assert(sys_command_cd_play(0, 0, 2, 0, status_callback, record_sub));
    advance(int(cda_evt * 100 + 137)); pcm();
    const auto saved = snapshot(); const float phase = next_sample_evtime;
    const auto expected_q = future(1); const auto expected_pcm = pcm(); const auto expected = snapshot();
    assert(device_set_inserted(false) == Result::Ok && device_select(1) == Result::Ok);
    assert(device_set_inserted(true) == Result::Ok && device_bound_disc() == &second);
    const auto untouched = snapshot(); const auto calls = a.provider.calls + b.provider.calls; const auto cycle = get_cycles();
    assert(!device_restore(saved, &second, status_callback, record_sub)); equal(snapshot(), untouched);
    assert(!device_restore(saved, &fresh_a.disc, status_callback, record_sub)); equal(snapshot(), untouched);
    assert(device_select(0) == Result::Invalid && device_catalog_bind(fresh, 2) == Result::Invalid);
    equal(snapshot(), untouched);
    assert(device_can_restore(saved, device_resolve_disc(saved.disc_id), status_callback, record_sub));
    equal(snapshot(), untouched); assert(get_cycles() == cycle && a.provider.calls + b.provider.calls == calls);
    assert(device_restore(saved, device_resolve_disc(saved.disc_id), status_callback, record_sub));
    equal(snapshot(), saved); assert(device_bound_disc() == &a.disc && sys_command_isopen(0) == 1);
    last_cycles = get_cycles(); next_sample_evtime = phase; p6_pcm_reset();
    assert(future(1) == expected_q && pcm() == expected_pcm); equal(snapshot(), expected);
    device_unbind(); setup();
    assert(device_catalog_bind(fresh, 2, 1) == Result::Ok && sys_command_open(0) == 1);
    const auto fresh_calls = fresh_a.provider.calls + fresh_b.provider.calls;
    assert(device_restore(saved, device_resolve_disc(saved.disc_id), status_callback, record_sub));
    assert(fresh_a.provider.calls + fresh_b.provider.calls == fresh_calls && device_bound_disc() == &fresh_a.disc);
    equal(snapshot(), saved); last_cycles = get_cycles(); next_sample_evtime = phase; p6_pcm_reset();
    assert(future(1) == expected_q && pcm() == expected_pcm); equal(snapshot(), expected); device_unbind();
}
int main() { for (int mode = 0; mode < 14; ++mode) replay(mode); catalog_restore(); }
