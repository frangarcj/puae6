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
