"""MED01/T66b: normalized TOC/sectors; fixed upstream encoder, no engine build."""
import binascii
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import materialize_upstream as mat


@unittest.skipUnless(shutil.which("clang++"), "clang++ unavailable")
class CdDiscTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="cd-disc-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        entry = next(p for p in json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"] if p["id"] == "0355-cdrom-mode1-sync")
        source = (ROOT / "upstream/winuae/cdrom.cpp").read_bytes()
        patch = (ROOT / entry["file"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == entry["files"][0]["before_sha256"]
        assert hashlib.sha256(patch).hexdigest() == entry["sha256"]
        target = work / "cdrom.cpp"
        target.write_bytes(source)
        mat._apply(patch, work, True)
        mat._apply(patch, work, False)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry["files"][0]["after_sha256"]
        cls.exe = work / "probe"
        # This pure unit needs only cstring and real uae integer types. Skip the
        # unrelated platform sysdeps header, not any encoder code or algorithm.
        command = ["clang++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
                   "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-DUAE_SYSDEPS_H", "-include", "cstring",
                   "-I" + str(ROOT / "ports/portable"), "-I" + str(ROOT / "ports/portable/media"), "-I" + str(ROOT / "upstream/winuae/include"),
                   str(ROOT / "tests/media/cd_disc_probe.cpp"), str(ROOT / "ports/portable/media/cd_disc.cpp"),
                   str(ROOT / "ports/portable/media/cd_akiko.cpp"),
                   str(ROOT / "ports/portable/media/cd_read.cpp"), str(ROOT / "ports/portable/media/cd_subchannel.cpp"),
                   str(ROOT / "ports/portable/media/cd_source.cpp"), str(target), "-o", str(cls.exe)]
        out = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise AssertionError(out.stderr)

    def run_probe(self, case):
        out = subprocess.run([str(self.exe), case], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0:max_allocation_size_mb=24"})
        self.assertEqual(0, out.returncode, out.stderr)
        return out.stdout

    def test_mixed_toc_raw_payload_ecma_sync_edc_and_both_pregaps(self):
        self.run_probe("reads")

    def test_waterbox_v2_descriptor_layout_regions_and_rebind(self):
        rows=self.run_probe('wire-v2').splitlines()
        self.assertEqual([int(row.split()[1]) for row in rows],[3,7,8,9,10,11,12,15,16])
        for row in map(str.split,rows):
            lba=int(row[1]); track=2 if lba<9 else 3 if lba<15 else 4
            index1=5 if track==2 else 11 if track==3 else 17
            pre,post=lba<index1, lba in (7,8)
            control=0 if lba in (3,11,12) else 4
            q=bytes((control*16+1,track,0 if pre else 2 if post else 1,0,0,abs(lba-index1),0,0,2,(lba//10)*16+lba%10))
            expected=(b'\xff' if pre or post else b'\0')*12+q+(binascii.crc_hqx(q,0)^0xffff).to_bytes(2,'big')+bytes(72)
            self.assertEqual(bytes.fromhex(row[2]),expected)

    def test_rejections_faults_and_rebind_are_transactional(self):
        self.run_probe("failures")

    def test_akiko_toc_and_subchannels_use_real_upstream_layouts(self):
        self.run_probe("akiko")

    def test_private_sessions_preserve_boundaries_toc_q_and_transactional_rebind(self):
        rows = self.run_probe('sessions').splitlines()
        self.assertEqual([0, 3, 20, 219, 220, 223], [int(row.split()[1]) for row in rows])
        def bcd(n): return n // 10 * 16 + n % 10
        def clock(n): return bytes(map(bcd, (n // 4500, n // 75 % 60, n % 75)))
        for row in map(str.split, rows):
            lba = int(row[1]); track = 1 if lba < 4 else 2
            one = 0 if track == 1 else 220; pre = lba < one
            q = bytes((1 if track == 1 else 0x41, track, 0 if pre else 1))
            q += clock(abs(lba - one)) + b'\0' + clock(lba + 150)
            expected = (b'\xff' if pre else b'\0') * 12 + q + (binascii.crc_hqx(q, 0) ^ 0xffff).to_bytes(2, 'big') + bytes(72)
            self.assertEqual(bytes.fromhex(row[2]), expected)

    def test_canonical_stored_and_synthetic_sectors_and_pause_boundaries(self):
        self.run_probe("canonical")

    def test_mode2_empty_form1_gaps_headers_subq_no_io_and_msf_limits(self):
        out = subprocess.run([str(self.exe), 'mode2-gaps'], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stderr)
        headers = {0: '000200', 74: '000274', 75: '000300', 4349: '005974', 4350: '010000', 4501: '010201', 4503: '010203'}
        self.assertEqual(14, len(out.stdout.splitlines()))
        for row in map(str.split, out.stdout.splitlines()):
            lba = int(row[2]); self.assertEqual(row[3], '00' + 'ff' * 10 + '00' + headers[lba] + '02')
            relative = abs(4502 - lba)
            def bcd(n): return n // 10 * 16 + n % 10
            clock = bytes(map(bcd, (relative // 4500, relative // 75 % 60, relative % 75)))
            q = bytes((0x41, 1, int(lba >= 4502))) + clock + b'\0' + bytes.fromhex(headers[lba])
            expected = b'\xff' * 12 + q + (binascii.crc_hqx(q, 0) ^ 0xffff).to_bytes(2, 'big') + bytes(72)
            self.assertEqual(bytes.fromhex(row[4]), expected)

    def test_explicit_regions_modes_subchannels_postgap_and_rebind(self):
        rows = self.run_probe('regions').splitlines(); self.assertTrue(rows)
        for row in map(str.split,rows):
            control,lba = map(int,row[1:3]); pre,post = lba<2,lba>=5
            def bcd(n): return n//10*16+n%10
            q = bytes((control*16+1,1,0 if pre else 2 if post else 1,0,0,abs(lba-2),0,0,2,bcd(lba)))
            expected = (b'\xff' if pre or post else b'\0')*12+q+(binascii.crc_hqx(q,0)^0xffff).to_bytes(2,'big')+bytes(72)
            self.assertEqual(bytes.fromhex(row[3]),expected)
