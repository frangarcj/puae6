// Exercise the real Paula stereo mixer against the portable PCM queue.
#include <audio.cpp>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>

uae_prefs currprefs{};

static constexpr size_t frames = 1025;
static int16_t batch[frames * 2];
static int16_t chunked[frames * 2];

static void prepare()
{
	memset(audio_channel, 0, sizeof audio_channel);
	for (int ch = 0; ch < AUDIO_CHANNELS_PAULA; ++ch) {
		audio_channel[ch].data.mixvol = 1;
		audio_channel[ch].data.adk_mask = ~0u;
	}
	currprefs.sound_filter = 0;
	sound_paula_volume[0] = sound_paula_volume[1] = 32768;
	active_sound_stereo = SND_STEREO;
	assert(paula_sndbufsize == P6_PCM_STAGE_FRAMES * 2 * sizeof(int16_t));
	p6_pcm_reset();
}

static void set_samples(size_t i)
{
	audio_channel[0].data.current_sample = 11 + int(i % 31);
	audio_channel[1].data.current_sample = 17 + int(i % 29);
	audio_channel[2].data.current_sample = 23 + int(i % 37);
	audio_channel[3].data.current_sample = 31 + int(i % 41);
}

static void expected_samples(size_t i, int16_t *out)
{
	int c0 = 11 + int(i % 31), c1 = 17 + int(i % 29);
	int c2 = 23 + int(i % 37), c3 = 31 + int(i % 41);
	out[0] = int16_t(2 * (c0 + c3)); // right: channels 0 + 3
	out[1] = int16_t(2 * (c1 + c2)); // left: channels 1 + 2
}

static void render()
{
	for (size_t i = 0; i < frames; ++i) {
		set_samples(i);
		sample16s_handler();
	}
	assert(p6_pcm_status() == 0);
}

static void mixer_and_batch_read()
{
	prepare();
	render();
	size_t written = 0;
	assert(p6_pcm_read(batch, frames, &written) == 0);
	assert(written == frames && p6_pcm_status() == 0);
	for (size_t i = 0; i < frames; ++i) {
		int16_t expected[2];
		expected_samples(i, expected);
		assert(batch[2 * i] == expected[0]);
		assert(batch[2 * i + 1] == expected[1]);
	}
}

static void chunked_read_matches_batch()
{
	prepare();
	render();
	size_t total = 0;
	const size_t caps[] = {1, 17, 257, 511, 3, 64};
	unsigned call = 0;
	while (total < frames) {
		size_t written = 0;
		size_t cap = caps[call++ % (sizeof caps / sizeof caps[0])];
		assert(p6_pcm_read(chunked + total * 2, cap, &written) == 0);
		assert(written > 0 && written <= cap);
		total += written;
	}
	assert(total == frames && p6_pcm_status() == 0);
	assert(memcmp(batch, chunked, sizeof batch) == 0);
}

static void interleaved_production_and_reads()
{
	prepare();
	size_t total = 0;
	for (size_t i = 0; i < frames; ++i) {
		set_samples(i);
		sample16s_handler();
		if ((i % 19) == 0) {
			size_t n;
			assert(p6_pcm_read(chunked + total * 2, 7, &n) == 0);
			total += n;
		}
	}
	size_t n;
	assert(p6_pcm_read(chunked + total * 2, frames - total, &n) == 0);
	assert(total + n == frames && memcmp(batch, chunked, sizeof batch) == 0);
}

static void capacity_and_reset()
{
	prepare();
	static int16_t out[P6_PCM_CAPACITY_FRAMES * 2];
	for (size_t i = 0; i < P6_PCM_CAPACITY_FRAMES; ++i) {
		PUT_SOUND_WORD(i);
		PUT_SOUND_WORD(~i);
	}
	assert(p6_pcm_status() == P6_PCM_OK);
	size_t n = 99;
	assert(p6_pcm_read(nullptr, 0, &n) == P6_PCM_OK && n == 0);
	assert(p6_pcm_read(nullptr, 1, &n) == P6_PCM_ARGUMENT);
	assert(p6_pcm_read(out, 1, nullptr) == P6_PCM_ARGUMENT);
	assert(p6_pcm_status() == P6_PCM_OK);
	assert(p6_pcm_read(out, P6_PCM_CAPACITY_FRAMES, &n) == 0);
	assert(n == P6_PCM_CAPACITY_FRAMES);
	for (size_t i = 0; i < n; ++i) {
		assert(out[i * 2] == int16_t(i));
		assert(out[i * 2 + 1] == -int(i) - 1);
	}
	assert(p6_pcm_read(out, 1, &n) == 0 && n == 0);
	for (size_t i = 0; i < P6_PCM_CAPACITY_FRAMES + 1; ++i) {
		PUT_SOUND_WORD(1);
		PUT_SOUND_WORD(2);
	}
	assert(p6_pcm_status() == P6_PCM_CAPACITY);
	out[0] = 123;
	assert(p6_pcm_read(out, 1, &n) == P6_PCM_CAPACITY && n == 0 && out[0] == 123);
	// Continued synthesis after failure must remain bounded and visibly failed.
	for (int i = 0; i < P6_PCM_STAGE_FRAMES * 8; ++i) PUT_SOUND_WORD(i);
	finish_sound_buffer();
	assert(p6_pcm_status() == P6_PCM_CAPACITY);
	p6_pcm_reset();
	assert(p6_pcm_read(out, 1, &n) == 0 && n == 0);
	PUT_SOUND_WORD(1); // A read boundary must not split a stereo frame.
	assert(p6_pcm_read(out, 1, &n) == P6_PCM_ALIGNMENT && n == 0);
	p6_pcm_reset();
	PUT_SOUND_WORD(1);
	finish_sound_buffer();
	assert(p6_pcm_status() == P6_PCM_ALIGNMENT);
	p6_pcm_reset();
	assert(p6_pcm_status() == 0 && paula_sndbufpt == paula_sndbuffer);
}

static void signed_pcm_limits()
{
	prepare();
	PUT_SOUND_WORD((uae_u16)0x8000);
	PUT_SOUND_WORD((uae_u16)0x7fff);
	finish_sound_buffer();
	int16_t out[2]{};
	size_t written = 0;
	assert(p6_pcm_read(out, 1, &written) == 0);
	assert(written == 1 && out[0] == INT16_MIN && out[1] == INT16_MAX);
	assert(p6_pcm_status() == 0);
}

int main()
{
	mixer_and_batch_read();
	chunked_read_matches_batch();
	interleaved_production_and_reads();
	signed_pcm_limits();
	capacity_and_reset();
}
