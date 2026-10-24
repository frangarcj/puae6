// The IPF reader of ports/portable/media/ipf_track.h on its own (tests/media/test_ipf.py, tests/fuzz/test_ipf.py), built with the sanitizers and no engine.
//   usage: ipf_probe FILE [REVOLUTIONS]       one image, every track and revolution printed
//          ipf_probe --bundle BUNDLE [REVS]   BUNDLE is a file of images, each a little-endian u32 length and its bytes; one summary line per image
// Each image is copied into a heap block of exactly its size, so that a read past its end is an ASan report and not a wrong answer. For one image: "ok" or "refused: <why>", then
// for every track the image describes and every revolution: "track N rev R cells C splice S weak W words HEX timing HEX" (or "track N rev R failed"), the hex of the 16-bit words
// and of the timing. In a bundle each image is one line: "refused: <why>" or "ok failed F digest H": the number of tracks that do not decode and a hash of everything decoded.
#include "ipf_track.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

struct Hash {
    uint64_t value = 1469598103934665603ull;
    void add(uint64_t x) { value = (value ^ x) * 1099511628211ull; }
};

int one(const uint8_t *data, uint64_t size, unsigned revolutions, bool verbose)
{
    const char *why = nullptr;
    p6_ipf::Image *image = p6_ipf::open_memory(data, size, &why);
    if (!image) {
        printf("refused: %s\n", why ? why : "?");
        return 0;
    }
    unsigned bad_track = 0, failed = 0;
    const bool checked = p6_ipf::check_tracks(image, &bad_track) == nullptr;
    std::vector<uint16_t> words(p6_ipf::kWordCapacity), timing(p6_ipf::kTimingCapacity);
    Hash hash;
    if (verbose) printf(checked ? "ok\n" : "ok (track %u cannot be decoded)\n", bad_track);
    for (unsigned track = 0; track < p6_ipf::kTracks; ++track) {
        if (!p6_ipf::has_track(image, track)) continue;
        for (unsigned rev = 0; rev < revolutions; ++rev) {
            p6_ipf::Track out = {};
            const int r = p6_ipf::decode(image, track, rev, words.data(), words.size(), timing.data(), timing.size(), &out);
            hash.add(track * 8 + rev);
            hash.add(uint64_t(r + 2));
            if (r == p6_ipf::kFailed) { ++failed; if (verbose) printf("track %u rev %u failed\n", track, rev); continue; }
            if (r == p6_ipf::kAbsent) { if (verbose) printf("track %u rev %u absent\n", track, rev); continue; }
            hash.add(out.cells); hash.add(out.splice); hash.add(out.weak);
            for (uint32_t i = 0; i < (out.cells + 15) / 16; ++i) hash.add(words[i]);
            for (uint32_t i = 0; i < (out.cells + 7) / 8 + 2; ++i) hash.add(timing[i]);
            if (!verbose) continue;
            printf("track %u rev %u cells %u splice %u weak %d words ", track, rev, out.cells, out.splice, out.weak ? 1 : 0);
            for (uint32_t i = 0; i < (out.cells + 15) / 16; ++i) printf("%04x", words[i]);
            printf(" timing ");
            for (uint32_t i = 0; i < (out.cells + 7) / 8 + 2; ++i) printf("%04x", timing[i]);
            printf("\n");
        }
    }
    p6_ipf::close(image);
    if (!verbose) printf("ok failed %u digest %016llx\n", failed, static_cast<unsigned long long>(hash.value));
    return 0;
}

}  // namespace

int main(int argc, char **argv)
{
    const bool bundle = argc >= 3 && !strcmp(argv[1], "--bundle");
    if (argc < 2 || argc > 4 || (!bundle && argc > 3)) { fprintf(stderr, "usage: %s FILE [REVOLUTIONS] | --bundle BUNDLE [REVOLUTIONS]\n", argv[0]); return 64; }
    const char *path = bundle ? argv[2] : argv[1];
    const unsigned revolutions = bundle ? (argc == 4 ? unsigned(atoi(argv[3])) : 2) : (argc == 3 ? unsigned(atoi(argv[2])) : 1);
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return 2; }
    if (!bundle) {
        fseek(f, 0, SEEK_END);
        const long size = ftell(f);
        fseek(f, 0, SEEK_SET);
        uint8_t *exact = static_cast<uint8_t *>(malloc(size > 0 ? size_t(size) : 1));
        if (size > 0 && fread(exact, 1, size_t(size), f) != size_t(size)) { fprintf(stderr, "short read\n"); return 2; }
        fclose(f);
        one(exact, uint64_t(size), revolutions, true);
        free(exact);
        return 0;
    }
    for (;;) {
        uint8_t head[4];
        if (fread(head, 1, 4, f) != 4) break;
        const uint32_t size = uint32_t(head[0]) | uint32_t(head[1]) << 8 | uint32_t(head[2]) << 16 | uint32_t(head[3]) << 24;
        uint8_t *exact = static_cast<uint8_t *>(malloc(size ? size : 1));
        if (size && fread(exact, 1, size, f) != size) { fprintf(stderr, "the bundle is cut\n"); return 2; }
        one(exact, size, revolutions, false);
        free(exact);
    }
    fclose(f);
    return 0;
}
