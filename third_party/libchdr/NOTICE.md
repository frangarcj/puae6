# Standalone CHD dependency (T66t1)

Imported byte for byte from TASEmulators/libretro-uae commit
ccecb1ead642c1bbe391308b88a7ffa9478b918d, verified against
references/machawk-uae.manifest.json. selection.json records every source/hash.
No PUAE engine or libretro-common source is included.

* libchdr: BSD-3-Clause; Romain Tisserand and Aaron Giles. Preserve LICENSE.txt
  and the notices in the selected sources/headers in source distributions;
  reproduce BSD notices in binary distribution materials.
* LZMA SDK 19.00: Igor Pavlov, public domain; notices in deps/lzma sources and
  headers, including 7zVersion.h. _7ZIP_ST excludes threads.
* zlib snapshot, API header 1.2.7 (some implementation notices 1.2.8):
  Jean-loup Gailly and Mark Adler; license in deps/zlib/zlib.h.
  Preserve that notice and identify altered sources if modifications are made.
* dr_flac 0.12.28: David Reid; public domain or MIT-0, chosen MIT-0 here.
  Both license texts are preserved at the end of include/dr_libs/dr_flac.h.

patches/0001 corrects Adler-32 ABI and standalone includes, without
changing the 32-bit calculation. Applied only to a derived build copy.
patches/0002 replaces host FILE/cursor with borrowed ReadFn offsets, validates
CDv5 geometry/resources, and makes construction/cleanup transactional.
patches/0003 validates bounded maps/metadata, resolves SELF iteratively and
checks bitstream/Huffman input. patches/0004 validates CD codec split lengths,
exact decoder completion, FLAC format/positions and failed codec cleanup.

The private CDv5 reader uses bounded ReadFn offsets, validated maps/metadata
and checked codec payloads. This dependency is not linked to a product. CD TOC,
identity, subchannels, provider restore and public loading remain later gates.
