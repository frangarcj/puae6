#include "sysdeps.h"
#include "diagnostics.h"
#include <stdarg.h>
#include <errno.h>

/* Ordinary guest storage: snapshots must retain pending messages and counters. */
static p6_diagnostic queue_slots[P6_DIAG_CAPACITY];
static uint32_t head, queued_count, dropped;

static void enqueue(uint32_t kind, int32_t code, uint32_t flags,
                    const char *format, va_list args)
{
 if (queued_count == P6_DIAG_CAPACITY) {
  if (dropped != UINT32_MAX) ++dropped;
  return;
 }
 int saved_errno = errno;
 p6_diagnostic record = {};
 record.kind = kind; record.code = code; record.flags = flags;
 int length = format ? vsnprintf(record.text, sizeof record.text, format, args) : -1;
 if (length < 0) {
  memset(record.text, 0, sizeof record.text);
  record.flags |= P6_DIAG_FORMAT_ERROR;
 } else if ((size_t)length >= sizeof record.text) {
  record.length = sizeof record.text - 1;
  record.flags |= P6_DIAG_TRUNCATED;
 } else {
  record.length = (uint32_t)length;
 }
 queue_slots[(head + queued_count) % P6_DIAG_CAPACITY] = record;
 ++queued_count;
 errno = saved_errno;
}

void p6_diag_write(uint32_t kind, int32_t code, uint32_t flags, const char *format, ...)
{
 va_list args; va_start(args, format);
 enqueue(kind, code, flags, format, args);
 va_end(args);
}

int32_t p6_diag_read(p6_diagnostic *out, size_t capacity)
{
 if (!out && capacity) return -1;
 size_t n = capacity < queued_count ? capacity : queued_count;
 for (size_t i = 0; i < n; ++i) {
  out[i] = queue_slots[head];
  queue_slots[head] = {};
  head = (head + 1) % P6_DIAG_CAPACITY;
 }
 queued_count -= (uint32_t)n;
 return (int32_t)n;
}

uint32_t p6_diag_dropped(void) { return dropped; }
void p6_diag_reset(void)
{
 memset(queue_slots, 0, sizeof queue_slots);
 head = queued_count = dropped = 0;
}
void logging_init(void) { p6_diag_reset(); }

/* Upstream signatures come from the real sysdeps.h (TCHAR is UTF-8 char).
 * These functions only enqueue; flushing to a frontend is a bridge operation. */
#define P6_LOG_FUNCTION(name, kind) \
 void name(const TCHAR *format, ...) { \
  va_list args; va_start(args, format); \
  enqueue(kind, 0, 0, format, args); va_end(args); \
 }
P6_LOG_FUNCTION(write_log, P6_DIAG_LOG)
P6_LOG_FUNCTION(write_logx, P6_DIAG_LOG)
P6_LOG_FUNCTION(write_dlog, P6_DIAG_LOG)
P6_LOG_FUNCTION(console_out_f, P6_DIAG_LOG)
P6_LOG_FUNCTION(gui_message, P6_DIAG_ERROR)
#undef P6_LOG_FUNCTION
void console_out(const TCHAR *text) { p6_diag_write(P6_DIAG_LOG, 0, 0, "%s", text ? text : ""); }

int gui_message_multibutton(int flags, const TCHAR *format, ...)
{
 va_list args; va_start(args, format);
 enqueue(P6_DIAG_ERROR, flags, P6_DIAG_UNSUPPORTED, format, args);
 va_end(args);
 return -1; /* Explicit unsupported/cancel; never consent to an action. */
}
