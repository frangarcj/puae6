/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "custom.h"
#include "audio.h"
#include "gensound.h"
#include "sounddep/sound.h"
#include "drive_sound.h"
#include "driveclick.h"

static bool opened, paused, buffer_paused, ntsc;
static int rate;
static int lifecycle_error = P6_SOUND_STATE;

static float chipset_clock()
{
    return ntsc ? CHIPSET_CLOCK_NTSC : CHIPSET_CLOCK_PAL;
}

static bool transport_supported(const uae_prefs &p)
{
    return p.sound_stereo == SND_STEREO
        && (p.sound_freq == 44100 || p.sound_freq == 48000)
        && (p.produce_sound == 2 || p.produce_sound == 3)
        && !p.sound_auto && !p.sound_stereo_swap_paula
        && p.sound_volume_master == 0
        && p.sound_volume_paula >= 0 && p.sound_volume_paula <= 100;
}

static void fail(int error)
{
    if (lifecycle_error == P6_PCM_OK || lifecycle_error == P6_SOUND_STATE)
        lifecycle_error = error;
    p6_pcm_set_delivery(false);
}

bool p6_sound_validate_config(const uae_prefs &p)
{
    // Validate before set_audio shifts/multiplies or changes live preferences.
    // Public machine/profile validation remains the bridge's responsibility.
    const bool supported = transport_supported(p)
        && p.sound_interpol >= 0 && p.sound_interpol <= 4
        && p.sound_filter >= FILTER_SOUND_OFF && p.sound_filter <= FILTER_SOUND_ON
        && p.sound_filter_type >= FILTER_SOUND_TYPE_A500
        && p.sound_filter_type <= FILTER_SOUND_TYPE_A500_FIXEDONLY
        && p.sound_stereo_separation >= 0 && p.sound_stereo_separation <= 10
        && p.sound_mixed_stereo_delay >= 0 && p.sound_mixed_stereo_delay < 31
        && (1u << p.sound_mixed_stereo_delay) <= SOUND_MAX_DELAY_BUFFER
        && p.sound_volume_cd <= 100; // Upstream clamps negative CD attenuation.
    if (!supported) fail(P6_SOUND_UNSUPPORTED);
    return supported;
}

int p6_sound_status()
{
    if (!p6_drive_sound::healthy()) return P6_SOUND_UNSUPPORTED;
    return lifecycle_error ? lifecycle_error : p6_pcm_status();
}

int setup_sound()
{
    sound_available = 1; // The statically allocated PCM transport is available.
    return 1;
}

int init_sound()
{
    if (!sound_available) return 0;
    if (!transport_supported(currprefs)) { fail(P6_SOUND_UNSUPPORTED); return 0; }
    if (opened) {
        if (rate != currprefs.sound_freq || ntsc != bool(currprefs.ntscmode))
            fail(P6_SOUND_UNSUPPORTED);
        return p6_sound_status() == P6_PCM_OK;
    }
    if (lifecycle_error != P6_SOUND_STATE) return 0;
    rate = currprefs.sound_freq;
    ntsc = currprefs.ntscmode;
    opened = true;
    paused = buffer_paused = false;
    lifecycle_error = P6_PCM_OK;
    active_sound_stereo = SND_STEREO;
    // set_audio subsequently chooses its configured upstream interpolator.
    sample_handler = sample16s_handler;
    scaled_sample_evtime = chipset_clock() * float(CYCLE_UNIT) / rate;
    p6_pcm_reset();
    p6_pcm_set_delivery(true);
    driveclick_init();
    return 1;
}

void update_sound(float clk)
{
    if (!opened) return; // Upstream allows clock notification before init.
    if (!transport_supported(currprefs) || rate != currprefs.sound_freq || ntsc != bool(currprefs.ntscmode)) {
        fail(P6_SOUND_UNSUPPORTED);
        return;
    }
    // Physical CCK clock, never refresh-rate/host audio-fill adjustment. NaN
    // and infinities also fail this equality. Custom's caller still needs its
    // deterministic pacing patch before whole-core linkage is accepted.
    if (clk != chipset_clock()) { fail(P6_SOUND_CLOCK); return; }
    if (!lifecycle_error)
        scaled_sample_evtime = clk * float(CYCLE_UNIT) / rate;
}

void close_sound()
{
    opened = paused = buffer_paused = false;
    rate = 0;
    lifecycle_error = P6_SOUND_STATE;
    p6_pcm_set_delivery(false);
    p6_pcm_reset();
    sample_handler = nullptr;
    scaled_sample_evtime = 0;
}

void reset_sound()
{
    if (opened) p6_pcm_reset();
}

static void delivery()
{
    p6_pcm_set_delivery(opened && !lifecycle_error && !paused && !buffer_paused);
}
bool sound_paused() { return !opened || paused || buffer_paused; }
void pause_sound() { if (opened) { paused = true; delivery(); } }
void resume_sound() { if (opened) { paused = false; delivery(); } }
void pause_sound_buffer() { if (opened) { buffer_paused = true; delivery(); } }
void restart_sound_buffer() { if (opened) { buffer_paused = false; delivery(); } }
