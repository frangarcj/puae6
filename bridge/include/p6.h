#ifndef P6_H_INCLUDED
#define P6_H_INCLUDED

/* Native PUAE6 C ABI. This header does not expose engine or C++ types. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define P6_ABI_VERSION  UINT32_C(1)
#define P6_ERROR_TEXT_CAPACITY 256u
#define P6_CONTENT_ID_SIZE 32u
#define P6_MAX_MEDIA 16u

typedef int32_t p6_result;
#define P6_OK                    INT32_C(0)
#define P6_ERROR_BAD_ARGUMENT    INT32_C(1)
#define P6_ERROR_BAD_STATE       INT32_C(2)
#define P6_ERROR_BUSY            INT32_C(3)
#define P6_ERROR_UNSUPPORTED     INT32_C(4)
#define P6_ERROR_NEED_FIRMWARE   INT32_C(5)
#define P6_ERROR_WRONG_MEDIA     INT32_C(6)
#define P6_ERROR_CAPACITY        INT32_C(7)
#define P6_ERROR_IO              INT32_C(8)
#define P6_ERROR_CORRUPT_STATE   INT32_C(9)
#define P6_ERROR_INCOMPATIBLE    INT32_C(10)
#define P6_ERROR_INTERNAL        INT32_C(11)

typedef struct p6_error {
    uint32_t abi_version;
    uint32_t struct_size;
    char message[P6_ERROR_TEXT_CAPACITY];
} p6_error;

/* Capability bits describe implemented behavior, not planned extensions. */
typedef struct p6_caps {
    uint32_t abi_version;
    uint32_t struct_size;
    uint64_t flags;
    uint32_t max_media;
    uint32_t max_video_width;
    uint32_t max_video_height;
    uint32_t max_audio_frames;
} p6_caps;
#define P6_CAP_STATE            (UINT64_C(1) << 0)
#define P6_CAP_MEMORY_REGIONS   (UINT64_C(1) << 1)
#define P6_CAP_MEDIA_EVENTS     (UINT64_C(1) << 2)
#define P6_CAP_LAG_FLAG         (UINT64_C(1) << 3)

typedef struct p6_config {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t machine;           /* P6_MACHINE_A500 only in ABI v1. */
    uint32_t video_standard;    /* P6_VIDEO_PAL or P6_VIDEO_NTSC. */
    uint32_t audio_rate_hz;
    uint32_t flags;             /* Must be zero in ABI v1. */
    uint64_t random_seed;
    int64_t rtc_epoch_seconds;  /* UTC epoch; never read from host clock. */
    uint64_t reserved[4];
} p6_config;
#define P6_MACHINE_A500 UINT32_C(1)
#define P6_VIDEO_PAL    UINT32_C(1)
#define P6_VIDEO_NTSC   UINT32_C(2)

/* ABI v1 fixes A500 memory at 512 KiB Chip RAM and 512 KiB Slow RAM. */
typedef struct p6_buffer {
    const void *data;
    uint64_t bytes;
} p6_buffer;

/* image data is borrowed and immutable through p6_unload(); bytes is exact. */
typedef struct p6_media {
    uint32_t type;
    uint32_t flags;             /* Must be zero in ABI v1. */
    uint8_t content_id[P6_CONTENT_ID_SIZE];
    p6_buffer image;
} p6_media;
#define P6_MEDIA_ADF UINT32_C(1)

typedef struct p6_port_input {
    uint32_t type;
    uint32_t buttons;
    int16_t dx;
    int16_t dy;
    uint32_t reserved;
} p6_port_input;
#define P6_PORT_NONE      UINT32_C(0)
#define P6_PORT_JOYSTICK  UINT32_C(1)
#define P6_PORT_MOUSE     UINT32_C(2)
#define P6_BUTTON_1       (UINT32_C(1) << 0)
#define P6_BUTTON_2       (UINT32_C(1) << 1)
#define P6_BUTTON_3       (UINT32_C(1) << 2)
#define P6_BUTTON_UP      (UINT32_C(1) << 3)
#define P6_BUTTON_DOWN    (UINT32_C(1) << 4)
#define P6_BUTTON_LEFT    (UINT32_C(1) << 5)
#define P6_BUTTON_RIGHT   (UINT32_C(1) << 6)

/* keys is a 128-key Amiga key bitmap; key n is keys[n / 32] bit (n % 32).
 * Bits are key positions, not text. For P6_PORT_NONE all fields are zero.
 * dx/dy are signed int16 relative deltas and are meaningful only for mouse. */
typedef struct p6_input {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t keys[4];
    p6_port_input ports[2];
    uint32_t reserved[4];
} p6_input;

typedef struct p6_rect {
    uint32_t x, y, width, height;
} p6_rect;

/* Frame pointers are borrowed until the next mutating p6_* call. Audio count
 * is stereo frames (interleaved int16 samples), not individual samples.
 *
 * Video. video_width x video_height is the delivered buffer and is constant for a
 * loaded image (756x576 PAL, 756x486 NTSC, see p6_caps); video_stride_bytes is the
 * row pitch, never width * 4. Rows and columns outside `active` are zero. `active`
 * is the raster the renderer drew for this unit and can be smaller than the buffer
 * (PAL 756x574 normally, 756x576 for a guest that leaves the long-field bit set).
 * aspect_num/aspect_den is the PIXEL aspect ratio (width : height of one pixel of
 * the buffer), PAL 26:25 and NTSC 43:50; the picture aspect is
 * active.width * num : active.height * den. A unit is one video field. field_parity
 * is P6_FIELD_PROGRESSIVE unless the monitor detected interlace; then EVEN when the
 * unit drew rows 0,2,4.. of the buffer (the Amiga long field) and ODD for rows
 * 1,3,5.. The buffer always holds both fields (a weave). cadence_num/cadence_den is
 * the nominal number of such units per second for the unit's own mode, as integers;
 * the exact spacing of units is master_ticks. When a unit has no picture to show
 * (the renderer discarded it while changing mode, or P6_RENDER_VIDEO was not
 * requested) video is NULL, video_bytes is zero and P6_FRAME_NO_VIDEO is set. */
typedef struct p6_frame {
    uint32_t abi_version;
    uint32_t struct_size;
    const void *video;
    uint64_t video_bytes;
    uint32_t video_format;      /* P6_PIXEL_XRGB8888. */
    uint32_t video_width;
    uint32_t video_height;
    uint32_t video_stride_bytes;
    p6_rect active;
    uint32_t aspect_num;
    uint32_t aspect_den;
    const int16_t *audio;
    uint64_t audio_frames;
    uint64_t master_ticks;
    uint64_t unit_index;
    uint32_t cadence_num;        /* video units per second = num / den. */
    uint32_t cadence_den;
    uint32_t field_parity;       /* P6_FIELD_PROGRESSIVE/EVEN/ODD. */
    uint32_t flags;
    uint64_t reserved[2];
} p6_frame;
#define P6_PIXEL_XRGB8888 UINT32_C(1)
#define P6_FIELD_PROGRESSIVE UINT32_C(0)
#define P6_FIELD_EVEN        UINT32_C(1)
#define P6_FIELD_ODD         UINT32_C(2)
#define P6_FRAME_LAG         (UINT32_C(1) << 0)
#define P6_FRAME_NO_VIDEO    (UINT32_C(1) << 1)
#define P6_RENDER_VIDEO      (UINT32_C(1) << 0)
#define P6_RENDER_AUDIO      (UINT32_C(1) << 1)

/* Extensions use caller-owned bounded buffers. State format is native to the
 * exact compatible build/config/media; it is not a Waterbox snapshot or a
 * cross-build interchange format. Region data is borrowed until next mutation. */
typedef struct p6_memory_region {
    uint32_t type;
    uint32_t flags;
    const void *data;
    uint64_t bytes;
} p6_memory_region;
#define P6_REGION_CHIP_RAM UINT32_C(1)
#define P6_REGION_SLOW_RAM UINT32_C(2)

typedef struct p6_media_event {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t type;
    uint32_t drive;             /* 0..3. */
    uint32_t media_index;
    uint32_t reserved0;         /* Zero; aligns sequence at byte offset 24. */
    uint64_t sequence;
    uint64_t reserved[2];
} p6_media_event;
#define P6_MEDIA_EVENT_MOUNT UINT32_C(1)
#define P6_MEDIA_EVENT_EJECT UINT32_C(2)
#define P6_MEDIA_INDEX_EJECT UINT32_MAX

/* One active native instance per loaded image; calls are not reentrant and
 * belong to one caller thread. A failed load releases partial work, retains no
 * supplied pointer, and leaves the initialized instance usable. p6_unload is
 * safe when already unloaded; p6_shutdown is safe when already shut down.
 * Firmware and every media image passed to p6_load stay immutable and alive
 * until p6_unload returns; the API retains no pointer to the descriptor array.
 * Reset queues for the next frame boundary and does not advance emulation.
 * There, reset runs first, then queued media events in strictly increasing
 * sequence order, then input is applied and exactly one video unit is run.
 *
 * Version/size mismatches, nonzero reserved fields, unknown flags, and
 * unsupported settings are rejected. Optional p6_error must be initialized
 * with this ABI version and sizeof(p6_error); its message is always bounded,
 * NUL-terminated, and empty on success. Output structs are initialized by the
 * caller with matching abi_version/struct_size. p6_abi_version and
 * p6_query_caps are callable before initialization. This describes a native
 * C ABI only, without a C# layout or calling-convention claim.
 *
 * P6_RENDER_VIDEO/AUDIO control delivery only. Hardware timing, audio sampling
 * and filter phases advance regardless of flags. p6_memory_regions reports
 * required count when regions is NULL/capacity zero; insufficient capacity
 * returns P6_ERROR_CAPACITY without partial output. Extensions with no
 * advertised capability return P6_ERROR_UNSUPPORTED. */
uint32_t p6_abi_version(void);
p6_result p6_query_caps(p6_caps *caps, p6_error *error);
p6_result p6_init(const p6_config *config, p6_error *error);
p6_result p6_load(p6_buffer firmware, const p6_media *media,
                  uint32_t media_count, p6_error *error);
p6_result p6_run_frame(const p6_input *input, uint32_t render_flags,
                       p6_frame *frame, p6_error *error);
p6_result p6_reset(p6_error *error);
p6_result p6_unload(p6_error *error);
p6_result p6_shutdown(p6_error *error);

p6_result p6_state_bound(uint64_t *bytes, p6_error *error);
p6_result p6_state_save(void *buffer, uint64_t capacity, uint64_t *written,
                        p6_error *error);
p6_result p6_state_load(const void *buffer, uint64_t bytes, p6_error *error);
p6_result p6_memory_regions(p6_memory_region *regions, uint32_t capacity,
                            uint32_t *count, p6_error *error);
/* Enqueue for the next boundary. EJECT requires media_index to be
 * P6_MEDIA_INDEX_EJECT; MOUNT requires an index passed to p6_load. */
p6_result p6_media_event_enqueue(const p6_media_event *event, p6_error *error);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* P6_H_INCLUDED */
