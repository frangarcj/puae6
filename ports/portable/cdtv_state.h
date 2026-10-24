// Private semantic values. Shared scheduler/Paula and a verified Disc are external.
#pragma once
#include "media/cd_device_state.h"
struct p6_cdtv_state {
    int32_t configured;
    int32_t cdtvscsi;
    int32_t unitnum;
    uint32_t dma_mask;
    int32_t service_running;
    uint32_t callback_mode;
    uint32_t request_size;
    int32_t dmac_istr;
    int32_t dmac_cntr;
    int32_t dmac_dawr;
    uint32_t dmac_acr;
    int32_t dmac_wtc;
    int32_t dmac_dma;
    int32_t activate_stch;
    int32_t cdrom_command_done;
    int32_t cd_playing;
    int32_t cd_paused;
    int32_t cd_motor;
    int32_t cd_media;
    int32_t cd_error;
    int32_t cd_finished;
    int32_t cd_isready;
    int32_t cd_audio_finished;
    int32_t cdrom_sector;
    int32_t cdrom_sectors;
    int32_t cdrom_length;
    int32_t cdrom_offset;
    uint32_t last_cd_position;
    uint32_t play_start;
    uint32_t play_end;
    uint32_t last_play_pos;
    uint32_t last_play_end;
    int32_t datatrack;
    int32_t cd_audio_status;
    int32_t cd_volume;
    int32_t cd_volume_stored;
    int32_t dac_control_data_format;
    int32_t cd_led;
    int32_t frontpanel;
    int32_t cdtv_hsync;
    int32_t dma_finished;
    int32_t cdtv_sectorsize;
    uint64_t dma_wait;
    uint64_t seek_deadline;
    int32_t cdrom_command_cnt_in;
    int32_t cdrom_command_cnt_out;
    int32_t cdrom_command_size_out;
    int32_t tp_a;
    int32_t tp_b;
    int32_t tp_c;
    int32_t tp_ad;
    int32_t tp_bd;
    int32_t tp_cd;
    int32_t tp_imask;
    int32_t tp_cr;
    int32_t tp_air;
    int32_t tp_ilatch;
    int32_t tp_ilatch2;
    int32_t volstrobe1;
    int32_t volstrobe2;
    int32_t stch;
    int32_t cmd;
    int32_t enable;
    int32_t xaen;
    int32_t dten;
    int32_t sten;
    int32_t scor;
    int32_t sbcp;
    int32_t subcodebufferoffset;
    int32_t subcodebufferoffsetw;
    int32_t subcodeoffset;
    int32_t subcode_activecnt;
    int32_t subqcnt;
    int32_t subchannelcounter;
    int32_t last_out;
    uint32_t queue[100];
    uint8_t input[16], output[16], q[16], nvram[32768];
    uint8_t sub_used[36], sub[36][96], transfer[96];
    struct Entry { uint8_t adr, control, tno, point, track, zero, crc[2]; int32_t address, paddress; };
    struct Toc { int32_t first_track, first_track_offset, last_track, last_track_offset, firstaddress, lastaddress, tracks, points; Entry entry[103]; } toc;
    p6_cd::DeviceState device;
};
namespace p6_cd { class Disc; }
struct p6_cdtv_context { int64_t cycle; uint32_t ntsc, cda_period; const p6_cd::Disc *disc; uint32_t bus_phase = 0; };
bool p6_cdtv_state_get(p6_cdtv_state *);
bool p6_cdtv_state_validate(const p6_cdtv_state *, const p6_cdtv_context &);
bool p6_cdtv_state_preflight(const p6_cdtv_state *); // Runtime destination only; no reset or mutation.
bool p6_cdtv_state_apply(const p6_cdtv_state *, const p6_cdtv_context &);
uint64_t p6_cdtv_callback_observations(); // Diagnostic only, never state.
void (*p6_cdtv_subcode_handler())(uint32_t); // Stable callback binding, never serialized.

bool p6_cdtv_media_absent(); // Pure quiescent query: the controller has observed removal.
