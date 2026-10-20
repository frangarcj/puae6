// Private read-only CDv5 reader without parents; no public CD provider.
#pragma once
#include "cd_source.h"
#include <memory>
namespace p6_cd {
class ChdReader {
public:
    ChdReader();
    ~ChdReader();
    ChdReader(const ChdReader &) = delete;
    ChdReader &operator=(const ChdReader &) = delete;
    // Immutable context is borrowed through destruction; no reentry or cursor.
    // CD units2448; hunk<=512KiB, <=2^20hunks, expanded map<=12MiB,
    // compressed map<=16MiB, compressed block<=1MiB. Metadata: <=4096 entries,
    // <=64KiB each, <=1MiB total. SELF resolves iteratively before publication.
    // Resource limits return Unsupported; I/O/allocation errors use ReadError.
    Result open(ReadFn, const void *context, uint64_t bytes);
    // <=2448 bytes, transactional output, including codec/I/O errors crossing hunks.
    // Decoders require exact input/output and stream completion (LZMA permits
    // CHD termination without an end marker); CD FLAC is 44100Hz/stereo/16-bit.
    Result read_at(uint64_t, void *, size_t) const;
    // cap0 queries presence/length/flags only. Otherwise payload must fit (<=64KiB).
    // Missing: Ok and found=false; errors preserve all outputs and invalidate cache.
    Result metadata(uint32_t tag, uint32_t index, void *, size_t capacity,
                    uint32_t *length, uint8_t *flags, bool *found) const;
    uint64_t bytes() const;
    uint32_t hunk_bytes() const;
    const char *error() const { return error_ ? error_ : ""; }
private:
    Result fail(Result, const char *) const;
    struct Data;
    std::unique_ptr<Data> data_;
    mutable const char *error_ = nullptr;
};
}
