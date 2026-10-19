"""The patched gencpu emits only the requested CPU models and is otherwise upstream's tool."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROFILE_MODELS = ("0", "1", "2", "3", "4", "5", "11", "12", "13", "14", "21")
PROFILE_OUTPUTS = {"cputbl.h", "cpustbl.cpp", "cpuemu_0.cpp", "cpuemu_11.cpp", "cpuemu_13.cpp", "cpuemu_21.cpp"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(shutil.which("clang++"), "clang++ unavailable")
class GencpuModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=300)
        cls.tmp = tempfile.TemporaryDirectory(prefix="p6-gencpu-")
        cls.work = Path(cls.tmp.name)
        recipe = json.loads((ROOT / "manifests/codegen.json").read_text())
        cls.flags = recipe["flags"]["c++"]
        cls.env = {**os.environ, **recipe["environment"]}
        cls.patched = cls.build("patched", ROOT / "build/upstream")
        cls.pristine = cls.build("pristine", ROOT / "upstream/winuae")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build(cls, label, tree):
        objects = []
        sources = [tree / "gencpu.cpp", tree / "readcpu.cpp", ROOT / "build/generated/cpudefs.cpp"]
        for index, source in enumerate(sources):
            obj = cls.work / f"{label}-{index}.o"
            subprocess.run(["clang++", *cls.flags, "-I" + str(ROOT / "tools/codegen_host"),
                            "-I" + str(tree / "include"), "-x", "c++", "-c", str(source),
                            "-o", str(obj)], check=True, env=cls.env, timeout=300,
                           capture_output=True)
            objects.append(obj)
        executable = cls.work / f"gencpu-{label}"
        subprocess.run(["clang++", *map(str, objects), "-o", str(executable)], check=True,
                       env=cls.env, timeout=120, capture_output=True)
        return executable

    def run_tool(self, executable, *args):
        out = Path(tempfile.mkdtemp(prefix="out-", dir=self.work))
        result = subprocess.run([str(executable), *args], cwd=out, env=self.env, timeout=600,
                                capture_output=True, text=True)
        return result, out

    def test_without_arguments_it_is_upstream(self):
        patched, patched_out = self.run_tool(self.patched)
        pristine, pristine_out = self.run_tool(self.pristine)
        self.assertEqual((patched.returncode, pristine.returncode), (0, 0))
        names = sorted(p.name for p in pristine_out.iterdir())
        self.assertEqual(sorted(p.name for p in patched_out.iterdir()), names)
        self.assertGreater(len(names), 15)
        for name in names:
            with self.subTest(file=name):
                # T53: the returning loop table of compatible 68010 handlers has its own type;
                # replacing that symbol recovers upstream byte for byte, including every CE handler unchanged.
                actual = (patched_out / name).read_bytes()
                if name == "cpuemu_11.cpp":
                    actual = actual.replace(b"loop_mode_table_compatible", b"loop_mode_table")
                self.assertEqual(actual, (pristine_out / name).read_bytes())

    def test_profile_models_match_the_full_generation_and_the_build_tree(self):
        restricted, restricted_out = self.run_tool(self.patched, *PROFILE_MODELS)
        self.assertEqual(restricted.returncode, 0, restricted.stderr)
        self.assertEqual({p.name for p in restricted_out.iterdir()}, PROFILE_OUTPUTS)
        # The generated tree the engine compiles is exactly this output.
        for name in PROFILE_OUTPUTS:
            with self.subTest(file=name):
                self.assertEqual(digest(restricted_out / name), digest(ROOT / "build/generated" / name))
        # The handler file is the one the unrestricted run writes.
        _, full_out = self.run_tool(self.pristine)
        for name in ("cpuemu_13.cpp", "cpuemu_21.cpp"):
            self.assertEqual(digest(restricted_out / name), digest(full_out / name))
        # The 68020 cycle-exact handlers are also the ones upstream ships pregenerated (T24a).
        self.assertEqual(digest(restricted_out / "cpuemu_21.cpp"), digest(ROOT / "upstream/winuae/cpuemu_21.cpp"))
        table = restricted_out / "cpustbl.cpp"
        names = {line.split("op_smalltbl_")[1].split("[")[0] for line in table.read_text().splitlines()
                 if line.startswith("const struct cputbl op_smalltbl_")}
        self.assertEqual(names, set(PROFILE_MODELS))

    def test_invalid_model_numbers_are_rejected(self):
        for argument in ("abc", "56", "-1", "13x", ""):
            with self.subTest(argument=argument):
                result, out = self.run_tool(self.patched, "13", argument)
                self.assertEqual(result.returncode, 1)
                self.assertIn("invalid model number", result.stderr)
                self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
