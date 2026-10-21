#ifndef P6W_CD_H
#define P6W_CD_H
#include <stdint.h>
/* Private CD extension; ABI1/init/layout31 remain unchanged. Host-owned call
 * buffers are borrowed. Callback destination is guest memory, <=2448 bytes. */
typedef struct p6w_cd_track { uint32_t index0, index1, end, mode, control, reserved; } p6w_cd_track;
typedef struct p6w_cd_info {
 uint32_t version, size;
 uint8_t id[32];
 uint64_t bytes;
 uint32_t count, leadout;
 p6w_cd_track tracks[99];
} p6w_cd_info;
/* Regional exports bind_v2/catalog_bind_v2 use these descriptors. The header and
 * six-word track prefix match v1; v1 reserved stays zero, never an extension.
 * Active modes: 0 Audio, 1 Mode1Raw, 2 Mode2Raw. Inactive fields are zero.
 * bytes = leadout*2448, with every source offset in that canonical LBA space.
 * Stored/SUB flags describe the transported materialization (including Q/SBI),
 * not the physical packing/provenance of an image. No metadata is inferred from Q. */
typedef struct p6w_cd_track_v2 {
 uint32_t index0, index1, end, mode, control, reserved;
 uint32_t stored_pregap, synthetic_pregap, postgap;
 uint32_t flags, pregap_mode, pregap_control;
} p6w_cd_track_v2;
typedef struct p6w_cd_info_v2 {
 uint32_t version, size;
 uint8_t id[32];
 uint64_t bytes;
 uint32_t count, leadout;
 p6w_cd_track_v2 tracks[99];
} p6w_cd_info_v2;
/* V3 adds explicit ordered sessions to the unchanged regional track prefix.
 * bytes is the virtual LBA2448 extent, including unreadable inter-session gaps.
 * Zero sessions retains legacy geometry; every inactive session field is zero. */
typedef struct p6w_cd_session {
 uint32_t first_track, last_track, format;
 int32_t cue0;
 uint32_t a2;
} p6w_cd_session;
typedef struct p6w_cd_info_v3 {
 uint32_t version, size;
 uint8_t id[32];
 uint64_t bytes;
 uint32_t count, leadout;
 p6w_cd_track_v2 tracks[99];
 uint32_t session_count;
 p6w_cd_session sessions[99];
} p6w_cd_info_v3;
/* flags share the Disc regional policy: Explicit=1, program SUB96=2,
 * pregap SUB96=4, stored postgap=8, postgap SUB96=16, INDEX02 postgap=32.
 * flags0 selects legacy behavior and requires zero pregap_mode/control. */
/* layout_v2 returns 10 u64: info size/bytes offset/tracks offset, track size,
 * then stored_pregap/synthetic_pregap/postgap/flags/pregap_mode/control offsets.
 * V1 and V2 cannot rebind across versions; callbacks retain slots0/1. */
/* Return exact byte count, or a negative error. No exception crosses callback. */
typedef int32_t (*p6w_cd_read_fn)(uint64_t offset, void *destination, uint32_t count);
/* Catalog callback uses the immutable canonical ordinal, never a host cursor. */
typedef int32_t (*p6w_cd_catalog_read_fn)(uint32_t disc, uint64_t offset, void *destination, uint32_t count);
/* New private exports borrow info[1..16]; rebind validates all before publishing
 * the thunk and preserves snapshot selection/presence. change requires absence
 * before choosing another ordinal. status writes count, selected, present, opens. */
/* Adapter-internal teardown, called after the engine closes Akiko. */
void p6w_cd_session_end(void);
#endif
