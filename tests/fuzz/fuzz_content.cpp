// Deterministic mutation fuzzing of what the libretro adapter takes from the frontend's side (T17): playlists, the frontend's VFS and the option
// values. No libFuzzer: a fixed-seed xorshift generator, so the same seed and iteration count is the same run and a fixed bug is a seed. It links
// no engine and no bridge: adapters/libretro/content.h and options.h are header-only, and this target is compiled with the sanitizers by
// tests/fuzz/test_fuzz.py (ContentFuzzTests).
//   fuzz_content m3u SEED N         parse_m3u on N hostile playlists, against an independent oracle (accepted within the caps, or refused with a message)
//   fuzz_content vfs SEED N         FileReader::read through a hostile VFS (lying sizes, short reads, failures, endless streams): never beyond the cap
//   fuzz_content options SEED N     read_options with hostile values from the frontend: only the exact value names of the schema are accepted
//   fuzz_content hdz SEED N         decode_hard_file (T63a) on hostile .hdf and .hdz files: gzip members cut, flipped, bombs, wrong sizes; never beyond the cap
//   fuzz_content volume SEED N      the volume builder (T64a) on hostile trees, LHA archives and ZIP files: flipped, cut, claiming more than the caps, with names that leave the volume
//   fuzz_content archive SEED N DIR the archives of disks (T59: sevenzip.h, archive.h) on the seed archives in DIR (a ZIP, 7z and RP9 of ADFs, written by test_fuzz.py): flipped, cut, with the
//                                   7z checksums put right again, random LZMA streams and absurd claims, members by name, RP9 manifests of random XML, and the order of a set
//   fuzz_content model SEED N       the automatic model and the tags in the names (T76: model.h resolve_model) on random, near-miss, glued and hostile names (bytes, NUL, 100 KB, every tag
//                                   300 times), against an independent oracle written from the table of docs/options.md; pure (twice the same answer), never automatic, a choice made by
//                                   hand is never changed, and what is said is bounded
//   fuzz_content firmware SEED N    the Amiga Forever ROMs (T51: romkey.h decrypt_cloanto_rom) on encrypted and plain images with keys of every length, wrong keys, cut and flipped tags and
//                                   payloads of 0 to 600 KB, against an independent oracle (the XOR indexed by position modulo the key, not by a running counter); the input is never touched,
//                                   nothing comes out unless the result is Decrypted, a round trip gives the original back, and the version table (firmware.h) knows exactly its seven choices
// Output is a histogram per kind of mutation and per outcome; nothing else varies between runs.
#include "content.h"
#include "firmware.h"
#include "hdf.h"
#include "archive.h"
#include "model.h"
#include "options.h"
#include "romkey.h"
#include "volume.h"

#include <stdio.h>
#include <stdlib.h>
#include <algorithm>
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
    template <size_t N> const char *pick(const char *const (&list)[N]) { return list[below(N)]; }
};

[[noreturn]] void bug(const char *what, uint64_t iteration, const std::string &detail = std::string())
{
    fprintf(stderr, "FUZZ FAILURE at iteration %llu: %s%s%s\n", (unsigned long long)iteration, what, detail.empty() ? "" : ": ", detail.c_str());
    exit(1);
}

// Self-test of the harness: with this variable set the oracles are wrong on purpose, and the run has to fail.
bool selftest() { static const bool on = getenv("P6_FUZZ_SELFTEST") != nullptr; return on; }

// --- M3U ---------------------------------------------------------------------------------------------------------------------------------------

constexpr size_t kMaxEntries = 16;       // P6_MAX_MEDIA: what libretro_core.cpp hands to parse_m3u

const char *const kNames[] = {"a.adf", "Disk 2.ADF", "dir/disk3.adf", "../up.adf", "d\xC3\xA9sk.adf", "x", "disk.d64", ".adf", "a.b.c.adf", "tail/"};
const char *const kLabels[] = {"", "Disk 1", " padded ", "\xC3\xA9\xE2\x82\xAC", "has|bar", "||", "x"};
const char *const kPrefixes[] = {"", "/", "\\", "C:", "C:\\dir\\", "saf://tree/", "http://host/", "smb:/x", ":", "//", "a://", "\\\\server\\share\\"};
const char *const kDirectories[] = {"", "/", "/games/", "C:\\games\\", "saf://tree/", "rel/", "/d\xC3\xA9j\xC3\xA0 vu/"};

std::string valid_playlist(Random &rng)
{
    std::string text = rng.coin() ? "\xEF\xBB\xBF" : "";
    text += rng.coin() ? "#EXTM3U\n" : "";
    const unsigned count = 1 + rng.below(6);
    for (unsigned i = 0; i < count; ++i) {
        text += std::string(rng.pick(kPrefixes)) + rng.pick(kNames);
        if (rng.coin()) text += std::string("|") + rng.pick(kLabels);
        text += rng.coin() ? "\n" : rng.coin() ? "\r\n" : "\r";
        if (rng.below(5) == 0) text += "#SAVEDISK:\"save.adf\"\n";
    }
    return text;
}

struct Playlist1 {
    std::string kind;
    std::string text;
};

Playlist1 hostile_playlist(Random &rng)
{
    static const char *const kinds[] = {"random-bytes", "valid-flipped", "long-line", "path-length-boundary", "nul", "bom-middle", "cr-only", "bar", "savedisk",
                                        "many-entries", "huge-path", "path-forms", "utf8-invalid", "whitespace", "huge-file"};
    Playlist1 result;
    result.kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
    std::string &text = result.text;
    const std::string &kind = result.kind;
    if (kind == "random-bytes") {
        text.resize(rng.below(300));
        for (char &c : text) c = char(rng.below(256));
    } else if (kind == "valid-flipped") {
        text = valid_playlist(rng);
        for (unsigned n = 1 + rng.below(4); n; --n) {
            const size_t at = rng.below(uint32_t(text.size() + 1));
            switch (rng.below(3)) {
            case 0: if (at < text.size()) text[at] = char(rng.below(256)); break;
            case 1: text.insert(text.begin() + std::ptrdiff_t(at), char(rng.below(256))); break;
            default: if (at < text.size()) text.erase(at, 1 + rng.below(5)); break;
            }
        }
    } else if (kind == "long-line") {
        text = valid_playlist(rng) + std::string(rng.below(20000), rng.coin() ? 'a' : ' ') + (rng.coin() ? "\n" : "");
    } else if (kind == "path-length-boundary") {
        // The cap is on the path as written, before it is joined to the directory: 4095, 4096 and 4097 bytes, with and without the label.
        const size_t length = kMaxPathBytes - 1 + rng.below(3);
        text = std::string(length, 'p') + (rng.coin() ? "|label" : "") + "\n";
        if (rng.coin()) text = " \t" + text;
    } else if (kind == "nul") {
        text = valid_playlist(rng);
        text.insert(rng.below(uint32_t(text.size() + 1)), 1, '\0');
        if (rng.coin()) text += std::string("#comment") + '\0' + "\n";
    } else if (kind == "bom-middle") {
        text = valid_playlist(rng);
        text.insert(rng.below(uint32_t(text.size() + 1)), "\xEF\xBB\xBF");
        if (rng.coin()) text = "\xEF\xBB\xBF\xEF\xBB\xBF" + text;
    } else if (kind == "cr-only") {
        text = valid_playlist(rng);
        for (char &c : text) if (c == '\n') c = '\r';
        if (rng.coin()) text += "\r\r\n\n\r";
    } else if (kind == "bar") {
        for (unsigned n = 1 + rng.below(5); n; --n) {
            static const char *const shapes[] = {"|", "|label", "a.adf|", "a.adf||", "|||", "a.adf|b|c", " | ", "a.adf |  Label  ", "a.adf\t|\t"};
            text += std::string(rng.pick(shapes)) + "\n";
        }
    } else if (kind == "savedisk") {
        static const char *const lines[] = {"#SAVEDISK:", "#SAVEDISK:\"x.adf\"", "#SAVEDISK", "#savedisk:", " #SAVEDISK:x", "#SAVEDISK:x|y", "##SAVEDISK:", "a.adf|#SAVEDISK:"};
        for (unsigned n = 1 + rng.below(40); n; --n) text += std::string(rng.pick(lines)) + (rng.coin() ? "\n" : "\r\n");
        if (rng.coin()) text += "a.adf\n";
    } else if (kind == "many-entries") {
        const unsigned count = 14 + rng.below(30);      // around the cap of 16
        for (unsigned i = 0; i < count; ++i) text += "disk" + std::to_string(i) + ".adf\n";
    } else if (kind == "huge-path") {
        text = std::string(kMaxPathBytes + 1 + rng.below(60000), '/') + "\n";
    } else if (kind == "path-forms") {
        for (unsigned n = 1 + rng.below(8); n; --n) text += std::string(rng.pick(kPrefixes)) + rng.pick(kNames) + "\n";
    } else if (kind == "utf8-invalid") {
        static const char *const bad[] = {"\xC0\xAF", "\xC3", "\xED\xA0\x80", "\xF8\x88\x80\x80\x80", "\xFF\xFE", "\x80", "\xE2\x82", "\xF4\x90\x80\x80"};
        for (unsigned n = 1 + rng.below(6); n; --n) text += std::string("d") + rng.pick(bad) + ".adf" + (rng.coin() ? std::string("|") + rng.pick(bad) : "") + "\n";
    } else if (kind == "whitespace") {
        static const char *const blanks[] = {" ", "\t", "  \t ", "\v", "\f", "\xC2\xA0"};
        for (unsigned n = 1 + rng.below(10); n; --n) text += std::string(rng.pick(blanks)) + (rng.coin() ? rng.pick(kNames) : "") + rng.pick(blanks) + "\n";
    } else {
        // "huge-file": a playlist as big as the reader lets one through (kMaxPlaylistBytes), of short lines, comments or one endless line.
        const unsigned shape = rng.below(3);
        while (text.size() < kMaxPlaylistBytes) {
            if (shape == 0) text += "# a comment line of a certain length to fill the file up\n";
            else if (shape == 1) text += "d.adf\n";
            else text += std::string(1000, 'z');
        }
        text.resize(kMaxPlaylistBytes);
    }
    return result;
}

bool oracle_is_absolute(const std::string &path)
{
    if (path.empty()) return false;
    if (path[0] == '/' || path[0] == '\\') return true;
    if (path.size() >= 2 && path[1] == ':' && ((path[0] >= 'a' && path[0] <= 'z') || (path[0] >= 'A' && path[0] <= 'Z'))) return true;
    return path.find("://") != std::string::npos;
}

struct Verdict {
    bool accepted = true;
    std::vector<std::string> paths;
    unsigned save_disks = 0;
    unsigned error_line = 0;
};

// The contract of parse_m3u written again from its documentation: lines end at CR, LF or CRLF; a leading BOM is skipped; a NUL byte anywhere in a line
// (comments too) refuses; blanks and `#` lines are skipped; the path is what precedes the first `|`, trimmed; empty, longer than the cap, or the
// seventeenth entry refuses, at the first such line.
Verdict oracle(const std::string &input, const std::string &directory)
{
    Verdict verdict;
    std::string text = input;
    if (text.compare(0, 3, "\xEF\xBB\xBF") == 0) text.erase(0, 3);
    std::vector<std::string> lines(1);
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\r' && i + 1 < text.size() && text[i + 1] == '\n') ++i;
        if (text[i] == '\r' || text[i] == '\n') lines.emplace_back();
        else lines.back() += text[i];
    }
    if (lines.back().empty()) lines.pop_back();         // text that ends at a line end has no further line
    unsigned number = 0;
    for (std::string line : lines) {
        ++number;
        if (line.find('\0') != std::string::npos) { verdict.accepted = false; verdict.error_line = number; return verdict; }
        const size_t first = line.find_first_not_of(" \t");
        if (first == std::string::npos) continue;
        line = line.substr(first, line.find_last_not_of(" \t") + 1 - first);
        if (line[0] == '#') {
            if (line.compare(0, 10, "#SAVEDISK:") == 0) ++verdict.save_disks;
            continue;
        }
        std::string path = line.substr(0, line.find('|'));
        while (!path.empty() && (path.back() == ' ' || path.back() == '\t')) path.pop_back();
        if (path.empty() || path.size() > kMaxPathBytes || verdict.paths.size() >= kMaxEntries) {
            verdict.accepted = false;
            verdict.error_line = number;
            return verdict;
        }
        verdict.paths.push_back(oracle_is_absolute(path) ? path : directory + path);
    }
    return verdict;
}

int fuzz_m3u(uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> kinds;
    std::map<std::string, unsigned> refusals;
    unsigned entries_total = 0;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const Playlist1 input = hostile_playlist(rng);
        const std::string directory = rng.pick(kDirectories) + (rng.below(20) == 0 ? std::string(rng.below(kMaxPathBytes), 'd') + "/" : std::string());
        std::vector<uint8_t> bytes(input.text.begin(), input.text.end());
        Playlist playlist;
        playlist.entries.push_back({"stale", "stale"});        // the parser clears what an earlier call left
        playlist.save_disks_ignored = 77;
        std::string error;
        const bool accepted = parse_m3u(bytes, directory, kMaxEntries, &playlist, &error);
        const Verdict expected = oracle(input.text, directory);
        auto &count = kinds[input.kind];
        (accepted ? count.first : count.second)++;
        if (accepted != (expected.accepted != selftest())) bug("the parser and the oracle disagree on accepting", iteration, input.kind);
        if (accepted) {
            if (!error.empty()) bug("an accepted playlist left an error message", iteration, error);
            if (playlist.entries.size() > kMaxEntries) bug("more entries than the cap", iteration);
            if (playlist.entries.size() != expected.paths.size()) bug("a different number of entries", iteration, input.kind);
            if (playlist.save_disks_ignored != expected.save_disks) bug("a different number of #SAVEDISK lines", iteration);
            for (size_t i = 0; i < playlist.entries.size(); ++i) {
                if (playlist.entries[i].path != expected.paths[i]) bug("a different resolved path", iteration, input.kind);
                if (playlist.entries[i].path.size() > directory.size() + kMaxPathBytes) bug("a path beyond the cap joined to its directory", iteration);
            }
            entries_total += unsigned(playlist.entries.size());
        } else {
            if (error.empty()) bug("a refusal without a message", iteration, input.kind);
            const std::string wanted = error.compare(0, 13, "the playlist ") == 0 ? "(line " + std::to_string(expected.error_line) + ")"
                                                                                   : "playlist line " + std::to_string(expected.error_line) + ":";
            if (error.find(wanted) == std::string::npos) bug("the message does not name the line the oracle found", iteration, error);
            if (playlist.entries.size() > kMaxEntries) bug("a refused playlist kept more than the cap", iteration);
            std::string reason = error.compare(0, 13, "the playlist ") == 0 ? "more than the cap" : error.substr(error.find(": ") + 2);
            ++refusals[reason];
        }
    }
    for (const auto &kind : kinds) printf("m3u kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &reason : refusals) printf("m3u refusal \"%s\": %u\n", reason.first.c_str(), reason.second);
    printf("m3u entries accepted %u\n", entries_total);
    return 0;
}

// --- the frontend's VFS -------------------------------------------------------------------------------------------------------------------------

// A VFS that lies: the size it reports, how much it gives per read, where it fails, whether the stream ever ends. The content is a known pattern so
// that what the reader returns can be compared byte for byte.
struct FakeFile {
    int64_t claimed;
    uint64_t length;            // UINT64_MAX: an endless stream
    uint64_t fail_at;           // a read that would pass this offset returns -1 (UINT64_MAX: never)
    uint32_t chunk_limit;       // the most a single read gives
    uint64_t at = 0;
    uint64_t delivered = 0;
    bool fails = false;
};

FakeFile fake;
bool fake_open_fails = false;
unsigned fake_opens = 0, fake_closes = 0;

uint8_t pattern(uint64_t offset) { return uint8_t(offset * 31 + (offset >> 8)); }

retro_vfs_file_handle *fake_open(const char *, unsigned, unsigned)
{
    if (fake_open_fails) return nullptr;
    ++fake_opens;
    return reinterpret_cast<retro_vfs_file_handle *>(&fake);
}
int fake_close(retro_vfs_file_handle *) { ++fake_closes; return 0; }
int64_t fake_size(retro_vfs_file_handle *) { return fake.claimed; }
int64_t fake_read(retro_vfs_file_handle *, void *buffer, uint64_t length)
{
    if (fake.at >= fake.fail_at) { fake.fails = true; return -1; }
    uint64_t give = length < fake.chunk_limit ? length : fake.chunk_limit;
    if (fake.length != UINT64_MAX && give > fake.length - fake.at) give = fake.length - fake.at;
    if (fake.fail_at != UINT64_MAX && give > fake.fail_at - fake.at) give = fake.fail_at - fake.at;
    for (uint64_t i = 0; i < give; ++i) static_cast<uint8_t *>(buffer)[i] = pattern(fake.at + i);
    fake.at += give;
    fake.delivered += give;
    return int64_t(give);
}

int fuzz_vfs(uint64_t seed, uint64_t iterations)
{
    retro_vfs_interface vfs = {};
    vfs.open = fake_open;
    vfs.close = fake_close;
    vfs.size = fake_size;
    vfs.read = fake_read;
    FileReader reader;
    reader.use_vfs(&vfs);
    Random rng(seed);
    std::map<std::string, unsigned> outcomes;
    static const char *const names[] = {"Ok", "CannotOpen", "TooLarge", "ReadFailed", "OutOfMemory"};
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const size_t max_bytes = rng.below(8) == 0 ? rng.below(4) : 1 + rng.below(300000);
        fake = FakeFile();
        const uint32_t shape = rng.below(6);
        fake.length = shape == 0 ? UINT64_MAX : shape == 1 ? max_bytes : shape == 2 ? max_bytes + 1 : rng.below(uint32_t(max_bytes) * 2 + 2);
        const uint32_t claim = rng.below(7);
        fake.claimed = claim == 0 ? -1 : claim == 1 ? 0 : claim == 2 ? int64_t(1) << 62 : claim == 3 ? INT64_MIN : claim == 4 ? int64_t(max_bytes) + 1
                       : fake.length == UINT64_MAX ? 12345 : int64_t(fake.length);
        fake.fail_at = rng.below(6) == 0 ? rng.below(uint32_t(max_bytes) + 2) : UINT64_MAX;
        const uint32_t chunk = rng.below(4);
        fake.chunk_limit = chunk == 0 ? 1 + rng.below(7) : chunk == 1 ? 4096 : chunk == 2 ? 65536 : UINT32_MAX;
        if (fake.chunk_limit < 8 && max_bytes > 20000) fake.chunk_limit = 4096;       // one byte at a time would only make the run slow
        fake_open_fails = rng.below(10) == 0;
        fake_opens = fake_closes = 0;
        std::vector<uint8_t> out(7, 0xAA);        // filled by an earlier call: read() starts from nothing
        const ReadResult result = reader.read("some/path", max_bytes, &out);
        ++outcomes[names[int(result)]];
        if (fake_opens != fake_closes) bug("a handle was not closed exactly once", iteration);
        if (fake.delivered > max_bytes + 1) bug("more bytes were pulled than the cap allows", iteration);
        if (result != ReadResult::Ok && !out.empty()) bug("a refused read left bytes behind", iteration);
        const bool oversized_claim = fake.claimed > 0 && uint64_t(fake.claimed) > max_bytes;
        switch (result) {
        case ReadResult::Ok:
            if (out.size() > max_bytes || out.size() != fake.length || oversized_claim) bug("an accepted read that is not the file within the cap", iteration);
            for (size_t i = 0; i < out.size(); ++i)
                if (out[i] != pattern(i)) bug("the bytes read are not the bytes of the file", iteration);
            break;
        case ReadResult::CannotOpen:
            if (!fake_open_fails) bug("CannotOpen for a file that opens", iteration);
            break;
        case ReadResult::TooLarge:
            if (!oversized_claim && fake.length != UINT64_MAX && fake.length <= max_bytes) bug("TooLarge for a file within the cap", iteration);
            break;
        case ReadResult::ReadFailed:
            if (!fake.fails) bug("ReadFailed without a failing read", iteration);
            break;
        default:
            bug("an unexpected result", iteration);
        }
        if (selftest() && result == ReadResult::Ok) bug("self-test: an accepted read is reported as a failure", iteration);
    }
    for (const auto &outcome : outcomes) printf("vfs result %s: %u\n", outcome.first.c_str(), outcome.second);
    return 0;
}

// --- option values ---------------------------------------------------------------------------------------------------------------------------

// The frontend: GET_VARIABLE answers with whatever the run put in `values` (a key it has no value for is a frontend that never heard of it).
std::map<std::string, std::string> values;
std::map<std::string, bool> missing_value;      // the frontend answers true but with a NULL value

bool hostile_environment(unsigned command, void *data)
{
    if (command != RETRO_ENVIRONMENT_GET_VARIABLE) return false;
    retro_variable *variable = static_cast<retro_variable *>(data);
    const auto found = values.find(variable->key);
    if (found == values.end()) return false;
    variable->value = missing_value[variable->key] ? nullptr : found->second.c_str();
    return true;
}

std::string hostile_value(Random &rng, const p6_options::Option &option, std::string *kind)
{
    const std::string valid = option.values[rng.below(option.count)].name;
    static const char *const kinds[] = {"exact", "empty", "padded", "case", "suffix", "prefix", "zeros", "other-value", "non-ascii", "long", "random-bytes", "control",
                                        "other-options-value", "number-forms"};
    *kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
    if (*kind == "exact") return valid;
    if (*kind == "empty") return "";
    if (*kind == "padded") return rng.coin() ? valid + " " : (rng.coin() ? " " + valid : "\t" + valid + "\n");
    if (*kind == "case") {
        std::string text = valid;
        for (char &c : text) if (rng.coin()) c = char(c >= 'a' && c <= 'z' ? c - 32 : c >= 'A' && c <= 'Z' ? c + 32 : c);
        if (text == valid) text[0] = char(text[0] >= 'a' && text[0] <= 'z' ? text[0] - 32 : text[0] >= 'A' && text[0] <= 'Z' ? text[0] + 32 : text[0] + 1);
        return text;
    }
    if (*kind == "suffix") { static const char *const s[] = {"x", "Hz", " Hz", "\xC2\xA0", "0", ".0", "\\", "|"}; return valid + rng.pick(s); }
    if (*kind == "prefix") { static const char *const s[] = {"x", "+", "-", "0", "\xEF\xBB\xBF", "="}; return rng.pick(s) + valid; }
    if (*kind == "zeros") return "0" + valid;
    if (*kind == "other-value") { static const char *const s[] = {"SECAM", "PAL-M", "PAL60", "22050", "96000", "0", "-1", "1", "true", "auto", "default", "16:9", "4:3 ",
                                                           "a1200", "A1200 ", "A1200OG", "A12000", "A500+", "A500PLUS", "A500OG", "A600HD", "A2000", "A4000", "CD32", "CDTV", "A500,A600", "A600,A1200"}; return rng.pick(s); }
    if (*kind == "non-ascii") { static const char *const s[] = {"\xD0\xA0\xD0\x90L", "\xEF\xBC\x90", "\xDB\xB4\xDB\xB8", "\xFF", "\xC3", "\xE2\x82\xAC", "\xC0\x80"}; return rng.pick(s); }
    if (*kind == "long") return std::string(1 + rng.below(70000), rng.coin() ? '9' : 'P') + (rng.coin() ? valid : "");
    if (*kind == "random-bytes") {
        std::string text(1 + rng.below(40), '\0');
        for (char &c : text) c = char(1 + rng.below(255));        // no NUL: a C string ends there
        return text;
    }
    if (*kind == "control") { static const char *const s[] = {"\n", "\r\n", "\x1b[0m", "%s%n", "\x7f", "\t"}; return std::string(valid) + rng.pick(s); }
    if (*kind == "other-options-value") {
        const p6_options::Option &other = p6_options::kOptions[rng.below(p6_options::kOptionCount)];
        return other.values[rng.below(other.count)].name;           // valid for another key: valid here only when it is one of ours too
    }
    static const char *const forms[] = {"0x1", "0x2", "1.0", "2.0", "01", "02", "+1", "1e0", "4.41e4", "44100.0", "0xAC44", "0b1"};
    return rng.pick(forms);
}

int fuzz_options(uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> kinds;
    std::map<std::string, unsigned> accepted_values;
    unsigned all_defaults = 0;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        values.clear();
        missing_value.clear();
        std::string first_kind, kind_text;
        // Which options the frontend holds, and what it holds for them.
        std::map<std::string, std::string> held;
        const bool calm = rng.below(3) == 0;                         // a third of the frontends hold only exact names, so that acceptance is exercised too
        const bool holds_nothing = iteration % 250 == 7;             // a frontend with none of our options at all: with six of them the random choice alone reaches it too rarely
        for (const p6_options::Option &option : p6_options::kOptions) {
            const uint32_t how = rng.below(10);
            if (how == 0 || holds_nothing) continue;                 // the frontend has no such variable
            if (how == 1) { values[option.key] = "ignored"; missing_value[option.key] = true; continue; }     // answers with a NULL value
            std::string kind = "exact";
            values[option.key] = held[option.key] = calm ? option.values[rng.below(option.count)].name : hostile_value(rng, option, &kind);
            if (first_kind.empty() || kind != "exact") first_kind = kind;
        }
        p6_options::Settings settings;       // the defaults of the schema
        const p6_options::Settings before = settings;
        std::string why;
        const bool accepted = read_options(hostile_environment, &settings, &why);
        // The oracle: every held value must be, byte for byte, one of the names of that option; the first that is not decides the refusal.
        const p6_options::Option *bad = nullptr;
        std::string bad_value;
        for (const p6_options::Option &option : p6_options::kOptions) {
            const auto found = held.find(option.key);
            if (found == held.end()) continue;
            bool exact = false;
            for (unsigned i = 0; i < option.count; ++i) exact = exact || found->second == option.values[i].name;
            if (!exact) { bad = &option; bad_value = found->second; break; }
        }
        auto &count = kinds[first_kind.empty() ? "no-value" : first_kind];
        (accepted ? count.first : count.second)++;
        if (accepted != ((bad == nullptr) != selftest())) bug("read_options and the oracle disagree", iteration, first_kind + " " + why);
        if (accepted) {
            if (!why.empty()) bug("an accepted set of options left a message", iteration, why);
            // What the frontend holds, with the preset it names (T49) laid over the model and the memory as the core does after reading everything.
            p6_options::Settings expected = before;
            for (const p6_options::Option &option : p6_options::kOptions) {
                const auto found = held.find(option.key);
                if (found != held.end()) {
                    for (unsigned i = 0; i < option.count; ++i) if (found->second == option.values[i].name) expected.*option.field = option.values[i].number;
                    ++accepted_values[std::string(option.key) + "=" + found->second];
                }
            }
            p6_options::apply_preset(&expected);
            for (const p6_options::Option &option : p6_options::kOptions)
                if (settings.*option.field != expected.*option.field) bug("a setting that is not the one the frontend holds", iteration, option.key);
            if (held.empty()) ++all_defaults;
        } else {
            if (!bad) bug("refused with nothing wrong", iteration);
            if (why.find(bad->key) == std::string::npos) bug("the refusal does not name the option", iteration, why);
            if (why.find("\"" + bad_value + "\"") == std::string::npos) bug("the refusal does not quote the value", iteration, why);
            if (why.find("allowed: " + allowed_values(*bad)) == std::string::npos) bug("the refusal does not list the allowed values", iteration, why);
            // The options before the bad one are set; the bad one and the ones after it are untouched (the caller must not use the result anyway).
            bool reached = false;
            for (const p6_options::Option &option : p6_options::kOptions) {
                reached = reached || &option == bad;
                if (reached && settings.*option.field != before.*option.field) bug("an option at or after the refused one was changed", iteration, option.key);
            }
        }
    }
    for (const auto &kind : kinds) printf("options kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &value : accepted_values) printf("options accepted %s: %u\n", value.first.c_str(), value.second);
    printf("options accepted with nothing held %u\n", all_defaults);
    return 0;
}

// --- hard files (T63a) -------------------------------------------------------------------------------------------------------------------------

void put32(std::vector<uint8_t> *out, uint32_t value)
{
    for (int i = 0; i < 4; ++i) out->push_back(uint8_t(value >> (8 * i)));
}

// A gzip file whose DEFLATE stream is stored blocks: valid, and needs no compressor.
std::vector<uint8_t> gzip_stored(const std::vector<uint8_t> &data)
{
    std::vector<uint8_t> out = {0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 0xFF};
    size_t at = 0;
    do {
        const size_t n = std::min<size_t>(data.size() - at, 65535);
        const bool last = at + n == data.size();
        out.push_back(last ? 1 : 0);
        out.push_back(uint8_t(n));
        out.push_back(uint8_t(n >> 8));
        out.push_back(uint8_t(~n));
        out.push_back(uint8_t((~n) >> 8));
        out.insert(out.end(), data.begin() + std::ptrdiff_t(at), data.begin() + std::ptrdiff_t(at + n));
        at += n;
    } while (at < data.size());
    put32(&out, crc32_update(0, data.data(), data.size()));
    put32(&out, uint32_t(data.size()));
    return out;
}

void put16(std::vector<uint8_t> *out, uint32_t value)
{
    out->push_back(uint8_t(value));
    out->push_back(uint8_t(value >> 8));
}

// A zip file of stored (uncompressed) members: a local header and data for each, then the central directory and the end record.
std::vector<uint8_t> zip_stored(const std::vector<std::pair<std::string, std::vector<uint8_t>>> &members)
{
    std::vector<uint8_t> out, directory;
    for (const auto &member : members) {
        const uint32_t offset = uint32_t(out.size()), crc = crc32_update(0, member.second.data(), member.second.size()), size = uint32_t(member.second.size());
        for (std::vector<uint8_t> *part : {&out, &directory}) {
            const bool central = part == &directory;
            part->insert(part->end(), {'P', 'K', uint8_t(central ? 1 : 3), uint8_t(central ? 2 : 4)});
            if (central) put16(part, 20);                          // version made by
            put16(part, 20);                                       // version needed
            put16(part, 0);                                        // flags
            put16(part, 0);                                        // method: stored
            put16(part, 0); put16(part, 0x21);                     // time, date
            put32(part, crc); put32(part, size); put32(part, size);
            put16(part, uint32_t(member.first.size()));
            put16(part, 0);                                        // extra
            if (central) {
                put16(part, 0); put16(part, 0); put16(part, 0);    // comment, disk, internal attributes
                put32(part, 0);                                    // external attributes
                put32(part, offset);
            }
            part->insert(part->end(), member.first.begin(), member.first.end());
        }
        out.insert(out.end(), member.second.begin(), member.second.end());
    }
    const uint32_t directory_offset = uint32_t(out.size());
    out.insert(out.end(), directory.begin(), directory.end());
    out.insert(out.end(), {'P', 'K', 5, 6});
    put16(&out, 0); put16(&out, 0);
    put16(&out, uint32_t(members.size())); put16(&out, uint32_t(members.size()));
    put32(&out, uint32_t(directory.size()));
    put32(&out, directory_offset);
    put16(&out, 0);
    return out;
}

// A gzip file whose stream is one fixed-Huffman block: a zero, then `matches` copies of 258 bytes at distance 1 (13 bits each): a bomb a few bytes in size.
std::vector<uint8_t> gzip_bomb(size_t matches)
{
    std::vector<uint8_t> out = {0x1F, 0x8B, 8, 0, 0, 0, 0, 0, 0, 0xFF};
    uint32_t bits = 0;
    unsigned have = 0;
    auto put_bit = [&](unsigned bit) {
        bits |= bit << have;
        if (++have == 8) {
            out.push_back(uint8_t(bits));
            bits = 0;
            have = 0;
        }
    };
    auto put_code = [&](unsigned code, unsigned n) { for (unsigned i = n; i-- > 0;) put_bit((code >> i) & 1); };      // Huffman codes go most significant bit first
    put_bit(1); put_bit(1); put_bit(0);                           // BFINAL, BTYPE = 01 (fixed codes)
    put_code(0x30 + 0, 8);                                        // literal 0
    for (size_t i = 0; i < matches; ++i) {
        put_code(0xC0 + (285 - 280), 8);                          // length 258
        put_code(0, 5);                                           // distance 1
    }
    put_code(0, 7);                                               // end of block
    while (have) put_bit(0);
    for (int i = 0; i < 8; ++i) out.push_back(0);                 // a trailer nobody gets to read
    return out;
}

std::vector<uint8_t> hard_image(Random &rng, size_t blocks)
{
    std::vector<uint8_t> image(blocks * kHdfBlock, 0);
    switch (rng.below(3)) {
    case 0: for (uint8_t &b : image) b = uint8_t(rng.below(256)); break;
    case 1: for (size_t i = 0; i < image.size(); i += 61) image[i] = uint8_t(rng.below(256)); break;
    default: break;
    }
    if (rng.coin() && image.size() >= 4) memcpy(image.data(), "RDSK", 4);
    return image;
}

int fuzz_hdz(uint64_t seed, uint64_t iterations)
{
    static const char *const kinds[] = {"valid-hdz", "valid-hdf", "hdz-flipped", "hdz-truncated", "hdz-trailing", "hdz-bad-crc", "hdz-bad-size", "wrong-size", "over-cap", "bomb",
                                        "random-gzip", "random-bytes", "not-gzip", "gzip-named-hdf", "valid-zip", "zip-flipped", "zip-two-hdf", "zip-no-hdf"};
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> histogram;
    std::map<std::string, unsigned> refusals;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const size_t cap = kHdfBlock * (1 + rng.below(160));       // up to 80 KiB: the cap is a parameter, so the fuzzer can reach it
        const std::string kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
        std::vector<uint8_t> file, image;
        std::string name = "disk.hdz";
        bool must_load = false, must_refuse = false;
        const size_t blocks = 1 + rng.below(unsigned(cap / kHdfBlock));
        if (kind == "valid-hdz" || kind == "valid-hdf") {
            image = hard_image(rng, blocks);
            if (kind == "valid-hdf") {
                name = "Disk.HDF";
                file = image;
            } else {
                name = rng.coin() ? "disk.hdz" : "DISK.HDZ";
                file = gzip_stored(image);
            }
            must_load = true;
        } else if (kind == "hdz-flipped" || kind == "hdz-truncated" || kind == "hdz-trailing" || kind == "hdz-bad-crc" || kind == "hdz-bad-size") {
            image = hard_image(rng, blocks);
            file = gzip_stored(image);
            if (kind == "hdz-flipped") {
                file[rng.below(unsigned(file.size()))] ^= uint8_t(1u << rng.below(8));
            } else if (kind == "hdz-truncated") {
                file.resize(rng.below(unsigned(file.size())));
                must_refuse = true;
            } else if (kind == "hdz-trailing") {
                for (unsigned n = 1 + rng.below(40); n--;) file.push_back(uint8_t(rng.below(256)));
                must_refuse = true;
            } else if (kind == "hdz-bad-crc") {
                file[file.size() - 8 + rng.below(4)] ^= uint8_t(1 + rng.below(255));
                must_refuse = true;
            } else {
                file[file.size() - 4 + rng.below(4)] ^= uint8_t(1 + rng.below(255));
                must_refuse = true;
            }
        } else if (kind == "wrong-size") {
            static const size_t sizes[] = {0, 1, 511, 513, 1000, 1023, 4097, 100000};
            image.assign(sizes[rng.below(sizeof sizes / sizeof *sizes)], 7);
            const bool zipped = rng.coin();
            name = zipped ? "x.hdz" : "x.hdf";
            file = zipped ? gzip_stored(image) : image;
            must_refuse = true;
        } else if (kind == "over-cap") {
            image.assign(cap + kHdfBlock * (1 + rng.below(3)), 1);
            const bool zipped = rng.coin();
            name = zipped ? "x.hdz" : "x.hdf";
            file = zipped ? gzip_stored(image) : image;
            must_refuse = true;
        } else if (kind == "bomb") {
            file = gzip_bomb(cap / 258 + 1 + rng.below(4000));
            must_refuse = true;
        } else if (kind == "random-gzip") {
            file = {0x1F, 0x8B, uint8_t(rng.coin() ? 8 : rng.below(256)), uint8_t(rng.below(256))};
            for (unsigned n = rng.below(300); n--;) file.push_back(uint8_t(rng.below(256)));
        } else if (kind == "random-bytes") {
            for (unsigned n = rng.below(1200); n--;) file.push_back(uint8_t(rng.below(256)));
            name = rng.coin() ? "r.hdz" : "r.hdf";
        } else if (kind == "not-gzip") {
            file = hard_image(rng, blocks);
            file[0] = uint8_t(rng.below(0x1F));                     // anything but the gzip magic
            must_refuse = true;
        } else if (kind == "valid-zip" || kind == "zip-flipped") {
            image = hard_image(rng, blocks);
            file = zip_stored({{"__MACOSX/._x", {1, 2, 3}}, {rng.coin() ? "dir/Disk.HDF" : "disk.hdf", image}, {"readme.txt", {'h', 'i'}}});
            if (kind == "zip-flipped") file[rng.below(unsigned(file.size()))] ^= uint8_t(1u << rng.below(8));
            else must_load = true;
        } else if (kind == "zip-two-hdf") {
            file = zip_stored({{"a.hdf", hard_image(rng, 1)}, {"b.hdf", hard_image(rng, 2)}});
            must_refuse = true;
        } else if (kind == "zip-no-hdf") {
            file = zip_stored({{"a.adf", hard_image(rng, 1)}, {"b.txt", {'x'}}});
            must_refuse = true;
        } else {                                                    // a gzip file with the name of a plain hard file: it is the image, as a file of that name is
            image = hard_image(rng, 1 + rng.below(8));
            file = gzip_stored(image);
            name = "disk.hdf";
        }
        const std::vector<uint8_t> original = file;
        std::string why;
        const bool loaded = decode_hard_file(&file, name, cap, &why);
        auto &count = histogram[kind];
        if (loaded) {
            ++count.first;
            if (file.size() < kHdfBlock || file.size() % kHdfBlock || file.size() > cap) bug("a hard file that is not whole blocks within the cap was accepted", iteration, kind);
            if (container_detail::lower_extension(name) == "hdz") {
                // Whatever gunzip gave back is what the member's trailer says it is (the CRC and the size), and the member ends the file. A zip checked its member's CRC against the
                // central directory itself.
                if (original[0] == 0x1F) {
                    const uint8_t *trailer = original.data() + original.size() - 8;
                    if (container_detail::le32(trailer) != crc32_update(0, file.data(), file.size()) || container_detail::le32(trailer + 4) != uint32_t(file.size()))
                        bug("an .hdz was accepted whose trailer does not describe it", iteration, kind);
                }
            } else if (file != original) {
                bug("a plain hard file was changed", iteration, kind);
            }
            if (must_refuse || (selftest() && must_load)) bug("a hard file that has to be refused was accepted", iteration, kind);
            if (must_load && file != image) bug("a valid file did not come back as its image", iteration, kind);
            if (!why.empty()) bug("an accepted file left a reason", iteration, why);
        } else {
            ++count.second;
            if (why.empty()) bug("a refusal without a message", iteration, kind);
            if (must_load) bug("a valid hard file was refused", iteration, why);
            std::string key;                                        // the reason with its numbers taken out: one line per kind of refusal
            for (char c : why) if (c < '0' || c > '9') key += c;
            ++refusals[key.substr(0, 64)];
        }
    }
    for (const auto &kind : histogram) printf("hdz kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &reason : refusals) printf("hdz refusal \"%s\": %u\n", reason.first.c_str(), reason.second);
    return 0;
}

// --- volumes (T64a) --------------------------------------------------------------------------------------------------------------------------------

using Tree = std::map<std::string, std::vector<uint8_t>>;      // path -> bytes; a path that ends with '/' is a directory

// An LHA archive of stored members (the -lh0- method needs no compressor) with the header level of the call: 0, 1 or 2 (the writer of tests/whdload/lha_fixture.py, in C++). `method` may be
// another one, with `packed` as the bytes of the member and `original` as the size the header claims. A name that ends with '/' is a directory.
std::vector<uint8_t> lha_member(const std::string &name, const std::vector<uint8_t> &data, unsigned level, const char *method, uint32_t original, const std::vector<uint8_t> *packed = nullptr)
{
    const std::vector<uint8_t> &body = packed ? *packed : data;
    const uint16_t crc = lha_detail::crc16(data.data(), data.size());
    const bool directory = !name.empty() && name.back() == '/';
    std::string file = name, folder;
    if (level != 0) {
        const std::string bare = directory ? name.substr(0, name.size() - 1) : name;
        const size_t slash = bare.find_last_of('/');
        folder = directory ? bare + "/" : (slash == std::string::npos ? std::string() : bare.substr(0, slash + 1));
        file = directory ? std::string() : bare.substr(folder.size());
        for (char &c : folder) if (c == '/') c = char(0xFF);
    }
    // The extended headers: each is [kind][body][size of the next]; the first size sits in the fixed part.
    std::vector<std::pair<uint8_t, std::string>> items;
    if (level == 2 && !file.empty()) items.push_back({1, file});
    if (level != 0 && !folder.empty()) items.push_back({2, folder});
    std::vector<uint8_t> tail;
    for (size_t i = 0; i < items.size(); ++i) {
        tail.push_back(items[i].first);
        tail.insert(tail.end(), items[i].second.begin(), items[i].second.end());
        const size_t next = i + 1 < items.size() ? items[i + 1].second.size() + 3 : 0;
        put16(&tail, uint32_t(next));
    }
    const uint32_t first = items.empty() ? 0 : uint32_t(items[0].second.size() + 3);
    std::vector<uint8_t> out, fixed;
    for (const char *m = method; *m; ++m) fixed.push_back(uint8_t(*m));
    put32(&fixed, uint32_t(body.size() + (level == 1 ? tail.size() : 0)));
    put32(&fixed, original);
    put32(&fixed, 0x5A210000);
    fixed.push_back(0x20);
    fixed.push_back(uint8_t(level));
    if (level != 2) {
        fixed.push_back(uint8_t(file.size()));
        fixed.insert(fixed.end(), file.begin(), file.end());
    }
    put16(&fixed, crc);
    if (level != 0) {
        fixed.push_back('A');
        put16(&fixed, first);
    }
    if (level == 2) {
        put16(&out, uint32_t(2 + fixed.size() + tail.size()));
    } else {
        unsigned sum = 0;
        for (uint8_t b : fixed) sum += b;
        out = {uint8_t(fixed.size()), uint8_t(sum)};
    }
    out.insert(out.end(), fixed.begin(), fixed.end());
    out.insert(out.end(), tail.begin(), tail.end());
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

std::vector<uint8_t> lha_archive(const Tree &tree, unsigned level)
{
    std::vector<uint8_t> out;
    for (const auto &item : tree) {
        const bool directory = !item.first.empty() && item.first.back() == '/';
        const std::vector<uint8_t> member = lha_member(item.first, item.second, level, directory ? "-lhd-" : "-lh0-", uint32_t(directory ? 0 : item.second.size()));
        out.insert(out.end(), member.begin(), member.end());
    }
    out.push_back(0);
    return out;
}

std::string random_name(Random &rng, bool hostile)
{
    static const char *const good[] = {"a", "Game", "data", "level1.dat", "S", "Startup-Sequence", "x y", "n\xE9", "ZZ", "file.info", "0123456789012345678901234567890"};
    static const char *const bad[] = {"", ".", "..", "a:b", "x\x01y", "has/slash", "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn", "\\", ("a\xFF" "b")};
    std::string name = hostile && rng.below(3) == 0 ? bad[rng.below(sizeof bad / sizeof *bad)] : good[rng.below(sizeof good / sizeof *good)];
    if (name.size() == 31 && !hostile) name.pop_back();            // the longest valid name is 30 bytes
    return name;
}

// The independent check of a volume: every block that is reachable has its checksum, every file has the bytes the tree said, nothing is reached twice, the bitmap is valid.
bool volume_matches(const std::vector<uint8_t> &image, const Tree &tree, std::string *why)
{
    if (image.size() % (32 * 512) || image.size() < 64 * 512) { *why = "the size is not a multiple of 32 blocks"; return false; }
    const uint32_t blocks = uint32_t(image.size() / 512), root = (blocks - 1 + 2) / 2;
    auto longs = [&](uint32_t block, uint32_t at) { const uint8_t *p = image.data() + size_t(block) * 512 + at * 4; return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; };
    auto sum_ok = [&](uint32_t block) { uint32_t sum = 0; for (uint32_t i = 0; i < 128; ++i) sum += longs(block, i); return sum == 0; };
    if (!sum_ok(root) || longs(root, 127) != 1) { *why = "root block"; return false; }
    std::vector<bool> used(blocks, false);
    size_t files = 0, expected_files = 0;
    for (const auto &item : tree) if (item.first.empty() || item.first.back() != '/') ++expected_files;
    std::vector<std::pair<uint32_t, std::string>> pending = {{root, ""}};
    while (!pending.empty()) {
        const auto here = pending.back();
        pending.pop_back();
        for (uint32_t slot = 0; slot < 72; ++slot) {
            for (uint32_t chain = longs(here.first, 6 + slot); chain;) {
                if (chain >= blocks || used[chain] || !sum_ok(chain)) { *why = "directory entry"; return false; }
                used[chain] = true;
                const uint8_t *raw = image.data() + size_t(chain) * 512 + 108 * 4;
                const std::string name(reinterpret_cast<const char *>(raw + 1), raw[0]);
                const std::string path = here.second.empty() ? name : here.second + "/" + name;
                if (longs(chain, 127) == 2) {
                    pending.push_back({chain, path});
                } else {
                    const auto found = tree.find(path);
                    if (found == tree.end()) { *why = "a file that is not in the tree: " + path; return false; }
                    const uint32_t size = longs(chain, 81);
                    if (size != found->second.size()) { *why = "size of " + path; return false; }
                    std::vector<uint32_t> list;
                    for (uint32_t header = chain;;) {
                        for (uint32_t i = 0; i < longs(header, 2); ++i) list.push_back(longs(header, 77 - i));
                        header = longs(header, 126);
                        if (!header) break;
                        if (header >= blocks || used[header] || !sum_ok(header)) { *why = "extension block"; return false; }
                        used[header] = true;
                    }
                    std::vector<uint8_t> data;
                    for (uint32_t block : list) {
                        if (block >= blocks || used[block]) { *why = "data block"; return false; }
                        used[block] = true;
                        data.insert(data.end(), image.begin() + std::ptrdiff_t(block) * 512, image.begin() + std::ptrdiff_t(block + 1) * 512);
                    }
                    data.resize(size);
                    if (data != found->second) { *why = "bytes of " + path; return false; }
                    ++files;
                }
                chain = longs(chain, 124);
            }
        }
    }
    if (files != expected_files) { *why = "the number of files"; return false; }
    return true;
}

int fuzz_volume(uint64_t seed, uint64_t iterations)
{
    static const char *const kinds[] = {"tree-valid", "tree-hostile", "lha-valid", "lha-flipped", "lha-truncated", "lha-random-method", "lha-big-claim", "lha-hostile-names",
                                        "zip-valid", "zip-flipped", "zip-hostile-names", "zip-big-claim", "zip-truncated"};
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> histogram;
    std::map<std::string, unsigned> refusals;
    LhaLimits small;
    small.max_entries = 64;
    small.max_file = 1 << 16;
    small.max_total = 1 << 17;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const std::string kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
        Tree tree;
        const unsigned members_wanted = 1 + rng.below(8);
        for (unsigned i = 0; i < members_wanted; ++i) {
            std::string path;
            for (unsigned depth = rng.below(3); depth > 0; --depth) path += random_name(rng, false) + "/";
            const bool directory = rng.below(5) == 0;
            std::string leaf = random_name(rng, false);
            std::vector<uint8_t> data;
            if (!directory) for (unsigned n = rng.below(3) ? rng.below(1500) : rng.below(40000); n--;) data.push_back(uint8_t(rng.below(4) ? 'a' + rng.below(5) : rng.below(256)));
            tree[path + leaf + (directory ? "/" : "")] = directory ? std::vector<uint8_t>() : data;
        }
        // A tree whose paths cannot all stand together (a file and a directory with the same name, two names that differ in case) is not "valid": the oracle below is the writer itself,
        // so only the names are controlled here, and the check is "accepted => the image is exactly the tree".
        std::string why;
        const bool hostile = kind.find("hostile") != std::string::npos;
        bool accepted = false, must_refuse = false;
        Tree expected = tree;
        if (kind == "tree-valid" || kind == "tree-hostile") {
            std::vector<VolumeFile> files;
            expected.clear();
            for (const auto &item : tree) {
                std::string path = item.first;
                if (kind == "tree-hostile" && rng.below(2) == 0) path = random_name(rng, true) + (rng.coin() ? "/" + random_name(rng, true) : "");
                VolumeFile file;
                file.is_dir = !path.empty() && path.back() == '/';
                file.path = file.is_dir ? path.substr(0, path.size() - 1) : path;
                file.data = item.second;
                if (!file.is_dir) expected[file.path] = file.data;
                else expected[file.path + "/"] = {};
                files.push_back(std::move(file));
            }
            std::vector<uint8_t> image;
            accepted = build_ffs_volume(&files, VolumeOptions(), &image, &why);
            if (accepted) {
                // directories are listed with a trailing slash in `expected`; a file named like a directory would have been refused
                Tree check;
                for (const auto &item : expected) if (item.first.empty() || item.first.back() != '/') check[item.first] = item.second;
                std::string reason;
                // Only files are compared (the tree may have implicit directories); the walk counts the files it reaches.
                if (!volume_matches(image, check, &reason)) bug("an accepted tree did not come back as an image of itself", iteration, reason);
            }
        } else if (kind.compare(0, 3, "lha") == 0) {
            const unsigned level = rng.below(3);
            std::vector<uint8_t> blob = lha_archive(tree, level);
            if (kind == "lha-flipped") {
                for (unsigned n = 1 + rng.below(3); n--;) blob[rng.below(unsigned(blob.size()))] ^= uint8_t(1u << rng.below(8));
            } else if (kind == "lha-truncated") {
                blob.resize(rng.below(unsigned(blob.size())));
            } else if (kind == "lha-random-method") {
                static const char *const methods[] = {"-lh5-", "-lh6-", "-lh7-"};
                std::vector<uint8_t> packed;
                for (unsigned n = rng.below(600); n--;) packed.push_back(uint8_t(rng.below(256)));
                blob = lha_member("f", {}, rng.below(3), methods[rng.below(3)], 1 + rng.below(4000), &packed);
                blob.push_back(0);
            } else if (kind == "lha-big-claim") {
                std::vector<uint8_t> packed = {1, 2, 3};
                blob = lha_member("f", {}, rng.below(3), rng.coin() ? "-lh5-" : "-lh0-", rng.coin() ? uint32_t(small.max_file + 1 + rng.below(1000)) : 0xFFFFFFFFu, &packed);
                blob.push_back(0);
                must_refuse = true;
            } else if (kind == "lha-hostile-names") {
                std::map<std::string, std::vector<uint8_t>> bad;
                static const char *const names[] = {"../x", "a/../b", "/abs", "C:x", "a//b", ".", "x\x01y", "a/./b"};
                bad[names[rng.below(sizeof names / sizeof *names)]] = {1, 2, 3};
                blob = lha_archive(bad, 0);
                must_refuse = true;
            }
            std::vector<LhaEntry> entries;
            accepted = read_lha(blob.data(), blob.size(), small, &entries, &why);
            if (accepted) {
                size_t total = 0;
                for (const LhaEntry &entry : entries) {
                    total += entry.data.size();
                    if (entry.name.empty() || entry.name[0] == '/' || entry.name.find("..") == 0 || entry.name.find(':') != std::string::npos) bug("an LHA member with a name that leaves the volume was accepted", iteration, entry.name);
                }
                if (entries.size() > small.max_entries || total > small.max_total) bug("an LHA was accepted beyond its limits", iteration);
                if (kind == "lha-valid") {
                    std::map<std::string, std::vector<uint8_t>> got;
                    for (const LhaEntry &entry : entries) got[entry.name + (entry.is_dir ? "/" : "")] = entry.data;
                    for (const auto &item : tree) {
                        const std::string key = item.first;
                        if (!got.count(key) && !(key.back() == '/' && got.count(key))) bug("a valid LHA lost a member", iteration, key);
                        else if (got[key] != item.second) bug("a valid LHA changed a member", iteration, key);
                    }
                }
                std::vector<VolumeFile> files;
                std::vector<uint8_t> image;
                for (const LhaEntry &entry : entries) {
                    VolumeFile file;
                    file.path = entry.name;
                    file.is_dir = entry.is_dir;
                    file.data = entry.data;
                    files.push_back(std::move(file));
                }
                std::string reason;
                if (build_ffs_volume(&files, VolumeOptions(), &image, &reason)) {
                    Tree check;
                    for (const LhaEntry &entry : entries) if (!entry.is_dir) check[entry.name] = entry.data;
                    if (!volume_matches(image, check, &reason)) bug("the image of an accepted LHA is not its content", iteration, reason);
                }
            } else if (kind == "lha-valid") {
                bug("a valid LHA was refused", iteration, why);
            }
        } else {
            std::vector<std::pair<std::string, std::vector<uint8_t>>> members;
            for (const auto &item : tree) members.push_back({item.first, item.second});
            std::vector<uint8_t> blob;
            if (kind == "zip-hostile-names") {
                static const char *const names[] = {"../x", "a/../b", "/abs", "a//b", "./x", "a/./b", "x\x01y"};
                members = {{names[rng.below(sizeof names / sizeof *names)], {1}}, {"ok", {2}}};
                must_refuse = true;
            }
            if (kind == "zip-big-claim") {
                members = {{"big", {1, 2, 3}}};
                must_refuse = true;
            }
            blob = zip_stored(members);
            if (kind == "zip-big-claim") {
                // declare 100 MiB in the central directory (and the local header): the 48 MiB cap refuses it before anything is read
                for (size_t at = 0; at + 4 < blob.size(); ++at)
                    if (!memcmp(&blob[at], "PK\1\2", 4)) { blob[at + 24] = 0; blob[at + 25] = 0; blob[at + 26] = 0; blob[at + 27] = 0x06; break; }
            }
            if (kind == "zip-flipped") blob[rng.below(unsigned(blob.size()))] ^= uint8_t(1u << rng.below(8));
            if (kind == "zip-truncated") blob.resize(rng.below(unsigned(blob.size())));
            std::vector<VolumeFile> files;
            accepted = volume_from_zip(blob, &files, &why);
            if (accepted) {
                for (const VolumeFile &file : files) {
                    if (file.path.empty() || file.path[0] == '/' || file.path.find("..") != std::string::npos) bug("a ZIP member with a path that leaves the volume was accepted", iteration, file.path);
                }
                if (kind == "zip-valid") {
                    std::vector<uint8_t> image;
                    std::string reason;
                    Tree check;
                    for (const VolumeFile &file : files) if (!file.is_dir) check[file.path] = file.data;
                    if (build_ffs_volume(&files, VolumeOptions(), &image, &reason) && !volume_matches(image, check, &reason)) bug("the image of an accepted ZIP is not its content", iteration, reason);
                }
            }
        }
        auto &count = histogram[kind];
        (void)hostile;
        if (accepted) {
            ++count.first;
            if (must_refuse || (selftest() && kind == "tree-valid")) bug("something that has to be refused was accepted", iteration, kind);
            if (!why.empty()) bug("an accepted source left a reason", iteration, why);
        } else {
            ++count.second;
            if (why.empty()) bug("a refusal without a message", iteration, kind);
            std::string key;                                        // the reason without the names (inside quotes or parentheses), the numbers and the member a CRC belongs to
            bool quoted = false;
            int nested = 0;
            for (char c : why) {
                if (c == '"') quoted = !quoted;
                else if (!quoted && c == '(') ++nested;
                else if (!quoted && c == ')') nested -= nested > 0;
                else if (!quoted && !nested && (c < '0' || c > '9')) key += c;
            }
            if (key.compare(0, 6, "CRC of") == 0) key = "CRC of a member";
            for (const char *tail : {" appears twice", " is both a file and a directory"})            // the path in front of these two is a name
                if (key.size() > strlen(tail) && key.compare(key.size() - strlen(tail), strlen(tail), tail) == 0) key = std::string("a path") + tail;
            ++refusals[key.substr(0, 48)];
        }
    }
    for (const auto &kind : histogram) printf("volume kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &reason : refusals) printf("volume refusal \"%s\": %u\n", reason.first.c_str(), reason.second);
    return 0;
}

// --- archives of disks (T59) ------------------------------------------------------------------------------------------------------------------------

struct ArchiveSeed {
    std::string name;
    std::vector<uint8_t> bytes;
    std::vector<std::pair<std::string, uint32_t>> expect;      // the disks of the clean archive, in playlist order: member name and CRC-32
};

std::vector<uint8_t> slurp(const std::string &path, bool *ok)
{
    std::vector<uint8_t> out;
    FILE *f = fopen(path.c_str(), "rb");
    *ok = f != nullptr;
    if (!f) return out;
    uint8_t buffer[65536];
    size_t got;
    while ((got = fread(buffer, 1, sizeof buffer, f)) > 0) out.insert(out.end(), buffer, buffer + got);
    fclose(f);
    return out;
}

std::vector<ArchiveSeed> load_archive_seeds(const std::string &directory)
{
    std::vector<ArchiveSeed> seeds;
    bool ok = false;
    const std::vector<uint8_t> index = slurp(directory + "/index.txt", &ok);
    if (!ok) { fprintf(stderr, "no index.txt in %s\n", directory.c_str()); exit(2); }
    const std::string text(index.begin(), index.end());
    size_t at = 0;
    while (at < text.size()) {
        size_t end = text.find('\n', at);
        if (end == std::string::npos) end = text.size();
        const std::string name = text.substr(at, end - at);
        at = end + 1;
        if (name.empty()) continue;
        ArchiveSeed seed;
        seed.name = name;
        seed.bytes = slurp(directory + "/" + name, &ok);
        if (!ok) { fprintf(stderr, "no seed %s\n", name.c_str()); exit(2); }
        const std::vector<uint8_t> expect = slurp(directory + "/" + name + ".expect", &ok);
        if (!ok) { fprintf(stderr, "no expectation for %s\n", name.c_str()); exit(2); }
        const std::string lines(expect.begin(), expect.end());
        size_t line_at = 0;
        while (line_at < lines.size()) {
            size_t line_end = lines.find('\n', line_at);
            if (line_end == std::string::npos) line_end = lines.size();
            const std::string line = lines.substr(line_at, line_end - line_at);
            line_at = line_end + 1;
            const size_t space = line.rfind(' ');
            if (space == std::string::npos) continue;
            seed.expect.push_back({line.substr(0, space), uint32_t(strtoul(line.c_str() + space + 1, nullptr, 16))});
        }
        seeds.push_back(std::move(seed));
    }
    return seeds;
}

void put_u32_at(uint8_t *at, uint32_t value)
{
    for (int i = 0; i < 4; ++i) at[i] = uint8_t(value >> (8 * i));
}

// The seven-zip header checksums put right again after a mutation, so that the structure has to defend itself.
void fix_sevenzip_checksums(std::vector<uint8_t> *bytes)
{
    if (bytes->size() < 32) return;
    uint8_t *b = bytes->data();
    uint64_t offset = 0, size = 0;
    for (int i = 7; i >= 0; --i) { offset = (offset << 8) | b[12 + i]; size = (size << 8) | b[20 + i]; }
    if (offset <= bytes->size() - 32 && size <= bytes->size() - 32 - offset) put_u32_at(b + 28, crc32_update(0, b + 32 + offset, size_t(size)));
    put_u32_at(b + 8, crc32_update(0, b + 12, 20));
}

// A 7z of one folder whose coder is LZMA or LZMA2, with a plain header written here: random packed bytes and a claimed size.
std::vector<uint8_t> random_lzma_archive(Random &rng, uint64_t claim)
{
    std::vector<uint8_t> packed;
    for (unsigned n = 1 + rng.below(400); n--;) packed.push_back(uint8_t(rng.below(256)));
    if (rng.below(4)) packed[0] = rng.coin() ? 0 : uint8_t(0x80 | rng.below(0x80));
    const bool lzma2 = rng.coin();
    auto number = [](uint64_t value) {
        std::vector<uint8_t> out = {0xFF};
        for (int i = 0; i < 8; ++i) out.push_back(uint8_t(value >> (8 * i)));
        return out;
    };
    std::vector<uint8_t> header = {0x01, 0x04, 0x06, 0x00, 0x01, 0x09};
    auto append = [&](const std::vector<uint8_t> &v) { header.insert(header.end(), v.begin(), v.end()); };
    append(number(packed.size()));
    header.insert(header.end(), {0x00, 0x07, 0x0B, 0x01, 0x00, 0x01});
    if (lzma2) header.insert(header.end(), {0x21, 0x21, 0x01, uint8_t(rng.below(41))});
    else header.insert(header.end(), {0x23, 0x03, 0x01, 0x01, 0x05, uint8_t(rng.below(225)), uint8_t(rng.below(256)), uint8_t(rng.below(256)), uint8_t(rng.below(256)), uint8_t(rng.below(256))});
    header.push_back(0x0C);
    append(number(claim));
    header.insert(header.end(), {0x00, 0x00, 0x05, 0x01, 0x11, 0x05, 0x00, 'x', 0x00, 0x00, 0x00, 0x00, 0x00});
    std::vector<uint8_t> bytes = {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C, 0, 4, 0, 0, 0, 0};
    const uint64_t offset = packed.size();
    for (int i = 0; i < 8; ++i) bytes.push_back(uint8_t(offset >> (8 * i)));
    for (int i = 0; i < 8; ++i) bytes.push_back(uint8_t(uint64_t(header.size()) >> (8 * i)));
    bytes.insert(bytes.end(), 4, 0);
    bytes.insert(bytes.end(), packed.begin(), packed.end());
    bytes.insert(bytes.end(), header.begin(), header.end());
    put_u32_at(bytes.data() + 28, crc32_update(0, header.data(), header.size()));
    put_u32_at(bytes.data() + 8, crc32_update(0, bytes.data() + 12, 20));
    return bytes;
}

int fuzz_archive(uint64_t seed, uint64_t iterations, const std::string &directory)
{
    static const char *const kinds[] = {"clean", "flip", "flip-fixed", "truncated", "cut-fixed", "member", "random-lzma", "big-claim", "manifest-flip", "manifest-random", "manifest-deep", "order"};
    Random rng(seed);
    const std::vector<ArchiveSeed> seeds = load_archive_seeds(directory);
    ArchiveLimits limits;
    limits.sevenzip.max_unpack = 3u << 20;
    limits.sevenzip.max_header = 1u << 20;
    // Every disk the seeds can give, by CRC: whatever is accepted has to be one of them, byte for byte.
    std::map<uint32_t, std::vector<uint8_t>> originals;
    for (const ArchiveSeed &s : seeds) {
        DiskSet set;
        std::string why;
        if (!read_disk_set(s.bytes, "", limits, &set, &why)) bug("a seed archive was refused", 0, s.name + ": " + why);
        for (DiskMember &disk : set.disks) originals[crc32_update(0, disk.adf.data(), disk.adf.size())] = disk.adf;
    }
    std::map<std::string, std::pair<unsigned, unsigned>> histogram;
    std::map<std::string, unsigned> refusals;
    auto note_refusal = [&](const std::string &why) {
        std::string key;                                        // the reason without the names (in quotes), the numbers and what is in parentheses
        bool quoted = false;
        int nested = 0;
        for (char c : why) {
            if (c == '"') quoted = !quoted;
            else if (!quoted && c == '(') ++nested;
            else if (!quoted && c == ')') nested -= nested > 0;
            else if (!quoted && !nested && (c < '0' || c > '9')) key += c;
        }
        ++refusals[key.substr(0, 56)];
    };
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const std::string kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
        auto &count = histogram[kind];
        if (kind == "order") {
            static const char *const parts[] = {"Game", " ", "(Program)", "(Disk 1)", "(Disk 2)", ".adf", "/", "a", "B", "2", "10", "\xC3\xA9"};
            std::string names[3];
            for (std::string &name : names) for (unsigned n = 1 + rng.below(5); n--;) name += parts[rng.below(sizeof parts / sizeof *parts)];
            using archive_detail::disk_name_before;
            for (const std::string &x : names) if (disk_name_before(x, x)) bug("the order is not irreflexive", iteration, x);
            for (const std::string &x : names) for (const std::string &y : names) {
                if (disk_name_before(x, y) && disk_name_before(y, x)) bug("the order is not antisymmetric", iteration, x + " / " + y);
                if (x != y && !disk_name_before(x, y) && !disk_name_before(y, x)) bug("the order is not total", iteration, x + " / " + y);
            }
            for (const std::string &x : names) for (const std::string &y : names) for (const std::string &z : names)
                if (disk_name_before(x, y) && disk_name_before(y, z) && !disk_name_before(x, z)) bug("the order is not transitive", iteration, x + " / " + y + " / " + z);
            ++count.first;
            continue;
        }
        if (kind == "manifest-flip" || kind == "manifest-random" || kind == "manifest-deep") {
            static const char *const pieces[] = {"<rp9>", "</rp9>", "<application>", "</application>", "<media>", "</media>", "<floppy>", "</floppy>", "<floppy priority=\"1\" root=\"shared\"/>", "a.adf", "<!-- c -->",
                                                 "<![CDATA[b.adf]]>", "&amp;", "&#x41;", "&#0;", "&bogus;", "<?xml version=\"1.0\"?>", "<!DOCTYPE x>", "<!DOCTYPE x [<!ENTITY e \"f\">]>", " priority=\"7\"", "<x:floppy>",
                                                 "</x:floppy>", "\"", "'", "<", ">", "/", "\xEF\xBB\xBF", "<harddrive>h.hdf</harddrive>"};
            std::string text = "<rp9><requirements><host>x</host></requirements><application><media><floppy priority=\"2\">b.adf</floppy><floppy priority=\"1\" root=\"\">a.adf</floppy><harddrive>h.hdf</harddrive></media></application></rp9>";
            if (kind == "manifest-flip") {
                for (unsigned n = 1 + rng.below(4); n--;) text[rng.below(unsigned(text.size()))] ^= char(1u << rng.below(8));
            } else if (kind == "manifest-random") {
                text.clear();
                for (unsigned n = rng.below(60); n--;) text += pieces[rng.below(sizeof pieces / sizeof *pieces)];
            } else {
                for (unsigned n = rng.below(300); n--;) text += rng.coin() ? "<a>" : "<a x=\"1\"><floppy>";
            }
            archive_detail::Rp9Media media;
            std::string why;
            const bool accepted = archive_detail::parse_rp9_media(reinterpret_cast<const uint8_t *>(text.data()), text.size(), &media, &why);
            if (accepted) {
                ++count.first;
                if (media.floppies.size() > text.size()) bug("more floppies than bytes", iteration, kind);
                if (!why.empty()) bug("an accepted manifest left a reason", iteration, why);
            } else {
                ++count.second;
                if (why.empty()) bug("a refusal without a message", iteration, kind);
                note_refusal(why);
            }
            continue;
        }
        if (kind == "random-lzma" || kind == "big-claim") {
            const uint64_t claim = kind == "big-claim" ? (uint64_t(1) << (22 + rng.below(38))) : 1 + rng.below(200000);
            const std::vector<uint8_t> bytes = random_lzma_archive(rng, claim);
            sevenzip::Archive archive;
            std::string why;
            const bool opened = archive.open(bytes.data(), bytes.size(), limits.sevenzip, &why);
            bool accepted = false;
            if (opened) {
                for (size_t i = 0; i < archive.entries().size(); ++i) {
                    std::vector<uint8_t> data;
                    if (!archive.extract(i, &data, &why)) continue;
                    accepted = true;
                    if (data.size() != archive.entries()[i].size) bug("an extracted file has another size than its entry", iteration, kind);
                    if (data.size() > limits.sevenzip.max_unpack) bug("an extracted file is beyond the cap", iteration, kind);
                }
            }
            if (kind == "big-claim" && (accepted || opened)) bug("a claim beyond the cap was taken", iteration, kind);
            if (accepted) {
                ++count.first;
            } else {
                ++count.second;
                if (why.empty()) bug("a refusal without a message", iteration, kind);
                note_refusal(why);
            }
            continue;
        }
        const ArchiveSeed &base = seeds[rng.below(unsigned(seeds.size()))];
        std::vector<uint8_t> bytes = base.bytes;
        std::string member;
        const bool is_7z = sevenzip::looks_like_sevenzip(bytes.data(), bytes.size());
        const bool clean = kind == "clean";
        if (kind == "flip" || kind == "flip-fixed") {
            for (unsigned n = 1 + rng.below(3); n--;) bytes[rng.below(unsigned(bytes.size()))] ^= uint8_t(1u << rng.below(8));
            if (kind == "flip-fixed" && is_7z) fix_sevenzip_checksums(&bytes);
        } else if (kind == "truncated" || kind == "cut-fixed") {
            bytes.resize(rng.below(unsigned(bytes.size())));
            if (kind == "cut-fixed" && is_7z) fix_sevenzip_checksums(&bytes);
        } else if (kind == "member") {
            member = rng.coin() && !base.expect.empty() ? base.expect[rng.below(unsigned(base.expect.size()))].first : rng.coin() ? "missing.adf" : std::string(1, char('a' + rng.below(26)));
        }
        DiskSet set;
        std::string why;
        const bool accepted = read_disk_set(bytes, member, limits, &set, &why);
        if (accepted) {
            ++count.first;
            if (!why.empty()) bug("an accepted archive left a reason", iteration, why);
            if (set.disks.empty() || set.disks.size() > limits.max_disks) bug("a set with no disk or too many was accepted", iteration, kind);
            if (set.kind.empty()) bug("an accepted set without a kind", iteration, kind);
            for (const DiskMember &disk : set.disks) {
                const uint32_t crc = crc32_update(0, disk.adf.data(), disk.adf.size());
                if (disk.adf.size() != kDiskBytes) bug("a disk of the wrong size was accepted", iteration, disk.name);
                const auto original = originals.find(crc);
                if (original == originals.end() || original->second != disk.adf) bug("an accepted disk is not a disk of the seeds", iteration, kind + " " + base.name + " " + disk.name);
            }
            if (set.kind != "RP9 package" && member.empty())
                for (size_t i = 1; i < set.disks.size(); ++i)
                    if (archive_detail::disk_name_before(set.disks[i].name, set.disks[i - 1].name)) bug("a set is not in the order of its names", iteration, set.disks[i - 1].name + " / " + set.disks[i].name);
            if (clean) {
                std::vector<std::pair<std::string, uint32_t>> got;
                for (const DiskMember &disk : set.disks) got.push_back({disk.name, crc32_update(0, disk.adf.data(), disk.adf.size())});
                std::vector<std::pair<std::string, uint32_t>> want = base.expect;
                if (selftest()) std::reverse(want.begin(), want.end());
                if (got != want) bug("a clean archive did not give its disks in the expected order", iteration, base.name);
            }
        } else {
            ++count.second;
            if (why.empty()) bug("a refusal without a message", iteration, kind);
            if (clean) bug("a clean archive was refused", iteration, base.name + ": " + why);
            note_refusal(why);
        }
    }
    for (const auto &kind : histogram) printf("archive kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &reason : refusals) printf("archive refusal \"%s\": %u\n", reason.first.c_str(), reason.second);
    return 0;
}

// --- the automatic model and the tags in the names (T76) ---------------------------------------------------------------------------------------------

// An independent oracle for resolve_model (adapters/libretro/model.h): written from the table of docs/options.md with its own loops and its own numbers (1 A500, 2 A600, 3 A1200,
// 4 A500 OG, 5 A500+, 6 A1000, 7 A2000 OG, 8 A2000), so that the library and the oracle can only agree by being right.
struct OracleTag {
    const char *text;
    bool bare;
    int machine;            // 0 = a machine this core does not have (a warning)
    int memory;             // 0 none, 1 the A1200 preset (8 MiB Fast), 2 the A600 preset (2 MiB Chip + 8 MiB Fast)
};

const OracleTag kOracleTags[] = {
    {"(A4030)", false, 0, 0}, {"(030)", false, 0, 0}, {"(A4040)", false, 0, 0}, {"(040)", false, 0, 0},
    {"(A1200OG)", false, 3, 0}, {"(A1200NF)", false, 3, 0}, {"(A1200)", false, 3, 1}, {"CD32", true, 3, 1}, {"(CD32NF)", false, 3, 1}, {"(CD32FR)", false, 3, 1},
    {"(A600)", false, 2, 2}, {"(A500+)", false, 5, 0}, {"(A500PLUS)", false, 5, 0}, {"(A500OG)", false, 4, 0}, {"(512K)", false, 4, 0}, {"(512KB)", false, 4, 0},
    {"(A500)", false, 1, 0}, {"(A2000OG)", false, 7, 0}, {"(A2000)", false, 8, 0}, {"(A1000)", false, 6, 0},
};
const char *const kOracleWeak[] = {"AGA", "AmigaCD", "ECS", "OCS"};
const OracleTag kOracleNtsc[] = {{"NTSC", true, 0, 0}, {"(USA)", false, 0, 0}};
const OracleTag kOraclePal[] = {{"PAL", true, 0, 0}, {"(Europe)", false, 0, 0}, {"(France)", false, 0, 0}, {"(Germany)", false, 0, 0}, {"(Italy)", false, 0, 0},
                                {"(Spain)", false, 0, 0}, {"(Finland)", false, 0, 0}, {"(Denmark)", false, 0, 0}, {"(Sweden)", false, 0, 0}};

bool oracle_has(const std::string &name, const std::string &tag, bool bare)
{
    auto letter_or_digit = [](unsigned char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
    for (size_t at = 0; at + tag.size() <= name.size(); ++at) {
        if (name.compare(at, tag.size(), tag) != 0) continue;
        if (!bare) return true;
        const bool left = at == 0 || !letter_or_digit(static_cast<unsigned char>(name[at - 1]));
        const bool right = at + tag.size() == name.size() || !letter_or_digit(static_cast<unsigned char>(name[at + tag.size()]));
        if (left && right) return true;
    }
    return false;
}

struct Expected {
    uint32_t machine, video, chip, fast;
    std::string model_by;       // "tag", "word", "type", "explicit", "off", "blocked"
    std::string video_by;       // "tag", "option", "explicit", "off"
    const OracleTag *tag = nullptr;
};

Expected oracle_resolve(const p6_options::Settings &s, bool hard, const std::vector<std::string> &names)
{
    Expected e{s.machine, s.video_standard, s.chip_size, s.fast_size, "explicit", "explicit"};
    const bool tags = s.filename_tags != 0;
    if (s.machine == 0) {
        const uint32_t type_model = hard ? s.model_hd : s.model_fd;
        e.machine = type_model;
        e.model_by = tags ? "type" : "off";
        const OracleTag *decided = nullptr;
        for (size_t n = 0; tags && n < names.size() && !decided; ++n)
            for (const OracleTag &tag : kOracleTags)
                if (oracle_has(names[n], tag.text, tag.bare)) { decided = &tag; break; }
        if (decided) {
            e.tag = decided;
            if (decided->machine == 0) {
                e.model_by = "blocked";
            } else {
                e.model_by = "tag";
                e.machine = uint32_t(decided->machine);
                if (decided->memory == 2 && s.chip_size == 0) e.chip = 2048;
                if (decided->memory >= 1 && s.fast_size == 0) e.fast = 8192;
            }
        } else {
            const char *word = nullptr;
            for (size_t n = 0; tags && n < names.size() && !word; ++n)
                for (const char *candidate : kOracleWeak)
                    if (oracle_has(names[n], candidate, true)) { word = candidate; break; }
            if (word) {
                const std::string w = word;
                auto in = [&](std::initializer_list<uint32_t> list) { return std::find(list.begin(), list.end(), type_model) != list.end(); };
                if ((w == "AGA" || w == "AmigaCD") && !in({3})) { e.machine = 3; e.model_by = "word"; }
                else if (w == "ECS" && !in({5, 2, 8})) { e.machine = 2; e.model_by = "word"; }
                else if (w == "OCS" && !in({1, 4, 5, 6, 7})) { e.machine = 1; e.model_by = "word"; }
            }
        }
    }
    if (s.video_standard == 0 || s.video_standard == 3) {
        e.video = s.video_standard == 3 ? 2 : 1;
        e.video_by = tags ? "option" : "off";
        for (size_t n = 0; tags && n < names.size(); ++n) {
            bool ntsc = false, pal = false;
            for (const OracleTag &tag : kOracleNtsc) ntsc = ntsc || oracle_has(names[n], tag.text, tag.bare);
            for (const OracleTag &tag : kOraclePal) pal = pal || oracle_has(names[n], tag.text, tag.bare);
            if (ntsc || pal) { e.video = ntsc ? 2 : 1; e.video_by = "tag"; break; }
        }
    }
    return e;
}

const char *const kTagWords[] = {"(A4030)", "(030)", "(A4040)", "(040)", "(A1200OG)", "(A1200NF)", "(A1200)", "CD32", "(CD32NF)", "(CD32FR)", "(CD32)", "(A600)", "(A500+)", "(A500PLUS)",
                                 "(A500OG)", "(512K)", "(512KB)", "(A500)", "(A2000OG)", "(A2000)", "(A1000)", "AGA", "AmigaCD", "ECS", "OCS", "NTSC", "(USA)", "PAL", "(Europe)",
                                 "(France)", "(Germany)", "(Italy)", "(Spain)", "(Finland)", "(Denmark)", "(Sweden)", "CDTV", "(MD)", "_Arcadia", "(CE)"};
const char *const kFillers[] = {"Game", " ", "-", "_", ".", "/", "\\", "Disk 1", "(Disk 2)", "[!]", "v1.2", "0", "9", "a", "Z", "\xC3\xA9", "\xE2\x82\xAC", "(", ")", "+", "OG", "NF", "FR",
                                "/home/user/Amiga/", "C:\\Games\\", ".adf", ".zip#", "Some Game (1991)(Publisher)"};

std::string random_tag_name(Random &rng, unsigned tags)
{
    std::string name = rng.coin() ? rng.pick(kFillers) : "";
    for (unsigned i = 0; i < tags; ++i) {
        name += rng.pick(kFillers);
        name += rng.pick(kTagWords);
        if (rng.coin()) name += rng.pick(kFillers);
    }
    return name + ".adf";
}

// A tag that is wrong in one small way: a character changed, dropped or added, a case flipped, a bare word glued to a letter or a digit.
std::string near_miss(Random &rng)
{
    std::string tag = rng.pick(kTagWords);
    switch (rng.below(7)) {
    case 0: tag[rng.below(uint32_t(tag.size()))] ^= 0x20; break;                         // a case flip (a no-op on a digit or a symbol: still a legal name)
    case 1: tag.erase(rng.below(uint32_t(tag.size())), 1); break;
    case 2: tag.insert(rng.below(uint32_t(tag.size() + 1)), 1, char('A' + rng.below(26))); break;
    case 3: tag = "x" + tag; break;
    case 4: tag += "x"; break;
    case 5: tag.insert(rng.below(uint32_t(tag.size() + 1)), " "); break;
    default: tag += std::to_string(rng.below(10)); break;
    }
    return std::string(rng.pick(kFillers)) + tag + rng.pick(kFillers);
}

std::string hostile_name(Random &rng)
{
    std::string name;
    switch (rng.below(5)) {
    case 0: name.resize(rng.below(200)); for (char &c : name) c = char(rng.below(256)); break;                                         // bytes, NUL and invalid UTF-8 included
    case 1: name.assign(kMaxPathBytes - 1 + rng.below(3), 'p'); name.insert(rng.below(uint32_t(name.size())), rng.pick(kTagWords)); break;       // at the cap
    case 2: name.assign(60000 + rng.below(40000), 'A'); name += rng.pick(kTagWords); break;                                             // far beyond any path
    case 3: for (unsigned i = 0; i < 300; ++i) name += rng.pick(kTagWords); break;                                                     // every tag, many times
    default: name = "(((((((" + std::string(rng.below(100), '(') + rng.pick(kTagWords) + std::string(rng.below(100), ')'); break;
    }
    return name;
}

int fuzz_model(uint64_t seed, uint64_t iterations)
{
    static const char *const kinds[] = {"clean", "multi", "near", "bare", "hostile", "explicit", "off", "order"};
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> histogram;
    std::map<std::string, unsigned> decided, warned;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const std::string kind = kinds[rng.below(sizeof kinds / sizeof *kinds)];
        p6_options::Settings settings;
        settings.machine = rng.below(3) ? 0 : 1 + rng.below(8);
        static const uint32_t videos[] = {0, 3, 1, 2};
        settings.video_standard = videos[rng.below(4)];
        static const uint32_t chips[] = {0, 512, 1024, 2048}, fasts[] = {0, 1024, 2048, 4096, 8192};
        settings.chip_size = chips[rng.below(4)];
        settings.fast_size = fasts[rng.below(5)];
        settings.model_fd = 1 + rng.below(8);
        settings.model_hd = 1 + rng.below(8);
        settings.filename_tags = rng.below(8) ? 1 : 0;
        if (kind == "explicit") { settings.machine = 1 + rng.below(8); settings.video_standard = 1 + rng.below(2); settings.filename_tags = 1; }
        if (kind == "off") settings.filename_tags = 0;
        if (kind != "explicit" && kind != "off" && rng.below(4)) { settings.machine = 0; }
        const bool hard = rng.below(4) == 0;
        std::vector<std::string> names;
        const unsigned count = rng.below(9);
        for (unsigned i = 0; i < count; ++i) {
            if (kind == "clean") names.push_back(random_tag_name(rng, 1));
            else if (kind == "multi" || kind == "explicit" || kind == "off" || kind == "order") names.push_back(random_tag_name(rng, 1 + rng.below(4)));
            else if (kind == "near") names.push_back(near_miss(rng) + (rng.coin() ? random_tag_name(rng, 1) : std::string()));
            else if (kind == "bare") {
                // A bare word between neighbours: letters and digits glue it (no tag), anything else frees it.
                static const char *const words[] = {"PAL", "NTSC", "AGA", "AmigaCD", "ECS", "OCS", "CD32", "CDTV"};
                static const char *const glue[] = {"", "x", "Z", "0", "9", "_", " ", "-", ".", "(", ")", "\xC3\xA9", "/"};
                names.push_back(std::string(rng.pick(glue)) + rng.pick(words) + rng.pick(glue));
            } else names.push_back(hostile_name(rng));
        }
        if (kind == "order" && names.size() > 1) std::reverse(names.begin() + std::ptrdiff_t(rng.below(uint32_t(names.size()))), names.end());

        const ModelResolution first = resolve_model(settings, hard, names), again = resolve_model(settings, hard, names);
        const Expected want = oracle_resolve(settings, hard, names);
        auto &count_kind = histogram[kind];
        const bool by_tag = want.model_by == "tag" || want.model_by == "word" || want.video_by == "tag";
        (by_tag ? count_kind.first : count_kind.second)++;
        // Pure: the same inputs, the same answer, notes and warnings included.
        if (first.notes != again.notes || first.warnings != again.warnings || first.settings.machine != again.settings.machine || first.settings.video_standard != again.settings.video_standard ||
            first.settings.chip_size != again.settings.chip_size || first.settings.fast_size != again.settings.fast_size)
            bug("the same inputs gave two answers", iteration, kind);
        // Never automatic, always a machine and a standard of the ABI.
        if (first.settings.machine < 1 || first.settings.machine > 8) bug("the machine is not one of the eight", iteration, std::to_string(first.settings.machine));
        if (first.settings.video_standard != 1 && first.settings.video_standard != 2) bug("the video standard is not PAL or NTSC", iteration, std::to_string(first.settings.video_standard));
        uint32_t expected_machine = want.machine;
        if (selftest()) expected_machine = expected_machine % 8 + 1;
        if (first.settings.machine != expected_machine)
            bug("the machine is not the oracle's", iteration, kind + ": " + std::to_string(first.settings.machine) + " instead of " + std::to_string(expected_machine) + " by " + want.model_by);
        if (first.settings.video_standard != want.video) bug("the video standard is not the oracle's", iteration, kind + ": " + std::to_string(first.settings.video_standard) + " by " + want.video_by);
        if (first.settings.chip_size != want.chip || first.settings.fast_size != want.fast) bug("the memory is not the oracle's", iteration, kind);
        // Nothing else changes, and what a person chose is never changed by a tag.
        if (settings.machine != 0 && first.settings.machine != settings.machine) bug("a model chosen by hand was changed", iteration, kind);
        if ((settings.video_standard == 1 || settings.video_standard == 2) && first.settings.video_standard != settings.video_standard) bug("a standard chosen by hand was changed", iteration, kind);
        if (settings.machine != 0 && (first.settings.chip_size != settings.chip_size || first.settings.fast_size != settings.fast_size)) bug("the memory of a model chosen by hand was changed", iteration, kind);
        if (settings.fast_size != 0 && first.settings.fast_size != settings.fast_size) bug("a Fast RAM size chosen by hand was changed", iteration, kind);
        if (settings.chip_size != 0 && first.settings.chip_size != settings.chip_size) bug("a Chip RAM size chosen by hand was changed", iteration, kind);
        for (const p6_options::Option &option : p6_options::kOptions) {
            if (option.field == &p6_options::Settings::machine || option.field == &p6_options::Settings::video_standard || option.field == &p6_options::Settings::chip_size ||
                option.field == &p6_options::Settings::fast_size)
                continue;
            if (first.settings.*option.field != settings.*option.field) bug("an option the resolution does not own was changed", iteration, option.key);
        }
        // With the tags off nothing is read, so nothing is said about a tag; and what is said is bounded however long the names are.
        if (settings.filename_tags == 0 && !first.warnings.empty()) bug("a warning with the tags off", iteration, first.warnings[0]);
        if (first.notes.size() > 3 || first.warnings.size() > 4) bug("more messages than there is to say", iteration, std::to_string(first.notes.size()));
        for (const std::string &text : first.notes) if (text.size() > 600) bug("a note longer than a line of the log", iteration, std::to_string(text.size()));
        for (const std::string &text : first.warnings) if (text.size() > 600) bug("a warning longer than a line of the log", iteration, std::to_string(text.size()));
        if (want.model_by == "tag") ++decided[want.tag ? want.tag->text : "?"];
        if (want.model_by == "word") ++decided["word"];
        if (want.model_by == "blocked") ++warned["unsupported-model-tag"];
        if (want.video_by == "tag") ++decided["region"];
        if (!first.warnings.empty()) ++warned["warnings"];
        if (want.model_by == "blocked" && first.warnings.empty()) bug("a tag of a machine this core does not have was not warned", iteration, kind);
    }
    for (const auto &kind : histogram) printf("model kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &tag : decided) printf("model decided \"%s\": %u\n", tag.first.c_str(), tag.second);
    for (const auto &what : warned) printf("model warned \"%s\": %u\n", what.first.c_str(), what.second);
    return 0;
}

// --- the ROMs encrypted by Amiga Forever (T51) --------------------------------------------------------------------------------------------------------

// A byte string that starts like a Kickstart in one of the three ways WinUAE accepts, or like none.
std::vector<uint8_t> kickstart_like(Random &rng, size_t bytes, bool valid)
{
    std::vector<uint8_t> rom(bytes);
    for (uint8_t &b : rom) b = uint8_t(rng.next());
    if (!valid) {
        if (bytes >= 4) { rom[0] = 0x12; rom[2] = 0x00; }       // neither $11 $11/$14 first nor $4E $F9 at offset 2
        return rom;
    }
    if (bytes >= 4) {
        switch (rng.below(3)) {
        case 0: rom[0] = 0x11; rom[1] = 0x11; break;
        case 1: rom[0] = 0x11; rom[1] = 0x14; break;
        default: rom[2] = 0x4e; rom[3] = 0xf9; break;
        }
    }
    return rom;
}

std::vector<uint8_t> encrypt_oracle(const std::vector<uint8_t> &rom, const std::vector<uint8_t> &key)
{
    std::vector<uint8_t> out(kCloantoMagic, kCloantoMagic + 11);
    for (size_t n = 0; n < rom.size(); ++n) out.push_back(uint8_t(rom[n] ^ key[n % key.size()]));
    return out;
}

int fuzz_firmware(uint64_t seed, uint64_t iterations)
{
    Random rng(seed);
    std::map<std::string, std::pair<unsigned, unsigned>> histogram;       // kind -> (decrypted, not)
    std::map<std::string, unsigned> results;
    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {
        const unsigned shape = rng.below(9);
        const char *const kinds[] = {"clean", "clean-long-key", "plain", "near-tag", "no-key", "wrong-key", "short", "huge", "flipped"};
        const std::string kind = kinds[shape];
        const size_t sizes[] = {0, 1, 3, 4, 5, 256, 4096, 262144, 524288, 600000};
        size_t bytes = shape == 7 ? 524288 + rng.below(80000) : shape == 6 ? rng.below(4) : sizes[rng.below(10)];
        if (shape == 0 || shape == 1 || shape == 4 || shape == 5 || shape == 8) bytes = std::max<size_t>(bytes, 4);
        const bool valid = shape == 6 ? rng.coin() : shape == 5 ? rng.coin() : true;
        const std::vector<uint8_t> rom = kickstart_like(rng, bytes, valid);
        size_t key_bytes = shape == 1 ? 1 + rng.below(70000) : shape == 7 ? 1 : 1 + rng.below(2000);
        if (rng.below(8) == 0) key_bytes = 1 + rng.below(4);
        std::vector<uint8_t> key(key_bytes);
        for (uint8_t &b : key) b = uint8_t(rng.next());
        std::vector<uint8_t> file = encrypt_oracle(rom, key);
        std::vector<uint8_t> used_key = key;
        if (shape == 2) {                       // not encrypted at all: a plain ROM, noise, or the tag cut short
            file = rom;
            if (rng.coin() && file.size() >= 11) {
                memcpy(file.data(), kCloantoMagic, 11);
                file.resize(rng.below(11));
            }
        } else if (shape == 3) {                // one byte of the tag different (or the tag cut)
            if (file.size() > 11) file[rng.below(11)] ^= uint8_t(1 + rng.below(255));
        } else if (shape == 8) {                // the payload flipped somewhere
            if (file.size() > 11) file[11 + rng.below(file.size() - 11)] ^= uint8_t(1 + rng.below(255));
        }
        const bool encrypted = file.size() >= 11 && !memcmp(file.data(), kCloantoMagic, 11);
        if (shape == 4) used_key.clear();
        if (shape == 5) for (size_t i = 0; i < used_key.size(); ++i) used_key[i] ^= uint8_t(1 + rng.below(255));
        // The oracle: decrypt by position modulo the key length and apply WinUAE's test to the result.
        CloantoResult expected = CloantoResult::NotEncrypted;
        std::vector<uint8_t> want;
        if (encrypted) {
            if (used_key.empty()) {
                expected = CloantoResult::NeedsKey;
            } else {
                const size_t payload = file.size() - 11;
                for (size_t n = 0; n < payload; ++n) want.push_back(uint8_t(file[11 + n] ^ used_key[n % used_key.size()]));
                const bool like = payload >= 4 && ((want[2] == 0x4e && want[3] == 0xf9) || (want[0] == 0x11 && (want[1] == 0x11 || want[1] == 0x14)));
                expected = like ? CloantoResult::Decrypted : CloantoResult::WrongKey;
            }
        }
        if (selftest()) expected = expected == CloantoResult::Decrypted ? CloantoResult::WrongKey : CloantoResult::Decrypted;
        const std::vector<uint8_t> file_before = file, key_before = used_key;
        std::vector<uint8_t> out = {1, 2, 3};     // anything left in it must be cleared
        const CloantoResult got = decrypt_cloanto_rom(file.data(), file.size(), used_key.empty() ? nullptr : used_key.data(), used_key.size(), &out);
        if (got != expected) bug("the result is not the oracle's", iteration, kind + ": " + std::to_string(int(got)) + " instead of " + std::to_string(int(expected)));
        if (file != file_before || used_key != key_before) bug("the input was modified", iteration, kind);
        if (got == CloantoResult::Decrypted) {
            if (out != want) bug("the decrypted image is not the oracle's", iteration, kind);
            if ((shape == 0 || shape == 1 || shape == 7) && out != rom) bug("a round trip did not give the ROM back", iteration, kind);
        } else if (!out.empty()) {
            bug("something came out of a result that is not Decrypted", iteration, kind);
        }
        std::vector<uint8_t> again;
        if (decrypt_cloanto_rom(file.data(), file.size(), used_key.empty() ? nullptr : used_key.data(), used_key.size(), &again) != got || (got == CloantoResult::Decrypted && again != out))
            bug("the same input gave another answer", iteration, kind);
        if (is_cloanto_rom(file.data(), file.size()) != encrypted) bug("is_cloanto_rom disagrees with the tag", iteration, kind);
        auto &entry = histogram[kind];
        (got == CloantoResult::Decrypted ? entry.first : entry.second)++;
        ++results[got == CloantoResult::Decrypted ? "Decrypted" : got == CloantoResult::NeedsKey ? "NeedsKey" : got == CloantoResult::WrongKey ? "WrongKey" : "NotEncrypted"];
    }
    // The table of versions: seven choices, each with names, and nothing else (the value 0 is Automatic, the others are no choice).
    unsigned known = 0;
    for (uint32_t value = 0; value < 300; ++value) {
        const KickstartChoice *choice = kickstart_choice(value);
        if (!choice) continue;
        ++known;
        if (choice->value != value || !choice->count || !choice->names[0][0] || !choice->option[0]) bug("an entry of the table of versions is empty", value);
    }
    if (known != 7 || kickstart_choice(0xFFFFFFFFu) || kickstart_choice(0)) bug("the table of versions does not have exactly seven choices", known);
    for (const auto &kind : histogram) printf("firmware kind %s: accepted %u refused %u\n", kind.first.c_str(), kind.second.first, kind.second.second);
    for (const auto &what : results) printf("firmware result %s: %u\n", what.first.c_str(), what.second);
    return 0;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc == 4 && !strcmp(argv[1], "m3u")) return fuzz_m3u(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "vfs")) return fuzz_vfs(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "options")) return fuzz_options(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "hdz")) return fuzz_hdz(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "volume")) return fuzz_volume(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 5 && !strcmp(argv[1], "archive")) return fuzz_archive(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10), argv[4]);
    if (argc == 4 && !strcmp(argv[1], "model")) return fuzz_model(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    if (argc == 4 && !strcmp(argv[1], "firmware")) return fuzz_firmware(strtoull(argv[2], nullptr, 10), strtoull(argv[3], nullptr, 10));
    fprintf(stderr, "usage: %s m3u SEED N | vfs SEED N | options SEED N | hdz SEED N | volume SEED N | archive SEED N DIR | model SEED N | firmware SEED N\n", argv[0]);
    return 2;
}
