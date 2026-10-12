// The archive readers of the libretro adapter (T59: sevenzip.h, archive.h), outside the core, for tests/libretro-host/test_archives.py. Built with the sanitizers; no engine.
//   archive_probe sevenzip FILE [MAX_UNPACK]   every entry of a 7z: "entry NAME dir|file SIZE", then for each file "data NAME crc32=HEX size=N" or "refused NAME: WHY"
//   archive_probe disks FILE [MEMBER]          the disks of a ZIP, 7z or RP9 in playlist order: "kind K", "note N", "disk NAME crc32=HEX", or "refused: WHY"
//   archive_probe order NAME...                the names in the order PUAE5's playlist gives them (archive.h)
#include "archive.h"

#include <stdio.h>
#include <stdlib.h>
#include <algorithm>
#include <string>
#include <vector>

using namespace p6_libretro;

static bool slurp(const char *path, std::vector<uint8_t> *out)
{
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    uint8_t buffer[65536];
    size_t got;
    while ((got = fread(buffer, 1, sizeof buffer, f)) > 0) out->insert(out->end(), buffer, buffer + got);
    fclose(f);
    return true;
}

int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    const std::string command = argv[1];
    if (command == "order") {
        std::vector<std::string> names(argv + 2, argv + argc);
        std::sort(names.begin(), names.end(), archive_detail::disk_name_before);
        for (const std::string &name : names) printf("%s\n", name.c_str());
        return 0;
    }
    std::vector<uint8_t> file;
    if (!slurp(argv[2], &file)) {
        fprintf(stderr, "cannot read %s\n", argv[2]);
        return 2;
    }
    if (command == "sevenzip") {
        sevenzip::Limits limits;
        if (argc > 3) limits.max_unpack = strtoull(argv[3], nullptr, 10);
        sevenzip::Archive archive;
        std::string why;
        if (!archive.open(file.data(), file.size(), limits, &why)) {
            printf("refused: %s\n", why.c_str());
            return 0;
        }
        for (const sevenzip::Entry &entry : archive.entries())
            printf("entry %s %s %llu\n", entry.name.c_str(), entry.is_dir ? "dir" : "file", (unsigned long long)entry.size);
        for (size_t i = 0; i < archive.entries().size(); ++i) {
            const sevenzip::Entry &entry = archive.entries()[i];
            if (entry.is_dir) continue;
            std::vector<uint8_t> data;
            if (archive.extract(i, &data, &why)) printf("data %s crc32=%08x size=%zu\n", entry.name.c_str(), crc32_update(0, data.data(), data.size()), data.size());
            else printf("refused %s: %s\n", entry.name.c_str(), why.c_str());
        }
        return 0;
    }
    if (command == "disks") {
        ArchiveLimits limits;
        DiskSet set;
        std::string why;
        const bool ok = read_disk_set(file, argc > 3 ? argv[3] : "", limits, &set, &why);
        if (!ok) {
            printf("refused: %s\n", why.c_str());
            return 0;
        }
        printf("kind %s\n", set.kind.c_str());
        for (const std::string &note : set.notes) printf("note %s\n", note.c_str());
        for (const DiskMember &disk : set.disks) printf("disk %s crc32=%08x size=%zu\n", disk.name.c_str(), crc32_update(0, disk.adf.data(), disk.adf.size()), disk.adf.size());
        return 0;
    }
    return 2;
}
