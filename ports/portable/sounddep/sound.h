/* PUAE6 synchronous PCM boundary. SPDX-License-Identifier: GPL-2.0-or-later
 * Names/constants follow WinUAE 6030 od-win32/sounddep/sound.h; no device API.
 * Only SND_STEREO is supported. Lifecycle/config validation is separate. */
#ifndef P6_SOUND_H
#define P6_SOUND_H
#include <stddef.h>
#include <stdint.h>

#define SOUNDSTUFF 1
#define SOUND_MODE_NG 0
#define SOUND16_BASE_VAL 0
#define SOUND8_BASE_VAL 128
#define DEFAULT_SOUND_MAXB 16384
#define DEFAULT_SOUND_MINB 16384
#define DEFAULT_SOUND_BITS 16
#define DEFAULT_SOUND_FREQ 44100
#define HAVE_STEREO_SUPPORT
#define FILTER_SOUND_OFF 0
#define FILTER_SOUND_EMUL 1
#define FILTER_SOUND_ON 2
#define FILTER_SOUND_TYPE_A500 0
#define FILTER_SOUND_TYPE_A1200 1
#define FILTER_SOUND_TYPE_A500_FIXEDONLY 2

enum { P6_PCM_STAGE_FRAMES = 512, P6_PCM_CAPACITY_FRAMES = 8192 };
enum { P6_PCM_OK = 0, P6_PCM_CAPACITY = -1, P6_PCM_ARGUMENT = -2,
       P6_PCM_ALIGNMENT = -3, P6_SOUND_STATE = -4,
       P6_SOUND_UNSUPPORTED = -5, P6_SOUND_CLOCK = -6 };
extern uae_u16 paula_sndbuffer[];
extern uae_u16 *paula_sndbufpt;
extern int paula_sndbufsize; // Bytes, as expected by check_sound_buffers.
extern int active_sound_stereo;

// Internal pull transport, not the public p6 ABI. Preserve upstream write order
// (put_sound_word_right, then left); frontend channel mapping is separate.
// Capacity/count use stereo
// frames. Capacity/alignment errors poison output until reset; bad read
// arguments leave output intact. Never return partial
// success after overflow. No allocation, host callbacks, or timing feedback.
// 8192 is a transport bound, not a proved maximum per emulated frame/rate.
// Lifecycle must validate supported rates and bridge must propagate status.
void p6_pcm_reset();
int p6_pcm_status();
int p6_pcm_read(int16_t *dst, size_t capacity_frames, size_t *written);
void p6_pcm_put_word(uae_u16 value);
// Delivery changes discard presentation data but preserve sticky errors.
void p6_pcm_set_delivery(bool enabled);
void finish_sound_buffer();
#define PUT_SOUND_WORD(b) p6_pcm_put_word((uae_u16)(b))
#define PUT_SOUND_WORD_MONO(b) PUT_SOUND_WORD(b)

// Internal lifecycle status; the public bridge must check before advancing and
// propagate failures. close/init is required after a configuration/clock fault.
int p6_sound_status();
struct uae_prefs;
bool p6_sound_validate_config(const uae_prefs &prefs);
int init_sound();
int setup_sound();
void close_sound();
void reset_sound();
void restart_sound_buffer();
void pause_sound_buffer();
void pause_sound();
void resume_sound();
bool sound_paused();
void sound_volume(int);
#endif
