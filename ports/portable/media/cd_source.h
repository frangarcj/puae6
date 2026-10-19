// Immutable CD source, private portable interface (T66a). No engine or public ABI.
#ifndef P6_MEDIA_CD_SOURCE_H
#define P6_MEDIA_CD_SOURCE_H
#include <stddef.h>
#include <stdint.h>

namespace p6_cd {
constexpr uint32_t kIsoSectorBytes = 2048;
constexpr size_t kMaxReadBytes = 2448;
struct Identity {
    uint8_t content_id[32]; // SHA-256 supplied/verified by the host, never a path.
    uint64_t bytes;
    uint32_t sector_bytes;  // 2048: aligned ISO source; 1: opaque byte source (BIN/container).
};
// The caller owns the context through the last read. For a given identity, bytes
// must be immutable; no reentry, wall-clock effects or cursor between requests.
// Write at most length bytes, set *got even on error; true alone is not an exact read.
using ReadFn = bool (*)(const void *context, uint64_t offset, void *out, size_t length, size_t *got);
enum class Result { Ok, Invalid, Incompatible, Range, ReadError, Unsupported };

class Source {
public:
    // Bind once. Metadata is copied; the image is never copied or allocated.
    Result bind(const Identity &identity, ReadFn read, const void *context);
    // Rebind after restore: identical ID, length and geometry required. Validate
    // everything before replacing either callback or context. A failure keeps both.
    Result rebind(const Identity &identity, ReadFn read, const void *context);
    const Identity &identity() const { return identity_; }
    // At most kMaxReadBytes; errors leave the destination unchanged. The caller
    // supplies length accessible output bytes. An empty read at EOF is valid.
    Result read_at(uint64_t offset, void *out, size_t length) const;
    Result read_sector(uint64_t lba, void *out, size_t capacity) const;
private:
    Identity identity_{};
    ReadFn read_ = nullptr;
    const void *context_ = nullptr;
};
} // namespace p6_cd
#endif
