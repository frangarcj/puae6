// Private Akiko body: fixed-width little endian, no owner pointers or padding.
// Outer chunk/version, scheduler, Paula and verified Disc identity are separate.
#pragma once
#include "state_io/archive.h"
#include "akiko_state.h"
namespace p6_state {
template<class T> inline void akiko_candidate_value(Archive &a, T &v, const char *name)
{
    // Validate fills caller-owned scratch too; never read its uninitialized bytes.
    const T next = a.val<T>(a.mode() == Mode::Save ? v : T{}, name);
    if (a.reading() && a.ok()) v = next;
}
template<class T, size_t N> inline void akiko_candidate_value(Archive &a, T (&v)[N], const char *name)
{
    for (auto &element : v) {
        if (!a.ok()) break;
        akiko_candidate_value(a, element, name);
    }
}
// Save/Measure leave the caller unchanged. Reading mutates ONLY this candidate,
// which may be incomplete after failure and must never be applied in that case.
inline void akiko_candidate_fields(Archive &a, p6_akiko_state &candidate)
{
#define AK_VALUE(object, member) akiko_candidate_value(a, object.member, #object "." #member)
    auto &c = candidate.controller;
    AK_VALUE(c, config); AK_VALUE(c, misc_initialized);
#define AK_CONTROL(type, name) AK_VALUE(c, name);
    P6_AKIKO_CONTROL_FIELDS(AK_CONTROL)
#undef AK_CONTROL
    AK_VALUE(c, input); AK_VALUE(c, result); AK_VALUE(c, command);
    AK_VALUE(c, response); AK_VALUE(c, toc_bytes); AK_VALUE(c, q);
    auto &t = c.toc;
    AK_VALUE(t, first_track); AK_VALUE(t, first_track_offset);
    AK_VALUE(t, last_track); AK_VALUE(t, last_track_offset);
    AK_VALUE(t, firstaddress); AK_VALUE(t, lastaddress); AK_VALUE(t, tracks); AK_VALUE(t, points);
    for (auto &e : t.entry) {
        AK_VALUE(e, adr); AK_VALUE(e, control); AK_VALUE(e, tno); AK_VALUE(e, point);
        AK_VALUE(e, track); AK_VALUE(e, zero); AK_VALUE(e, crc);
        AK_VALUE(e, address); AK_VALUE(e, paddress);
    }
    auto &clock = c.clock;
    AK_VALUE(clock, cycle); AK_VALUE(clock, rate); AK_VALUE(clock, sector);
    AK_VALUE(clock, qcode); AK_VALUE(clock, media); AK_VALUE(clock, first);
    AK_VALUE(clock, q_due); AK_VALUE(clock, media_due); AK_VALUE(clock, speed);
    auto &nv = c.nvram; auto &chip = nv.chip;
    AK_VALUE(chip, state); AK_VALUE(chip, last_data); AK_VALUE(chip, last_clock);
    AK_VALUE(chip, device_out); AK_VALUE(chip, buffer); AK_VALUE(chip, estate);
    AK_VALUE(chip, current_addr); AK_VALUE(chip, eeprom_addr); AK_VALUE(chip, write_offset);
    AK_VALUE(chip, memory); AK_VALUE(nv, direction); AK_VALUE(nv, scl);
    AK_VALUE(nv, sda); AK_VALUE(nv, reserved);
    auto &transport = candidate.transport;
    AK_VALUE(transport, service); AK_VALUE(transport, mute_pending);
    AK_VALUE(transport, read); AK_VALUE(transport, write); AK_VALUE(transport, muted);
    AK_VALUE(transport, head); AK_VALUE(transport, words); AK_VALUE(transport, info);
    AK_VALUE(transport, sector); AK_VALUE(transport, sub_read); AK_VALUE(transport, sub_write);
    AK_VALUE(transport, sub_used); AK_VALUE(transport, sub);
    auto &device = candidate.device; auto &audio = device.audio; auto &stream = audio.stream;
    AK_VALUE(audio, delay); AK_VALUE(audio, pcm); AK_VALUE(audio, disc_id);
    AK_VALUE(stream, active); AK_VALUE(stream, paused); AK_VALUE(stream, frames);
    AK_VALUE(stream, offset); AK_VALUE(stream, remaining); AK_VALUE(stream, event_cycles);
    AK_VALUE(stream, period); AK_VALUE(stream, userdata); AK_VALUE(stream, volume);
    for (auto &channel : stream.channel) {
        AK_VALUE(channel, current); AK_VALUE(channel, previous);
        AK_VALUE(channel, accumulator); AK_VALUE(channel, cycles);
    }
    AK_VALUE(audio, bound); AK_VALUE(audio, running); AK_VALUE(audio, sector);
    AK_VALUE(audio, callbacks); AK_VALUE(audio, position); AK_VALUE(audio, end);
    AK_VALUE(audio, left); AK_VALUE(audio, right); AK_VALUE(audio, status); AK_VALUE(audio, error);
    AK_VALUE(device, disc_id); AK_VALUE(device, bound); AK_VALUE(device, opens);
    AK_VALUE(device, position); AK_VALUE(device, audio_head); AK_VALUE(device, terminal_ack); AK_VALUE(device, error);
#undef AK_VALUE
}
// No allocation, I/O, time advancement or destination-owner access. The caller
// supplies ordinary heap scratch and must discard it if this returns false.
inline bool akiko_candidate_decode(const uint8_t *data, uint64_t size,
                                   p6_akiko_state &candidate, const p6_akiko_context &context)
{
    if (!data) return false;
    auto a = Archive::validator(data, size);
    akiko_candidate_fields(a, candidate);
    a.finish();
    return a.ok() && p6_akiko_state_validate(&candidate, context);
}
}
