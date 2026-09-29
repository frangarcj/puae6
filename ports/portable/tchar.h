/* PUAE6 text uses UTF-8 bytes, never the process locale or Win32 wide chars.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef P6_TCHAR_H
#define P6_TCHAR_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#if defined(UNICODE) || defined(_UNICODE)
#error "P6 TCHAR requires narrow UTF-8, not a Windows Unicode build"
#endif
typedef char TCHAR;
#define _T(literal) literal
#define _TEXT(literal) literal

/* Legacy engine strings are NUL-terminated byte sequences, not Unicode scalar
 * operations. Copies require caller-owned capacity and nonoverlapping ranges.
 * strncpy pads short inputs but does NOT terminate a truncated input. These
 * operations neither validate UTF-8 nor replace the checked public copy below.
 * No formatting, numeric conversion or filesystem aliases are provided. */
#define _tcslen strlen
#define _tcscpy strcpy
#define _tcscat strcat
#define _tcsncpy strncpy
#define _tcscmp strcmp
#define _tcsncmp strncmp
#define _tcschr strchr
#define _tcsrchr strrchr
#define _tcsstr strstr
#define _tcscspn strcspn

/* ASCII syntax only. Non-ASCII bytes and EOF are unchanged/unclassified, even
 * when plain char is signed. Comparisons fold unsigned bytes; no libc locale. */
static inline int p6_ascii_tolower(int c)
{ return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c; }
static inline int p6_ascii_toupper(int c)
{ return c >= 'a' && c <= 'z' ? c - ('a' - 'A') : c; }
static inline int p6_ascii_isdigit(int c)
{ return c >= '0' && c <= '9'; }
static inline int p6_ascii_isxdigit(int c)
{ return p6_ascii_isdigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
static inline int p6_ascii_stricmp(const char *a, const char *b)
{
    for (;;) {
        int x = p6_ascii_tolower((unsigned char)*a++);
        int y = p6_ascii_tolower((unsigned char)*b++);
        if (x != y || !x) return x - y;
    }
}
static inline int p6_ascii_strnicmp(const char *a, const char *b, size_t n)
{
    while (n--) {
        int x = p6_ascii_tolower((unsigned char)*a++);
        int y = p6_ascii_tolower((unsigned char)*b++);
        if (x != y || !x) return x - y;
    }
    return 0;
}
#define _totlower p6_ascii_tolower
#define _totupper p6_ascii_toupper
#define _istdigit p6_ascii_isdigit
#define _istxdigit p6_ascii_isxdigit
#define _tcsicmp p6_ascii_stricmp
#define _tcsnicmp p6_ascii_strnicmp
static inline int p6_ascii_isspace(int c)
{
    return c == ' ' || (c >= '\t' && c <= '\r');
}
#define _istspace p6_ascii_isspace

enum p6_text_result { P6_TEXT_OK = 0, P6_TEXT_ARGUMENT = -1,
                      P6_TEXT_CAPACITY = -2, P6_TEXT_UTF8 = -3 };

/* Explicit byte length excludes the terminator. Embedded NUL is rejected.
 * Caller supplies valid ranges. On error dst is unchanged; overlap is allowed.
 * Capacity includes the final NUL. No truncation or lossy transcoding. */
static inline int p6_tchar_copy(TCHAR *dst, size_t capacity,
                                const char *src, size_t bytes)
{
    if (!dst || (!src && bytes)) return P6_TEXT_ARGUMENT;
    if (bytes >= capacity) return P6_TEXT_CAPACITY; /* also avoids bytes+1 overflow */
    size_t i = 0;
    while (i < bytes) {
        unsigned char lead = (unsigned char)src[i++];
        if (lead == 0) return P6_TEXT_UTF8;
        if (lead < 0x80) continue;
        unsigned trail;
        uint32_t code, minimum;
        if (lead >= 0xc2 && lead <= 0xdf) { trail = 1; code = lead & 0x1f; minimum = 0x80; }
        else if (lead >= 0xe0 && lead <= 0xef) { trail = 2; code = lead & 0x0f; minimum = 0x800; }
        else if (lead >= 0xf0 && lead <= 0xf4) { trail = 3; code = lead & 7; minimum = 0x10000; }
        else return P6_TEXT_UTF8;
        if (trail > bytes - i) return P6_TEXT_UTF8;
        while (trail--) {
            unsigned char next = (unsigned char)src[i++];
            if ((next & 0xc0) != 0x80) return P6_TEXT_UTF8;
            code = (code << 6) | (next & 0x3f);
        }
        if (code < minimum || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff))
            return P6_TEXT_UTF8;
    }
    if (bytes) memmove(dst, src, bytes);
    dst[bytes] = 0;
    return P6_TEXT_OK;
}
#endif
