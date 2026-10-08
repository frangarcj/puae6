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
#define P6_CAP_MEDIA_APPEND     (UINT64_C(1) << 4)

typedef struct p6_config {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t machine;           /* P6_MACHINE_A500, A600 or A1200; any other value is P6_ERROR_UNSUPPORTED. */
    uint32_t video_standard;    /* P6_VIDEO_PAL or P6_VIDEO_NTSC. */
    uint32_t audio_rate_hz;
    uint32_t flags;             /* Must be zero in ABI v1. */
    uint64_t random_seed;
    int64_t rtc_epoch_seconds;  /* UTC epoch; never read from host clock. */
    uint64_t reserved[4];
} p6_config;
#define P6_MACHINE_A500 UINT32_C(1)
#define P6_MACHINE_A600 UINT32_C(2)   /* ECS, 1 MiB Chip, no Slow RAM, no IDE/PCMCIA (Gayle is not emulated). */
#define P6_MACHINE_A1200 UINT32_C(3)  /* 68EC020 cycle exact (24-bit bus), AGA, 2 MiB Chip, no Slow/Fast RAM, no Gayle. */
#define P6_VIDEO_PAL    UINT32_C(1)
#define P6_VIDEO_NTSC   UINT32_C(2)

/* Memory is fixed per machine: A500 512 KiB Chip + 512 KiB Slow; A600 1 MiB Chip, no Slow; A1200 2 MiB Chip, no Slow. */
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
 * Audio. With P6_RENDER_AUDIO, `audio` points to audio_frames pairs [L0,R0,L1,R1,...]
 * of what Paula produced from the previous unit boundary to this one (left = channels
 * 0 and 3, right = 1 and 2; 44100 or 48000 Hz as configured), and is non-NULL even for
 * zero frames; without the flag both are zero. A call never returns more than
 * p6_caps.max_audio_frames. The count follows the emulated clock alone (no per-frame
 * rounding); p6_state_save does not invalidate the pointer.
 *
 * Video. video_width x video_height is the delivered buffer and is constant for a
 * loaded image (760x576 PAL, 760x486 NTSC, see p6_caps); video_stride_bytes is the
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
/* Lag: the emulated CPU read no input register (joystick/mouse counters, POT, CIA-A PRA, CIA-A SDR) during the unit. */
#define P6_FRAME_LAG         (UINT32_C(1) << 0)
#define P6_FRAME_NO_VIDEO    (UINT32_C(1) << 1)
/* Drive light: at the end of the unit a floppy drive's LED is lit (motor on or writing). An output derived from the
 * drive state, it never feeds back into the machine. */
#define P6_FRAME_DRIVE_LIGHT (UINT32_C(1) << 2)
#define P6_RENDER_VIDEO      (UINT32_C(1) << 0)
#define P6_RENDER_AUDIO      (UINT32_C(1) << 1)

/* Extensions use caller-owned bounded buffers. State format is native to the
 * exact compatible build/config/media; it is not a Waterbox snapshot or a
 * cross-build interchange format. Region data is borrowed until next mutation.
 *
 * Memory regions (T20a) are the memory as the emulated CPU sees it, in the order of the 68000: bytes are in big-endian
 * order and offset n of a region is byte n of that memory. The profile reports Chip RAM, Slow RAM and the ROM, each
 * with its real size; a RAM that the machine does not have is not reported. Only RAM carries P6_REGION_WRITABLE: the
 * ROM is read-only. A host write is an external action that the emulated machine did not do, so a caller that records
 * a replay has to treat it as input. The pointers are valid until the next call that changes the machine (a
 * p6_run_frame, a reset, an unload); a caller that keeps a region across those has to ask again. */
typedef struct p6_memory_region {
    uint32_t type;
    uint32_t flags;
    const void *data;
    uint64_t bytes;
} p6_memory_region;
#define P6_REGION_CHIP_RAM UINT32_C(1)
#define P6_REGION_SLOW_RAM UINT32_C(2)
#define P6_REGION_ROM      UINT32_C(3)
#define P6_REGION_WRITABLE (UINT32_C(1) << 0)

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
 * sequence order, then input is applied (after the reset head, so that it does not
 * mix with the keyboard's power-up sequence) and exactly one video unit is run.
 * Input semantics are in docs/02-contracts.md ("Entrada"): the packet is the host
 * state of the unit, only differences reach the machine, keys 0x68..0x7f and
 * inconsistent ports are rejected before anything is touched, and a mouse is on
 * port 0 only.
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
/* Queues a hard reset for the next p6_run_frame, whose head runs it before media events
 * and input (BAD_STATE unless loaded; a second call before that frame changes nothing,
 * and before the first frame there is nothing to start over). The guest restarts with
 * clean RAM and the keyboard powers up again; disks, configuration, the unit index and
 * master_ticks are kept, and keys the host still holds are pressed again once after the
 * keyboard's power-up stream. It is not unload + load: the reset head reseeds the random
 * generator from the configured seed (the start-up delay is the power-on one) and drive
 * mechanics (cylinder) are not reset. */
p6_result p6_reset(p6_error *error);
p6_result p6_unload(p6_error *error);
p6_result p6_shutdown(p6_error *error);

/* Native state (P6_CAP_STATE, announced by the images that carry it; the others do not define these functions). A state is the machine at a unit
 * boundary of a loaded image: p6_state_save needs one that has run p6_run_frame once (BAD_STATE before: there is no machine to describe yet),
 * and p6_state_load also accepts one that has not started (it runs the reset head first).
 * p6_state_bound is an upper bound of any state of this image, computed from the configuration and the registered media and never changing
 * until p6_unload: it does not grow with time, held keys or the media queue. p6_state_save writes at most `capacity` bytes and sets `written`
 * (CAPACITY if the buffer is too small, nothing written beyond it). p6_state_load validates the whole state (length, checksums, build, firmware,
 * configuration, media manifest, every chunk) before it touches the machine, so a refused state (CORRUPT_STATE or INCOMPATIBLE) leaves the image
 * running exactly as it was; the loaded machine then continues bit for bit as the saved one did. A state is valid only in an image of the same
 * build, firmware and configuration whose media manifest starts with the one the state was taken with (the manifest only grows: p6_media_append); the
 * media the drives hold need not be the same, the state names them. */
p6_result p6_state_bound(uint64_t *bytes, p6_error *error);
p6_result p6_state_save(void *buffer, uint64_t capacity, uint64_t *written,
                        p6_error *error);
p6_result p6_state_load(const void *buffer, uint64_t bytes, p6_error *error);
p6_result p6_memory_regions(p6_memory_region *regions, uint32_t capacity,
                            uint32_t *count, p6_error *error);
/* Enqueue for the next boundary. EJECT requires media_index to be
 * P6_MEDIA_INDEX_EJECT; MOUNT requires an index of the manifest (passed to p6_load or returned by p6_media_append). Only DF0 and
 * DF1 exist (drives 2 and 3 are UNSUPPORTED, above 3 BAD_ARGUMENT); at most 16
 * events wait for a boundary (CAPACITY) and sequence must exceed the last accepted
 * one for the whole session. A refused event queues nothing and uses no sequence.
 * Events queued before the first p6_run_frame set the power-on disks (the last one
 * per drive wins); later ones follow the engine's delayed swap: eject at the next
 * vsync, insert about 100 (PAL) units later. */
p6_result p6_media_event_enqueue(const p6_media_event *event, p6_error *error);

/* Registers one more disk after p6_load (P6_CAP_MEDIA_APPEND), always at the END of the media manifest: the new medium gets the next index, the ones
 * before it keep theirs, and a medium is never removed during a session. The medium is validated exactly as p6_load validates its array (type ADF,
 * flags 0, 901120 bytes, a content_id that is the SHA-256 of the image: UNSUPPORTED, BAD_ARGUMENT, WRONG_MEDIA); one whose content_id is already in
 * the manifest is not registered twice and *index is the index it already has (a disk is its content, never its path). A manifest that already holds
 * P6_MAX_MEDIA media answers CAPACITY for a new medium. *index is written only on success, which changes nothing else: no machine state, no queued event, no
 * drive; mounting the medium is a p6_media_event_enqueue of the returned index. The image is borrowed until p6_unload like those of p6_load. Callable
 * between units on a loaded image (BAD_STATE otherwise); a call made while another is in progress is BUSY.
 *
 * A native state records how many media the manifest had and the hash of that prefix: it loads in an image whose manifest starts with the same media
 * (the image may have appended more since), and it is INCOMPATIBLE in one whose manifest does not. Every medium a state names is inside its own prefix. */
p6_result p6_media_append(const p6_media *medium, uint32_t *index, p6_error *error);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* P6_H_INCLUDED */
