#ifndef P6_DIAGNOSTICS_H
#define P6_DIAGNOSTICS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum { P6_DIAG_CAPACITY = 32, P6_DIAG_TEXT_CAPACITY = 256 };
enum { P6_DIAG_LOG = 1, P6_DIAG_ERROR = 2, P6_DIAG_NOTIFY = 3,
       P6_DIAG_FILENAME = 4, P6_DIAG_DISK_CHANGE = 5 };
enum { P6_DIAG_TRUNCATED = 1, P6_DIAG_FORMAT_ERROR = 2, P6_DIAG_UNSUPPORTED = 4,
       P6_DIAG_WRITE_PROTECTED = 8 };
typedef struct p6_diagnostic {
 uint32_t kind;
 int32_t code;
 uint32_t flags, length;
 /* NUL-terminated diagnostic bytes; length excludes NUL. A truncated UTF-8
  * sequence may end here: presentation must decode with replacement. */
 char text[P6_DIAG_TEXT_CAPACITY];
} p6_diagnostic;

/* Single emulation thread, one fixed queue per loaded image/sandbox. Formatting
 * uses target libc; no callbacks, retained host pointers, clocks or files.
 * Bridge drains only at boundary.
 * Full queue drops new messages; cumulative loss count saturates at UINT32_MAX.
 * Diagnostic formatting is observational, never an emulator error return.
 * printf formats must be trusted and arguments valid for the format; strings
 * are copied during the call and no argument pointers are retained. */
void p6_diag_write(uint32_t kind, int32_t code, uint32_t flags, const char *format, ...);
/* NULL with nonzero capacity: -1, no consumption. Zero capacity is a no-op. */
int32_t p6_diag_read(p6_diagnostic *out, size_t capacity);
uint32_t p6_diag_dropped(void);
/* Clears diagnostics only, never causal gui_data. Not an Amiga reset. */
void p6_diag_reset(void);

/* Observational copy only; never feed these indicators back into input.
 * Ports 0..3, axes left/right/up/down, buttons 0..10 match pinned WinUAE.
 * Disk events use code=drive 0..3 and text=name (empty means removal), with
 * WRITE_PROTECTED meaningful only for DISK_CHANGE. Names are not host paths. */
typedef struct p6_gui_observation {
 uint32_t led_mask, buttons[4];
 int32_t axes[4][4];
 int32_t activity[4]; /* HD, CD, MD, NET; preserves negative disabled state. */
} p6_gui_observation;
int32_t p6_gui_read(p6_gui_observation *out); /* -1 for NULL, otherwise 0. */
/* Reset presentation counters/indicators only. Preserve CIA power, disk/CPU
 * fields, disabled activity sentinels, and queued diagnostic/media events. */
void p6_gui_reset(void);
#ifdef __cplusplus
}
#endif
#endif
