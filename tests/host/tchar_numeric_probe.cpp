// Exercises the locale-free integer parsing and array-bounded formatting of tchar.h.
#include <cassert>
#include <cerrno>
#include <climits>
#include <clocale>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "tchar.h"

static void expect_long(const char *text, int base, long value, int consumed, int error = 0)
{
    char *end = nullptr;
    errno = 0;
    const long got = p6_tcstol(text, &end, base);
    assert(got == value);
    assert(end == text + consumed);
    assert(errno == error);
}

static void parse_cases()
{
    expect_long("123", 10, 123, 3);
    expect_long(" \t\n\v\f\r42x", 10, 42, 8);
    expect_long("-17", 10, -17, 3);
    expect_long("+5", 10, 5, 2);
    expect_long("- 5", 10, 0, 0);              // sign without digits: nothing consumed
    expect_long("", 10, 0, 0);
    expect_long("abc", 10, 0, 0);
    expect_long("ff", 16, 255, 2);
    expect_long("0xFF", 16, 255, 4);
    expect_long("0x", 16, 0, 1);               // "0" is a number, "x" is not part of it
    expect_long("0xg", 16, 0, 1);
    expect_long("-0x10", 16, -16, 5);
    expect_long("0x1F", 0, 31, 4);
    expect_long("017", 0, 15, 3);
    expect_long("08", 0, 0, 1);                // octal stops at the 8
    expect_long("0", 0, 0, 1);
    expect_long("12", 0, 12, 2);
    expect_long("101", 2, 5, 3);
    expect_long("zz", 36, 1295, 2);
    expect_long("Zz", 36, 1295, 2);
    expect_long("12", 2, 1, 1);
    expect_long("\xc3\xa9" "12", 10, 0, 0);    // non-ASCII bytes are never digits or space
    expect_long("\xa0" "12", 10, 0, 0);
    expect_long("7\xc3\xa9", 10, 7, 1);
    // Range: saturating with ERANGE, every digit consumed.
    expect_long("9223372036854775807", 10, LONG_MAX, 19);
    expect_long("9223372036854775808", 10, LONG_MAX, 19, ERANGE);
    expect_long("-9223372036854775808", 10, LONG_MIN, 20);
    expect_long("-9223372036854775809", 10, LONG_MIN, 20, ERANGE);
    const std::string huge(100, '9');
    expect_long(huge.c_str(), 10, LONG_MAX, 100, ERANGE);
    expect_long((huge + "x").c_str(), 10, LONG_MAX, 100, ERANGE);
    expect_long("7fffffffffffffff", 16, LONG_MAX, 16);
    expect_long("8000000000000000", 16, LONG_MAX, 16, ERANGE);
    // Invalid bases report EINVAL and consume nothing.
    for (int base : {-1, 1, 37, 100}) expect_long("10", base, 0, 0, EINVAL);
    // A null end pointer is allowed and errno is left alone on success.
    errno = 12345;
    assert(p6_tcstol("77", nullptr, 10) == 77 && errno == 12345);
}

static void wrappers()
{
    errno = 99;
    assert(p6_tstol("-42") == -42 && errno == 99);
    assert(p6_tstol("9223372036854775808") == LONG_MAX && errno == 99);
    assert(p6_tstol("nothing") == 0 && errno == 99);
    assert(p6_tstoi("2147483647") == INT_MAX && p6_tstoi("2147483648") == INT_MAX);
    assert(p6_tstoi("-2147483648") == INT_MIN && p6_tstoi("-2147483649") == INT_MIN);
    assert(p6_tstoi("  12abc") == 12 && errno == 99);
    assert(_tstol("5") == 5 && _tstoi("6") == 6 && _tcstol("7", nullptr, 10) == 7);
}

// Same input, same result as the C library in the C locale, for a fixed pseudo-random corpus.
static void differential()
{
    assert(std::setlocale(LC_ALL, "C") != nullptr);
    static const char alphabet[] = "0123456789abcdefgxXzZ+- \t\v9";
    uint32_t state = 0x12345678u;
    auto next = [&]() { state = state * 1664525u + 1013904223u; return state >> 8; };
    const int bases[] = {0, 2, 8, 10, 16, 36};
    for (int i = 0; i < 40000; ++i) {
        char text[24];
        const unsigned length = next() % 22;
        for (unsigned k = 0; k < length; ++k) text[k] = alphabet[next() % (sizeof alphabet - 1)];
        text[length] = 0;
        const int base = bases[next() % 6];
        char *ours_end, *libc_end;
        errno = 0; const long ours = p6_tcstol(text, &ours_end, base); const int ours_errno = errno;
        errno = 0; const long libc = std::strtol(text, &libc_end, base); const int libc_errno = errno;
        assert(ours == libc && ours_end == libc_end);
        assert((ours_errno == ERANGE) == (libc_errno == ERANGE));
    }
}

template <size_t N>
static int fmt_fill(TCHAR (&out)[N], const char *value)
{
    std::memset(out, 0x7e, N);
    return _stprintf(out, "%s", value);
}

static void formatting()
{
    struct Frame { unsigned char pre[8]; TCHAR text[8]; unsigned char post[8]; } f;
    std::memset(&f, 0xa5, sizeof f);
    assert(_stprintf(f.text, "n=%d", 42) == 4 && std::strcmp(f.text, "n=42") == 0);
    assert(_stprintf(f.text, "%s", "1234567") == 7 && std::strcmp(f.text, "1234567") == 0);  // exact fit
    assert(_stprintf(f.text, "%s", "12345678") == 8 && std::strcmp(f.text, "1234567") == 0);  // truncated
    assert(_stprintf(f.text, "%s", "") == 0 && f.text[0] == 0);
    for (unsigned char b : f.pre) assert(b == 0xa5);
    for (unsigned char b : f.post) assert(b == 0xa5);

    TCHAR one[1];
    assert(fmt_fill(one, "abc") == 3 && one[0] == 0);
    TCHAR two[2];
    assert(fmt_fill(two, "abc") == 3 && std::strcmp(two, "a") == 0);

    // UTF-8: a, e-acute (2 bytes), euro (3 bytes) = 6 bytes; a partial tail is never left behind.
    const char *mixed = "a\xc3\xa9\xe2\x82\xac";
    TCHAR n7[7], n6[6], n5[5], n4[4], n3[3], n2[2];
    assert(fmt_fill(n7, mixed) == 6 && std::strcmp(n7, mixed) == 0);
    assert(fmt_fill(n6, mixed) == 6 && std::strcmp(n6, "a\xc3\xa9") == 0);   // 5 bytes fit; euro cut
    assert(fmt_fill(n5, mixed) == 6 && std::strcmp(n5, "a\xc3\xa9") == 0);
    assert(fmt_fill(n4, mixed) == 6 && std::strcmp(n4, "a\xc3\xa9") == 0);   // 3 bytes: complete e-acute
    assert(fmt_fill(n3, mixed) == 6 && std::strcmp(n3, "a") == 0);           // 2 bytes: lone lead dropped
    assert(fmt_fill(n2, mixed) == 6 && std::strcmp(n2, "a") == 0);
    // 4-byte sequence: game controller, U+1F3AE.
    const char *pad = "\xf0\x9f\x8e\xae";
    TCHAR p5[5], p4[4], p3[3], p2[2];
    assert(fmt_fill(p5, pad) == 4 && std::strcmp(p5, pad) == 0);
    assert(fmt_fill(p4, pad) == 4 && p4[0] == 0);
    assert(fmt_fill(p3, pad) == 4 && p3[0] == 0);
    assert(fmt_fill(p2, pad) == 4 && p2[0] == 0);
    // ASCII truncation is left alone, and stray continuation bytes are not "repaired".
    TCHAR c4[4];
    assert(fmt_fill(c4, "abcdef") == 6 && std::strcmp(c4, "abc") == 0);
    assert(fmt_fill(c4, "\x80\x80\x80\x80") == 4 && (unsigned char)c4[0] == 0x80);
}

int main()
{
    parse_cases();
    wrappers();
    differential();
    formatting();
    std::puts("tchar-numeric-ok");
    return 0;
}
