#include "cd_cue.h"
#include "cd_file.h"
#include "cd_cue_metadata.h"
#include <algorithm>
#include <string>
namespace p6_libretro {
using namespace p6_cd;
struct CdCue::Data {
    std::unique_ptr<CdFile> files[kMaxSources];
    std::string paths[kMaxSources], directory, text;
    Encoding types[kMaxSources]{};
    Binding binding[kMaxSources]{};
    Layout layout{};
    const retro_vfs_interface *vfs = nullptr;
    size_t cursor = 0;
    uint32_t count = 0;
    static int line(void *ctx, char *out, size_t capacity)
    {
        auto &d = *static_cast<Data *>(ctx);
        if (d.cursor == d.text.size()) return 0;
        const auto newline = d.text.find('\n', d.cursor);
        const size_t length = (newline == std::string::npos ? d.text.size() : newline + 1) - d.cursor;
        if (length >= capacity) return -1;
        memcpy(out, d.text.data() + d.cursor, length); out[length] = 0; d.cursor += length;
        return int(length);
    }
    static Result resolve(void *ctx, const char *name, Encoding type, CueSource *out)
    {
        auto &d = *static_cast<Data *>(ctx);
        std::string path = name;
        std::replace(path.begin(), path.end(), '\\', '/');
        if (path[0] != '/' && !(path.size() > 1 && path[1] == ':')) path = d.directory + path;
        for (uint32_t n = 0; n < d.count; ++n) if (d.paths[n] == path && d.types[n] == type) {
            *out = {n, d.binding[n].identity.bytes}; return Result::Ok;
        }
        if (d.count == kMaxSources) return Result::Range;
        auto file = std::make_unique<CdFile>();
        if (!file->open(path.c_str(), d.vfs)) return Result::ReadError;
        const auto result = file->bind(type, &d.binding[d.count]);
        if (result != Result::Ok) return result;
        *out = {d.count, d.binding[d.count].identity.bytes};
        d.paths[d.count] = path; d.types[d.count] = type; d.files[d.count++] = std::move(file);
        return Result::Ok;
    }
    Result normalize(const CueMetadata &cue)
    {
        uint64_t offset = 0, clock = 0, cursor_lba = 0;
        for (uint32_t n = 0; n < cue.tracks; ++n) {
            const auto &c = cue.track[n];
            if (c.stride != 2048 && c.stride != 2352) return Result::Unsupported;
            const uint64_t start = c.has_index0 ? c.index0 : c.index1;
            if (c.new_file) offset = start * c.stride;
            else {
                const auto &prev = cue.track[n - 1];
                if (start <= prev.index1 || c.source != prev.source) return Result::Invalid;
                offset += (start - clock) * prev.stride;
            }
            const auto bytes = binding[c.source].identity.bytes;
            if (offset >= bytes) return Result::Range;
            uint64_t stored = 0;
            if (n + 1 < cue.tracks && !cue.track[n + 1].new_file) {
                const auto &next = cue.track[n + 1];
                const auto next_start = next.has_index0 ? next.index0 : next.index1;
                if (next_start <= c.index1) return Result::Invalid;
                stored = next_start - start;
            } else {
                if ((bytes - offset) % c.stride) return Result::Invalid;
                stored = (bytes - offset) / c.stride;
            }
            const uint64_t index1 = cursor_lba + c.pregap + c.index1 - start;
            const uint64_t end = cursor_lba + c.pregap + stored + c.postgap;
            if (stored > (bytes - offset) / c.stride || index1 >= end - c.postgap || end > INT_MAX - 150) return Result::Range;
            layout.track[n] = {uint32_t(cursor_lba), uint32_t(index1), uint32_t(end), c.source, c.stride, offset,
                               c.mode, 1, c.control, c.has_index0, c.pregap, c.postgap};
            clock = start; cursor_lba = end;
        }
        layout.tracks = cue.tracks; layout.leadout = uint32_t(cursor_lba);
        // Canonical LE fields, never C++ padding, host paths, cursors or timestamps.
        p6_sha256 hash; p6_sha256_init(&hash); p6_sha256_update(&hash, "P6CUE1", 6);
        auto put = [&](uint64_t value, unsigned bytes) {
            uint8_t encoded[8];
            for (unsigned n = 0; n < bytes; ++n) encoded[n] = uint8_t(value >> (n * 8));
            p6_sha256_update(&hash, encoded, bytes);
        };
        put(count, 4); put(layout.tracks, 4); put(layout.leadout, 4);
        for (uint32_t n = 0; n < count; ++n) {
            put(unsigned(types[n]), 4); put(binding[n].identity.bytes, 8); put(binding[n].identity.sector_bytes, 4);
            p6_sha256_update(&hash, binding[n].identity.content_id, 32);
        }
        for (uint32_t n = 0; n < layout.tracks; ++n) {
            const auto &t = layout.track[n];
            for (auto value : {t.index0, t.index1, t.end, t.source, t.stride}) put(value, 4);
            put(t.offset, 8); put(unsigned(t.mode), 4); put(t.adr, 4); put(t.control, 4);
            put(t.stored_pregap, 4); put(t.synthetic_pregap, 4); put(t.postgap, 4);
        }
        p6_sha256_final(&hash, layout.content_id);
        Disc validation;
        return validation.bind(layout, binding, count);
    }
};
CdCue::CdCue() = default;
CdCue::~CdCue() = default;
Result CdCue::open(const char *path, const retro_vfs_interface *vfs)
{
    if (!path || data_) return Result::Invalid;
    CdFile cue;
    if (!cue.open(path, vfs)) return Result::ReadError;
    if (!cue.size() || cue.size() > 65536) return Result::Range;
    auto candidate = std::make_unique<Data>(); candidate->vfs = vfs;
    candidate->text.resize(size_t(cue.size()));
    if (!cue.read_at(0, &candidate->text[0], candidate->text.size())) return Result::ReadError;
    const std::string filename = path;
    const auto slash = filename.find_last_of("/\\");
    candidate->directory = slash == std::string::npos ? "" : filename.substr(0, slash + 1);
    CueMetadata metadata{};
    auto result = cue_metadata({candidate.get(), Data::line, Data::resolve}, &metadata);
    if (result == Result::Ok) result = candidate->normalize(metadata);
    if (result == Result::Ok) data_ = std::move(candidate);
    return result;
}
const Layout *CdCue::layout() const { return data_ ? &data_->layout : nullptr; }
const Binding *CdCue::bindings() const { return data_ ? data_->binding : nullptr; }
uint32_t CdCue::sources() const { return data_ ? data_->count : 0; }
}
