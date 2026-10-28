// Value-only private CD32 FMV state. No pointer, callback or host resource.
#pragma once
#include "audio_cda_state.h"
#include "fmv_decoder_journal.h"
#include <stdint.h>

constexpr uint32_t P6_FMV_RAM_BYTES = 524288;
constexpr uint32_t P6_FMV_AUDIO_RAM_BYTES = 262144;
constexpr uint32_t P6_FMV_VIDEO_FRAMES = 8;
constexpr uint32_t P6_FMV_VIDEO_BYTES = 352 * 288 * 4;
constexpr uint32_t P6_FMV_PCM_FRAMES = 128;
constexpr uint32_t P6_FMV_PCM_SAMPLES = 1152 * 2;
constexpr uint32_t P6_FMV_CDA_SAMPLES = 4 * 1152 * 2;
constexpr uint32_t P6_FMV_JOURNAL_MAX = 20 + p6_fmv::kJournalEvents * 12 + p6_fmv::kJournalBytes;
constexpr uint32_t P6_FMV_AUX_WIDTH = 1824;
constexpr uint32_t P6_FMV_AUX_HEIGHT = 626;
constexpr uint32_t P6_FMV_AUX_PIXELS = P6_FMV_AUX_WIDTH * P6_FMV_AUX_HEIGHT;

struct p6_fmv_video_frame {
    int32_t width, height, depth;
    uint8_t data[P6_FMV_VIDEO_BYTES];
};
struct p6_fmv_pcm_frame {
    uint32_t ready;
    int16_t pcm[P6_FMV_PCM_SAMPLES];
};
struct p6_fmv_packet {
    uint32_t length, pts_valid;
    uint64_t pts;
};
struct p6_fmv_genlock_state {
    uint32_t allocated, active, width, height, depth, border32, border16;
    uint8_t pixels[P6_FMV_VIDEO_BYTES];
};
struct p6_fmv_composite_layout {
    uint32_t width, height, pixel_bytes;
    uint32_t amiga_stride_bytes, output_stride_bytes;
};
enum p6_fmv_compose_result : int32_t {
    P6_FMV_COMPOSE_OK = 0,
    P6_FMV_COMPOSE_ARGUMENT = 1,
    P6_FMV_COMPOSE_GEOMETRY = 2,
    P6_FMV_COMPOSE_DEPTH = 3,
    P6_FMV_COMPOSE_CAPACITY = 4,
};
struct p6_fmv_context {
    int64_t scheduler_cycle;
    uint32_t pcm_period;
};
struct p6_fmv_aux_state {
    uint32_t selected, initialized, locked, hardwired;
    int32_t width_allocated, height_allocated, outwidth, outheight;
    int32_t inwidth, inheight, inwidth2, inheight2, extrawidth, extraheight;
    int32_t xoffset, yoffset, inxoffset, inyoffset, monitor_id, last_drawn_line;
    uint32_t pixel_width, pixel_height;
    uint32_t pixels[P6_FMV_AUX_PIXELS];
};

struct p6_fmv_state {
    uint32_t version, mapped;
    uint8_t ram[P6_FMV_RAM_BYTES];
    uint8_t audio_ram[P6_FMV_AUDIO_RAM_BYTES];
    uint16_t cl450_regs[256], cl450_imem[1024], cl450_tmem[128], cl450_hmem[16], cl450_vid[16];
    uint32_t cl450_scr_bits, cl450_play, cl450_blank, cl450_border_color;
    uint32_t cl450_interruptmask, cl450_pending_interrupts, cl450_threshold;
    int32_t cl450_buffer_offset, cl450_buffer_empty_cnt, libmpeg_offset;
    uint32_t fmv_bufon[2], fmv_syncadjust_bits, newpacket_mode;
    p6_fmv_packet packets[32];
    int32_t packet_write, packet_read, frame_rate, frame_pixbytes, frame_width, frame_height;
    int32_t video_hsync_wait, videoram_read, videoram_write, videoram_count, frame_count;
    p6_fmv_video_frame video[P6_FMV_VIDEO_FRAMES];
    uint16_t l64111_regs[32], l64111_intmask[2], l64111_intstatus[2], mpeg_io_reg;
    uint8_t l64111_fifo[128];
    int32_t l64111_fifo_count, audio_frame_count, audio_frame_size;
    int32_t audio_frame_detect, audio_head_detect, l64111_cb_mask;
    int32_t audio_data_remaining, audio_skip_size;
    int32_t mp2_id, mp2_v[2][1024], mp2_voffs;
    p6_fmv_pcm_frame pcm[P6_FMV_PCM_FRAMES];
    uint32_t cda_present, cda_streamid, cd_audio_mode_changed;
    p6_cda::State cda_stream;
    int16_t cda_buffers[2][P6_FMV_CDA_SAMPLES];
    uint32_t max_sync_vpos_bits, remaining_sync_vpos_bits;
    p6_fmv_genlock_state genlock;
    p6_fmv_aux_state aux;
    uint32_t journal_size;
    uint8_t journal[P6_FMV_JOURNAL_MAX];
};

// Apply may materialize the authenticated bound cartridge into its fixed banks
// in a fresh image; it never advances the guest or performs host I/O.
bool p6_fmv_state_get(p6_fmv_state *, const char **why = nullptr);
bool p6_fmv_state_validate(const p6_fmv_state *, const p6_fmv_context &);
bool p6_fmv_state_preflight();
bool p6_fmv_state_apply(const p6_fmv_state *, const p6_fmv_context &, const char **why = nullptr);
bool p6_fmv_audio_phase_get(int32_t *, unsigned);
bool p6_fmv_audio_phase_set(const int32_t *, unsigned);
bool p6_fmv_genlock_get(p6_fmv_genlock_state *);
bool p6_fmv_genlock_validate(const p6_fmv_genlock_state *);
bool p6_fmv_genlock_apply(const p6_fmv_genlock_state *);
// Pure bounded software composition over engine-owned input/output storage.
// The full layout and capacities are checked before the first output byte changes.
int p6_fmv_compose(const p6_fmv_composite_layout *, const p6_fmv_genlock_state *,
                   const void *amiga, uint64_t amiga_bytes, void *output, uint64_t output_bytes);
bool p6_fmv_aux_get(p6_fmv_aux_state *);
bool p6_fmv_aux_validate(const p6_fmv_aux_state *);
bool p6_fmv_aux_can_apply(const p6_fmv_aux_state *);
bool p6_fmv_aux_apply(const p6_fmv_aux_state *);
