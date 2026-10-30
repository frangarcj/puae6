"""FMV-B2 scalar MPEG-1/Layer II vectors, bounds and exact build parity."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

from tests.build_support import ROOT, build_targets


VECTORS = ROOT / "tests/fmv/vectors"


class FmvDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((VECTORS / "manifest.json").read_text())

    def decode(self, name):
        encoded = "".join((VECTORS / name).read_text().split())
        return base64.b64decode(encoded, validate=True)

    def test_owned_vectors_have_pinned_provenance_and_independent_oracles(self):
        spec = self.manifest
        self.assertEqual(1, spec["schema_version"])
        self.assertIn("ffmpeg 9.0.2", spec["generator"])
        video = self.decode(spec["video"]["file"])
        self.assertEqual(spec["video"]["decoded_bytes"], len(video))
        self.assertEqual(spec["video"]["sha256"], hashlib.sha256(video).hexdigest())
        self.assertEqual("IBBPBBP", spec["video"]["picture_types"])
        self.assertIn("red 32x32 box", spec["video"]["source"])
        self.assertEqual(7, len(spec["video"]["rgb32_sha256"]))
        for key in ("tone", "silence"):
            item = spec["audio"][key]
            encoded = self.decode(item["file"])
            self.assertEqual(spec["audio"]["encoded_bytes"], len(encoded))
            self.assertEqual(item["sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(hashlib.sha256(bytes(1152 * 2 * 2)).hexdigest(),
                         spec["audio"]["silence"]["pcm_s16le_sha256"])
        quiet = spec["audio"]["quiet_multitone"]
        encoded = self.decode(quiet["file"])
        self.assertEqual(quiet["encoded_bytes"], len(encoded))
        self.assertEqual(quiet["sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(quiet["frames"] * spec["audio"]["samples_per_channel"],
                         quiet["samples_per_channel"])
        self.assertEqual(1, quiet["body_max_stereo_zero_run"])
        self.assertIn("incommensurate tones", quiet["source"])
        self.assertRegex(quiet["ffmpeg_pcm_s16le_sha256"], r"^[0-9a-f]{64}$")

    def test_scalar_decoder_patches_are_hash_closed_and_materialized(self):
        result = subprocess.run(
            ["python3", str(ROOT / "tools/materialize_libmpeg2.py"), "--root", str(ROOT)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        series = json.loads((ROOT / "patches/libmpeg2/series.json").read_text())
        expected = {}
        first = {}
        for patch in series["patches"]:
            patch_path = ROOT / patch["file"]
            self.assertEqual(patch["sha256"], hashlib.sha256(patch_path.read_bytes()).hexdigest())
            self.assertNotIn("fno-sanitize", patch_path.read_text())
            for item in patch["files"]:
                first.setdefault(item["path"], item["before_sha256"])
                if item["path"] in expected:
                    self.assertEqual(expected[item["path"]], item["before_sha256"])
                expected[item["path"]] = item["after_sha256"]
        pristine = ROOT / "references/machawk-uae/deps/libmpeg2"
        derived = ROOT / "build/libmpeg2"
        for name, digest in first.items():
            self.assertEqual(digest, hashlib.sha256((pristine / name).read_bytes()).hexdigest())
        for name, digest in expected.items():
            self.assertEqual(digest, hashlib.sha256((derived / name).read_bytes()).hexdigest())

    def expected_output(self):
        spec = self.manifest
        lines = ["malformed truncation/oversize/chunk-bound PASS"]
        lines += [f"video {index} {kind} {digest}" for index, (kind, digest) in enumerate(
            zip(spec["video"]["picture_types"], spec["video"]["rgb32_sha256"]))]
        memory = spec["libmpeg2_requested_bytes_at_352x288_rgb32"]
        lines.append("mpeg-memory " + " ".join(
            f"{label}={memory[key]}" for label, key in (
                ("peak", "peak_live"), ("context", "context"), ("chunk", "chunk"),
                ("yuv", "yuv"), ("convert-id", "convert_id"),
                ("frames", "converted_frames"))))
        lines[-1] += f" mp2-context={spec['kjmp2_context_bytes']}"
        audio = spec["audio"]
        for key in ("tone", "silence"):
            lines.append(f"audio {key} consumed={audio['encoded_bytes']} rate={audio['rate']} "
                         f"samples={audio['samples_per_channel']} hash={audio[key]['pcm_s16le_sha256']}")
        quiet = audio["quiet_multitone"]
        lines.append(f"audio quiet-multitone consumed={quiet['encoded_bytes']} "
                     f"frames={quiet['frames']} rate={audio['rate']} "
                     f"samples={quiet['samples_per_channel']} "
                     f"body-max-stereo-zero-run={quiet['body_max_stereo_zero_run']} "
                     f"hash={quiet['pcm_s16le_sha256']}")
        return "\n".join(lines) + "\n"

    def build_x86_64_probe(self, directory):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return None
        arch = shutil.which("arch")
        clang = shutil.which("clang")
        clangxx = shutil.which("clang++")
        if not arch or not clang or not clangxx:
            return None
        runnable = subprocess.run([arch, "-x86_64", "/usr/bin/true"], capture_output=True)
        if runnable.returncode:
            return None
        output = Path(directory) / "x86_64"
        output.mkdir()
        includes = [
            ROOT / "ports/portable", ROOT / "build/generated", ROOT / "build/upstream/include",
            ROOT / "build/upstream", ROOT / "build/libmpeg2/include", ROOT / "build/libmpeg2/src",
            ROOT / "build/libmpeg2/src/convert",
        ]
        sources = [
            ROOT / "tests/fmv/fmv_decoder_probe.cpp",
            ROOT / "build/upstream/archivers/mp2/kjmp2.cpp",
            ROOT / "ports/portable/media/sha256.c",
            *[ROOT / "build/libmpeg2/src" / name for name in (
                "alloc.c", "cpu_accel.c", "cpu_state.c", "decode.c", "header.c", "idct.c",
                "motion_comp.c", "slice.c", "convert/rgb.c")],
        ]
        objects = []
        common = ["-arch", "x86_64", "-O2", "-fno-fast-math", "-ffp-contract=off",
                  *[value for path in includes for value in ("-I", str(path))]]
        for index, source in enumerate(sources):
            obj = output / f"{index}.o"
            compiler = clangxx if source.suffix == ".cpp" else clang
            standard = "-std=c++17" if source.suffix == ".cpp" else "-std=c11"
            result = subprocess.run([compiler, *common, standard, "-UNDEBUG", "-c", str(source),
                                     "-o", str(obj)], capture_output=True, text=True, timeout=120)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            objects.append(obj)
        executable = output / "p6_fmv_decoder_probe"
        result = subprocess.run([clangxx, "-arch", "x86_64", *map(str, objects), "-o", str(executable)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return arch, executable

    def test_o0_sanitized_and_o2_outputs_are_exactly_equal(self):
        builds = (
            build_targets(ROOT / "build/fmv-decoder-sanitize", ["p6_fmv_decoder_probe"],
                          sanitizers=True),
            build_targets(ROOT / "build/fmv-decoder-opt", ["p6_fmv_decoder_probe"],
                          optimized=True),
        )
        with tempfile.TemporaryDirectory(prefix="p6-fmv-vectors-") as directory:
            paths = []
            for section, key in (("video", None), ("audio", "tone"), ("audio", "silence"),
                                 ("audio", "quiet_multitone")):
                item = self.manifest[section] if key is None else self.manifest[section][key]
                path = Path(directory) / Path(item["file"]).stem
                path.write_bytes(self.decode(item["file"]))
                paths.append(path)
            outputs = []
            for build in builds:
                result = subprocess.run([str(build["p6_fmv_decoder_probe"]), *map(str, paths)],
                                        capture_output=True, text=True, timeout=120,
                                        env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
                self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-8000:])
                outputs.append(result.stdout)
            cross = self.build_x86_64_probe(directory)
            if cross:
                arch, executable = cross
                result = subprocess.run([arch, "-x86_64", str(executable), *map(str, paths)],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-8000:])
                outputs.append(result.stdout)
        self.assertEqual(self.expected_output(), outputs[0])
        self.assertTrue(all(output == outputs[0] for output in outputs[1:]))
        optimized_commands = (ROOT / "build/fmv-decoder-opt/compile_commands.json").read_text()
        self.assertIn("-O2", optimized_commands)
        self.assertNotIn("-ffast-math", optimized_commands)


if __name__ == "__main__":
    unittest.main()
