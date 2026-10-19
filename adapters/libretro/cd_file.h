// Native host-only seekable source. FileReader's whole-file contract is unchanged.
#pragma once
#include "libretro.h"
#include "cd_disc.h"
#include "cd_wave.h"
#include "sha256.h"
#include <limits.h>
#include <stdio.h>
#include <string.h>
namespace p6_libretro {
class CdFile {
public:
    CdFile() = default;
    CdFile(const CdFile &) = delete;
    CdFile &operator=(const CdFile &) = delete;
    ~CdFile() { close(); }
    // The VFS table and this object must outlive all bound Source/Disc reads.
    bool open(const char *path, const retro_vfs_interface *vfs = nullptr)
    {
        if (file_ || handle_ || !path) return false;
        int64_t size = -1;
        if (vfs) {
            if (!vfs->open || !vfs->close || !vfs->size || !vfs->seek || !vfs->read) return false;
            vfs_ = vfs;
            handle_ = vfs->open(path, RETRO_VFS_FILE_ACCESS_READ, RETRO_VFS_FILE_ACCESS_HINT_NONE);
            if (handle_) size = vfs->size(handle_);
        } else {
            file_ = fopen(path, "rb");
            if (file_ && seek(0, SEEK_END)) {
#ifdef _WIN32
                size = _ftelli64(file_);
#else
                size = ftello(file_);
#endif
            }
        }
        if (size < 0) { close(); return false; }
        bytes_ = uint64_t(size);
        return true;
    }
    uint64_t size() const { return bytes_; }
    const p6_cd::Slice &slice() const { return slice_; }
    // Exact, no partial output, at most64KiB. Host cursor is reset for every read.
    bool read_at(uint64_t offset, void *out, size_t length) const
    {
        if ((!file_ && !handle_) || length > kChunk || (length && !out) || offset > bytes_ || length > bytes_ - offset) return false;
        if (!length) return true;
        uint8_t staging[kChunk];
        if (handle_) {
            if (vfs_->seek(handle_, int64_t(offset), RETRO_VFS_SEEK_POSITION_START) != int64_t(offset) ||
                vfs_->read(handle_, staging, length) != int64_t(length)) return false;
        } else {
            if (!seek(offset, SEEK_SET) || fread(staging, 1, length, file_) != length) return false;
        }
        memcpy(out, staging, length);
        return true;
    }
    // Publish once: the context/slice cannot change behind an existing binding.
    p6_cd::Result bind(p6_cd::Encoding encoding, p6_cd::Binding *out)
    {
        using namespace p6_cd;
        if (bound_ || !out || (!file_ && !handle_) || !bytes_) return Result::Invalid;
        Slice candidate{0, bytes_, encoding};
        if (encoding == Encoding::AudioPcmS16Le) {
            const auto result = wave_slice(whole_read, this, bytes_, &candidate);
            if (result != Result::Ok) return result;
        } else if (encoding != Encoding::Opaque && encoding != Encoding::Iso2048) return Result::Unsupported;
        if (encoding == Encoding::Iso2048 && bytes_ % kIsoSectorBytes) return Result::Invalid;
        Identity id{};
        id.bytes = candidate.bytes; id.sector_bytes = encoding == Encoding::Iso2048 ? kIsoSectorBytes : 1;
        p6_sha256 hash; p6_sha256_init(&hash);
        uint8_t buffer[kChunk];
        for (uint64_t offset = 0; offset < candidate.bytes;) {
            const size_t length = size_t(candidate.bytes - offset < kChunk ? candidate.bytes - offset : kChunk);
            if (!read_at(candidate.offset + offset, buffer, length)) return Result::ReadError;
            p6_sha256_update(&hash, buffer, length); offset += length;
        }
        p6_sha256_final(&hash, id.content_id);
        slice_ = candidate; bound_ = true;
        *out = {id, slice_read, this};
        return Result::Ok;
    }
private:
    static constexpr size_t kChunk = 64 * 1024;
    static bool whole_read(const void *context, uint64_t at, void *out, size_t length, size_t *got)
    {
        const bool ok = static_cast<const CdFile *>(context)->read_at(at, out, length);
        *got = ok ? length : 0; return ok;
    }
    static bool slice_read(const void *context, uint64_t at, void *out, size_t length, size_t *got)
    {
        const auto &file = *static_cast<const CdFile *>(context);
        *got = 0;
        if (at > file.slice_.bytes || length > file.slice_.bytes - at) return false;
        return whole_read(context, file.slice_.offset + at, out, length, got);
    }
    bool seek(uint64_t offset, int whence) const
    {
#ifdef _WIN32
        return _fseeki64(file_, int64_t(offset), whence) == 0;
#else
        static_assert(sizeof(off_t) >= 8, "CD source requires64-bit file offsets");
        return fseeko(file_, off_t(offset), whence) == 0;
#endif
    }
    void close()
    {
        if (handle_) vfs_->close(handle_);
        if (file_) fclose(file_);
        handle_ = nullptr; file_ = nullptr; vfs_ = nullptr; bytes_ = 0;
    }
    const retro_vfs_interface *vfs_ = nullptr;
    retro_vfs_file_handle *handle_ = nullptr;
    FILE *file_ = nullptr;
    uint64_t bytes_ = 0;
    p6_cd::Slice slice_{};
    bool bound_ = false;
};
}
