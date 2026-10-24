#include "cd_ccd.h"
#include "cd_file.h"
#include <string>
namespace p6_libretro {
using namespace p6_cd;
struct CdCcd::Data {
    CdFile img, sub;
    CcdMetadata metadata{};
    Layout layout{};
    Binding binding{};
    std::string text;
    size_t cursor = 0;
    static int line(void *ctx, char *out, size_t capacity)
    {
        auto &d = *static_cast<Data *>(ctx);
        if (d.cursor == d.text.size()) return 0;
        const auto end = d.text.find('\n', d.cursor);
        const auto length = (end == std::string::npos ? d.text.size() : end + 1) - d.cursor;
        if (length >= capacity) return -1;
        memcpy(out, d.text.data() + d.cursor, length); out[length] = 0; d.cursor += length;
        return int(length);
    }
    static bool read(const void *ctx, uint64_t offset, void *out, size_t length, size_t *got)
    {
        const auto &d = *static_cast<const Data *>(ctx); *got = 0;
        const auto bytes = d.binding.identity.bytes;
        if (length > 2448 || (length && !out) || offset > bytes || length > bytes - offset) return false;
        uint8_t staging[2448], sector[2448];
        for (size_t done = 0; done < length;) {
            const auto at = offset + done, lba = at / 2448, within = at % 2448;
            if (!d.img.read_at(lba * 2352, sector, 2352) || !d.sub.read_at(lba * 96, sector + 2352, 96)) return false;
            const size_t take = length - done < 2448 - within ? length - done : size_t(2448 - within);
            memcpy(staging + done, sector + within, take); done += take;
        }
        if (length) memcpy(out, staging, length);
        *got = length; return true;
    }
    Result prepare()
    {
        const uint64_t sectors = img.size() / 2352;
        if (!sectors || img.size() % 2352 || sub.size() != sectors * 96 || sectors > INT_MAX - 150) return Result::Range;
        binding = {{{0}, sectors * 2448, 1}, read, this};
        for (uint32_t n = 0; n < metadata.entries; ++n) if (metadata.entry[n].value[CcdPoint] == 0xa2)
            layout.leadout = uint32_t(metadata.entry[n].value[CcdPLba]);
        if (layout.leadout > sectors) return Result::Range;
        layout.tracks = metadata.tracks;
        for (uint32_t n = 0; n < metadata.tracks; ++n) {
            const auto &raw = metadata.track[n];
            // DiscSystem SS_CCD reads IMG=LBA*2352 and SUB=LBA*96. Negative
            // first INDEX00 is outside this domain; no150-sector file offset.
            const uint32_t start = n ? uint32_t(raw.has_index0 ? raw.index0 : raw.index1) : 0;
            uint32_t end = layout.leadout;
            if (n + 1 < metadata.tracks) {
                const auto &next = metadata.track[n + 1];
                end = uint32_t(next.has_index0 ? next.index0 : next.index1);
            }
            const CcdEntry *entry = nullptr;
            for (uint32_t i = 0; i < metadata.entries; ++i) if (metadata.entry[i].value[CcdPoint] == int(n + 1)) entry = &metadata.entry[i];
            if (!entry) return Result::Invalid;
            layout.track[n] = {start, uint32_t(raw.index1), end, 0, 2448, uint64_t(start) * 2448,
                               raw.mode == 0 ? Mode::Audio : raw.mode == 1 ? Mode::Mode1Raw : Mode::Mode2Raw,
                               uint8_t(entry->value[CcdAdr]), uint8_t(entry->value[CcdControl]), true};
        }
        Disc validation;
        const auto valid = validation.bind(layout, &binding, 1);
        if (valid != Result::Ok) return valid;
        uint8_t sector[2448]; p6_sha256 hash; p6_sha256_init(&hash);
        for (uint64_t lba = 0; lba < sectors; ++lba) {
            size_t got = 0;
            if (!read(this, lba * 2448, sector, sizeof sector, &got)) return Result::ReadError;
            p6_sha256_update(&hash, sector, sizeof sector);
        }
        p6_sha256_final(&hash, binding.identity.content_id);
        p6_sha256_init(&hash); p6_sha256_update(&hash, "P6CCD1", 6);
        auto put = [&](uint64_t value, unsigned bytes = 4) {
            uint8_t data[8];
            for (unsigned n = 0; n < bytes; ++n) data[n] = uint8_t(value >> (8 * n));
            p6_sha256_update(&hash, data, bytes);
        };
        put(binding.identity.bytes, 8); put(1); p6_sha256_update(&hash, binding.identity.content_id, 32);
        put(metadata.tracks); put(metadata.entries); put(metadata.pregap_mode); put(metadata.pregap_subc);
        for (uint32_t n = 0; n < metadata.entries; ++n) for (auto value : metadata.entry[n].value) put(uint32_t(value));
        for (uint32_t n = 0; n < metadata.tracks; ++n) {
            const auto &t = metadata.track[n];
            put(uint32_t(t.mode)); put(uint32_t(t.index0)); put(uint32_t(t.index1)); put(t.has_index0);
        }
        p6_sha256_final(&hash, layout.content_id);
        return Result::Ok;
    }
};
CdCcd::CdCcd() = default;
CdCcd::~CdCcd() = default;
Result CdCcd::open(const char *path, const retro_vfs_interface *vfs)
{
    if (!path || data_) return Result::Invalid;
    CdFile ccd;
    if (!ccd.open(path, vfs)) return Result::ReadError;
    if (!ccd.size() || ccd.size() > 65536) return Result::Range;
    auto candidate = std::make_unique<Data>(); candidate->text.resize(size_t(ccd.size()));
    if (!ccd.read_at(0, &candidate->text[0], candidate->text.size())) return Result::ReadError;
    const auto parsed = ccd_metadata(candidate.get(), Data::line, &candidate->metadata);
    if (parsed != Result::Ok) return parsed;
    const std::string filename = path; const auto dot = filename.find_last_of('.'), slash = filename.find_last_of("/\\");
    if (dot == std::string::npos || (slash != std::string::npos && dot < slash)) return Result::Invalid;
    const auto stem = filename.substr(0, dot);
    if (!candidate->img.open((stem + ".img").c_str(), vfs) || !candidate->sub.open((stem + ".sub").c_str(), vfs)) return Result::ReadError;
    const auto result = candidate->prepare();
    if (result == Result::Ok) data_ = std::move(candidate);
    return result;
}
const Layout *CdCcd::layout() const { return data_ ? &data_->layout : nullptr; }
const Binding *CdCcd::bindings() const { return data_ ? &data_->binding : nullptr; }
uint32_t CdCcd::sources() const { return data_ ? 1 : 0; }
const CcdMetadata *CdCcd::metadata() const { return data_ ? &data_->metadata : nullptr; }
}
