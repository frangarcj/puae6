"""Compare the complete runtime opcode parser with the pinned HOST parser."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated

ROOT = Path(__file__).resolve().parents[2]


class ReadCpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify the entire recipe, patch series and generated input identity,
        # and support standalone execution from a tree without build caches.
        ensure_generated()
        cls.tree = ROOT / "build/upstream"
        cls.cpudefs = ROOT / "build/generated/cpudefs.cpp"

    def test_full_runtime_table_matches_host_parser(self):
        probe = ROOT / "tests/host/readcpu_probe.cpp"
        results = {}
        with tempfile.TemporaryDirectory(prefix="p6-readcpu-") as temporary:
            for optimization in ("-O0", "-O2"):
                for profile in ("host", "portable"):
                    with self.subTest(optimization=optimization, profile=profile):
                        executable = Path(temporary) / f"readcpu-{profile}-{optimization[2:]}"
                        if profile == "portable":
                            include_dirs = [ROOT / "ports/portable", ROOT / "build/generated",
                                            self.tree / "include", self.tree]
                        else:
                            include_dirs = [ROOT / "tools/codegen_host", ROOT / "build/generated",
                                            self.tree / "include", self.tree]
                        command = ["clang++", "-std=c++17", optimization, "-UNDEBUG",
                                   "-Wall", "-Wextra", "-Werror=format",
                                   "-fno-fast-math", "-fsanitize=address,undefined",
                                   "-fno-sanitize-recover=all"]
                        command.extend("-I" + str(path) for path in include_dirs)
                        # Link the complete parser and the exact generated descriptor table.
                        command.extend([str(probe), str(self.tree / "readcpu.cpp"),
                                        str(self.cpudefs), "-o", str(executable)])
                        environment = os.environ.copy()
                        environment.update(LC_ALL="C", TZ="UTC")
                        compiled = subprocess.run(command, timeout=60, env=environment,
                                                  capture_output=True, text=True)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        run = subprocess.run([str(executable)], timeout=60,
                                             env=environment, capture_output=True)
                        self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
                        rows = run.stdout.splitlines()
                        self.assertEqual(len(rows), 65537)  # metadata plus every opcode
                        for opcode, row in enumerate(rows[1:]):
                            fields = row.split(b",")
                            self.assertEqual(int(fields[0], 16), opcode)
                            self.assertEqual(len(fields), 3 if fields[1] == b"0" else 27)
                        results[(profile, optimization)] = run.stdout

        self.assertEqual(results[("portable", "-O0")], results[("host", "-O0")])
        self.assertEqual(results[("portable", "-O2")], results[("host", "-O2")])
        self.assertEqual(results[("portable", "-O0")], results[("portable", "-O2")])
        self.assertEqual(results[("host", "-O0")], results[("host", "-O2")])


if __name__ == "__main__":
    unittest.main()
