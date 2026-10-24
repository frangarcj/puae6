// The volume the libretro adapter builds from a directory, an LHA or a ZIP (T64a): ONE plain FFS partition in memory, the shape of a hard file without an RDB (the first long of
// block 0 is the DosType, 32 sectors, 1 head, 2 reserved blocks; docs/02-contracts.md "HDF"), which p6_load takes as a P6_MEDIA_HDF like any hard file. It is a C++ port of
// tools/whdload/ffs.py, the writer of the offline WHDLoad preparation (T30a), and that module is its oracle: for the same tree, label, time and size both give the same bytes
// (tests/hdf/test_volume.py compares them on whole trees).
//
// Nothing here reads the host: no clock, no file system order, no uid, no path. The files and directories come in as (path, bytes) with the bytes of the names already in AmigaDOS's
// character set (ISO 8859-1); the image depends only on that tree, the label, the time stamp and the free space asked for. Names and children are ordered by their bytes, blocks are handed
// out in that order (the root in the middle of the volume, then the bitmap pages, then every directory, file header, extension and data block in the order of a depth-first walk), and every
// date is the one stamp. A DosType of DOS\1 (FFS) is written unless a name has a byte above 127, which needs DOS\3 (international mode).
//
// Layout (AmigaDOS "filing system"; longs of 512-byte blocks): root = T_HEADER(2), ht_size 72 at long 3, checksum at 5, hash table 6..77, bitmap valid flag 78, bitmap pages 79..103, dates
// 105..107 / 118..120 / 121..123, name 108..115, ST_ROOT(1) at 127; directory = T_HEADER, own key, hash table, protection 80, date, name, hash chain 124, parent 125, ST_USERDIR(2);
// file = T_HEADER, own key, number of data blocks listed, first data block, data block table 77 down to 6, protection 80, size 81, date, name, chain, parent, extension block 126,
// ST_FILE(-3); extension = T_LIST(16), own key, count, table, parent 125 (the file header), next extension 126, ST_FILE; data = FFS: the 512 bytes of the file, no header.
//
// Bounds: every size is checked before it allocates anything. The volume has at most 25 bitmap pages in its root block (127 * 32 blocks each, about 49 MiB; the extension chain of the
// bitmap is not written), names have 30 bytes at most and the nesting is capped, so a hostile tree is an error message and never a large allocation or a deep recursion.
#ifndef P6_LIBRETRO_FFS_VOLUME_H
#define P6_LIBRETRO_FFS_VOLUME_H

#include <algorithm>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <map>
#include <string>
#include <vector>

namespace p6_libretro {

struct VolumeFile {
    std::string path;                  // names in AmigaDOS bytes joined by '/', no leading or trailing '/'
    bool is_dir = false;
    std::vector<uint8_t> data;         // consumed by build_ffs_volume
};

struct VolumeOptions {
    std::string label = "PUAE6";       // the name of the volume (1 to 30 bytes, no ':' or '/')
    uint32_t free_blocks = 0;          // blocks left free for the guest's writes
    uint32_t min_blocks = 0;
    // The time stamp of everything: days since 1978-01-01, minutes since midnight, ticks (1/50 s). The default is 1990-01-01 00:00:00, the one of tools/whdload/ffs.py.
    uint32_t days = 4383, minutes = 0, ticks = 0;
};

namespace ffs {

constexpr uint32_t kBlock = 512;
constexpr uint32_t kReserved = 2;
constexpr uint32_t kHashSize = 72;
constexpr uint32_t kTable = 72;                // data block pointers in a file header or an extension block
constexpr uint32_t kBitmapLongs = 127;         // a bitmap page maps 127 * 32 blocks
constexpr uint32_t kBitmapPages = 25;          // in the root block; the extension chain is not written
constexpr size_t kMaxName = 30;
constexpr size_t kMaxDepth = 64;               // nesting of directories
constexpr size_t kMaxNodes = 8192;             // files and directories of one volume
constexpr uint32_t kTHeader = 2, kTList = 16, kStRoot = 1, kStUserDir = 2, kStFile = 0xFFFFFFFDu;
// What 25 bitmap pages map: the largest volume, in blocks, and the room a tree may take of it.
constexpr uint32_t kMaxBlocks = kReserved + kBitmapPages * kBitmapLongs * 32;

inline uint8_t upper(uint8_t value, bool international)
{
    if ((value >= 0x61 && value <= 0x7A) || (international && value >= 0xE0 && value <= 0xFE && value != 0xF7)) return uint8_t(value - 0x20);
    return value;
}

inline uint32_t name_hash(const std::string &name, bool international)
{
    uint32_t h = uint32_t(name.size());
    for (unsigned char c : name) h = (h * 13 + upper(c, international)) & 0x7FF;
    return h % kHashSize;
}

inline std::string folded(const std::string &name)
{
    std::string out = name;
    for (char &c : out) c = char(upper(static_cast<unsigned char>(c), true));
    return out;
}

struct Node {
    std::string name;
    bool is_dir = false;
    uint32_t parent = 0;
    std::vector<uint8_t> data;
    std::map<std::string, uint32_t> children;          // by the bytes of the name
    std::map<std::string, std::string> spellings;      // folded name -> the name as spelled: two names that differ only in case cannot both be in a directory
    bool seen = false;                                 // a file whose bytes came in (a second one with the same path is an error, empty or not)
    uint32_t key = 0, chain = 0;
    std::vector<uint32_t> data_blocks, ext_blocks;
};

inline void put_long(std::vector<uint8_t> *image, size_t block, unsigned index, uint32_t value)
{
    uint8_t *p = image->data() + block * kBlock + size_t(index) * 4;
    p[0] = uint8_t(value >> 24);
    p[1] = uint8_t(value >> 16);
    p[2] = uint8_t(value >> 8);
    p[3] = uint8_t(value);
}

// The checksum long of a block built in `longs` (128 of them): the sum of all the longs is zero.
inline void seal(uint32_t longs[128], unsigned checksum_index, uint8_t *out)
{
    longs[checksum_index] = 0;
    uint32_t sum = 0;
    for (unsigned i = 0; i < 128; ++i) sum += longs[i];
    longs[checksum_index] = 0u - sum;
    for (unsigned i = 0; i < 128; ++i) {
        out[i * 4] = uint8_t(longs[i] >> 24);
        out[i * 4 + 1] = uint8_t(longs[i] >> 16);
        out[i * 4 + 2] = uint8_t(longs[i] >> 8);
        out[i * 4 + 3] = uint8_t(longs[i]);
    }
}

inline void put_bstr(uint32_t *longs, const std::string &name)       // 32 bytes: the length and the name, zero padded, as 8 longs
{
    uint8_t raw[32] = {};
    raw[0] = uint8_t(name.size());
    memcpy(raw + 1, name.data(), name.size());
    for (unsigned i = 0; i < 8; ++i) longs[i] = (uint32_t(raw[i * 4]) << 24) | (uint32_t(raw[i * 4 + 1]) << 16) | (uint32_t(raw[i * 4 + 2]) << 8) | raw[i * 4 + 3];
}

inline bool valid_name(const std::string &name)
{
    return !name.empty() && name.size() <= kMaxName && name.find(':') == std::string::npos && name.find('/') == std::string::npos && name.find('\0') == std::string::npos;
}

inline uint64_t data_block_count(size_t bytes) { return (uint64_t(bytes) + kBlock - 1) / kBlock; }
inline uint64_t extension_count(uint64_t data_blocks) { return data_blocks > kTable ? (data_blocks - kTable + kTable - 1) / kTable : 0; }

}  // namespace ffs

// Builds the volume. `files` is consumed (the data moves into the image). False with the reason in `why` when the tree is not a volume: a bad name, a file that is a directory too,
// two names that differ only in case, a tree too deep or too large for 25 bitmap pages.
inline bool build_ffs_volume(std::vector<VolumeFile> *files, const VolumeOptions &options, std::vector<uint8_t> *image, std::string *why)
{
    using namespace ffs;
    why->clear();
    image->clear();
    if (!valid_name(options.label)) {
        *why = "the volume name is not an AmigaDOS name";
        return false;
    }
    std::vector<Node> nodes(1);
    nodes[0].is_dir = true;
    bool high = false;
    for (VolumeFile &file : *files) {
        if (file.path.empty()) {
            *why = "an entry without a name";
            return false;
        }
        size_t at = 0, depth = 0;
        uint32_t current = 0;
        while (true) {
            const size_t slash = file.path.find('/', at);
            const bool last = slash == std::string::npos;
            const std::string part = file.path.substr(at, last ? std::string::npos : slash - at);
            if (!valid_name(part)) {
                *why = "the name \"" + part + "\" (in " + file.path + ") cannot be an AmigaDOS name (1 to 30 bytes, no ':' or '/')";
                return false;
            }
            if (++depth > kMaxDepth) {
                *why = file.path + " is nested more than " + std::to_string(kMaxDepth) + " directories deep";
                return false;
            }
            for (unsigned char c : part) high = high || c > 127;
            const std::string key = folded(part);
            auto twin = nodes[current].spellings.find(key);
            if (twin != nodes[current].spellings.end() && twin->second != part) {
                *why = "\"" + twin->second + "\" and \"" + part + "\" differ only in case";
                return false;
            }
            auto found = nodes[current].children.find(part);
            uint32_t child;
            if (found == nodes[current].children.end()) {
                if (nodes.size() >= kMaxNodes) {
                    *why = "the volume has more than " + std::to_string(kMaxNodes) + " files and directories";
                    return false;
                }
                child = uint32_t(nodes.size());
                nodes.emplace_back();
                nodes[child].name = part;
                nodes[child].parent = current;
                nodes[child].is_dir = !last || file.is_dir;
                nodes[current].children[part] = child;
                nodes[current].spellings[key] = part;
            } else {
                child = found->second;
            }
            if (last) {
                if (nodes[child].is_dir != file.is_dir) {
                    *why = file.path + " is both a file and a directory";
                    return false;
                }
                if (!file.is_dir) {
                    if (nodes[child].seen) {
                        *why = file.path + " appears twice";
                        return false;
                    }
                    nodes[child].data = std::move(file.data);
                    nodes[child].seen = true;
                }
                break;
            }
            if (!nodes[child].is_dir) {
                *why = file.path.substr(0, slash) + " is both a file and a directory";
                return false;
            }
            current = child;
            at = slash + 1;
        }
        file.data.clear();
        file.data.shrink_to_fit();
    }
    files->clear();

    // The order of the walk: every node after its parent, the children by the bytes of their names.
    std::vector<uint32_t> order;
    {
        std::vector<uint32_t> stack;
        for (auto it = nodes[0].children.rbegin(); it != nodes[0].children.rend(); ++it) stack.push_back(it->second);
        while (!stack.empty()) {
            const uint32_t n = stack.back();
            stack.pop_back();
            order.push_back(n);
            for (auto it = nodes[n].children.rbegin(); it != nodes[n].children.rend(); ++it) stack.push_back(it->second);
        }
    }
    uint64_t needed = kReserved + 1 + uint64_t(options.free_blocks);
    for (uint32_t n : order) {
        needed += 1;
        if (!nodes[n].is_dir) {
            const uint64_t data = data_block_count(nodes[n].data.size());
            needed += data + extension_count(data);
        }
        if (needed > kMaxBlocks) break;
    }
    if (needed > kMaxBlocks) {
        *why = "the tree does not fit in a volume of " + std::to_string(kBitmapPages) + " bitmap pages (about 49 MiB)";
        return false;
    }
    // The smallest multiple of 32 blocks that holds the tree, the bitmap pages and the free blocks, and is at least min_blocks (and 64).
    uint64_t pages = 1, blocks64 = 0, wanted = 0;
    for (;;) {
        blocks64 = std::max<uint64_t>(std::max<uint64_t>(options.min_blocks, needed + pages), 64);
        blocks64 = (blocks64 + 31) / 32 * 32;
        wanted = (blocks64 - kReserved + kBitmapLongs * 32 - 1) / (kBitmapLongs * 32);
        if (wanted <= pages) break;
        pages = wanted;
    }
    if (wanted > kBitmapPages) {
        *why = "a volume of " + std::to_string(blocks64) + " blocks needs more than " + std::to_string(kBitmapPages) + " bitmap pages";
        return false;
    }
    const uint32_t blocks = uint32_t(blocks64);
    pages = (uint64_t(blocks) - kReserved + kBitmapLongs * 32 - 1) / (kBitmapLongs * 32);
    const uint32_t root_block = (blocks - 1 + kReserved) / 2;
    const bool international = high;

    std::vector<bool> taken(blocks, false);
    for (uint32_t b = root_block; b < root_block + 1 + pages; ++b) taken[b] = true;
    uint32_t cursor = kReserved;
    auto allocate = [&]() {
        while (cursor < blocks && taken[cursor]) ++cursor;
        const uint32_t block = cursor;           // the needed count made sure there is one
        taken[block] = true;
        return block;
    };
    for (uint32_t n : order) {
        Node &node = nodes[n];
        node.key = allocate();
        if (!node.is_dir) {
            const uint64_t count = data_block_count(node.data.size());
            node.data_blocks.reserve(size_t(count));
            for (uint64_t i = 0; i < count; ++i) node.data_blocks.push_back(allocate());
            const uint64_t extensions = extension_count(count);
            for (uint64_t i = 0; i < extensions; ++i) node.ext_blocks.push_back(allocate());
        }
    }

    try {
        image->assign(size_t(blocks) * kBlock, 0);
    } catch (...) {
        image->clear();
        *why = "not enough memory for the volume";
        return false;
    }
    (*image)[0] = 'D'; (*image)[1] = 'O'; (*image)[2] = 'S'; (*image)[3] = international ? 3 : 1;

    // The hash chains: the children of a directory in the order of their names, the last one named of a slot first.
    auto hash_table = [&](uint32_t directory, uint32_t table[kHashSize]) {
        for (uint32_t i = 0; i < kHashSize; ++i) table[i] = 0;
        for (auto &entry : nodes[directory].children) {
            Node &child = nodes[entry.second];
            const uint32_t h = name_hash(child.name, international);
            child.chain = table[h];
            table[h] = child.key;
        }
    };
    uint32_t root_table[kHashSize];           // the chains of the top-level names are set before the nodes are written
    hash_table(0, root_table);
    uint8_t block[kBlock];
    auto put = [&](uint32_t number, const uint8_t *bytes) { memcpy(image->data() + size_t(number) * kBlock, bytes, kBlock); };
    auto header_common = [&](uint32_t *longs, const Node &node, uint32_t parent_key, uint32_t kind) {
        longs[0] = kTHeader;
        longs[1] = node.key;
        longs[105] = options.days; longs[106] = options.minutes; longs[107] = options.ticks;
        put_bstr(longs + 108, node.name);
        longs[124] = node.chain;
        longs[125] = parent_key;
        longs[127] = kind;
    };
    for (uint32_t n : order) {
        const Node &node = nodes[n];
        const uint32_t parent_key = node.parent == 0 ? root_block : nodes[node.parent].key;
        uint32_t longs[128] = {};
        if (node.is_dir) {
            hash_table(n, longs + 6);
            header_common(longs, node, parent_key, kStUserDir);
            seal(longs, 5, block);
            put(node.key, block);
            continue;
        }
        const size_t listed = std::min<size_t>(node.data_blocks.size(), kTable);
        longs[2] = uint32_t(listed);
        longs[4] = node.data_blocks.empty() ? 0 : node.data_blocks[0];
        for (size_t i = 0; i < listed; ++i) longs[77 - i] = node.data_blocks[i];
        longs[81] = uint32_t(node.data.size());
        longs[126] = node.ext_blocks.empty() ? 0 : node.ext_blocks[0];
        header_common(longs, node, parent_key, kStFile);
        seal(longs, 5, block);
        put(node.key, block);
        for (size_t e = 0; e < node.ext_blocks.size(); ++e) {
            uint32_t ext[128] = {};
            const size_t from = kTable * (e + 1), count = std::min<size_t>(node.data_blocks.size() - from, kTable);
            ext[0] = kTList;
            ext[1] = node.ext_blocks[e];
            ext[2] = uint32_t(count);
            for (size_t i = 0; i < count; ++i) ext[77 - i] = node.data_blocks[from + i];
            ext[125] = node.key;
            ext[126] = e + 1 < node.ext_blocks.size() ? node.ext_blocks[e + 1] : 0;
            ext[127] = kStFile;
            seal(ext, 5, block);
            put(node.ext_blocks[e], block);
        }
        for (size_t i = 0; i < node.data_blocks.size(); ++i) {
            const size_t offset = i * kBlock, length = std::min<size_t>(kBlock, node.data.size() - offset);
            memcpy(image->data() + size_t(node.data_blocks[i]) * kBlock, node.data.data() + offset, length);
        }
        nodes[n].data.clear();
        nodes[n].data.shrink_to_fit();
    }

    uint32_t root[128] = {};
    root[0] = kTHeader;
    root[3] = kHashSize;
    memcpy(root + 6, root_table, sizeof root_table);
    root[78] = 0xFFFFFFFFu;
    for (uint32_t page = 0; page < pages; ++page) root[79 + page] = root_block + 1 + page;
    for (unsigned base : {105u, 118u, 121u}) {
        root[base] = options.days; root[base + 1] = options.minutes; root[base + 2] = options.ticks;
    }
    put_bstr(root + 108, options.label);
    root[127] = kStRoot;
    seal(root, 5, block);
    put(root_block, block);
    for (uint32_t page = 0; page < pages; ++page) {
        uint32_t longs[128] = {};
        for (uint32_t w = 0; w < kBitmapLongs; ++w) {
            uint32_t word = 0;
            for (uint32_t bit = 0; bit < 32; ++bit) {
                const uint64_t number = uint64_t(kReserved) + (uint64_t(page) * kBitmapLongs + w) * 32 + bit;
                if (number < blocks && !taken[size_t(number)]) word |= 1u << bit;
            }
            longs[1 + w] = word;
        }
        seal(longs, 0, block);
        put(root_block + 1 + page, block);
    }
    return true;
}

}  // namespace p6_libretro

#endif
