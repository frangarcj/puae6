/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef P6_ROM_STATUS_H
#define P6_ROM_STATUS_H
#ifdef __cplusplus
extern "C" {
#endif
enum { P6_ROM_LOAD_OK = 0, P6_ROM_UNLOADED = 1, P6_ROM_OPEN_ERROR = 2,
       P6_ROM_READ_ERROR = 3, P6_ROM_UNSUPPORTED = 4, P6_ROM_NO_MEMORY = 5 };
/* Result of the engine's most recent effective ROM load, per image/sandbox.
 * Errors survive resets that do not reload ROM. Init/cleanup mark unloaded;
 * a new load replaces the result. Version queries/diagnostics do not change it.
 * LOAD_OK is legacy loader completion, NOT firmware/profile/hash validation.
 * The bridge must validate firmware and check this after load/reset and before
 * run; CPU entry also guards it. No public ABI success is implied by this API. */
int p6_memory_rom_status(void);
#ifdef __cplusplus
}
#endif
#endif
