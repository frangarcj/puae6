#pragma once

#include "deps/libz/zlib.h"
#include "deps/libz/unzip.h"

struct zfile;

/* Opens a zip through minizip using a memory copy of an already-open zfile. */
unzFile bizhawk_unzopen_zfile(struct zfile *z);
