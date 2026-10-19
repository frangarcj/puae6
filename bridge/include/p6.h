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
#define P6_MAX_HDF 4u

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
/* max_video_width x max_video_height is the buffer of the profile's raster (hires, double lines); a configuration that asks for another resolution or line mode
 * (p6_config.video_resolution, video_line_mode) delivers the buffer p6_query_video reports for it. */
#define P6_CAP_STATE            (UINT64_C(1) << 0)
#define P6_CAP_MEMORY_REGIONS   (UINT64_C(1) << 1)
#define P6_CAP_MEDIA_EVENTS     (UINT64_C(1) << 2)
#define P6_CAP_LAG_FLAG         (UINT64_C(1) << 3)
#define P6_CAP_MEDIA_APPEND     (UINT64_C(1) << 4)
#define P6_CAP_MEDIA_WRITE      (UINT64_C(1) << 5)   /* P6_CONFIG_FLOPPY_WRITES, p6_media_export and p6_media_write_status */
#define P6_CAP_MEDIA_EXT_ADF    (UINT64_C(1) << 7)   /* P6_MEDIA_ADF also accepts the extended ADF of WinUAE (UAE-1ADF and UAE--ADF: raw MFM tracks and AmigaDOS tracks), read only (T57a) */
#define P6_CAP_MEDIA_FDI        (UINT64_C(1) << 8)   /* P6_MEDIA_ADF also accepts the FDI image ("Formatted Disk Image file": empty, Amiga and low-level tracks), read only (T57b) */
#define P6_CAP_MEDIA_IPF        (UINT64_C(1) << 9)   /* P6_MEDIA_ADF also accepts the IPF image (SPS/CAPS, tag "CAPS"), read only, decoded by the free reader of MAME (T58) */
#define P6_CAP_MEDIA_HDF        (UINT64_C(1) << 6)   /* P6_MEDIA_HDF: hard files on the UAE controller (uaehf.device), their write overlay, their export and their native state (T29) */

typedef struct p6_config {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t machine;           /* P6_MACHINE_A500, A600 or A1200; any other value is P6_ERROR_UNSUPPORTED. */
    uint32_t video_standard;    /* P6_VIDEO_PAL or P6_VIDEO_NTSC. */
    uint32_t audio_rate_hz;
    uint32_t flags;             /* P6_CONFIG_*; any other bit is P6_ERROR_BAD_ARGUMENT. */
    uint64_t random_seed;
    int64_t rtc_epoch_seconds;  /* UTC epoch; never read from host clock. */
    uint32_t chip_kib;          /* Chip RAM: 0 = the machine's own, else 512, 1024, 1536 or 2048 (A500: 512 or 1024). */
    uint32_t slow_kib;          /* Slow RAM at $C00000: 0 = the machine's own, P6_MEMORY_NONE = none, else 512, 1024, 1536 or 1792. */
    uint32_t fast_kib;          /* Zorro II Fast RAM (one autoconfig board): 0 or P6_MEMORY_NONE = none, else 1024, 2048, 4096 or 8192. */
    union {
        uint32_t fpu_model;     /* Floating point unit (T54): 0 = none, 68881 or 68882 (A1200 only: it is the 68020's coprocessor), computed by SoftFloat whatever the host. */
        uint32_t reserved32;    /* The name these four bytes had until T54 (zero, ABI v1); an earlier source that sets it to zero means no FPU. */
    };
    /* Paula's output (T73), in the first four bytes of what was reserved[0]. Zero in every field is the profile's own setting, so a source that predates them (those bytes zero) is
     * unchanged; a restart option: it changes the samples and the audio state, so it enters config_hash (docs/02-contracts.md, Opciones de audio de Paula) off the profile's setting only. */
    uint8_t sound_separation;       /* Stereo separation: 0 = the profile's (70 %), else 1 + percent / 10 (1 = 0 %, mono; 11 = 100 %, as Paula wires the channels). */
    uint8_t sound_interpolation;    /* 0 = the profile's (anti), else 1 + upstream's sound_interpol: 1 none, 2 anti, 4 rh, 5 crux; 3 (sinc) is P6_ERROR_UNSUPPORTED. */
    uint8_t sound_filter;           /* 0 = the profile's (emulated, the LED filter follows the power LED), else 1 + upstream's sound_filter: 1 off, 2 emulated, 3 always on. */
    uint8_t sound_filter_type;      /* 0 = auto (A500 and A600: the A500 model, A1200: the A1200 model), 1 = A500 model, 2 = A1200 model. */
    /* Floppy drive speed (T62), in the fifth byte of what was reserved[0]: how fast the drive hands the disk's bits over, 0 = the profile's (the real drive, 100 %), else 1 + a step:
     * 1 = 100 %, 2 = 200 %, 3 = 400 %, 4 = 800 %, 5 = turbo (upstream's floppy_speed 0: a transfer of the whole DSKLEN at once). A restart option: it changes when the DMA ends, so it enters
     * config_hash off the profile's speed only (bits 25 to 27 of the flags word, below). Anything above 5 is P6_ERROR_UNSUPPORTED. */
    uint8_t floppy_speed;
    /* Resolution and line mode of the raster (T72), in the sixth and seventh bytes of what was reserved[0]. They are the engine's own gfx_resolution and gfx_vresolution, so the picture the
     * renderer draws changes, and so does the buffer announced at load. video_resolution: 0 = the profile's (hires), else 1 = lores, 2 = hires, 3 = super-hires. video_line_mode: 0 = the
     * profile's (double), else 1 = single (one raster row per line; an interlaced guest shows each field on its own, P6_FIELD_PROGRESSIVE), 2 = double (two rows per line, the fields
     * of an interlaced guest woven). Restart options: they enter config_hash only off the profile's raster (bits 28 to 30 of the flags word, below), so the default configuration keeps
     * its hash, its bytes and its states. Anything above 3 / 2 is P6_ERROR_UNSUPPORTED. */
    uint8_t video_resolution;
    uint8_t video_line_mode;
    /* The CPU (T52), in the eighth byte of what was reserved[0]: 0 = the machine's own (68000 on the A500, A500 OG, A500+, A600, A1000, A2000 OG and A2000; 68EC020 on the A1200), else
     * 1 + (model - 68000) / 10: 1 = 68000, 2 = 68010, 3 = 68020. The 68000 machines also take the 68010 (the same sockets, the cycle-exact 68010 of upstream); the A1200 has its
     * soldered 68EC020 and nothing else; any other combination is P6_ERROR_UNSUPPORTED with the reason. A restart option: it changes the instruction set, the exception frames and
     * the registers, so it enters config_hash off the machine's own CPU only (a derived hash, bridge/config.cpp cpu_identity, domain p6/config/cpu/v1). */
    uint8_t cpu_model;
    /* The clock of the CPU (T52), in the first byte of what was reserved[1]: 0 = the machine's own (2 on the machines with a 68000, 4 on the A1200), else the multiplier of upstream's
     * cpu_clock_multiplier (N << 8), PUAE5's values: 1 (3.55 MHz), 2 (7.09 MHz, a 68000), 4 (14.19 MHz, the A1200's 68EC020), 8, 10, 12 or 16 (56.75 MHz); any other is P6_ERROR_UNSUPPORTED. One CPU
     * clock lasts CYCLE_UNIT / N of emulated time, so every access and instruction costs fewer or more colour clocks; nothing reads the clock of the host. A restart option: it enters config_hash off
     * the machine's own clock only, together with the CPU model (bridge/config.cpp cpu_identity, domain p6/config/cpu/v1). */
    uint8_t cpu_multiplier;
    /* The controller the hard files (P6_MEDIA_HDF) are on (T56), in the second byte of what was reserved[1]: P6_HDF_CONTROLLER_UAE (0, the default: uaehf.device and the UAEFS board, the
     * way PUAE5 mounts them) or P6_HDF_CONTROLLER_IDE (1: the IDE port of Gayle, with the drives the real A600 and A1200 have, master and slave of its channel and the pair behind an
     * IDE splitter). Only the A600 and the A1200 have Gayle: IDE on another machine is P6_ERROR_UNSUPPORTED. Gayle is switched on only while there is a hard file behind it: an IDE
     * config whose p6_load has no hard file is the machine the default config is, with its identity, its states and its movies. A restart option: it is part of config_hash while it
     * is in effect (derived hash, bridge/config.cpp ide_attached, domain p6/config/ide/v1). Anything above 1 is P6_ERROR_UNSUPPORTED. */
    uint8_t hdf_controller;
    /* T53: restart CPU accuracy, 0 exact (the unchanged default), 1 compatible prefetch, 2 normal instruction timing.
     * The level is part of state identity. Every supported 68000/68010/68020 machine can select each level at load.
     * No level reads a host clock. */
    uint8_t cpu_compatibility;
    uint8_t reserved7[5];           /* Zero in ABI v1 (the rest of what was reserved[1]). */
} p6_config;
#define P6_CPU_EXACT 0u
#define P6_CPU_COMPATIBLE 1u
#define P6_CPU_NORMAL 2u
#define P6_HDF_CONTROLLER_UAE 0u
#define P6_HDF_CONTROLLER_IDE 1u
/* P6_CONFIG_FLOPPY_WRITES: the floppies of this session are writable. The disk images stay immutable; what the guest writes goes to a write overlay
 * (sector granularity, one fixed pool of P6_OVERLAY_SECTORS shared by every disk) that is part of the machine state and of p6_state_bound, so a state
 * load or a rewind takes the writes back and the base is never touched. Without the flag every disk is write protected, as in ABI v1. The flag is
 * part of the configuration identity: a state of one setting does not load in the other. Getting the writes out is explicit: p6_media_export. */
#define P6_CONFIG_FLOPPY_WRITES UINT32_C(1)
#define P6_OVERLAY_SECTORS UINT32_C(3520)    /* 512-byte sectors: two disks' worth */
#define P6_MEMORY_NONE UINT32_C(0xFFFFFFFF)
/* The flags word of config_hash carries p6_config.flags in bits 0 to 7 (the defined P6_CONFIG_* flags; any other bit is refused) and, when the memory is not the preset's,
 * the sizes above them (bridge/config.cpp identity_flags: bit 31, fast_kib/1024 at bit 8, slow_kib/64 at 12, chip_kib/64 at 17), so the two never collide.
 * The floppy speed (T62) takes bits 25 to 27 of it: 0 for the real drive speed, whether the config says 0 or 1, else 1 = 200 %, 2 = 400 %, 3 = 800 %, 4 = turbo.
 * The raster (T72) takes bits 28 to 30: 28-29 the resolution off hires (1 = lores, 2 = super-hires) and 30 set for the single line mode; the profile's own raster, however it is spelled, adds nothing.
 * Paula's output options (sound_*) are not in that word: when they differ from the profile's setting for the machine, config_hash is derived from the one above and an audio word
 * (bridge/config.cpp audio_identity, domain p6/config/audio/v1); at the profile's setting the hash is the one it always was. */
#define P6_MACHINE_A500 UINT32_C(1)
#define P6_MACHINE_A600 UINT32_C(2)   /* ECS, 1 MiB Chip, no Slow RAM, no PCMCIA; Gayle (IDE) only with p6_config.hdf_controller = P6_HDF_CONTROLLER_IDE and a hard file. */
#define P6_MACHINE_A1200 UINT32_C(3)  /* 68EC020 cycle exact (24-bit bus), AGA, 2 MiB Chip, no Slow/Fast RAM; Gayle (IDE) as the A600, no PCMCIA. */
/* T50: more presets of the same 68000/68EC020 family, each built from upstream's built_in_prefs and listed in docs/02-contracts.md ("Maquinas y capacidades"). Their values were added
 * after the three above, which keep value, config_hash and state identity. */
#define P6_MACHINE_A500OG UINT32_C(4)    /* A500 with OCS and 512 KiB Chip only (no Slow RAM, no clock): upstream's A500 configuration 3. */
#define P6_MACHINE_A500PLUS UINT32_C(5)  /* A500+: ECS, 1 MiB Chip, clock; built from upstream's A500+ (model 1). */
#define P6_MACHINE_A1000 UINT32_C(6)     /* A1000: the 8361 Agnus and 8362 Denise of upstream's A1000 chipset, 512 KiB Chip, the Kickstart given to p6_load as a ROM (no boot ROM, no WOM). */
#define P6_MACHINE_A2000OG UINT32_C(7)   /* A2000 with OCS: 512 KiB Chip + 512 KiB Slow, the A2000 chipset table (clock, mains-driven CIA TOD). */
#define P6_MACHINE_A2000 UINT32_C(8)     /* A2000 with the ECS Agnus and the OCS Denise (PUAE5's), 1 MiB Chip, no Slow RAM. */
#define P6_VIDEO_PAL    UINT32_C(1)
#define P6_VIDEO_NTSC   UINT32_C(2)

/* Memory of the machine as the preset has it (all three memory fields of p6_config zero): A500 512 KiB Chip + 512 KiB Slow; A600 1 MiB Chip,
 * no Slow; A1200 2 MiB Chip, no Slow; no Fast RAM on any. chip_kib, slow_kib and fast_kib change it at load (T49); they enter config_hash only when
 * the resulting memory is not the preset's, so a config that names the preset's own sizes is the same machine, state and movie identity included. */
typedef struct p6_buffer {
    const void *data;
    uint64_t bytes;
} p6_buffer;

/* image data is borrowed and immutable through p6_unload(); bytes is exact. */
typedef struct p6_media {
    uint32_t type;              /* P6_MEDIA_ADF (a floppy image, below) or P6_MEDIA_HDF. */
    uint32_t flags;             /* Must be zero in ABI v1. */
    uint8_t content_id[P6_CONTENT_ID_SIZE];
    p6_buffer image;
} p6_media;
#define P6_MEDIA_ADF UINT32_C(1)
/* P6_MEDIA_ADF: a floppy disk image, whose format the bytes say: a plain ADF (exactly 901120 bytes, the only one a write overlay can cover) or, with P6_CAP_MEDIA_EXT_ADF
 * (T57), an extended ADF ("UAE-1ADF" or "UAE--ADF": per-track raw MFM with its length in bits, and AmigaDOS tracks, mixed) or, with P6_CAP_MEDIA_FDI, an FDI image
 * ("Formatted Disk Image file", FDI 1.x and 2.x: empty, Amiga double-density and low-level tracks) or, with P6_CAP_MEDIA_IPF, an IPF ("CAPS": the cells of every track with their
 * timing and weak cells; every tag has its checksum verified and every track is decoded once, at p6_load, so what the decoder cannot read is WRONG_MEDIA with the reason), at most 3 MiB, read from memory by the engine's own disk reader. The content_id is the SHA-256 of the bytes as given, so an extended ADF and a plain one of the same disk are two media. A track-level image is
 * always write protected (P6_CONFIG_FLOPPY_WRITES does not apply to it: p6_media_write_status_get reports it with no sectors and p6_media_export answers UNSUPPORTED), and
 * the floppies of a session together take at most 16 plain disks (14 417 920 bytes: CAPACITY beyond). A header that the engine could not read safely (more tracks than a
 * drive has, a track that lies outside the file or holds more bits than the engine's buffer, a high-density track) is WRONG_MEDIA, before the engine is touched. */
/* P6_MEDIA_HDF: a hard file, mounted from power-on on the UAE controller (uaehf.device and the UAEFS board, the way PUAE5 does) as DH0, DH1... in
 * the order the hard files have in the p6_load array, at most P6_MAX_HDF. The image is a multiple of 512 bytes, at least one block; a hard file that starts
 * with "RDSK" brings its own RDB, any other is one partition (32 sectors, 1 head, 2 reserved blocks, boot priority 0). There is no fixed size limit: the image
 * is copied whole (the memory it takes is its size, twice: the base and the room for the overlay of its writes, see below) and p6_load answers CAPACITY if it does not fit. A hard file is never a floppy: it cannot be mounted in a
 * drive or appended with p6_media_append; flags must be 0. Guest I/O is synchronous, with no emulated delay: a request finishes in the unit that
 * issued it and the guest sees the reply at the next INT2. The Kickstart must be 1.3 or later (unit 34+). A native state of a session with a hard file carries the
 * UAE controller (the UAEFS board, the trap area, the units of the filesystem and of uaehf.device, the queue of the EXTER interrupt) and, of each hard file, the 4 KiB
 * chunks the guest wrote and nothing of the base; the base is named by its content id, so a state of another base image, or of a session without that hard file, is
 * refused (INCOMPATIBLE) before the machine is touched. p6_state_bound counts every chunk of every hard file as written, so it grows with the image. SCSI direct commands
 * (HD_SCSICMD) answer IOERR_NOCMD. Writes: see the paragraph on hard files below. */
#define P6_MEDIA_HDF UINT32_C(2)

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
#define P6_PORT_CD32      UINT32_C(3)  /* Seven buttons and a digital D-pad, either gameport; no relative motion. */
#define P6_BUTTON_1       (UINT32_C(1) << 0)
#define P6_BUTTON_2       (UINT32_C(1) << 1)
#define P6_BUTTON_3       (UINT32_C(1) << 2)
#define P6_BUTTON_UP      (UINT32_C(1) << 3)
#define P6_BUTTON_DOWN    (UINT32_C(1) << 4)
#define P6_BUTTON_LEFT    (UINT32_C(1) << 5)
#define P6_BUTTON_RIGHT   (UINT32_C(1) << 6)
#define P6_CD32_RED       P6_BUTTON_1
#define P6_CD32_BLUE      P6_BUTTON_2
#define P6_CD32_GREEN     P6_BUTTON_3
#define P6_CD32_YELLOW    (UINT32_C(1) << 7)
#define P6_CD32_REWIND    (UINT32_C(1) << 8)
#define P6_CD32_FORWARD   (UINT32_C(1) << 9)
#define P6_CD32_PLAY      (UINT32_C(1) << 10)

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
 * loaded image (760x576 PAL, 760x486 NTSC for the profile's raster, see p6_caps; the
 * resolution and line mode of p6_config scale it, see p6_query_video); video_stride_bytes is the
 * row pitch, never width * 4. Rows and columns outside `active` are zero. `active`
 * is the raster the renderer drew for this unit and can be smaller than the buffer
 * (PAL 756x574 normally, 756x576 for a guest that leaves the long-field bit set).
 * aspect_num/aspect_den is the PIXEL aspect ratio (width : height of one pixel of
 * the buffer), PAL 26:25 and NTSC 43:50; the picture aspect is
 * active.width * num : active.height * den. A unit is one video field. field_parity
 * is P6_FIELD_PROGRESSIVE unless the monitor detected interlace; then EVEN when the
 * unit drew rows 0,2,4.. of the buffer (the Amiga long field) and ODD for rows
 * 1,3,5.. The buffer always holds both fields (a weave); in single line mode (p6_config.video_line_mode) it holds the one field of the unit and the parity stays
 * P6_FIELD_PROGRESSIVE, there is nothing to weave. cadence_num/cadence_den is
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
 * order and offset n of a region is byte n of that memory. The profile reports Chip RAM, Slow RAM, Fast RAM and the ROM, each
 * with its real size; a RAM that the machine does not have is not reported. Only RAM carries P6_REGION_WRITABLE: the
 * ROM is read-only. A host write is an external action that the emulated machine did not do, so a caller that records
 * a replay has to treat it as input. The pointers are valid until the next call that changes the machine (a
 * p6_run_frame, a reset, an unload); a caller that keeps a region across those has to ask again. Measured (T75): while one image is
 * loaded with one configuration the engine keeps its buffers (memory_reset only reallocates when a size changes), so a reset, a state
 * load and a run-ahead leave the pointers where they were; a caller that hands them to somebody else (a frontend's memory map) asks again
 * after each of those and tells that somebody when one differs. The Slow RAM is reported with the size the CPU
 * reaches (T75a), not the size allocated: the engine allocates 2 MiB for the 1.5 and 1.75 MiB options and maps only what was asked, so every client
 * (a frontend memory map, a Waterbox domain) sees the same bytes the CPU can address. */
typedef struct p6_memory_region {
    uint32_t type;
    uint32_t flags;
    const void *data;
    uint64_t bytes;
} p6_memory_region;
#define P6_REGION_CHIP_RAM UINT32_C(1)
#define P6_REGION_SLOW_RAM UINT32_C(2)
#define P6_REGION_ROM      UINT32_C(3)
#define P6_REGION_FAST_RAM UINT32_C(4)   /* Zorro II Fast RAM (T49): present only when p6_config.fast_kib asked for it, and after the unit that ran its autoconfig reset */
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
/* The raster of a configuration (T72), callable before p6_init: buffer_width x buffer_height is the video_width x video_height of every frame of an image loaded with it (constant,
 * never exceeded: a picture that does not fit is delivered as P6_FRAME_NO_VIDEO), active_width x active_height the nominal raster of a normal unit, and aspect_num : aspect_den the
 * pixel aspect of that configuration. The configuration is validated like p6_init does. */
typedef struct p6_video_geometry {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t buffer_width, buffer_height;
    uint32_t active_width, active_height;
    uint32_t aspect_num, aspect_den;
} p6_video_geometry;
p6_result p6_query_video(const p6_config *config, p6_video_geometry *geometry, p6_error *error);
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

/* Hard files (P6_MEDIA_HDF) are always writable (T29b): the guest's writes go to an overlay of 4 KiB chunks, made on the first write to a chunk, over the immutable base
 * (the registered copy and the caller's buffer never change). The room is reserved by p6_load and is the size of the image, so a write cannot run out of it. They are
 * not floppies: P6_CONFIG_FLOPPY_WRITES does not apply. p6_media_export(index of a hard file) copies the image as it is now, the base with the overlay over it, into a buffer of
 * at least its size (CAPACITY otherwise, nothing written), and p6_media_write_status_get counts in chunks (overlay_capacity = chunks of the image, overlay_used =
 * medium_sectors = chunks written, refused_writes = raw_tracks = 0). Nothing leaves the process by itself: no flush, no file. The overlay is machine state, like the sectors of a
 * writable floppy: a state carries the chunks written as of its unit (never the base) and loading it replaces the overlay as a whole.
 *
 * Writable floppies (P6_CAP_MEDIA_WRITE, config flag P6_CONFIG_FLOPPY_WRITES; UNSUPPORTED otherwise).
 *
 * The identity of a disk stays its base: the content_id of the manifest, the CRC a drive reports and the media_manifest_hash of a state never include
 * what was written. The writes are machine state, like RAM: a state carries the overlay as of its unit, and loading it replaces the overlay (a state
 * taken before a write loads the disk as it was). A guest write that does not fit is refused without storing any of it: while fewer than 11 sectors are
 * free the drives report the disk write protected, so a well behaved guest never tries; one that tries anyway is counted in `refused_writes`.
 *
 * p6_media_write_status describes the overlay as of the last unit boundary; nothing in it grows with time except what a guest writes.
 * p6_media_export copies the disk as it is now (the base with the overlay over it, exactly 901120 bytes) into the caller's buffer; it reads the
 * machine and changes nothing in it (no flush, no state change, no event), and the bytes are the caller's to make durable (write a temporary file,
 * flush it, rename it over the target: nothing of that is done by run, save or load, and a caller that speculates (rewind, run-ahead, a second
 * instance) must not export from the speculative branch). CAPACITY (nothing written) when the buffer is smaller than the disk; UNSUPPORTED (nothing written) when the
 * guest wrote a track in a format that is not AmigaDOS: an ADF has no place for it (the machine keeps it and reads it back, but the image would lose it), see raw_tracks.
 * A guest write that cannot be kept (every raw slot taken) is counted in refused_writes. */
typedef struct p6_media_write_status {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t media_index;       /* in: the disk. */
    uint32_t reserved0;         /* Zero. */
    uint64_t overlay_capacity;  /* out: sectors the pool holds (P6_OVERLAY_SECTORS), for all disks together. */
    uint64_t overlay_used;      /* out: sectors in use, for all disks together. */
    uint64_t medium_sectors;    /* out: sectors of this disk that differ from the base's. */
    uint64_t refused_writes;    /* out: guest writes refused for lack of room since the load or the state that was loaded. */
    uint64_t raw_tracks;        /* out: tracks of this disk that the guest wrote in a format that is not AmigaDOS (kept exactly, at most 16 for all disks; they stop p6_media_export). */
    uint64_t reserved[1];
} p6_media_write_status;
p6_result p6_media_write_status_get(p6_media_write_status *status, p6_error *error);
p6_result p6_media_export(uint32_t media_index, void *buffer, uint64_t capacity, uint64_t *written, p6_error *error);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* P6_H_INCLUDED */
