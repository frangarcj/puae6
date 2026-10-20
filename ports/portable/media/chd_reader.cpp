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
    uint32_t hunk = 0;
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
Result ChdReader::open(ReadFn read, const void *context, uint64_t bytes)
{
    if (data_ || !read || !bytes) { error_ = "invalid binding"; return Result::Invalid; }
    std::unique_ptr<Data> candidate(new(std::nothrow) Data);
    if (!candidate) { error_ = "out of memory"; return Result::ReadError; }
    candidate->file = {read, context, bytes};
    auto e = chd_open_file(&candidate->file, CHD_OPEN_READ, nullptr, &candidate->decoder);
    if (e != CHDERR_NONE) { error_ = chd_error_string(e); return result(e); }
    const auto *header = chd_get_header(candidate->decoder);
    candidate->bytes = header->logicalbytes; candidate->hunk = header->hunkbytes;
    candidate->scratch.reset(new(std::nothrow) uint8_t[candidate->hunk]);
    if (!candidate->scratch) { error_ = "out of memory"; return Result::ReadError; }
    data_ = std::move(candidate); error_ = nullptr;
    return Result::Ok;
}
Result ChdReader::read_at(uint64_t offset, void *out, size_t count) const
{
    if (!data_ || count > kMaxReadBytes || (count && !out)) { error_ = "invalid read"; return Result::Invalid; }
    if (offset > data_->bytes || count > data_->bytes - offset) { error_ = "read range"; return Result::Range; }
    uint8_t staging[kMaxReadBytes];
    for (size_t done = 0; done < count;) {
        const uint64_t at = offset + done;
        const size_t within = at % data_->hunk, take = std::min(count - done, data_->hunk - within);
        auto e = chd_read(data_->decoder, uint32_t(at / data_->hunk), data_->scratch.get());
        if (e != CHDERR_NONE) { error_ = chd_error_string(e); return result(e); }
        memcpy(staging + done, data_->scratch.get() + within, take); done += take;
    }
    if (count) memcpy(out, staging, count);
    error_ = nullptr; return Result::Ok;
}
}
