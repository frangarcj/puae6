// The volume builder of the libretro adapter (adapters/libretro/volume.h), alone: tests/hdf/test_volume.py runs it against the oracle (tools/whdload/ffs.py and lha.py) and tests/fuzz uses
// the same headers. No engine, no libretro: the headers are included as the core includes them.
//
//   volume_tool tree MANIFEST OUT    MANIFEST lines: "D <hex of the path>" or "F <hex of the path> <hex of the data, or - when empty>"
//   volume_tool dir  DIRECTORY OUT   a directory of the host, read through the C library as the core does without a VFS
//   volume_tool lha  FILE OUT        an LHA archive
//   volume_tool zip  FILE OUT        a ZIP file of files
//   volume_tool iszipvolume FILE     prints "volume" or "disk"
// Writes the image to OUT (summary on stdout) and exits 0, or says why on stderr and exits 2.
#include "../../adapters/libretro/volume.h"

#include <stdio.h>
#include <fstream>
#include <iostream>
#include <sstream>

using namespace p6_libretro;

static bool unhex(const std::string &text, std::string *out)
{
    out->clear();
    if (text == "-") return true;
    if (text.size() % 2) return false;
    for (size_t i = 0; i < text.size(); i += 2) {
        unsigned value = 0;
        if (sscanf(text.substr(i, 2).c_str(), "%2x", &value) != 1) return false;
        out->push_back(char(value));
    }
    return true;
}

static bool read_all(const char *path, std::vector<uint8_t> *bytes)
{
    FILE *file = fopen(path, "rb");
    if (!file) return false;
    uint8_t chunk[65536];
    size_t got;
    bytes->clear();
    while ((got = fread(chunk, 1, sizeof chunk, file)) > 0) bytes->insert(bytes->end(), chunk, chunk + got);
    fclose(file);
    return true;
}

int main(int argc, char **argv)
{
    if (argc < 3) return 64;
    const std::string mode = argv[1];
    std::vector<VolumeFile> files;
    std::string why;
    bool ok = false;
    if (mode == "iszipvolume") {
        std::vector<uint8_t> bytes;
        if (!read_all(argv[2], &bytes)) return 2;
        puts(zip_is_volume(bytes) ? "volume" : "disk");
        return 0;
    }
    if (argc < 4) return 64;
    if (mode == "tree") {
        std::ifstream in(argv[2]);
        std::string line;
        ok = true;
        while (ok && std::getline(in, line)) {
            std::istringstream words(line);
            std::string kind, path, data;
            words >> kind >> path >> data;
            VolumeFile file;
            std::string raw, bytes;
            if (!unhex(path, &raw) || (kind == "F" && !unhex(data, &bytes))) { why = "bad manifest line"; ok = false; break; }
            file.path = raw;
            file.is_dir = kind == "D";
            file.data.assign(bytes.begin(), bytes.end());
            files.push_back(std::move(file));
        }
    } else if (mode == "dir") {
        FileReader reader;
        ok = volume_from_directory(reader, argv[2], &files, &why);
    } else if (mode == "lha" || mode == "zip") {
        std::vector<uint8_t> bytes;
        if (!read_all(argv[2], &bytes)) { fprintf(stderr, "error: cannot read %s\n", argv[2]); return 2; }
        if (bytes.size() > kMaxVolumeArchiveBytes) { fprintf(stderr, "error: archive too large\n"); return 2; }
        ok = mode == "lha" ? volume_from_lha(bytes, &files, &why) : volume_from_zip(bytes, &files, &why);
    } else {
        return 64;
    }
    std::vector<uint8_t> image;
    VolumeSummary summary;
    if (ok) ok = build_volume_image(&files, &image, &summary, &why);
    if (!ok) {
        fprintf(stderr, "error: %s\n", why.c_str());
        return 2;
    }
    FILE *out = fopen(argv[3], "wb");
    if (!out || fwrite(image.data(), 1, image.size(), out) != image.size()) return 3;
    fclose(out);
    printf("files=%zu directories=%zu bytes=%zu image=%zu\n", summary.files, summary.directories, summary.bytes, image.size());
    return 0;
}
