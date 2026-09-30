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

/* Sizes and offsets the guest was compiled with, for the host to check its own layout against: {sizeof(p6w_init), offsetof(result),
 * offsetof(error), sizeof(p6w_frame), offsetof(frame), offsetof(result), sizeof(p6_frame), sizeof(p6_input), sizeof(p6_config),
 * sizeof(p6w_media), offsetof(media), ...}; see puae6_guest.cpp for the order. Returns the number of values written. */
#define P6W_LAYOUT_VALUES 14

#ifdef __cplusplus
} /* extern "C" */
#endif
#endif
