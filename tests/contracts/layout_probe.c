/* HARNESS: layout of the native 64-bit ABI only; no emulated instructions. */
#include <stddef.h>
#include <stdint.h>
#include "p6.h"
#ifdef __cplusplus
#include <type_traits>
#define CHECK static_assert
#define ALIGN alignof
#else
#define CHECK _Static_assert
#define ALIGN _Alignof
#endif
#define LAYOUT(type, bytes, alignment) \
    CHECK(sizeof(type) == (bytes), #type " size"); \
    CHECK(ALIGN(type) == (alignment), #type " alignment")
#define OFFSET(type, field, offset) CHECK(offsetof(type, field) == (offset), #type "." #field)
CHECK(sizeof(void *) == 8, "This probe validates the native 64-bit ABI only");
#ifdef __cplusplus
CHECK((std::is_same<decltype(&p6_cartridge_bind),p6_result (*)(uint32_t,p6_buffer,p6_error *)>::value), "cartridge C ABI signature");
#else
CHECK(_Generic(&p6_cartridge_bind,p6_result (*)(uint32_t,p6_buffer,p6_error *):1,default:0), "cartridge C ABI signature");
#endif
CHECK(P6_CAP_AR1200 == (UINT64_C(1)<<14) && P6_CARTRIDGE_AR1200==1200, "AR1200 additive ABI values");
CHECK(P6_CAP_AR1 == (UINT64_C(1)<<13) && P6_CARTRIDGE_AR1==1, "AR1 additive ABI values");
CHECK(P6_CAP_AR23 == (UINT64_C(1)<<12) && P6_CARTRIDGE_AR2==2 && P6_CARTRIDGE_AR3==3, "cartridge ABI values");
LAYOUT(p6_result, 4, 4);
LAYOUT(p6_error, 264, 4);
LAYOUT(p6_caps, 32, 8);
LAYOUT(p6_config, 72, 8);
LAYOUT(p6_buffer, 16, 8);
LAYOUT(p6_media, 56, 8);
LAYOUT(p6_port_input, 16, 4);
LAYOUT(p6_input, 72, 4);
LAYOUT(p6_input_v2, 108, 4);
OFFSET(p6_input_v2, base, 0);
OFFSET(p6_input_v2, parallel, 72);
OFFSET(p6_input_v2, commands, 104);
LAYOUT(p6_rect, 16, 4);
LAYOUT(p6_frame, 128, 8);
LAYOUT(p6_memory_region, 24, 8);
LAYOUT(p6_media_event, 48, 8);
OFFSET(p6_error, message, 8);
OFFSET(p6_caps, flags, 8);
OFFSET(p6_config, random_seed, 24);
OFFSET(p6_config, rtc_epoch_seconds, 32);
OFFSET(p6_config, chip_kib, 40);
OFFSET(p6_config, slow_kib, 44);
OFFSET(p6_config, fast_kib, 48);
OFFSET(p6_config, fpu_model, 52);     /* T54: the union with reserved32 */
OFFSET(p6_config, reserved32, 52);
OFFSET(p6_config, sound_separation, 56);       /* T73: the first bytes of what was reserved[0] */
OFFSET(p6_config, sound_interpolation, 57);
OFFSET(p6_config, sound_filter, 58);
OFFSET(p6_config, sound_filter_type, 59);
OFFSET(p6_config, floppy_speed, 60);           /* T62: the fifth byte of what was reserved[0] */
OFFSET(p6_config, video_resolution, 61);       /* T72: the sixth and seventh bytes of what was reserved[0] */
OFFSET(p6_config, video_line_mode, 62);
OFFSET(p6_config, cpu_model, 63);             /* T52: the eighth byte of what was reserved[0] */
OFFSET(p6_config, cpu_multiplier, 64);       /* T52: the first byte of what was reserved[1] */
OFFSET(p6_config, hdf_controller, 65);       /* T56: the second byte of what was reserved[1] */
OFFSET(p6_config, cpu_compatibility, 66);       /* T53: formerly reserved */
OFFSET(p6_config, floppy_drives, 67);
OFFSET(p6_config, collision_level, 68);
OFFSET(p6_config, reserved7, 69);
OFFSET(p6_config, z3_memory, 70);
OFFSET(p6_config, reserved8, 71);
OFFSET(p6_buffer, bytes, 8);
OFFSET(p6_media, content_id, 8);
OFFSET(p6_media, image, 40);
OFFSET(p6_port_input, dx, 8);
OFFSET(p6_port_input, dy, 10);
OFFSET(p6_port_input, reserved, 12);
OFFSET(p6_input, keys, 8);
OFFSET(p6_input, ports, 24);
OFFSET(p6_input, reserved, 56);
OFFSET(p6_frame, video, 8);
OFFSET(p6_frame, video_bytes, 16);
OFFSET(p6_frame, video_format, 24);
OFFSET(p6_frame, active, 40);
OFFSET(p6_frame, aspect_num, 56);
OFFSET(p6_frame, audio, 64);
OFFSET(p6_frame, audio_frames, 72);
OFFSET(p6_frame, master_ticks, 80);
OFFSET(p6_frame, unit_index, 88);
OFFSET(p6_frame, cadence_num, 96);
OFFSET(p6_frame, field_parity, 104);
OFFSET(p6_frame, flags, 108);
OFFSET(p6_frame, reserved, 112);
OFFSET(p6_memory_region, data, 8);
OFFSET(p6_media_event, media_index, 16);
OFFSET(p6_media_event, sequence, 24);
OFFSET(p6_media_event, reserved, 32);
CHECK(P6_OK == 0 && P6_ERROR_INTERNAL == 11, "result code numbering");
CHECK(sizeof(((p6_port_input *)0)->dx) == 2, "signed mouse delta storage");
int main(void) { return 0; }
