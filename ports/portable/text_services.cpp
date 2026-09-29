#include "sysconfig.h"
#include "sysdeps.h"
#include <string.h>

// TCHAR is UTF-8 in this profile (tchar.h), so the legacy "ANSI <-> TCHAR" helpers
// have fixed, locale-free meanings. Storage comes from xmalloc, is owned by the
// caller and is released with xfree; failure is a NULL result with errno set.

// Length in TCHAR units. The units are bytes: legacy callers index and memmove
// strings in whole TCHAR elements and never count code points.
int uaetcslen(const TCHAR *s)
{
 return (int)strlen(s);
}

TCHAR *my_strdup(const TCHAR *s)
{
 const size_t bytes = strlen(s) + 1;
 TCHAR *copy = xmalloc(TCHAR, bytes);
 if (copy)
  memcpy(copy, s, bytes);
 return copy;
}

// Amiga and legacy text is single-byte ISO-8859-1: bytes 0x00-0x7f are ASCII and
// stay as they are, 0x80-0xff become the UTF-8 encoding of U+0080-U+00FF.
TCHAR *au(const char *s)
{
 size_t bytes = 1;
 for (const unsigned char *p = (const unsigned char *)s; *p; ++p)
  bytes += *p < 0x80 ? 1 : 2;
 TCHAR *out = xmalloc(TCHAR, bytes);
 if (!out)
  return NULL;
 TCHAR *w = out;
 for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
  if (*p < 0x80) {
   *w++ = (TCHAR)*p;
  } else {
   *w++ = (TCHAR)(0xc0 | (*p >> 6));
   *w++ = (TCHAR)(0x80 | (*p & 0x3f));
  }
 }
 *w = 0;
 return out;
}
