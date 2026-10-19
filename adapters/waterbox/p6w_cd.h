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
/* Return exact byte count, or a negative error. No exception crosses callback. */
typedef int32_t (*p6w_cd_read_fn)(uint64_t offset, void *destination, uint32_t count);
#endif
