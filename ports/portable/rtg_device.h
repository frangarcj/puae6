/* Bounded software half of the PUAE6 UAE Zorro III RTG device.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * This is deliberately independent of the host presentation API and of Amiga
 * Autoconfig/traps.  The latter translate guest requests to this checked
 * surface; no host pointer is retained in semantic state.
 */
#ifndef P6_RTG_DEVICE_H
#define P6_RTG_DEVICE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
	P6_RTG_VRAM_BYTES = 4 * 1024 * 1024,
	P6_RTG_APERTURE_BYTES = 16 * 1024 * 1024,
	P6_RTG_MAX_WIDTH = 1024,
	P6_RTG_MAX_HEIGHT = 768,
	P6_RTG_MAX_STRIDE_BYTES = 4096,
	P6_RTG_OUTPUT_STRIDE_BYTES = P6_RTG_MAX_WIDTH * 4,
	P6_RTG_OUTPUT_BYTES = P6_RTG_OUTPUT_STRIDE_BYTES * P6_RTG_MAX_HEIGHT,
	P6_RTG_MODE_COUNT = 9,
	P6_RTG_PALETTE_SIZE = 256,
	P6_RTG_SNAPSHOT_VERSION = 1
};

/* Values deliberately match WinUAE 6.0.3 include/rtgmodes.h. */
enum p6_rtg_format {
	P6_RTG_RGBFB_CLUT = 1,
	P6_RTG_RGBFB_R5G6B5PC = 4,
	P6_RTG_RGBFB_A8R8G8B8 = 6
};

enum p6_rtg_result {
	P6_RTG_OK = 0,
	P6_RTG_BAD_ARGUMENT = -1,
	P6_RTG_BAD_STATE = -2,
	P6_RTG_UNSUPPORTED = -3,
	P6_RTG_BOUNDS = -4
};

typedef struct p6_rtg_mode {
	uint32_t width;
	uint32_t height;
	uint32_t format;
	uint32_t bytes_per_pixel;
} p6_rtg_mode;

/* offset is relative to the populated 4 MiB, never to a host address or to
 * the unpopulated part of the 16 MiB Zorro III aperture. */
typedef struct p6_rtg_surface {
	uint32_t offset;
	uint32_t stride_bytes;
	uint32_t width;
	uint32_t height;
	uint32_t format;
} p6_rtg_surface;

typedef struct p6_rtg_view {
	p6_rtg_mode mode;
	p6_rtg_surface surface;
	uint32_t x_offset;
	uint32_t y_offset;
	uint32_t display_enabled;
	uint32_t active;
} p6_rtg_view;

typedef struct p6_rtg_frame {
	const uint32_t *pixels; /* host-endian 0x00RRGGBB */
	uint32_t width;
	uint32_t height;
	uint32_t stride_bytes;
	uint64_t frame_counter;
} p6_rtg_frame;

/* Semantic device state only. VRAM is serialized separately through
 * p6_rtg_vram_data(); the XRGB output and have-frame latch are derived and
 * deliberately invalidated on apply. Every member is fixed-width and callers
 * must still serialize fields individually rather than copying this C struct. */
typedef struct p6_rtg_snapshot {
	uint32_t version;
	p6_rtg_view view;
	uint32_t palette[P6_RTG_PALETTE_SIZE];
	uint64_t frame_counter;
} p6_rtg_snapshot;

void p6_rtg_reset(void);
int p6_rtg_rebind(void);
int p6_rtg_mode_at(uint32_t index, p6_rtg_mode *out);
int p6_rtg_set_mode(uint32_t width, uint32_t height, uint32_t format);
int p6_rtg_set_panning(const p6_rtg_surface *surface, uint32_t x, uint32_t y);
int p6_rtg_set_display(uint32_t enabled);
int p6_rtg_set_palette(uint32_t first, uint32_t count, const uint32_t *xrgb8888);
int p6_rtg_fill(const p6_rtg_surface *surface, uint32_t x, uint32_t y,
	uint32_t width, uint32_t height, uint32_t pixel);
int p6_rtg_blit(const p6_rtg_surface *source, uint32_t source_x, uint32_t source_y,
	const p6_rtg_surface *destination, uint32_t destination_x, uint32_t destination_y,
	uint32_t width, uint32_t height);
int p6_rtg_present(void);
int p6_rtg_frame_get(p6_rtg_frame *out);
int p6_rtg_view_get(p6_rtg_view *out);
int p6_rtg_snapshot_get(p6_rtg_snapshot *out);
int p6_rtg_snapshot_validate(const p6_rtg_snapshot *snapshot);
int p6_rtg_snapshot_apply(const p6_rtg_snapshot *snapshot);

/* Engine memory-bank access. The storage has static lifetime and exactly the
 * populated size; callers must still enforce their own bus-width semantics. */
uint8_t *p6_rtg_vram_data(void);
size_t p6_rtg_vram_size(void);

#ifdef __cplusplus
}
#endif
#endif
