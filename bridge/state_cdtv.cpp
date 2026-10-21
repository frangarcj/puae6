// Complete private CDTV body; shared scheduling, RAM and Paula precede it.
#include "state.h"
#include "session.h"
#include "cdtv_state.h"
#include "media/cd_device.h"
#include <memory>
namespace p6_state {
namespace {
template<class T> void value(Archive &a, T &v, const char *name)
{
    auto next = a.val<T>(a.mode() == Mode::Save ? v : T{}, name);
    if (a.reading() && a.ok()) v = next;
}
template<class T, size_t N> void value(Archive &a, T (&v)[N], const char *name)
{
    for (auto &element : v) value(a, element, name);
}
}
static void fields(Archive &a, p6_cdtv_state &c)
{
    value(a, c.configured, "cdtv.configured");
    value(a, c.cdtvscsi, "cdtv.cdtvscsi");
    value(a, c.unitnum, "cdtv.unitnum");
    value(a, c.dma_mask, "cdtv.dma_mask");
    value(a, c.service_running, "cdtv.service_running");
    value(a, c.callback_mode, "cdtv.callback_mode");
    value(a, c.request_size, "cdtv.request_size");
    value(a, c.dmac_istr, "cdtv.dmac_istr");
    value(a, c.dmac_cntr, "cdtv.dmac_cntr");
    value(a, c.dmac_dawr, "cdtv.dmac_dawr");
    value(a, c.dmac_acr, "cdtv.dmac_acr");
    value(a, c.dmac_wtc, "cdtv.dmac_wtc");
    value(a, c.dmac_dma, "cdtv.dmac_dma");
    value(a, c.activate_stch, "cdtv.activate_stch");
    value(a, c.cdrom_command_done, "cdtv.cdrom_command_done");
    value(a, c.cd_playing, "cdtv.cd_playing");
    value(a, c.cd_paused, "cdtv.cd_paused");
    value(a, c.cd_motor, "cdtv.cd_motor");
    value(a, c.cd_media, "cdtv.cd_media");
    value(a, c.cd_error, "cdtv.cd_error");
    value(a, c.cd_finished, "cdtv.cd_finished");
    value(a, c.cd_isready, "cdtv.cd_isready");
    value(a, c.cd_audio_finished, "cdtv.cd_audio_finished");
    value(a, c.cdrom_sector, "cdtv.cdrom_sector");
    value(a, c.cdrom_sectors, "cdtv.cdrom_sectors");
    value(a, c.cdrom_length, "cdtv.cdrom_length");
    value(a, c.cdrom_offset, "cdtv.cdrom_offset");
    value(a, c.last_cd_position, "cdtv.last_cd_position");
    value(a, c.play_start, "cdtv.play_start");
    value(a, c.play_end, "cdtv.play_end");
    value(a, c.last_play_pos, "cdtv.last_play_pos");
    value(a, c.last_play_end, "cdtv.last_play_end");
    value(a, c.datatrack, "cdtv.datatrack");
    value(a, c.cd_audio_status, "cdtv.cd_audio_status");
    value(a, c.cd_volume, "cdtv.cd_volume");
    value(a, c.cd_volume_stored, "cdtv.cd_volume_stored");
    value(a, c.dac_control_data_format, "cdtv.dac_control_data_format");
    value(a, c.cd_led, "cdtv.cd_led");
    value(a, c.frontpanel, "cdtv.frontpanel");
    value(a, c.cdtv_hsync, "cdtv.cdtv_hsync");
    value(a, c.dma_finished, "cdtv.dma_finished");
    value(a, c.cdtv_sectorsize, "cdtv.cdtv_sectorsize");
    value(a, c.dma_wait, "cdtv.dma_wait");
    value(a, c.seek_deadline, "cdtv.seek_deadline");
    value(a, c.cdrom_command_cnt_in, "cdtv.cdrom_command_cnt_in");
    value(a, c.cdrom_command_cnt_out, "cdtv.cdrom_command_cnt_out");
    value(a, c.cdrom_command_size_out, "cdtv.cdrom_command_size_out");
    value(a, c.tp_a, "cdtv.tp_a");
    value(a, c.tp_b, "cdtv.tp_b");
    value(a, c.tp_c, "cdtv.tp_c");
    value(a, c.tp_ad, "cdtv.tp_ad");
    value(a, c.tp_bd, "cdtv.tp_bd");
    value(a, c.tp_cd, "cdtv.tp_cd");
    value(a, c.tp_imask, "cdtv.tp_imask");
    value(a, c.tp_cr, "cdtv.tp_cr");
    value(a, c.tp_air, "cdtv.tp_air");
    value(a, c.tp_ilatch, "cdtv.tp_ilatch");
    value(a, c.tp_ilatch2, "cdtv.tp_ilatch2");
    value(a, c.volstrobe1, "cdtv.volstrobe1");
    value(a, c.volstrobe2, "cdtv.volstrobe2");
    value(a, c.stch, "cdtv.stch");
    value(a, c.cmd, "cdtv.cmd");
    value(a, c.enable, "cdtv.enable");
    value(a, c.xaen, "cdtv.xaen");
    value(a, c.dten, "cdtv.dten");
    value(a, c.sten, "cdtv.sten");
    value(a, c.scor, "cdtv.scor");
    value(a, c.sbcp, "cdtv.sbcp");
    value(a, c.subcodebufferoffset, "cdtv.subcodebufferoffset");
    value(a, c.subcodebufferoffsetw, "cdtv.subcodebufferoffsetw");
    value(a, c.subcodeoffset, "cdtv.subcodeoffset");
    value(a, c.subcode_activecnt, "cdtv.subcode_activecnt");
    value(a, c.subqcnt, "cdtv.subqcnt");
    value(a, c.subchannelcounter, "cdtv.subchannelcounter");
    value(a, c.last_out, "cdtv.last_out");
    value(a, c.queue, "cdtv.queue");
    value(a, c.input, "cdtv.input");
    value(a, c.output, "cdtv.output");
    value(a, c.q, "cdtv.q");
    value(a, c.nvram, "cdtv.nvram");
    value(a, c.sub_used, "cdtv.sub_used");
    value(a, c.sub, "cdtv.sub");
    value(a, c.transfer, "cdtv.transfer");
    value(a, c.toc.first_track, "cdtv.toc.first_track");
    value(a, c.toc.first_track_offset, "cdtv.toc.first_track_offset");
    value(a, c.toc.last_track, "cdtv.toc.last_track");
    value(a, c.toc.last_track_offset, "cdtv.toc.last_track_offset");
    value(a, c.toc.firstaddress, "cdtv.toc.firstaddress");
    value(a, c.toc.lastaddress, "cdtv.toc.lastaddress");
    value(a, c.toc.tracks, "cdtv.toc.tracks");
    value(a, c.toc.points, "cdtv.toc.points");
    for (auto &e : c.toc.entry) {
        value(a, e.adr, "cdtv.toc.adr");
        value(a, e.control, "cdtv.toc.control");
        value(a, e.tno, "cdtv.toc.tno");
        value(a, e.point, "cdtv.toc.point");
        value(a, e.track, "cdtv.toc.track");
        value(a, e.zero, "cdtv.toc.zero");
        value(a, e.crc, "cdtv.toc.crc");
        value(a, e.address, "cdtv.toc.address");
        value(a, e.paddress, "cdtv.toc.paddress");
    }
    auto &d = c.device; auto &audio = d.audio; auto &stream = audio.stream;
    value(a, d.disc_id, "cdtv.d.disc_id");
    value(a, d.bound, "cdtv.d.bound");
    value(a, d.inserted, "cdtv.d.inserted");
    value(a, d.opens, "cdtv.d.opens");
    value(a, d.position, "cdtv.d.position");
    value(a, d.audio_head, "cdtv.d.audio_head");
    value(a, d.terminal_ack, "cdtv.d.terminal_ack");
    value(a, d.error, "cdtv.d.error");
    value(a, audio.delay, "cdtv.audio.delay");
    value(a, audio.pcm, "cdtv.audio.pcm");
    value(a, audio.disc_id, "cdtv.audio.disc_id");
    value(a, audio.bound, "cdtv.audio.bound");
    value(a, audio.running, "cdtv.audio.running");
    value(a, audio.sector, "cdtv.audio.sector");
    value(a, audio.callbacks, "cdtv.audio.callbacks");
    value(a, audio.position, "cdtv.audio.position");
    value(a, audio.end, "cdtv.audio.end");
    value(a, audio.left, "cdtv.audio.left");
    value(a, audio.right, "cdtv.audio.right");
    value(a, audio.status, "cdtv.audio.status");
    value(a, audio.error, "cdtv.audio.error");
    value(a, stream.active, "cdtv.stream.active");
    value(a, stream.paused, "cdtv.stream.paused");
    value(a, stream.frames, "cdtv.stream.frames");
    value(a, stream.offset, "cdtv.stream.offset");
    value(a, stream.remaining, "cdtv.stream.remaining");
    value(a, stream.event_cycles, "cdtv.stream.event_cycles");
    value(a, stream.period, "cdtv.stream.period");
    value(a, stream.userdata, "cdtv.stream.userdata");
    value(a, stream.volume, "cdtv.stream.volume");
    for (auto &channel : stream.channel) {
        value(a, channel.current, "cdtv.channel.current"); value(a, channel.previous, "cdtv.channel.previous");
        value(a, channel.accumulator, "cdtv.channel.accumulator"); value(a, channel.cycles, "cdtv.channel.cycles");
    }
}
void visit_cdtv(Archive &a)
{
    std::unique_ptr<p6_cdtv_state> state(new (std::nothrow) p6_cdtv_state{});
    a.require(state != nullptr, "CDTV staging allocation failed"); if (!a.ok()) return; auto &c = *state;
    if (a.mode() == Mode::Save) a.require(p6_cdtv_state_get(&c), "CDTV owner cannot be captured");
    fields(a, c);
    if (a.mode() == Mode::Measure) return;
    a.finish(); if (!a.ok()) return;
    const uint32_t ntsc = p6_session::session.config.video_standard == P6_VIDEO_NTSC;
    const p6_cdtv_context context{scheduler_cycle(a.mode()), ntsc, ntsc ? 41558u : 41179u, p6_cd::device_resolve_disc(c.device.disc_id), cdtv_bus_phase(a.mode())};
    a.require(p6_cdtv_state_validate(&c, context), "CDTV candidate is invalid");
    if (a.applying() && a.ok()) a.require(p6_cdtv_state_apply(&c, context), "CDTV destination is unavailable");
}
const char *cdtv_matches_destination(const uint8_t *body, uint64_t size, MediaPlan &)
{
    std::unique_ptr<p6_cdtv_state> state(new (std::nothrow) p6_cdtv_state{}); if (!state) return "CDTV staging allocation failed";
    auto archive = Archive::validator(body, size); fields(archive, *state); archive.finish();
    return archive.ok() && p6_cdtv_state_preflight(state.get()) ? nullptr : "CDTV destination bank is foreign";
}
}
