// Bounded field-by-field state archive (docs/adr/0009-state-format.md, T15a).
//
// One visitor function per chunk describes the state once and serves four jobs, so that the order of the fields, their widths and
// their limits cannot drift between them:
//
//   Save      writes the live values into a caller buffer of known capacity,
//   Measure   counts the bytes of the largest state the visitor can write (arrays at their maximum),
//   Validate  reads a body and checks every value against its range without touching the machine,
//   Apply     reads the same body and stores the values into the live variables.
//
// Validate and Apply run the same code over the same bytes, so a body that Validate accepts cannot fail to apply for a structural
// reason: the analysis is as strict as the application by construction. Everything is a fixed-width little-endian integer, a byte
// run or a count with a maximum; no padding, no pointer and no struct is ever copied as memory. The first problem sticks: after it
// every call is a no-op that returns zero (a count of zero ends a loop), and why() names it.
//
// Plain C++17 with no allocation and no libc beyond memcpy: it is portable to every adapter.
#ifndef P6_STATE_ARCHIVE_H
#define P6_STATE_ARCHIVE_H
#include <stdint.h>
#include <string.h>
#include <type_traits>

namespace p6_state {

enum class Mode : uint8_t { Save, Measure, Validate, Apply };

// Called by Save for every field it writes (offset in the archive, width, name), for tools that need the field map.
typedef void (*FieldHook)(void *context, uint64_t offset, uint64_t size, const char *name);

class Archive {
public:
    static Archive saver(uint8_t *out, uint64_t capacity) { return Archive(Mode::Save, out, nullptr, capacity); }
    static Archive measurer() { return Archive(Mode::Measure, nullptr, nullptr, 0); }
    static Archive validator(const uint8_t *in, uint64_t size) { return Archive(Mode::Validate, nullptr, in, size); }
    static Archive applier(const uint8_t *in, uint64_t size) { return Archive(Mode::Apply, nullptr, in, size); }

    Mode mode() const { return mode_; }
    bool applying() const { return mode_ == Mode::Apply; }
    bool reading() const { return mode_ == Mode::Validate || mode_ == Mode::Apply; }
    bool ok() const { return why_ == nullptr; }
    const char *why() const { return why_; }
    uint64_t position() const { return pos_; }
    void on_field(FieldHook hook, void *context) { hook_ = hook; hook_context_ = context; }

    void fail(const char *why) { if (!why_) why_ = why ? why : "state refused"; }
    void require(bool condition, const char *why) { if (!condition) fail(why); }

    // After the last field of a body: reading must have used exactly the bytes there are.
    void finish()
    {
        if (reading() && ok() && pos_ != size_) fail("bytes after the last field of the body");
    }

    // The value to use for a field of integer type T. Save writes `live` and returns it; Measure counts it and returns `live`;
    // Validate and Apply read it and return what was read (zero on a problem). With a range, a value outside [lo, hi] is refused.
    template <class T> T val(T live, const char *name = "")
    {
        static_assert(std::is_integral<T>::value && !std::is_same<T, bool>::value, "fixed-width integers only");
        return field<T>(live, name);
    }
    template <class T> T val(T live, T lo, T hi, const char *name = "")
    {
        const T v = field<T>(live, name);
        if (reading() && ok() && (v < lo || v > hi)) { fail("a field is out of its range"); return lo; }
        return v;
    }

    // A live variable: Apply stores the value read, the other modes leave it alone.
    template <class T> void num(T &live, const char *name = "")
    {
        const T v = val<T>(live, name);
        if (applying() && ok()) live = v;
    }
    template <class T> void num(T &live, T lo, T hi, const char *name = "")
    {
        const T v = val<T>(live, lo, hi, name);
        if (applying() && ok()) live = v;
    }
    void flag(bool &live, const char *name = "")
    {
        const uint8_t v = val<uint8_t>(live ? 1 : 0, 0, 1, name);
        if (applying() && ok()) live = v != 0;
    }

    // Floating point values travel as their bit pattern.
    void f32(float &live, const char *name = "")
    {
        uint32_t bits;
        memcpy(&bits, &live, sizeof bits);
        const uint32_t v = val<uint32_t>(bits, name);
        if (applying() && ok()) memcpy(&live, &v, sizeof v);
    }
    void f64(double &live, const char *name = "")
    {
        uint64_t bits;
        memcpy(&bits, &live, sizeof bits);
        const uint64_t v = val<uint64_t>(bits, name);
        if (applying() && ok()) memcpy(&live, &v, sizeof v);
    }

    // A run of bytes. Save copies from `p`, Apply copies into it, Validate and Measure only account for it. `p` is never read or
    // written outside Save and Apply respectively, so a caller may pass a live buffer in every mode.
    void bytes(void *p, uint64_t n, const char *name = "")
    {
        if (!ok()) return;
        if (mode_ == Mode::Measure) { pos_ += n; return; }
        if (mode_ == Mode::Save) {
            if (n > size_ - pos_) { fail("the buffer is too small for the state"); return; }
            if (n) memcpy(out_ + pos_, p, size_t(n));
            record(n, name);
            pos_ += n;
            return;
        }
        if (n > size_ - pos_) { fail("the body ends before its last field"); return; }
        if (mode_ == Mode::Apply && n) memcpy(p, in_ + pos_, size_t(n));
        pos_ += n;
    }

    // The count of a list of at most `max` entries: returns how many entries follow. Save writes `live` (which must not exceed
    // the maximum), Measure answers `max` so that a loop over the entries counts them all, Validate and Apply read it and refuse
    // a larger one; Apply also stores it in `live`.
    uint32_t count(uint32_t &live, uint32_t max, const char *name = "")
    {
        if (mode_ == Mode::Measure) { field<uint32_t>(0, name); return max; }
        if (mode_ == Mode::Save && live > max) { fail("a list is longer than its bound"); return 0; }
        const uint32_t n = field<uint32_t>(live, name);
        if (reading() && ok() && n > max) { fail("a list is longer than its bound"); return 0; }
        if (!ok()) return 0;
        if (applying()) live = n;
        return n;
    }

private:
    Archive(Mode mode, uint8_t *out, const uint8_t *in, uint64_t size) : out_(out), in_(in), size_(size), mode_(mode) {}

    void record(uint64_t n, const char *name)
    {
        if (hook_) hook_(hook_context_, pos_, n, name ? name : "");
    }

    template <class T> T field(T live, const char *name)
    {
        if (!ok()) return reading() ? T(0) : live;
        const uint64_t width = sizeof(T);
        if (mode_ == Mode::Measure) { pos_ += width; return live; }
        if (mode_ == Mode::Save) {
            if (width > size_ - pos_) { fail("the buffer is too small for the state"); return live; }
            uint64_t bits = uint64_t(static_cast<typename std::make_unsigned<T>::type>(live));
            for (uint64_t i = 0; i < width; ++i) out_[pos_ + i] = uint8_t(bits >> (8 * i));
            record(width, name);
            pos_ += width;
            return live;
        }
        if (width > size_ - pos_) { fail("the body ends before its last field"); return T(0); }
        uint64_t bits = 0;
        for (uint64_t i = 0; i < width; ++i) bits |= uint64_t(in_[pos_ + i]) << (8 * i);
        pos_ += width;
        return T(static_cast<typename std::make_unsigned<T>::type>(bits));
    }

    uint8_t *out_;
    const uint8_t *in_;
    uint64_t size_;
    uint64_t pos_ = 0;
    Mode mode_;
    const char *why_ = nullptr;
    FieldHook hook_ = nullptr;
    void *hook_context_ = nullptr;
};

}  // namespace p6_state
#endif
