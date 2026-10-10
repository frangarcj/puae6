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

// TCHAR (UTF-8) to the legacy single-byte text of the Amiga side: ISO-8859-1. A code point above U+00FF, or a malformed sequence, becomes `defchar`
// ('?' when defchar is not positive). At most maxlen-1 bytes are written and the result always ends with NUL; the number of bytes the whole text needs
// (excluding NUL) is returned, so a caller can size a buffer by passing maxlen 0.
static size_t latin1_from_utf8(char *dst, size_t maxlen, const TCHAR *src, int defchar)
{
 const char other = defchar > 0 && defchar < 0x100 ? (char)defchar : '?';
 size_t need = 0;
 for (const unsigned char *p = (const unsigned char *)src; *p; ++need) {
  unsigned char out = other;
  if (*p < 0x80) {
   out = *p++;
  } else if ((*p == 0xc2 || *p == 0xc3) && (p[1] & 0xc0) == 0x80) {
   out = (unsigned char)(((*p & 3) << 6) | (p[1] & 0x3f));
   p += 2;
  } else {
   ++p;                                   // the rest of a longer or broken sequence is skipped byte by byte
   while ((*p & 0xc0) == 0x80) ++p;
  }
  if (dst && need + 1 < maxlen) dst[need] = (char)out;
 }
 if (dst && maxlen) dst[need < maxlen - 1 ? need : maxlen - 1] = 0;
 return need;
}

int uaestrlen(const char *s)
{
 return (int)strlen(s);
}

char *ua_fs(const TCHAR *s, int defchar)
{
 const size_t bytes = latin1_from_utf8(NULL, 0, s, defchar) + 1;
 char *out = xmalloc(char, bytes);
 if (out)
  latin1_from_utf8(out, bytes, s, defchar);
 return out;
}

char *ua(const TCHAR *s)
{
 return ua_fs(s, '?');
}

char *ua_fs_copy(char *dst, int maxlen, const TCHAR *src, int defchar)
{
 latin1_from_utf8(dst, maxlen > 0 ? (size_t)maxlen : 0, src, defchar);
 return dst;
}

char *ua_copy(char *dst, int maxlen, const TCHAR *src)
{
 return ua_fs_copy(dst, maxlen, src, '?');
}

TCHAR *au_fs(const char *s)
{
 return au(s);
}

TCHAR *au_fs_copy(TCHAR *dst, int maxlen, const char *src)
{
 TCHAR *whole = au(src);
 if (whole && maxlen > 0) {
  size_t n = strlen(whole);
  if (n > (size_t)maxlen - 1) {
   n = (size_t)maxlen - 1;
   while (n && ((unsigned char)whole[n] & 0xc0) == 0x80) --n;      // never cut a two-byte character in half
  }
  memcpy(dst, whole, n);
  dst[n] = 0;
 }
 xfree(whole);
 return dst;
}

bool samepath(const TCHAR *p1, const TCHAR *p2)
{
 return strcmp(p1, p2) == 0;
}
