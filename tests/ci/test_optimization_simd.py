"""Hosted SIMD infrastructure fixtures; never compile or execute a probe."""
import copy
import unittest

from tests.simd import evaluate, matrix


def fixture(row, mode):
    arm = matrix.ROWS[row]["isa"] == "arm64"
    return dict(row=row, mode=mode, environment={"isa": "aarch64" if arm else "x86_64"},
                verified_rgb_values=1 << 24, forced_fallback="scalar",
                backend="neon" if arm and mode != "fallback" else "scalar",
                product_backend="neon" if row.startswith("linux-arm64-") and mode != "fallback" else "portable",
                rgb_digest="0123456789abcdef", sources={"fixture": "a" * 64},
                compiler=matrix.PINS[row]["compiler_version"], compiler_sha256="b" * 64,
                flags=evaluate.flags(mode, row), sanitizer_options="detect_leaks=0")


class RecordTests(unittest.TestCase):
    def test_all_fifteen_records_and_every_original_oracle(self):
        for row in matrix.ROWS:
            for mode in ("opt", "fallback", "san"):
                good = fixture(row, mode)
                self.assertEqual(matrix.validate_record(good, row, mode, None, good["sources"]), good["rgb_digest"])
                faults = dict(row="foreign", mode="foreign", environment={"isa": "foreign"}, verified_rgb_values=1,
                              forced_fallback="neon", backend="foreign", product_backend="foreign", rgb_digest="f" * 16,
                              sources={}, compiler="foreign", compiler_sha256="invalid", flags=[], sanitizer_options="detect_leaks=1")
                for key, value in faults.items():
                    with self.subTest(row=row, mode=mode, field=key):
                        bad = {**copy.deepcopy(good), key: value}
                        with self.assertRaises(ValueError):
                            matrix.validate_record(bad, row, mode, good["rgb_digest"], good["sources"])

    def test_flag_variants_keep_strict_math_sanitizers_and_scalar_vectorization(self):
        for row in matrix.ROWS:
            for mode in ("opt", "fallback", "san"):
                flags = evaluate.flags(mode, row)
                self.assertIn("-fno-fast-math", flags); self.assertIn("-ffp-contract=off", flags)
                if mode == "san":
                    self.assertIn("-fsanitize=address,undefined", flags); self.assertIn("-fno-sanitize-recover=all", flags)
                if mode == "fallback":
                    self.assertIn("-DP6_SIMD_NO_NEON=1", flags)
                    self.assertIn("-fno-tree-vectorize" if row.endswith("gcc") else "-fno-vectorize", flags)
