# mame-ipf: the IPF decoder of MAME

`ipf_dsk.cpp` and `ipf_dsk.h` are the reader of the SPS (CAPS) floppy image format from the MAME project, `src/lib/formats/ipf_dsk.cpp` and `.h`, at the commit
`7870bf4b4a562b63d1f048d5b1cf8b3250ba45e8` of <https://github.com/mamedev/mame> (2026-08-28). Both files declare, in their first lines,

```
// license:BSD-3-Clause
// copyright-holders:Olivier Galibert
```

and are used here under that license (3-clause BSD, the text is in `LICENSE-BSD-3-Clause.txt`, taken from `docs/legal/BSD-3-Clause` of the same commit). MAME as a whole is
GPL-2.0 (its `COPYING` says so and that individual source files may be under less restrictive licenses, as noted in their header comments); only these two files are used.

Copyright (c) Olivier Galibert, and the other contributors to these files in the history of MAME (Miodrag Milanovic, Vas Crabb, AJR, Roman Donchenko, Curt Coder, holub and firewave).

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the conditions of the license in `LICENSE-BSD-3-Clause.txt` are met:
the copyright notice and the disclaimer are retained in source (this file and the headers of the files), they are reproduced in the documentation of a binary distribution, and
the name of the copyright holder and of the contributors is not used to endorse or promote products derived from this software without written permission.

## What was changed

`original/` holds the two files exactly as they are in MAME (their SHA-256 is in `PROVENANCE.json`). `ipf_dsk.cpp` and `ipf_dsk.h` are those files with the changes of
`local-changes.diff` (a test applies the diff backwards and compares the result with `original/`). In short:

- The interface to MAME's stream layer (`identify`, `load`, `name`, `description`, `extensions`, the whole-file `parse` and its tag scanner) is removed: the project reads the file
  in `ports/portable/media/ipf_track.cpp`, by offsets, without a copy and without writing to it.
- Sizes are added up in 64 bits and limited to what a double-density drive reads (160000 cells a track, 168 track keys, cylinders 0 to 82); offsets into the data of a track
  are checked against its size; a gap pattern of no bits is refused; an ordering of two positions that wrapped around is written so that it cannot.
- The finished track is moved to the caller, not copied, and `mask & (1 << n)` of the speed key no longer shifts beyond 31.

`flopimg.h` and `multibyte.h` are not MAME's: they are small stand-ins written for this project (GPL-2.0-or-later like the rest of it) with the names the decoder uses.
They contain no MAME code. capsimg (the SPS library) is not used and its source was not consulted.
