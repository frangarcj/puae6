#include "chd_reader.h"
#include "cd_chd_metadata.h"
#include <libchdr/chd.h>
extern "C" {
#include <libchdr/bitstream.h>
#include <libchdr/huffman.h>
#include <libchdr/flac.h>
}
#include <algorithm>
#include <fstream>
#include <iterator>
#include <vector>
#include <set>
#include <cstdlib>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); abort(); } } while (0)
// Only the derived C target redirects allocation; C++ containers remain ordinary.
static std::set<void *> live;
static size_t allocation_calls = 0, allocation_fault = 0;
extern "C" void *chd_test_malloc(size_t bytes) {
    if (++allocation_calls == allocation_fault) return nullptr;
    void *p = std::malloc(bytes); if (p) live.insert(p); return p;
}
extern "C" void *chd_test_calloc(size_t n, size_t bytes) {
    if (++allocation_calls == allocation_fault) return nullptr;
    void *p = std::calloc(n, bytes); if (p) live.insert(p); return p;
}
extern "C" void *chd_test_realloc(void *p, size_t bytes) {
    if (++allocation_calls == allocation_fault) return nullptr;
    live.erase(p); void *next = std::realloc(p, bytes);
    if (next) live.insert(next); else if (p && bytes) live.insert(p); return next;
}
extern "C" void chd_test_free(void *p) { if (p) CHECK(live.erase(p) == 1); std::free(p); }
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
static void allocations(Fake original, const std::vector<uint8_t> &expected) {
    CHECK(live.empty()); allocation_calls = 0;
    { ChdReader r; uint8_t out[2448]; CHECK(r.open(Fake::read, &original, original.data.size()) == Result::Ok);
      CHECK(r.read_at(0, out, sizeof out) == Result::Ok); }
    const auto count = allocation_calls; CHECK(count && count < 256 && live.empty());
    for (size_t fail = 1; fail <= count; ++fail) {
        Fake f = original; allocation_calls = 0; allocation_fault = fail;
        { ChdReader r; uint8_t out[2448]; memset(out, 0xa5, sizeof out);
          auto status = r.open(Fake::read, &f, f.data.size());
          if (status == Result::Ok) status = r.read_at(0, out, sizeof out);
          if (status == Result::Ok) CHECK(!memcmp(out, expected.data(), sizeof out));
          else CHECK(status == Result::ReadError && std::all_of(out, out + sizeof out, [](uint8_t b) { return b == 0xa5; }) && strlen(r.error()));
          allocation_fault = 0;
          if (!r.bytes()) CHECK(r.open(Fake::read, &f, f.data.size()) == Result::Ok);
          CHECK(r.read_at(0, out, sizeof out) == Result::Ok && !memcmp(out, expected.data(), sizeof out) && f.calls < 5000);
        }
        CHECK(live.empty());
    }
}
static void cache(Fake f, const std::vector<uint8_t> &expected) {
    ChdReader r; CHECK(r.open(Fake::read, &f, f.data.size()) == Result::Ok); uint8_t out[2448];
    auto read = [&](uint64_t at) { CHECK(r.read_at(at, out, sizeof out) == Result::Ok && !memcmp(out, expected.data() + at, sizeof out)); };
    read(0); auto calls = f.calls;
    for (uint64_t at = 2448; at < r.hunk_bytes(); at += 2448) { read(at); CHECK(f.calls == calls); }
    for (unsigned kind = 0; kind < 3; ++kind) {
        auto status = kind == 0 ? r.read_at(r.bytes(), out, 1) : kind == 1 ? r.read_at(0, nullptr, 1) : r.read_at(0, out, 2449);
        CHECK(status == (kind == 0 ? Result::Range : Result::Invalid)); read(0); CHECK(f.calls > calls); calls = f.calls;
    }
    read(r.hunk_bytes()); CHECK(f.calls > calls); calls = f.calls;
    read(0); CHECK(f.calls > calls);
    for (bool short_read : {false, true}) {
        f.fault = f.calls + 1; f.short_read = short_read; memset(out, 0xa5, sizeof out);
        CHECK(r.read_at(r.hunk_bytes() - 800, out, sizeof out) == Result::ReadError);
        CHECK(std::all_of(out, out + sizeof out, [](uint8_t b) { return b == 0xa5; }));
        calls = f.calls; f.fault = 0; read(0); CHECK(f.calls > calls);
        read(r.hunk_bytes()); read(0);
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
static void metadata(Fake f) {
    core_file file{Fake::read, &f, f.data.size()}; chd_file *decoder = nullptr;
    CHECK(chd_open_file(&file, CHD_OPEN_READ, nullptr, &decoder) == CHDERR_NONE);
    uint8_t out[4], flags = 0; uint32_t length = 0, tag = 0;
    const auto wanted = CHD_MAKE_TAG('T','E','S','T');
    CHECK(chd_get_metadata(decoder, wanted, 0, out, sizeof out, &length, &tag, &flags) == CHDERR_NONE);
    CHECK(length == 4 && tag == wanted && flags == 0x81 && !memcmp(out, "\1\2\3\4", 4));
    memset(out, 0xa5, 4); length = 0x12345678; tag = 0x98765432; flags = 0xa5; f.fault = f.calls + 1;
    CHECK(chd_get_metadata(decoder, wanted + 1, 0, out, 4, &length, &tag, &flags) == CHDERR_READ_ERROR && out[0] == 0xa5);
    f.fault = f.calls + 2; f.short_read = true;
    CHECK(chd_get_metadata(decoder, wanted, 0, out, 4, &length, &tag, &flags) == CHDERR_READ_ERROR);
    CHECK(length == 0x12345678 && tag == 0x98765432 && flags == 0xa5);
    CHECK(std::all_of(out, out + 4, [](uint8_t b) { return b == 0xa5; }) && f.calls < 5000);
    chd_close(decoder);
}
static void toc(Fake original, const char *mode, const std::vector<uint8_t> &expected) {
    ChdReader r; CHECK(r.open(Fake::read, &original, original.data.size()) == Result::Ok);
    ChdMetadata metadata; memset(&metadata, 0xa5, sizeof metadata); uint8_t before[sizeof metadata]; memcpy(before, &metadata, sizeof metadata);
    auto start = original.calls; auto status = chd_metadata(r, &metadata);
    if (!strcmp(mode,"toc-invalid") || !strcmp(mode,"toc-unsupported")) {
        CHECK(status == (!strcmp(mode,"toc-invalid") ? Result::Invalid : Result::Unsupported) && !memcmp(before,&metadata,sizeof metadata)); return;
    }
    CHECK(status == Result::Ok && original.calls < 30000);
    if (!strcmp(mode,"toc-errors")) {
        const auto reads = original.calls - start;
        for (bool short_read : {false,true}) for (unsigned at = 1; at <= reads; ++at) {
            Fake f = original; ChdReader owner; CHECK(owner.open(Fake::read,&f,f.data.size()) == Result::Ok);
            f.fault = f.calls + at; f.short_read = short_read; memset(&metadata,0xa5,sizeof metadata);
            CHECK(chd_metadata(owner,&metadata) == Result::ReadError && !memcmp(before,&metadata,sizeof metadata));
        }
        return;
    }
    std::vector<uint8_t> bytes;
    auto put = [&](uint64_t value, unsigned width) { for (unsigned n = 0; n < width; ++n) bytes.push_back(uint8_t(value >> (n*8))); };
    put(metadata.tracks,4); put(metadata.padded_frames,8); put(metadata.tail_frames,8);
    for (unsigned n = 0; n < metadata.tracks; ++n) {
        const auto &t = metadata.track[n]; put(t.offset_frames,8);
        for (auto value : {t.number,t.frames,t.pregap,t.postgap,t.extra_frames,t.data_bytes,t.pg_data_bytes}) put(value,4);
        put(t.stored_pregap,1); put(t.flags,1);
        for (const auto &span : {std::pair{t.type,size_t(16)}, {t.subtype,size_t(8)}, {t.pgtype,size_t(16)}, {t.pgsub,size_t(8)}})
            bytes.insert(bytes.end(),span.first,span.first+span.second);
    }
    CHECK(bytes == expected);
}
static void query(Fake f) {
    ChdReader r; CHECK(r.open(Fake::read,&f,f.data.size()) == Result::Ok); uint8_t out[2448];
    uint32_t length = 0; uint8_t flags = 0; bool found = false; const auto tag = CHD_MAKE_TAG('T','E','S','T');
    const auto start = f.calls;
    CHECK(r.metadata(tag,0,out,4,&length,&flags,&found) == Result::Ok && found && length == 4 && flags == 0x81 && !memcmp(out,"\1\2\3\4",4));
    const auto calls = f.calls - start;
    auto sentinels = [&] { memset(out,0xa5,sizeof out); length = 0x12345678; flags = 0xa5; found = true; };
    auto intact = [&] { CHECK(length == 0x12345678 && flags == 0xa5 && found && std::all_of(out,out+sizeof out,[](uint8_t b){ return b == 0xa5; })); };
    sentinels(); CHECK(r.metadata(tag+1,0,out,4,&length,&flags,&found) == Result::Ok && !found); found = true; intact();
    sentinels(); CHECK(r.metadata(tag,0,out,3,&length,&flags,&found) == Result::Range); intact();
    CHECK(r.metadata(tag,0,nullptr,1,&length,&flags,&found) == Result::Invalid); intact();
    for (bool short_read : {false,true}) for (unsigned at = 1; at <= calls; ++at) {
        CHECK(r.read_at(0,out,sizeof out) == Result::Ok); sentinels(); f.fault = f.calls + at; f.short_read = short_read;
        CHECK(r.metadata(tag,0,out,4,&length,&flags,&found) == Result::ReadError); intact();
        auto after = f.calls; f.fault = 0; CHECK(r.read_at(0,out,sizeof out) == Result::Ok && f.calls > after);
    }
    for (unsigned at : {1,2}) {
        sentinels(); allocation_calls = 0; allocation_fault = at;
        CHECK(r.metadata(tag,0,out,4,&length,&flags,&found) == Result::ReadError); intact(); allocation_fault = 0;
    }
}
static void flac_wrapper(const std::vector<uint8_t> &frame) {
    flac_decoder decoder{}; CHECK(!flac_decoder_init(&decoder));
    CHECK(!flac_decoder_reset(&decoder, 48000, 2, 588, frame.data(), frame.size()));
    CHECK(!flac_decoder_reset(&decoder, 44100, 1, 588, frame.data(), frame.size()));
    CHECK(!flac_decoder_reset(&decoder, 44100, 2, 40000, frame.data(), frame.size()));
    CHECK(!flac_decoder_reset(&decoder, 44100, 2, 588, frame.data(), UINT32_MAX));
    int16_t out[1178];
    for (bool underflow : {false, true}) {
        CHECK(flac_decoder_reset(&decoder, 44100, 2, 588, frame.data(), frame.size()));
        memset(out, 0xa5, sizeof out);
        CHECK(flac_decoder_decode_interleaved(&decoder, out, 588, false));
        CHECK(std::all_of(out, out + 1176, [](int16_t n) { return n == 0; }));
        if (underflow) decoder.compressed_offset = 0;
        CHECK(flac_decoder_finish(&decoder) == (underflow ? UINT32_MAX : frame.size()) && !decoder.decoder);
    }
    CHECK(flac_decoder_reset(&decoder, 44100, 2, 588, frame.data(), frame.size()));
    CHECK(!flac_decoder_decode_interleaved(&decoder, out, 589, false)); flac_decoder_free(&decoder);
    CHECK(flac_decoder_finish(&decoder) == UINT32_MAX);
}
static void bits() {
    uint8_t bytes[4] = {0x80,0,0,0}; auto *stream = create_bitstream(bytes, 4);
    CHECK(bitstream_read(stream, 32) == 0x80000000u && !bitstream_overflow(stream));
    CHECK(bitstream_flush(stream) == 4); bitstream_read(stream, 1); CHECK(bitstream_overflow(stream)); chd_test_free(stream);
    stream = create_bitstream(bytes, 1); CHECK(bitstream_peek(stream, 8) == 0x80 && !bitstream_overflow(stream));
    CHECK(bitstream_read(stream, 1) == 1 && bitstream_read(stream, 7) == 0 && !bitstream_overflow(stream));
    bitstream_read(stream, 33); CHECK(bitstream_overflow(stream)); chd_test_free(stream);
    uint8_t unaligned[5] = {0xff,0x80,0,0,0}; stream = create_bitstream(unaligned, 5);
    CHECK(bitstream_read(stream, 1) == 1 && bitstream_peek(stream, 32) == 0xff000000u);
    CHECK(bitstream_read(stream, 32) == 0xff000000u && !bitstream_overflow(stream)); chd_test_free(stream);
    auto *decoder = create_huffman_decoder(16, 8); stream = create_bitstream(bytes, 1);
    huffman_decode_one(decoder, stream); CHECK(bitstream_overflow(stream)); delete_huffman_decoder(decoder); chd_test_free(stream);
}
int main(int argc, char **argv) {
    CHECK(argc == 4); Fake f{load(argv[2])};
    if (!strcmp(argv[1], "positive")) positive(f, load(argv[3]));
    else if (!strcmp(argv[1], "faults")) faults(f, load(argv[3]));
    else if (!strcmp(argv[1], "headers")) headers(f, Fake{load(argv[3])});
    else if (!strcmp(argv[1], "metadata")) metadata(f);
    else if (!strncmp(argv[1], "toc",3)) toc(f, argv[1], load(argv[3]));
    else if (!strcmp(argv[1], "query")) query(f);
    else if (!strcmp(argv[1], "bits")) bits();
    else if (!strcmp(argv[1], "flac-wrapper")) flac_wrapper(f.data);
    else if (!strcmp(argv[1], "cache")) cache(f, load(argv[3]));
    else if (!strcmp(argv[1], "allocations")) allocations(f, load(argv[3]));
    else if (!strcmp(argv[1], "bad-read")) {
        for (unsigned n = 0; n < 3; ++n) {
            ChdReader r; CHECK(r.open(Fake::read, &f, f.data.size()) == Result::Ok); uint8_t out[2448]; memset(out, 0xa5, sizeof out);
            CHECK(r.read_at(0, out, sizeof out) == Result::Invalid && strlen(r.error()));
            CHECK(std::all_of(out, out + sizeof out, [](uint8_t b) { return b == 0xa5; }) && f.calls < 5000);
        }
    }
    else {
        ChdReader r; auto status = r.open(Fake::read, &f, f.data.size()); uint8_t out[2448];
        if (!strcmp(argv[1], "reject") || !strcmp(argv[1], "limit")) {
            CHECK(status == (!strcmp(argv[1], "limit") ? Result::Unsupported : Result::Invalid) && !r.bytes() && f.calls < 5000);
            memset(out, 0xa5, sizeof out); CHECK(r.read_at(0, out, 1) == Result::Invalid && out[0] == 0xa5);
        } else if (!strcmp(argv[1], "accept")) CHECK(status == Result::Ok && f.calls < 5000);
        else if (!strcmp(argv[1], "zero")) { CHECK(status == Result::Ok); auto calls = f.calls;
            CHECK(r.read_at(0, out, sizeof out) == Result::Ok && f.calls == calls && std::all_of(out, out + sizeof out, [](uint8_t b) { return b == 0; }));
        } else { CHECK(!strcmp(argv[1], "self") && status == Result::Ok); auto expected = load(argv[3]);
            for (uint64_t at = 0; at < r.bytes(); at += 2448) CHECK(r.read_at(at, out, sizeof out) == Result::Ok && !memcmp(out, expected.data(), sizeof out));
            CHECK(f.calls < r.bytes() / 2448 + 100);
        }
    }
    CHECK(live.empty());
}
