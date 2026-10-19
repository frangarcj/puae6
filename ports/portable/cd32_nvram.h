/* Private Akiko owner/protocol view, not a public ABI or wire format. */
#ifndef P6_CD32_NVRAM_H
#define P6_CD32_NVRAM_H
#include "cd32_eeprom.h"
struct p6_cd32_nvram_state {
 p6_cd32_eeprom_state chip;
 uint8_t direction, scl, sda, reserved;
};
/* Only an active, initialized 1024-byte owner can be read/restored. Setter
 * validates chip and latched pins before mutation; DDR keeps all eight bits.
 * Session creation/reset/destruction use Akiko's real device lifecycle. */
bool p6_akiko_nvram_get(p6_cd32_nvram_state *out);
bool p6_akiko_nvram_set(const p6_cd32_nvram_state *in);
#endif
