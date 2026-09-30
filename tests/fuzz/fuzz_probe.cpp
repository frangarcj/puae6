// Deterministic mutation fuzzing of the surfaces that take outside data: the p6_config struct, firmware images
// and the logical-name store. No libFuzzer: a fixed-seed xorshift generator mutates valid inputs and every run of
// the same seed and iteration count is the same run, so a failure is a reproducer and a fixed bug is a seed.
//   fuzz_probe config SEED N             p6_init on mutated p6_config structs, against an independent oracle
//   fuzz_probe firmware ROM SEED N       p6_load on mutated images; accepted ones run two units and unload
//   fuzz_probe blob SEED N               random operations on the blob store
// Output is a histogram of result codes; nothing else varies between runs.
#include "p6.h"
#include "blob_io.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <vector>

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

const uint64_t kBoundary[] = {0, 1, 2, 3, 255, 256, 44100, 48000, 65535, 65536, 0x7fffffffull, 0x80000000ull,
                              0xffffffffull, 0x100000000ull, 0x7fffffffffffffffull, 0x8000000000000000ull,
                              0xffffffffffffffffull};

p6_error fresh_error()
{
    p6_error e{};
    e.abi_version = P6_ABI_VERSION;
    e.struct_size = sizeof e;
    return e;
}

p6_config valid_config()
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = P6_MACHINE_A500;
    c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000;
    c.random_seed = 1;
    c.rtc_epoch_seconds = 946684800;
    return c;
}

// The rules of ABI v1, written again here from the header comments: the fuzzer's oracle.
bool oracle_accepts(const p6_config &c)
{
    // Self-test of the harness: with this variable set the oracle is wrong on purpose, and the run has to fail.
    static const bool inverted = getenv("P6_FUZZ_SELFTEST") != nullptr;
    return inverted ^ c.abi_version == P6_ABI_VERSION && c.struct_size == sizeof c && c.machine == P6_MACHINE_A500 &&
           (c.video_standard == P6_VIDEO_PAL || c.video_standard == P6_VIDEO_NTSC) &&
           (c.audio_rate_hz == 44100 || c.audio_rate_hz == 48000) && c.flags == 0 && !c.reserved[0] &&
           !c.reserved[1] && !c.reserved[2] && !c.reserved[3] && c.rtc_epoch_seconds >= INT64_C(-62135596800) &&
           c.rtc_epoch_seconds <= INT64_C(253402300799);
}

bool message_is_bounded(const p6_error &e)
{
    return memchr(e.message, 0, sizeof e.message) != nullptr;
}

[[noreturn]] void bug(const char *what, uint64_t iteration)
{
    fprintf(stderr, "FUZZ FAILURE at iteration %llu: %s\n", (unsigned long long)iteration, what);
    exit(1);
}

int fuzz_config(uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    std::map<int, unsigned> histogram;
    for (uint64_t i = 0; i < iterations; ++i) {
        p6_config c = valid_config();
        unsigned char *raw = reinterpret_cast<unsigned char *>(&c);
        for (unsigned edits = rng.below(4); edits--;) {
            switch (rng.below(4)) {
            case 0: raw[rng.below(sizeof c)] ^= uint8_t(1u << rng.below(8)); break;
            case 1: raw[rng.below(sizeof c)] = uint8_t(rng.next()); break;
            case 2: {   // a whole aligned field set to a boundary value
                const uint64_t v = kBoundary[rng.below(sizeof kBoundary / sizeof kBoundary[0])];
                const unsigned at = rng.below(sizeof c / 4) * 4;
                memcpy(raw + at, &v, at + 8 <= sizeof c && rng.coin() ? 8 : 4);
                break;
            }
            default: c.video_standard = uint32_t(rng.below(4)); c.audio_rate_hz = rng.coin() ? 44100 : 48000; break;
            }
        }
        p6_error error = fresh_error();
        p6_error *pass = &error;
        const unsigned kind = rng.below(6);
        p6_error unusable{};                       // wrong version and size: nothing may be written into it
        if (kind == 0) pass = nullptr;
        else if (kind == 1) { unusable.abi_version = 7; unusable.message[0] = 'x'; pass = &unusable; }
        const bool null_config = rng.below(50) == 0;
        const p6_result r = p6_init(null_config ? nullptr : &c, pass);
        ++histogram[int(r)];
        const bool expected = !null_config && kind != 1 && oracle_accepts(c);
        if ((r == P6_OK) != expected) {
            char text[128];
            snprintf(text, sizeof text, "p6_init returned %d, the oracle says %s", int(r), expected ? "accept" : "reject");
            bug(text, i);
        }
        if (kind == 1 && (r != P6_ERROR_BAD_ARGUMENT || unusable.message[0] != 'x')) bug("unusable error struct written", i);
        if (pass == &error && !message_is_bounded(error)) bug("unbounded message", i);
        if (pass == &error && r == P6_OK && error.message[0]) bug("message set on success", i);
        if (r == P6_OK) {
            const p6_result twice = p6_init(&c, nullptr);
            if (twice != P6_ERROR_BAD_STATE) bug("second init did not report BAD_STATE", i);
            if (p6_shutdown(nullptr) != P6_OK || p6_shutdown(nullptr) != P6_OK) bug("shutdown", i);
        }
    }
    for (const auto &entry : histogram) printf("config result %d: %u\n", entry.first, entry.second);
    return 0;
}

std::vector<uint8_t> read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    std::vector<uint8_t> data;
    uint8_t buffer[65536];
    for (size_t n; (n = fread(buffer, 1, sizeof buffer, f)) > 0;) data.insert(data.end(), buffer, buffer + n);
    fclose(f);
    return data;
}

void fix_checksum(std::vector<uint8_t> &rom)
{
    if (rom.size() < 32 || rom.size() % 4) return;
    const size_t at = rom.size() - 24;
    memset(&rom[at], 0, 4);
    uint64_t sum = 0;
    for (size_t i = 0; i < rom.size(); i += 4)
        sum += (uint64_t(rom[i]) << 24) | (uint64_t(rom[i + 1]) << 16) | (uint64_t(rom[i + 2]) << 8) | rom[i + 3];
    while (sum >> 32) sum = (sum & 0xffffffffu) + (sum >> 32);
    const uint32_t check = ~uint32_t(sum);
    for (unsigned i = 0; i < 4; ++i) rom[at + i] = uint8_t(check >> (24 - 8 * i));
}

int fuzz_firmware(const char *path, uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    const std::vector<uint8_t> pristine = read_file(path);
    p6_config config = valid_config();
    p6_error error = fresh_error();
    if (p6_init(&config, &error) != P6_OK) bug("p6_init", 0);
    std::map<int, unsigned> histogram;
    unsigned ran = 0;
    for (uint64_t i = 0; i < iterations; ++i) {
        std::vector<uint8_t> rom = pristine;
        for (unsigned edits = 1 + rng.below(4); edits--;) {
            switch (rng.below(7)) {
            case 0: if (!rom.empty()) rom[rng.below(uint32_t(rom.size()))] ^= uint8_t(1u << rng.below(8)); break;
            case 1: if (!rom.empty()) rom[rng.below(uint32_t(rom.size()))] = uint8_t(rng.next()); break;
            case 2: rom.resize(rng.below(uint32_t(rom.size() + 1))); break;
            case 3: rom.resize(rom.size() + rng.below(2048)); break;
            case 4: {   // the header and the length field, the places the loader looks at
                static const uint32_t places[] = {0, 1, 2, 3, 4, 12, 13};
                if (rom.size() > 16) rom[places[rng.below(7)]] = uint8_t(rng.next());
                if (rom.size() > 32) rom[rom.size() - 20 + rng.below(4)] = uint8_t(rng.next());
                break;
            }
            case 5: rom.assign(rng.coin() ? 262144 : 524288, uint8_t(rng.below(3) ? 0 : rng.next())); break;
            default: for (unsigned k = rng.below(64); k-- && !rom.empty();) rom[rng.below(uint32_t(rom.size()))] = uint8_t(rng.next());
            }
        }
        if (rng.coin()) fix_checksum(rom);           // let a mutated image get past the checksum
        error = fresh_error();
        const p6_result r = p6_load(p6_buffer{rom.empty() ? nullptr : rom.data(), rom.size()}, nullptr, 0, &error);
        ++histogram[int(r)];
        if (!message_is_bounded(error)) bug("unbounded message", i);
        switch (r) {
        case P6_OK: {
            ++ran;
            p6_input input{};
            input.abi_version = P6_ABI_VERSION;
            input.struct_size = sizeof input;
            p6_frame frame{};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            for (int unit = 0; unit < 2; ++unit) {
                const p6_result run = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, &error);
                ++histogram[1000 + int(run)];
                if (run != P6_OK && run != P6_ERROR_BAD_STATE && run != P6_ERROR_NEED_FIRMWARE && run != P6_ERROR_UNSUPPORTED &&
                    run != P6_ERROR_INTERNAL) bug("untyped run result", i);
                if (run != P6_OK) break;
            }
            if (p6_unload(&error) != P6_OK) bug("unload", i);
            break;
        }
        case P6_ERROR_NEED_FIRMWARE: case P6_ERROR_WRONG_MEDIA: case P6_ERROR_CAPACITY: case P6_ERROR_UNSUPPORTED:
            if (!error.message[0]) bug("error without a message", i);
            break;
        default:
            bug("a load result outside the documented set", i);
        }
        if (i % 25 == 24) {   // a failed load leaves the instance usable: the pristine image still loads
            error = fresh_error();
            if (p6_load(p6_buffer{pristine.data(), pristine.size()}, nullptr, 0, &error) != P6_OK) bug("pristine load after a failure", i);
            if (p6_unload(&error) != P6_OK) bug("unload", i);
        }
    }
    p6_shutdown(nullptr);
    for (const auto &entry : histogram) printf("firmware result %d: %u\n", entry.first, entry.second);
    printf("firmware images that ran: %u\n", ran);
    return 0;
}

int fuzz_blob(uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    std::map<int, unsigned> histogram;
    std::vector<p6_blob_handle> handles;
    static const char alphabet[] = "abcXYZ019_-./ :\\~%\x01\xff";
    std::vector<uint8_t> payload(600, 0x5a);
    p6_blob_clear();
    for (uint64_t i = 0; i < iterations; ++i) {
        char id[100];
        static const char *const known[] = {"a", "rom1", "disk-2.adf", "k_3", "x.y", "Z9"};
        if (rng.below(4)) {   // mostly well-formed ids, so that the later operations have something to act on
            strcpy(id, known[rng.below(6)]);
        } else {
            const unsigned length = rng.below(4) == 0 ? rng.below(99) : 1 + rng.below(12);
            for (unsigned k = 0; k < length; ++k) id[k] = alphabet[rng.below(sizeof alphabet - 1)];
            id[length] = 0;
        }
        int r = 0;
        switch (rng.below(8)) {
        case 0: case 1: r = p6_blob_register(id, uint32_t(rng.below(6)), payload.data(), rng.below(600)); break;
        case 2: r = p6_blob_exists(id); break;
        case 3: {
            p6_blob_handle h = 0;
            r = p6_blob_open(id, rng.coin() ? "rb" : (rng.coin() ? "wb" : ""), &h);
            if (r == P6_BLOB_OK) handles.push_back(h);
            else if (h != 0) bug("open left a handle in its output on failure", i);
            break;
        }
        case 4: if (!handles.empty()) r = p6_blob_close(handles[rng.below(uint32_t(handles.size()))]); else r = p6_blob_close(rng.next()); break;
        case 5: {
            uint8_t out[64];
            size_t items = 7;
            const p6_blob_handle h = handles.empty() || rng.below(5) == 0 ? rng.next() : handles[rng.below(uint32_t(handles.size()))];
            const size_t width = rng.below(9), count = rng.below(70);
            r = p6_blob_read(h, out, sizeof out, width, count, &items);
            if (r != P6_BLOB_OK && items != 0) bug("read reported items on failure", i);
            break;
        }
        case 6: {
            const p6_blob_handle h = handles.empty() || rng.below(5) == 0 ? rng.next() : handles[rng.below(uint32_t(handles.size()))];
            r = p6_blob_seek(h, int64_t(rng.next()) >> rng.below(64), int(rng.below(5)) - 1);
            break;
        }
        default: {
            const p6_blob_handle h = handles.empty() ? rng.next() : handles[rng.below(uint32_t(handles.size()))];
            p6_blob_info info{};
            r = rng.coin() ? p6_blob_stat(h, &info) : p6_blob_write(h, payload.data(), rng.below(9));
        }
        }
        if (r > 1 || r < P6_BLOB_DUPLICATE) bug("a blob result outside the documented set", i);
        ++histogram[r];
        if (i % 500 == 499) { p6_blob_clear(); handles.clear(); }
    }
    p6_blob_clear();
    for (const auto &entry : histogram) printf("blob result %d: %u\n", entry.first, entry.second);
    return 0;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc == 4 && !strcmp(argv[1], "config")) return fuzz_config(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 5 && !strcmp(argv[1], "firmware")) return fuzz_firmware(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "blob")) return fuzz_blob(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    fprintf(stderr, "usage: %s config SEED N | firmware ROM SEED N | blob SEED N\n", argv[0]);
    return 64;
}
