"""Offline tests for the Waterbox ELF build gate."""

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build_waterbox.py"
sys.path.insert(0, str(TOOL.parent))
import build_waterbox  # noqa: E402


def elf64(loads, machine=183, elf_class=2, elf_type=2):
    """Make a minimal ELF64 little-endian executable with PT_LOAD headers."""
    ident = b"\x7fELF" + bytes((elf_class, 1, 1, 0)) + bytes(8)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, elf_type, machine, 1,
                          0, 64, 0, 0, 64, 56, len(loads), 0, 0, 0)
    phdrs = b"".join(struct.pack("<IIQQQQQQ", 1, flags, off, va, va,
                                    filesz, memsz, 0x4000)
                      for off, va, filesz, memsz, flags in loads)
    size = max([64 + len(phdrs)] + [off + filesz for off, _, filesz, _, _ in loads])
    data = bytearray(size)
    data[:64] = header
    data[64:64 + len(phdrs)] = phdrs
    return data


class BuildElfTests(unittest.TestCase):
    def check_bad(self, data):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(data)
            artifact.flush()
            with self.assertRaises(build_waterbox.BuildError):
                build_waterbox.validate_elf(artifact.name)

    def test_accepts_arm64_load_segments(self):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(elf64([(0x4000, 0x4000, 0x3000, 0x4000, 5),
                                  (0x8000, 0x8000, 0x2000, 0x4000, 6)]))
            artifact.flush()
            result = build_waterbox.validate_elf(artifact.name)
            self.assertIsInstance(result, dict)

    def test_rejects_non_arm64_and_non_elf64(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], machine=62))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], elf_class=1))

    def test_rejects_missing_load_and_misaligned_load(self):
        self.check_bad(elf64([]))
        self.check_bad(elf64([(0x4001, 0x4000, 0x4000, 0x4000, 5)]))
        self.check_bad(elf64([(0x4000, 0x4001, 0x4000, 0x4000, 5)]))

    def test_rejects_invalid_or_truncated_load_range(self):
        bad_size = elf64([(0x4000, 0x4000, 0x4001, 0x4000, 5)])
        self.check_bad(bad_size)
        complete = elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])
        self.check_bad(complete[:24])
        truncated = complete[:-1]
        self.check_bad(truncated)

    def test_rejects_rwx_and_mixed_permissions_in_host_page(self):
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 7)]))
        self.check_bad(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5),
                              (0x8000, 0x4000, 0x4000, 0x4000, 6)]))

    def test_cli_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.wbx"
            result = subprocess.run([sys.executable, str(TOOL), "--check-artifact",
                                     str(missing)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", (result.stdout + result.stderr).lower())


class SmokeCoverageTests(unittest.TestCase):
    """--smoke must require the whole T09 coverage, never accept a partial harness."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.damaged = Path(self.directory.name)
        good = elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])
        for kind, data in {
            "wrong-machine": elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)], machine=62),
            "bad-magic": b"\0" + bytes(good[1:]),
            "truncated": bytes(good[:100]),
            "mixed-rw-rx-group": elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5),
                                        (0x8000, 0x4000, 0x4000, 0x4000, 6)]),
        }.items():
            (self.damaged / f"{kind}.wbx").write_bytes(bytes(data))
        self.receipt = {"guest": {"sha256": "a" * 64}, "runtime_dylib": {"sha256": "b" * 64}}
        self.result = {
            "status": "PASS", "evidence": "SMOKE_RUNTIME", "amiga_emulation": False,
            "guest_sha256": "a" * 64, "runtime_sha256": "b" * 64,
            "runner": {"os": "Darwin 25.6.0", "architecture": "Arm64"},
            "checks": {check: list(names) for check, names in build_waterbox.REQUIRED_ASSERTIONS.items()},
            "load_probes": [{"kind": kind, "outcome": "rejected"} for kind in build_waterbox.LAYOUT_PROBES],
        }

    def verify(self):
        return build_waterbox.verify_smoke(self.result, self.receipt, self.damaged)

    def test_accepts_complete_coverage_and_reports_build_gate_reasons(self):
        self.result["runner"] = {"os": "macOS 26.7.0", "architecture": "Arm64"}
        summary = self.verify()
        self.assertEqual(["WB01", "WB13", "WB14", "WB15"], summary["checks"])
        self.assertEqual(set(build_waterbox.DAMAGED_IMAGES), set(summary["build_gate_rejections"]))

    def test_rejects_missing_assertions_and_wrong_artifacts(self):
        self.result["checks"]["WB13"].remove("abi_callback_reentrant")
        with self.assertRaisesRegex(build_waterbox.BuildError, "WB13.*abi_callback_reentrant"):
            self.verify()
        self.setUp()
        del self.result["checks"]["WB14"]
        with self.assertRaisesRegex(build_waterbox.BuildError, "WB14"):
            self.verify()
        self.setUp()
        self.result["guest_sha256"] = "c" * 64
        with self.assertRaisesRegex(build_waterbox.BuildError, "distinto del recibo"):
            self.verify()
        self.setUp()
        self.result["runner"] = {"os": "Linux 6.1", "architecture": "X64"}
        with self.assertRaisesRegex(build_waterbox.BuildError, "runner Darwin ARM64"):
            self.verify()
        self.setUp()
        self.result["status"] = "FAIL"
        with self.assertRaises(build_waterbox.BuildError):
            self.verify()

    def test_never_accepts_a_mixed_group_and_needs_the_build_gate_to_reject_damage(self):
        for probe in self.result["load_probes"]:
            if probe["kind"] == "mixed-rw-rx-group":
                probe["outcome"] = "accepted"
        with self.assertRaisesRegex(build_waterbox.BuildError, "RW dentro de RX"):
            self.verify()
        self.setUp()
        (self.damaged / "wrong-machine.wbx").write_bytes(bytes(elf64([(0x4000, 0x4000, 0x4000, 0x4000, 5)])))
        with self.assertRaisesRegex(build_waterbox.BuildError, "acepta la imagen dañada wrong-machine"):
            self.verify()

    def test_runtime_aborting_on_a_mixed_group_is_fail_closed_and_recorded(self):
        for probe in self.result["load_probes"]:
            if probe["kind"] == "mixed-rw-rx-group":
                probe["outcome"] = "aborted"
        self.assertEqual("aborted", self.verify()["runtime_outcomes"]["mixed-rw-rx-group"])


class Puae6RecipeTests(unittest.TestCase):
    """T19a: what goes into puae6.wbx and how it is built."""

    def test_the_source_list_is_the_one_of_the_native_build(self):
        sources = build_waterbox.puae6_sources()
        import json
        selection = json.loads((ROOT / "manifests/source-manifest.json").read_text())["selection"]
        self.assertTrue(set(f"build/upstream/{u}" for u in selection["upstream_units"]) <= set(sources))
        self.assertTrue(set(f"build/generated/{u}" for u in selection["generated_units"]) <= set(sources))
        self.assertTrue(set(build_waterbox.cmake_sources("p6_portable")) <= set(sources))
        self.assertTrue(set(build_waterbox.cmake_sources("p6_bridge")) <= set(sources))
        self.assertEqual("adapters/waterbox/puae6_guest.cpp", sources[-1])
        self.assertFalse([source for source in sources if "libretro" in source], "the libretro adapter is never linked into the guest")
        self.assertEqual(len(sources), len(set(sources)))

    def test_the_makefile_uses_the_pinned_macHawk_recipe_and_adds_no_linker_script_or_target_flags_of_its_own(self):
        text = "\n".join(line for line in (ROOT / "ports/waterbox/Makefile").read_text().splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("include $(REPO)/references/machawk/waterbox/common.mak", text)
        self.assertRegex(text, r"(?m)^WBX_TARGET_ARCH := aarch64$")
        self.assertRegex(text, r"(?m)^WBX_HOST_PAGE_SIZE := 16384$")
        for forbidden in ("LINKSCRIPT", "TARGET_FLAGS", "-ffixed", "-T "):
            self.assertNotIn(forbidden, text, "the register reservation, the page size and the linker script come from common.mak")
        self.assertIn("-ffp-contract=off", text)       # as the native build: no fused multiply-add reaches Paula's filter

    def test_the_import_inventory_authorizes_no_host_facing_name(self):
        allowed = build_waterbox.allowed_imports()
        self.assertTrue({"malloc", "memcpy", "snprintf", "sinf", "abort", "_Znwm", "__cxa_throw"} <= allowed)
        for name in ("clock_gettime", "time", "fopen", "open", "pthread_create", "socket", "dlopen", "usleep", "getenv", "rand"):
            self.assertNotIn(name, allowed)

    def test_classify_imports_reports_what_is_not_authorized(self):
        allowed = {"malloc", "memcpy"}
        report = build_waterbox.classify_imports({"malloc", "clock_gettime", "p6_load"}, {"p6_load"}, allowed)
        self.assertEqual(["clock_gettime", "malloc"], report["imports"])       # p6_load is defined by our own objects
        self.assertEqual(["clock_gettime"], report["unexpected"])
        self.assertEqual(["memcpy"], report["unused_allowances"])

    @unittest.skipUnless((ROOT / "build/waterbox/puae6/puae6-receipt.json").is_file(), "puae6.wbx has not been built (tools/build_waterbox.py --puae6)")
    def test_the_built_guest_passes_the_contract_checks_and_imports_only_what_is_authorized(self):
        import json
        receipt = json.loads((ROOT / "build/waterbox/puae6/puae6-receipt.json").read_text())
        guest = ROOT / "build/waterbox/puae6/release/puae6.wbx"
        self.assertEqual(receipt["guest"]["sha256"], build_waterbox.sha256(guest))
        self.assertEqual("PASS", build_waterbox.check_no_compiler_tls(guest)["wb16"])
        self.assertEqual("EM_AARCH64", build_waterbox.validate_elf(guest)["machine"])
        self.assertEqual([], receipt["imports"]["unexpected"])
        self.assertEqual(set(build_waterbox.puae6_sources()), set(receipt["sources"]))


def lint_elf(code=(0xD503201F,), phdr_types=(), tls_section=False, tls_symbol=False):
    """ELF64 with one R+X PT_LOAD holding `code`, optional extra program headers and sections."""
    text = b"".join(struct.pack("<I", word) for word in code)
    phdrs = [(1, 5, 0x4000, 0x4000, len(text), len(text), 0x4000)]
    phdrs += [(kind, 4, 0, 0, 0, 0, 8) for kind in phdr_types]
    strtab = b"\0.shstrtab\0.tbss\0.symtab\0"
    symbol_type = 6 if tls_symbol else 1  # STT_TLS or STT_OBJECT
    symtab = bytes(24) + struct.pack("<IBBHQQ", 0, (1 << 4) | symbol_type, 0, 0, 0, 0)
    str_off = 0x4000 + len(text)
    sym_off = (str_off + len(strtab) + 7) // 8 * 8
    sh_off = (sym_off + len(symtab) + 7) // 8 * 8

    def section(name, kind, flags, offset, size, entsize=0):
        return struct.pack("<IIQQQQIIQQ", name, kind, flags, 0, offset, size, 0, 0, 1, entsize)

    sections = [bytes(64), section(1, 3, 0, str_off, len(strtab))]
    if tls_section:
        sections.append(section(11, 8, 0x403, sym_off, 0))  # .tbss, SHF_TLS
    sections.append(section(17, 2, 0, sym_off, len(symtab), 24))
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    data = bytearray(sh_off + 64 * len(sections))
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 183, 1, 0, 64, sh_off, 0,
                             64, 56, len(phdrs), 64, len(sections), 1)
    for index, (kind, flags, off, va, filesz, memsz, align) in enumerate(phdrs):
        struct.pack_into("<IIQQQQQQ", data, 64 + 56 * index, kind, flags, off, va, va, filesz, memsz, align)
    data[0x4000:0x4000 + len(text)] = text
    data[str_off:str_off + len(strtab)] = strtab
    data[sym_off:sym_off + len(symtab)] = symtab
    data[sh_off:] = b"".join(sections)
    return bytes(data)


class NoCompilerTlsTests(unittest.TestCase):
    """WB16: the final guest ELF must not contain compiler-generated TLS."""

    def check(self, data):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(data)
            artifact.flush()
            return build_waterbox.check_no_compiler_tls(artifact.name)

    def check_rejected(self, data, reason):
        with self.assertRaisesRegex(build_waterbox.BuildError, reason):
            self.check(data)

    def test_accepts_code_that_only_uses_x28_and_other_system_registers(self):
        # mov x28,x0; mrs x0,nzcv; msr nzcv,x0; nop: x28 is the sandbox context.
        result = self.check(lint_elf(code=(0xAA0003FC, 0xD53B4200, 0xD51B4200, 0xD503201F)))
        self.assertEqual("PASS", result["wb16"])
        self.assertEqual(4, result["executable_words_scanned"])

    def test_rejects_thread_register_access_in_executable_code(self):
        self.check_rejected(lint_elf(code=(0xD503201F, 0xD53BD04A)), "mrs TPIDR_EL0 en 0x4004")
        self.check_rejected(lint_elf(code=(0xD51BD049,)), "msr TPIDR_EL0")
        self.check_rejected(lint_elf(code=(0xD53BD060,)), "mrs TPIDRRO_EL0")

    def test_rejects_tls_program_header_section_and_symbol(self):
        self.check_rejected(lint_elf(phdr_types=(7,)), "PT_TLS")
        self.check_rejected(lint_elf(tls_section=True), "seccion TLS .tbss")
        self.check_rejected(lint_elf(tls_symbol=True), "simbolo STT_TLS")

    def test_data_segments_are_not_scanned_as_code(self):
        data = bytearray(lint_elf())
        struct.pack_into("<I", data, 64 + 4, 4)  # make the PT_LOAD read-only data
        struct.pack_into("<I", data, 0x4000, 0xD53BD04A)
        self.assertEqual("PASS", self.check(bytes(data))["wb16"])

    def test_rejects_truncated_segments_and_reports_extra_findings(self):
        self.check_rejected(lint_elf()[:0x4000 + 1], "excede el archivo|inválida")
        self.check_rejected(lint_elf(code=(0xD53BD04A,) * 8, phdr_types=(7,)), "y \\d+ mas")

    def test_cli_check_artifact_applies_wb16(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "tls.wbx"
            artifact.write_bytes(lint_elf(code=(0xD53BD04A,)))
            result = subprocess.run([sys.executable, str(TOOL), "--check-artifact",
                                     str(artifact)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WB16", result.stderr)


if __name__ == "__main__":
    unittest.main()
