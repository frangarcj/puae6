// PUAE6 stand-in for MAME's src/lib/util/multibyte.h: the one function ipf_dsk.cpp uses. Written for this project; it contains no MAME code.
// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef P6_MAME_IPF_MULTIBYTE_H
#define P6_MAME_IPF_MULTIBYTE_H

#include <cstdint>

constexpr uint32_t get_u32be(const uint8_t *buf) noexcept
{
	return (uint32_t(buf[0]) << 24) | (uint32_t(buf[1]) << 16) | (uint32_t(buf[2]) << 8) | uint32_t(buf[3]);
}

#endif
