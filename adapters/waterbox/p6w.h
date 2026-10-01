/* PUAE6 guest ABI for Waterbox (T19b). The host (the managed wrapper, or the test harness) calls these exports of puae6.wbx.
 *
 * Pointers in these structs point into HOST memory and are valid only during the call that receives them: the guest copies what it
 * needs (firmware and media go into the sealed heap before the seal) and keeps none. The pointers the guest RETURNS in p6w_frame
 * (video, audio) point into GUEST memory and are valid, while the sandbox is active, until the next call into the guest; the host reads
 * them right after the call and never keeps them. Every struct carries abi_version and struct_size, which the guest checks. */
#ifndef P6W_H_INCLUDED
#define P6W_H_INCLUDED

#include "p6.h"

#ifdef __cplusplus
extern "C" {
#endif

#define P6W_ABI_VERSION UINT32_C(1)

typedef struct p6w_media {
    uint32_t type;                      /* P6_MEDIA_*; flags must be zero, as in p6_media. */
    uint32_t flags;
    uint8_t content_id[P6_CONTENT_ID_SIZE];
    const void *data;                   /* host memory, copied into the sealed heap. */
    uint64_t bytes;
} p6w_media;

/* Input of p6w_init; result and error are outputs. Firmware and media are copied, so the host may free them when the call returns. */
typedef struct p6w_init {
    uint32_t abi_version;
    uint32_t struct_size;
    p6_config config;
    const void *firmware;               /* host memory, copied into the sealed heap. */
    uint64_t firmware_bytes;
    uint32_t media_count;
    uint32_t reserved0;
    p6w_media media[P6_MAX_MEDIA];
    int32_t result;                     /* out: p6_result. */
    uint32_t reserved1;
    char error[P6_ERROR_TEXT_CAPACITY]; /* out: empty on success. */
} p6w_init;

/* Output of p6w_frame_advance. frame is what p6_run_frame filled in; its video and audio pointers are guest memory. */
typedef struct p6w_frame {
    uint32_t abi_version;
    uint32_t struct_size;
    p6_frame frame;
    int32_t result;                     /* out: p6_result. */
    uint32_t reserved;
    char error[P6_ERROR_TEXT_CAPACITY];
} p6w_frame;

/* One media event for the next boundary (T21a): p6_media_event_enqueue. Result and error are outputs, as in p6w_frame. */
typedef struct p6w_media_event {
    uint32_t abi_version;
    uint32_t struct_size;
    p6_media_event event;
    int32_t result;                     /* out: p6_result. */
    uint32_t reserved;
    char error[P6_ERROR_TEXT_CAPACITY]; /* out: empty on success. */
} p6w_media_event;

/* A memory area of the machine (T20a), in the layout BizHawk's LibWaterboxCore.MemoryArea has, so that the managed side can hand it to
 * WaterboxMemoryDomain as it is. The area is always a function hook: `data` is the address of a guest function
 *     void access(void *buffer, int64_t address, int64_t count, bool write)
 * that copies `count` bytes between `buffer` (host memory, valid for the call) and the area at byte `address`. The access runs inside the
 * sandbox, so a write to a page that the dirty tracking still protects is recorded as the guest's own (a managed write would be fatal), and
 * it resolves the engine's pointer at every call: nothing here can dangle if the engine moves its RAM. A read or write that is out of range,
 * a write to the ROM, or an access with no machine loaded does nothing (a read gives zeros) and is counted by p6w_memory_refused. */
typedef struct p6w_memory_area {
    const void *data;                   /* the guest function above (P6W_AREA_FUNCTION_HOOK is always set). */
    const char *name;                   /* NUL terminated, static. */
    int64_t size;                       /* bytes; never zero. */
    int64_t flags;                      /* P6W_AREA_*; the values are those of LibWaterboxCore.MemoryDomainFlags. */
} p6w_memory_area;
#define P6W_AREA_WRITABLE      INT64_C(1)
#define P6W_AREA_PRIMARY       INT64_C(8)
#define P6W_AREA_BIG_ENDIAN    INT64_C(16)      /* the most significant byte of a word comes first, as on the 68000 */
#define P6W_AREA_WORD_SIZE_2   INT64_C(64)
#define P6W_AREA_FUNCTION_HOOK INT64_C(1024)
#define P6W_MAX_AREAS 4

/* Sizes and offsets the guest was compiled with, for the host to check its own layout against: {sizeof(p6w_init), offsetof(result),
 * offsetof(error), sizeof(p6w_frame), offsetof(frame), offsetof(result), sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config),
 * sizeof(p6w_media), offsetof(media), ..., sizeof(p6w_memory_area), offsetof(name), offsetof(size), offsetof(flags), sizeof(p6w_media_event), offsetof(event), offsetof(result),
 * offsetof(error)}; see puae6_guest.cpp
 * for the order. Returns the number of values written. */
#define P6W_LAYOUT_VALUES 22

#ifdef __cplusplus
} /* extern "C" */
#endif
#endif
