/* WinUAE addrbank binding for the bounded PUAE6 RTG VRAM. */
#ifndef P6_RTG_MEMORY_H
#define P6_RTG_MEMORY_H

#include <stdint.h>

struct addrbank;

extern struct addrbank gfxmem_bank;
extern struct addrbank *gfxmem_banks[4];

int p6_rtg_memory_enable(void);
void p6_rtg_memory_disable(void);
int p6_rtg_memory_set_bus_base(uint32_t start);
int p6_rtg_memory_rebind(void);
#endif
