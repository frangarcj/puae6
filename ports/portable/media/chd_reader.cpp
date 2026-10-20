#include "chd_reader.h"
#include <libchdr/chd.h>
#include <algorithm>
#include <new>
#include <string.h>
namespace p6_cd {
struct ChdReader::Data {
    core_file file{};
    chd_file *decoder = nullptr;
    uint64_t bytes = 0;
    uint32_t hunk = 0, cached = UINT32_MAX;
    std::unique_ptr<uint8_t[]> scratch;
    ~Data() { if (decoder) chd_close(decoder); }
};
static Result result(chd_error e)
{
    if (e == CHDERR_NONE) return Result::Ok;
    if (e == CHDERR_NOT_SUPPORTED || e == CHDERR_UNSUPPORTED_FORMAT || e == CHDERR_UNSUPPORTED_VERSION) return Result::Unsupported;
    return e == CHDERR_READ_ERROR || e == CHDERR_OUT_OF_MEMORY ? Result::ReadError : Result::Invalid;
}
ChdReader::ChdReader() = default;
ChdReader::~ChdReader() = default;
uint64_t ChdReader::bytes() const { return data_ ? data_->bytes : 0; }
uint32_t ChdReader::hunk_bytes() const { return data_ ? data_->hunk : 0; }
Result ChdReader::fail(Result status, const char *why) const
{
    if (data_) data_->cached = UINT32_MAX;
    error_ = why; return status;
}
Result ChdReader::open(ReadFn read, const void *context, uint64_t bytes)
{
    if (data_ || !read || !bytes) return fail(Result::Invalid, "invalid binding");
    std::unique_ptr<Data> candidate(new(std::nothrow) Data);
    if (!candidate) return fail(Result::ReadError, "out of memory");
    candidate->file = {read, context, bytes};
    // Maps, SELF and the complete bounded metadata chain validate before publication.
    // Codec payloads validate on read, inside scratch before transactional delivery.
    auto e = chd_open_file(&candidate->file, CHD_OPEN_READ, nullptr, &candidate->decoder);
    if (e != CHDERR_NONE) return fail(result(e), chd_error_string(e));
    const auto *header = chd_get_header(candidate->decoder);
    candidate->bytes = header->logicalbytes; candidate->hunk = header->hunkbytes;
    candidate->scratch.reset(new(std::nothrow) uint8_t[candidate->hunk]);
    if (!candidate->scratch) return fail(Result::ReadError, "out of memory");
    data_ = std::move(candidate); error_ = nullptr;
    return Result::Ok;
}
Result ChdReader::read_at(uint64_t offset, void *out, size_t count) const
{
    if (!data_ || count > kMaxReadBytes || (count && !out)) return fail(Result::Invalid, "invalid read");
    if (offset > data_->bytes || count > data_->bytes - offset) return fail(Result::Range, "read range");
    uint8_t staging[kMaxReadBytes];
    for (size_t done = 0; done < count;) {
        const uint64_t at = offset + done;
        const size_t within = at % data_->hunk, take = std::min(count - done, data_->hunk - within);
        const auto hunk = uint32_t(at / data_->hunk);
        if (hunk != data_->cached) {
            data_->cached = UINT32_MAX;
            auto e = chd_read(data_->decoder, hunk, data_->scratch.get());
            if (e != CHDERR_NONE) return fail(result(e), chd_error_string(e));
            data_->cached = hunk;
        }
        memcpy(staging + done, data_->scratch.get() + within, take); done += take;
    }
    if (count) memcpy(out, staging, count);
    error_ = nullptr; return Result::Ok;
}
Result ChdReader::metadata(uint32_t tag, uint32_t index, void *out, size_t capacity,
                           uint32_t *length, uint8_t *flags, bool *found) const
{
    if (!data_ || !length || !flags || !found || capacity > 65536 || (capacity && !out)) return fail(Result::Invalid, "invalid metadata query");
    uint32_t size; uint8_t bits;
    auto e = chd_get_metadata(data_->decoder, tag, index, nullptr, 0, &size, nullptr, &bits);
    if (e == CHDERR_METADATA_NOT_FOUND) { *found = false; error_ = nullptr; return Result::Ok; }
    if (e != CHDERR_NONE) return fail(result(e), chd_error_string(e));
    if (capacity) {
        if (size > capacity) return fail(Result::Range, "metadata capacity");
        // libchdr stages this exact payload before copying, including short-read failures.
        e = chd_get_metadata(data_->decoder, tag, index, out, uint32_t(capacity), &size, nullptr, &bits);
        if (e != CHDERR_NONE) return fail(result(e), chd_error_string(e));
    }
    *length = size; *flags = bits; *found = true; error_ = nullptr; return Result::Ok;
}
}
