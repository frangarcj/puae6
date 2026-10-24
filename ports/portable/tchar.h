/* PUAE6 text uses UTF-8 bytes, never the process locale or Win32 wide chars.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef P6_TCHAR_H
#define P6_TCHAR_H
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
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
 * Bounded formatting and locale-free integer parsing are provided at the end of
 * this header. No scanf, floating-point or filesystem aliases exist. */
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
/* Integer parsing with the strtol contract but no libc locale: ASCII whitespace,
 * optional sign, base 0 or 2..36, "0x" only for base 0/16 and only when a hex
 * digit follows (otherwise the "0" alone is parsed). No digits: returns 0 and
 * *end = s. Overflow consumes every digit, returns LONG_MAX/LONG_MIN and sets
 * ERANGE. An invalid base sets EINVAL. errno is otherwise untouched. */
static inline int p6_digit_value(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'z') return c - 'a' + 10;
    if (c >= 'A' && c <= 'Z') return c - 'A' + 10;
    return 36;
}
static inline long p6_tcstol(const char *s, char **end, int base)
{
    const char *p = s;
    unsigned long limit, value = 0;
    int negative = 0, any = 0, overflow = 0, digit;
    if (base < 0 || base == 1 || base > 36) {
        if (end) *end = (char *)s;
        errno = EINVAL;
        return 0;
    }
    while (p6_ascii_isspace((unsigned char)*p)) ++p;
    if (*p == '+' || *p == '-') negative = *p++ == '-';
    if ((base == 0 || base == 16) && p[0] == '0' && (p[1] == 'x' || p[1] == 'X') &&
        p6_ascii_isxdigit((unsigned char)p[2])) {
        p += 2;
        base = 16;
    } else if (base == 0) {
        base = p[0] == '0' ? 8 : 10;
    }
    limit = negative ? (unsigned long)LONG_MAX + 1ul : (unsigned long)LONG_MAX;
    for (; (digit = p6_digit_value((unsigned char)*p)) < base; ++p) {
        any = 1;
        if (overflow) continue;
        if (value > (limit - (unsigned long)digit) / (unsigned long)base) {
            overflow = 1;
            continue;
        }
        value = value * (unsigned long)base + (unsigned long)digit;
    }
    if (end) *end = (char *)(any ? p : s);
    if (overflow) {
        errno = ERANGE;
        return negative ? LONG_MIN : LONG_MAX;
    }
    return negative ? (long)(0ul - value) : (long)value;
}
/* atol/atoi replacements: decimal, saturating, and errno is preserved. */
static inline long p6_tstol(const char *s)
{
    const int saved = errno;
    const long value = p6_tcstol(s, NULL, 10);
    errno = saved;
    return value;
}
static inline int p6_tstoi(const char *s)
{
    const long value = p6_tstol(s);
    return value > INT_MAX ? INT_MAX : value < INT_MIN ? INT_MIN : (int)value;
}
/* An explicit definition made before this header (a reference build of the legacy
 * body, for instance -D_stprintf=sprintf) wins over these aliases. */
#ifndef _tcstol
#define _tcstol p6_tcstol
#endif
#ifndef _tstol
#define _tstol p6_tstol
#endif
#ifndef _tstoi
#define _tstoi p6_tstoi
#endif

/* Drop an incomplete UTF-8 sequence left at the end of a truncated string. */
static inline void p6_utf8_drop_partial(char *s, size_t length)
{
    size_t start = length;
    unsigned continuation = 0;
    while (start > 0 && continuation < 3 && ((unsigned char)s[start - 1] & 0xc0) == 0x80) {
        --start;
        ++continuation;
    }
    if (start == 0) return;
    const unsigned char lead = (unsigned char)s[start - 1];
    const unsigned need = lead >= 0xf0 ? 3 : lead >= 0xe0 ? 2 : lead >= 0xc0 ? 1 : 0;
    if (need > continuation) s[start - 1] = 0;
}

#ifdef __cplusplus
#if defined(__GNUC__) || defined(__clang__)
#define P6_PRINTF_LIKE(fmt_index, first_index) __attribute__((format(printf, fmt_index, first_index)))
#else
#define P6_PRINTF_LIKE(fmt_index, first_index)
#endif
/* Legacy _stprintf(array, ...) with the capacity taken from the array type, so a
 * pointer destination does not compile and must be adapted at its call site.
 * Output is always terminated. Truncation is not an error: the return value is
 * the length snprintf would have written (>= N means truncated) and a partial
 * trailing UTF-8 sequence is removed. A formatting failure yields "" and a
 * negative result. Callers must not use %f/%g/%e: those follow the C library
 * numeric locale, which this engine never sets and frontends may. */
template <size_t N>
P6_PRINTF_LIKE(2, 3)
inline int p6_stprintf(TCHAR (&dst)[N], const char *format, ...)
{
    static_assert(N > 0, "destination must hold the terminator");
    va_list args;
    va_start(args, format);
    const int length = vsnprintf(dst, N, format, args);
    va_end(args);
    if (length < 0)
        dst[0] = 0;
    else if ((size_t)length >= N)
        p6_utf8_drop_partial(dst, N - 1);
    return length;
}
#ifndef _stprintf
#define _stprintf p6_stprintf
#endif
#endif
#endif
