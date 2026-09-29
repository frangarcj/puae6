// Real Paula scaling, without DMA, clocks, host devices or filter setup.
#include <audio.cpp>
#include <cassert>
#include <cstdint>

uae_prefs currprefs{};

static int64_t floor_div(int64_t n, int64_t d)
{
    return n >= 0 ? n / d : -((-n + d - 1) / d);
}

static void finish_ranges()
{
    const int volumes[] = {0, 1, 16384, 32767, 32768};
    for (int bits = 14; bits <= 18; ++bits) {
        const int bound = 1 << (bits - 1);
        for (int volume : volumes) {
            sound_paula_volume[0] = sound_paula_volume[1] = volume;
            for (int value = -bound; value < bound; ++value) {
                int64_t scaled = bits < 16 ? int64_t(value) * (1 << (16 - bits))
                    : floor_div(value, 1 << (bits - 16));
                int expected = int(scaled * volume / 32768);
                assert(FINISH_DATA(value, bits, 0) == expected);
                assert(FINISH_DATA(value, bits, 1) == expected);
            }
        }
    }
}

static void stereo_signed()
{
    currprefs.sound_filter = 0;
    sound_paula_volume[0] = sound_paula_volume[1] = 32768;
    // Every signed 8-bit sample and all Paula 6-bit volume values, including
    // AUDxVOL's bit-6 maximum (64), through the actual stereo mixer.
    for (int sample = -128; sample <= 127; ++sample) {
        for (int volume = 0; volume <= 64; ++volume) {
            for (int ch = 0; ch < 4; ++ch) {
                audio_channel[ch].data.current_sample = ch < 2 ? sample : 0;
                audio_channel[ch].data.mixvol = volume;
                audio_channel[ch].data.adk_mask = ~0u;
            }
            p6_pcm_reset();
            sample16s_handler();
            int16_t out[2]; size_t count;
            assert(p6_pcm_read(out, 1, &count) == 0 && count == 1);
            assert(out[0] == sample * volume * 2 && out[1] == sample * volume * 2);
        }
    }
    for (int ch = 0; ch < 4; ++ch) {
        audio_channel[ch].data.current_sample = -128;
        audio_channel[ch].data.mixvol = 64;
    }
    p6_pcm_reset(); sample16s_handler();
    int16_t out[2]; size_t count;
    assert(p6_pcm_read(out, 1, &count) == 0 && count == 1);
    assert(out[0] == -32768 && out[1] == -32768);
}

static void sinc_steps()
{
    auto &d = audio_channel[0].data;
    audio_data[0] = &d;
    const int levels[] = {-8192, -1, 0, 1, 8128};
    const int tables[] = {0, 1, 4}; // A500 LED off/on and unfiltered sinc.
    for (int table : tables) {
    sound_use_filter_sinc = table == 4 ? 0 : FILTER_MODEL_A500;
    led_filter_on = table == 1;
    for (int before : levels) for (int after : levels) {
        memset(&d, 0, sizeof d);
        d.sinc_output_state = after;
        d.sinc_queue_head = 0;
        d.sinc_queue[0].output = after - before;
        d.sinc_queue[1].time = -SINC_QUEUE_MAX_AGE;
        for (int age = 0; age <= SINC_QUEUE_MAX_AGE; ++age) {
            d.sinc_queue_time = age;
            int64_t sum = int64_t(after) * 131072;
            if (age < SINC_QUEUE_MAX_AGE)
                sum -= int64_t(winsinc_integral[table][age]) * (after - before);
            int64_t expected = floor_div(sum, 32768);
            if (expected < -32768) expected = -32768;
            if (expected > 32767) expected = 32767;
            int result;
            samplexx_sinc_handler(&result, 0, 1);
            assert(result == expected);
        }
    }
    }
}

int main(int argc, char **argv)
{
    // Separate repro modes let sanitizers expose both original signed shifts.
    if (argc == 1 || !strcmp(argv[1], "finish")) finish_ranges();
    if (argc == 1 || !strcmp(argv[1], "stereo")) stereo_signed();
    if (argc == 1 || !strcmp(argv[1], "sinc")) sinc_steps();
}
