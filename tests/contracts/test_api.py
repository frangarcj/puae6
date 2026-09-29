"""HARNESS only: public C declarations/layout/linkage, without an emulator."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NativeContractTests(unittest.TestCase):
    def test_c_and_cpp_layout_and_c_linkage(self):
        cc, cxx = shutil.which('clang'), shutil.which('clang++')
        self.assertIsNotNone(cc, 'HOST C compiler required, not a skipped PASS')
        self.assertIsNotNone(cxx, 'HOST C++ compiler required, not a skipped PASS')
        with tempfile.TemporaryDirectory(prefix='p6-api-') as tmp:
            work = Path(tmp)
            def command(args, success=True):
                result = subprocess.run(list(map(str, args)), capture_output=True, text=True)
                if success:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0, 'incompatible prototype accepted')
                return result
            common = ['-Wall', '-Wextra', '-Werror', '-pedantic', '-I' + str(ROOT / 'bridge/include')]
            probe = ROOT / 'tests/contracts/layout_probe.c'
            for compiler, language, standard in [(cc, 'c', 'c11'), (cxx, 'c++', 'c++17')]:
                binary = work / language.replace('+', 'p')
                command([compiler, '-x', language, '-std=' + standard, *common, probe, '-o', binary])
                command([binary])
                invalid = work / 'invalid.c'
                invalid.write_text('#include "p6.h"\nint main(void) {\n'
                                   'p6_result (*wrong)(void) = p6_run_frame; (void)wrong; return 0; }\n')
                command([compiler, '-x', language, '-std=' + standard, *common,
                         '-fsyntax-only', invalid], success=False)
            # A single query symbol is a C/C++ linkage probe, not a core implementation.
            (work / 'query.c').write_text('#include "p6.h"\n'
                'uint32_t p6_abi_version(void) { return P6_ABI_VERSION; }\n')
            (work / 'caller.cpp').write_text('#include "p6.h"\n'
                'int main() { return p6_abi_version() != P6_ABI_VERSION; }\n')
            command([cc, '-std=c11', *common, '-c', work / 'query.c', '-o', work / 'query.o'])
            command([cxx, '-std=c++17', *common, work / 'caller.cpp', work / 'query.o',
                     '-o', work / 'linkage'])
            command([work / 'linkage'])
