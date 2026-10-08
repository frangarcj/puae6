// DMS (Disk Masher System) archives of one Amiga disk, decoded in memory to the 901120-byte ADF they hold (T17d). Header-only, no engine.
//
// The compression modes (RLE, Quick, Medium, Deep and the two Heavy ones) are those of xDMS v1.3 by Andre Rodrigues de la Rocha (public domain), the
// decoder WinUAE ships in archivers/dms; this is a bounds-checked rewrite for a hostile file, not a different format:
//   - every read past the end of a track's packed data returns zero bits and is accounted for, so a short stream is refused instead of read over;
//   - a track is limited to the 32000 bytes xDMS allows, which also bounds every loop (each loop makes at least one output byte);
//   - the tables xDMS indexes with a count taken from the stream (Heavy's code lengths) are checked against their size;
//   - the whole archive is checked before it is believed: header CRC, track header CRC, packed data CRC, and the checksum of the unpacked track.
// tests/media/dms_differential.cpp feeds the same random streams to this code and to the vendored xDMS and compares byte for byte.
// What is refused, with a message: encrypted archives (the password is a key search over the checksum, not a feature to emulate), high-density
// archives, FMS archives, and an archive that does not hold every track of an 80-cylinder disk unless it is marked "no zero" (the format's way of
// leaving out tracks that are all zeros). The banner, the FILE_ID.DIZ and the fake boot block that advertising DMS files carry are skipped.
#ifndef P6_LIBRETRO_DMS_H
#define P6_LIBRETRO_DMS_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <memory>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kDmsTrackBytes = 11264;                       // 2 sides x 11 sectors x 512 bytes: what a track of a double-density disk unpacks to
constexpr unsigned kDmsTracks = 80;
constexpr size_t kDmsDiskBytes = kDmsTracks * kDmsTrackBytes;  // 901120

namespace dms_detail {

constexpr size_t kHeaderBytes = 56;
constexpr size_t kTrackHeaderBytes = 20;
constexpr size_t kMaxTrackBytes = 32000;           // the largest packed, intermediate or unpacked track xDMS accepts
constexpr size_t kSlack = 1024;                    // a match may run past the end of its output by at most one match length (256), as in xDMS

inline uint16_t crc16(const uint8_t *data, size_t size)
{
    static const struct Table {
        uint16_t entry[256];
        Table()
        {
            for (unsigned i = 0; i < 256; ++i) {
                unsigned value = i;
                for (int bit = 0; bit < 8; ++bit) value = (value & 1) ? 0xA001u ^ (value >> 1) : value >> 1;
                entry[i] = uint16_t(value);
            }
        }
    } table;
    unsigned crc = 0;
    for (size_t i = 0; i < size; ++i) crc = table.entry[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    return uint16_t(crc);
}

inline uint16_t checksum16(const uint8_t *data, size_t size)
{
    unsigned sum = 0;
    for (size_t i = 0; i < size; ++i) sum += data[i];
    return uint16_t(sum);
}

// The d_code / d_len tables of Medium and Deep (the ones of LZHUF): the code of a byte whose top bits are the index, and how many bits follow it.
struct Distance {
    uint8_t code[256], length[256];
    Distance()
    {
        static const struct { unsigned codes, repeat, bits; } kGroups[] = {{1, 32, 3}, {3, 16, 4}, {8, 8, 5}, {12, 4, 6}, {24, 2, 7}, {16, 1, 8}};
        unsigned at = 0, code = 0;
        for (const auto &g : kGroups)
            for (unsigned c = 0; c < g.codes; ++c, ++code)
                for (unsigned r = 0; r < g.repeat; ++r, ++at) {
                    this->code[at] = uint8_t(code);
                    length[at] = uint8_t(g.bits);
                }
    }
};

// The bit reader of xDMS: a buffer of at least 16 bits is kept full, bytes past the end are zero, and `pos` counts them as read so that the end check
// of the unpackers sees how far the stream went.
struct Bits {
    const uint8_t *data = nullptr;
    size_t size = 0, pos = 0;
    uint32_t buffer = 0;
    unsigned count = 0;
    bool bad = false;                                  // dropped more bits than the buffer holds: the stream is corrupt

    void init(const uint8_t *in, size_t bytes)
    {
        data = in; size = bytes; pos = 0; buffer = 0; count = 0; bad = false;
        drop(0);
    }
    unsigned get(unsigned n) const { return uint16_t(buffer >> (count - n)); }          // n <= 16
    void drop(int n)
    {
        if (n < 0 || unsigned(n) > count) { bad = true; n = 0; }
        count -= unsigned(n);
        buffer &= (1u << count) - 1;
        while (count < 16) {
            buffer = (buffer << 8) | (pos < size ? data[pos] : 0u);
            ++pos;
            count += 8;
        }
    }
    // xDMS accepts a stream that ends within two bytes of the length the track header gives.
    bool ended_well() const { return !bad && pos >= size && pos <= size + 2; }
};

class Decoder {
public:
    Decoder() : state_(new State()) { reset(); }

    // The state the unpackers keep from one track to the next (the dictionaries), back to what a new archive starts with.
    void reset()
    {
        state_->quick_loc = 251;
        state_->medium_loc = 0x3fbe;
        state_->heavy_loc = 0;
        state_->deep_loc = 0x3fc4;
        state_->deep_tables = false;
        memset(state_->text, 0, 0x3fc8);
        state_->last_length = 0;
        state_->np = 0;
    }

    // Bytes of the last stream that the bit reader has loaded, prefetch included (the unpackers accept a stream that ends within two bytes of it).
    size_t stream_end() const { return bits_.pos; }

    // Each returns true when the stream decoded to `size` bytes and ended where its track header said. `out` has room for size + kSlack bytes.
    bool rle(const uint8_t *in, size_t in_size, uint8_t *out, size_t size) const
    {
        size_t i = 0, o = 0;
        while (o < size) {
            if (i >= in_size) return false;
            const uint8_t a = in[i++];
            if (a != 0x90) { out[o++] = a; continue; }
            if (i >= in_size) return false;
            const uint8_t b = in[i++];
            if (!b) { out[o++] = a; continue; }
            if (i >= in_size) return false;
            const uint8_t value = in[i++];
            size_t n = b;
            if (b == 0xFF) {
                if (in_size - i < 2) return false;
                n = size_t(in[i] << 8) + in[i + 1];
                i += 2;
            }
            if (o + n > size) return false;
            memset(out + o, value, n);
            o += n;
        }
        return true;
    }

    bool quick(const uint8_t *in, size_t in_size, uint8_t *out, size_t size)
    {
        State &s = *state_;
        bits_.init(in, in_size);
        for (size_t o = 0; o < size;) {
            if (bits_.get(1)) {
                bits_.drop(1);
                out[o++] = s.text[s.quick_loc++ & 0xFF] = uint8_t(bits_.get(8));
                bits_.drop(8);
            } else {
                bits_.drop(1);
                unsigned j = bits_.get(2) + 2;
                bits_.drop(2);
                uint16_t i = uint16_t(s.quick_loc - bits_.get(8) - 1);
                bits_.drop(8);
                while (j--) out[o++] = s.text[s.quick_loc++ & 0xFF] = s.text[i++ & 0xFF];
            }
        }
        s.quick_loc = uint16_t((s.quick_loc + 5) & 0xFF);
        return bits_.ended_well();
    }

    bool medium(const uint8_t *in, size_t in_size, uint8_t *out, size_t size)
    {
        State &s = *state_;
        bits_.init(in, in_size);
        for (size_t o = 0; o < size;) {
            if (bits_.get(1)) {
                bits_.drop(1);
                out[o++] = s.text[s.medium_loc++ & 0x3FFF] = uint8_t(bits_.get(8));
                bits_.drop(8);
            } else {
                bits_.drop(1);
                unsigned c = bits_.get(8);
                bits_.drop(8);
                unsigned j = distance_.code[c] + 3;
                unsigned u = distance_.length[c];
                c = ((c << u) | bits_.get(u)) & 0xFF;
                bits_.drop(int(u));
                u = distance_.length[c];
                c = (unsigned(distance_.code[c]) << 8) | (((c << u) | bits_.get(u)) & 0xFF);
                bits_.drop(int(u));
                uint16_t i = uint16_t(s.medium_loc - c - 1);
                while (j--) out[o++] = s.text[s.medium_loc++ & 0x3FFF] = s.text[i++ & 0x3FFF];
            }
        }
        s.medium_loc = uint16_t((s.medium_loc + 66) & 0x3FFF);
        return bits_.ended_well();
    }

    bool deep(const uint8_t *in, size_t in_size, uint8_t *out, size_t size)
    {
        State &s = *state_;
        bits_.init(in, in_size);
        if (!s.deep_tables) init_deep();
        for (size_t o = 0; o < size;) {
            const unsigned c = deep_char();
            if (c < 256) {
                out[o++] = s.text[s.deep_loc++ & 0x3FFF] = uint8_t(c);
            } else {
                unsigned j = c - 255 + kThreshold;
                uint16_t i = uint16_t(s.deep_loc - deep_position() - 1);
                while (j--) out[o++] = s.text[s.deep_loc++ & 0x3FFF] = s.text[i++ & 0x3FFF];
            }
        }
        s.deep_loc = uint16_t((s.deep_loc + 60) & 0x3FFF);
        return bits_.ended_well();
    }

    // `flags` is the track's flag byte masked as xDMS does: bit 3 (8) selects Heavy 2 (8 KB dictionary), bit 1 (2) says the tables follow.
    bool heavy(const uint8_t *in, size_t in_size, uint8_t *out, size_t size, unsigned flags)
    {
        State &s = *state_;
        unsigned mask;
        if (flags & 8) { s.np = 15; mask = 0x1FFF; }
        else { s.np = 14; mask = 0x0FFF; }
        bits_.init(in, in_size);
        if (flags & 2) {
            if (!read_tree_c() || !read_tree_p()) return false;
        }
        for (size_t o = 0; o < size;) {
            const unsigned c = decode_c();
            if (c < 256) {
                out[o++] = s.text[s.heavy_loc++ & mask] = uint8_t(c);
            } else {
                unsigned j = c - kHeavyOffset;
                uint16_t i = uint16_t(s.heavy_loc - decode_p() - 1);
                while (j--) out[o++] = s.text[s.heavy_loc++ & mask] = s.text[i++ & mask];
            }
            if (bits_.bad) return false;
        }
        return bits_.ended_well();
    }

private:
    static constexpr unsigned kThreshold = 2;
    static constexpr unsigned kLookahead = 60;
    static constexpr unsigned kChars = 256 - kThreshold + kLookahead;      // 314
    static constexpr unsigned kTable = kChars * 2 - 1;                     // 627
    static constexpr unsigned kRoot = kTable - 1;
    static constexpr unsigned kMaxFreq = 0x8000;
    static constexpr unsigned kHeavyChars = 510;
    static constexpr unsigned kHeavyDistances = 20;
    static constexpr unsigned kHeavyOffset = 253;

    struct State {
        uint8_t text[0x4000];                          // the dictionaries; xDMS clears the first 0x3fc8 bytes between tracks and so does reset()
        uint16_t quick_loc, medium_loc, heavy_loc, deep_loc;
        bool deep_tables;
        uint16_t last_length, np;
        uint16_t freq[kTable + 1], parent[kTable + kChars], son[kTable];       // Deep's adaptive Huffman tree
        uint16_t left[2 * kHeavyChars - 1], right[2 * kHeavyChars - 1 + 9];    // Heavy's trees above the lookup tables
        uint8_t c_len[kHeavyChars], p_len[kHeavyDistances];
        uint16_t c_table[4096], p_table[256];
    };

    // --- Deep: LZSS with the adaptive Huffman coding of LZHUF -------------------------------------------------------------------------------

    void init_deep()
    {
        State &s = *state_;
        unsigned i, j;
        for (i = 0; i < kChars; ++i) {
            s.freq[i] = 1;
            s.son[i] = uint16_t(i + kTable);
            s.parent[i + kTable] = uint16_t(i);
        }
        for (i = 0, j = kChars; j <= kRoot; i += 2, ++j) {
            s.freq[j] = uint16_t(s.freq[i] + s.freq[i + 1]);
            s.son[j] = uint16_t(i);
            s.parent[i] = s.parent[i + 1] = uint16_t(j);
        }
        s.freq[kTable] = 0xFFFF;
        s.parent[kRoot] = 0;
        s.deep_tables = true;
    }

    unsigned deep_char()
    {
        State &s = *state_;
        unsigned c = s.son[kRoot];
        while (c < kTable) {
            c = s.son[c + bits_.get(1)];
            bits_.drop(1);
        }
        c -= kTable;
        deep_update(c);
        return c;
    }

    unsigned deep_position()
    {
        unsigned i = bits_.get(8);
        bits_.drop(8);
        const unsigned c = unsigned(distance_.code[i]) << 8;
        const unsigned j = distance_.length[i];
        i = ((i << j) | bits_.get(j)) & 0xFF;
        bits_.drop(int(j));
        return c | i;
    }

    void deep_reconstruct()
    {
        State &s = *state_;
        unsigned i, j, k, f, l;
        j = 0;
        for (i = 0; i < kTable; ++i)
            if (s.son[i] >= kTable) {
                s.freq[j] = uint16_t((s.freq[i] + 1) / 2);
                s.son[j] = s.son[i];
                ++j;
            }
        for (i = 0, j = kChars; j < kTable; i += 2, ++j) {
            k = i + 1;
            f = s.freq[j] = uint16_t(s.freq[i] + s.freq[k]);
            for (k = j - 1; f < s.freq[k]; --k) {}
            ++k;
            l = (j - k) * 2;
            memmove(&s.freq[k + 1], &s.freq[k], l);
            s.freq[k] = uint16_t(f);
            memmove(&s.son[k + 1], &s.son[k], l);
            s.son[k] = uint16_t(i);
        }
        for (i = 0; i < kTable; ++i) {
            k = s.son[i];
            if (k >= kTable) s.parent[k] = uint16_t(i);
            else s.parent[k] = s.parent[k + 1] = uint16_t(i);
        }
    }

    void deep_update(unsigned c)
    {
        State &s = *state_;
        if (s.freq[kRoot] == kMaxFreq) deep_reconstruct();
        c = s.parent[c + kTable];
        do {
            unsigned k = ++s.freq[c];
            unsigned l = c + 1;
            if (k > s.freq[l]) {
                while (k > s.freq[++l]) {}
                --l;
                s.freq[c] = s.freq[l];
                s.freq[l] = uint16_t(k);
                const unsigned i = s.son[c];
                s.parent[i] = uint16_t(l);
                if (i < kTable) s.parent[i + 1] = uint16_t(l);
                const unsigned j = s.son[l];
                s.son[l] = uint16_t(i);
                s.parent[j] = uint16_t(c);
                if (j < kTable) s.parent[j + 1] = uint16_t(c);
                s.son[c] = uint16_t(j);
                c = l;
            }
        } while ((c = s.parent[c]) != 0);
    }

    // --- Heavy: LZSS with static Huffman trees sent in front of the data (LHA's) ----------------------------------------------------------------

    // The lookup table of LHA: codes up to `table_bits` long resolve in one step, longer ones continue in the left/right trees. Returns 0 or an
    // error number, as xDMS's make_table does, and writes no tree node past 2 * n - 1.
    struct TableBuilder {
        State &s;
        const uint8_t *length;
        uint16_t *table;
        unsigned n, table_size, avail, depth, max_depth, len, bit, codeword;
        int c;
        int error;

        unsigned node()
        {
            unsigned i = 0;
            if (error) return 0;
            if (len == depth) {
                while (++c < int(n))
                    if (length[c] == len) {
                        i = codeword;
                        codeword += bit;
                        if (codeword > table_size) { error = 1; return 0; }
                        while (i < codeword) table[i++] = uint16_t(c);
                        return unsigned(c);
                    }
                c = -1;
                ++len;
                bit >>= 1;
            }
            ++depth;
            if (depth < max_depth) {
                node();
                node();
            } else if (depth > 32) {
                error = 2;
                return 0;
            } else {
                if ((i = avail++) >= 2 * n - 1) { error = 3; return 0; }
                s.left[i] = uint16_t(node());
                s.right[i] = uint16_t(node());
                if (codeword >= table_size) { error = 4; return 0; }
                if (depth == max_depth) table[codeword++] = uint16_t(i);
            }
            --depth;
            return i;
        }
    };

    int make_table(unsigned n, const uint8_t *length, unsigned table_bits, uint16_t *table)
    {
        TableBuilder b{*state_, length, table, n, 1u << table_bits, n, 1, table_bits + 1, 1, (1u << table_bits) / 2, 0, -1, 0};
        b.node();
        if (b.error) return b.error;
        b.node();
        if (b.error) return b.error;
        return b.codeword != b.table_size ? 5 : 0;
    }

    bool read_tree_c()
    {
        State &s = *state_;
        unsigned n = bits_.get(9);
        bits_.drop(9);
        if (n > kHeavyChars) return false;
        if (n > 0) {
            unsigned i;
            for (i = 0; i < n; ++i) {
                s.c_len[i] = uint8_t(bits_.get(5));
                bits_.drop(5);
            }
            for (; i < kHeavyChars; ++i) s.c_len[i] = 0;
            return make_table(kHeavyChars, s.c_len, 12, s.c_table) == 0;
        }
        n = bits_.get(9);
        bits_.drop(9);
        memset(s.c_len, 0, sizeof s.c_len);
        for (unsigned i = 0; i < 4096; ++i) s.c_table[i] = uint16_t(n);
        return true;
    }

    bool read_tree_p()
    {
        State &s = *state_;
        unsigned n = bits_.get(5);
        bits_.drop(5);
        if (n > kHeavyDistances) return false;
        if (n > 0) {
            unsigned i;
            for (i = 0; i < n; ++i) {
                s.p_len[i] = uint8_t(bits_.get(4));
                bits_.drop(4);
            }
            for (; i < s.np; ++i) s.p_len[i] = 0;
            return make_table(s.np, s.p_len, 8, s.p_table) == 0;
        }
        n = bits_.get(5);
        bits_.drop(5);
        memset(s.p_len, 0, sizeof s.p_len);
        for (unsigned i = 0; i < 256; ++i) s.p_table[i] = uint16_t(n);
        return true;
    }

    unsigned decode_c()
    {
        State &s = *state_;
        unsigned j = s.c_table[bits_.get(12)];
        if (j < kHeavyChars) {
            bits_.drop(s.c_len[j]);
        } else {
            bits_.drop(12);
            const unsigned i = bits_.get(16);
            unsigned m = 0x8000;
            for (int step = 0; j >= kHeavyChars; ++step, m >>= 1) {
                if (step == 32) { bits_.bad = true; return 0; }       // a tree is never this deep
                j = (i & m) ? s.right[j] : s.left[j];
            }
            bits_.drop(int(s.c_len[j]) - 12);
        }
        return j;
    }

    unsigned decode_p()
    {
        State &s = *state_;
        unsigned j = s.p_table[bits_.get(8)];
        if (j < s.np) {
            bits_.drop(s.p_len[j]);
        } else {
            bits_.drop(8);
            const unsigned i = bits_.get(16);
            unsigned m = 0x8000;
            for (int step = 0; j >= s.np; ++step, m >>= 1) {
                if (step == 32) { bits_.bad = true; return 0; }
                j = (i & m) ? s.right[j] : s.left[j];
            }
            bits_.drop(int(s.p_len[j]) - 8);
        }
        if (j != unsigned(s.np) - 1) {
            if (j > 0) {
                const unsigned n = j - 1;
                j = bits_.get(n) | (1u << n);
                bits_.drop(int(n));
            }
            s.last_length = uint16_t(j);
        }
        return s.last_length;
    }

    std::unique_ptr<State> state_;
    Bits bits_;
    Distance distance_;
};

inline uint16_t be16(const uint8_t *p) { return uint16_t((p[0] << 8) | p[1]); }

}  // namespace dms_detail

// True when `in` starts like a DMS archive.
inline bool looks_like_dms(const uint8_t *in, size_t size) { return size >= 4 && !memcmp(in, "DMS!", 4); }

// Decodes the archive to the ADF it holds. On failure returns false with the reason in `why` and leaves `adf` unspecified.
inline bool decode_dms(const uint8_t *in, size_t size, std::vector<uint8_t> *adf, std::string *why)
{
    using namespace dms_detail;
    auto fail = [&](const std::string &text) { *why = text; return false; };
    if (size < kHeaderBytes) return fail("the DMS file is truncated (no header)");
    if (!looks_like_dms(in, size)) return fail("not a DMS archive");
    if (be16(in + kHeaderBytes - 2) != crc16(in + 4, kHeaderBytes - 6)) return fail("the DMS header is corrupt (CRC mismatch)");
    const unsigned info = be16(in + 10);
    const unsigned disk_type = be16(in + 50);
    if (info & 2) return fail("the DMS archive is encrypted; the core does not decrypt");
    if (info & 16) return fail("the DMS archive is a high-density disk, not an 880 KB ADF");
    if (disk_type == 7) return fail("the DMS file is an FMS archive of files, not a disk");

    adf->assign(kDmsDiskBytes, 0);
    std::vector<uint8_t> first(kMaxTrackBytes + kSlack, 0), second(kMaxTrackBytes + kSlack, 0);      // the two buffers xDMS passes data through
    Decoder decoder;
    bool present[kDmsTracks] = {};
    unsigned found = 0;
    size_t pos = kHeaderBytes;
    while (pos < size) {
        if (size - pos < kTrackHeaderBytes || memcmp(in + pos, "TR", 2) != 0) {
            // xDMS stops at the first thing that is not a track (archives of the time carry text or zeros after the data) when the disk is whole.
            if (found == kDmsTracks) break;
            if (size - pos < kTrackHeaderBytes) return fail("the DMS file is truncated (a track header is cut)");
            if (found) break;
            return fail("the DMS file has no tracks");
        }
        const uint8_t *header = in + pos;
        if (be16(header + 18) != crc16(header, kTrackHeaderBytes - 2)) {
            if (found == kDmsTracks) break;
            return fail("a DMS track header is corrupt (CRC mismatch)");
        }
        const unsigned number = be16(header + 2);
        const size_t packed = be16(header + 6), middle = be16(header + 8), unpacked = be16(header + 10);
        const unsigned flags = header[12], mode = header[13];
        const unsigned checksum = be16(header + 14), data_crc = be16(header + 16);
        if (packed > kMaxTrackBytes || middle > kMaxTrackBytes || unpacked > kMaxTrackBytes) return fail("a DMS track claims more data than a track can hold");
        if (size - pos - kTrackHeaderBytes < packed) return fail("the DMS file is truncated (a track is cut)");
        pos += kTrackHeaderBytes;
        const uint8_t *data = in + pos;
        pos += packed;
        if (crc16(data, packed) != data_crc) return fail("a DMS track is corrupt (CRC mismatch on track " + std::to_string(number) + ")");
        // Track 80 is FILE_ID.DIZ, 0xFFFF the banner, and a track 0 of 1024 bytes the fake boot block: none of them is disk data.
        if (number >= kDmsTracks || unpacked <= 2048) {
            decoder.reset();
            continue;
        }
        const std::string which = "DMS track " + std::to_string(number);
        if (unpacked != kDmsTrackBytes) return fail(which + " does not unpack to " + std::to_string(kDmsTrackBytes) + " bytes");
        // Unpack as xDMS does, ping-ponging between the two buffers; the result is in `second`.
        memcpy(first.data(), data, packed);
        bool ok = true;
        switch (mode) {
        case 0:
            ok = packed >= unpacked;
            if (ok) memcpy(second.data(), first.data(), unpacked);
            break;
        case 1:
            ok = decoder.rle(first.data(), packed, second.data(), unpacked);
            break;
        case 2:
        case 3:
        case 4:
            ok = mode == 2 ? decoder.quick(first.data(), packed, second.data(), middle)
               : mode == 3 ? decoder.medium(first.data(), packed, second.data(), middle)
                           : decoder.deep(first.data(), packed, second.data(), middle);
            if (ok) ok = decoder.rle(second.data(), middle, first.data(), unpacked);
            if (ok) memcpy(second.data(), first.data(), unpacked);
            break;
        case 5:
        case 6:
            ok = decoder.heavy(first.data(), packed, second.data(), middle, mode == 5 ? flags & 7 : flags | 8);
            if (ok && (flags & 4)) {
                ok = decoder.rle(second.data(), middle, first.data(), unpacked);
                if (ok) memcpy(second.data(), first.data(), unpacked);
            } else if (ok) {
                ok = middle >= unpacked;
            }
            break;
        default:
            return fail(which + " uses an unknown compression mode (" + std::to_string(mode) + ")");
        }
        if (!ok) return fail(which + " does not decompress (corrupt data)");
        if (!(flags & 1)) decoder.reset();
        if (checksum16(second.data(), unpacked) != checksum) return fail(which + " is corrupt (checksum mismatch)");
        memcpy(adf->data() + size_t(number) * kDmsTrackBytes, second.data(), unpacked);
        if (!present[number]) { present[number] = true; ++found; }
    }
    if (!found) return fail("the DMS file holds no disk tracks");
    if (found != kDmsTracks && !(info & 1)) return fail("the DMS archive holds " + std::to_string(found) + " of 80 tracks (a split or partial archive)");
    return true;
}

}  // namespace p6_libretro

#endif
