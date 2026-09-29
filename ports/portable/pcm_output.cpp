/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "sysconfig.h"
#include "sysdeps.h"
#include "audio.h"
#include "sounddep/sound.h"

uae_u16 paula_sndbuffer[P6_PCM_STAGE_FRAMES * 2];
uae_u16 *paula_sndbufpt = paula_sndbuffer;
int paula_sndbufsize = sizeof paula_sndbuffer;
int active_sound_stereo = SND_STEREO;
static int16_t pcm[P6_PCM_CAPACITY_FRAMES * 2];
static size_t queued_frames;
static int output_error;
static bool delivery_enabled = true;

void p6_pcm_set_delivery(bool enabled)
{
    if (enabled != delivery_enabled) {
        paula_sndbufpt = paula_sndbuffer;
        queued_frames = 0;
    }
    delivery_enabled = enabled;
}

void p6_pcm_reset()
{
    memset(paula_sndbuffer, 0, sizeof paula_sndbuffer);
    memset(pcm, 0, sizeof pcm);
    paula_sndbufpt = paula_sndbuffer;
    queued_frames = 0;
    output_error = P6_PCM_OK;
}

int p6_pcm_status() { return output_error; }

void finish_sound_buffer()
{
    const size_t words = paula_sndbufpt - paula_sndbuffer;
    if (!output_error) {
        if (words % 2) output_error = P6_PCM_ALIGNMENT;
        else if (words / 2 > P6_PCM_CAPACITY_FRAMES - queued_frames)
            output_error = P6_PCM_CAPACITY;
        else {
            // Copy the two's-complement sample representation, without
            // implementation-defined unsigned-to-signed numeric conversion.
            memcpy(pcm + queued_frames * 2, paula_sndbuffer, words * sizeof(uae_u16));
            queued_frames += words / 2;
        }
    }
    // Continue accepting synthesis writes safely even after an error. Reads
    // keep reporting the failure; neither samples nor success are fabricated.
    paula_sndbufpt = paula_sndbuffer;
}

void p6_pcm_put_word(uae_u16 value)
{
    if (!delivery_enabled) return;
    if (!output_error && queued_frames * 2 + (paula_sndbufpt - paula_sndbuffer)
            >= P6_PCM_CAPACITY_FRAMES * 2)
        output_error = P6_PCM_CAPACITY;
    if (paula_sndbufpt == paula_sndbuffer + P6_PCM_STAGE_FRAMES * 2)
        finish_sound_buffer();
    *paula_sndbufpt++ = value;
}

int p6_pcm_read(int16_t *dst, size_t capacity_frames, size_t *written)
{
    if (!written || (!dst && capacity_frames)) return P6_PCM_ARGUMENT;
    *written = 0;
    if (output_error) return output_error;
    // Zero capacity is a non-mutating read, not an acknowledgement/drop.
    if (!capacity_frames) return P6_PCM_OK;
    const size_t words = paula_sndbufpt - paula_sndbuffer;
    if (words % 2) {
        output_error = P6_PCM_ALIGNMENT;
        return output_error;
    }
    // Drain queued frames before flushing the staging area so consuming a
    // prefix never creates an artificial overflow in an otherwise valid read.
    size_t take = queued_frames < capacity_frames ? queued_frames : capacity_frames;
    if (take) {
        memcpy(dst, pcm, take * 2 * sizeof(int16_t));
        queued_frames -= take;
        memmove(pcm, pcm + take * 2, queued_frames * 2 * sizeof(int16_t));
    }
    size_t extra = capacity_frames - take;
    if (extra > words / 2) extra = words / 2;
    if (!queued_frames && extra) {
        memcpy(dst + take * 2, paula_sndbuffer, extra * 2 * sizeof(int16_t));
        memmove(paula_sndbuffer, paula_sndbuffer + extra * 2,
                (words - extra * 2) * sizeof(uae_u16));
        paula_sndbufpt -= extra * 2;
        take += extra;
    }
    *written = take;
    return P6_PCM_OK;
}
