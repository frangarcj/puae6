// license:BSD-3-Clause
// copyright-holders:Olivier Galibert
#ifndef MAME_FORMATS_IPF_DSK_H
#define MAME_FORMATS_IPF_DSK_H

#pragma once

#include "flopimg.h"

#include <vector>

// PUAE6: the stream interface of MAME (identify, load, name, description, extensions) is not carried. The decoder is reached through
// ports/portable/media/ipf_track.cpp, which includes ipf_dsk.cpp and so sees ipf_decode, now public.
class ipf_format : public floppy_image_format_t
{
public:
	struct ipf_decode;
};

#endif // MAME_FORMATS_IPF_DSK_H
