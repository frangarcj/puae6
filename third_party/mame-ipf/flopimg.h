// PUAE6 stand-in for MAME's src/lib/formats/flopimg.h. It carries only what ipf_dsk.cpp uses of the floppy image layer, with the same names and values, and keeps the
// one track the decoder produced as a vector of cell levels instead of converting it to flux transitions. Written for this project; it contains no MAME code.
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef P6_MAME_IPF_FLOPIMG_H
#define P6_MAME_IPF_FLOPIMG_H

#include <cstdint>
#include <utility>
#include <vector>

class floppy_image
{
public:
	// MAME: the time of a cell (in nanoseconds) in the low 28 bits and what the cell is in the high 4.
	enum : uint32_t {
		TIME_MASK = 0x0fffffff,
		MG_MASK   = 0xf0000000,
		MG_SHIFT  = 28,
		MG_F      = (0u << MG_SHIFT),   // flux orientation change
		MG_N      = (1u << MG_SHIFT),   // non-magnetized zone: a weak cell
		MG_D      = (2u << MG_SHIFT),   // damaged zone
		MG_E      = (3u << MG_SHIFT)    // end of zone
	};

	// What generate_track_from_levels was given for the track decoded last.
	std::vector<uint32_t> levels;
	int splice = -1;
	int track = -1;
	int head = -1;
	bool captured = false;
};

class floppy_image_format_t
{
protected:
	// MAME: the input of the level generator, with the time in the low bits as above.
	enum : uint32_t {
		MG_SHIFT = 28,
		MG_0     = (4u << MG_SHIFT),    // a cell without a flux transition
		MG_1     = (5u << MG_SHIFT),    // a cell with one
		MG_W     = (6u << MG_SHIFT)     // a weak cell
	};

	static void generate_track_from_levels(int track, int head, const std::vector<uint32_t> &trackbuf, int splice_pos, floppy_image &image)
	{
		image.levels = trackbuf;
		image.splice = splice_pos;
		image.track = track;
		image.head = head;
		image.captured = true;
	}

	// The same, taking the buffer instead of copying it (the local change of ipf_dsk.cpp: a track is up to 160000 cells of 4 bytes).
	static void generate_track_from_levels(int track, int head, std::vector<uint32_t> &&trackbuf, int splice_pos, floppy_image &image)
	{
		image.levels = std::move(trackbuf);
		image.splice = splice_pos;
		image.track = track;
		image.head = head;
		image.captured = true;
	}
};

#endif
