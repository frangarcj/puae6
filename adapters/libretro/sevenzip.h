// A bounded 7z reader (T59): the archive container of 7-Zip (7zFormat.txt) and its Copy, LZMA and LZMA2 methods, decoded in memory, with no temporary file and no write. Header-only, no
// engine, no ABI. It is written for hostile input, in the style of inflate.h and lha.h:
//   - Nothing is sized from a number the archive claims. Every count is checked against the bytes that are left in the header (each item takes at least one byte) and against `Limits`
//     before a vector grows; the sum of what the folders unpack to is capped (`max_unpack`) before the first byte is decoded, and a folder is decoded into one buffer of exactly its
//     declared size, so a decompression bomb costs at most `max_unpack` bytes of memory and as many symbols of time (a match or a literal makes at least one byte; an LZMA2 chunk
//     makes at least one and is bounded by its declared size).
//   - Reads past the end of the input are a result (a flag in the range decoder, a bounds check in the parser), never a memory access. A distance that reaches before the start of the
//     dictionary, a chunk that overruns the folder, a CRC that does not match and a header that disagrees with itself are all "corrupt".
//   - What is read: the signature header with both CRCs, plain and LZMA-encoded headers (also encoded twice), solid and non-solid folders, empty files and directories. One coder per folder,
//     and it is Copy, LZMA (030101) or LZMA2 (21): the filters (BCJ, Delta, ARM...), BCJ2, PPMd and AES are named in the refusal. Nothing else of the archive (times, attributes,
//     comments) is looked at, and nothing is ever run.
//   - The dictionary is the output buffer itself, so the dictionary size an archive asks for costs nothing and is not used; a distance is valid when it does not reach before the start of
//     the data (or of the LZMA2 dictionary reset). A conforming encoder never produces a distance beyond its own dictionary, so this accepts every archive 7-Zip reads and no memory the
//     archive could choose.
// The LZMA decoder follows the public description of the algorithm (the range decoder with 11-bit probabilities, the 12-state machine, literals with and without a match byte, the length
// coders and the distance slots with their direct and aligned bits); tests/libretro-host/test_archives.py checks it against Python's liblzma and against 7-Zip itself.
#ifndef P6_LIBRETRO_SEVENZIP_H
#define P6_LIBRETRO_SEVENZIP_H

#include "inflate.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <new>
#include <string>
#include <vector>

namespace p6_libretro {
namespace sevenzip {

struct Limits {
    size_t max_entries = 4096;                       // files and directories of one archive
    uint64_t max_unpack = 32u * 1024 * 1024;         // what all the folders unpack to together; also the most one folder can be
    size_t max_header = 4u * 1024 * 1024;            // a header once decoded
};

struct Entry {
    std::string name;                                // UTF-8, with '/' separators as the archive stores them
    bool is_dir = false;
    bool has_stream = false;                         // false for a directory and for an empty file
    uint64_t size = 0;
    bool has_crc = false;
    uint32_t crc = 0;
    size_t folder = 0;
    uint64_t offset = 0;                             // inside the unpacked folder
};

namespace detail {

enum Id : unsigned {
    kEnd = 0x00, kHeader = 0x01, kArchiveProperties = 0x02, kAdditionalStreamsInfo = 0x03, kMainStreamsInfo = 0x04, kFilesInfo = 0x05, kPackInfo = 0x06, kUnpackInfo = 0x07,
    kSubStreamsInfo = 0x08, kSize = 0x09, kCrc = 0x0A, kFolder = 0x0B, kCodersUnpackSize = 0x0C, kNumUnpackStream = 0x0D, kEmptyStream = 0x0E, kEmptyFile = 0x0F,
    kAnti = 0x10, kNames = 0x11, kEncodedHeader = 0x17
};

constexpr size_t kSignatureHeader = 32;
constexpr unsigned kMaxCoders = 8, kMaxStreamsPerCoder = 8;

inline uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
inline uint64_t le64(const uint8_t *p) { return uint64_t(le32(p)) | (uint64_t(le32(p + 4)) << 32); }

// --- the bytes of a header ----------------------------------------------------------------------------------------------------------------------

class Reader {
public:
    Reader(const uint8_t *data, size_t size) : p_(data), n_(size) {}
    bool ok() const { return !bad_; }
    size_t left() const { return bad_ ? 0 : n_ - at_; }
    void fail() { bad_ = true; }
    uint8_t u8()
    {
        if (bad_ || at_ >= n_) { bad_ = true; return 0; }
        return p_[at_++];
    }
    uint32_t u32()
    {
        if (bad_ || n_ - at_ < 4) { bad_ = true; return 0; }
        const uint32_t v = le32(p_ + at_);
        at_ += 4;
        return v;
    }
    // The variable-length number of 7z: the leading ones of the first byte say how many bytes follow.
    uint64_t number()
    {
        const unsigned first = u8();
        uint64_t value = 0;
        unsigned mask = 0x80;
        for (unsigned i = 0; i < 8; ++i) {
            if (!(first & mask)) {
                value += uint64_t(first & (mask - 1)) << (8 * i);
                return value;
            }
            value |= uint64_t(u8()) << (8 * i);
            mask >>= 1;
        }
        return value;
    }
    // A count that stands for items that each take at least `unit` bytes of what is left: a number above that cannot be true.
    uint64_t count(uint64_t cap, size_t unit = 1)
    {
        const uint64_t value = number();
        if (bad_ || value > cap || value > left() / (unit ? unit : 1)) { bad_ = true; return 0; }
        return value;
    }
    const uint8_t *take(size_t size)
    {
        if (bad_ || n_ - at_ < size) { bad_ = true; return nullptr; }
        const uint8_t *here = p_ + at_;
        at_ += size;
        return here;
    }
    void skip(uint64_t size)
    {
        if (bad_ || size > n_ - at_) { bad_ = true; return; }
        at_ += size_t(size);
    }

private:
    const uint8_t *p_;
    size_t n_, at_ = 0;
    bool bad_ = false;
};

// A vector of `n` booleans, most significant bit first.
inline bool read_bits(Reader &r, size_t n, std::vector<bool> *bits)
{
    bits->assign(n, false);
    const uint8_t *bytes = r.take((n + 7) / 8);
    if (!bytes) return false;
    for (size_t i = 0; i < n; ++i) (*bits)[i] = (bytes[i / 8] >> (7 - i % 8)) & 1;
    return true;
}

// Digests: "all defined" or a bit vector, then one CRC-32 for each defined item.
inline bool read_digests(Reader &r, size_t n, std::vector<bool> *defined, std::vector<uint32_t> *crc)
{
    const unsigned all = r.u8();
    if (!r.ok()) return false;
    if (all) defined->assign(n, true);
    else if (!read_bits(r, n, defined)) return false;
    crc->assign(n, 0);
    for (size_t i = 0; i < n; ++i)
        if ((*defined)[i]) (*crc)[i] = r.u32();
    return r.ok();
}

// --- the structure of the streams ----------------------------------------------------------------------------------------------------------------

struct Coder {
    uint64_t method = 0;                             // the id bytes as a big-endian number
    unsigned id_size = 0;
    unsigned in_streams = 1, out_streams = 1;
    std::vector<uint8_t> props;
};

struct Folder {
    std::vector<Coder> coders;
    unsigned bind_pairs = 0;
    unsigned packed_streams = 0;                     // how many of the pack streams are this folder's
    unsigned main_out = 0;                           // the out stream no bind pair reads: the folder's result
    std::vector<uint64_t> unpack_sizes;              // one per out stream of every coder
    uint64_t unpack_size = 0;                        // the one of the out stream nobody reads: what the folder produces
    bool has_crc = false;
    uint32_t crc = 0;
    uint64_t pack_offset = 0, pack_size = 0;         // absolute in the file (the first pack stream, the only one of a decodable folder)
    unsigned substreams = 1;
};

struct Substream {
    uint64_t size = 0;
    bool has_crc = false;
    uint32_t crc = 0;
};

struct Streams {
    uint64_t pack_pos = 0;
    std::vector<uint64_t> pack_sizes;
    std::vector<Folder> folders;
    std::vector<Substream> substreams;               // every folder's, in order
};

inline const char *method_name(uint64_t method)
{
    switch (method) {
    case 0x21: return "LZMA2";
    case 0x03: return "Delta";
    case 0x04: return "BCJ x86";
    case 0x05: return "PPC";
    case 0x06: return "IA64";
    case 0x07: return "ARM";
    case 0x08: return "ARM Thumb";
    case 0x09: return "SPARC";
    case 0x0A: return "ARM64";
    case 0x030101: return "LZMA";
    case 0x030401: return "PPMd";
    case 0x03030103: return "BCJ x86";
    case 0x0303011B: return "BCJ2";
    case 0x03030205: return "PPC";
    case 0x03030401: return "IA64";
    case 0x03030501: return "ARM";
    case 0x03030701: return "ARM Thumb";
    case 0x03030805: return "SPARC";
    case 0x040108: return "Deflate";
    case 0x040202: return "BZip2";
    case 0x06F10701: return "AES";
    default: return nullptr;
    }
}

inline std::string method_text(uint64_t method)
{
    char hex[32];
    snprintf(hex, sizeof hex, "0x%llX", (unsigned long long)method);
    const char *name = method_name(method);
    return std::string(name ? name : "an unknown method") + " (" + hex + ")";
}

inline bool read_coders(Reader &r, const Limits &, Folder *folder)
{
    const uint64_t count = r.count(kMaxCoders, 2);
    if (!r.ok() || count == 0) return false;
    unsigned in_total = 0, out_total = 0;
    for (uint64_t i = 0; i < count; ++i) {
        Coder coder;
        const unsigned flags = r.u8();
        coder.id_size = flags & 0x0F;
        if ((flags & 0x80) != 0 || coder.id_size > 8) return false;
        const uint8_t *id = r.take(coder.id_size);
        if (!id) return false;
        for (unsigned b = 0; b < coder.id_size; ++b) coder.method = (coder.method << 8) | id[b];
        if (flags & 0x10) {
            const uint64_t in = r.number(), out = r.number();
            if (!r.ok() || in == 0 || in > kMaxStreamsPerCoder || out == 0 || out > kMaxStreamsPerCoder) return false;
            coder.in_streams = unsigned(in);
            coder.out_streams = unsigned(out);
        }
        if (flags & 0x20) {
            const uint64_t size = r.number();
            if (!r.ok() || size > 64 || size > r.left()) return false;
            const uint8_t *props = r.take(size_t(size));
            if (!props) return false;
            coder.props.assign(props, props + size);
        }
        in_total += coder.in_streams;
        out_total += coder.out_streams;
        folder->coders.push_back(std::move(coder));
    }
    if (out_total == 0 || out_total > kMaxCoders * kMaxStreamsPerCoder) return false;
    folder->bind_pairs = out_total - 1;
    if (in_total < folder->bind_pairs) return false;
    folder->packed_streams = in_total - folder->bind_pairs;
    std::vector<bool> in_bound(in_total, false), out_bound(out_total, false);
    for (unsigned i = 0; i < folder->bind_pairs; ++i) {
        const uint64_t in = r.number(), out = r.number();
        if (!r.ok() || in >= in_total || out >= out_total || in_bound[size_t(in)] || out_bound[size_t(out)]) return false;
        in_bound[size_t(in)] = out_bound[size_t(out)] = true;
    }
    for (unsigned i = 0; i < out_total; ++i)
        if (!out_bound[i]) folder->main_out = i;
    if (folder->packed_streams > 1) {
        for (unsigned i = 0; i < folder->packed_streams; ++i) {
            const uint64_t index = r.number();
            if (!r.ok() || index >= in_total) return false;
        }
    }
    return r.ok();
}

inline bool read_streams(Reader &r, const Limits &limits, uint64_t file_size, Streams *streams, std::string *why)
{
    auto damaged = [&](const char *what) {
        *why = std::string("the 7z archive is damaged (") + what + ")";
        return false;
    };
    bool have_pack = false, have_unpack = false;
    for (;;) {
        const uint64_t id = r.number();
        if (!r.ok()) return damaged("a header is cut short");
        if (id == kEnd) break;
        if (id == kPackInfo) {
            streams->pack_pos = r.number();
            const uint64_t count = r.count(limits.max_entries);
            if (!r.ok()) return damaged("bad pack info");
            streams->pack_sizes.clear();
            for (;;) {
                const uint64_t sub = r.number();
                if (!r.ok()) return damaged("bad pack info");
                if (sub == kEnd) break;
                if (sub == kSize) {
                    for (uint64_t i = 0; i < count; ++i) streams->pack_sizes.push_back(r.number());
                    if (!r.ok()) return damaged("bad pack sizes");
                } else if (sub == kCrc) {
                    std::vector<bool> defined;
                    std::vector<uint32_t> crc;
                    if (!read_digests(r, size_t(count), &defined, &crc)) return damaged("bad pack digests");
                } else {
                    return damaged("unknown pack info property");
                }
            }
            if (streams->pack_sizes.size() != count) return damaged("pack sizes are missing");
            have_pack = true;
        } else if (id == kUnpackInfo) {
            if (r.u8() != kFolder) return damaged("unpack info without folders");
            const uint64_t count = r.count(limits.max_entries, 2);
            if (!r.ok() || r.u8() != 0) return damaged("bad folders (external data is not read)");
            streams->folders.assign(size_t(count), Folder());
            for (Folder &folder : streams->folders)
                if (!read_coders(r, limits, &folder)) return damaged("bad coders");
            if (r.u8() != kCodersUnpackSize) return damaged("folders without unpack sizes");
            for (Folder &folder : streams->folders) {
                for (unsigned i = 0; i <= folder.bind_pairs; ++i) folder.unpack_sizes.push_back(r.number());
                if (!r.ok()) return damaged("bad unpack sizes");
                folder.unpack_size = folder.unpack_sizes[folder.main_out];
            }
            for (;;) {
                const uint64_t sub = r.number();
                if (!r.ok()) return damaged("bad unpack info");
                if (sub == kEnd) break;
                if (sub == kCrc) {
                    std::vector<bool> defined;
                    std::vector<uint32_t> crc;
                    if (!read_digests(r, streams->folders.size(), &defined, &crc)) return damaged("bad folder digests");
                    for (size_t i = 0; i < streams->folders.size(); ++i) {
                        streams->folders[i].has_crc = defined[i];
                        streams->folders[i].crc = crc[i];
                    }
                } else {
                    return damaged("unknown unpack info property");
                }
            }
            have_unpack = true;
        } else if (id == kSubStreamsInfo) {
            if (!have_unpack) return damaged("sub streams before folders");
            uint64_t sub = r.number();
            uint64_t total = 0;
            if (sub == kNumUnpackStream) {
                for (Folder &folder : streams->folders) {
                    const uint64_t n = r.number();
                    if (!r.ok() || n > limits.max_entries || total + n > limits.max_entries) return damaged("too many files");
                    folder.substreams = unsigned(n);
                    total += n;
                }
                sub = r.number();
            } else {
                total = streams->folders.size();
            }
            streams->substreams.clear();
            for (Folder &folder : streams->folders) {
                if (folder.substreams == 0) continue;
                uint64_t sum = 0;
                for (unsigned i = 0; i + 1 < folder.substreams; ++i) {
                    if (sub != kSize) return damaged("sub stream sizes are missing");
                    const uint64_t size = r.number();
                    if (!r.ok() || size > folder.unpack_size - sum) return damaged("a sub stream is larger than its folder");
                    sum += size;
                    Substream s;
                    s.size = size;
                    streams->substreams.push_back(s);
                }
                Substream last;
                last.size = folder.unpack_size - sum;
                streams->substreams.push_back(last);
            }
            if (!r.ok()) return damaged("bad sub stream sizes");
            if (sub == kSize) sub = r.number();
            // Digests: one for every sub stream whose CRC the folder does not already say.
            size_t wanted = 0;
            for (const Folder &folder : streams->folders)
                if (folder.substreams && !(folder.substreams == 1 && folder.has_crc)) wanted += folder.substreams;
            size_t at = 0;
            for (const Folder &folder : streams->folders) {                // the CRC of a folder with one sub stream is that sub stream's
                if (folder.substreams == 1 && folder.has_crc) {
                    streams->substreams[at].has_crc = true;
                    streams->substreams[at].crc = folder.crc;
                }
                at += folder.substreams;
            }
            while (r.ok() && sub != kEnd) {
                if (sub != kCrc) return damaged("unknown sub streams property");
                std::vector<bool> defined;
                std::vector<uint32_t> crc;
                if (!read_digests(r, wanted, &defined, &crc)) return damaged("bad sub stream digests");
                size_t k = 0, index = 0;
                for (const Folder &folder : streams->folders) {
                    if (folder.substreams == 1 && folder.has_crc) { ++index; continue; }
                    for (unsigned i = 0; i < folder.substreams; ++i, ++k, ++index) {
                        streams->substreams[index].has_crc = defined[k];
                        streams->substreams[index].crc = crc[k];
                    }
                }
                sub = r.number();
            }
            if (!r.ok()) return damaged("bad sub streams");
        } else {
            return damaged("unknown streams property");
        }
    }
    if (!have_unpack) {
        if (have_pack && !streams->pack_sizes.empty()) return damaged("pack streams without folders");
        return true;
    }
    // Without sub streams info every folder is one stream.
    if (streams->substreams.empty()) {
        bool explicit_zero = false;
        for (const Folder &folder : streams->folders) explicit_zero = explicit_zero || folder.substreams != 1;
        if (!explicit_zero) {
            for (const Folder &folder : streams->folders) {
                Substream s;
                s.size = folder.unpack_size;
                s.has_crc = folder.has_crc;
                s.crc = folder.crc;
                streams->substreams.push_back(s);
            }
        }
    }
    // The pack streams of the folders, in order, and the caps.
    uint64_t total = 0, position = kSignatureHeader;
    if (streams->pack_pos > file_size) return damaged("pack streams out of range");
    position += streams->pack_pos;
    size_t next = 0;
    for (Folder &folder : streams->folders) {
        if (folder.unpack_size > limits.max_unpack || total + folder.unpack_size > limits.max_unpack) {
            *why = "the 7z archive unpacks to more than the core reads (" + std::to_string(limits.max_unpack >> 20) + " MiB)";
            return false;
        }
        total += folder.unpack_size;
        if (folder.packed_streams == 0 || next + folder.packed_streams > streams->pack_sizes.size()) return damaged("a folder has no pack stream");
        folder.pack_offset = position;
        folder.pack_size = streams->pack_sizes[next];
        for (unsigned i = 0; i < folder.packed_streams; ++i) {
            const uint64_t size = streams->pack_sizes[next++];
            if (size > file_size || position > file_size || size > file_size - position) return damaged("packed data beyond the end of the archive");
            position += size;
        }
    }
    return true;
}

// --- LZMA ----------------------------------------------------------------------------------------------------------------------------------------

constexpr unsigned kNumStates = 12, kPosStatesMax = 16, kLenToPosStates = 4, kEndPosModel = 14, kFullDistances = 128, kAlignBits = 4;

class Rc {
public:
    bool init(const uint8_t *in, size_t n)
    {
        if (n < 5 || in[0] != 0) return false;
        p_ = in + 5;
        end_ = in + n;
        range_ = 0xFFFFFFFFu;
        code_ = (uint32_t(in[1]) << 24) | (uint32_t(in[2]) << 16) | (uint32_t(in[3]) << 8) | in[4];
        bad_ = false;
        return code_ != range_;
    }
    bool bad() const { return bad_; }
    unsigned bit(uint16_t &prob)
    {
        normalize();
        const uint32_t bound = (range_ >> 11) * prob;
        if (code_ < bound) {
            range_ = bound;
            prob = uint16_t(prob + ((2048 - prob) >> 5));
            return 0;
        }
        range_ -= bound;
        code_ -= bound;
        prob = uint16_t(prob - (prob >> 5));
        return 1;
    }
    uint32_t direct(unsigned bits)
    {
        uint32_t result = 0;
        while (bits--) {
            normalize();
            range_ >>= 1;
            code_ -= range_;
            const uint32_t t = 0 - (code_ >> 31);
            code_ += range_ & t;
            result = (result << 1) + t + 1;
        }
        return result;
    }
    unsigned tree(uint16_t *probs, unsigned bits)
    {
        unsigned m = 1;
        for (unsigned i = 0; i < bits; ++i) m = (m << 1) | bit(probs[m]);
        return m - (1u << bits);
    }
    unsigned reverse_tree(uint16_t *probs, unsigned bits)
    {
        unsigned m = 1, symbol = 0;
        for (unsigned i = 0; i < bits; ++i) {
            const unsigned b = bit(probs[m]);
            m = (m << 1) | b;
            symbol |= b << i;
        }
        return symbol;
    }

private:
    void normalize()
    {
        if (range_ < (1u << 24)) {
            range_ <<= 8;
            if (p_ < end_) code_ = (code_ << 8) | *p_++;
            else { bad_ = true; code_ <<= 8; }
        }
    }
    const uint8_t *p_ = nullptr, *end_ = nullptr;
    uint32_t range_ = 0, code_ = 0;
    bool bad_ = true;
};

inline void reset_probs(uint16_t *p, size_t n) { for (size_t i = 0; i < n; ++i) p[i] = 1024; }

struct LenCoder {
    uint16_t choice, choice2, low[kPosStatesMax][8], mid[kPosStatesMax][8], high[256];
    void reset()
    {
        choice = choice2 = 1024;
        reset_probs(&low[0][0], sizeof low / sizeof(uint16_t));
        reset_probs(&mid[0][0], sizeof mid / sizeof(uint16_t));
        reset_probs(high, 256);
    }
    unsigned decode(Rc &rc, unsigned pos_state)
    {
        if (!rc.bit(choice)) return rc.tree(low[pos_state], 3);
        if (!rc.bit(choice2)) return 8 + rc.tree(mid[pos_state], 3);
        return 16 + rc.tree(high, 8);
    }
};

enum class LzmaResult { Ok, Corrupt, EndMarker };

class Lzma {
public:
    // lc, lp and pb from the properties byte; false when it is out of range or asks for more literal contexts than `max_literal_bits` (lc + lp).
    bool set_props(unsigned byte, unsigned max_literal_bits)
    {
        if (byte >= 9 * 5 * 5) return false;
        lc_ = byte % 9;
        byte /= 9;
        lp_ = byte % 5;
        pb_ = byte / 5;
        if (lc_ + lp_ > max_literal_bits) return false;
        literal_.assign(size_t(0x300) << (lc_ + lp_), 1024);
        return true;
    }
    void reset_state()
    {
        reset_probs(is_match_, sizeof is_match_ / sizeof(uint16_t));
        reset_probs(is_rep_, kNumStates);
        reset_probs(is_rep_g0_, kNumStates);
        reset_probs(is_rep_g1_, kNumStates);
        reset_probs(is_rep_g2_, kNumStates);
        reset_probs(is_rep0_long_, sizeof is_rep0_long_ / sizeof(uint16_t));
        reset_probs(&pos_slot_[0][0], sizeof pos_slot_ / sizeof(uint16_t));
        reset_probs(pos_decoders_, sizeof pos_decoders_ / sizeof(uint16_t));
        reset_probs(align_, sizeof align_ / sizeof(uint16_t));
        reset_probs(literal_.data(), literal_.size());
        len_.reset();
        rep_len_.reset();
        state_ = 0;
        rep0_ = rep1_ = rep2_ = rep3_ = 0;
    }

    // Decodes from the range-coded bytes in[0..n) into out[pos..end). `dict_start` is where the dictionary begins (matches do not reach before it, and the position bits count from it).
    LzmaResult decode(const uint8_t *in, size_t n, uint8_t *out, size_t pos, size_t end, size_t dict_start)
    {
        Rc rc;
        if (!rc.init(in, n)) return LzmaResult::Corrupt;
        const unsigned pb_mask = (1u << pb_) - 1, lp_mask = (1u << lp_) - 1;
        while (pos < end) {
            const size_t processed = pos - dict_start;
            const unsigned pos_state = unsigned(processed) & pb_mask;
            if (!rc.bit(is_match_[(state_ << 4) + pos_state])) {
                const unsigned prev = processed ? out[pos - 1] : 0;
                uint16_t *probs = &literal_[size_t(0x300) * (((unsigned(processed) & lp_mask) << lc_) + (prev >> (8 - lc_)))];
                unsigned symbol = 1;
                if (state_ >= 7) {
                    if (rep0_ >= processed) return LzmaResult::Corrupt;
                    unsigned match_byte = out[pos - rep0_ - 1];
                    unsigned offs = 0x100;
                    while (symbol < 0x100) {
                        match_byte <<= 1;
                        const unsigned match_bit = match_byte & offs;
                        const unsigned b = rc.bit(probs[offs + match_bit + symbol]);
                        symbol = (symbol << 1) | b;
                        offs &= b ? match_bit : ~match_bit;
                    }
                } else {
                    while (symbol < 0x100) symbol = (symbol << 1) | rc.bit(probs[symbol]);
                }
                out[pos++] = uint8_t(symbol);
                state_ = state_ < 4 ? 0 : state_ < 10 ? state_ - 3 : state_ - 6;
                if (rc.bad()) return LzmaResult::Corrupt;
                continue;
            }
            unsigned len;
            if (!rc.bit(is_rep_[state_])) {
                rep3_ = rep2_;
                rep2_ = rep1_;
                rep1_ = rep0_;
                len = len_.decode(rc, pos_state);
                state_ = state_ < 7 ? 7 : 10;
                // The distance.
                const unsigned slot = rc.tree(pos_slot_[len < kLenToPosStates ? len : kLenToPosStates - 1], 6);
                uint32_t distance;
                if (slot < 4) {
                    distance = slot;
                } else {
                    const unsigned direct_bits = (slot >> 1) - 1;
                    distance = (2 | (slot & 1)) << direct_bits;
                    if (slot < kEndPosModel) {
                        distance += rc.reverse_tree(pos_decoders_ + distance - slot, direct_bits);
                    } else {
                        distance += rc.direct(direct_bits - kAlignBits) << kAlignBits;
                        distance += rc.reverse_tree(align_, kAlignBits);
                    }
                }
                rep0_ = distance;
                if (rep0_ == 0xFFFFFFFFu) return rc.bad() ? LzmaResult::Corrupt : LzmaResult::EndMarker;
            } else {
                if (processed == 0) return LzmaResult::Corrupt;
                if (!rc.bit(is_rep_g0_[state_])) {
                    if (!rc.bit(is_rep0_long_[(state_ << 4) + pos_state])) {
                        if (rep0_ >= processed) return LzmaResult::Corrupt;
                        state_ = state_ < 7 ? 9 : 11;
                        out[pos] = out[pos - rep0_ - 1];
                        ++pos;
                        if (rc.bad()) return LzmaResult::Corrupt;
                        continue;
                    }
                } else {
                    uint32_t distance;
                    if (!rc.bit(is_rep_g1_[state_])) {
                        distance = rep1_;
                    } else {
                        if (!rc.bit(is_rep_g2_[state_])) {
                            distance = rep2_;
                        } else {
                            distance = rep3_;
                            rep3_ = rep2_;
                        }
                        rep2_ = rep1_;
                    }
                    rep1_ = rep0_;
                    rep0_ = distance;
                }
                len = rep_len_.decode(rc, pos_state);
                state_ = state_ < 7 ? 8 : 11;
            }
            len += 2;
            if (rc.bad() || rep0_ >= processed || len > end - pos) return LzmaResult::Corrupt;
            const uint8_t *from = out + pos - rep0_ - 1;
            uint8_t *to = out + pos;
            for (unsigned i = 0; i < len; ++i) to[i] = from[i];            // may overlap: byte by byte, forwards
            pos += len;
        }
        return rc.bad() ? LzmaResult::Corrupt : LzmaResult::Ok;
    }

private:
    unsigned lc_ = 0, lp_ = 0, pb_ = 0, state_ = 0;
    uint32_t rep0_ = 0, rep1_ = 0, rep2_ = 0, rep3_ = 0;
    uint16_t is_match_[kNumStates << 4], is_rep_[kNumStates], is_rep_g0_[kNumStates], is_rep_g1_[kNumStates], is_rep_g2_[kNumStates], is_rep0_long_[kNumStates << 4];
    uint16_t pos_slot_[kLenToPosStates][64], pos_decoders_[1 + kFullDistances - kEndPosModel], align_[1 << kAlignBits];
    LenCoder len_, rep_len_;
    std::vector<uint16_t> literal_;
};

// LZMA with the 5 properties bytes of a 7z coder: exactly `out_size` bytes, optionally followed by an end marker the encoder chose to write.
inline bool decode_lzma(const std::vector<uint8_t> &props, const uint8_t *in, size_t n, uint8_t *out, size_t out_size, std::string *why)
{
    if (props.size() != 5) { *why = "the LZMA coder has bad properties"; return false; }
    Lzma lzma;
    if (!lzma.set_props(props[0], 10)) { *why = "the LZMA coder has bad properties (or asks for more literal context bits, lc + lp, than any encoder writes)"; return false; }
    lzma.reset_state();
    if (out_size == 0) return true;
    const LzmaResult result = lzma.decode(in, n, out, 0, out_size, 0);
    if (result != LzmaResult::Ok) { *why = "the LZMA data is corrupt or truncated"; return false; }
    return true;
}

// LZMA2: chunks of stored or LZMA data, each with its sizes in front (the control byte says which, and what is reset).
inline bool decode_lzma2(const std::vector<uint8_t> &props, const uint8_t *in, size_t n, uint8_t *out, size_t out_size, std::string *why)
{
    if (props.size() != 1 || props[0] > 40) { *why = "the LZMA2 coder has bad properties"; return false; }
    Lzma lzma;
    size_t at = 0, pos = 0, dict_start = 0;
    bool need_dict = true, need_props = true;
    auto corrupt = [&]() { *why = "the LZMA2 data is corrupt or truncated"; return false; };
    for (;;) {
        if (at >= n) return pos == out_size ? true : corrupt();            // a stream that ends without its end byte but has everything is not harmed
        const unsigned control = in[at++];
        if (control == 0) break;
        if (control == 1 || control == 2) {
            if (n - at < 2) return corrupt();
            const size_t size = ((size_t(in[at]) << 8) | in[at + 1]) + 1;
            at += 2;
            if (control == 1) {
                dict_start = pos;
                need_dict = false;
                need_props = true;
            } else if (need_dict) {
                return corrupt();
            }
            if (size > n - at || size > out_size - pos) return corrupt();
            memcpy(out + pos, in + at, size);
            at += size;
            pos += size;
            continue;
        }
        if (control < 0x80) return corrupt();
        if (n - at < 4) return corrupt();
        const unsigned mode = (control >> 5) & 3;
        const size_t unpack = ((size_t(control & 0x1F) << 16) | (size_t(in[at]) << 8) | in[at + 1]) + 1;
        const size_t pack = ((size_t(in[at + 2]) << 8) | in[at + 3]) + 1;
        at += 4;
        if (mode == 3) {
            dict_start = pos;
            need_dict = false;
        } else if (need_dict) {
            return corrupt();
        }
        if (mode >= 2) {
            if (at >= n || !lzma.set_props(in[at++], 4)) return corrupt();
            need_props = false;
        } else if (need_props) {
            return corrupt();
        }
        if (mode >= 1) lzma.reset_state();
        if (pack > n - at || unpack > out_size - pos) return corrupt();
        if (lzma.decode(in + at, pack, out, pos, pos + unpack, dict_start) != LzmaResult::Ok) return corrupt();
        at += pack;
        pos += unpack;
    }
    return pos == out_size ? true : corrupt();
}

// UTF-16LE with surrogate pairs to UTF-8; false for an unpaired surrogate.
inline bool utf8_from_utf16(const uint8_t *p, size_t units, std::string *out)
{
    out->clear();
    for (size_t i = 0; i < units; ++i) {
        uint32_t c = uint32_t(p[2 * i]) | (uint32_t(p[2 * i + 1]) << 8);
        if (c >= 0xD800 && c < 0xDC00) {
            if (i + 1 >= units) return false;
            const uint32_t d = uint32_t(p[2 * i + 2]) | (uint32_t(p[2 * i + 3]) << 8);
            if (d < 0xDC00 || d >= 0xE000) return false;
            c = 0x10000 + ((c - 0xD800) << 10) + (d - 0xDC00);
            ++i;
        } else if (c >= 0xDC00 && c < 0xE000) {
            return false;
        }
        if (c < 0x80) {
            out->push_back(char(c));
        } else if (c < 0x800) {
            out->push_back(char(0xC0 | (c >> 6)));
            out->push_back(char(0x80 | (c & 0x3F)));
        } else if (c < 0x10000) {
            out->push_back(char(0xE0 | (c >> 12)));
            out->push_back(char(0x80 | ((c >> 6) & 0x3F)));
            out->push_back(char(0x80 | (c & 0x3F)));
        } else {
            out->push_back(char(0xF0 | (c >> 18)));
            out->push_back(char(0x80 | ((c >> 12) & 0x3F)));
            out->push_back(char(0x80 | ((c >> 6) & 0x3F)));
            out->push_back(char(0x80 | (c & 0x3F)));
        }
    }
    return true;
}

}  // namespace detail

class Archive {
public:
    // `data` must stay alive and unchanged while the archive is used.
    bool open(const uint8_t *data, size_t size, const Limits &limits, std::string *why)
    {
        using namespace detail;
        data_ = data;
        size_ = size;
        limits_ = limits;
        entries_.clear();
        folders_.clear();
        cached_ = SIZE_MAX;
        if (size < kSignatureHeader || memcmp(data, "7z\xBC\xAF\x27\x1C", 6) != 0) { *why = "the file is not a 7z archive"; return false; }
        if (data[6] != 0) { *why = "the 7z archive is of a newer format version than the core reads"; return false; }
        if (le32(data + 8) != crc32_update(0, data + 12, 20)) { *why = "the 7z archive is damaged (bad start header CRC)"; return false; }
        const uint64_t offset = le64(data + 12), header_size = le64(data + 20);
        const uint32_t header_crc = le32(data + 28);
        if (header_size == 0) return true;                                  // an archive of nothing
        if (offset > size - kSignatureHeader || header_size > size - kSignatureHeader - offset) { *why = "the 7z archive is truncated or damaged (header out of range)"; return false; }
        if (header_size > limits.max_header) { *why = "the 7z archive has a header larger than the core reads"; return false; }
        std::vector<uint8_t> header(data + kSignatureHeader + offset, data + kSignatureHeader + offset + header_size);
        if (crc32_update(0, header.data(), header.size()) != header_crc) { *why = "the 7z archive is damaged (bad header CRC)"; return false; }
        for (unsigned round = 0; round < 4; ++round) {
            Reader r(header.data(), header.size());
            const uint64_t id = r.number();
            if (!r.ok()) { *why = "the 7z archive is damaged (empty header)"; return false; }
            if (id == kHeader) return read_header(r, why);
            if (id != kEncodedHeader) { *why = "the 7z archive is damaged (unknown header type)"; return false; }
            Streams streams;
            if (!read_streams(r, limits_, size_, &streams, why)) return false;
            if (streams.folders.empty()) { *why = "the 7z archive is damaged (encoded header without a folder)"; return false; }
            Folder &folder = streams.folders[0];
            if (folder.unpack_size > limits.max_header) { *why = "the 7z archive has a header larger than the core reads"; return false; }
            std::vector<uint8_t> decoded;
            if (!decode_folder(folder, &decoded, why)) return false;
            header.swap(decoded);
        }
        *why = "the 7z archive is damaged (headers nested too deep)";
        return false;
    }

    const std::vector<Entry> &entries() const { return entries_; }

    // The bytes of entry `index` (a file), checked against its CRC. A solid folder is decoded once and kept for the entries that follow.
    bool extract(size_t index, std::vector<uint8_t> *out, std::string *why)
    {
        using namespace detail;
        out->clear();
        if (index >= entries_.size() || entries_[index].is_dir) { *why = "the 7z entry is not a file"; return false; }
        const Entry &entry = entries_[index];
        if (entry.has_stream) {
            if (cached_ != entry.folder) {
                cache_.clear();
                cached_ = SIZE_MAX;
                if (!decode_folder(folders_[entry.folder], &cache_, why)) return false;
                cached_ = entry.folder;
            }
            if (entry.offset > cache_.size() || entry.size > cache_.size() - entry.offset) { *why = "the 7z archive is damaged (a file outside its folder)"; return false; }
            out->assign(cache_.begin() + std::ptrdiff_t(entry.offset), cache_.begin() + std::ptrdiff_t(entry.offset + entry.size));
        }
        if (entry.has_crc && crc32_update(0, out->data(), out->size()) != entry.crc) { *why = "the 7z entry is corrupt (CRC mismatch)"; return false; }
        return true;
    }

private:
    bool read_header(detail::Reader &r, std::string *why)
    {
        using namespace detail;
        auto damaged = [&](const char *what) {
            *why = std::string("the 7z archive is damaged (") + what + ")";
            return false;
        };
        Streams streams;
        bool have_files = false;
        for (;;) {
            const uint64_t id = r.number();
            if (!r.ok()) return damaged("the header is cut short");
            if (id == kEnd) break;
            if (id == kArchiveProperties) {
                for (;;) {
                    const uint64_t type = r.number();
                    if (!r.ok()) return damaged("bad archive properties");
                    if (type == kEnd) break;
                    r.skip(r.number());
                    if (!r.ok()) return damaged("bad archive properties");
                }
            } else if (id == kAdditionalStreamsInfo) {
                Streams ignored;
                if (!read_streams(r, limits_, size_, &ignored, why)) return false;
            } else if (id == kMainStreamsInfo) {
                if (!read_streams(r, limits_, size_, &streams, why)) return false;
            } else if (id == kFilesInfo) {
                if (!read_files(r, streams, why)) return false;
                have_files = true;
            } else {
                return damaged("unknown header property");
            }
        }
        if (!have_files && !streams.substreams.empty()) return damaged("streams without files");
        folders_ = std::move(streams.folders);
        return true;
    }

    bool read_files(detail::Reader &r, const detail::Streams &streams, std::string *why)
    {
        using namespace detail;
        auto damaged = [&](const char *what) {
            *why = std::string("the 7z archive is damaged (") + what + ")";
            return false;
        };
        const uint64_t count = r.number();
        if (!r.ok()) return damaged("bad file count");
        if (count > limits_.max_entries) {
            *why = "the 7z archive holds more than " + std::to_string(limits_.max_entries) + " files and directories";
            return false;
        }
        std::vector<bool> empty_stream(size_t(count), false), empty_file, anti;
        std::vector<std::string> names;
        size_t empty_streams = 0;
        for (;;) {
            const uint64_t type = r.number();
            if (!r.ok()) return damaged("bad file properties");
            if (type == kEnd) break;
            const uint64_t size = r.number();
            if (!r.ok() || size > r.left()) return damaged("a file property is out of range");
            const uint8_t *body = r.take(size_t(size));
            if (!body) return damaged("a file property is out of range");
            Reader p(body, size_t(size));
            if (type == kEmptyStream) {
                if (!read_bits(p, size_t(count), &empty_stream)) return damaged("bad empty stream flags");
                empty_streams = 0;
                for (bool b : empty_stream) empty_streams += b;
            } else if (type == kEmptyFile) {
                if (!read_bits(p, empty_streams, &empty_file)) return damaged("bad empty file flags");
            } else if (type == kAnti) {
                if (!read_bits(p, empty_streams, &anti)) return damaged("bad anti flags");
            } else if (type == kNames) {
                if (p.u8() != 0) return damaged("external names are not read");
                const size_t bytes = p.left();
                const uint8_t *text = p.take(bytes);
                if (!text || bytes % 2) return damaged("bad names");
                size_t start = 0;
                const size_t units = bytes / 2;
                for (size_t i = 0; i < units; ++i) {
                    if (text[2 * i] || text[2 * i + 1]) continue;
                    std::string name;
                    if (!utf8_from_utf16(text + 2 * start, i - start, &name)) return damaged("a name is not valid UTF-16");
                    if (names.size() >= count) return damaged("more names than files");
                    names.push_back(std::move(name));
                    start = i + 1;
                }
                if (start != units || names.size() != count) return damaged("the names do not match the files");
            }
            // Everything else (times, attributes, start positions, padding) is not looked at.
        }
        if (names.size() != count) return damaged("the files have no names");
        if (empty_file.empty()) empty_file.assign(empty_streams, false);
        if (anti.empty()) anti.assign(empty_streams, false);
        if (empty_file.size() != empty_streams || anti.size() != empty_streams) return damaged("the empty file flags do not match the empty streams");
        size_t stream = 0, empty_index = 0, substream_in_folder = 0, folder_index = 0;
        uint64_t offset = 0;
        entries_.reserve(size_t(count));
        for (size_t i = 0; i < size_t(count); ++i) {
            Entry entry;
            entry.name = std::move(names[i]);
            if (empty_stream[i]) {
                entry.is_dir = !empty_file[empty_index];
                if (anti[empty_index]) entry.is_dir = false, entry.name.clear();           // an anti item deletes a file in an update: it is nothing here
                ++empty_index;
            } else {
                while (folder_index < streams.folders.size() && substream_in_folder >= streams.folders[folder_index].substreams) {
                    ++folder_index;
                    substream_in_folder = 0;
                    offset = 0;
                }
                if (folder_index >= streams.folders.size() || stream >= streams.substreams.size()) return damaged("more files than streams");
                const Substream &sub = streams.substreams[stream++];
                entry.has_stream = true;
                entry.size = sub.size;
                entry.has_crc = sub.has_crc;
                entry.crc = sub.crc;
                entry.folder = folder_index;
                entry.offset = offset;
                offset += sub.size;
                ++substream_in_folder;
            }
            entries_.push_back(std::move(entry));
        }
        if (stream != streams.substreams.size()) return damaged("more streams than files");
        return true;
    }

    // The folder's bytes: its one coder over its one pack stream.
    bool decode_folder(const detail::Folder &folder, std::vector<uint8_t> *out, std::string *why) const
    {
        using namespace detail;
        if (folder.coders.size() != 1 || folder.coders[0].in_streams != 1 || folder.coders[0].out_streams != 1 || folder.packed_streams != 1) {
            std::string chain;
            for (const Coder &coder : folder.coders) chain += (chain.empty() ? "" : " + ") + method_text(coder.method);
            *why = "the 7z archive uses a chain of coders (" + chain + "); only a single Copy, LZMA or LZMA2 coder is read";
            return false;
        }
        const Coder &coder = folder.coders[0];
        if (folder.pack_offset > size_ || folder.pack_size > size_ - folder.pack_offset) { *why = "the 7z archive is truncated (packed data out of range)"; return false; }
        if (folder.unpack_size > limits_.max_unpack) { *why = "the 7z archive unpacks to more than the core reads"; return false; }
        const uint8_t *pack = data_ + folder.pack_offset;
        const size_t pack_size = size_t(folder.pack_size), unpack_size = size_t(folder.unpack_size);
        try {
            out->assign(unpack_size, 0);
        } catch (const std::bad_alloc &) {
            out->clear();
            *why = "the 7z archive needs more memory than is available";
            return false;
        }
        bool ok;
        if (coder.method == 0x00) {
            ok = pack_size == unpack_size;
            if (ok) memcpy(out->data(), pack, unpack_size);
            else *why = "the 7z archive is damaged (a stored folder has two sizes)";
        } else if (coder.method == 0x030101) {
            ok = decode_lzma(coder.props, pack, pack_size, out->data(), unpack_size, why);
        } else if (coder.method == 0x21) {
            ok = decode_lzma2(coder.props, pack, pack_size, out->data(), unpack_size, why);
        } else {
            *why = "the 7z archive uses " + method_text(coder.method) + "; only Copy, LZMA and LZMA2 are read";
            ok = false;
        }
        if (!ok) { out->clear(); return false; }
        if (folder.has_crc && crc32_update(0, out->data(), out->size()) != folder.crc) { *why = "the 7z archive is corrupt (CRC mismatch)"; out->clear(); return false; }
        return true;
    }

    const uint8_t *data_ = nullptr;
    size_t size_ = 0;
    Limits limits_;
    std::vector<Entry> entries_;
    std::vector<detail::Folder> folders_;
    std::vector<uint8_t> cache_;
    size_t cached_ = SIZE_MAX;
};

inline bool looks_like_sevenzip(const uint8_t *data, size_t size) { return size >= 6 && memcmp(data, "7z\xBC\xAF\x27\x1C", 6) == 0; }

}  // namespace sevenzip
}  // namespace p6_libretro

#endif
