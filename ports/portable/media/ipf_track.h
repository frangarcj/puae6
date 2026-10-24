// IPF (the SPS / CAPS floppy image) as the bridge and the engine read it (T58). A disk is a list of tracks, each one revolution of the cells under the head: the cells of the
// MFM stream (a 1 is a flux transition), the time each took (2000 ns is the nominal cell, protections vary it) and the cells that are weak or noise (they read differently
// at each revolution). The decoder is the free reader of MAME (third_party/mame-ipf, BSD-3-Clause, docs/adr/0013-ipf-decoder-licence.md); this is the interface to it that
// the rest of the project uses. capsimg is not used.
//
// The file is never copied: it is read through a callback at offsets (the engine reads the bytes the bridge registered, the tests an array), once whole when the image is
// opened (every tag has its CRC checked and the tracks are indexed) and, afterwards, the data of one track at a time when the track is decoded.
#ifndef P6_MEDIA_IPF_TRACK_H
#define P6_MEDIA_IPF_TRACK_H

#include <stddef.h>
#include <stdint.h>

namespace p6_ipf {

constexpr uint32_t kMaxFileBytes = 3u * 1024u * 1024u;   // as every track-level floppy image (p6_floppy::kMaxTrackDiskBytes)
constexpr uint32_t kMaxCells = 8u * 20000u;              // a track the drive can read: the 20000 bytes of trackdisk.h
constexpr uint32_t kMaxTrackData = 256u * 1024u;         // the block descriptions and data of one track
constexpr uint32_t kTracks = 2u * 83u;                   // the tracks of a drive (disk.cpp MAX_TRACKS)
constexpr uint32_t kNominalCellNs = 2000;

// Reads exactly `length` bytes at `offset` of the image into `out`; false when it cannot.
typedef bool (*ReadFn)(const void *context, uint64_t offset, void *out, size_t length);

struct Image;                                            // opaque: the index of the tracks

// Opens the image of `bytes` bytes: nullptr (and a static text in *why) when it is not a well-formed IPF the decoder reads. Allocates; close() frees. The callback is given `context`,
// or, when context_size is not 0 (at most kContextBytes), the image's own copy of those bytes, which lives as long as the image does.
constexpr size_t kContextBytes = 96;
Image *open(ReadFn read, const void *context, uint64_t bytes, const char **why, size_t context_size = 0);
// The same, over bytes that stay where they are while the image is open.
Image *open_memory(const uint8_t *data, uint64_t bytes, const char **why);
// The same, over the medium of the bridge's store with that id (blob_io.h): read by offset, never copied, nothing kept between two reads.
Image *open_blob(const char *id, const char **why);
void close(Image *image);

// One more than the highest track (cylinder * 2 + head) the image describes.
unsigned track_count(const Image *image);

// Is the track (cylinder * 2 + head) described by the image? A track that is not is an unformatted one for the drive.
bool has_track(const Image *image, unsigned track);

struct Track {
    uint32_t cells;      // length of the revolution in cells (bits of the MFM stream)
    uint32_t splice;     // the cell where the track was written first and last (the write splice), < cells
    bool weak;           // some cells are weak or noise: another revolution reads other bits (decode with revolution + 1)
};

enum Result : int { kFailed = -1, kAbsent = 0, kDecoded = 1 };

// Capacity the buffers of decode() need for any track: words and timing values.
constexpr size_t kWordCapacity = kMaxCells / 16u + 1u;
constexpr size_t kTimingCapacity = kMaxCells / 8u + 2u;

// Decodes one revolution of a track. words: the MFM stream, 16 cells to a word, the first cell in the top bit (the order of disk.cpp's bigmfmbuf), zero beyond the
// end. timing: one value for every 8 cells (and two more, a copy of the first), the mean time of those cells in thousandths of a nominal cell (1000 = 2000 ns), as
// disk.cpp's tracktiming. The weak and noise cells take their bits from a generator seeded by the track and the revolution, nothing else: the same track and revolution
// are the same bits in any process. kAbsent: no such track in the image; kFailed: the description of the track does not decode (or does not fit the buffers).
int decode(const Image *image, unsigned track, unsigned revolution, uint16_t *words, size_t word_capacity, uint16_t *timing, size_t timing_capacity, Track *out);

// Does every track of the image decode? nullptr when so, else the text of the first refusal (static) and its track in *track.
const char *check_tracks(const Image *image, unsigned *track);

}  // namespace p6_ipf

#endif
