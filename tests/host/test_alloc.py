"""Actual patched macros plus libc failures; bank expression probe is not an engine run."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize

PRELUDE = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
#include <assert.h>
#ifdef TEST_ALLOCATOR
/* Fault-injection backend only in this translation unit, never production. */
static size_t calls, requested;
static void *fail_malloc(size_t n) {
 ++calls; requested=n; errno=ENOMEM; return NULL;
}
static void *fail_calloc(size_t n, size_t s) {
 assert(!s || n <= SIZE_MAX/s); return fail_malloc(n*s);
}
static void *fail_realloc(void *p, size_t n) { (void)p; return fail_malloc(n); }
#define malloc fail_malloc
#define calloc fail_calloc
#define realloc fail_realloc
#endif
#include "sysdeps.h"
'''

REAL = r'''
int main(void) {
 size_t count=8;
 uint32_t *p=xcalloc(uint32_t, count++);
 assert(p && count==9);
 for (size_t i=0;i<8;++i) assert(p[i]==0);
 assert((uintptr_t)p % _Alignof(max_align_t)==0);
 for (size_t i=0;i<8;++i) p[i]=(uint32_t)i+17;
 uint32_t *q=xrealloc(uint32_t,p,16);
 assert(q);
 for (size_t i=0;i<8;++i) assert(q[i]==i+17);
 p=q;
 volatile size_t huge=SIZE_MAX;
 errno=0;
 assert(xmalloc(uint32_t,huge/4+1)==NULL && errno==ENOMEM);
 errno=0;
 assert(xcalloc(uint32_t,huge/4+1)==NULL && errno==ENOMEM);
 errno=0;
 assert(xrealloc(uint32_t,p,huge/4+1)==NULL && errno==ENOMEM);
 for (size_t i=0;i<8;++i) assert(p[i]==i+17);
 /* Escape results through volatile storage: Clang may elide an allocation
  * whose result is used only in a null comparison. This probe must call libc. */
 void *volatile failed;
 errno=0;
 failed=xmalloc(unsigned char,huge);
 assert(failed==NULL && errno==ENOMEM);
 errno=0;
 failed=xcalloc(unsigned char,huge);
 assert(failed==NULL && errno==ENOMEM);
 errno=0;
 failed=xrealloc(unsigned char,p,huge);
 assert(failed==NULL && errno==ENOMEM);
 for (size_t i=0;i<8;++i) assert(p[i]==i+17);
 xfree(p);
 /* Both NULL and freeable unique storage are permitted for zero sizes. */
 xfree(xmalloc(char,0)); xfree(xcalloc(char,0));
 xfree(xrealloc(char,NULL,0)); xfree(NULL);
 long double *aligned=xmalloc(long double,2);
 assert(aligned && (uintptr_t)aligned % _Alignof(long double)==0);
 aligned[1]=1.25L; assert(aligned[1]==1.25L); xfree(aligned);
 return 0;
}
'''

INJECTED = r'''
static void *bank_request(uae_u32 reserved_size) {
 struct { uae_u32 reserved_size; } bank={reserved_size}, *ab=&bank;
 return BANK_EXPRESSION;
}
int main(void) {
 unsigned char live=77; size_t n=3, i=0; void *ptrs[]={&live};
 errno=0;
 assert(xmalloc(uint32_t,SIZE_MAX/4+1)==NULL && errno==ENOMEM && calls==0);
 errno=0;
 assert(xcalloc(uint32_t,SIZE_MAX/4+1)==NULL && errno==ENOMEM && calls==0);
 errno=0;
 assert(xrealloc(uint32_t,&live,SIZE_MAX/4+1)==NULL && errno==ENOMEM && calls==0);
 /* Exact boundary is representable, so must reach the allocator. */
 assert(xmalloc(uint32_t,SIZE_MAX/4)==NULL && calls==1);
 assert(requested==(SIZE_MAX/4)*4);
 assert(xcalloc(uint32_t,SIZE_MAX/4)==NULL && calls==2);
 assert(requested==(SIZE_MAX/4)*4);
 assert(xrealloc(uint32_t,ptrs[i++],n++)==NULL && calls==3);
 assert(i==1 && n==4 && requested==12 && live==77);
 assert(xmalloc(uint32_t,n++)==NULL && calls==4 && n==5 && requested==16);
 assert(xcalloc(uint32_t,n++)==NULL && calls==5 && n==6 && requested==20);
 assert(xmalloc(char,0)==NULL && calls==6 && requested==0);
 assert(xcalloc(char,0)==NULL && calls==7 && requested==0);
 assert(xrealloc(char,&live,0)==NULL && calls==8 && requested==0);
 assert(bank_request(0)==NULL && calls==9 && requested==4);
 assert(bank_request(512*1024)==NULL && calls==10 && requested==512*1024+4);
 for (uae_u32 j=0;j<4;++j) {
  assert(bank_request(UINT32_MAX-j)==NULL && calls==11+j);
  assert(requested==(size_t)UINT32_MAX-j+4);
 }
 assert(xrealloc(uint32_t,&live,SIZE_MAX/4)==NULL && calls==15);
 assert(requested==(SIZE_MAX/4)*4 && live==77);
 return 0;
}
'''


class AllocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        _, cls.tree = materialize(ROOT, "build/t05-sysdeps/" + key)
        body = (cls.tree / "memory.cpp").read_text().split("#ifndef NATMEM_OFFSET", 1)[1]
        match = re.search(r"ab->baseaddr = (xcalloc \(uae_u8, \(size_t\)ab->reserved_size \+ 4\));", body)
        if not match:
            raise AssertionError("Missing checked portable bank allocation expression")
        cls.bank_expression = match[1]

    def run_probe(self, injected):
        with tempfile.TemporaryDirectory(prefix="p6-alloc-") as directory:
            for lang, standard in (("c", "c11"), ("c++", "c++17")):
                with self.subTest(lang=lang):
                    output = Path(directory) / "probe"
                    source = PRELUDE + (INJECTED.replace("BANK_EXPRESSION", self.bank_expression)
                                        if injected else REAL)
                    if lang == "c++":
                        source = source.replace("_Alignof", "alignof")
                    command = ["clang++" if lang == "c++" else "clang", "-x", lang,
                               "-std="+standard, "-O2", "-Wall", "-Wextra", "-Werror",
                               "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                               "-I", str(ROOT / "ports/portable"), "-I", str(self.tree / "include")]
                    if injected:
                        command += ["-DTEST_ALLOCATOR"]
                    result = subprocess.run(command+["-o", str(output), "-"], input=source,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    env = dict(os.environ, ASAN_OPTIONS="allocator_may_return_null=1:detect_leaks=0")
                    result = subprocess.run([str(output)], env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_libc_ownership_and_failure(self):
        self.run_probe(False)

    def test_preallocation_bounds_and_single_evaluation(self):
        self.run_probe(True)


if __name__ == "__main__":
    unittest.main()
