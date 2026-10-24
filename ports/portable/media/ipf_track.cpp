// IPF reader interface (ports/portable/media/ipf_track.h). It includes the decoder of MAME (third_party/mame-ipf/ipf_dsk.cpp, BSD-3-Clause, with the local changes recorded
// in third_party/mame-ipf/PROVENANCE.json) so that it can reach the decoder's own types; everything below the include is this project's. The file boundary is the
// boundary of the licences.
#include "ipf_track.h"

#include "../../../third_party/mame-ipf/ipf_dsk.cpp"

#include <new>
#include <string.h>
#include <vector>

// An allocation that fails is reported (a refusal, a track that does not decode) wherever the build has exceptions. The Waterbox guest is compiled without them (ports/waterbox/Makefile):
// libc++'s throw sites would import std::length_error, std::bad_array_new_length and __cxa_free_exception, which HOST01 does not allow, so there a failed allocation ends the sandbox's
// call as any allocation does in the guest. Every size here is bounded (160000 cells, 256 KiB of data, 168 tracks), so the heap budget (docs/03-waterbox.md) is what decides it.
#if !defined(__cpp_exceptions) && defined(__linux__) && defined(_LIBCPP_VERSION) && _LIBCPP_VERSION < 190000     // the guest: LLVM 18 libc++ on aarch64-linux-gnu (musl)
// Without exceptions libc++ ends in this function where it would throw (a vector that cannot grow). It is overridable, and the guest does not import it from anywhere: the call stops the
// sandbox's call as a trap does (an allocation that fails, or a size past max_size(), which the bounds above make impossible).
namespace std { inline namespace __1 { [[noreturn]] void __libcpp_verbose_abort(const char *, ...) { __builtin_trap(); } } }
#endif

#if defined(__cpp_exceptions)
#define P6_TRY try
#define P6_CATCH(...) catch (...) { __VA_ARGS__ }
#else
#define P6_TRY
#define P6_CATCH(...)
#endif

namespace p6_ipf {

namespace {

constexpr uint32_t kInfoBytes = 96, kImgeBytes = 80, kDataBytes = 28, kCapsBytes = 12;
constexpr uint32_t kTagCaps = 0x43415053, kTagInfo = 0x494e464f, kTagImge = 0x494d4745, kTagData = 0x44415441;
constexpr uint32_t kKeys = P6_IPF_MAX_KEYS;
constexpr uint32_t kImageTracks = 2u * (P6_IPF_MAX_CYLINDER + 1u);

struct CrcTable {
    uint32_t value[256];
    constexpr CrcTable() : value()
    {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t crc = i;
            for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
            value[i] = crc;
        }
    }
};
constexpr CrcTable kCrc;

// The reflected CRC-32 of zlib, which is what the format uses (crc32r of MAME), a chunk at a time. `state` starts at 0xFFFFFFFF and the result is its complement.
inline uint32_t crc_update(uint32_t state, const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; ++i) state = kCrc.value[(state ^ p[i]) & 0xFF] ^ (state >> 8);
    return state;
}

inline uint32_t be32(const uint8_t *p) { return get_u32be(p); }

}  // namespace

struct Image {
    ReadFn read = nullptr;
    const void *context = nullptr;
    char owned[kContextBytes] = {};                      // the copy of the context of open(..., context_size)
    uint64_t bytes = 0;
    ipf_format::ipf_decode decode;                       // INFO, and the IMGE of every track by its key
    uint64_t data_offset[kKeys] = {};                    // where the data of the track of each key starts
    bool has_data[kKeys] = {};
    int16_t by_track[kImageTracks];                      // includes empty outer padding; only kTracks are accessible to the drive
    const uint8_t *memory = nullptr;                     // open_memory
};

namespace {

bool read_memory(const void *context, uint64_t offset, void *out, size_t length)
{
    const Image *image = static_cast<const Image *>(context);
    if (offset > image->bytes || length > image->bytes - offset) return false;
    memcpy(out, image->memory + offset, length);
    return true;
}

// The CRC of the `size` bytes at `offset`, read in chunks. With `zero_field` the four bytes at +8 count as zero: the CRC of a tag is taken with its own field cleared.
bool crc_of(const Image &image, uint64_t offset, uint64_t size, bool zero_field, uint32_t *out)
{
    uint8_t chunk[512];
    uint32_t state = 0xFFFFFFFFu;
    for (uint64_t done = 0; done < size;) {
        const size_t n = size - done < sizeof chunk ? size_t(size - done) : sizeof chunk;
        if (!image.read(image.context, offset + done, chunk, n)) return false;
        if (zero_field)
            for (uint64_t k = 8; k < 12; ++k)
                if (k >= done && k < done + n) chunk[k - done] = 0;
        state = crc_update(state, chunk, n);
        done += n;
    }
    *out = ~state;
    return true;
}

const char *scan(Image &image)
{
    const uint64_t bytes = image.bytes;
    image.decode.tcount = kKeys;
    image.decode.tinfos.resize(kKeys);
    memset(image.by_track, 0xFF, sizeof image.by_track);
    bool have_info = false;
    uint64_t pos = 0;
    uint32_t first = 0;
    while (pos < bytes) {
        uint8_t head[12];
        if (bytes - pos < 12 || !image.read(image.context, pos, head, 12)) return "an IPF is cut short in a tag header";
        const uint32_t id = be32(head), size = be32(head + 4), crc = be32(head + 8);
        if (size < 12 || size > bytes - pos) return "an IPF tag is smaller than its header or lies beyond the end of the file";
        uint32_t actual;
        if (!crc_of(image, pos, size, true, &actual)) return "an IPF cannot be read";
        if (actual != crc) return "an IPF tag whose checksum is wrong";
        if (pos == 0 && (id != kTagCaps || size != kCapsBytes)) return "the first tag of an IPF is not CAPS";
        first += 1;
        uint8_t body[kInfoBytes > kImgeBytes ? kInfoBytes : kImgeBytes];
        const uint64_t tag_end = pos + size;
        switch (id) {
        case kTagCaps:
            if (size != kCapsBytes || first != 1) return "an IPF has a CAPS tag that is not the first, or of another size";
            break;
        case kTagInfo:
            if (size != kInfoBytes || have_info) return "an IPF has an INFO tag of another size or more than one";
            if (!image.read(image.context, pos, body, size)) return "an IPF cannot be read";
            if (!image.decode.parse_info(body)) return "an IPF whose INFO is not a version the decoder reads";
            have_info = true;
            break;
        case kTagImge: {
            if (size != kImgeBytes || !have_info) return "an IPF has an IMGE tag of another size or before its INFO";
            if (!image.read(image.context, pos, body, size)) return "an IPF cannot be read";
            const uint32_t key = be32(body + 64);
            if (key < kKeys && image.decode.tinfos[key].info_set) return "an IPF describes the same track key twice";
            if (!image.decode.parse_imge(body)) return "an IPF track description the decoder does not read (or outside the cylinders of a drive)";
            break;
        }
        case kTagData: {
            if (size != kDataBytes || !have_info) return "an IPF has a DATA tag of another size or before its INFO";
            if (!image.read(image.context, pos, body, size)) return "an IPF cannot be read";
            const uint32_t key = be32(body + 24), data_size = be32(body + 12);
            if (key >= kKeys || image.has_data[key]) return "an IPF has data for a track key that is out of range or already has data";
            if (data_size > kMaxTrackData || data_size > bytes - tag_end) return "an IPF track data is larger than the core reads or lies beyond the end of the file";
            if (!crc_of(image, tag_end, data_size, false, &actual)) return "an IPF cannot be read";
            if (actual != be32(body + 20)) return "an IPF track data whose checksum is wrong";
            ipf_format::ipf_decode::track_info &t = image.decode.tinfos[key];
            t.data_size = data_size;
            t.data_size_bits = be32(body + 16);
            image.data_offset[key] = tag_end;
            image.has_data[key] = true;
            pos = tag_end + data_size;
            continue;
        }
        default:
            break;                                      // CTEX, CTEI, TRCK...: nothing the decoder needs
        }
        pos = tag_end;
    }
    if (!have_info) return "an IPF has no INFO tag";
    unsigned described = 0;
    for (uint32_t key = 0; key < kKeys; ++key) {
        const ipf_format::ipf_decode::track_info &t = image.decode.tinfos[key];
        if (t.info_set != image.has_data[key]) return "an IPF track has a description and no data, or data and no description";
        if (!t.info_set) continue;
        const uint32_t track = t.cylinder * 2 + t.head;
        if (track >= kImageTracks) return "an IPF track beyond the image geometry";
        if (track >= kTracks && (t.type != 1 || t.size_cells || t.datasize_cells || t.gapsize_cells || t.block_count || t.data_size))
            return "an IPF has a formatted track beyond the cylinders of a drive";
        if (image.by_track[track] >= 0) return "an IPF has two descriptions of the same cylinder and head";
        image.by_track[track] = int16_t(key);
        if (track < kTracks) ++described;
    }
    if (!described) return "an IPF has no tracks";
    return nullptr;
}

inline uint64_t mix64(uint64_t x)
{
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

}  // namespace

namespace {

Image *index_image(Image *image, const char **why)
{
    if (const char *text = scan(*image)) {
        *why = text;
        delete image;
        return nullptr;
    }
    return image;
}

}  // namespace

Image *open(ReadFn read, const void *context, uint64_t bytes, const char **why, size_t context_size)
{
    const char *unused;
    if (!why) why = &unused;
    *why = nullptr;
    if (!read || bytes < kCapsBytes + kInfoBytes) { *why = "an IPF is shorter than its first two tags"; return nullptr; }
    if (bytes > kMaxFileBytes) { *why = "an IPF is larger than the core reads (3 MiB)"; return nullptr; }
    Image *image = nullptr;
    P6_TRY {
        image = new Image;
        image->read = read;
        image->context = context;
        if (context_size && context_size <= kContextBytes && context) {
            memcpy(image->owned, context, context_size);
            image->context = image->owned;
        }
        image->bytes = bytes;
        return index_image(image, why);
    } P6_CATCH(delete image; *why = "an IPF could not be indexed for lack of memory"; return nullptr;)
    return nullptr;
}

Image *open_memory(const uint8_t *data, uint64_t bytes, const char **why)
{
    const char *unused;
    if (!why) why = &unused;
    *why = nullptr;
    if (!data || bytes < kCapsBytes + kInfoBytes) { *why = "an IPF is shorter than its first two tags"; return nullptr; }
    if (bytes > kMaxFileBytes) { *why = "an IPF is larger than the core reads (3 MiB)"; return nullptr; }
    Image *image = nullptr;
    P6_TRY {
        image = new Image;
        image->memory = data;
        image->read = read_memory;
        image->context = image;
        image->bytes = bytes;
        return index_image(image, why);
    } P6_CATCH(delete image; *why = "an IPF could not be indexed for lack of memory"; return nullptr;)
    return nullptr;
}

void close(Image *image) { delete image; }

unsigned track_count(const Image *image)
{
    unsigned count = 0;
    for (unsigned n = 0; image && n < kTracks; ++n)
        if (image->by_track[n] >= 0) count = n + 1;
    return count;
}

bool has_track(const Image *image, unsigned track) { return image && track < kTracks && image->by_track[track] >= 0; }

int decode(const Image *image, unsigned track, unsigned revolution, uint16_t *words, size_t word_capacity, uint16_t *timing, size_t timing_capacity, Track *out)
{
    if (!image || !words || !timing || !out || track >= kTracks) return kFailed;
    const int key = image->by_track[track];
    if (key < 0) return kAbsent;
    P6_TRY {
        ipf_format::ipf_decode::track_info t = image->decode.tinfos[key];
        if (!t.size_cells) return kAbsent;                 // an empty description: the drive sees an unformatted track
        std::vector<uint8_t> data;
        uint32_t cells = 0;
        std::vector<uint32_t> levels;
        int splice = 0;
        if (t.type == 1) {
            // The surface has no cell structure (noise): every cell is weak.
            cells = t.size_cells;
            levels.assign(cells, floppy_image::MG_N | kNominalCellNs);
        } else {
            if (t.data_size > kMaxTrackData) return kFailed;
            data.resize(t.data_size ? t.data_size : 1);
            if (t.data_size && !image->read(image->context, image->data_offset[key], data.data(), t.data_size)) return kFailed;
            t.data = data.data();
            floppy_image result;
            ipf_format::ipf_decode &dec = const_cast<ipf_format::ipf_decode &>(image->decode);   // generate_track reads the INFO and keeps nothing
            if (!dec.generate_track(t, result) || !result.captured) return kFailed;
            levels = std::move(result.levels);
            splice = result.splice;
            cells = uint32_t(levels.size());
        }
        if (!cells || cells > kMaxCells || (cells + 15) / 16 > word_capacity || (cells + 7) / 8 + 2 > timing_capacity) return kFailed;

        memset(words, 0, ((cells + 15) / 16) * sizeof(uint16_t));
        const uint64_t seed = mix64((uint64_t(track) << 32) ^ revolution ^ 0x1BF0E5A5C0DEC0DEull);
        uint64_t weak_bits = 0;
        uint32_t weak_index = 0;
        bool weak = false;
        uint64_t sum = 0;
        uint32_t counted = 0;
        for (uint32_t i = 0; i < cells; ++i) {
            const uint32_t level = levels[i];
            const uint32_t kind = level & floppy_image::MG_MASK;
            bool bit;
            if (kind == floppy_image::MG_N) {
                if ((weak_index & 63) == 0) weak_bits = mix64(seed + (weak_index >> 6));
                bit = (weak_bits >> (weak_index & 63)) & 1;
                ++weak_index;
                weak = true;
            } else {
                bit = kind == (uint32_t(5) << floppy_image::MG_SHIFT);       // MG_1
            }
            if (bit) words[i >> 4] |= uint16_t(0x8000u >> (i & 15));
            sum += level & floppy_image::TIME_MASK;
            if (++counted == 8 || i + 1 == cells) {
                const uint64_t mean = (sum + counted) / (2ull * counted);   // thousandths of a nominal cell, rounded
                timing[i >> 3] = uint16_t(mean == 0 ? 1 : mean > 0xFFFF ? 0xFFFF : mean);
                sum = 0;
                counted = 0;
            }
        }
        timing[(cells + 7) / 8] = timing[0];
        timing[(cells + 7) / 8 + 1] = timing[0];
        out->cells = cells;
        out->splice = splice < 0 ? 0 : uint32_t(splice) % cells;
        out->weak = weak;
        return kDecoded;
    } P6_CATCH(return kFailed;)
    return kFailed;
}

const char *check_tracks(const Image *image, unsigned *track)
{
    static_assert(kWordCapacity >= kMaxCells / 16, "words");
    std::vector<uint16_t> words(kWordCapacity), timing(kTimingCapacity);
    for (unsigned n = 0; n < kTracks; ++n) {
        if (!has_track(image, n)) continue;
        Track out;
        if (decode(image, n, 0, words.data(), words.size(), timing.data(), timing.size(), &out) == kFailed) {
            if (track) *track = n;
            return "an IPF track whose blocks the decoder cannot read";
        }
    }
    return nullptr;
}

}  // namespace p6_ipf
