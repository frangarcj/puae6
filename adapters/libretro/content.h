// What the libretro adapter reads from the frontend's side of the world (T17a): files (through the frontend's VFS when it offers one, with stdio
// otherwise), paths, and M3U playlists. Nothing here touches the native ABI or the engine, and nothing allocates from a size the file claims: every
// read has a cap, and a file that grows past it is refused.
//
// Paths are UTF-8 bytes handed to the VFS or to fopen as they are: the adapter never converts, normalizes or resolves them, and a path is never part of
// any identity (the media are named by the SHA-256 of their bytes, see docs/adr/0008-media-identity.md). The stdio fallback is the POSIX one (macOS,
// Linux, Waterbox); a Windows frontend always offers the VFS.
#ifndef P6_LIBRETRO_CONTENT_H
#define P6_LIBRETRO_CONTENT_H

#include "libretro.h"

#include <algorithm>
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#if !defined(_WIN32)
#include <dirent.h>
#include <sys/stat.h>
#endif
#include <new>
#include <string>
#include <vector>

namespace p6_libretro {

constexpr size_t kMaxPlaylistBytes = 64 * 1024;      // an M3U of sixteen long paths is far below this
constexpr size_t kMaxPathBytes = 4096;               // one playlist path or one system directory

enum class ReadResult { Ok, CannotOpen, TooLarge, ReadFailed, OutOfMemory };

// One entry of a directory as the frontend (or the C library) lists it. A link is what it points to: the VFS cannot tell a link, so neither does the C library path.
struct DirEntry {
    std::string name;
    bool is_dir = false;
};
enum class ListResult { Ok, CannotOpen, TooMany };

// Reads whole files, through the frontend's VFS (version 1 is enough: open, size, read, close) when one was given and through stdio when not.
class FileReader {
public:
    // `version` is the VFS interface version the frontend agreed to (1 to 3): the directory calls exist from 3 on.
    void use_vfs(const retro_vfs_interface *vfs, unsigned version = 1)
    {
        vfs_ = vfs;
        vfs_dirs_ = vfs && version >= 3 && vfs->stat && vfs->opendir && vfs->readdir && vfs->dirent_get_name && vfs->dirent_is_dir && vfs->closedir;
    }
    bool uses_vfs() const { return vfs_ != nullptr; }
    bool lists_with_vfs() const { return vfs_dirs_; }

    // Is `path` a directory? Through the VFS when it has the directory calls, with the C library otherwise (nothing on Windows without the VFS).
    bool is_directory(const std::string &path) const
    {
        if (vfs_dirs_) return (vfs_->stat(path.c_str(), nullptr) & RETRO_VFS_STAT_IS_DIRECTORY) != 0;
#if !defined(_WIN32)
        struct stat info;
        return stat(path.c_str(), &info) == 0 && S_ISDIR(info.st_mode);
#else
        return false;
#endif
    }

    // The entries of a directory (not "." or ".."), hidden ones included, in the order the host gives them: nobody may depend on it. More than `max_entries` is TooMany.
    ListResult list(const std::string &path, size_t max_entries, std::vector<DirEntry> *out) const
    {
        out->clear();
        try {
            return vfs_dirs_ ? list_vfs(path, max_entries, out) : list_posix(path, max_entries, out);
        } catch (const std::bad_alloc &) {
            out->clear();
            return ListResult::TooMany;
        }
    }

    // Fills `out` with the whole file. A file larger than `max_bytes` is TooLarge whatever the frontend says about its size, and nothing larger than
    // `max_bytes + 1` is ever buffered.
    ReadResult read(const std::string &path, size_t max_bytes, std::vector<uint8_t> *out) const
    {
        out->clear();
        try {
            return vfs_ ? read_vfs(path, max_bytes, out) : read_stdio(path, max_bytes, out);
        } catch (const std::bad_alloc &) {
            out->clear();
            return ReadResult::OutOfMemory;
        }
    }

private:
    static constexpr size_t kChunk = 64 * 1024;

    ReadResult read_vfs(const std::string &path, size_t max_bytes, std::vector<uint8_t> *out) const
    {
        retro_vfs_file_handle *file = vfs_->open(path.c_str(), RETRO_VFS_FILE_ACCESS_READ, RETRO_VFS_FILE_ACCESS_HINT_NONE);
        if (!file) return ReadResult::CannotOpen;
        ReadResult result = ReadResult::Ok;
        const int64_t claimed = vfs_->size ? vfs_->size(file) : -1;
        if (claimed > 0 && uint64_t(claimed) > max_bytes) {
            result = ReadResult::TooLarge;
        } else {
            if (claimed > 0) out->reserve(size_t(claimed));
            for (;;) {
                const size_t before = out->size();
                const size_t want = std::min(kChunk, max_bytes + 1 - before);
                out->resize(before + want);
                const int64_t got = vfs_->read(file, out->data() + before, want);
                out->resize(before + (got > 0 ? size_t(got) : 0));
                if (got < 0) {
                    result = ReadResult::ReadFailed;
                    break;
                }
                if (out->size() > max_bytes) {
                    result = ReadResult::TooLarge;
                    break;
                }
                if (got == 0) break;
            }
        }
        vfs_->close(file);
        if (result != ReadResult::Ok) out->clear();
        return result;
    }

    ListResult list_vfs(const std::string &path, size_t max_entries, std::vector<DirEntry> *out) const
    {
        retro_vfs_dir_handle *dir = vfs_->opendir(path.c_str(), true);
        if (!dir) return ListResult::CannotOpen;
        ListResult result = ListResult::Ok;
        while (vfs_->readdir(dir)) {
            const char *name = vfs_->dirent_get_name(dir);
            if (!name || !strcmp(name, ".") || !strcmp(name, "..")) continue;
            if (out->size() >= max_entries) {
                result = ListResult::TooMany;
                break;
            }
            DirEntry entry;
            entry.name = name;
            entry.is_dir = vfs_->dirent_is_dir(dir);
            out->push_back(std::move(entry));
        }
        vfs_->closedir(dir);
        if (result != ListResult::Ok) out->clear();
        return result;
    }

    static ListResult list_posix(const std::string &path, size_t max_entries, std::vector<DirEntry> *out)
    {
#if !defined(_WIN32)
        DIR *dir = opendir(path.c_str());
        if (!dir) return ListResult::CannotOpen;
        ListResult result = ListResult::Ok;
        while (const struct dirent *item = readdir(dir)) {
            if (!strcmp(item->d_name, ".") || !strcmp(item->d_name, "..")) continue;
            if (out->size() >= max_entries) {
                result = ListResult::TooMany;
                break;
            }
            DirEntry entry;
            entry.name = item->d_name;
            struct stat info;
            const std::string full = path.empty() || path.back() == '/' ? path + entry.name : path + "/" + entry.name;
            entry.is_dir = stat(full.c_str(), &info) == 0 && S_ISDIR(info.st_mode);
            out->push_back(std::move(entry));
        }
        closedir(dir);
        if (result != ListResult::Ok) out->clear();
        return result;
#else
        (void)path; (void)max_entries; (void)out;
        return ListResult::CannotOpen;
#endif
    }

    static ReadResult read_stdio(const std::string &path, size_t max_bytes, std::vector<uint8_t> *out)
    {
        FILE *file = fopen(path.c_str(), "rb");
        if (!file) return ReadResult::CannotOpen;
        ReadResult result = ReadResult::Ok;
        for (;;) {
            const size_t before = out->size();
            const size_t want = std::min(kChunk, max_bytes + 1 - before);
            out->resize(before + want);
            const size_t got = fread(out->data() + before, 1, want, file);
            out->resize(before + got);
            if (out->size() > max_bytes) {
                result = ReadResult::TooLarge;
                break;
            }
            if (got < want) {
                if (ferror(file)) result = ReadResult::ReadFailed;
                break;
            }
        }
        fclose(file);
        if (result != ReadResult::Ok) out->clear();
        return result;
    }

    const retro_vfs_interface *vfs_ = nullptr;
    bool vfs_dirs_ = false;
};

// --- paths -------------------------------------------------------------------------------------------------------------------------------------

inline bool is_separator(char c) { return c == '/' || c == '\\'; }

// Absolute: a root, a drive letter or a URL-like scheme (a frontend's own namespaces, saf://...), which a playlist entry must not be joined to.
inline bool is_absolute(const std::string &path)
{
    if (path.empty()) return false;
    if (is_separator(path[0])) return true;
    if (path.size() >= 2 && isalpha(static_cast<unsigned char>(path[0])) && path[1] == ':') return true;
    return path.find("://") != std::string::npos;
}

inline std::string directory_of(const std::string &path)      // with its trailing separator, empty when the path has none
{
    size_t cut = path.size();
    while (cut && !is_separator(path[cut - 1])) --cut;
    return path.substr(0, cut);
}

inline std::string file_name(const std::string &path) { return path.substr(directory_of(path).size()); }

inline std::string without_extension(const std::string &name)
{
    const size_t dot = name.rfind('.');
    return dot == std::string::npos || dot == 0 ? name : name.substr(0, dot);
}

inline bool has_extension(const std::string &path, const char *extension)       // `extension` in lower case, without the dot
{
    const std::string name = file_name(path);
    const size_t dot = name.rfind('.');
    if (dot == std::string::npos) return false;
    const size_t length = strlen(extension);
    if (name.size() - dot - 1 != length) return false;
    for (size_t i = 0; i < length; ++i)
        if (tolower(static_cast<unsigned char>(name[dot + 1 + i])) != extension[i]) return false;
    return true;
}

inline std::string join_path(const std::string &directory, const std::string &name)
{
    if (directory.empty() || is_separator(directory.back())) return directory + name;
    return directory + "/" + name;
}

// --- playlists ---------------------------------------------------------------------------------------------------------------------------------

struct PlaylistEntry {
    std::string path;            // resolved against the playlist's directory
    std::string label;           // from the playlist, or the file's name without its extension
};

struct Playlist {
    std::vector<PlaylistEntry> entries;
    unsigned save_disks_ignored = 0;       // "#SAVEDISK:" lines: the core never writes to a disk
};

// Parses an M3U of disk images: one `file|label` per line (the label may be empty or missing), `#` lines are comments, relative paths are relative
// to the playlist's own directory, a leading UTF-8 BOM and CR, LF or CRLF line ends are accepted. Returns false and a message that names the line
// when the text cannot be a playlist. It does not look at the files, and it does not judge their extensions.
inline bool parse_m3u(const std::vector<uint8_t> &bytes, const std::string &directory, size_t max_entries, Playlist *playlist, std::string *error)
{
    playlist->entries.clear();
    playlist->save_disks_ignored = 0;
    size_t at = 0;
    if (bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) at = 3;
    unsigned line_number = 0;
    auto fail = [&](const char *what) {
        *error = "playlist line " + std::to_string(line_number) + ": " + what;
        return false;
    };
    while (at < bytes.size()) {
        size_t end = at;
        while (end < bytes.size() && bytes[end] != '\n' && bytes[end] != '\r') ++end;
        ++line_number;
        std::string line(bytes.begin() + std::ptrdiff_t(at), bytes.begin() + std::ptrdiff_t(end));
        at = end;
        if (at < bytes.size() && bytes[at] == '\r' && at + 1 < bytes.size() && bytes[at + 1] == '\n') ++at;
        ++at;
        if (line.find('\0') != std::string::npos) return fail("a NUL byte is not text");
        size_t first = 0, last = line.size();
        while (first < last && (line[first] == ' ' || line[first] == '\t')) ++first;
        while (last > first && (line[last - 1] == ' ' || line[last - 1] == '\t')) --last;
        line = line.substr(first, last - first);
        if (line.empty()) continue;
        if (line[0] == '#') {
            if (line.compare(0, 10, "#SAVEDISK:") == 0) ++playlist->save_disks_ignored;
            continue;
        }
        const size_t bar = line.find('|');                // a path with a '|' in it cannot be named in a playlist
        std::string path = line.substr(0, bar);
        std::string label = bar == std::string::npos ? std::string() : line.substr(bar + 1);
        while (!path.empty() && (path.back() == ' ' || path.back() == '\t')) path.pop_back();
        size_t label_start = 0;
        while (label_start < label.size() && (label[label_start] == ' ' || label[label_start] == '\t')) ++label_start;
        label.erase(0, label_start);
        if (path.empty()) return fail("the entry has no file name");
        if (path.size() > kMaxPathBytes) return fail("the path is too long");
        if (playlist->entries.size() >= max_entries) {
            *error = "the playlist has more than " + std::to_string(max_entries) + " disks (line " + std::to_string(line_number) + ")";
            return false;
        }
        if (!is_absolute(path)) path = directory + path;
        if (label.empty()) label = without_extension(file_name(path));
        playlist->entries.push_back({path, label});
    }
    return true;
}

}  // namespace p6_libretro

#endif
