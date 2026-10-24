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
extern bool samepath(const TCHAR *p1, const TCHAR *p2);    // declared in uae.h, which this probe does not include

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

    // The other way (T29a): UTF-8 to ISO-8859-1 for the Amiga side. What does not fit becomes the default character, a copy is bounded and always ends in NUL.
    char *latin = ua("caf\xc3\xa9 \xe2\x82\xac");           // e acute, then the euro sign (not in ISO-8859-1)
    assert(latin && strcmp(latin, "caf\xe9 ?") == 0 && uaestrlen(latin) == 6);
    free(latin);
    latin = ua_fs("a\xe2\x82\xac" "b", '_');
    assert(latin && strcmp(latin, "a_b") == 0);
    free(latin);
    for (int b = 1; b < 256; ++b) {                     // au() and ua() are inverse on every byte value
        char in[2] = { (char)b, 0 };
        TCHAR *wide_one = au(in);
        char *narrow = ua(wide_one);
        assert(narrow && narrow[0] == (char)b && narrow[1] == 0);
        free(narrow);
        xfree(wide_one);
    }
    char small[4];
    memset(small, 'z', sizeof small);
    assert(ua_copy(small, 4, "abcdef") == small && strcmp(small, "abc") == 0);
    assert(ua_copy(small, 0, "abcdef") == small && small[0] == 'a');   // nothing is written for a capacity of 0
    TCHAR cut[4];
    assert(au_fs_copy(cut, 4, "a\xe9\xe9") == cut && strcmp(cut, "a\xc3\xa9") == 0);   // a two-byte character is never cut in half
    TCHAR *via = au_fs("\xe9");
    assert(via && strcmp(via, "\xc3\xa9") == 0);
    xfree(via);
    assert(samepath("a/b", "a/b") && !samepath("a/b", "a/B"));

    // The counters and the bit test of the trap area (T29a): the new value of an increment or decrement, the old state of the bit.
    volatile uae_atomic counter = 5;
    assert(atomic_inc(&counter) == 6 && counter == 6 && atomic_dec(&counter) == 5 && counter == 5);
    volatile uae_atomic bits = 0x6;
    assert(atomic_bit_test_and_reset(&bits, 1) == 1 && bits == 0x4 && atomic_bit_test_and_reset(&bits, 1) == 0 && bits == 0x4);

    // Interrupt-request bits: zero without producers, and the atomic contract holds on it.
    assert(uae_int_requested == 0);
    assert(atomic_or(&uae_int_requested, 0x2000) == 0 && uae_int_requested == 0x2000);
    assert(atomic_and(&uae_int_requested, ~0x2000u) == 0x2000 && uae_int_requested == 0);
    std::puts("text-services-ok");
    return 0;
}
