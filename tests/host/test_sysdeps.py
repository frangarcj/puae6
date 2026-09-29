"""Exercise the real patched sysdeps header and portable include boundary."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from materialize_upstream import materialize

PROBE = '''#include "sysdeps.h"
#include "machdep/maccess.h"
#ifdef __cplusplus
#define CHECK static_assert
#else
#define CHECK _Static_assert
#endif
CHECK(sizeof(uae_u8)==1 && sizeof(uae_u16)==2 && sizeof(uae_u32)==4 && sizeof(uae_u64)==8, "UAE widths");
CHECK(sizeof(uae_s8)==1 && sizeof(uae_s16)==2 && sizeof(uae_s32)==4 && sizeof(uae_s64)==8, "signed widths");
CHECK((uae_s32)-1 < 0 && (uae_u32)-1 > 0, "signedness");
CHECK(sizeof(uaecptr)==4 && sizeof(uae_atomic)==4 && sizeof(TCHAR)==1, "engine ABI");
CHECK(CPU_64_BIT == 1 && CPUEMU_13 == 1, "profile");
#if defined(__aarch64__) && !defined(CPU_arm)
#error AArch64 detection failed
#endif
#if defined(WINDOWS) || defined(JIT) || defined(NATMEM_OFFSET) || defined(SUPPORT_THREADS)
#error unexpected host feature
#endif
int main(void) {
 TCHAR name[16]; unsigned char bytes[9] = {0};
 assert(p6_tchar_copy(name,sizeof name,TARGET_NAME,sizeof(TARGET_NAME)-1)==P6_TEXT_OK);
 assert(strcmp(name,"puae6")==0);
 do_put_mem_quad(bytes+1, UVAL64(0x0123456789abcdef));
 assert(do_get_mem_quad(bytes+1)==UVAL64(0x0123456789abcdef));
 return 0;
}'''


class SysdepsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Content-verified output is separate from the previous generation tree.
        key = hashlib.sha256((ROOT / "patches/winuae/series.json").read_bytes()).hexdigest()
        cls.receipt, cls.tree = materialize(ROOT, "build/t05-sysdeps/" + key)

    def test_real_headers_c_cpp_and_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="p6-sysdeps-") as directory:
            for language, standard in (("c", "c11"), ("c++", "c++17")):
                with self.subTest(language=language):
                    output = Path(directory) / "probe"
                    deps = Path(directory) / "probe.d"
                    command = ["clang", "-x", language, "-std="+standard, "-Wall", "-Wextra", "-Werror",
                               "-I", str(ROOT/"ports/portable"), "-I", str(self.tree/"include"),
                               "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                               "-MMD", "-MF", str(deps), "-o", str(output), "-"]
                    if language == "c++": command[0] = "clang++"
                    result = subprocess.run(command, input=PROBE, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    subprocess.run([str(output)], check=True)
                    content = deps.read_text()
                    for header in ("sysconfig.h", "tchar.h", "target.h", "machdep/maccess.h"):
                        self.assertIn(str(ROOT/"ports/portable"/header), content)
                    self.assertIn(str(self.tree/"include/sysdeps.h"), content)
                    self.assertNotIn("od-win32", content)


if __name__ == "__main__":
    unittest.main()
