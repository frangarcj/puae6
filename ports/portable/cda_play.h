// The CD32 FMV cartridge needs only cda_audio's two owned PCM buffers. WinUAE's
// od-win32/cda_play also contains a threaded host CD player; that is deliberately
// not part of the cooperative portable graph.
#ifndef P6_PORTABLE_CDA_PLAY_H
#define P6_PORTABLE_CDA_PLAY_H

extern volatile bool cd_audio_mode_changed;

class cda_audio {
public:
    uae_u8 *buffers[2]{};
    cda_audio(int num_sectors, int sector_size, int sample_rate);
    ~cda_audio();
    void setvolume(int left, int right);
};

#endif
