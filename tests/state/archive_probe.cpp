// T15a: the field archive (ports/portable/state_io/archive.h) tested on its own, with no engine. Self-checking: prints "archive ok" and
// returns 0, or names the first failed expectation. The sanitizer build runs it too, which is what proves that a read never goes past the
// bytes it was given and a write never goes past its capacity (the buffers below are exact-size heap blocks with guard bytes).
#include "archive.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

using namespace p6_state;

#define EXPECT(condition)                                                                    \
    do {                                                                                     \
        if (!(condition)) { fprintf(stderr, "failed: %s (line %d)\n", #condition, __LINE__); exit(1); } \
    } while (0)

namespace {

// A little machine with every kind of field, to be saved, validated and applied.
struct Machine {
    uint8_t a = 0;
    uint16_t b = 0;
    uint32_t c = 0;
    uint64_t d = 0;
    int32_t e = 0;
    int64_t f = 0;
    bool g = false;
    float h = 0;
    double i = 0;
    uint8_t blob[5] = {};
    uint32_t count = 0;
    struct Item { uint16_t x; uint8_t y; } items[4] = {};
    uint32_t mode = 0;      // 0..3
};

void visit(Archive &ar, Machine &m)
{
    ar.num(m.a, "a");
    ar.num(m.b, "b");
    ar.num(m.c, "c");
    ar.num(m.d, "d");
    ar.num(m.e, "e");
    ar.num(m.f, "f");
    ar.flag(m.g, "g");
    ar.f32(m.h, "h");
    ar.f64(m.i, "i");
    ar.bytes(m.blob, sizeof m.blob, "blob");
    const uint32_t n = ar.count(m.count, 4, "count");
    for (uint32_t k = 0; k < n; ++k) {
        ar.num(m.items[k].x, "item.x");
        ar.num(m.items[k].y, "item.y");
    }
    ar.num<uint32_t>(m.mode, 0, 3, "mode");
}

Machine sample()
{
    Machine m;
    m.a = 0xA5; m.b = 0xBEEF; m.c = 0xDEADBEEF; m.d = 0x0123456789ABCDEFull; m.e = -123456; m.f = -(int64_t(1) << 40);
    m.g = true; m.h = -0.15625f; m.i = 3.141592653589793;
    memcpy(m.blob, "\x01\x02\x03\x04\x05", 5);
    m.count = 3;
    for (uint32_t k = 0; k < 3; ++k) m.items[k] = {uint16_t(100 + k), uint8_t(7 * k)};
    m.mode = 2;
    return m;
}

bool same(const Machine &x, const Machine &y)
{
    if (x.count != y.count) return false;
    for (uint32_t k = 0; k < x.count; ++k)
        if (x.items[k].x != y.items[k].x || x.items[k].y != y.items[k].y) return false;
    return x.a == y.a && x.b == y.b && x.c == y.c && x.d == y.d && x.e == y.e && x.f == y.f && x.g == y.g && !memcmp(&x.h, &y.h, sizeof x.h) &&
           !memcmp(&x.i, &y.i, sizeof x.i) && !memcmp(x.blob, y.blob, sizeof x.blob) && x.mode == y.mode;
}

// A block of exactly `size` bytes with guard bytes after it that must stay as they were.
struct Exact {
    std::vector<uint8_t> memory;
    size_t size;
    explicit Exact(size_t n) : memory(n + 16, 0xCC), size(n) {}
    uint8_t *data() { return memory.data(); }
    bool guard_intact() const { for (size_t k = size; k < memory.size(); ++k) if (memory[k] != 0xCC) return false; return true; }
};

struct Fields { std::vector<std::pair<uint64_t, uint64_t>> spans; std::vector<std::string> names; };
void note(void *context, uint64_t offset, uint64_t size, const char *name)
{
    Fields *fields = static_cast<Fields *>(context);
    fields->spans.push_back({offset, size});
    fields->names.push_back(name);
}

}  // namespace

int main()
{
    Machine original = sample();

    // Measure answers the largest state; Save of fewer entries is smaller, of the maximum equal.
    Archive measure = Archive::measurer();
    Machine scratch = original;
    visit(measure, scratch);
    EXPECT(measure.ok());
    const uint64_t largest = measure.position();
    EXPECT(largest == 1 + 2 + 4 + 8 + 4 + 8 + 1 + 4 + 8 + 5 + 4 + 4 * 3 + 4);
    Machine full = original;
    full.count = 4;
    full.items[3] = {9, 9};

    Exact exact_full{size_t(largest)};
    Archive save_full = Archive::saver(exact_full.data(), largest);
    visit(save_full, full);
    EXPECT(save_full.ok() && save_full.position() == largest && exact_full.guard_intact());

    // Save, then Validate (live variables untouched), then Apply into another machine.
    Exact bytes_exact{size_t(largest) - 3};
    Archive save = Archive::saver(bytes_exact.data(), bytes_exact.size);
    Fields fields;
    save.on_field(note, &fields);
    visit(save, original);
    EXPECT(save.ok() && save.position() == largest - 3 && bytes_exact.guard_intact());
    EXPECT(fields.names.size() == 9 + 1 + 1 + 3 * 2 + 1 && fields.names[0] == "a" && fields.spans[0].first == 0 && fields.spans[1].first == 1);
    const uint64_t length = save.position();

    Machine target;
    Archive validate = Archive::validator(bytes_exact.data(), length);
    visit(validate, target);
    validate.finish();
    EXPECT(validate.ok() && validate.position() == length);
    EXPECT(target.a == 0 && target.count == 0 && target.d == 0);        // validation wrote nothing
    Archive apply = Archive::applier(bytes_exact.data(), length);
    visit(apply, target);
    apply.finish();
    EXPECT(apply.ok());
    EXPECT(same(original, target));

    // Little endian, fixed width: the first bytes are a, then b low byte first.
    EXPECT(bytes_exact.data()[0] == 0xA5 && bytes_exact.data()[1] == 0xEF && bytes_exact.data()[2] == 0xBE);

    // Every truncation is refused, none reads past its bytes (the block is exact), and the first refusal sticks.
    for (uint64_t cut = 0; cut < length; ++cut) {
        Exact piece{size_t(cut)};
        memcpy(piece.data(), bytes_exact.data(), size_t(cut));
        Machine m;
        Archive a = Archive::validator(piece.data(), cut);
        visit(a, m);
        a.finish();
        EXPECT(!a.ok());
        const char *first = a.why();
        a.fail("a later problem");
        EXPECT(a.why() == first);
    }

    // Bytes after the last field are refused by finish().
    {
        Exact longer{size_t(length) + 1};
        memcpy(longer.data(), bytes_exact.data(), size_t(length));
        longer.data()[length] = 0;
        Machine m;
        Archive a = Archive::validator(longer.data(), length + 1);
        visit(a, m);
        a.finish();
        EXPECT(!a.ok());
    }

    // Ranges: a flag of 2, a count of 5 and a mode of 4 are refused by Validate and by Apply. Validate leaves the machine as it was; Apply
    // stops at the field it refuses and never stores the refused value.
    {
        std::vector<uint8_t> bad(bytes_exact.data(), bytes_exact.data() + length);
        const size_t flag_at = 1 + 2 + 4 + 8 + 4 + 8;
        const size_t count_at = flag_at + 1 + 4 + 8 + 5;
        const size_t mode_at = count_at + 4 + 3 * 3;
        for (int which = 0; which < 3; ++which) {
            std::vector<uint8_t> altered = bad;
            if (which == 0) altered[flag_at] = 2;
            if (which == 1) altered[count_at] = 5;
            if (which == 2) altered[mode_at] = 4;
            for (int mode = 0; mode < 2; ++mode) {
                Machine m = sample();
                m.a = 1;
                const Machine before = m;
                Archive a = mode ? Archive::applier(altered.data(), altered.size()) : Archive::validator(altered.data(), altered.size());
                visit(a, m);
                EXPECT(!a.ok());
                if (!mode) EXPECT(same(m, before));
                if (which >= 1) EXPECT(m.count == 3 && m.mode == 2);       // the refused value was never stored
            }
        }
    }

    // A buffer that is too small refuses the save and writes nothing beyond its capacity.
    for (uint64_t capacity = 0; capacity + 1 < largest - 3; capacity += 7) {
        Exact small{size_t(capacity)};
        Archive a = Archive::saver(small.data(), capacity);
        Machine m = original;
        visit(a, m);
        EXPECT(!a.ok() && small.guard_intact());
    }

    // A list longer than its maximum cannot be saved.
    {
        Exact room{256};
        Machine m = original;
        m.count = 5;
        Archive a = Archive::saver(room.data(), 256);
        visit(a, m);
        EXPECT(!a.ok() && room.guard_intact());
    }

    // The floating point bit patterns survive, including a NaN payload and negative zero.
    {
        float nan_in;
        const uint32_t pattern = 0x7FC00123u;
        memcpy(&nan_in, &pattern, 4);
        double zero = -0.0, out_zero = 1.0;
        float out_nan = 0;
        Exact room{12};
        Archive s = Archive::saver(room.data(), 12);
        s.f32(nan_in);
        s.f64(zero);
        EXPECT(s.ok());
        Archive r = Archive::applier(room.data(), 12);
        r.f32(out_nan);
        r.f64(out_zero);
        r.finish();
        uint32_t back;
        memcpy(&back, &out_nan, 4);
        EXPECT(r.ok() && back == pattern && std::signbit(out_zero) && out_zero == 0.0);
    }

    printf("archive ok\n");
    return 0;
}
