// Deterministic mutation fuzzing of the surfaces that take outside data: the p6_config struct, firmware images
// and the logical-name store. No libFuzzer: a fixed-seed xorshift generator mutates valid inputs and every run of
// the same seed and iteration count is the same run, so a failure is a reproducer and a fixed bug is a seed.
//   fuzz_probe config SEED N             p6_init on mutated p6_config structs, against an independent oracle
//   fuzz_probe firmware ROM SEED N       p6_load on mutated images; accepted ones run two units and unload
//   fuzz_probe blob SEED N               random operations on the blob store
//   fuzz_probe media ROM SEED N          p6_load on mutated disk lists and p6_media_event_enqueue on mutated events, against an oracle
//   fuzz_probe state-writable ROM SEED N the same with P6_CONFIG_FLOPPY_WRITES: the good states carry a write overlay (T27), and p6_media_export and
//                                        p6_media_write_status are called with hostile arguments
//   fuzz_probe state ROM SEED N          p6_state_load on N mutated states of a running image (truncated, extended, bit flips, byte runs, extreme values, chunk table edits, swapped and
//                                        spliced chunks, other build/ROM/config/media identities and manifest prefixes), with, without and with an appended disk, resealed or not
//   fuzz_probe hardfile ROM SEED N IMAGE...  (T29d) p6_load of a hard file whose Rigid Disk Block, PART, FSHD and LSEG blocks were mutated (fields set to boundary values, pointers
//                                        sent elsewhere, blocks swapped, copied, zeroed or cut off, with the checksums put right again most of the time), then the micro-ROM of
//                                        tests/hdf/hdf_rom.py (mount_program) asks the filesystem for the partitions, which parses all of it; the state is saved and loaded back
//   fuzz_probe trackdisk ROM SEED N IMAGE...  (T57) p6_load of extended ADF images (and FDI, T57b) with a mutated header or table, against an independent oracle of the format; the accepted ones are
//                                        read by the micro-ROM of tests/media/trackdisk.py sweep_rom_source over the first cylinders at turbo speed, saved and loaded back
// Output is a histogram of result codes; nothing else varies between runs.
#include "p6.h"
#include "blob_io.h"
#include "media.h"
#include "sha256.h"
#include "envelope.h"

#include <algorithm>
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

bool g_writable = false;      // state-writable: the session is configured with P6_CONFIG_FLOPPY_WRITES

p6_config valid_config(uint32_t machine = P6_MACHINE_A500)
{
    p6_config c{};
    c.abi_version = P6_ABI_VERSION;
    c.struct_size = sizeof c;
    c.machine = machine;
    c.video_standard = P6_VIDEO_PAL;
    c.audio_rate_hz = 48000;
    c.random_seed = 1;
    c.rtc_epoch_seconds = 946684800;
    c.flags = g_writable ? P6_CONFIG_FLOPPY_WRITES : 0;
    return c;
}

// The memory rules of p6_config (T49), written again from the header comments: 0 is the machine's own, P6_MEMORY_NONE is none. The A500 has an OCS Agnus (Chip RAM 512 KiB or 1 MiB),
// AGA maps 1.5 MiB of Slow RAM at most, and the Fast RAM board is 1, 2, 4 or 8 MiB.
bool machine_accepted(uint32_t machine) { return machine >= P6_MACHINE_A500 && machine <= P6_MACHINE_A2000; }       // 1 to 8 (T50)

// OCS Agnus: the A500, the A500 OG and the A2000 OG (the A1000 has its own rule); the others have the ECS Agnus or AGA and take 2 MiB of Chip RAM.
bool ocs_agnus(uint32_t machine) { return machine == P6_MACHINE_A500 || machine == P6_MACHINE_A500OG || machine == P6_MACHINE_A2000OG; }

bool memory_accepted(const p6_config &c)
{
    if (c.machine == P6_MACHINE_A1000)         // the memory of its preset: 512 KiB of Chip RAM, nothing else
        return (c.chip_kib == 0 || c.chip_kib == 512) && (c.slow_kib == 0 || c.slow_kib == P6_MEMORY_NONE) && (c.fast_kib == 0 || c.fast_kib == P6_MEMORY_NONE);
    const bool a500 = ocs_agnus(c.machine), aga = c.machine == P6_MACHINE_A1200;
    const bool chip = c.chip_kib == 0 || c.chip_kib == 512 || c.chip_kib == 1024 || (!a500 && c.chip_kib == 2048);
    const bool slow = c.slow_kib == 0 || c.slow_kib == P6_MEMORY_NONE || c.slow_kib == 512 || c.slow_kib == 1024 || c.slow_kib == 1536 || (!aga && c.slow_kib == 1792);
    const bool fast = c.fast_kib == 0 || c.fast_kib == P6_MEMORY_NONE || c.fast_kib == 1024 || c.fast_kib == 2048 || c.fast_kib == 4096 || c.fast_kib == 8192;
    return chip && slow && fast;
}

// The FPU (T54, p6_config.fpu_model, the four bytes that were reserved32): none, or a 68881 or 68882 on the A1200 (the 68020); anything else is refused.
bool fpu_accepted(const p6_config &c)
{
    return c.fpu_model == 0 || ((c.fpu_model == 68881 || c.fpu_model == 68882) && c.machine == P6_MACHINE_A1200);
}

// Paula's output (T73, the first four bytes of what was reserved[0]): 0 is the profile's, separation 1 to 11, interpolation 1, 2, 4 or 5 (3, sinc, is refused), filter 1 to 3, filter type 1 or 2.
// The fifth byte is the floppy speed (T62): 0 is the profile's, 1 to 5 the steps; the sixth and seventh are the raster (T72: resolution 0 the profile's, 1 lores, 2 hires, 3 super-hires; line
// mode 0 the profile's, 1 single, 2 double); the eighth is the CPU (T52: 0 the machine's own, 1 68000 and 2 68010 on the 68000 machines, 3 68020 on the A1200) and the first of the next eight its
// clock (0 the machine's own, else 1, 2, 4, 8, 10, 12 or 16); the other seven are still reserved.
bool cpu_accepted(const p6_config &c)
{
    return c.cpu_model == 0 || (c.machine == P6_MACHINE_A1200 ? c.cpu_model == 3 : (c.cpu_model == 1 || c.cpu_model == 2));
}

bool clock_accepted(const p6_config &c)
{
    const uint8_t m = c.cpu_multiplier;
    return m == 0 || m == 1 || m == 2 || m == 4 || m == 8 || m == 10 || m == 12 || m == 16;
}

// The controller of the hard files (T56, the second byte of what was reserved[1]): the UAE controller (0), or the IDE of Gayle (1), which only the A600 and the A1200 have; anything else is refused.
bool hdf_controller_accepted(const p6_config &c)
{
    return c.hdf_controller == P6_HDF_CONTROLLER_UAE || (c.hdf_controller == P6_HDF_CONTROLLER_IDE && (c.machine == P6_MACHINE_A600 || c.machine == P6_MACHINE_A1200));
}

bool audio_accepted(const p6_config &c)
{
    return c.sound_separation <= 11 && c.sound_interpolation <= 5 && c.sound_interpolation != 3 && c.sound_filter <= 3 && c.sound_filter_type <= 2 && c.floppy_speed <= 5 &&
           c.video_resolution <= 3 && c.video_line_mode <= 2 && cpu_accepted(c) && clock_accepted(c);
}

// The rules of ABI v1, written again here from the header comments: the fuzzer's oracle.
bool oracle_accepts(const p6_config &c)
{
    // Self-test of the harness: with this variable set the oracle is wrong on purpose, and the run has to fail.
    static const bool inverted = getenv("P6_FUZZ_SELFTEST") != nullptr;
    return inverted ^ c.abi_version == P6_ABI_VERSION && c.struct_size == sizeof c && machine_accepted(c.machine) &&
           (c.video_standard == P6_VIDEO_PAL || c.video_standard == P6_VIDEO_NTSC) &&
           (c.audio_rate_hz == 44100 || c.audio_rate_hz == 48000) && (c.flags & ~P6_CONFIG_FLOPPY_WRITES) == 0 && fpu_accepted(c) && audio_accepted(c) &&
           !c.reserved7[0] && !c.reserved8[0] && c.z3_memory <= 1 && (!c.z3_memory || c.machine == P6_MACHINE_A4030) && c.collision_level <= 4 && c.floppy_drives <= 4 &&
           c.cpu_compatibility <= P6_CPU_NORMAL && hdf_controller_accepted(c) && c.rtc_epoch_seconds >= INT64_C(-62135596800) &&
           c.rtc_epoch_seconds <= INT64_C(253402300799) && memory_accepted(c);
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
            switch (rng.below(5)) {
            case 0: raw[rng.below(sizeof c)] ^= uint8_t(1u << rng.below(8)); break;
            case 1: raw[rng.below(sizeof c)] = uint8_t(rng.next()); break;
            case 2: {   // a whole aligned field set to a boundary value
                const uint64_t v = kBoundary[rng.below(sizeof kBoundary / sizeof kBoundary[0])];
                const unsigned at = rng.below(sizeof c / 4) * 4;
                memcpy(raw + at, &v, at + 8 <= sizeof c && rng.coin() ? 8 : 4);
                break;
            }
            case 3: {   // a size next to the ones the machines have
                static const uint32_t sizes[] = {0, 256, 512, 1024, 1536, 1792, 2048, 4096, 8192, 16384, P6_MEMORY_NONE};
                uint32_t *field = rng.below(3) == 0 ? &c.chip_kib : rng.coin() ? &c.slow_kib : &c.fast_kib;
                *field = sizes[rng.below(sizeof sizes / sizeof sizes[0])];
                if (rng.coin()) c.machine = 1 + rng.below(8);
                if (rng.below(4) == 0) { static const uint32_t fpus[] = {0, 68881, 68882, 68040, 1}; c.fpu_model = fpus[rng.below(5)]; }       // T54: valid on the A1200 only
                if (rng.below(4) == 0) {         // T73: Paula's output, in and out of range
                    c.sound_separation = uint8_t(rng.below(14)); c.sound_interpolation = uint8_t(rng.below(8));
                    c.sound_filter = uint8_t(rng.below(5)); c.sound_filter_type = uint8_t(rng.below(4)); c.floppy_speed = uint8_t(rng.below(8));
                }
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

// --- media ---------------------------------------------------------------------------------------------------------

constexpr uint64_t kAdfBytes = 1760u * 512u;

std::vector<uint8_t> random_adf(Random &rng)
{
    std::vector<uint8_t> adf(kAdfBytes);
    uint64_t x = rng.next() | 1;
    for (uint8_t &b : adf) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; b = uint8_t(x >> 24); }
    adf[0] = 'D'; adf[1] = 'O'; adf[2] = 'S'; adf[3] = 0;
    return adf;
}

bool special_header(const uint8_t *d)
{
    return !memcmp(d, "UAE--ADF", 8) || !memcmp(d, "UAE-1ADF", 8) || !memcmp(d, "\x00\x00\x03\xf3\x00\x00\x00\x00", 8);
}

// The rules of p6_load's media list, written again from docs/adr/0008-media-identity.md: the oracle.
bool oracle_accepts_media(const std::vector<p6_media> &list)
{
    if (list.size() > P6_MAX_MEDIA) return false;
    for (size_t i = 0; i < list.size(); ++i) {
        const p6_media &m = list[i];
        if (m.type != P6_MEDIA_ADF || m.flags != 0 || !m.image.data || m.image.bytes != kAdfBytes) return false;
        if (special_header(static_cast<const uint8_t *>(m.image.data))) return false;
        for (size_t j = 0; j < i; ++j) if (!memcmp(list[j].content_id, m.content_id, P6_CONTENT_ID_SIZE)) return false;
    }
    for (const p6_media &m : list) {
        uint8_t digest[32];
        p6_sha256_hash(m.image.data, m.image.bytes, digest);
        if (memcmp(digest, m.content_id, 32)) return false;
    }
    return true;
}

int fuzz_media(const char *path, uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    const std::vector<uint8_t> rom = read_file(path);
    // A small pool of images: two good ones, two that carry an extended header and one that is a byte short.
    std::vector<std::vector<uint8_t>> pool = {random_adf(rng), random_adf(rng), random_adf(rng), random_adf(rng)};
    memcpy(pool[2].data(), "UAE-1ADF", 8);
    memcpy(pool[3].data(), rng.coin() ? "UAE--ADF" : "\x00\x00\x03\xf3\x00\x00\x00\x00", 8);
    const std::vector<uint8_t> short_image(kAdfBytes - 1, 7);
    p6_config config = valid_config();
    p6_error error = fresh_error();
    if (p6_init(&config, &error) != P6_OK) bug("p6_init", 0);
    std::map<int, unsigned> histogram;
    unsigned ran = 0, events_accepted = 0, events_refused = 0;
    for (uint64_t i = 0; i < iterations; ++i) {
        std::vector<p6_media> list(rng.below(4) == 0 ? rng.below(20) : rng.below(4));
        std::vector<std::vector<uint8_t>> mutated;     // owns the mutated copies for this iteration
        mutated.reserve(list.size());
        for (p6_media &m : list) {
            const std::vector<uint8_t> *image = &pool[rng.below(2) ? rng.below(2) : rng.below(4)];
            if (rng.below(8) == 0) image = &short_image;
            if (rng.below(6) == 0) {                   // a flipped byte somewhere: still 901120 bytes, another identity
                mutated.push_back(*image);
                mutated.back()[4 + rng.below(uint32_t(kAdfBytes - 4))] ^= uint8_t(1 + rng.below(255));
                image = &mutated.back();
            }
            m = p6_media{};
            m.type = rng.below(10) == 0 ? uint32_t(rng.below(4)) : P6_MEDIA_ADF;
            m.flags = rng.below(12) == 0 ? uint32_t(rng.next()) : 0;
            m.image = p6_buffer{image->data(), image->size()};
            if (rng.below(16) == 0) m.image.data = nullptr;
            if (rng.below(16) == 0 && image->size() >= 4096) m.image.bytes = kBoundary[rng.below(8)] % 4096;   // never above the real size
            p6_sha256_hash(image->data(), image->size(), m.content_id);
            switch (rng.below(10)) {
            case 0: m.content_id[rng.below(32)] ^= uint8_t(1u << rng.below(8)); break;
            case 1: for (uint8_t &b : m.content_id) b = uint8_t(rng.next()); break;
            case 2: memset(m.content_id, 0, sizeof m.content_id); break;
            default: break;
            }
        }
        const bool expected = oracle_accepts_media(list);
        error = fresh_error();
        const p6_result r = p6_load(p6_buffer{rom.data(), rom.size()}, list.empty() && rng.coin() ? nullptr : list.data(),
                                    uint32_t(list.size()), &error);
        ++histogram[int(r)];
        if (!message_is_bounded(error)) bug("unbounded message", i);
        if ((r == P6_OK) != expected) bug(r == P6_OK ? "an invalid media list was accepted" : "a valid media list was refused", i);
        if (r != P6_OK) {
            if (r != P6_ERROR_CAPACITY && r != P6_ERROR_BAD_ARGUMENT && r != P6_ERROR_UNSUPPORTED && r != P6_ERROR_WRONG_MEDIA)
                bug("a media result outside the documented set", i);
            if (!error.message[0]) bug("error without a message", i);
            continue;
        }
        ++ran;
        // Events against a model of the queue rules.
        unsigned queued = 0;
        bool have = false;
        uint64_t last = 0, sequence = rng.next() % 100;
        for (unsigned n = rng.below(24); n--;) {
            p6_media_event e{};
            e.abi_version = P6_ABI_VERSION;
            e.struct_size = sizeof e;
            e.type = rng.below(8) == 0 ? uint32_t(rng.below(5)) : (rng.coin() ? P6_MEDIA_EVENT_MOUNT : P6_MEDIA_EVENT_EJECT);
            e.drive = rng.below(8) == 0 ? uint32_t(rng.below(6)) : uint32_t(rng.below(2));
            e.media_index = e.type == P6_MEDIA_EVENT_EJECT && rng.below(6) ? P6_MEDIA_INDEX_EJECT : uint32_t(rng.below(list.size() + 2));
            sequence += rng.below(6) == 0 ? 0 : 1 + rng.below(3);
            e.sequence = rng.below(10) == 0 ? rng.next() % 50 : sequence;
            if (rng.below(20) == 0) e.reserved[rng.below(2)] = 1;
            const bool valid = !e.reserved0 && !e.reserved[0] && !e.reserved[1] &&
                (e.type == P6_MEDIA_EVENT_MOUNT || e.type == P6_MEDIA_EVENT_EJECT) && e.drive <= 1 &&
                (e.type == P6_MEDIA_EVENT_MOUNT ? e.media_index < list.size() : e.media_index == P6_MEDIA_INDEX_EJECT) &&
                (!have || e.sequence > last) && queued < 16;
            error = fresh_error();
            const p6_result q = p6_media_event_enqueue(&e, &error);
            if ((q == P6_OK) != valid) bug(q == P6_OK ? "an invalid event was queued" : "a valid event was refused", i);
            if (q == P6_OK) { ++queued; have = true; last = e.sequence; ++events_accepted; }
            else if (!error.message[0]) bug("event error without a message", i);
            else ++events_refused;
            ++histogram[2000 + int(q)];
        }
        p6_input input{};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        p6_frame frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        for (unsigned unit = rng.below(4); unit--;) {
            const p6_result run = p6_run_frame(&input, 0, &frame, &error);
            ++histogram[1000 + int(run)];
            if (run != P6_OK) bug("a unit failed with media mounted", i);
        }
        if (p6_unload(&error) != P6_OK) bug("unload", i);
    }
    p6_shutdown(nullptr);
    for (const auto &entry : histogram) printf("media result %d: %u\n", entry.first, entry.second);
    printf("media lists that loaded: %u, events accepted %u refused %u\n", ran, events_accepted, events_refused);
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


// --- state ---------------------------------------------------------------------------------------------------------
//
// p6_state_load takes a buffer from outside, so it is the surface of the native state that an attacker or a corrupt file reaches. A valid state is made in-process (a micro-ROM that ran a few
// units, with and without a disk) and then loaded back thousands of times with a mutation applied. Half of the mutations leave the checksums alone (the cheap refusals); the other half
// recompute the payload and header checksums, so that the structure checks, the identity, the chunk table and every chunk visitor in Validate mode see bytes that are well formed
// as a container and wrong as a machine. What must hold, for every candidate:
//   - the result is OK, CORRUPT_STATE or INCOMPATIBLE (BAD_ARGUMENT for a null buffer with a length), never INTERNAL, which is a faulted image;
//   - there is no sanitizer report (the run is in the ASan + UBSan build) and no allocation proportional to a forged length (the test caps the allocator, ASAN_OPTIONS
//     max_allocation_size_mb);
//   - a refusal leaves the machine exactly as it was: saving it again gives the bytes of the good state, byte for byte;
//   - the image stays usable after a refusal: a unit runs and the state after it is the one of a control run that never saw the attempt (the reference, taken before the loop);
//   - an accepted candidate (a flipped byte in RAM is a valid machine) leaves an image that runs a unit; loading the good state afterwards is always accepted and gives the good state back.

constexpr uint64_t kStateUnits = 3;                    // units the image runs before the good state is taken
constexpr uint32_t kStateChunks = 11;

uint64_t le64(const uint8_t *p) { uint64_t v = 0; for (int i = 7; i >= 0; --i) v = v << 8 | p[i]; return v; }
void put_le(uint8_t *p, uint64_t v, unsigned bytes) { for (unsigned i = 0; i < bytes; ++i) p[i] = uint8_t(v >> (8 * i)); }

// The checksums of a state whose bytes were edited: the payload's (offset 272) and the header's (offset 304), as seal() writes them.
void rehash(std::vector<uint8_t> &s, bool fix_length)
{
    if (s.size() < p6_state::kHeaderSize) return;
    if (fix_length) put_le(&s[40], s.size() - p6_state::kHeaderSize, 8);
    const uint8_t *part = s.data() + p6_state::kHeaderSize;
    uint64_t length = s.size() - p6_state::kHeaderSize;
    p6_state::domain_hash("p6/native-payload/v1", &part, &length, 1, &s[272]);
    part = s.data();
    length = 304;
    p6_state::domain_hash("p6/native-header/v1", &part, &length, 1, &s[304]);
}

struct StateImage {
    std::vector<uint8_t> good;                         // the state the loop loads mutated copies of
    std::vector<uint8_t> reference;                    // the state one unit after it, in a machine that never saw an attempt
    std::vector<uint8_t> reference_pcm;                // the audio of that unit
    std::vector<uint8_t> foreign;                      // a valid state of the other session (another media manifest)
    uint64_t chunk_at[kStateChunks], chunk_length[kStateChunks];
    uint64_t bound = 0;
    const uint8_t *adf_id = nullptr;                   // the content id of the disk of the session (null: none), for the header edits that name a manifest
};

p6_input state_input()
{
    p6_input input{};
    input.abi_version = P6_ABI_VERSION;
    input.struct_size = sizeof input;
    input.ports[0] = {P6_PORT_MOUSE, 1, 3, -2, 0};      // something for the input chunk to carry
    input.keys[1] = 1u << 0;                            // key 0x20 held
    return input;
}

// One unit; the PCM it handed over is returned in `pcm`.
p6_result state_unit(std::vector<uint8_t> *pcm, p6_error *error)
{
    const p6_input input = state_input();
    p6_frame frame{};
    frame.abi_version = P6_ABI_VERSION;
    frame.struct_size = sizeof frame;
    const p6_result r = p6_run_frame(&input, P6_RENDER_VIDEO | P6_RENDER_AUDIO, &frame, error);
    if (pcm) {
        pcm->clear();
        if (r == P6_OK && frame.audio && frame.audio_frames) {
            const uint8_t *data = reinterpret_cast<const uint8_t *>(frame.audio);
            pcm->assign(data, data + size_t(frame.audio_frames) * 2 * sizeof(int16_t));
        }
    }
    return r;
}

bool state_save(std::vector<uint8_t> &out, uint64_t *bound_seen)
{
    p6_error error = fresh_error();
    uint64_t bound = 0, written = 0;
    if (p6_state_bound(&bound, &error) != P6_OK) return false;
    if (bound_seen) *bound_seen = bound;
    out.assign(bound, 0);
    if (p6_state_save(out.data(), out.size(), &written, &error) != P6_OK || written > bound) return false;
    out.resize(written);
    return true;
}

// What a guest leaves on a writable disk, written the way the engine writes it (zfile over the blob store): a few runs of sectors, one of them a whole track.
void guest_writes(const uint8_t content_id[P6_CONTENT_ID_SIZE])
{
    char id[p6_bridge::kMediaBlobIdBytes];
    p6_bridge::media_blob_id(content_id, id);
    p6_blob_handle h = 0;
    if (p6_blob_open(id, "r+b", &h) != P6_BLOB_OK) bug("a writable disk did not open r+b", 0);
    uint8_t data[11 * 512];
    for (size_t i = 0; i < sizeof data; ++i) data[i] = uint8_t(i * 7 + (i >> 9) * 13 + 1);
    const struct { int64_t at; size_t bytes; } runs[] = {{0, 11 * 512}, {100 * 512 + 17, 700}, {1759 * 512, 512}};
    for (const auto &run : runs)
        if (p6_blob_seek(h, run.at, P6_BLOB_SET) != P6_BLOB_OK || p6_blob_write(h, data, run.bytes) != P6_BLOB_OK) bug("a guest write did not land", 0);
    p6_blob_close(h);
}

// Loads the image of one session (`media`: a disk mounted at boot or none), runs it and takes the good state, the reference and, where asked, leaves the image loaded.
// `append` registers the disk with p6_media_append after a load of no media instead of passing it to p6_load (T17e): the manifest, and so the state, is the same.
bool state_session(const std::vector<uint8_t> &rom, const std::vector<uint8_t> *adf, StateImage &image, bool keep_loaded, bool append = false)
{
    p6_error error = fresh_error();
    p6_media media{};
    if (adf) {
        media.type = P6_MEDIA_ADF;
        media.image = p6_buffer{adf->data(), adf->size()};
        p6_sha256_hash(adf->data(), adf->size(), media.content_id);
    }
    const bool at_load = adf && !append;
    if (p6_load(p6_buffer{rom.data(), rom.size()}, at_load ? &media : nullptr, at_load ? 1 : 0, &error) != P6_OK) return false;
    if (adf && append) {
        uint32_t index = UINT32_MAX;
        if (p6_media_append(&media, &index, &error) != P6_OK || index != 0) return false;
    }
    if (adf && g_writable) guest_writes(media.content_id);
    if (adf) {
        p6_media_event event{};
        event.abi_version = P6_ABI_VERSION;
        event.struct_size = sizeof event;
        event.type = P6_MEDIA_EVENT_MOUNT;
        event.drive = 0;
        event.media_index = 0;
        event.sequence = 1;
        if (p6_media_event_enqueue(&event, &error) != P6_OK) return false;
    }
    for (uint64_t unit = 0; unit < kStateUnits; ++unit)
        if (state_unit(nullptr, &error) != P6_OK) return false;
    if (!state_save(image.good, &image.bound)) return false;
    if (state_unit(&image.reference_pcm, &error) != P6_OK || !state_save(image.reference, nullptr)) return false;
    // The chunk table of the good state, for the edits that name a chunk.
    uint64_t at = p6_state::kHeaderSize;
    for (uint32_t i = 0; i < kStateChunks; ++i) {
        if (at + p6_state::kChunkHeaderSize > image.good.size()) return false;
        image.chunk_at[i] = at;
        image.chunk_length[i] = le64(&image.good[at + 8]);
        at += p6_state::kChunkHeaderSize + image.chunk_length[i];
    }
    if (at != image.good.size()) return false;
    if (p6_state_load(image.good.data(), image.good.size(), &error) != P6_OK) return false;     // back to the good state
    if (!keep_loaded && p6_unload(&error) != P6_OK) return false;
    return true;
}

struct Candidate {
    std::vector<uint8_t> bytes;
    const char *kind;
    bool null_buffer = false;
    uint64_t length = ~uint64_t(0);        // the length handed to the call when it is not the size of the buffer
};

const char *const kStateKinds[] = {"truncate", "extend", "flip", "flip-resealed", "run", "extreme", "chunk-header", "header-field", "identity", "swap-chunks", "drop-dup-chunk",
                                   "foreign", "null-buffer", "length-forged", "manifest-prefix"};
constexpr unsigned kStateKindCount = sizeof kStateKinds / sizeof kStateKinds[0];

Candidate mutate_state(const StateImage &image, Random &rng, unsigned kind)
{
    Candidate c{image.good, kStateKinds[kind], false};
    std::vector<uint8_t> &s = c.bytes;
    const uint64_t header = p6_state::kHeaderSize;
    const auto payload_byte = [&]() { return header + rng.below(uint32_t(s.size() - header)); };
    const auto boundary = [&]() { return kBoundary[rng.below(sizeof kBoundary / sizeof kBoundary[0])]; };
    switch (kind) {
    case 0: {           // truncated, at the places where the parser changes its mind and anywhere
        static const uint64_t cuts[] = {0, 1, 7, 8, 12, 335, 336, 337, 351, 352, 353};
        size_t to = rng.below(3) == 0 ? size_t(cuts[rng.below(sizeof cuts / sizeof cuts[0])]) : rng.below(uint32_t(s.size()));
        if (rng.below(4) == 0) {                  // at a chunk boundary or inside a chunk header
            const unsigned chunk = rng.below(kStateChunks);
            to = size_t(image.chunk_at[chunk] + rng.below(p6_state::kChunkHeaderSize + 2));
        }
        s.resize(std::min(to, s.size()));
        if (rng.coin()) rehash(s, rng.coin());
        break;
    }
    case 1: {           // extended: some bytes, or the bound plus one
        const size_t extra = rng.below(4) == 0 ? size_t(image.bound - s.size() + 1) : 1 + rng.below(4096);
        for (size_t i = 0; i < extra; ++i) s.push_back(uint8_t(rng.next()));
        if (rng.coin()) rehash(s, rng.coin());
        break;
    }
    case 2:             // bits flipped anywhere: the checksums refuse it
        for (unsigned n = 1 + rng.below(4); n--;) s[rng.below(uint32_t(s.size()))] ^= uint8_t(1u << rng.below(8));
        break;
    case 3:             // bits flipped in the payload with the checksums made right again: every visitor sees them
        for (unsigned n = 1 + rng.below(4); n--;) s[payload_byte()] ^= uint8_t(1u << rng.below(8));
        rehash(s, false);
        break;
    case 4: {           // a run of bytes in the payload: noise, zeros, ones or a copy of other bytes of the same state
        const size_t length = 1 + rng.below(64), at = payload_byte();
        const unsigned fill = rng.below(4);
        const size_t from = payload_byte();
        for (size_t i = 0; i < length && at + i < s.size(); ++i)
            s[at + i] = fill == 0 ? uint8_t(rng.next()) : fill == 1 ? 0 : fill == 2 ? 0xFF : s[std::min(from + i, s.size() - 1)];
        rehash(s, false);
        break;
    }
    case 5: {           // a count, length, index or value of the body set to an extreme, at the width a field has
        const unsigned width = 1u << rng.below(4);
        const size_t at = payload_byte() & ~size_t(width - 1);
        if (at + width <= s.size() && at >= header) put_le(&s[at], boundary(), width);
        rehash(s, false);
        break;
    }
    case 6: {           // the header of one chunk: tag, version or body length
        const unsigned chunk = rng.below(kStateChunks);
        const uint64_t at = image.chunk_at[chunk];
        switch (rng.below(3)) {
        case 0: s[at + rng.below(4)] ^= uint8_t(1 + rng.below(255)); break;
        case 1: put_le(&s[at + 4], rng.coin() ? boundary() : rng.below(8), 4); break;
        default: put_le(&s[at + 8], rng.coin() ? boundary() : image.chunk_length[chunk] + rng.below(5) - 2, 8); break;
        }
        rehash(s, false);
        break;
    }
    case 7: {           // a field of the header: format, size, profile, flags, unit, ticks, payload length, chunk count, reserved
        static const struct { unsigned at, width; } fields[] = {{8, 4}, {12, 4}, {16, 4}, {20, 4}, {24, 8}, {32, 8}, {40, 8}, {48, 4}, {52, 4}, {108, 4}};
        const auto &f = fields[rng.below(sizeof fields / sizeof fields[0])];
        put_le(&s[f.at], rng.coin() ? boundary() : rng.next(), f.width);
        rehash(s, false);
        break;
    }
    case 8: {           // another build, firmware, configuration or media: one byte of an identity hash of the header differs
        static const unsigned starts[] = {56, 88, 112, 144, 176, 208, 240};
        static const unsigned widths[] = {32, 20, 32, 32, 32, 32, 32};
        const unsigned which = rng.below(7);
        s[starts[which] + rng.below(widths[which])] ^= uint8_t(1 + rng.below(255));
        rehash(s, false);
        break;
    }
    case 9: {           // two chunks exchanged (header and body), or one body replaced by the bytes of another
        const unsigned a = rng.below(kStateChunks), b = rng.below(kStateChunks);
        std::vector<uint8_t> out(s.begin(), s.begin() + header);
        std::vector<unsigned> order(kStateChunks);
        for (unsigned i = 0; i < kStateChunks; ++i) order[i] = i;
        std::swap(order[a], order[b]);
        for (unsigned i : order)
            out.insert(out.end(), image.good.begin() + image.chunk_at[i], image.good.begin() + image.chunk_at[i] + p6_state::kChunkHeaderSize + image.chunk_length[i]);
        s = out;
        rehash(s, true);
        break;
    }
    case 10: {          // a chunk dropped or repeated, with the chunk count fixed or not
        const unsigned chunk = rng.below(kStateChunks);
        std::vector<uint8_t> out(s.begin(), s.begin() + header);
        for (unsigned i = 0; i < kStateChunks; ++i) {
            const auto first = image.good.begin() + image.chunk_at[i];
            const auto last = first + p6_state::kChunkHeaderSize + image.chunk_length[i];
            if (i != chunk || rng.coin()) out.insert(out.end(), first, last);       // dropped when the coin is tails
            if (i == chunk && rng.coin()) out.insert(out.end(), first, last);
        }
        s = out;
        if (rng.coin()) put_le(&s[48], kStateChunks + rng.below(3) - 1, 4);
        rehash(s, true);
        break;
    }
    case 11: {          // the state of another machine (another media manifest), whole or with one of its chunks spliced into this one
        if (rng.coin()) { s = image.foreign; break; }
        const unsigned chunk = rng.below(kStateChunks);
        uint64_t at = p6_state::kHeaderSize, length = 0;
        for (unsigned i = 0; i <= chunk; ++i) {
            length = le64(&image.foreign[at + 8]);
            if (i < chunk) at += p6_state::kChunkHeaderSize + length;
        }
        std::vector<uint8_t> out(s.begin(), s.begin() + header);
        for (unsigned i = 0; i < kStateChunks; ++i) {
            const auto &source = i == chunk ? image.foreign : image.good;
            const uint64_t from = i == chunk ? at : image.chunk_at[i];
            const uint64_t size = p6_state::kChunkHeaderSize + (i == chunk ? length : image.chunk_length[i]);
            out.insert(out.end(), source.begin() + from, source.begin() + from + size);
        }
        s = out;
        rehash(s, true);
        break;
    }
    case 12:            // no buffer at all
        c.null_buffer = true;
        break;
    case 14: {          // the manifest the header names (T17e): the hash of no media, of the disk, of another one or of two, resealed. The state loads only where that list is a prefix of the live
                        // manifest, and then only if every medium its bodies name is inside it
        uint8_t other[32];
        for (uint8_t &byte : other) byte = uint8_t(rng.next());
        const uint8_t *ids[2] = {image.adf_id && rng.coin() ? image.adf_id : other, rng.coin() ? other : image.adf_id ? image.adf_id : other};
        const uint32_t types[2] = {P6_MEDIA_ADF, P6_MEDIA_ADF};
        p6_state::media_manifest_hash(rng.below(3), types, ids, &s[208]);
        rehash(s, false);
        break;
    }
    default:            // the buffer holds the whole good state and the length given to the call is shorter: the parser must read no further than it was told
        c.length = rng.below(3) == 0 ? s.size() - 1 - rng.below(8) : rng.below(uint32_t(s.size()));
        break;
    }
    if (kind != 2 && kind != 12 && kind != 13 && s.size() > header && rng.below(4) == 0) {        // a second edit on top, with the checksums right again
        s[payload_byte()] ^= uint8_t(1u << rng.below(8));
        rehash(s, false);
    }
    return c;
}

// p6_media_export and p6_media_write_status with arguments from outside: every outcome is one of the documented codes, nothing is written beyond the capacity that was given,
// a refusal leaves the caller's buffer alone, and an accepted export is the base with the guest's sectors over it (the same bytes the blob store merges).
void fuzz_export(Random &rng, const std::vector<uint8_t> &rom, const std::vector<uint8_t> &adf, uint64_t iterations)
{
    p6_error error = fresh_error();
    p6_media media{};
    media.type = P6_MEDIA_ADF;
    media.image = p6_buffer{adf.data(), adf.size()};
    p6_sha256_hash(adf.data(), adf.size(), media.content_id);
    if (p6_load(p6_buffer{rom.data(), rom.size()}, &media, 1, &error) != P6_OK) bug("the writable session for the export fuzz could not load", 0);
    guest_writes(media.content_id);
    std::vector<uint8_t> expected(adf);
    {
        uint8_t data[11 * 512];
        for (size_t i = 0; i < sizeof data; ++i) data[i] = uint8_t(i * 7 + (i >> 9) * 13 + 1);
        memcpy(expected.data(), data, 11 * 512);
        memcpy(expected.data() + 100 * 512 + 17, data, 700);
        memcpy(expected.data() + 1759 * 512, data, 512);
    }
    const uint32_t indexes[] = {0, 0, 0, 1, 15, 16, 0xffffffffu};
    const uint64_t capacities[] = {0, 1, 901119, 901120, 901120, 901121, 1u << 20, ~uint64_t(0)};
    for (uint64_t i = 0; i < iterations; ++i) {
        const uint32_t index = indexes[rng.below(7)];
        const uint64_t capacity = capacities[rng.below(8)];
        std::vector<uint8_t> out(size_t(1u << 20) + 64, 0xA5);          // a canary after the largest capacity that can be honest
        uint64_t written = 0x1234, *written_ptr = rng.below(16) ? &written : nullptr;
        void *buffer = rng.below(16) ? out.data() : nullptr;
        const uint64_t allowed = capacity < (1u << 20) ? capacity : (1u << 20);
        error = fresh_error();
        const p6_result r = p6_media_export(index, buffer, capacity, written_ptr, &error);
        for (size_t k = size_t(allowed); k < out.size(); ++k)
            if (out[k] != 0xA5) bug("p6_media_export wrote beyond the capacity it was given", i);
        const bool should = index == 0 && buffer && written_ptr && capacity >= 901120 && capacity <= (1u << 20);
        if (capacity > (1u << 20) && buffer && written_ptr && index == 0) continue;      // a capacity larger than the real buffer: the call would honestly write 901120 bytes, which fit
        if (should != (r == P6_OK)) bug("p6_media_export accepted or refused the wrong call", i);
        if (r == P6_OK && (written != 901120 || memcmp(out.data(), expected.data(), expected.size()))) bug("the export is not the base with the guest's writes over it", i);
        if (r != P6_OK && written_ptr && written != 0) bug("a refused export left a length", i);
        if (r != P6_OK && r != P6_ERROR_BAD_ARGUMENT && r != P6_ERROR_CAPACITY) bug("an export answered a code the header does not give it", i);
        p6_media_write_status st{};
        st.abi_version = rng.below(8) ? P6_ABI_VERSION : 7;
        st.struct_size = rng.below(8) ? sizeof st : 8;
        st.media_index = indexes[rng.below(7)];
        st.reserved0 = rng.below(16) ? 0 : 1;
        error = fresh_error();
        const p6_result q = p6_media_write_status_get(rng.below(32) ? &st : nullptr, &error);
        const bool good = st.abi_version == P6_ABI_VERSION && st.struct_size == sizeof st && !st.reserved0 && st.media_index == 0;
        if (q == P6_OK && (!good || st.overlay_used == 0 || st.overlay_capacity != P6_OVERLAY_SECTORS || st.refused_writes != 0)) bug("a status that cannot be", i);
        if (q != P6_OK && q != P6_ERROR_BAD_ARGUMENT) bug("a status answered a code the header does not give it", i);
    }
    if (p6_unload(&error) != P6_OK) bug("unload after the export fuzz", 0);
}

int fuzz_state(const char *path, uint64_t seed, uint64_t iterations, uint32_t machine)
{
    Random rng(seed);
    const std::vector<uint8_t> rom = read_file(path);
    const std::vector<uint8_t> adf = random_adf(rng);
    p6_config config = valid_config(machine);
    p6_error error = fresh_error();
    if (p6_init(&config, &error) != P6_OK) bug("p6_init", 0);
    uint8_t adf_id[P6_CONTENT_ID_SIZE];
    p6_sha256_hash(adf.data(), adf.size(), adf_id);
    StateImage images[3];
    images[1].adf_id = images[2].adf_id = adf_id;
    if (!state_session(rom, nullptr, images[0], false)) bug("the good state of the session without media could not be made", 0);
    if (!state_session(rom, &adf, images[1], false)) bug("the good state of the session with a disk could not be made", 0);
    if (!state_session(rom, &adf, images[2], false, true)) bug("the good state of the session with an appended disk could not be made", 0);
    // The disk registered after the load and the one passed to p6_load are the same manifest: the machine, and so its state, is the same byte for byte.
    if (images[2].good != images[1].good || images[2].bound != images[1].bound) bug("a disk appended after p6_load made another state than the same disk passed to it", 0);
    images[0].foreign = images[1].good;
    images[1].foreign = images[0].good;
    images[2].foreign = images[0].good;
    if (g_writable) fuzz_export(rng, rom, adf, iterations);

    std::map<int, unsigned> histogram;
    unsigned accepted_by_kind[kStateKindCount] = {}, refused_by_kind[kStateKindCount] = {};
    unsigned unit_checks = 0, accepted_runs = 0, resyncs = 0;
    for (unsigned session = 0; session < 3; ++session) {
        StateImage &image = images[session];
        if (!state_session(rom, session ? &adf : nullptr, image, true, session == 2)) bug("a session could not be started again", 0);
        // The machine is the good state's at the top of every iteration; the loop puts it back whenever an attempt or a unit changed it.
        for (uint64_t i = 0; i < iterations; ++i) {
            const uint64_t tag = session * iterations + i;
            const unsigned kind = rng.below(kStateKindCount);
            Candidate c = mutate_state(image, rng, kind);
            const uint64_t length = c.null_buffer ? 1 + rng.below(1000) : c.length != ~uint64_t(0) ? c.length : c.bytes.size();
            error = fresh_error();
            const p6_result r = p6_state_load(c.null_buffer ? nullptr : c.bytes.data(), length, &error);
            ++histogram[int(r)];
            if (!message_is_bounded(error)) bug("unbounded message", tag);
            if (r == P6_ERROR_INTERNAL) bug("an attempt faulted the image", tag);
            if (c.null_buffer) {
                if (r != P6_ERROR_BAD_ARGUMENT) bug("a null buffer with a length was not BAD_ARGUMENT", tag);
                ++refused_by_kind[kind];
                continue;
            }
            if (r != P6_OK && r != P6_ERROR_CORRUPT_STATE && r != P6_ERROR_INCOMPATIBLE) {
                char text[96];
                snprintf(text, sizeof text, "a load result outside the documented set: %d (%s)", int(r), c.kind);
                bug(text, tag);
            }
            if (r != P6_OK) {
                ++refused_by_kind[kind];
                if (!error.message[0]) bug("a refusal without a message", tag);
                // A refusal left the machine alone: saving it again gives the good state, byte for byte.
                std::vector<uint8_t> again;
                uint64_t bound = 0;
                if (!state_save(again, &bound)) bug("the image could not be saved after a refusal", tag);
                if (getenv("P6_FUZZ_SELFTEST")) again[again.size() / 2] ^= 1;      // self-test of the harness: the comparison below has to fail
                if (again != image.good) bug("a refused state changed the machine", tag);
                if (bound != image.bound) bug("the bound moved after a refusal", tag);
                if (rng.below(8) == 0) {
                    // And it runs on as a machine that never saw the attempt: one unit, and the state and the samples after it are the reference's.
                    std::vector<uint8_t> pcm, after;
                    if (state_unit(&pcm, &error) != P6_OK) bug("a unit failed after a refusal", tag);
                    if (!state_save(after, nullptr)) bug("the image could not be saved after a unit that followed a refusal", tag);
                    if (pcm != image.reference_pcm) bug("the samples of the unit after a refusal are not the control's", tag);
                    if (after != image.reference) {
                        size_t at = p6_state::kHeaderSize;      // the header holds the payload checksum, which differs whenever anything does
                        while (at < after.size() && at < image.reference.size() && after[at] == image.reference[at]) ++at;
                        unsigned chunk = 0;
                        while (chunk + 1 < kStateChunks && at >= image.chunk_at[chunk + 1]) ++chunk;
                        fprintf(stderr, "first differing payload byte of the state: %zu in chunk %u (sizes %zu and %zu)\n", at, chunk, after.size(), image.reference.size());
                        bug("the unit after a refusal is not the control's", tag);
                    }
                    ++unit_checks;
                    error = fresh_error();
                    if (p6_state_load(image.good.data(), image.good.size(), &error) != P6_OK) bug("the good state was refused after a unit", tag);
                    ++resyncs;
                }
                continue;
            }
            if (kind == 2) bug("a state with flipped bits and its old checksums was accepted", tag);
            ++accepted_by_kind[kind];
            // An accepted state is a machine, however odd (a flipped byte of RAM): it has to run, and the good state has to bring the image back.
            if (rng.below(3) == 0) {
                if (state_unit(nullptr, &error) != P6_OK) {
                    char text[200];
                    snprintf(text, sizeof text, "an accepted state (%s) left an image that cannot run a unit: %s", c.kind, error.message);
                    bug(text, tag);
                }
                ++accepted_runs;
            }
            error = fresh_error();
            if (p6_state_load(image.good.data(), image.good.size(), &error) != P6_OK) bug("the good state was refused after an accepted attempt", tag);
            ++resyncs;
            std::vector<uint8_t> back;
            if (!state_save(back, nullptr) || back != image.good) bug("the good state did not give itself back", tag);
        }
        if (p6_unload(&error) != P6_OK) bug("unload", 0);
    }
    p6_shutdown(nullptr);
    for (const auto &entry : histogram) printf("state result %d: %u\n", entry.first, entry.second);
    for (unsigned k = 0; k < kStateKindCount; ++k) printf("state kind %s: accepted %u refused %u\n", kStateKinds[k], accepted_by_kind[k], refused_by_kind[k]);
    printf("state unit checks %u, accepted states that ran %u, resyncs %u, bound %llu/%llu\n", unit_checks, accepted_runs, resyncs,
           (unsigned long long)images[0].bound, (unsigned long long)images[1].bound);
    return 0;
}

// --- T29d: hard files with a hostile Rigid Disk Block -------------------------------------------------------------------------------------------------------------------
constexpr uint32_t kBlock = 512;
constexpr uint32_t kRdbIds[] = {0x5244534B, 0x50415254, 0x46534844, 0x4C534547};      // RDSK PART FSHD LSEG
constexpr uint32_t kMountResults = 0x5200;          // tests/hdf/hdf_rom.py: MOUNT_RES, a long per partition asked for (8), then DONE
constexpr uint32_t kMountAsked = 8, kDone = 0x444F4E45;

uint32_t get_be(const std::vector<uint8_t> &image, size_t at) { return uint32_t(image[at]) << 24 | uint32_t(image[at + 1]) << 16 | uint32_t(image[at + 2]) << 8 | image[at + 3]; }
void put_be(std::vector<uint8_t> &image, size_t at, uint32_t v) { for (int i = 0; i < 4; ++i) image[at + i] = uint8_t(v >> (24 - 8 * i)); }

bool is_rdb_block(const std::vector<uint8_t> &image, size_t block)
{
    if ((block + 1) * kBlock > image.size()) return false;
    const uint32_t id = get_be(image, block * kBlock);
    for (uint32_t known : kRdbIds) if (id == known) return true;
    return false;
}

// "The longs of the block add up to zero", over as many longs as the block says it has (at most the block): what rdb_checksum asks.
void fix_checksum(std::vector<uint8_t> &image, size_t block)
{
    const size_t base = block * kBlock;
    uint32_t longs = get_be(image, base + 4);
    if (longs < 4 || longs > kBlock / 4) longs = kBlock / 4;
    put_be(image, base + 8, 0);
    uint32_t sum = 0;
    for (uint32_t i = 0; i < longs; ++i) sum += get_be(image, base + 4 * i);
    put_be(image, base + 8, 0u - sum);
}

std::vector<uint8_t> mutate_hard_file(const std::vector<uint8_t> &good, Random &rng)
{
    std::vector<uint8_t> image = good;
    const size_t blocks = image.size() / kBlock;
    std::vector<size_t> rdb;
    for (size_t b = 0; b < blocks && b < 64; ++b) if (is_rdb_block(image, b)) rdb.push_back(b);
    if (rdb.empty()) rdb.push_back(0);
    for (unsigned edits = 1 + rng.below(4); edits--;) {
        const size_t block = rdb[rng.below(uint32_t(rdb.size()))];
        const size_t base = block * kBlock;
        if (base + kBlock > image.size()) continue;
        switch (rng.below(10)) {
        case 0: image[base + rng.below(kBlock)] ^= uint8_t(1u << rng.below(8)); break;
        case 1: put_be(image, base + 4 * rng.below(kBlock / 4), uint32_t(kBoundary[rng.below(sizeof kBoundary / sizeof kBoundary[0])])); break;
        case 2: {       // a link of the lists (next, partition list, file system list, first load segment, the block size field) sent elsewhere
            static const unsigned fields[] = {1, 4, 7, 8, 18, 9, 16};
            const uint32_t target = rng.coin() ? uint32_t(rng.below(uint32_t(blocks) + 2)) : (rng.coin() ? 0xFFFFFFFFu : uint32_t(rng.next()));
            put_be(image, base + 4 * fields[rng.below(sizeof fields / sizeof fields[0])], target);
            break;
        }
        case 3: {       // two blocks swapped
            const size_t other = rng.below(uint32_t(std::min<size_t>(blocks, 64)));
            for (uint32_t i = 0; i < kBlock; ++i) std::swap(image[base + i], image[other * kBlock + i]);
            break;
        }
        case 4: memset(&image[base], 0, kBlock); break;
        case 5: { const size_t other = rng.below(uint32_t(std::min<size_t>(blocks, 64))); memcpy(&image[other * kBlock], &image[base], kBlock); break; }
        case 6: {       // a run of random bytes
            const uint32_t at = rng.below(kBlock), length = 1 + rng.below(std::min<uint32_t>(32, kBlock - at));
            for (uint32_t i = 0; i < length; ++i) image[base + at + i] = uint8_t(rng.next());
            break;
        }
        case 7: if (blocks > 4) image.resize((blocks - 1 - rng.below(uint32_t(std::min<size_t>(blocks - 3, 8)))) * kBlock); break;      // cut off a few blocks
        case 8: image.resize(image.size() + kBlock * (1 + rng.below(3))); break;                                                        // zero blocks added
        default: put_be(image, base + 4, rng.below(4) == 0 ? uint32_t(rng.next()) : rng.below(200)); break;                             // how many longs the block says it has
        }
    }
    if (rng.below(10) < 7)
        for (size_t b = 0; b < image.size() / kBlock && b < 64; ++b) if (is_rdb_block(image, b)) fix_checksum(image, b);
    if (rng.below(12) == 0 && image.size() > 1) image.resize(image.size() - 1 - rng.below(kBlock - 1));          // not a whole number of blocks: refused
    return image;
}

// ide: the hard files are on the IDE port of Gayle (T56: the A600 with p6_config.hdf_controller = IDE) and the guest is the micro-ROM of tests/hdf/ide_rom.py, which identifies the drive and reads and writes
// blocks through its registers; what the engine parses is the same hostile RDB (hdf_hd_open reads the geometry out of it), and the machine that did saves and loads back.
int fuzz_hardfile(const char *rom_path, uint64_t seed, uint64_t iterations, int image_count, char **image_paths, bool ide = false)
{
    Random rng(seed);
    const std::vector<uint8_t> rom = read_file(rom_path);
    std::vector<std::vector<uint8_t>> pool;
    for (int i = 0; i < image_count; ++i) pool.push_back(read_file(image_paths[i]));
    p6_config config = valid_config();
    if (ide) {
        config.machine = P6_MACHINE_A600;
        config.hdf_controller = P6_HDF_CONTROLLER_IDE;
    }
    p6_error error = fresh_error();
    if (p6_init(&config, &error) != P6_OK) bug("p6_init", 0);
    std::map<int, unsigned> histogram, mounts;
    unsigned ran = 0, states = 0;
    std::vector<uint8_t> buffer;
    for (uint64_t i = 0; i < iterations; ++i) {
        const std::vector<uint8_t> image = mutate_hard_file(pool[rng.below(uint32_t(pool.size()))], rng);
        p6_media m{};
        m.type = P6_MEDIA_HDF;
        m.image = p6_buffer{image.data(), image.size()};
        p6_sha256_hash(image.data(), image.size(), m.content_id);
        const bool expected = !image.empty() && image.size() % kBlock == 0;
        error = fresh_error();
        const p6_result r = p6_load(p6_buffer{rom.data(), rom.size()}, &m, 1, &error);
        ++histogram[int(r)];
        if (!message_is_bounded(error)) bug("unbounded message", i);
        if ((r == P6_OK) != expected) bug(r == P6_OK ? "a hard file that is not whole blocks was accepted" : "a hard file of whole blocks was refused", i);
        if (r != P6_OK) {
            if (r != P6_ERROR_WRONG_MEDIA && r != P6_ERROR_CAPACITY && r != P6_ERROR_BAD_ARGUMENT) bug("a hard file result outside the documented set", i);
            if (!error.message[0]) bug("error without a message", i);
            continue;
        }
        ++ran;
        p6_input input{};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        p6_frame frame{};
        for (unsigned unit = 0; unit < 4; ++unit) {
            frame = p6_frame{};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            error = fresh_error();
            if (p6_run_frame(&input, 0, &frame, &error) != P6_OK) bug("a unit with a mutated hard file failed", i);
        }
        // What the filesystem answered for each partition asked for: the guest stored it in Chip RAM.
        p6_memory_region regions[8];
        uint32_t count = 0;
        if (p6_memory_regions(regions, 8, &count, nullptr) != P6_OK) bug("p6_memory_regions", i);
        for (uint32_t k = 0; k < count && !ide; ++k) {
            if (regions[k].type != P6_REGION_CHIP_RAM) continue;
            const uint8_t *chip = static_cast<const uint8_t *>(regions[k].data);
            if (regions[k].bytes < kMountResults + 4 * (kMountAsked + 1)) bug("Chip RAM too small", i);
            uint32_t done = uint32_t(chip[kMountResults + 4 * kMountAsked]) << 24 | uint32_t(chip[kMountResults + 4 * kMountAsked + 1]) << 16 |
                            uint32_t(chip[kMountResults + 4 * kMountAsked + 2]) << 8 | chip[kMountResults + 4 * kMountAsked + 3];
            if (done != kDone) bug("the guest did not finish asking for the partitions", i);
            for (uint32_t n = 0; n < kMountAsked; ++n) {
                const int32_t answer = int32_t(uint32_t(chip[kMountResults + 4 * n]) << 24 | uint32_t(chip[kMountResults + 4 * n + 1]) << 16 |
                                               uint32_t(chip[kMountResults + 4 * n + 2]) << 8 | chip[kMountResults + 4 * n + 3]);
                ++mounts[answer < -2 ? -3 : answer > 3 ? 4 : answer];
            }
        }
        // The state of a machine that has parsed a hostile disk saves and loads back (T29c), and the machine goes on.
        uint64_t bound = 0, written = 0;
        error = fresh_error();
        if (p6_state_bound(&bound, &error) != P6_OK) bug("p6_state_bound with a mutated hard file", i);
        buffer.resize(size_t(bound));
        error = fresh_error();
        if (p6_state_save(buffer.data(), buffer.size(), &written, &error) != P6_OK) bug("p6_state_save with a mutated hard file", i);
        error = fresh_error();
        if (p6_state_load(buffer.data(), written, &error) != P6_OK) bug("a state of a mutated hard file did not load back", i);
        frame = p6_frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        error = fresh_error();
        if (p6_run_frame(&input, 0, &frame, &error) != P6_OK) bug("a unit after the state load failed", i);
        ++states;
        error = fresh_error();
        if (p6_unload(&error) != P6_OK) bug("p6_unload", i);
    }
    for (const auto &entry : histogram) printf("hardfile result %d: %u\n", entry.first, entry.second);
    for (const auto &entry : mounts) printf("hardfile mount answer %d: %u\n", entry.first, entry.second);
    printf("hardfile loaded %u states %u\n", ran, states);
    p6_shutdown(nullptr);
    return 0;
}

// --- T57: extended ADF (and FDI) with a hostile header ---------------------------------------------------------------------------------------------------------------
// The floppy images the bridge accepts are decided by ports/portable/media/trackdisk.h; this is the same decision written again from the format descriptions in the header of that file
// (an independent oracle: a different walk over the table, 64-bit offsets), so that a validator that is too lax or too strict is a report. What the validator accepts is then read by
// the engine under the sanitizers: a micro-ROM walks the head over the first cylinders and reads both heads of each with disk DMA.
constexpr uint64_t kMaxTrackDisk = 3u << 20;

uint64_t be_n(const std::vector<uint8_t> &v, uint64_t at, unsigned n)
{
    uint64_t r = 0;
    for (unsigned i = 0; i < n; ++i) r = r << 8 | v[at + i];
    return r;
}

uint32_t crc32_bitwise(const uint8_t *p, size_t n)
{
    uint32_t crc = ~0u;
    for (size_t i = 0; i < n; ++i) {
        crc ^= p[i];
        for (int k = 0; k < 8; ++k) crc = crc & 1 ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
    }
    return ~crc;
}

// FDI (T57b): the header of 512 bytes, and tracks that are empty, Amiga (eleven sectors) or low level (four streams inside the track) and lie in the file.
bool oracle_accepts_fdi(const std::vector<uint8_t> &v)
{
    const uint64_t size = v.size();
    if (size > kMaxTrackDisk || size < 512 || (v[140] != 1 && v[140] != 2)) return false;
    if (v[140] * 256 + v[141] >= 513 && crc32_bitwise(v.data(), 508) != be_n(v, 508, 4)) return false;
    const uint64_t tracks = (be_n(v, 142, 2) + 1) * (uint64_t(v[144]) + 1);
    if (tracks > 166) return false;
    uint64_t at = 512;
    for (uint64_t t = 0; t < tracks; ++t) {
        const uint64_t kind = v[152 + 2 * t], sz = v[153 + 2 * t];
        uint64_t length;
        if (kind == 1) { length = (sz & 15) * 512; if ((sz & 15) != 11 || (sz >> 4) > 10) return false; }
        else if ((kind & 0xC0) == 0x80) length = (((kind & 0x3F) << 8) | sz) * 256;
        else if (kind == 0) length = sz * 256;
        else return false;
        if (at + length > size) return false;
        if ((kind & 0xC0) == 0x80) {
            if (length < 16) return false;
            const uint64_t pulses = be_n(v, at, 4);
            if (pulses < 2 || pulses > 262144) return false;
            uint64_t desc[4], pos = 16;
            for (int k = 0; k < 4; ++k) desc[k] = be_n(v, at + 4 + 3 * k, 3);
            const bool both = desc[1] != 0 && desc[2] != 0;
            for (int k = 0; k < 4; ++k) {
                if ((k == 1 || k == 2) && !both) continue;
                const uint64_t mode = desc[k] >> 22, len = desc[k] & 0x3FFFFF;
                if (pos > length || mode > 1 || len > length - pos || (k == 0 && len == 0)) return false;
                if (mode == 0 && pulses * 2 <= len && (len < pulses * 4 || (pos & 3))) return false;
                pos += len;
            }
        }
        at += length;
    }
    return true;
}

// True when `v` is a floppy image a double-density drive can be given: a plain ADF, an extended ADF whose every track lies inside the file, or an FDI of the tracks the core reads.
bool oracle_accepts_floppy(const std::vector<uint8_t> &v)
{
    const uint64_t size = v.size();
    if (size >= 25 && !memcmp(v.data(), "Formatted Disk Image file", 25)) return oracle_accepts_fdi(v);
    const auto starts = [&](const char *magic) { return size >= 8 && !memcmp(v.data(), magic, 8); };
    static const uint8_t hunk[8] = {0, 0, 3, 0xF3, 0, 0, 0, 0};
    if (size >= 8 && !memcmp(v.data(), hunk, 8)) return false;
    if (starts("UAE-1ADF")) {
        if (size > kMaxTrackDisk || size < 12) return false;
        if (be_n(v, 8, 2) != 0) return false;
        const uint64_t tracks = be_n(v, 10, 2);
        if (tracks < 1 || tracks > 166 || 12 + 12 * tracks > size) return false;
        uint64_t at = 12 + 12 * tracks;
        for (uint64_t t = 0; t < tracks; ++t) {
            const uint64_t row = 12 + 12 * t;
            const uint64_t type = v[row + 3], length = be_n(v, row + 4, 4), bits = be_n(v, row + 8, 4);
            if (be_n(v, row, 2) != 0 || length > 0xFFFFFF || bits > 0xFFFFFF || (v[row + 4] | v[row + 8]) != 0) return false;
            if (type > 1 || length > 20000 || at + length > size) return false;
            if (type == 0 ? length < 5632 : (bits < 16 || (bits + 7) / 8 > length)) return false;
            at += length;
        }
        return true;
    }
    if (starts("UAE--ADF")) {
        if (size > kMaxTrackDisk || size < 648) return false;
        uint64_t at = 648;
        for (uint64_t t = 0; t < 160; ++t) {
            const uint64_t sync = be_n(v, 8 + 4 * t, 2), length = be_n(v, 10 + 4 * t, 2);
            if (length > 20000 || at + length > size) return false;
            if (sync == 0 ? length < 5632 : length < 2) return false;
            at += length;
        }
        return true;
    }
    return size == 901120;
}

// IPF (T58): a list of tags {id, size, crc, body}, a DATA tag followed by the data of its track. Whether the bridge opens a mutated IPF is decided by the reader and checked against the
// oracle of tests/fuzz/test_ipf.py (the decoder on its own); here what it opens is read by the engine. The mutations reach the descriptions of the blocks: bytes of the data, a word set to
// a boundary value, the file cut or extended, and then every checksum put right again (the walk below) most of the time.
bool is_ipf(const std::vector<uint8_t> &v) { return v.size() >= 12 && !memcmp(v.data(), "CAPS", 4); }

void fix_ipf_checksums(std::vector<uint8_t> &v)
{
    uint64_t pos = 0;
    while (pos + 12 <= v.size()) {
        const uint64_t size = be_n(v, pos + 4, 4);
        if (size < 12 || pos + size > v.size()) return;
        uint64_t end = pos + size;
        if (!memcmp(&v[pos], "DATA", 4) && size == 28) {
            const uint64_t length = be_n(v, pos + 12, 4);
            if (end + length <= v.size()) {
                const uint32_t crc = crc32_bitwise(&v[end], size_t(length));
                for (int i = 0; i < 4; ++i) v[pos + 20 + i] = uint8_t(crc >> (24 - 8 * i));
                end += length;
            }
        }
        memset(&v[pos + 8], 0, 4);
        const uint32_t crc = crc32_bitwise(&v[pos], size_t(size));
        for (int i = 0; i < 4; ++i) v[pos + 8 + i] = uint8_t(crc >> (24 - 8 * i));
        pos = end;
    }
}

std::vector<uint8_t> mutate_ipf(std::vector<uint8_t> image, Random &rng)
{
    static const uint32_t boundary[] = {0, 1, 7, 8, 15, 16, 0xFF, 0x100, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000u, 0xFFFFFFF0u, 0xFFFFFFFFu, 160000, 160001, 262144, 168, 83, 82};
    for (unsigned edits = 1 + rng.below(4); edits--;) {
        switch (rng.below(6)) {
        case 0: image[rng.below(uint32_t(image.size()))] ^= uint8_t(1u << rng.below(8)); break;
        case 1: if (image.size() > 300) { const size_t at = 108 + rng.below(uint32_t(image.size() - 108)); for (unsigned i = 0; i < 1 + rng.below(8) && at + i < image.size(); ++i) image[at + i] = uint8_t(rng.next()); } break;
        case 2: if (image.size() > 300) { const size_t at = 4 * rng.below(uint32_t(image.size() / 4 - 1)); const uint32_t v = boundary[rng.below(sizeof boundary / sizeof boundary[0])]; for (int i = 0; i < 4; ++i) image[at + i] = uint8_t(v >> (24 - 8 * i)); } break;
        case 3: image.resize(image.size() - 1 - rng.below(uint32_t(std::min<size_t>(image.size() - 1, 4000)))); break;
        case 4: image.insert(image.end(), 1 + rng.below(64), uint8_t(rng.next())); break;
        default: if (image.size() > 400) { const size_t a = 108 + rng.below(uint32_t(image.size() - 300)), b = 108 + rng.below(uint32_t(image.size() - 300)); for (unsigned i = 0; i < 32; ++i) image[a + i] = image[b + i]; } break;
        }
        if (image.size() < 12) image.resize(12);
    }
    if (rng.below(10) < 8) fix_ipf_checksums(image);
    return image;
}

std::vector<uint8_t> mutate_floppy(const std::vector<uint8_t> &good, Random &rng)
{
    if (is_ipf(good)) return mutate_ipf(good, rng);
    std::vector<uint8_t> image = good;
    const bool ext2 = image.size() > 12 && !memcmp(image.data(), "UAE-1ADF", 8);
    const bool ext1 = image.size() > 648 && !memcmp(image.data(), "UAE--ADF", 8);
    const bool fdi = image.size() >= 512 && !memcmp(image.data(), "Formatted Disk Image file", 25);
    const size_t table = ext2 ? 12 + 12 * std::min<size_t>(be_n(image, 10, 2), 166) : ext1 ? 648 : fdi ? 512 : 0;
    if (fdi) {
        for (unsigned edits = 1 + rng.below(4); edits--;) {
            switch (rng.below(8)) {
            case 0: image[rng.below(512)] ^= uint8_t(1u << rng.below(8)); break;                                                   // a bit of the header, the version, the geometry or the track table
            case 1: { const size_t at = 152 + 2 * rng.below(24); image[at + rng.below(2)] = uint8_t(rng.coin() ? rng.next() : kBoundary[rng.below(6)]); break; }       // a track entry
            case 2: { const size_t at = 140 + rng.below(6); image[at] = uint8_t(rng.coin() ? rng.next() : kBoundary[rng.below(6)]); break; }                           // version, geometry, flags
            case 3: if (image.size() > 640) image[512 + rng.below(uint32_t(image.size() - 512))] ^= uint8_t(1 + rng.below(255)); break;                                 // a byte of the track data (descriptors, streams, sectors)
            case 4: if (image.size() > 640) { const size_t at = 512 + rng.below(uint32_t(image.size() - 512)); for (unsigned i = 0; i < 1 + rng.below(16) && at + i < image.size(); ++i) image[at + i] = uint8_t(rng.next()); } break;
            case 5: image.resize(image.size() - 1 - rng.below(uint32_t(std::min<size_t>(image.size() - 1, 70000)))); if (image.size() < 512) image.resize(512); break;
            case 6: image.insert(image.end(), 1 + rng.below(64), uint8_t(rng.next())); break;
            default: if (image.size() > 1024) { const size_t a = 512 + 256 * rng.below(uint32_t((image.size() - 512) / 256)), b = 512 + 256 * rng.below(uint32_t((image.size() - 512) / 256)); if (a + 256 <= image.size() && b + 256 <= image.size()) for (unsigned i = 0; i < 256; ++i) image[a + i] = image[b + i]; } break;
            }
        }
        if (image.size() >= 512 && rng.below(10) < 7) {      // the checksum of a 2.1 header put right again: the parser gets past it
            const uint32_t crc = crc32_bitwise(image.data(), 508);
            for (int i = 0; i < 4; ++i) image[508 + i] = uint8_t(crc >> (24 - 8 * i));
        }
        return image;
    }
    for (unsigned edits = rng.below(4); edits--;) {
        switch (rng.below(9)) {
        case 0: if (table && table <= image.size()) image[rng.below(uint32_t(table))] ^= uint8_t(1u << rng.below(8)); break;                  // a bit of the header or the table
        case 1: if (image.size() > table) image[table + rng.below(uint32_t(image.size() - table))] ^= uint8_t(1 + rng.below(255)); break;      // a byte of the data: still a valid image
        case 2: {       // a field of a row of the table set to a boundary value
            if (ext2) {
                const size_t row = 12 + 12 * rng.below(uint32_t(std::min<size_t>(be_n(image, 10, 2), 166)));
                static const unsigned offsets[] = {0, 2, 3, 4, 5, 8, 9};
                const size_t at = row + offsets[rng.below(7)];
                if (at + 4 <= image.size()) for (unsigned i = 0; i < 3; ++i) image[at + i] = uint8_t(kBoundary[rng.below(12)] >> (8 * (2 - i)));
            } else if (ext1) {
                const size_t at = 8 + 4 * rng.below(160);
                image[at] = uint8_t(kBoundary[rng.below(12)]); image[at + 1] = uint8_t(kBoundary[rng.below(12)] >> 8); image[at + 2] = uint8_t(rng.next()); image[at + 3] = uint8_t(rng.next());
            }
            break;
        }
        case 3: if (ext2) { image[10] = uint8_t(rng.next()); image[11] = uint8_t(rng.coin() ? 160 : rng.next()); } break;                      // how many tracks the file says it has
        case 4: if (image.size() > 16) image.resize(rng.below(4) == 0 ? table + rng.below(64) : image.size() - 1 - rng.below(uint32_t(std::min<size_t>(image.size() - 1, 40000)))); break;      // cut off
        case 5: image.insert(image.end(), 1 + rng.below(64), uint8_t(rng.next())); break;                                                       // bytes added (the end of the last track is not the end of the file)
        case 6: if (ext2) {         // two rows of the table swapped, or a row copied over another
            const size_t a = 12 + 12 * rng.below(160), b = 12 + 12 * rng.below(160);
            if (a + 12 <= image.size() && b + 12 <= image.size()) for (unsigned i = 0; i < 12; ++i) { if (rng.coin()) std::swap(image[a + i], image[b + i]); else image[a + i] = image[b + i]; }
        } break;
        case 7: if (image.size() >= 8 && rng.below(6) == 0) memcpy(image.data(), rng.coin() ? "UAE-1ADF" : "UAE--ADF", 8); break;                // the other magic over the same bytes
        default: if (image.size() > table + 64) { const size_t at = table + rng.below(uint32_t(image.size() - table - 32)); for (unsigned i = 0; i < 1 + rng.below(32); ++i) image[at + i] = uint8_t(rng.next()); } break;
        }
    }
    return image;
}

int fuzz_trackdisk(const char *rom_path, uint64_t seed, uint64_t iterations, int image_count, char **image_paths)
{
    Random rng(seed);
    const std::vector<uint8_t> rom = read_file(rom_path);
    std::vector<std::vector<uint8_t>> pool;
    for (int i = 0; i < image_count; ++i) pool.push_back(read_file(image_paths[i]));
    p6_config config = valid_config();
    config.floppy_speed = 5;                    // turbo: a read ends in two lines, so the guest covers many tracks in a few units
    p6_error error = fresh_error();
    if (p6_init(&config, &error) != P6_OK) bug("p6_init", 0);
    std::map<int, unsigned> histogram;
    unsigned ran = 0, states = 0, accepted_extended = 0, accepted_ipf = 0;
    std::vector<uint8_t> buffer;
    for (uint64_t i = 0; i < iterations; ++i) {
        const std::vector<uint8_t> image = rng.below(8) == 0 ? pool[rng.below(uint32_t(pool.size()))] : mutate_floppy(pool[rng.below(uint32_t(pool.size()))], rng);
        p6_media m{};
        m.type = P6_MEDIA_ADF;
        m.image = p6_buffer{image.data(), image.size()};
        p6_sha256_hash(image.data(), image.size(), m.content_id);
        const bool ipf = is_ipf(image);           // the reader of an IPF is the decoder, whose verdict tests/fuzz/test_ipf.py checks against its own oracle
        const bool expected = ipf ? true : oracle_accepts_floppy(image);
        error = fresh_error();
        const p6_result r = p6_load(p6_buffer{rom.data(), rom.size()}, &m, 1, &error);
        ++histogram[int(r)];
        if (!message_is_bounded(error)) bug("unbounded message", i);
        if (!ipf && (r == P6_OK) != expected) bug(r == P6_OK ? "a floppy image the oracle refuses was accepted" : "a floppy image the oracle accepts was refused", i);
        if (r != P6_OK) {
            if (r != P6_ERROR_WRONG_MEDIA) bug("a floppy result outside the documented set", i);
            if (!error.message[0]) bug("error without a message", i);
            continue;
        }
        ++ran;
        if (ipf) ++accepted_ipf;
        if (image.size() != 901120) ++accepted_extended;
        p6_media_event event{};
        event.abi_version = P6_ABI_VERSION;
        event.struct_size = sizeof event;
        event.type = P6_MEDIA_EVENT_MOUNT;
        event.sequence = 1;
        error = fresh_error();
        if (p6_media_event_enqueue(&event, &error) != P6_OK) bug("the boot disk could not be mounted", i);
        p6_input input{};
        input.abi_version = P6_ABI_VERSION;
        input.struct_size = sizeof input;
        for (unsigned unit = 0; unit < 52; ++unit) {
            p6_frame frame{};
            frame.abi_version = P6_ABI_VERSION;
            frame.struct_size = sizeof frame;
            error = fresh_error();
            if (p6_run_frame(&input, 0, &frame, &error) != P6_OK) bug("a unit with an extended ADF failed", i);
        }
        uint64_t bound = 0, saved = 0;
        error = fresh_error();
        if (p6_state_bound(&bound, &error) != P6_OK) bug("p6_state_bound with an extended ADF", i);
        buffer.resize(size_t(bound));
        error = fresh_error();
        if (p6_state_save(buffer.data(), buffer.size(), &saved, &error) != P6_OK) bug("p6_state_save with an extended ADF", i);
        error = fresh_error();
        if (p6_state_load(buffer.data(), saved, &error) != P6_OK) bug("a state with an extended ADF did not load back", i);
        p6_frame frame{};
        frame.abi_version = P6_ABI_VERSION;
        frame.struct_size = sizeof frame;
        error = fresh_error();
        if (p6_run_frame(&input, 0, &frame, &error) != P6_OK) bug("a unit after the state load failed", i);
        ++states;
        error = fresh_error();
        if (p6_unload(&error) != P6_OK) bug("p6_unload", i);
    }
    for (const auto &entry : histogram) printf("trackdisk result %d: %u\n", entry.first, entry.second);
    printf("trackdisk loaded %u extended %u states %u\n", ran, accepted_extended, states);
    printf("trackdisk ipf loaded %u\n", accepted_ipf);
    p6_shutdown(nullptr);
    return 0;
}

}  // namespace

// The machine a name of the command line stands for (the state target takes any machine, T50), 0 when it is none.
static uint32_t state_machine(const char *name)
{
    static const struct { const char *name; uint32_t machine; } machines[] = {
        {"a600", P6_MACHINE_A600}, {"a1200", P6_MACHINE_A1200}, {"a500og", P6_MACHINE_A500OG}, {"a500plus", P6_MACHINE_A500PLUS},
        {"a1000", P6_MACHINE_A1000}, {"a2000og", P6_MACHINE_A2000OG}, {"a2000", P6_MACHINE_A2000},
    };
    for (const auto &m : machines)
        if (!strcmp(name, m.name)) return m.machine;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 4 && !strcmp(argv[1], "config")) return fuzz_config(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 5 && !strcmp(argv[1], "firmware")) return fuzz_firmware(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10));
    if (argc == 5 && !strcmp(argv[1], "media")) return fuzz_media(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10));
    if ((argc == 5 || (argc == 6 && state_machine(argv[5]))) && (!strcmp(argv[1], "state") || !strcmp(argv[1], "state-writable"))) {
        g_writable = !strcmp(argv[1], "state-writable");
        return fuzz_state(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10), argc == 5 ? P6_MACHINE_A500 : state_machine(argv[5]));
    }
    if (argc >= 6 && !strcmp(argv[1], "hardfile")) return fuzz_hardfile(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10), argc - 5, argv + 5);
    if (argc >= 6 && !strcmp(argv[1], "hardfile-ide")) return fuzz_hardfile(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10), argc - 5, argv + 5, true);
    if (argc >= 6 && !strcmp(argv[1], "trackdisk")) return fuzz_trackdisk(argv[2], strtoull(argv[3], nullptr, 10), strtoull(argv[4], nullptr, 10), argc - 5, argv + 5);
    if (argc == 4 && !strcmp(argv[1], "blob")) return fuzz_blob(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    fprintf(stderr, "usage: %s config SEED N | firmware ROM SEED N | media ROM SEED N | state ROM SEED N [a600|a1200|a500og|a500plus|a1000|a2000og|a2000] | state-writable ROM SEED N | hardfile[-ide] ROM SEED N IMAGE... | trackdisk ROM SEED N IMAGE... | blob SEED N\n", argv[0]);
    return 64;
}
