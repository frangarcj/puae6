"""Real CPU IRQ functions, isolated from the still-incomplete CPU dispatch port."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CPUIRQTests(unittest.TestCase):
    def test_owner_irq_and_atomic_flags(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        common = ["clang++", "-std=c++17", "-UNDEBUG", "-I" + str(ROOT / "ports/portable"),
                  "-I" + str(ROOT / "build/generated"), "-I" + str(tree / "include"), "-I" + str(tree)]
        source = (tree / "newcpu.cpp").read_text()
        begin, end = "static void int_request_do(bool i6)", "int cpu_sleep_millis(int ms)"
        self.assertEqual(source.count(begin), 1)
        self.assertEqual(source.count(end), 1)
        irq = begin + source.split(begin)[1].split(end)[0]
        globals_ = [line for line in source.splitlines()
                    if line.startswith("static volatile uae_atomic uae_interrupt")]
        self.assertEqual(len(globals_), 3)
        with tempfile.TemporaryDirectory(prefix="p6-cpu-irq-") as tmp:
            tmp = Path(tmp)
            (tmp / "cpu_irq.inc").write_text("\n".join(globals_) + "\n" + irq)
            # Preprocess the WHOLE unit, not only the extracted test functions.
            deps = subprocess.run([*common, "-M", str(tree / "newcpu.cpp")],
                                  text=True, capture_output=True, check=True).stdout
            for forbidden in ("statusline.h", "threaddep/thread.h", "windows.h"):
                self.assertNotIn(forbidden, deps)
            preprocessed = subprocess.run([*common, "-E", "-P", str(tree / "newcpu.cpp")],
                                          text=True, capture_output=True, check=True).stdout
            body = preprocessed.split("void safe_interrupt_set(int num, int id, bool i6)")[1].split(end)[0]
            self.assertNotIn("is_mainthread", body)
            self.assertNotIn("atomic_or", body)
            obj = tmp / "atomic.o"
            provider = ROOT / "ports/portable/atomic.cpp"
            subprocess.run([*common, "-O0", "-c", str(provider), "-o", str(obj)], check=True)
            imports = subprocess.run(["nm", "-u", str(obj)], text=True, capture_output=True, check=True).stdout
            self.assertFalse(imports.strip(), imports)  # No libatomic, mutex or host runtime.
            for optimization in ("-O0", "-O2"):
                exe = tmp / "probe"
                subprocess.run([*common, optimization, "-I" + str(tmp), "-Werror=format",
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                str(ROOT / "tests/host/cpu_irq_probe.cpp"), str(provider), "-o", str(exe)],
                               check=True, timeout=60)
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
