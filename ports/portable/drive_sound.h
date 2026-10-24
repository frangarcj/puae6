/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef P6_DRIVE_SOUND_H
#define P6_DRIVE_SOUND_H
#include <stdint.h>
namespace p6_drive_sound {
constexpr unsigned kWords = 40;
void gain(unsigned percent); // Presentation only; set before advancing a unit.
bool healthy();
bool get(uint32_t *words, unsigned count);
bool valid(const uint32_t *words, unsigned count);
bool set(const uint32_t *words, unsigned count);
}
#endif
