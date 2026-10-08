// T17d probe for the disk containers of the libretro adapter (adapters/libretro/container.h): it links no engine and no bridge, only the adapter's
// header-only decoders, and tests/media/test_container.py builds it with ASan and UBSan. Like tests/fuzz/fuzz_content.cpp it has no libFuzzer: a fixed
// xorshift generator, so the same seed and iteration count is the same run and a fixed bug is a seed.
//   container_probe decode FILE                  decode_disk on a file: "ok SIZE\n" and the ADF bytes, or "refused: WHY\n"
//   container_probe fuzz SEED N ADF SEED_FILE... N hostile variants of the seed files (truncated, flipped, spliced, with lying sizes, and DMS tracks
//                                                resealed so that the unpackers see garbage); a variant that decodes must be exactly ADF
// The output of fuzz is a histogram per kind of mutation and per outcome; nothing else varies between runs.
#include "container.h"

#include <stdio.h>
#include <stdlib.h>
#include <map>
#include <string>
#include <vector>

namespace {

using namespace p6_libretro;

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

[[noreturn]] void bug(const char *what, uint64_t iteration, const std::string &detail = std::string())
{
    fprintf(stderr, "FUZZ FAILURE at iteration %llu: %s%s%s\n", (unsigned long long)iteration, what, detail.empty() ? "" : ": ", detail.c_str());
    exit(1);
}

bool read_file(const char *path, std::vector<uint8_t> *out)
{
    FILE *file = fopen(path, "rb");
    if (!file) return false;
    uint8_t chunk[65536];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, file)) > 0) {
        out->insert(out->end(), chunk, chunk + got);
        if (out->size() > 64u * 1024 * 1024) break;                  // a bomb fixture is small; this only protects the probe itself
    }
    fclose(file);
    return true;
}

int decode_main(const char *path)
{
    std::vector<uint8_t> bytes;
    if (!read_file(path, &bytes)) { fprintf(stderr, "cannot read %s\n", path); return 2; }
    std::string why;
    if (!decode_disk(&bytes, path, &why)) {
        printf("refused: %s\n", why.c_str());
        return 0;
    }
    printf("ok %zu\n", bytes.size());
    fflush(stdout);
    fwrite(bytes.data(), 1, bytes.size(), stdout);
    return 0;
}

// --- DMS resealing ------------------------------------------------------------------------------------------------------------------------------

void put16(uint8_t *p, unsigned v) { p[0] = uint8_t(v >> 8); p[1] = uint8_t(v); }

// Recomputes the CRCs of a DMS file as far as it still parses, so that a mutation reaches the unpackers instead of the CRC checks.
void reseal_dms(std::vector<uint8_t> *file)
{
    using namespace dms_detail;
    if (file->size() < kHeaderBytes || !looks_like_dms(file->data(), file->size())) return;
    put16(file->data() + kHeaderBytes - 2, crc16(file->data() + 4, kHeaderBytes - 6));
    size_t pos = kHeaderBytes;
    while (file->size() - pos >= kTrackHeaderBytes && !memcmp(file->data() + pos, "TR", 2)) {
        uint8_t *header = file->data() + pos;
        const size_t packed = be16(header + 6);
        if (file->size() - pos - kTrackHeaderBytes < packed) { put16(header + 18, crc16(header, kTrackHeaderBytes - 2)); return; }
        put16(header + 16, crc16(header + kTrackHeaderBytes, packed));
        put16(header + 18, crc16(header, kTrackHeaderBytes - 2));
        pos += kTrackHeaderBytes + packed;
    }
}

// Offsets of the track headers of a well-formed DMS seed.
std::vector<size_t> dms_tracks(const std::vector<uint8_t> &file)
{
    std::vector<size_t> tracks;
    if (file.size() < dms_detail::kHeaderBytes || !looks_like_dms(file.data(), file.size())) return tracks;
    size_t pos = dms_detail::kHeaderBytes;
    while (file.size() - pos >= dms_detail::kTrackHeaderBytes && !memcmp(file.data() + pos, "TR", 2)) {
        tracks.push_back(pos);
        const size_t packed = dms_detail::be16(file.data() + pos + 6);
        if (file.size() - pos - dms_detail::kTrackHeaderBytes < packed) break;
        pos += dms_detail::kTrackHeaderBytes + packed;
    }
    return tracks;
}

// --- mutations ----------------------------------------------------------------------------------------------------------------------------------

const char *const kKinds[] = {"none", "truncate", "extend", "flip", "zero-run", "extreme-byte", "huge-field", "splice", "random-bytes", "dms-data-resealed",
                              "dms-track-resealed", "zip-field", "header-flip"};

std::string mutate(Random &rng, const std::vector<std::vector<uint8_t>> &seeds, std::vector<uint8_t> *file)
{
    const std::vector<uint8_t> &seed = seeds[rng.below(uint32_t(seeds.size()))];
    *file = seed;
    std::string kind = kKinds[rng.below(sizeof kKinds / sizeof *kKinds)];
    auto at = [&] { return rng.below(uint32_t(file->size())); };
    if (file->empty()) return kind;
    if (kind == "truncate") {
        file->resize(rng.coin() ? at() : file->size() - 1 - rng.below(uint32_t(std::min<size_t>(file->size(), 64))));
    } else if (kind == "extend") {
        for (uint32_t i = 1 + rng.below(rng.coin() ? 8 : 4096); i; --i) file->push_back(uint8_t(rng.next()));
    } else if (kind == "flip") {
        for (uint32_t i = 1 + rng.below(8); i; --i) (*file)[at()] ^= uint8_t(1u << rng.below(8));
    } else if (kind == "header-flip") {
        for (uint32_t i = 1 + rng.below(4); i; --i) (*file)[rng.below(uint32_t(std::min<size_t>(file->size(), 64)))] ^= uint8_t(1u << rng.below(8));
    } else if (kind == "zero-run") {
        const size_t start = at();
        const size_t length = std::min<size_t>(file->size() - start, 1 + rng.below(rng.coin() ? 16 : 4096));
        memset(file->data() + start, rng.coin() ? 0 : 0xFF, length);
    } else if (kind == "extreme-byte") {
        static const uint8_t extremes[] = {0x00, 0x01, 0x7F, 0x80, 0xFF, 0x90, 'P', 'K', 0x1F, 0x8B};
        (*file)[at()] = extremes[rng.below(sizeof extremes)];
    } else if (kind == "huge-field") {
        // A 2 or 4 byte number at some position set to the largest or a boundary value: lengths, counts and offsets of every container.
        static const uint32_t values[] = {0xFFFFFFFFu, 0x7FFFFFFFu, 0x80000000u, 0x00FFFFFFu, 0x0000FFFFu, 0x00010000u, 0x000DC000u, 0x000DC001u, 0};
        const uint32_t value = values[rng.below(sizeof values / sizeof *values)];
        const size_t start = at();
        const bool big = rng.coin();
        for (unsigned i = 0; i < 4 && start + i < file->size(); ++i) (*file)[start + i] = uint8_t(big ? value >> (24 - 8 * i) : value >> (8 * i));
    } else if (kind == "splice") {
        const std::vector<uint8_t> &other = seeds[rng.below(uint32_t(seeds.size()))];
        const size_t cut = at(), from = rng.below(uint32_t(other.size()));
        file->resize(cut);
        file->insert(file->end(), other.begin() + std::ptrdiff_t(from), other.end());
    } else if (kind == "random-bytes") {
        const size_t length = 1 + rng.below(4096);
        file->assign(length, 0);
        for (uint8_t &byte : *file) byte = uint8_t(rng.next());
        if (rng.coin()) memcpy(file->data(), seed.data(), std::min<size_t>(std::min(seed.size(), length), 1 + rng.below(40)));     // keep the magic and a bit of the header
    } else if (kind == "zip-field") {
        // The sizes, the offset and the name length of the central directory entry and the end record of a zip.
        size_t entry = std::string::npos;
        for (size_t i = 0; i + 4 <= file->size(); ++i)
            if (!memcmp(file->data() + i, "PK\1\2", 4)) { entry = i; break; }
        if (entry != std::string::npos && entry + 46 <= file->size()) {
            static const size_t fields[] = {16, 20, 24, 28, 30, 32, 42};
            const size_t field = entry + fields[rng.below(sizeof fields / sizeof *fields)];
            static const uint32_t values[] = {0xFFFFFFFFu, 0x7FFFFFFFu, 0x00200000u, 0x000DC000u, 0x000DC001u, 0, 1, 0xFFFFu};
            const uint32_t value = values[rng.below(sizeof values / sizeof *values)];
            for (unsigned i = 0; i < 4 && field + i < file->size(); ++i) (*file)[field + i] = uint8_t(value >> (8 * i));
        }
    } else if (kind == "dms-data-resealed" || kind == "dms-track-resealed") {
        const std::vector<size_t> tracks = dms_tracks(*file);
        if (!tracks.empty()) {
            const size_t header = tracks[rng.below(uint32_t(tracks.size()))];
            const size_t packed = dms_detail::be16(file->data() + header + 6);
            uint8_t *data = file->data() + header + dms_detail::kTrackHeaderBytes;
            if (kind == "dms-data-resealed") {
                for (uint32_t i = 1 + rng.below(16); i && packed; --i) data[rng.below(uint32_t(packed))] = uint8_t(rng.next());
                if (rng.coin() && packed) for (size_t i = 0; i < packed; ++i) data[i] = uint8_t(rng.next());      // pure garbage for the unpacker
            }
            // Mode, flags and lengths: a track of a stored seed re-labelled as any compression mode, with plausible and implausible lengths.
            uint8_t *h = file->data() + header;
            if (kind == "dms-track-resealed" || rng.coin()) {
                h[13] = uint8_t(rng.below(9));                                       // modes 0 to 8: 7 and 8 do not exist
                h[12] = uint8_t(rng.next());                                         // flags: bit 0 keeps the dictionary, 1 sends the tables, 2 adds RLE
                if (rng.coin()) put16(h + 8, rng.coin() ? 11264 : rng.below(65536));            // the length after the first unpacking
                if (rng.coin()) put16(h + 10, rng.coin() ? 11264 : rng.below(65536));         // and after the RLE
                if (rng.below(4) == 0) put16(h + 14, uint16_t(rng.next()));          // the checksum, so that it does not always mismatch
            }
        }
        reseal_dms(file);
    }
    return kind;
}

int fuzz_main(uint64_t seed, uint64_t iterations, const char *adf_path, char **seed_paths, int seed_count)
{
    std::vector<uint8_t> adf;
    if (!read_file(adf_path, &adf) || adf.size() != kDiskBytes) { fprintf(stderr, "bad ADF %s\n", adf_path); return 2; }
    std::vector<std::vector<uint8_t>> seeds;
    for (int i = 0; i < seed_count; ++i) {
        seeds.emplace_back();
        if (!read_file(seed_paths[i], &seeds.back())) { fprintf(stderr, "cannot read %s\n", seed_paths[i]); return 2; }
        std::string why;
        std::vector<uint8_t> copy = seeds.back();
        if (!decode_disk(&copy, seed_paths[i], &why)) bug("a seed does not decode", 0, std::string(seed_paths[i]) + ": " + why);
        if (copy != adf) bug("a seed decodes to another disk than the one given", 0, seed_paths[i]);
    }
    if (seeds.empty()) { fprintf(stderr, "no seeds\n"); return 2; }
    Random rng(seed);
    std::map<std::string, std::pair<uint64_t, uint64_t>> by_kind;
    std::map<std::string, uint64_t> reasons;
    for (uint64_t i = 0; i < iterations; ++i) {
        std::vector<uint8_t> file;
        const std::string kind = mutate(rng, seeds, &file);
        std::vector<uint8_t> bytes = file;
        std::string why;
        const bool accepted = decode_disk(&bytes, "mutant.bin", &why);
        if (accepted) {
            if (bytes.size() != kDiskBytes) bug("accepted a disk of the wrong size", i, kind);
            if (bytes != adf) bug("accepted a different disk (the checks did not see the damage)", i, kind);
            ++by_kind[kind].first;
        } else {
            if (why.empty()) bug("refused without a reason", i, kind);
            if (why.size() > 400) bug("a reason that is not a sentence", i, kind);
            ++by_kind[kind].second;
            std::string head = why.substr(0, why.find_first_of("0123456789"));
            ++reasons[head];
        }
    }
    for (const auto &entry : by_kind) printf("container kind %s: accepted %llu refused %llu\n", entry.first.c_str(), (unsigned long long)entry.second.first, (unsigned long long)entry.second.second);
    for (const auto &entry : reasons) printf("container refusal \"%s\": %llu\n", entry.first.c_str(), (unsigned long long)entry.second);
    return 0;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "decode")) return decode_main(argv[2]);
    if (argc >= 6 && !strcmp(argv[1], "fuzz")) return fuzz_main(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10), argv[4], argv + 5, argc - 5);
    fprintf(stderr, "usage: container_probe decode FILE | fuzz SEED N ADF SEED_FILE...\n");
    return 2;
}
