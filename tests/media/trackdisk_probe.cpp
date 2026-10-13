// The floppy image validator of ports/portable/media/trackdisk.h on its own (tests/media/test_trackdisk_formats.py), built with the sanitizers and no engine.
//   usage: trackdisk_probe BUNDLE      BUNDLE is a file of images, each a little-endian u32 length and its bytes; one line per image: "ok <format>" or "refused: <why>"
// Each image is copied into a heap block of exactly its size before it is handed to classify_floppy, so that a read past the end is an ASan report and not a wrong answer.
#include "trackdisk.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: %s BUNDLE\n", argv[0]); return 64; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 2; }
    for (;;) {
        uint8_t head[4];
        if (fread(head, 1, 4, f) != 4) break;
        const uint32_t size = uint32_t(head[0]) | uint32_t(head[1]) << 8 | uint32_t(head[2]) << 16 | uint32_t(head[3]) << 24;
        uint8_t *exact = static_cast<uint8_t *>(malloc(size ? size : 1));
        if (size && fread(exact, 1, size, f) != size) { fprintf(stderr, "the bundle is cut\n"); return 2; }
        const p6_floppy::Verdict v = p6_floppy::classify_floppy(exact, size);
        if (v.ok) printf("ok %s\n", p6_floppy::format_name(v.format));
        else printf("refused: %s\n", v.why);
        free(exact);
    }
    fclose(f);
    return 0;
}
