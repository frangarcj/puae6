// Differential test of the DMS unpackers of adapters/libretro/dms.h against the xDMS v1.3 sources that WinUAE ships (archivers/dms, public domain),
// which are the reference for the format and for what real archives contain. There is no DMS encoder in the tree and no archive of a game may be
// committed, so the streams are random: Quick, Medium and Deep decode ANY bit sequence to some bytes, and for Heavy the code tables (the part that
// needs a valid prefix code) are generated here and followed by random bits. Both decoders get the same stream and the same history, track after track
// (the dictionaries carry over unless a track resets them, like a real archive), and must agree on whether the stream is accepted and on every byte of
// what it decodes to. A decoder that diverged from xDMS on any of the five modes, or on the carried state, shows here within a few hundred streams.
//   dms_differential SEED N     N random streams per mode; prints the number accepted and refused per mode
// Built by tests/media/test_container.py with the xDMS unpackers (not pfile.cpp, which is tied to zfile) and the sanitizers.
#include "dms.h"

#include "cdata.h"
#include "u_init.h"
#include "u_rle.h"
#include "u_quick.h"
#include "u_medium.h"
#include "u_deep.h"
#include "u_heavy.h"

#include <stdio.h>
#include <stdlib.h>
#include <vector>

UCHAR *dms_text;        // pfile.cpp owns this in WinUAE; the unpackers share it

namespace {

struct Random {
    uint64_t state;
    explicit Random(uint64_t seed) : state(seed * 2685821657736338717ull + 1442695040888963407ull) {}
    uint64_t next()
    {
        state ^= state >> 12; state ^= state << 25; state ^= state >> 27;
        return state * 2685821657736338717ull;
    }
    uint32_t below(uint32_t n) { return n ? uint32_t(next() % n) : 0; }
    bool coin() { return next() & 1; }
};

struct BitWriter {
    std::vector<uint8_t> bytes;
    unsigned used = 0;                         // bits used in the last byte
    void put(unsigned value, unsigned count)
    {
        for (int bit = int(count) - 1; bit >= 0; --bit) {
            if (!used) bytes.push_back(0);
            if ((value >> bit) & 1) bytes.back() |= uint8_t(0x80 >> used);
            used = (used + 1) & 7;
        }
    }
};

// Code lengths of a random complete prefix code over `alphabet` symbols, `used` of them with a code, none longer than `max_length`.
std::vector<unsigned> random_lengths(Random &rng, unsigned alphabet, unsigned used, unsigned max_length)
{
    std::vector<unsigned> leaves = {0};
    while (leaves.size() < used) {
        const unsigned pick = rng.below(unsigned(leaves.size()));
        if (leaves[pick] >= max_length) {
            bool any = false;
            for (unsigned &depth : leaves) if (depth < max_length) { any = true; break; }
            if (!any) break;
            continue;
        }
        const unsigned depth = leaves[pick] + 1;
        leaves[pick] = depth;
        leaves.push_back(depth);
    }
    std::vector<unsigned> lengths(alphabet, 0);
    std::vector<unsigned> slots(alphabet);
    for (unsigned i = 0; i < alphabet; ++i) slots[i] = i;
    for (size_t i = 0; i < leaves.size() && i < alphabet; ++i) {
        const unsigned at = i + rng.below(alphabet - unsigned(i));
        std::swap(slots[i], slots[at]);
        lengths[slots[i]] = leaves[i];
    }
    return lengths;
}

struct Track {
    std::vector<uint8_t> bytes;
    unsigned size, flags;
};

struct Stream {
    std::vector<uint8_t> bytes;
    unsigned flags;                            // the track flags for Heavy
};

Stream make_stream(Random &rng, unsigned mode, bool with_tables, unsigned np)
{
    BitWriter w;
    Stream s;
    s.flags = 0;
    if (mode >= 5 && with_tables) {
        s.flags |= 2;
        if (rng.below(8) == 0) {                                  // a single symbol: n = 0, then the symbol, which takes no bits (a value past the alphabet walks a tree that has no such node)
            w.put(0, 9);
            w.put(rng.below(510), 9);
        } else {
            const std::vector<unsigned> c = random_lengths(rng, 510, 2 + rng.below(rng.coin() ? 40 : 509), 16);
            unsigned n = 510;
            while (n > 1 && !c[n - 1]) --n;
            w.put(n, 9);
            for (unsigned i = 0; i < n; ++i) w.put(c[i], 5);
        }
        if (rng.below(8) == 0) {
            w.put(0, 5);
            w.put(rng.below(np), 5);
        } else {
            const std::vector<unsigned> p = random_lengths(rng, np, 2 + rng.below(np - 1), 15);
            unsigned n = np;
            while (n > 1 && !p[n - 1]) --n;
            w.put(n, 5);
            for (unsigned i = 0; i < n; ++i) w.put(p[i], 4);
        }
    }
    const unsigned length = rng.coin() ? 1 + rng.below(64) : 1 + rng.below(3000);
    for (unsigned i = 0; i < length; ++i) w.put(unsigned(rng.next() & 0xFF), 8);
    s.bytes = w.bytes;
    return s;
}

bool run_mine(p6_libretro::dms_detail::Decoder &d, unsigned mode, const std::vector<uint8_t> &bytes, uint8_t *out, unsigned size, unsigned flags)
{
    switch (mode) {
    case 2: return d.quick(bytes.data(), bytes.size(), out, size);
    case 3: return d.medium(bytes.data(), bytes.size(), out, size);
    case 4: return d.deep(bytes.data(), bytes.size(), out, size);
    default: return d.heavy(bytes.data(), bytes.size(), out, size, flags);
    }
}

[[noreturn]] void bug(const char *what, uint64_t iteration, unsigned mode, unsigned track)
{
    fprintf(stderr, "DIFFERENTIAL FAILURE at stream %llu (mode %u, track %u): %s\n", (unsigned long long)iteration, mode, track, what);
    exit(1);
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc != 3) { fprintf(stderr, "usage: dms_differential SEED N\n"); return 2; }
    Random rng(strtoull(argv[1], nullptr, 10));
    const uint64_t iterations = strtoull(argv[2], nullptr, 10);
    static const char *const kModes[] = {"", "", "quick", "medium", "deep", "heavy1", "heavy2"};
    uint64_t accepted[7] = {}, refused[7] = {};
    constexpr size_t kPad = 100000, kOut = 32000 + 1024;
    static UCHAR reference_text[32000];
    dms_text = reference_text;
    for (uint64_t i = 0; i < iterations; ++i) {
        const unsigned mode = 2 + rng.below(5);
        p6_libretro::dms_detail::Decoder mine;
        memset(reference_text, 0, sizeof reference_text);       // Init_Decrunchers clears only the first 0x3fc8 bytes: the rest must start as the rewrite's, zero
        Init_Decrunchers();
        // A run of tracks, as an archive has: the dictionaries carry over from one to the next unless the track resets them.
        const unsigned tracks = 1 + rng.below(4);
        std::vector<Track> history;
        for (unsigned t = 0; t < tracks; ++t) {
            const unsigned np = (mode == 6) ? 15 : 14;
            Stream stream = make_stream(rng, mode, t == 0 || rng.coin(), np);
            const unsigned size = 1 + rng.below(rng.coin() ? 600 : 11264);
            const unsigned flags = mode == 5 ? stream.flags & 7 : mode == 6 ? stream.flags | 8 : 0;
            if (rng.below(4)) {
                // Make the declared length the one the stream really has (three quarters of the time): replay the tracks before this one on a fresh
                // decoder, decode this one, and cut the stream where the bit reader says it ends. The rest decode as zeros, as they would in the file.
                p6_libretro::dms_detail::Decoder probe;
                std::vector<uint8_t> scratch(kOut);
                for (const Track &before : history) run_mine(probe, mode, before.bytes, scratch.data(), before.size, before.flags);
                run_mine(probe, mode, stream.bytes, scratch.data(), size, flags);
                if (probe.stream_end() >= 2) stream.bytes.resize(probe.stream_end() - rng.below(3));
            }
            history.push_back({stream.bytes, size, flags});
            std::vector<uint8_t> padded(stream.bytes);
            padded.resize(stream.bytes.size() + kPad, 0);
            std::vector<uint8_t> theirs_out(kOut, 0), mine_out(kOut, 0);
            bool theirs = false, ours = false;
            switch (mode) {
            case 2: theirs = Unpack_QUICK(padded.data(), theirs_out.data(), uint16_t(stream.bytes.size()), uint16_t(size)) == 0; break;
            case 3: theirs = Unpack_MEDIUM(padded.data(), theirs_out.data(), uint16_t(stream.bytes.size()), uint16_t(size)) == 0; break;
            case 4: theirs = Unpack_DEEP(padded.data(), theirs_out.data(), uint16_t(stream.bytes.size()), uint16_t(size)) == 0; break;
            default: theirs = Unpack_HEAVY(padded.data(), theirs_out.data(), uint8_t(flags), uint16_t(stream.bytes.size()), uint16_t(size)) == 0; break;
            }
            ours = run_mine(mine, mode, stream.bytes, mine_out.data(), size, flags);
            if (theirs != ours) bug(theirs ? "xDMS accepts a stream the rewrite refuses" : "the rewrite accepts a stream xDMS refuses", i, mode, t);
            if (theirs) {
                if (memcmp(theirs_out.data(), mine_out.data(), size) != 0) {
                    unsigned at = 0;
                    while (theirs_out[at] == mine_out[at]) ++at;
                    fprintf(stderr, "first difference at %u of %u (stream %zu bytes): xDMS %02x rewrite %02x\n", at, size, stream.bytes.size(), theirs_out[at], mine_out[at]);
                    bug("the unpacked bytes differ", i, mode, t);
                }
                ++accepted[mode];
            } else {
                ++refused[mode];
                break;                                                   // a failed track ends the archive: the state after it is not compared
            }
            if (rng.coin()) { Init_Decrunchers(); mine.reset(); history.clear(); }     // a track without "keep the dictionary"
        }
        // The RLE stage as well: the same random input and output size.
        if (mode == 2) {
            std::vector<uint8_t> bytes(1 + rng.below(400));
            for (uint8_t &b : bytes) b = rng.below(4) == 0 ? 0x90 : uint8_t(rng.below(5) == 0 ? rng.next() : 0);
            const unsigned size = 1 + rng.below(2000);
            std::vector<uint8_t> padded(bytes);
            padded.resize(bytes.size() + 16000, 0);      // xDMS reads past the end of a short stream
            std::vector<uint8_t> a(kOut, 0), b(kOut, 0);
            const bool theirs = Unpack_RLE(padded.data(), a.data(), uint16_t(bytes.size()), uint16_t(size)) == 0;
            const bool ours = mine.rle(bytes.data(), bytes.size(), b.data(), size);
            // xDMS reads the stream's last bytes unchecked (zeros here), so it may accept a cut-off run that the rewrite refuses; the reverse is a bug.
            if (ours && !theirs) bug("the rewrite accepts an RLE stream xDMS refuses", i, 1, 0);
            if (ours && memcmp(a.data(), b.data(), size) != 0) bug("the RLE output differs", i, 1, 0);
        }
    }
    for (unsigned mode = 2; mode <= 6; ++mode) printf("dms mode %s: accepted %llu refused %llu\n", kModes[mode], (unsigned long long)accepted[mode], (unsigned long long)refused[mode]);
    return 0;
}
