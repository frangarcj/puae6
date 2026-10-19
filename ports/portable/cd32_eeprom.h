/* Private semantic view of the real WinUAE 24C08; not a public ABI or wire format. */
#ifndef P6_CD32_EEPROM_H
#define P6_CD32_EEPROM_H
#include <stdint.h>

struct p6_cd32_eeprom_state {
 uint32_t state, last_data, last_clock, device_out, buffer, estate;
 int32_t current_addr, eeprom_addr, write_offset;
 uint8_t memory[1024];
};
/* Caller owns the 1024-byte backing memory for the lifetime of the chip.
 * Existing eeprom_i2c_set/eeprom_free operate on this object. No host backing. */
void *p6_cd32_eeprom_new(uint8_t *memory);
bool p6_cd32_eeprom_get(void *chip, p6_cd32_eeprom_state *out);
bool p6_cd32_eeprom_set(void *chip, const p6_cd32_eeprom_state *in);
#endif
