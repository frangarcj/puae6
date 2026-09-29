// Exercises the portable text helpers and the UAE interrupt-request word.
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "sysconfig.h"
#include "sysdeps.h"

extern volatile uae_atomic uae_int_requested;

static long fail_at = -1, calls;
extern "C" void *p6_text_test_malloc(size_t n)
{
    if (fail_at >= 0 && calls++ == fail_at) { errno = ENOMEM; return nullptr; }
    return std::malloc(n);
}

// Decode UTF-8 produced by au() back to ISO-8859-1 and require well-formed two-byte forms.
static std::string to_latin1(const char *utf8)
{
    std::string out;
    for (const unsigned char *p = (const unsigned char *)utf8; *p; ++p) {
        if (*p < 0x80) { out += (char)*p; continue; }
        assert((*p & 0xfe) == 0xc2 && (p[1] & 0xc0) == 0x80);   // U+0080..U+00FF only
        out += (char)(((*p & 0x03) << 6) | (p[1] & 0x3f));
        ++p;
    }
    return out;
}

int main()
{
    assert(uaetcslen("") == 0 && uaetcslen("abc") == 3);
    assert(uaetcslen("\xc3\xa9") == 2);                 // TCHAR units are bytes

    char original[] = "Kickstart 1.3";
    TCHAR *copy = my_strdup(original);
    assert(copy && copy != original && strcmp(copy, original) == 0);
    original[0] = 'X';
    assert(strcmp(copy, "Kickstart 1.3") == 0);
    xfree(copy);
    TCHAR *empty = my_strdup("");
    assert(empty && empty[0] == 0);
    xfree(empty);

    TCHAR *ascii = au("Workbench");
    assert(ascii && strcmp(ascii, "Workbench") == 0);
    xfree(ascii);
    TCHAR *mixed = au("caf\xe9 \xff\x80");
    assert(mixed && strcmp(mixed, "caf\xc3\xa9 \xc3\xbf\xc2\x80") == 0);
    xfree(mixed);
    for (int b = 1; b < 256; ++b) {                     // every single byte value
        char in[3] = { 'a', (char)b, 0 };
        TCHAR *out = au(in);
        assert(out && to_latin1(out) == std::string(in));
        assert(strlen(out) == (b < 0x80 ? 2u : 3u));
        xfree(out);
    }
    std::string big(100000, '\xa9');
    TCHAR *wide = au(big.c_str());
    assert(wide && strlen(wide) == 200000 && to_latin1(wide) == big);
    xfree(wide);

    // Allocation failure is a NULL result, never a partial string, and recovers.
    fail_at = 0; calls = 0;
    assert(my_strdup("x") == nullptr && errno == ENOMEM);
    fail_at = 0; calls = 0;
    errno = 0;
    assert(au("x") == nullptr && errno == ENOMEM);
    fail_at = -1;
    TCHAR *again = au("ok");
    assert(again && strcmp(again, "ok") == 0);
    xfree(again);

    // Interrupt-request bits: zero without producers, and the atomic contract holds on it.
    assert(uae_int_requested == 0);
    assert(atomic_or(&uae_int_requested, 0x2000) == 0 && uae_int_requested == 0x2000);
    assert(atomic_and(&uae_int_requested, ~0x2000u) == 0x2000 && uae_int_requested == 0);
    std::puts("text-services-ok");
    return 0;
}
