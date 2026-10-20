/* Decode trusted synthetic fixtures only; not a safe public CHD reader. */
#include <libchdr/chd.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "zlib.h"

int main(int argc, char **argv)
{
    if (argc != 4) return 64;
    if (!strcmp(argv[1], "checksums")) {
        uint8_t bytes[65539];
        for (size_t n = 0; n < sizeof bytes; ++n) bytes[n] = (uint8_t)(n * 31 + n / 251);
        uLong a = adler32(0, NULL, 0), c = crc32(0, NULL, 0);
        for (size_t n = 0; n < sizeof bytes;) {
            uInt take = sizeof bytes - n < 997 ? (uInt)(sizeof bytes - n) : 997;
            a = adler32(a, bytes + n, take); c = crc32(c, bytes + n, take); n += take;
        }
        return a != strtoul(argv[2], NULL, 10) || c != strtoul(argv[3], NULL, 10) ||
            adler32(1, bytes, sizeof bytes) != a || crc32(0, bytes, sizeof bytes) != c;
    }
    chd_file *chd = NULL;
    chd_error error = chd_open(argv[1], CHD_OPEN_READ, NULL, &chd);
    if (error != CHDERR_NONE) { fprintf(stderr, "chd_open: %s (%d)\n", chd_error_string(error), error); return 1; }
    const chd_header *h = chd_get_header(chd);
    uint32_t codec = strcmp(argv[3], "none") ? CHD_MAKE_TAG(argv[3][0], argv[3][1], argv[3][2], argv[3][3]) : 0;
    if (h->version != 5 || h->compression[0] != codec || h->unitbytes != 2448 ||
        h->hunkbytes != 19584 || h->logicalbytes != 40 * 2448) return 2;
    /* Ensure compressed fixtures actually invoke their decoder, not NONE/SELF. */
    if (codec) {
        unsigned compressed = 0;
        for (uint32_t n = 0; n < h->hunkcount; ++n) compressed += h->rawmap[12 * n] == 0;
        if (!compressed) return 3;
    }
    FILE *expected = fopen(argv[2], "rb");
    if (!expected) return 4;
    uint8_t *actual = malloc(h->hunkbytes), *want = malloc(h->hunkbytes);
    if (!actual || !want) return 5;
    /* Reverse and repeat hunks as well as sequential reads, checking every byte. */
    for (unsigned pass = 0; pass < 3; ++pass) {
        for (uint32_t at = 0; at < h->hunkcount; ++at) {
            uint32_t n = pass == 1 ? h->hunkcount - 1 - at : at;
            if (fseek(expected, (long)(n * h->hunkbytes), SEEK_SET) ||
                fread(want, 1, h->hunkbytes, expected) != h->hunkbytes ||
                chd_read(chd, n, actual) != CHDERR_NONE || memcmp(actual, want, h->hunkbytes)) return 6;
        }
    }
    free(want); free(actual); fclose(expected); chd_close(chd);
    return 0;
}
