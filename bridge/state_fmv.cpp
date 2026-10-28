// Private CD32 FMV body. Scheduler, Paula and Akiko apply before this owner.
#include "state.h"
#include "config.h"
#include "session.h"
#include "fmv_state.h"
#include <memory>
#include <new>

namespace p6_state {
namespace {

template<class T> void value(Archive &a, T &v, const char *name)
{
    const T next = a.val<T>(a.mode() == Mode::Save ? v : T{}, name);
    if (a.reading() && a.ok()) v = next;
}

template<class T, size_t N> void values(Archive &a, T (&v)[N], const char *name)
{
    for (auto &element : v) {
        if (!a.ok()) break;
        value(a, element, name);
    }
}

template<class T, size_t N, size_t M> void values(Archive &a, T (&v)[N][M], const char *name)
{
    for (auto &row : v) {
        if (!a.ok()) break;
        values(a, row, name);
    }
}

// Opaque byte planes need not be populated during Validate: their bounds are
// structural and Apply decodes the same body into a fresh candidate.
void opaque(Archive &a, uint8_t *bytes, uint64_t size, const char *name)
{
    a.bytes(bytes, size, name);
}

void stream_fields(Archive &a, p6_cda::State &s)
{
#define V(member) value(a, s.member, "fmv.cda." #member)
    V(active); V(paused); V(frames); V(offset); V(remaining); V(event_cycles);
    V(period); V(userdata); values(a, s.volume, "fmv.cda.volume");
    for (auto &channel : s.channel) {
        value(a, channel.current, "fmv.cda.channel.current");
        value(a, channel.previous, "fmv.cda.channel.previous");
        value(a, channel.accumulator, "fmv.cda.channel.accumulator");
        value(a, channel.cycles, "fmv.cda.channel.cycles");
    }
#undef V
}

void candidate_fields(Archive &a, p6_fmv_state &s)
{
#define V(member) value(a, s.member, "fmv." #member)
#define VS(member) values(a, s.member, "fmv." #member)
    V(version); V(mapped);
    opaque(a, s.ram, sizeof s.ram, "fmv.ram");
    opaque(a, s.audio_ram, sizeof s.audio_ram, "fmv.audio_ram");
    VS(cl450_regs); VS(cl450_imem); VS(cl450_tmem); VS(cl450_hmem); VS(cl450_vid);
    V(cl450_scr_bits); V(cl450_play); V(cl450_blank); V(cl450_border_color);
    V(cl450_interruptmask); V(cl450_pending_interrupts); V(cl450_threshold);
    V(cl450_buffer_offset); V(cl450_buffer_empty_cnt); V(libmpeg_offset);
    VS(fmv_bufon); V(fmv_syncadjust_bits); V(newpacket_mode);
    for (auto &packet : s.packets) {
        value(a, packet.length, "fmv.packet.length");
        value(a, packet.pts_valid, "fmv.packet.pts_valid");
        value(a, packet.pts, "fmv.packet.pts");
    }
    V(packet_write); V(packet_read); V(frame_rate); V(frame_pixbytes); V(frame_width); V(frame_height);
    V(video_hsync_wait); V(videoram_read); V(videoram_write); V(videoram_count); V(frame_count);
    for (auto &frame : s.video) {
        value(a, frame.width, "fmv.video.width"); value(a, frame.height, "fmv.video.height");
        value(a, frame.depth, "fmv.video.depth");
        const uint64_t used = frame.width > 0 && frame.height > 0 && frame.depth > 0
            ? uint64_t(frame.width) * uint64_t(frame.height) * uint64_t(frame.depth) : 0;
        opaque(a, frame.data, a.mode() == Mode::Measure ? sizeof frame.data : used, "fmv.video.data");
    }
    VS(l64111_regs); VS(l64111_intmask); VS(l64111_intstatus); V(mpeg_io_reg);
    opaque(a, s.l64111_fifo, sizeof s.l64111_fifo, "fmv.l64111_fifo");
    V(l64111_fifo_count); V(audio_frame_count); V(audio_frame_size); V(audio_frame_detect); V(audio_head_detect);
    V(l64111_cb_mask); V(audio_data_remaining); V(audio_skip_size); V(mp2_id); VS(mp2_v); V(mp2_voffs);
    for (auto &frame : s.pcm) {
        value(a, frame.ready, "fmv.pcm.ready");
        if (a.mode() == Mode::Measure || frame.ready) values(a, frame.pcm, "fmv.pcm.samples");
    }
    V(cda_present); V(cda_streamid); V(cd_audio_mode_changed); stream_fields(a, s.cda_stream);
    if (a.mode() == Mode::Measure || s.cda_present) values(a, s.cda_buffers, "fmv.cda.buffers");
    V(max_sync_vpos_bits); V(remaining_sync_vpos_bits);
    V(genlock.allocated); V(genlock.active); V(genlock.width); V(genlock.height); V(genlock.depth);
    V(genlock.border32); V(genlock.border16);
    {
        const uint64_t used = s.genlock.width && s.genlock.height && s.genlock.depth
            ? uint64_t(s.genlock.width) * uint64_t(s.genlock.height) * uint64_t(s.genlock.depth) : 0;
        opaque(a, s.genlock.pixels, a.mode() == Mode::Measure ? sizeof s.genlock.pixels : used, "fmv.genlock.pixels");
    }
    V(aux.selected); V(aux.initialized); V(aux.locked); V(aux.hardwired);
    V(aux.width_allocated); V(aux.height_allocated); V(aux.outwidth); V(aux.outheight);
    V(aux.inwidth); V(aux.inheight); V(aux.inwidth2); V(aux.inheight2); V(aux.extrawidth); V(aux.extraheight);
    V(aux.xoffset); V(aux.yoffset); V(aux.inxoffset); V(aux.inyoffset); V(aux.monitor_id); V(aux.last_drawn_line);
    V(aux.pixel_width); V(aux.pixel_height);
    {
        const uint64_t used = uint64_t(s.aux.pixel_width) * uint64_t(s.aux.pixel_height);
        const uint64_t count = a.mode() == Mode::Measure ? P6_FMV_AUX_PIXELS : used;
        for (uint64_t n = 0; n < count && a.ok(); ++n) value(a, s.aux.pixels[n], "fmv.aux.pixels");
    }
    V(journal_size);
    const uint32_t journal = a.mode() == Mode::Measure ? P6_FMV_JOURNAL_MAX : s.journal_size;
    // Validate must materialize the journal because decoder replay is part of
    // candidate validation. Save/Apply can keep the byte run compact.
    if (a.mode() == Mode::Validate) {
        for (uint32_t n = 0; n < journal && a.ok(); ++n) value(a, s.journal[n], "fmv.journal");
    } else {
        opaque(a, s.journal, journal, "fmv.journal");
    }
#undef VS
#undef V
}

} // namespace

void visit_fmv(Archive &a)
{
    std::unique_ptr<p6_fmv_state> candidate(new (std::nothrow) p6_fmv_state{});
    a.require(candidate != nullptr, "FMV staging allocation failed");
    if (!a.ok()) return;
    if (a.mode() == Mode::Save) {
        const char *why = nullptr;
        a.require(p6_fmv_state_get(candidate.get(), &why), why ? why : "FMV cannot be captured");
        if (!a.ok()) return;
    }
    candidate_fields(a, *candidate);
    if (a.mode() == Mode::Measure) return;
    a.finish();
    if (!a.ok()) return;
    const bool ntsc = p6_session::session.config.video_standard == P6_VIDEO_NTSC;
    const p6_fmv_context context{scheduler_cycle(a.mode()), ntsc ? 41558u : 41179u};
    a.require(context.scheduler_cycle >= 0 && p6_fmv_state_validate(candidate.get(), context),
              "FMV candidate is invalid");
    if (a.applying() && a.ok()) {
        const char *why = nullptr;
        if (!p6_fmv_state_apply(candidate.get(), context, &why)) a.fail(why ? why : "FMV destination is unavailable");
    }
}

} // namespace p6_state
