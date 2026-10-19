// Private value component. Transport, backend, RAM and scheduler are separate.
#pragma once
#include "cd32_nvram.h"
// One inventory drives declarations/capture/apply; no dump of globals or padding.
#define P6_AKIKO_CONTROL_FIELDS(X) \
 X(uint32_t, cdrom_intreq) \
 X(uint32_t, cdrom_intena) \
 X(uint32_t, cdrom_addressdata) \
 X(uint32_t, cdrom_addressmisc) \
 X(uint32_t, cdrom_flags) \
 X(uint32_t, cdrom_pbx) \
 X(uint32_t, cdrom_toc_crc) \
 X(uint8_t, cdrom_subcodeoffset) \
 X(uint8_t, cdcomtxinx) \
 X(uint8_t, cdcomrxinx) \
 X(uint8_t, cdcomtxcmp) \
 X(uint8_t, cdcomrxcmp) \
 X(uint8_t, cdrom_command) \
 X(uint8_t, cdrom_last_rx) \
 X(uint8_t, cdrom_door) \
 X(int32_t, cdrom_toc_counter) \
 X(int32_t, qcode_valid) \
 X(int32_t, cdrom_disk) \
 X(int32_t, cdrom_paused) \
 X(int32_t, cdrom_playing) \
 X(int32_t, cdrom_audiostatus) \
 X(int32_t, cdrom_command_active) \
 X(int32_t, cdrom_command_length) \
 X(int32_t, cdrom_checksum_error) \
 X(int32_t, cdrom_unknown_command) \
 X(int32_t, cdrom_data_offset) \
 X(int32_t, cdrom_speed) \
 X(int32_t, cdrom_sector_counter) \
 X(int32_t, cdrom_current_sector) \
 X(int32_t, cdrom_seek_delay) \
 X(int32_t, cdrom_audiotimeout) \
 X(int32_t, cdrom_led) \
 X(int32_t, cdrom_receive_length) \
 X(int32_t, cdrom_receive_offset) \
 X(int32_t, cd_initialized) \
 X(int32_t, cdrom_tx_dma_delay) \
 X(int32_t, cdrom_rx_dma_delay) \
 X(int32_t, lastmediastate) \
 X(int32_t, mediachanged) \
 X(int32_t, last_play_pos) \
 X(int32_t, last_play_end) \
 X(int32_t, subcodecounter) \
 X(int32_t, akiko_read_offset) \
 X(int32_t, akiko_write_offset) \
 X(int32_t, unitnum)
struct p6_akiko_controller_state {
    uint32_t config; // CD=1, C2P=2, NVRAM=4, NTSC=8; must match current profile.
#define P6_AKIKO_DECLARE(type, name) type name;
    P6_AKIKO_CONTROL_FIELDS(P6_AKIKO_DECLARE)
#undef P6_AKIKO_DECLARE
    uint32_t input[8], result[8];
    uint8_t command[32], response[32], toc_bytes[103 * 13], q[16];
    struct Entry { uint8_t adr, control, tno, point, track, zero, crc[2]; int32_t address, paddress; };
    struct Toc {
        int32_t first_track, first_track_offset, last_track, last_track_offset;
        int32_t firstaddress, lastaddress, tracks, points;
        Entry entry[103];
    } toc;
    struct Clock {
        int64_t cycle;
        uint64_t rate, sector, qcode, media;
        uint32_t first, q_due, media_due, speed;
    } clock;
    p6_cd32_nvram_state nvram;
};
// Quiescent initialized/reset CD transport. Candidate cycle belongs to the
// outer scheduler snapshot, not the current process. All calls are allocation,
// I/O, callback, IRQ and advancement free; apply validates before any mutation.
// Restore shared scheduler/Paula before advancing; this is not a machine codec.
bool p6_akiko_controller_get(p6_akiko_controller_state *);
// Candidate validation works before destination owners exist (native load preflight).
bool p6_akiko_controller_validate(const p6_akiko_controller_state *, uint32_t config, int64_t cycle);
bool p6_akiko_controller_preflight(const p6_akiko_controller_state *, int64_t cycle);
bool p6_akiko_controller_apply(const p6_akiko_controller_state *, int64_t cycle);
