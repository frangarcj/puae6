// DEFLATE (RFC 1951) and CRC-32, for the disk containers the libretro adapter reads (gzip in an ADZ, zip members; see container.h). Header-only, no
// dependency, no engine: the engine's own zlib is not part of the portable profile and the adapter links nothing but the native ABI.
//
// The decoder is written for hostile input. Nothing is sized from a number the stream claims: the output grows one block of bytes at a time and
// stops with TooLarge the moment it would pass `max_out`, so a decompression bomb costs at most `max_out` bytes of memory and `max_out` symbols of
// time (every symbol of a block makes at least one byte; a block header that makes none is bounded by the input, which the caller already capped).
// Reads past the end of the input are a result, never a memory access. The structure is the one of zlib's contrib/puff.c (canonical Huffman decoding
// from the code-length counts), which is small enough to audit.
#ifndef P6_LIBRETRO_INFLATE_H
#define P6_LIBRETRO_INFLATE_H

#include <stddef.h>
#include <stdint.h>
#include <vector>

namespace p6_libretro {

// CRC-32 of the IEEE polynomial (gzip, zip, PNG), updated over `size` bytes; start from 0.
inline uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t size)
{
    static const struct Table {
        uint32_t entry[256];
        Table()
        {
            for (uint32_t i = 0; i < 256; ++i) {
                uint32_t value = i;
                for (int bit = 0; bit < 8; ++bit) value = (value & 1) ? 0xEDB88320u ^ (value >> 1) : value >> 1;
                entry[i] = value;
            }
        }
    } table;
    crc = ~crc;
    for (size_t i = 0; i < size; ++i) crc = table.entry[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    return ~crc;
}

enum class InflateResult { Ok, Truncated, Corrupt, TooLarge };

namespace inflate_detail {

constexpr int kMaxBits = 15;
constexpr int kLitLenCodes = 288;
constexpr int kDistCodes = 30;

struct Huffman {
    uint16_t count[kMaxBits + 1];           // codes of each length
    uint16_t symbol[kLitLenCodes];          // symbols ordered by code
};

// Builds the canonical code of `n` lengths. False for an over-subscribed set, and for an incomplete one unless it has the single code zlib allows.
inline bool build(Huffman *h, const uint8_t *length, int n)
{
    for (int len = 0; len <= kMaxBits; ++len) h->count[len] = 0;
    for (int i = 0; i < n; ++i) ++h->count[length[i]];
    if (h->count[0] == n) return true;                   // no code at all: legal for the distance tree when the block only has literals
    int left = 1;
    for (int len = 1; len <= kMaxBits; ++len) {
        left <<= 1;
        left -= h->count[len];
        if (left < 0) return false;
    }
    if (left > 0 && !(n - h->count[0] == 1 && h->count[1] == 1)) return false;
    uint16_t offset[kMaxBits + 1];
    offset[1] = 0;
    for (int len = 1; len < kMaxBits; ++len) offset[len + 1] = uint16_t(offset[len] + h->count[len]);
    for (int i = 0; i < n; ++i)
        if (length[i]) h->symbol[offset[length[i]]++] = uint16_t(i);
    return true;
}

class Stream {
public:
    Stream(const uint8_t *in, size_t size, size_t max_out, std::vector<uint8_t> *out) : in_(in), size_(size), max_out_(max_out), out_(out) {}

    InflateResult run(size_t *consumed)
    {
        InflateResult result = InflateResult::Ok;
        for (bool last = false; !last && result == InflateResult::Ok;) {
            const int header = bits(3);
            if (header < 0) { result = InflateResult::Truncated; break; }
            last = header & 1;
            switch (header >> 1) {
            case 0: result = stored(); break;
            case 1: result = fixed(); break;
            case 2: result = dynamic(); break;
            default: result = InflateResult::Corrupt; break;
            }
        }
        *consumed = pos_;                                // a partly read byte counts as read: the stream ends on a byte boundary
        return result;
    }

private:
    int bits(int need)
    {
        uint32_t value = buffer_;
        while (count_ < need) {
            if (pos_ >= size_) return -1;
            value |= uint32_t(in_[pos_++]) << count_;
            count_ += 8;
        }
        buffer_ = value >> need;
        count_ -= need;
        return int(value & ((1u << need) - 1));
    }

    InflateResult stored()
    {
        buffer_ = 0;                                     // drop the rest of the byte
        count_ = 0;
        if (size_ - pos_ < 4) return InflateResult::Truncated;
        const unsigned len = in_[pos_] | (in_[pos_ + 1] << 8);
        const unsigned inverse = in_[pos_ + 2] | (in_[pos_ + 3] << 8);
        pos_ += 4;
        if (len != (~inverse & 0xFFFF)) return InflateResult::Corrupt;
        if (size_ - pos_ < len) return InflateResult::Truncated;
        if (out_->size() + len > max_out_) return InflateResult::TooLarge;
        out_->insert(out_->end(), in_ + pos_, in_ + pos_ + len);
        pos_ += len;
        return InflateResult::Ok;
    }

    int decode(const Huffman &h)
    {
        int code = 0, first = 0, index = 0;
        for (int len = 1; len <= kMaxBits; ++len) {
            const int bit = bits(1);
            if (bit < 0) return -1;
            code |= bit;
            const int count = h.count[len];
            if (code - count < first) return h.symbol[index + (code - first)];
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        return -2;                                       // ran out of codes: the tree is not complete and this is a code it lacks
    }

    InflateResult codes(const Huffman &lit, const Huffman &dist)
    {
        static const uint16_t kLengthBase[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
        static const uint8_t kLengthExtra[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
        static const uint16_t kDistBase[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
                                               8193, 12289, 16385, 24577};
        static const uint8_t kDistExtra[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
        for (;;) {
            int symbol = decode(lit);
            if (symbol == -1) return InflateResult::Truncated;
            if (symbol < 0) return InflateResult::Corrupt;
            if (symbol < 256) {
                if (out_->size() >= max_out_) return InflateResult::TooLarge;
                out_->push_back(uint8_t(symbol));
            } else if (symbol == 256) {
                return InflateResult::Ok;
            } else {
                symbol -= 257;
                if (symbol >= 29) return InflateResult::Corrupt;
                const int extra = bits(kLengthExtra[symbol]);
                if (extra < 0) return InflateResult::Truncated;
                const size_t length = size_t(kLengthBase[symbol]) + size_t(extra);
                symbol = decode(dist);
                if (symbol == -1) return InflateResult::Truncated;
                if (symbol < 0 || symbol >= 30) return InflateResult::Corrupt;
                const int more = bits(kDistExtra[symbol]);
                if (more < 0) return InflateResult::Truncated;
                const size_t distance = size_t(kDistBase[symbol]) + size_t(more);
                if (distance > out_->size()) return InflateResult::Corrupt;
                if (out_->size() + length > max_out_) return InflateResult::TooLarge;
                for (size_t i = 0; i < length; ++i) out_->push_back((*out_)[out_->size() - distance]);
            }
        }
    }

    InflateResult fixed()
    {
        static const struct Fixed {
            Huffman lit, dist;
            Fixed()
            {
                uint8_t lengths[kLitLenCodes];
                int i = 0;
                for (; i < 144; ++i) lengths[i] = 8;
                for (; i < 256; ++i) lengths[i] = 9;
                for (; i < 280; ++i) lengths[i] = 7;
                for (; i < kLitLenCodes; ++i) lengths[i] = 8;
                build(&lit, lengths, kLitLenCodes);
                uint8_t distances[32];                   // thirty codes are used; 30 and 31 complete the tree and are refused when decoded
                for (int d = 0; d < 32; ++d) distances[d] = 5;
                build(&dist, distances, 32);
            }
        } fixed;
        return codes(fixed.lit, fixed.dist);
    }

    InflateResult dynamic()
    {
        static const uint8_t kOrder[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
        const int nlen = bits(5), ndist = bits(5), ncode = bits(4);
        if (nlen < 0 || ndist < 0 || ncode < 0) return InflateResult::Truncated;
        const int lit_codes = nlen + 257, dist_codes = ndist + 1;
        if (lit_codes > 286 || dist_codes > kDistCodes) return InflateResult::Corrupt;
        uint8_t lengths[kLitLenCodes + kDistCodes] = {};
        for (int i = 0; i < ncode + 4; ++i) {
            const int v = bits(3);
            if (v < 0) return InflateResult::Truncated;
            lengths[kOrder[i]] = uint8_t(v);
        }
        Huffman lencode;
        if (!build(&lencode, lengths, 19)) return InflateResult::Corrupt;
        int index = 0;
        uint8_t all[kLitLenCodes + kDistCodes] = {};
        while (index < lit_codes + dist_codes) {
            int symbol = decode(lencode);
            if (symbol == -1) return InflateResult::Truncated;
            if (symbol < 0) return InflateResult::Corrupt;
            if (symbol < 16) {
                all[index++] = uint8_t(symbol);
                continue;
            }
            int repeat, value = 0;
            if (symbol == 16) {
                if (index == 0) return InflateResult::Corrupt;
                value = all[index - 1];
                repeat = bits(2);
                if (repeat < 0) return InflateResult::Truncated;
                repeat += 3;
            } else if (symbol == 17) {
                repeat = bits(3);
                if (repeat < 0) return InflateResult::Truncated;
                repeat += 3;
            } else {
                repeat = bits(7);
                if (repeat < 0) return InflateResult::Truncated;
                repeat += 11;
            }
            if (index + repeat > lit_codes + dist_codes) return InflateResult::Corrupt;
            while (repeat--) all[index++] = uint8_t(value);
        }
        if (all[256] == 0) return InflateResult::Corrupt;       // no end-of-block code
        Huffman lit, dist;
        if (!build(&lit, all, lit_codes)) return InflateResult::Corrupt;
        if (!build(&dist, all + lit_codes, dist_codes)) return InflateResult::Corrupt;
        return codes(lit, dist);
    }

    const uint8_t *in_;
    size_t size_;
    size_t pos_ = 0;
    uint32_t buffer_ = 0;
    int count_ = 0;
    size_t max_out_;
    std::vector<uint8_t> *out_;
};

}  // namespace inflate_detail

// Inflates the raw DEFLATE stream at `in` into `out` (appended; the caller clears it). Never produces more than `max_out` bytes in total and never reads
// outside [in, in + size). `consumed` is the number of input bytes the stream occupied, which for gzip and zip is where the trailer starts.
inline InflateResult inflate_raw(const uint8_t *in, size_t size, size_t max_out, std::vector<uint8_t> *out, size_t *consumed)
{
    return inflate_detail::Stream(in, size, max_out, out).run(consumed);
}

}  // namespace p6_libretro

#endif
