// Private CDv5 reader. Maps/codecs remain untrusted until T66t3/T66t4 pass.
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
    // compressed map<=16MiB. Resource limits return Unsupported, OOM ReadError.
    Result open(ReadFn, const void *context, uint64_t bytes);
    // <=2448 bytes, transactional output, including errors crossing hunks.
    Result read_at(uint64_t, void *, size_t) const;
    uint64_t bytes() const;
    uint32_t hunk_bytes() const;
    const char *error() const { return error_ ? error_ : ""; }
private:
    struct Data;
    std::unique_ptr<Data> data_;
    mutable const char *error_ = nullptr;
};
}
