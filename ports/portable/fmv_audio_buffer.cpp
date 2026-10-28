// Bounded synchronous PCM storage used by WinUAE's L64111 path. Playback and
// volume remain audio.cpp's audio_cda_* stream, advanced by emulated time.
#include "sysconfig.h"
#include "sysdeps.h"
#include "cda_play.h"

#include <limits.h>

cda_audio::cda_audio(int num_sectors, int sector_size, int sample_rate)
{
    (void)sample_rate;
    if (num_sectors <= 0 || sector_size <= 0 || num_sectors > INT_MAX / sector_size) return;
    const size_t bytes = size_t(num_sectors) * size_t(sector_size);
    for (auto &buffer : buffers) buffer = xcalloc(uae_u8, bytes);
    if (!buffers[0] || !buffers[1]) {
        for (auto &buffer : buffers) { xfree(buffer); buffer = nullptr; }
    }
}

cda_audio::~cda_audio()
{
    for (auto &buffer : buffers) { xfree(buffer); buffer = nullptr; }
}

void cda_audio::setvolume(int left, int right)
{
    // cd32_fmv.cpp applies the same values to cd_audio_state through
    // audio_cda_volume; the Win32 class only cached them for its host device.
    (void)left; (void)right;
}
