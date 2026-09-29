import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TcharTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="p6-text-")
        directory = Path(cls.temporary.name)
        cls.source = '#include "tchar.h"\nint copy_text(char*d, size_t c, const char*s, size_t n) { return p6_tchar_copy(d,c,s,n); }\n'
        path = directory / "text.dylib"
        subprocess.run(["clang", "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-shared", "-fPIC", "-I", str(ROOT / "ports/portable"), "-o", str(path), "-"],
                       input=cls.source, text=True, capture_output=True, check=True)
        cls.library = ctypes.CDLL(str(path))
        cls.copy = cls.library.copy_text
        cls.copy.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        cls.copy.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def check(self, raw, expected):
        output = ctypes.create_string_buffer(b"!" * (len(raw) + 4))
        before = output.raw
        result = self.copy(ctypes.addressof(output) + 1, len(raw) + 1, raw, len(raw))
        self.assertEqual(result, expected, raw)
        if expected:
            self.assertEqual(output.raw, before)
        else:
            self.assertEqual(output.raw, b"!" + raw + b"\0" + before[len(raw) + 2:])

    def test_valid_unicode_and_boundaries(self):
        for point in (1, 0x7f, 0x80, 0x7ff, 0x800, 0xd7ff, 0xe000, 0xffff, 0x10000, 0x10ffff):
            self.check(chr(point).encode("utf-8"), 0)
        self.check("Amiga / español / 日本語 / 🎮".encode(), 0)
        self.check(b"", 0)

    def test_malformed_utf8_is_transactional(self):
        for raw in (b"\0", b"a\0b", b"\x80", b"\xc0\x80", b"\xc1\xbf", b"\xc2",
                    b"\xe0\x80\x80", b"\xed\xa0\x80", b"\xef\xbf", b"\xf0\x80\x80\x80",
                    b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80", b"\xff", b"\xe2A\x80"):
            self.check(raw, -3)

    def test_capacity_null_overflow_and_overlap(self):
        buffer = ctypes.create_string_buffer(b"unchanged")
        old = buffer.raw
        for size in (0, 1, 3):
            self.assertEqual(self.copy(buffer, size, b"abcd", 4), -2)
            self.assertEqual(buffer.raw, old)
        self.assertEqual(self.copy(buffer, len(old), b"a", ctypes.c_size_t(-1).value), -2)
        self.assertEqual(self.copy(None, 10, b"a", 1), -1)
        self.assertEqual(self.copy(buffer, len(old), None, 1), -1)
        self.assertEqual(buffer.raw, old)
        self.assertEqual(self.copy(ctypes.addressof(buffer)+1, 9, buffer, 8), 0)
        self.assertEqual(buffer.raw, b"uunchange\0") # 1 preserved + 8 copied + terminator

    def test_rejects_wide_tchar_build(self):
        for macro in ("UNICODE", "_UNICODE"):
            result = subprocess.run(["clang", "-x", "c", "-fsyntax-only", "-D"+macro,
                                     "-I", str(ROOT/"ports/portable"), "-"],
                                    input=self.source, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires narrow UTF-8", result.stderr)

    def test_c_cpp_sanitizers_and_locale_independence(self):
        probe = '''#include "tchar.h"
#include <assert.h>
#include <locale.h>
int main(void) {
 const TCHAR *s = _T("A\\xc3\\xb1"); char out[4];
 for (int i=0; i<2; ++i) {
  setlocale(LC_ALL, i ? "" : "C");
  assert(p6_tchar_copy(out, sizeof out, s, 3)==0);
  assert(memcmp(s,out,4)==0);
  assert(p6_tchar_copy(out, sizeof out, "\\xf0", 1)==P6_TEXT_UTF8);
  /* Test the full signed/unsigned byte domain plus EOF. No ctype/locale UB. */
  for (int c=-128; c<256; ++c) {
   int expected = c==9 || c==10 || c==11 || c==12 || c==13 || c==32;
   assert(!!_istspace(c)==expected);
   assert(_totupper(c)==(c>=97 && c<=122 ? c-32 : c));
   assert(_totlower(c)==(c>=65 && c<=90 ? c+32 : c));
   assert(!!_istdigit(c)==(c>=48 && c<=57));
   assert(!!_istxdigit(c)==((c>=48 && c<=57)||(c>=65 && c<=70)||(c>=97 && c<=102)));
  }
  int c=9; assert(_istspace(c++) && c==10);
  c='a'; assert(_totupper(c++)=='A' && c=='b');
  c='A'; assert(_totlower(c++)=='a' && c=='B');
  c='9'; assert(_istdigit(c++) && c==':');
  c='F'; assert(_istxdigit(c++) && c=='G');
  assert(_tcscmp(s,out)==0);
  assert(_tcscmp("MOVE","MOVEQ")<0 && _tcscmp("MOVEQ","MOVE")>0);
  assert(_tcsncmp("Dreg,", "Dreg]", 4)==0);
  assert(_tcsncmp("Dreg,", "Dreg]", 5)<0);
  assert(_tcsncmp("x", "y", 0)==0);
  assert(_tcscmp("\\xff", "\\x7f")>0);
  for (int a=0; a<256; ++a) for (int b=0; b<256; ++b) {
   char x[2]={(char)a,0}, y[2]={(char)b,0};
   int ax=a>=65 && a<=90 ? a+32 : a, by=b>=65 && b<=90 ? b+32 : b;
   int got=_tcsicmp(x,y), want=ax-by;
   assert((got>0)-(got<0)==(want>0)-(want<0));
   assert(_tcsnicmp(x,y,1)==got && _tcsnicmp(x,y,0)==0);
  }
  assert(_tcsicmp("Kick.ROM", "kICK.rom")==0);
  assert(_tcsicmp("A\\xc3\\xb1", "a\\xc3\\xb1")==0);
  assert(_tcsicmp("\\xc3\\x91", "\\xc3\\xb1")!=0); /* No Unicode case folding. */
  assert(_tcsicmp("A", "aa")<0 && _tcsicmp("aa", "A")>0);
  assert(_tcsnicmp("AbX", "aBY", 2)==0 && _tcsnicmp("AbX", "aBY", 3)<0);
  assert(_tcsnicmp("x", "X", (size_t)-1)==0); /* Stop at NUL, not at n. */
  char one_a='A', one_b='a'; assert(_tcsnicmp(&one_a,&one_b,1)==0);
  char buf[12]; memset(buf,'!',sizeof buf);
  assert(_tcscpy(buf+1,s)==buf+1 && memcmp(buf,"!A\\xc3\\xb1\\0!",6)==0);
  assert(_tcslen(buf+1)==3 && _tcscat(buf+1,"xy")==buf+1);
  assert(memcmp(buf,"!A\\xc3\\xb1xy\\0!",8)==0);
  for (size_t n=0; n<=5; ++n) {
   memset(buf,'!',sizeof buf);
   assert(_tcsncpy(buf+1,"abc",n)==buf+1);
   for (size_t j=0; j<n; ++j) assert(buf[j+1]==(j<3 ? "abc"[j] : 0));
   assert(buf[0]=='!' && buf[n+1]=='!');
  }
  const char *p="abca";
  assert(_tcschr(p,'a')==p && _tcsrchr(p,'a')==p+3);
  assert(_tcschr(p,0)==p+4 && _tcsrchr(p,0)==p+4);
  assert(!_tcschr(p,'z') && !_tcsrchr(p,'z'));
  assert(_tcsstr(p,"")==p && _tcsstr(p,"bc")==p+1 && !_tcsstr(p,"z"));
  assert(_tcscspn(p,"cb")==1 && _tcscspn(p,"")==4 && _tcscspn("",p)==0);
  /* C++ overloads must retain mutability for writable inputs. */
  char *q=_tcschr(buf,'!'); assert(q==buf);
  q=_tcsrchr(buf,0); assert(q==buf+4);
  q=_tcsstr(buf,"abc"); assert(q==buf+1);
 }
 return 0;
}'''
        for language, standard, optimization in (
                (language, standard, optimization)
                for language, standard in (("c", "c11"), ("c++", "c++17"))
                for optimization in ("-O0", "-O2")):
            binary = Path(self.temporary.name) / "probe"
            subprocess.run(["clang", "-x", language, "-std="+standard, optimization,
                            "-I", str(ROOT/"ports/portable"),
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-o", str(binary), "-"],
                           input=probe, text=True, capture_output=True, check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
