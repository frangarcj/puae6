// A bounded LHA reader (T64a): header levels 0, 1 and 2, methods -lh0-, -lh5-, -lh6-, -lh7- and -lhd-, decoded in memory. It is a C++ port of tools/whdload/lha.py, the reader of the offline
// WHDLoad preparation (T30a), which is its oracle (tests/hdf/test_volume.py decodes the same archives with both and compares names and bytes).
//
// The archive is NOT trusted. Every size is checked against a limit before anything is allocated, a name is refused unless it is a plain relative path, the CRC-16 of every file is
// verified and nothing of the archive is ever run. The decoders follow the public description of the LH5 family (static Huffman over a sliding window, each block with its own trees);
// the window is 8 KiB (lh5), 32 KiB (lh6) or 64 KiB (lh7). Reading past the end of the data yields zero bits and is counted, so that a truncated stream fails instead of looping.
// Methods -lh1- to -lh4-, -lzs- and -lz4- (and LZX, a different format) are refused with a message.
#ifndef P6_LIBRETRO_LHA_H
#define P6_LIBRETRO_LHA_H

#include <algorithm>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <string>
#include <vector>

namespace p6_libretro {

struct LhaLimits {
    size_t max_entries = 4096;
    size_t max_file = 64u * 1024 * 1024;        // the bytes of one member
    size_t max_total = 128u * 1024 * 1024;      // the bytes of all of them
};

struct LhaEntry {
    std::string name;                           // relative path with '/' separators (the bytes of the header, ISO 8859-1)
    bool is_dir = false;
    std::vector<uint8_t> data;
};

namespace lha_detail {

constexpr size_t kMaxNamePart = 107;
constexpr unsigned kThreshold = 3, kNC = 510, kNT = 19, kTBit = 5, kCBit = 9;

inline uint16_t le16(const uint8_t *p) { return uint16_t(p[0] | (p[1] << 8)); }
inline uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }

inline uint16_t crc16(const uint8_t *data, size_t size)
{
    static uint16_t table[256];
    static bool ready = false;
    if (!ready) {
        for (unsigned n = 0; n < 256; ++n) {
            unsigned c = n;
            for (int k = 0; k < 8; ++k) c = c & 1 ? (c >> 1) ^ 0xA001u : c >> 1;
            table[n] = uint16_t(c);
        }
        ready = true;
    }
    unsigned crc = 0;
    for (size_t i = 0; i < size; ++i) crc = (crc >> 8) ^ table[(crc ^ data[i]) & 0xFF];
    return uint16_t(crc);
}

struct Error {
    std::string message;
};

// MSB-first bit reader; past the end it yields zero bits and counts the bytes it invented.
class Bits {
public:
    Bits(const uint8_t *data, size_t size) : data_(data), size_(size) {}
    uint32_t peek(unsigned n)
    {
        while (count_ < n) {
            if (pos_ < size_) buf_ = ((buf_ << 8) | data_[pos_++]) & 0xFFFFFFFFFFFFull;
            else { buf_ = (buf_ << 8) & 0xFFFFFFFFFFFFull; ++over_; }
            count_ += 8;
        }
        return uint32_t((buf_ >> (count_ - n)) & ((1ull << n) - 1));
    }
    void skip(unsigned n) { peek(n); count_ -= n; }
    uint32_t bits(unsigned n)
    {
        if (n == 0) return 0;
        const uint32_t value = peek(n);
        count_ -= n;
        return value;
    }
    unsigned over() const { return over_; }
    void drop(unsigned n) { count_ -= n; }          // after a peek that covered them

private:
    const uint8_t *data_;
    size_t size_, pos_ = 0;
    uint64_t buf_ = 0;
    unsigned count_ = 0, over_ = 0;
};

// Canonical code: the codes of one length are consecutive and follow the symbol order. `constant` is the tree of one symbol that costs no bits.
class Huffman {
public:
    bool build(const std::vector<unsigned> &lengths, bool has_constant, unsigned constant, std::string *why)
    {
        constant_ = has_constant;
        constant_symbol_ = constant;
        longest_ = 0;
        table_.clear();
        if (has_constant) return true;
        for (unsigned length : lengths) longest_ = length > longest_ ? length : longest_;
        if (longest_ > 16) { *why = "code length over 16"; return false; }
        if (!longest_) return true;
        table_.assign(size_t(1) << longest_, -1);
        std::vector<unsigned> counts(longest_ + 1, 0);
        for (unsigned length : lengths) if (length) ++counts[length];
        std::vector<unsigned> symbols;
        for (unsigned length = 1; length <= longest_; ++length)
            for (size_t i = 0; i < lengths.size(); ++i) if (lengths[i] == length) symbols.push_back(unsigned(i));
        unsigned code = 0;
        size_t index = 0;
        for (unsigned length = 1; length <= longest_; ++length) {
            code = length > 1 ? (code + counts[length - 1]) << 1 : 0;
            if (uint64_t(code) + counts[length] > (uint64_t(1) << length)) { *why = "over-subscribed Huffman code"; return false; }
            const size_t span = size_t(1) << (longest_ - length);
            for (unsigned k = 0; k < counts[length]; ++k, ++index) {
                const size_t start = size_t(code + k) * span;
                for (size_t j = 0; j < span; ++j) table_[start + j] = int32_t((symbols[index] << 5) | length);
            }
        }
        return true;
    }
    bool read(Bits *bits, unsigned *symbol, std::string *why) const
    {
        if (constant_) { *symbol = constant_symbol_; return true; }
        if (!longest_) { *why = "Huffman table without codes"; return false; }
        const int32_t entry = table_[bits->peek(longest_)];
        if (entry < 0) { *why = "invalid Huffman code"; return false; }
        bits->drop(unsigned(entry) & 31);
        *symbol = unsigned(entry) >> 5;
        return true;
    }

private:
    bool constant_ = false;
    unsigned constant_symbol_ = 0, longest_ = 0;
    std::vector<int32_t> table_;
};

// Code lengths of a small alphabet; `special` is the index after which a run of zeros may be coded. Sets *constant when the table is one symbol that costs no bits.
inline bool length_table(Bits *bits, unsigned size, unsigned nbit, int special, std::vector<unsigned> *lengths, bool *has_constant, unsigned *constant, std::string *why)
{
    const unsigned n = bits->bits(nbit);
    lengths->assign(size, 0);
    *has_constant = false;
    if (n == 0) {
        *has_constant = true;
        *constant = bits->bits(nbit);
        return true;
    }
    if (n > size) { *why = "length table too long"; return false; }
    unsigned i = 0;
    while (i < n) {
        unsigned c = bits->peek(3);
        if (c == 7) {
            const uint32_t window = bits->peek(16);
            unsigned mask = 0x1000;
            while (window & mask) {
                mask >>= 1;
                ++c;
                if (!mask) { *why = "length code too long"; return false; }
            }
            bits->skip(c - 3);
        } else {
            bits->skip(3);
        }
        (*lengths)[i++] = c;
        if (int(i) == special) {
            const unsigned run = bits->bits(2);
            if (i + run > size) { *why = "length table overflow"; return false; }
            i += run;
        }
    }
    return true;
}

// The `out_size` bytes that the LH5/6/7 stream expands to.
inline bool decode(const uint8_t *data, size_t size, unsigned method_bits, size_t out_size, std::vector<uint8_t> *out, std::string *why)
{
    const unsigned position_symbols = method_bits + 1, position_bits = method_bits == 13 ? 4 : 5;
    const size_t window = size_t(1) << method_bits;
    Bits bits(data, size);
    out->clear();
    out->reserve(std::min(out_size, size * 8 + 1024));          // never from the size the header claims: the output grows with what is really decoded
    uint32_t block = 0;
    Huffman literal, position, meta;
    std::vector<unsigned> lengths;
    bool constant = false;
    unsigned constant_symbol = 0;
    while (out->size() < out_size) {
        if (block == 0) {
            block = bits.bits(16);
            if (block == 0) { *why = "empty block"; return false; }
            if (!length_table(&bits, kNT, kTBit, 3, &lengths, &constant, &constant_symbol, why)) return false;
            if (!meta.build(lengths, constant, constant_symbol, why)) return false;
            const unsigned n = bits.bits(kCBit);
            if (n == 0) {
                if (!literal.build(std::vector<unsigned>(kNC, 0), true, bits.bits(kCBit), why)) return false;
            } else {
                if (n > kNC) { *why = "too many literal lengths"; return false; }
                std::vector<unsigned> literal_lengths(kNC, 0);
                unsigned i = 0;
                while (i < n) {
                    unsigned c;
                    if (!meta.read(&bits, &c, why)) return false;
                    if (c <= 2) {
                        const unsigned run = c == 0 ? 1 : c == 1 ? bits.bits(4) + 3 : bits.bits(kCBit) + 20;
                        if (i + run > kNC) { *why = "literal length run overflow"; return false; }
                        i += run;
                    } else {
                        literal_lengths[i++] = c - 2;
                    }
                }
                if (!literal.build(literal_lengths, false, 0, why)) return false;
            }
            if (!length_table(&bits, position_symbols, position_bits, -1, &lengths, &constant, &constant_symbol, why)) return false;
            if (!position.build(lengths, constant, constant_symbol, why)) return false;
        }
        --block;
        unsigned symbol;
        if (!literal.read(&bits, &symbol, why)) return false;
        if (symbol < 256) {
            out->push_back(uint8_t(symbol));
        } else {
            const size_t length = symbol - 256 + kThreshold;
            unsigned j;
            if (!position.read(&bits, &j, why)) return false;
            const size_t distance = size_t(j < 2 ? j : (size_t(1) << (j - 1)) + bits.bits(j - 1)) + 1;
            if (distance > out->size() || distance > window) { *why = "match points before the start of the data"; return false; }
            if (out->size() + length > out_size) { *why = "data longer than the header says"; return false; }
            size_t from = out->size() - distance;
            for (size_t k = 0; k < length; ++k) out->push_back((*out)[from + k]);
        }
        if (bits.over() > 8) { *why = "compressed data ends early"; return false; }
    }
    return true;
}

// The bytes of a header name with 0xFF and '\' as separators, split into parts.
inline std::vector<std::string> parts_of(const std::string &raw)
{
    std::vector<std::string> parts(1);
    for (unsigned char c : raw) {
        if (c == 0xFF || c == '\\' || c == '/') parts.emplace_back();
        else parts.back().push_back(char(c));
    }
    return parts;
}

inline bool member_name(const std::string &directory_raw, const std::string &name_raw, bool is_dir, std::string *name, std::string *why)
{
    std::vector<std::string> parts;
    if (!directory_raw.empty()) parts = parts_of(directory_raw);
    if (!parts.empty() && parts.back().empty()) parts.pop_back();           // the directory header of a member ends with a separator
    if (!name_raw.empty()) {
        std::vector<std::string> tail = parts_of(name_raw);
        parts.insert(parts.end(), tail.begin(), tail.end());
    }
    if (is_dir) {
        std::vector<std::string> kept;
        for (std::string &part : parts) if (!part.empty()) kept.push_back(std::move(part));
        parts.swap(kept);
    }
    std::string joined;
    for (const std::string &part : parts) {
        bool unsafe = part.empty() || part == "." || part == ".." || part.size() > kMaxNamePart || part.find(':') != std::string::npos;
        for (unsigned char c : part) unsafe = unsafe || c < 32;
        if (unsafe) { *why = "unsafe member name"; return false; }
        joined += (joined.empty() ? "" : "/") + part;
    }
    if (parts.empty()) { *why = "member without a name"; return false; }
    *name = joined;
    return true;
}

// Walks the chain of extended headers that starts at the 2-byte size at `cursor`. Gives the file name and directory name, the bytes used after the first size, and the end.
inline bool extended(const uint8_t *blob, size_t blob_size, size_t cursor, size_t limit, std::string *name, std::string *directory, size_t *used, size_t *end, std::string *why)
{
    name->clear();
    directory->clear();
    *used = 0;
    if (cursor + 2 > blob_size || cursor + 2 > limit) { *why = "bad extended header"; return false; }
    size_t size = le16(blob + cursor);
    cursor += 2;
    while (size) {
        if (size < 3 || cursor + size > limit || cursor + size > blob_size) { *why = "bad extended header"; return false; }
        const uint8_t kind = blob[cursor];
        const std::string body(reinterpret_cast<const char *>(blob + cursor + 1), size - 3);
        if (kind == 0x01) *name = body;
        else if (kind == 0x02) *directory = body;
        *used += size;
        cursor += size - 2;
        if (cursor + 2 > blob_size || cursor + 2 > limit) { *why = "bad extended header"; return false; }
        size = le16(blob + cursor);
        cursor += 2;
    }
    *end = cursor;
    return true;
}

}  // namespace lha_detail

// The members of an LHA archive in archive order. False with the reason in `why` for anything unsafe, damaged, unsupported or over a limit.
inline bool read_lha(const uint8_t *blob, size_t size, const LhaLimits &limits, std::vector<LhaEntry> *entries, std::string *why)
{
    using namespace lha_detail;
    entries->clear();
    size_t pos = 0, total = 0;
    while (pos < size && blob[pos] != 0) {
        if (entries->size() >= limits.max_entries) { *why = "too many entries"; return false; }
        if (size - pos < 24) { *why = "truncated header"; return false; }
        const unsigned level = blob[pos + 20];
        const std::string method(reinterpret_cast<const char *>(blob + pos + 2), 5);
        std::string name_raw, directory_raw;
        uint16_t file_crc = 0;
        size_t skip = 0, header_len = 0;
        if (level == 0 || level == 1) {
            header_len = size_t(blob[pos]) + 2;
            if (header_len < 24 || header_len > size - pos) { *why = "truncated header"; return false; }
            unsigned sum = 0;
            for (size_t i = 2; i < header_len; ++i) sum += blob[pos + i];
            if ((sum & 0xFF) != blob[pos + 1]) { *why = "header checksum"; return false; }
            const size_t name_len = blob[pos + 21], tail = pos + 22 + name_len;
            if (tail + (level == 0 ? 2 : 5) > pos + header_len) { *why = "name beyond the header"; return false; }
            name_raw.assign(reinterpret_cast<const char *>(blob + pos + 22), name_len);
            file_crc = le16(blob + tail);
            if (level == 1) {
                std::string ext_name, ext_dir;
                size_t end = 0;
                if (!extended(blob, size, tail + 3, size, &ext_name, &ext_dir, &skip, &end, why)) return false;
                if (!ext_name.empty()) name_raw = ext_name;
                directory_raw = ext_dir;
            }
        } else if (level == 2) {
            header_len = le16(blob + pos);
            if (header_len < 26 || header_len > size - pos) { *why = "truncated header"; return false; }
            file_crc = le16(blob + pos + 21);
            size_t used = 0, end = 0;
            if (!extended(blob, size, pos + 24, pos + header_len, &name_raw, &directory_raw, &used, &end, why)) return false;
            if (end > pos + header_len) { *why = "extended headers beyond the header"; return false; }
        } else {
            *why = "header level " + std::to_string(level) + " is not supported";
            return false;
        }
        size_t packed = le32(blob + pos + 7);
        const size_t original = le32(blob + pos + 11);
        if (skip > packed) { *why = "extended headers longer than the packed size"; return false; }
        packed -= skip;
        const size_t data_at = pos + header_len + skip;
        const bool is_dir = method == "-lhd-";
        unsigned method_bits = 0;
        if (method == "-lh5-") method_bits = 13;
        else if (method == "-lh6-") method_bits = 15;
        else if (method == "-lh7-") method_bits = 16;
        if (!is_dir && method != "-lh0-" && !method_bits) { *why = "method " + method + " is not supported"; return false; }
        if (data_at > size || packed > size - data_at) { *why = "packed data beyond the end of the archive"; return false; }
        if (original > limits.max_file) { *why = "member larger than the limit"; return false; }
        total += original;
        if (total > limits.max_total) { *why = "archive larger than the limit"; return false; }
        LhaEntry entry;
        if (!member_name(directory_raw, name_raw, is_dir, &entry.name, why)) return false;
        entry.is_dir = is_dir;
        if (!is_dir) {
            if (method == "-lh0-") {
                if (packed != original) { *why = "stored member with different sizes"; return false; }
                entry.data.assign(blob + data_at, blob + data_at + packed);
            } else if (!decode(blob + data_at, packed, method_bits, original, &entry.data, why)) {
                *why += " (" + entry.name + ")";
                return false;
            }
            if (crc16(entry.data.data(), entry.data.size()) != file_crc) { *why = "CRC of " + entry.name; return false; }
        }
        entries->push_back(std::move(entry));
        pos = data_at + packed;
    }
    return true;
}

}  // namespace p6_libretro

#endif
