// The engine side of the IDE port of Gayle (T56, patches 0320 to 0322): the hard files of the session on its drives and the state of the chip and its drives.
// Declared here, as the accessors of the hard-file section of P6DK are in state_hdf.cpp, because upstream's gayle.h has no place for them.
#ifndef P6_BRIDGE_IDE_UNITS_H
#define P6_BRIDGE_IDE_UNITS_H
#include <stdint.h>

struct uaedev_config_info;

// The drives Gayle opens on every reset (gayle.cpp 0321): unit 0 and 1 the master and the slave of its channel, 2 and 3 those of the second one behind the IDE splitter.
// p6_gayle_unit false for a unit that is taken or out of range; the list is empty at p6_gayle_units_clear (every build_preferences).
void p6_gayle_units_clear(void);
bool p6_gayle_unit(int ch, const struct uaedev_config_info *ci);

// The registers of the chip (4 words) and, per slot, the semantic values of a drive (54 words) with only the causal bytes of its transfer buffer (at most one 128-sector CD burst).
// The *_valid and *_live_bytes functions are pure: the bridge asks them while it validates a state, before the machine has run its reset head, so that applying a state that
// passed cannot fail. `splitter` says whether the second channel exists; `kind` says whether each slot is empty, HDF or CD.
constexpr unsigned kGayleWords = 4, kIdeWords = 54, kIdeSecbufMax = 128 * 2048;
enum : uint32_t { kIdeSlotEmpty = 0, kIdeSlotHdf = 1, kIdeSlotCd = 2 };
bool p6_gayle_words_valid(const uint32_t *w, unsigned n, bool splitter);
bool p6_ide_words_valid(const uint32_t *w, unsigned n, uint32_t kind);
uint32_t p6_ide_live_bytes(const uint32_t *w, uint32_t kind);
bool p6_ide_atapi_disc_matches(const uint32_t *w, unsigned n, const uint8_t disc_id[32]);
bool p6_gayle_state_get(uint32_t *w, unsigned n);
bool p6_gayle_state_set(const uint32_t *w, unsigned n);
bool p6_gayle_drive_state_get(unsigned unit, uint32_t *w, unsigned n, uint8_t *secbuf, uint32_t *secbuf_size);
bool p6_gayle_drive_state_set(unsigned unit, const uint32_t *w, unsigned n, const uint8_t *secbuf, uint32_t secbuf_size);

#endif
