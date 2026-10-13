// Archives of disks (T59): a ZIP, a 7z or an RP9 package that holds several floppy disks is a SET of disks, read in memory like every other container (container.h): no
// temporary directory, no write, nothing of the host kept. This is PUAE5's behaviour (libretro-uae core.c: the archive is unpacked, its disk images are sorted and written as an M3U)
// without the directory and the file: the core builds the playlist itself. Header-only, no engine, no ABI; the ADFs it returns go to p6_load exactly like any other disk, and the
// identity of a medium is still the SHA-256 of its ADF (docs/adr/0008-media-identity.md), whatever archive, member or order it came by.
//
// The rules (docs/02-contracts.md, "Medios"):
//   - What is a disk. A member whose name ends in .adf, .adz, .dms or .fdi (T57). A member in a format the core refuses (.ipf, ...) is not skipped: the archive is refused, naming it, because
//     a set that silently lost a disk is worse than no set. Anything else (readmes, screenshots, folders, macOS litter) is ignored and the log says how many.
//   - The order. PUAE5 sorts the names with qsort and sensible_strcmp (libretro-glue.c). This is that order, made a total order: a name that holds "(Program" sorts before one that does not
//     (No-Intro marks the program disk of a set that way; PUAE5 does it with a comparison that is not consistent, so its result depends on qsort), then the names are compared byte by byte
//     with the space counting as '/' (so "Turrican.adf" < "Turrican II.adf" < "Turrican III.adf", which plain byte order gets wrong), then plain bytes, then the position in the archive.
//     The archive's own order is never used: the same disks give the same playlist from a ZIP, a 7z, whatever the tool that made it did. Members in folders take part with their whole path.
//   - A member by name. "file.zip#member.adf" (and .7z, .rp9) is that member alone: the way RetroArch names a file in an archive, and a playlist entry may use it. The name is the member's
//     path inside the archive, or its file name when that is unique.
//   - RP9 (Cloanto's RetroPlatform package: a ZIP with rp9-manifest.xml). The manifest's <media> section lists the floppies of the package, in the order of their priority attribute (document
//     order for equal or missing ones); that is the playlist. The rest of the manifest (the machine, memory, Kickstart, peripherals, hard drives, CDs, tapes, snapshots, boot, clips) is not
//     read: the machine is chosen with puae6_machine, and the log says that it was ignored. A floppy that lives in the player's shared folder (root="shared") is not in the package and is
//     ignored with a note; a floppy the manifest names and the package does not hold refuses the package. A ZIP that holds the manifest is read as an RP9 whatever its extension.
//   - Bounds, before anything is allocated: 4096 entries, 16 disks (what the manifest of media keeps), a member of at most 2 MiB (a disk file), 32 MiB of unpacked 7z data, a manifest of at
//     most 256 KiB with 64 levels of elements. A hostile archive is a message, never a large allocation.
//   - What is not here. A 7z holds disks only: a 7z of files is refused (a ZIP or an LHA is how a volume comes, volume.h). An archive holding both disks and an .hdf is a set of disks and
//     the .hdf is ignored; a ZIP of files with no disk is a volume (T64a), and a hard file in a ZIP is an .hdz.
#ifndef P6_LIBRETRO_ARCHIVE_H
#define P6_LIBRETRO_ARCHIVE_H

#include "container.h"
#include "sevenzip.h"

#include <algorithm>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <utility>
#include <vector>

namespace p6_libretro {

constexpr size_t kMaxArchiveDisks = 16;                  // P6_MAX_MEDIA: the most distinct media the manifest keeps (libretro_core.cpp checks it)
constexpr size_t kMaxArchiveManifestBytes = 256 * 1024;
constexpr size_t kMaxArchiveDepth = 64;                  // nesting of the elements of an RP9 manifest

struct ArchiveLimits {
    size_t max_disks = kMaxArchiveDisks;
    size_t max_member = kMaxDiskFileBytes;
    size_t max_manifest = kMaxArchiveManifestBytes;
    sevenzip::Limits sevenzip;
};

struct DiskMember {
    std::string name;                                    // the member's name inside the archive
    std::vector<uint8_t> adf;                            // the disk, 901120 bytes
};

struct DiskSet {
    std::string kind;                                    // "ZIP file", "7z archive" or "RP9 package"
    std::vector<DiskMember> disks;                       // in playlist order
    std::vector<std::string> notes;                      // what was ignored or not read, for the log
};

// The bytes of a ZIP or a 7z.
inline bool is_archive_bytes(const std::vector<uint8_t> &bytes)
{
    return (bytes.size() >= 4 && (!memcmp(bytes.data(), "PK\3\4", 4) || !memcmp(bytes.data(), "PK\5\6", 4))) || sevenzip::looks_like_sevenzip(bytes.data(), bytes.size());
}

inline bool is_archive_extension(const std::string &path)
{
    const std::string ext = container_detail::lower_extension(path);
    return ext == "zip" || ext == "7z" || ext == "rp9";
}

// "dir/game.zip#disk 2.adf" is the archive "dir/game.zip" and the member "disk 2.adf". The last '#' counts, and only after an archive extension: a file name with a '#' in it is a file name.
inline bool split_member(const std::string &path, std::string *archive, std::string *member)
{
    const size_t hash = path.rfind('#');
    if (hash == std::string::npos || hash + 1 >= path.size()) return false;
    const std::string head = path.substr(0, hash);
    if (!is_archive_extension(head)) return false;
    *archive = head;
    *member = path.substr(hash + 1);
    return true;
}

// Is `path` something the core reads as disks: a disk or archive file, or a member of an archive by name?
inline bool is_disk_path(const std::string &path)
{
    std::string archive, member;
    return split_member(path, &archive, &member) ? true : is_disk_extension(path);
}

namespace archive_detail {

inline std::string base_name(const std::string &name)
{
    const size_t slash = name.find_last_of('/');
    return slash == std::string::npos ? name : name.substr(slash + 1);
}

inline unsigned char sort_key(unsigned char c) { return c == ' ' ? '/' : c; }

// PUAE5's order of the disks of a set (see the header).
inline bool disk_name_before(const std::string &a, const std::string &b)
{
    const bool program_a = a.find("(Program") != std::string::npos, program_b = b.find("(Program") != std::string::npos;
    if (program_a != program_b) return program_a;
    const size_t n = std::min(a.size(), b.size());
    for (size_t i = 0; i < n; ++i) {
        const unsigned char x = sort_key(static_cast<unsigned char>(a[i])), y = sort_key(static_cast<unsigned char>(b[i]));
        if (x != y) return x < y;
    }
    if (a.size() != b.size()) return a.size() < b.size();
    return a < b;
}

inline std::string lower(const std::string &s)
{
    std::string out = s;
    for (char &c : out) c = char(tolower(static_cast<unsigned char>(c)));
    return out;
}

// A ZIP or a 7z seen as a list of names and the bytes of each.
class View {
public:
    const char *kind() const { return sevenzip_ ? "7z archive" : "ZIP file"; }
    bool is_7z() const { return sevenzip_; }

    bool open(const std::vector<uint8_t> &file, const ArchiveLimits &limits, std::string *why)
    {
        file_ = &file;
        names_.clear();
        if (sevenzip::looks_like_sevenzip(file.data(), file.size())) {
            sevenzip_ = true;
            if (!seven_.open(file.data(), file.size(), limits.sevenzip, why)) return false;
            for (const sevenzip::Entry &entry : seven_.entries()) {
                const std::string base = base_name(entry.name);
                names_.push_back(entry.name);
                skip_.push_back(entry.is_dir || entry.name.empty() || base.empty() || entry.name.compare(0, 9, "__MACOSX/") == 0 || base.compare(0, 2, "._") == 0);
            }
            return true;
        }
        sevenzip_ = false;
        if (!container_detail::read_zip_directory(file, &zip_, why)) return false;
        for (const container_detail::ZipEntry &entry : zip_.entries) {
            names_.push_back(entry.name);
            skip_.push_back(container_detail::zip_entry_ignored(entry));
        }
        return true;
    }
    size_t size() const { return names_.size(); }
    const std::string &name(size_t i) const { return names_[i]; }
    bool ignored(size_t i) const { return skip_[i]; }

    bool extract(size_t i, size_t limit, const char *what, std::vector<uint8_t> *out, std::string *why)
    {
        if (!sevenzip_) return container_detail::extract_zip_entry(*file_, zip_, zip_.entries[i], limit, what, out, why);
        if (seven_.entries()[i].size > limit) { *why = std::string("the 7z entry is larger than any ") + what + " the core reads"; return false; }
        return seven_.extract(i, out, why);
    }

private:
    const std::vector<uint8_t> *file_ = nullptr;
    bool sevenzip_ = false;
    container_detail::ZipDirectory zip_;
    sevenzip::Archive seven_;
    std::vector<std::string> names_;
    std::vector<bool> skip_;
};

// --- the manifest of an RP9 --------------------------------------------------------------------------------------------------------------------

struct Floppy {
    std::string path;
    bool has_priority = false;
    unsigned long priority = 0;
    std::string root;
};

struct Rp9Media {
    std::vector<Floppy> floppies;
    unsigned other_media = 0;                            // hard drives, CDs, tapes and snapshots: not read
};

inline void append_utf8(unsigned long c, std::string *out)
{
    if (c < 0x80) out->push_back(char(c));
    else if (c < 0x800) { out->push_back(char(0xC0 | (c >> 6))); out->push_back(char(0x80 | (c & 0x3F))); }
    else if (c < 0x10000) { out->push_back(char(0xE0 | (c >> 12))); out->push_back(char(0x80 | ((c >> 6) & 0x3F))); out->push_back(char(0x80 | (c & 0x3F))); }
    else { out->push_back(char(0xF0 | (c >> 18))); out->push_back(char(0x80 | ((c >> 12) & 0x3F))); out->push_back(char(0x80 | ((c >> 6) & 0x3F))); out->push_back(char(0x80 | (c & 0x3F))); }
}

// The predefined entities and numeric references; anything else stays as written.
inline std::string xml_text(const std::string &raw)
{
    std::string out;
    for (size_t i = 0; i < raw.size(); ++i) {
        if (raw[i] != '&') { out.push_back(raw[i]); continue; }
        const size_t semicolon = raw.find(';', i);
        if (semicolon == std::string::npos || semicolon - i > 10) { out.push_back('&'); continue; }
        const std::string entity = raw.substr(i + 1, semicolon - i - 1);
        if (entity == "amp") out.push_back('&');
        else if (entity == "lt") out.push_back('<');
        else if (entity == "gt") out.push_back('>');
        else if (entity == "quot") out.push_back('"');
        else if (entity == "apos") out.push_back('\'');
        else if (entity.size() >= 2 && entity[0] == '#') {
            const bool hex = entity[1] == 'x';
            unsigned long value = 0;
            bool valid = entity.size() > size_t(hex ? 2 : 1);
            for (size_t k = hex ? 2 : 1; k < entity.size() && valid; ++k) {
                const int d = isdigit(static_cast<unsigned char>(entity[k])) ? entity[k] - '0' : hex && isxdigit(static_cast<unsigned char>(entity[k])) ? tolower(entity[k]) - 'a' + 10 : -1;
                if (d < 0) valid = false;
                else value = value * (hex ? 16 : 10) + unsigned(d);
            }
            if (!valid || value == 0 || value > 0x10FFFF || (value >= 0xD800 && value < 0xE000)) { out.push_back('&'); continue; }
            append_utf8(value, &out);
        } else { out.push_back('&'); continue; }
        i = semicolon;
    }
    return out;
}

inline std::string trimmed(const std::string &s)
{
    size_t a = 0, b = s.size();
    while (a < b && isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

inline std::string local_name(const std::string &name)
{
    const size_t colon = name.rfind(':');
    return lower(colon == std::string::npos ? name : name.substr(colon + 1));
}

// Reads the <media> section of the manifest: the floppy elements that are children of application/media. A scanner for the part of XML that matters (elements, attributes, text, comments,
// CDATA), not a validating parser: no entity is defined or expanded beyond the predefined ones, a DOCTYPE with an internal subset is refused, and the depth is bounded.
inline bool parse_rp9_media(const uint8_t *data, size_t size, Rp9Media *media, std::string *why)
{
    media->floppies.clear();
    media->other_media = 0;
    const std::string text(reinterpret_cast<const char *>(data), size);
    size_t at = text.compare(0, 3, "\xEF\xBB\xBF") == 0 ? 3 : 0;
    std::vector<std::string> stack;
    bool seen_root = false;
    std::string floppy_text;
    Floppy floppy;
    bool in_floppy = false;
    auto bad = [&](const char *what) {
        *why = std::string("the RP9 manifest is not usable XML (") + what + ")";
        return false;
    };
    while (at < text.size()) {
        const size_t lt = text.find('<', at);
        if (lt == std::string::npos) {
            if (in_floppy) floppy_text += text.substr(at);
            break;
        }
        if (in_floppy) floppy_text += text.substr(at, lt - at);
        at = lt;
        if (!text.compare(at, 4, "<!--")) {
            const size_t end = text.find("-->", at + 4);
            if (end == std::string::npos) return bad("a comment is not closed");
            at = end + 3;
            continue;
        }
        if (!text.compare(at, 9, "<![CDATA[")) {
            const size_t end = text.find("]]>", at + 9);
            if (end == std::string::npos) return bad("a CDATA section is not closed");
            if (in_floppy) floppy_text += text.substr(at + 9, end - at - 9);
            at = end + 3;
            continue;
        }
        if (!text.compare(at, 2, "<?")) {
            const size_t end = text.find("?>", at + 2);
            if (end == std::string::npos) return bad("a processing instruction is not closed");
            at = end + 2;
            continue;
        }
        if (!text.compare(at, 2, "<!")) {
            const size_t end = text.find('>', at + 2);
            if (end == std::string::npos || text.find('[', at) < end) return bad("a DOCTYPE with an internal subset is not read");
            at = end + 1;
            continue;
        }
        if (at + 1 < text.size() && text[at + 1] == '/') {
            const size_t end = text.find('>', at + 2);
            if (end == std::string::npos) return bad("an end tag is not closed");
            const std::string name = local_name(trimmed(text.substr(at + 2, end - at - 2)));
            if (stack.empty() || stack.back() != name) return bad("the elements do not nest");
            if (in_floppy && name == "floppy") {
                floppy.path = trimmed(xml_text(floppy_text));
                media->floppies.push_back(std::move(floppy));
                in_floppy = false;
            }
            stack.pop_back();
            at = end + 1;
            continue;
        }
        // A start tag: name, attributes, '>' or '/>'.
        size_t i = at + 1;
        const size_t name_start = i;
        while (i < text.size() && !isspace(static_cast<unsigned char>(text[i])) && text[i] != '>' && text[i] != '/') ++i;
        if (i == name_start || i >= text.size()) return bad("a tag is cut short");
        const std::string name = local_name(text.substr(name_start, i - name_start));
        Floppy attributes;
        bool self_closing = false;
        for (;;) {
            while (i < text.size() && isspace(static_cast<unsigned char>(text[i]))) ++i;
            if (i >= text.size()) return bad("a tag is cut short");
            if (text[i] == '>') { ++i; break; }
            if (text[i] == '/') {
                if (i + 1 >= text.size() || text[i + 1] != '>') return bad("a stray slash in a tag");
                self_closing = true;
                i += 2;
                break;
            }
            const size_t key_start = i;
            while (i < text.size() && text[i] != '=' && text[i] != '>' && text[i] != '/' && !isspace(static_cast<unsigned char>(text[i]))) ++i;
            const std::string key = local_name(text.substr(key_start, i - key_start));
            while (i < text.size() && isspace(static_cast<unsigned char>(text[i]))) ++i;
            if (i >= text.size() || text[i] != '=' || key.empty()) return bad("an attribute has no value");
            ++i;
            while (i < text.size() && isspace(static_cast<unsigned char>(text[i]))) ++i;
            if (i >= text.size() || (text[i] != '"' && text[i] != '\'')) return bad("an attribute value is not quoted");
            const char quote = text[i++];
            const size_t value_end = text.find(quote, i);
            if (value_end == std::string::npos) return bad("an attribute value is not closed");
            const std::string value = xml_text(text.substr(i, value_end - i));
            i = value_end + 1;
            if (key == "priority") {
                const std::string digits = trimmed(value);
                bool numeric = !digits.empty() && digits.size() <= 9;
                for (char c : digits) numeric = numeric && isdigit(static_cast<unsigned char>(c));
                if (numeric) { attributes.has_priority = true; attributes.priority = strtoul(digits.c_str(), nullptr, 10); }
            } else if (key == "root") {
                attributes.root = lower(trimmed(value));
            }
        }
        if (stack.size() >= kMaxArchiveDepth) return bad("the elements nest too deeply");
        const bool in_media = stack.size() >= 2 && stack[stack.size() - 1] == "media" && stack[stack.size() - 2] == "application";
        if (in_media) {
            if (name == "floppy") {
                floppy = attributes;
                if (self_closing) media->floppies.push_back(std::move(floppy));
                else { in_floppy = true; floppy_text.clear(); }
            } else {
                ++media->other_media;
            }
        }
        seen_root = true;
        if (!self_closing) stack.push_back(name);
        at = i;
    }
    if (!seen_root) return bad("it has no elements");
    if (!stack.empty()) return bad("an element is not closed");
    return true;
}

// A path of an RP9 manifest as a name inside the package: separators as '/', no leading "./", and nothing that leaves the package.
inline bool package_path(const std::string &raw, std::string *out)
{
    std::string path = raw;
    for (char &c : path) if (c == '\\') c = '/';
    while (path.compare(0, 2, "./") == 0) path.erase(0, 2);
    if (path.empty() || path[0] == '/' || (path.size() >= 2 && path[1] == ':')) return false;
    size_t at = 0;
    for (;;) {
        const size_t slash = path.find('/', at);
        const std::string part = path.substr(at, slash == std::string::npos ? std::string::npos : slash - at);
        if (part.empty() || part == "." || part == "..") return false;
        if (slash == std::string::npos) break;
        at = slash + 1;
    }
    *out = path;
    return true;
}

inline std::string normalized_member(const std::string &name)
{
    std::string out = name;
    for (char &c : out) if (c == '\\') c = '/';
    return out;
}

enum class Kind { Floppy, Unsupported, Other };

inline Kind classify(const std::string &name)
{
    const std::string base = base_name(name);
    const std::string ext = container_detail::lower_extension(base);
    if (ext == "adf" || ext == "adz" || ext == "dms" || ext == "fdi") return Kind::Floppy;
    if (ext == "7z" || unsupported_format_reason(base)) return Kind::Unsupported;       // a 7z inside an archive is not unpacked
    return Kind::Other;
}

}  // namespace archive_detail

// Reads the disks of an archive. With `member` empty: every disk, in playlist order; with a name: that member alone. False with a sentence about the file (without its path) in `why`.
inline bool read_disk_set(const std::vector<uint8_t> &file, const std::string &member, const ArchiveLimits &limits, DiskSet *set, std::string *why)
{
    using namespace archive_detail;
    set->disks.clear();
    set->notes.clear();
    why->clear();
    archive_detail::View view;
    if (!view.open(file, limits, why)) return false;
    set->kind = view.kind();
    const std::string kind = view.kind();

    // The member asked for by name.
    std::vector<size_t> chosen;
    if (!member.empty()) {
        const std::string wanted = normalized_member(member);
        std::vector<size_t> exact, by_base;
        for (size_t i = 0; i < view.size(); ++i) {
            if (view.ignored(i)) continue;
            const std::string name = normalized_member(view.name(i));
            if (name == wanted) exact.push_back(i);
            else if (base_name(name) == wanted) by_base.push_back(i);
        }
        if (exact.size() == 1) chosen = exact;
        else if (exact.empty() && by_base.size() == 1) chosen = by_base;
        else if (exact.empty() && by_base.empty()) { *why = "the " + kind + " has no member named \"" + member + "\""; return false; }
        else { *why = "the " + kind + " has more than one member named \"" + member + "\"; use its whole path"; return false; }
    } else {
        // The manifest of an RP9 decides the disks and their order.
        size_t manifest = SIZE_MAX;
        if (!view.is_7z()) {
            for (size_t i = 0; i < view.size(); ++i)
                if (!view.ignored(i) && lower(normalized_member(view.name(i))) == "rp9-manifest.xml") { manifest = i; break; }
        }
        if (manifest != SIZE_MAX) {
            set->kind = "RP9 package";
            std::vector<uint8_t> xml;
            if (!view.extract(manifest, limits.max_manifest, "RP9 manifest", &xml, why)) { *why = "rp9-manifest.xml: " + *why; return false; }
            Rp9Media media;
            if (!parse_rp9_media(xml.data(), xml.size(), &media, why)) return false;
            if (media.other_media)
                set->notes.push_back(std::to_string(media.other_media) + " hard drive, CD, tape or snapshot entr" + (media.other_media == 1 ? "y" : "ies") + " of the RP9 manifest ignored (only floppies are read)");
            set->notes.push_back("the RP9 configuration (machine, memory, Kickstart, peripherals, boot) is ignored: choose the machine with puae6_machine");
            std::vector<std::pair<std::pair<unsigned long, size_t>, size_t>> order;           // (priority, document order) -> member
            std::vector<size_t> seen;
            for (size_t k = 0; k < media.floppies.size(); ++k) {
                const Floppy &floppy = media.floppies[k];
                if (floppy.root == "shared") {
                    set->notes.push_back("the floppy \"" + floppy.path + "\" is in the player's shared folder, not in the package: ignored");
                    continue;
                }
                std::string path;
                if (!package_path(floppy.path, &path)) { *why = "the RP9 manifest names a floppy with an unsafe path (\"" + floppy.path + "\")"; return false; }
                size_t found = SIZE_MAX;
                size_t folded = SIZE_MAX, folded_count = 0;
                for (size_t i = 0; i < view.size(); ++i) {
                    if (view.ignored(i)) continue;
                    const std::string name = normalized_member(view.name(i));
                    if (name == path) { found = i; break; }
                    if (lower(name) == lower(path)) { folded = i; ++folded_count; }
                }
                if (found == SIZE_MAX && folded_count == 1) found = folded;
                if (found == SIZE_MAX) { *why = "the RP9 manifest names the floppy \"" + floppy.path + "\" and the package does not hold it"; return false; }
                if (std::find(seen.begin(), seen.end(), found) != seen.end()) continue;
                seen.push_back(found);
                order.push_back({{floppy.has_priority ? floppy.priority : ~0ul, k}, found});
            }
            std::sort(order.begin(), order.end());
            for (const auto &item : order) chosen.push_back(item.second);
            if (chosen.empty()) { *why = "the RP9 manifest lists no floppy that the package holds"; return false; }
        } else {
            std::vector<size_t> unsupported;
            unsigned ignored = 0;
            for (size_t i = 0; i < view.size(); ++i) {
                if (view.ignored(i)) continue;
                switch (classify(view.name(i))) {
                case Kind::Floppy: chosen.push_back(i); break;
                case Kind::Unsupported: unsupported.push_back(i); break;
                default: ++ignored; break;
                }
            }
            if (!unsupported.empty()) {
                *why = chosen.empty() ? "the " + kind + " holds a disk in a format the core does not read (IPF, FDI or similar); only ADF, ADZ and DMS disks are"
                                      : "the " + kind + " holds \"" + view.name(unsupported[0]) + "\", a disk in a format the core does not read (IPF, FDI or similar); only ADF, ADZ and DMS disks are";
                return false;
            }
            if (chosen.empty()) {
                *why = "the " + kind + " holds no disk image (.adf, .adz, .dms or .fdi)";
                return false;
            }
            std::sort(chosen.begin(), chosen.end(), [&](size_t a, size_t b) {
                const std::string &x = view.name(a), &y = view.name(b);
                if (x != y) return disk_name_before(x, y);
                return a < b;
            });
            if (ignored) set->notes.push_back(std::to_string(ignored) + " other member(s) of the " + kind + " ignored (they are not disks)");
        }
        if (chosen.size() > limits.max_disks) {
            *why = "the " + std::string(set->kind) + " holds " + std::to_string(chosen.size()) + " disk images; the core keeps at most " + std::to_string(limits.max_disks) +
                   ", so name the ones to use in an .m3u playlist (file" + (view.is_7z() ? ".7z" : ".zip") + "#member.adf) or pack fewer";
            return false;
        }
    }
    for (size_t index : chosen) {
        DiskMember disk;
        disk.name = view.name(index);
        if (!view.extract(index, limits.max_member, "disk file", &disk.adf, why)) {
            *why = "\"" + disk.name + "\": " + *why;
            return false;
        }
        std::string inner;
        if (!container_detail::decode_image(&disk.adf, disk.name, 1, &inner) || !container_detail::is_floppy_image(disk.adf)) {
            if (inner.empty()) inner = "not an ADF of " + std::to_string(kDiskBytes) + " bytes, an extended ADF or an FDI";
            *why = "the " + kind + " member \"" + disk.name + "\": " + inner;
            return false;
        }
        set->disks.push_back(std::move(disk));
    }
    return true;
}

}  // namespace p6_libretro

#endif
