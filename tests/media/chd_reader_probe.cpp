#include "chd_reader.h"
#include <libchdr/chd.h>
#include <algorithm>
#include <fstream>
#include <iterator>
#include <vector>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); abort(); } } while (0)
using namespace p6_cd;
struct Fake {
    std::vector<uint8_t> data;
    mutable unsigned calls = 0, largest = 0;
    unsigned fault = 0;
    bool short_read = false;
    static bool read(const void *ctx, uint64_t at, void *out, size_t count, size_t *got) {
        const auto &f = *static_cast<const Fake *>(ctx);
        ++f.calls; f.largest = std::max(f.largest, unsigned(count)); CHECK(count <= 2448);
        *got = 0;
        if (at > f.data.size() || count > f.data.size() - at) return false;
        memcpy(out, f.data.data() + at, count); *got = count;
        if (f.calls != f.fault) return true;
        if (f.short_read) --*got;
        return f.short_read;
    }
};
static std::vector<uint8_t> load(const char *path) {
    std::ifstream file(path, std::ios::binary); CHECK(file.good());
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
static void put(Fake &f, unsigned at, uint64_t value, unsigned count = 4) {
    for (unsigned n = 0; n < count; ++n) f.data[at + n] = uint8_t(value >> (8 * (count - n - 1)));
}
static uint64_t get(const Fake &f, unsigned at, unsigned count = 4) {
    uint64_t result = 0; for (unsigned n = 0; n < count; ++n) result = (result << 8) | f.data[at + n]; return result;
}
static void positive(Fake f, const std::vector<uint8_t> &expected) {
    ChdReader reader; CHECK(reader.open(Fake::read, &f, f.data.size()) == Result::Ok);
    CHECK(reader.bytes() == expected.size() && reader.hunk_bytes() == 19584);
    uint8_t out[2448];
    const size_t chunks = (expected.size() + 2052) / 2053;
    for (unsigned pass = 0; pass < 3; ++pass) for (size_t i = 0; i < chunks; ++i) {
        size_t at = (pass == 1 ? chunks - 1 - i : i) * 2053, count = std::min(size_t(2053), expected.size() - at);
        CHECK(reader.read_at(at, out, count) == Result::Ok && !memcmp(out, expected.data() + at, count));
    }
    CHECK(reader.open(Fake::read, &f, f.data.size()) == Result::Invalid);
    const auto calls = f.calls; memset(out, 0xa5, sizeof out);
    CHECK(reader.read_at(UINT64_MAX, out, 1) == Result::Range && out[0] == 0xa5);
    CHECK(reader.read_at(reader.bytes(), nullptr, 0) == Result::Ok && f.calls == calls);
    CHECK(reader.read_at(0, nullptr, 1) == Result::Invalid && f.calls == calls);
    CHECK(reader.read_at(0, out, 2449) == Result::Invalid && f.calls == calls);
    for (bool invalid : {false, true}) {
        Fake repeat = f; put(repeat, 20, get(f, 16)); if (invalid) put(repeat, 24, 0xdeadbeef);
        ChdReader owner;
        CHECK(owner.open(Fake::read, &repeat, repeat.data.size()) == (invalid ? Result::Unsupported : Result::Ok));
        if (!invalid) CHECK(owner.read_at(0, out, sizeof out) == Result::Ok && !memcmp(out, expected.data(), sizeof out));
        else CHECK(owner.bytes() == 0 && strlen(owner.error()));
    }
}
static void faults(Fake original, const std::vector<uint8_t> &expected) {
    constexpr uint64_t offset = 19584 - 800; uint8_t out[2448]; unsigned calls;
    { Fake f = original; ChdReader r; CHECK(r.open(Fake::read, &f, f.data.size()) == Result::Ok);
      CHECK(r.read_at(offset, out, sizeof out) == Result::Ok); calls = f.calls; }
    for (bool short_read : {false, true}) for (unsigned fault = 1; fault <= calls; ++fault) {
        Fake f = original; f.fault = fault; f.short_read = short_read; ChdReader r;
        memset(out, 0xa5, sizeof out); auto result = r.open(Fake::read, &f, f.data.size());
        if (result == Result::Ok) result = r.read_at(offset, out, sizeof out);
        CHECK(result == Result::ReadError && strlen(r.error()));
        CHECK(std::all_of(out, out + sizeof out, [](uint8_t b) { return b == 0xa5; }));
        f.fault = 0;
        if (!r.bytes()) CHECK(r.open(Fake::read, &f, f.data.size()) == Result::Ok);
        CHECK(r.read_at(offset, out, sizeof out) == Result::Ok && !memcmp(out, expected.data() + offset, sizeof out));
    }
}
static void headers(Fake original, Fake compressed) {
    struct Case { unsigned at; uint64_t value; unsigned width; Result result; };
    for (auto c : {Case{0,0,1,Result::Invalid}, {8,123,4,Result::Invalid}, {12,4,4,Result::Unsupported},
         {56,0,4,Result::Invalid}, {56,2449,4,Result::Invalid}, {60,0,4,Result::Invalid}, {60,2048,4,Result::Invalid}, {60,1224,4,Result::Unsupported},
         {32,0,8,Result::Invalid}, {32,2449,8,Result::Invalid}, {56,2448*215,4,Result::Unsupported},
         {32,((1ull<<20)+1)*19584,8,Result::Unsupported}, {32,UINT64_MAX-UINT64_MAX%2448,8,Result::Unsupported},
         {40,UINT64_MAX,8,Result::Invalid}, {48,UINT64_MAX,8,Result::Invalid}, {104,1,1,Result::Unsupported}}) {
        Fake f = original; put(f, c.at, c.value, c.width); ChdReader r;
        CHECK(r.open(Fake::read, &f, f.data.size()) == c.result && !r.bytes());
    }
    put(compressed, unsigned(get(compressed, 40, 8)), 16*1024*1024+1);
    ChdReader r; CHECK(r.open(Fake::read, &compressed, compressed.data.size()) == Result::Unsupported);
    Fake truncated = original; truncated.data.resize(123); ChdReader short_header;
    CHECK(short_header.open(Fake::read, &truncated, truncated.data.size()) == Result::ReadError);
    core_file file{Fake::read, &original, original.data.size()}; chd_file *decoder = nullptr;
    CHECK(chd_open_file(&file, CHD_OPEN_READWRITE, nullptr, &decoder) == CHDERR_NOT_SUPPORTED && !decoder && original.calls == 0);
    CHECK(chd_open("unused", CHD_OPEN_READ, nullptr, &decoder) == CHDERR_NOT_SUPPORTED && !decoder);
    CHECK(chd_precache(nullptr) == CHDERR_NOT_SUPPORTED);
}
int main(int argc, char **argv) {
    CHECK(argc == 4); Fake f{load(argv[2])};
    if (!strcmp(argv[1], "positive")) positive(f, load(argv[3]));
    else if (!strcmp(argv[1], "faults")) faults(f, load(argv[3]));
    else { CHECK(!strcmp(argv[1], "headers")); headers(f, Fake{load(argv[3])}); }
}
