// FMV-B2: scalar MPEG-1/Layer II exact-output and allocation probe.
#include "sysconfig.h"
#include "archivers/mp2/kjmp2.h"
#include "media/sha256.h"

extern "C" {
#include "mpeg2.h"
#include "mpeg2convert.h"
}

#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

struct Allocation {
    void *pointer;
    unsigned size;
    mpeg2_alloc_t reason;
};

std::array<Allocation, 64> allocations{};
size_t allocation_count;
size_t live_bytes;
size_t peak_bytes;
std::array<size_t, 5> peak_by_reason{};

extern "C" void *tracked_alloc(unsigned size, mpeg2_alloc_t reason)
{
    assert(allocation_count < allocations.size() && reason <= MPEG2_ALLOC_CONVERTED);
    void *pointer = nullptr;
    assert(posix_memalign(&pointer, 64, size ? size : 1) == 0);
    allocations[allocation_count++] = {pointer, size, reason};
    live_bytes += size;
    if (live_bytes > peak_bytes) peak_bytes = live_bytes;
    peak_by_reason[reason] += size;
    return pointer;
}

extern "C" int tracked_free(void *pointer)
{
    if (!pointer) return 1;
    for (size_t i = 0; i < allocation_count; ++i) {
        if (allocations[i].pointer != pointer) continue;
        assert(live_bytes >= allocations[i].size);
        live_bytes -= allocations[i].size;
        std::free(pointer);
        allocations[i] = allocations[--allocation_count];
        return 1;
    }
    return 0;
}

std::vector<uint8_t> read_file(const char *path)
{
    FILE *file = std::fopen(path, "rb");
    assert(file && std::fseek(file, 0, SEEK_END) == 0);
    const long length = std::ftell(file);
    assert(length > 0 && std::fseek(file, 0, SEEK_SET) == 0);
    std::vector<uint8_t> bytes(static_cast<size_t>(length));
    assert(std::fread(bytes.data(), 1, bytes.size(), file) == bytes.size());
    std::fclose(file);
    return bytes;
}

void print_sha256(const uint8_t *bytes, size_t length)
{
    uint8_t digest[P6_SHA256_DIGEST_BYTES];
    p6_sha256_hash(bytes, length, digest);
    for (uint8_t byte : digest) std::printf("%02x", byte);
}

char picture_type(const mpeg2_picture_t *picture)
{
    assert(picture);
    switch (picture->flags & PIC_MASK_CODING_TYPE) {
        case PIC_FLAG_CODING_TYPE_I: return 'I';
        case PIC_FLAG_CODING_TYPE_P: return 'P';
        case PIC_FLAG_CODING_TYPE_B: return 'B';
        default: return '?';
    }
}

bool bounded_parse(std::vector<uint8_t> bytes)
{
    const uint8_t sequence_end[] = {0, 0, 1, 0xb7, 0, 0, 0, 0};
    bytes.insert(bytes.end(), std::begin(sequence_end), std::end(sequence_end));
    mpeg2dec_t *decoder = mpeg2_init();
    assert(decoder);
    const mpeg2_info_t *info = mpeg2_info(decoder);
    mpeg2_buffer(decoder, bytes.data(), bytes.data() + bytes.size());
    bool oversize = false;
    unsigned iterations = 0;
    for (; iterations < 10000; ++iterations) {
        const mpeg2_state_t state = mpeg2_parse(decoder);
        if (state == STATE_BUFFER || state == STATE_INVALID || state == STATE_INVALID_END) break;
        if (state == STATE_SEQUENCE) {
            assert(info->sequence);
            if (!info->sequence->width || !info->sequence->height ||
                info->sequence->width > 352 || info->sequence->height > 288) {
                oversize = true;
                mpeg2_reset(decoder, 1);
                break;
            }
            assert(mpeg2_convert(decoder, mpeg2convert_rgb32, nullptr) == 0);
        }
    }
    assert(iterations < 10000);
    mpeg2_close(decoder);
    return oversize;
}

void malformed_probe(const char *valid_path)
{
    const auto valid = read_file(valid_path);
    for (size_t length = 0; length < 96; ++length) {
        const size_t available = length < valid.size() ? length : valid.size();
        assert(!bounded_parse(std::vector<uint8_t>(valid.begin(), valid.begin() + available)));
    }
    auto oversize = valid;
    assert(oversize.size() > 7 && !std::memcmp(oversize.data(), "\0\0\1\xb3", 4));
    const unsigned packed = (353u << 12) | 288u;
    oversize[4] = static_cast<uint8_t>(packed >> 16);
    oversize[5] = static_cast<uint8_t>(packed >> 8);
    oversize[6] = static_cast<uint8_t>(packed);
    assert(bounded_parse(oversize));
    // Fill libmpeg2's complete 1194 KiB chunk and cross the boundary without
    // any start code: it must return INVALID rather than write past the chunk.
    assert(!bounded_parse(std::vector<uint8_t>(1194u * 1024u + 64u, 0x55)));
    std::puts("malformed truncation/oversize/chunk-bound PASS");
}

void video_probe(const char *path)
{
    auto bytes = read_file(path);
    // Close the elementary stream explicitly, then provide the four-byte
    // sentinel that libmpeg2's bit reader is permitted to inspect.
    const uint8_t sequence_end[] = {0, 0, 1, 0xb7, 0, 0, 0, 0};
    bytes.insert(bytes.end(), std::begin(sequence_end), std::end(sequence_end));
    allocation_count = live_bytes = peak_bytes = 0;
    peak_by_reason.fill(0);
    mpeg2_malloc_hooks(tracked_alloc, tracked_free);
    mpeg2dec_t *decoder = mpeg2_init();
    assert(decoder);
    const mpeg2_info_t *info = mpeg2_info(decoder);
    mpeg2_buffer(decoder, bytes.data(), bytes.data() + bytes.size());

    unsigned frames = 0;
    for (unsigned iterations = 0; iterations < 10000; ++iterations) {
        const mpeg2_state_t state = mpeg2_parse(decoder);
        if (state == STATE_BUFFER) break;
        assert(state != STATE_INVALID && state != STATE_INVALID_END);
        if (state == STATE_SEQUENCE) {
            assert(info->sequence);
            assert(info->sequence->width == 352 && info->sequence->height == 288);
            assert(mpeg2_convert(decoder, mpeg2convert_rgb32, nullptr) == 0);
        }
        if ((state == STATE_SLICE || state == STATE_END) && info->display_fbuf) {
            assert(info->display_picture && info->sequence);
            const size_t size = size_t(info->sequence->width) * info->sequence->height * 4;
            uint32_t box = 0, background = 0;
            const unsigned box_x = frames * 24 + 16;
            std::memcpy(&box, info->display_fbuf->buf[0] + (112 * 352 + box_x) * 4, 4);
            std::memcpy(&background, info->display_fbuf->buf[0] + (250 * 352 + 340) * 4, 4);
            // Independent source oracle: the generated frame is black with a
            // saturated red 32x32 box moving 24 pixels to the right per frame.
            assert((box & 0x00ff0000) >= 0x00e00000 && (box & 0x0000ffff) <= 0x00002020);
            assert((background & 0x00ffffff) <= 0x00080808);
            std::printf("video %u %c ", frames++, picture_type(info->display_picture));
            print_sha256(info->display_fbuf->buf[0], size);
            std::putchar('\n');
        }
    }
    assert(frames == 7);
    std::printf("mpeg-memory peak=%zu context=%zu chunk=%zu yuv=%zu convert-id=%zu frames=%zu mp2-context=%zu\n",
                peak_bytes, peak_by_reason[MPEG2_ALLOC_MPEG2DEC], peak_by_reason[MPEG2_ALLOC_CHUNK],
                peak_by_reason[MPEG2_ALLOC_YUV], peak_by_reason[MPEG2_ALLOC_CONVERT_ID],
                peak_by_reason[MPEG2_ALLOC_CONVERTED], sizeof(kjmp2_context_t));
    mpeg2_close(decoder);
    assert(live_bytes == 0 && allocation_count == 0);
    mpeg2_malloc_hooks(nullptr, nullptr);
}

void audio_probe(const char *label, const char *path, bool expect_silence)
{
    auto encoded = read_file(path);
    assert(encoded.size() <= KJMP2_MAX_FRAME_SIZE);
    encoded.resize(KJMP2_MAX_FRAME_SIZE + 4, 0);
    std::array<signed short, KJMP2_SAMPLES_PER_FRAME * 2> pcm{};
    kjmp2_context_t context{};
    kjmp2_init(&context);
    const unsigned long consumed = kjmp2_decode_frame(&context, encoded.data(), pcm.data());
    assert(consumed > 4 && consumed <= KJMP2_MAX_FRAME_SIZE);
    std::array<uint8_t, KJMP2_SAMPLES_PER_FRAME * 4> canonical{};
    for (size_t i = 0; i < pcm.size(); ++i) {
        const uint16_t sample = static_cast<uint16_t>(pcm[i]);
        if (expect_silence) assert(sample == 0);
        canonical[i * 2] = static_cast<uint8_t>(sample);
        canonical[i * 2 + 1] = static_cast<uint8_t>(sample >> 8);
    }
    std::printf("audio %s consumed=%lu rate=%d samples=%d hash=", label, consumed,
                kjmp2_get_sample_rate(encoded.data()), KJMP2_SAMPLES_PER_FRAME);
    print_sha256(canonical.data(), canonical.size());
    std::putchar('\n');

    std::array<uint8_t, KJMP2_MAX_FRAME_SIZE + 4> invalid{};
    kjmp2_init(&context);
    assert(kjmp2_decode_frame(&context, invalid.data(), pcm.data()) == 0);
    invalid[0] = 0xff; invalid[1] = 0xfd; invalid[2] = 0;
    assert(kjmp2_decode_frame(&context, invalid.data(), pcm.data()) == 0);
}

void quiet_audio_probe(const char *path)
{
    auto encoded = read_file(path);
    const size_t encoded_size = encoded.size();
    encoded.resize(encoded_size + KJMP2_MAX_FRAME_SIZE + 4, 0);
    std::array<signed short, KJMP2_SAMPLES_PER_FRAME * 2> frame{};
    std::vector<signed short> pcm;
    kjmp2_context_t context{};
    kjmp2_init(&context);
    size_t offset = 0;
    unsigned frames = 0;
    while (offset + 4 < encoded_size) {
        const unsigned long consumed = kjmp2_decode_frame(&context, encoded.data() + offset,
                                                          frame.data());
        assert(consumed > 4 && offset + consumed <= encoded_size);
        pcm.insert(pcm.end(), frame.begin(), frame.end());
        offset += consumed;
        ++frames;
    }
    assert(offset == encoded_size && frames == 10);
    assert(pcm.size() == 11520 * 2);

    unsigned run = 0;
    unsigned maximum_run = 0;
    for (size_t sample = 1000; sample < 10520; ++sample) {
        const bool zero = pcm[sample * 2] == 0 && pcm[sample * 2 + 1] == 0;
        run = zero ? run + 1 : 0;
        if (run > maximum_run) maximum_run = run;
    }
    assert(maximum_run == 1);

    std::vector<uint8_t> canonical(pcm.size() * 2);
    for (size_t i = 0; i < pcm.size(); ++i) {
        const uint16_t sample = static_cast<uint16_t>(pcm[i]);
        canonical[i * 2] = static_cast<uint8_t>(sample);
        canonical[i * 2 + 1] = static_cast<uint8_t>(sample >> 8);
    }
    std::printf("audio quiet-multitone consumed=%zu frames=%u rate=%d samples=%zu "
                "body-max-stereo-zero-run=%u hash=", offset, frames,
                kjmp2_get_sample_rate(encoded.data()), pcm.size() / 2, maximum_run);
    print_sha256(canonical.data(), canonical.size());
    std::putchar('\n');
}

} // namespace

int main(int argc, char **argv)
{
    assert(argc == 5);
    malformed_probe(argv[1]);
    video_probe(argv[1]);
    audio_probe("tone", argv[2], false);
    audio_probe("silence", argv[3], true);
    quiet_audio_probe(argv[4]);
    return 0;
}
