"""Pinned ROM metadata queries consuming the portable text operations.

Synthetic metadata only: no ROM bytes, file loading or device emulation.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.host.test_audio_math import function

ROOT = Path(__file__).resolve().parents[2]
PROBE = r'''
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "rommgr.h"
#include <cassert>
#include <clocale>
#include <initializer_list>
static romdata roms[5];
static romlist installed[2];
static romlist *rl = installed;
static int romlist_cnt = 2;
#include "rom_text_under_test.h"
int main()
{
    for (int i=0; i<4; ++i) {
        roms[i].ver=1; roms[i].rev=3; roms[i].subver=40;
        roms[i].subrev=i+1; roms[i].model="A500\0";
        roms[i].type=ROMTYPE_KICK; roms[i].size=32; roms[i].id=10+i;
    }
    roms[0].name="Kick \xc3\xb1"; roms[0].defaultfilename="Kick.rom";
    roms[1].name="Later fixture"; roms[1].defaultfilename="Later.rom";
    roms[2].name="Split fixture"; roms[2].group=1;
    roms[3].name="Other fixture"; roms[3].model="A1200\0";
    roms[3].type=ROMTYPE_EXTCD32;
    installed[0].rd=&roms[0]; installed[1].rd=&roms[1];
    for (const char *locale : {"C", "", "tr_TR.UTF-8", "en_US.UTF-8"}) {
        if (!std::setlocale(LC_ALL,locale)) continue;
        for (bool all : {false,true}) {
            auto list=getromlistbyident(1,3,-1,-1,"A500",ROMTYPE_KICK,all);
            assert(list && list[0]->rd==&roms[1] && list[1]->rd==&roms[0] && !list[2]);
            if (all) assert(!list[0]->path && !list[1]->path);
            else assert(list[0]==&installed[1] && list[1]==&installed[0]);
            xfree(list);
            // In this pinned body a matching version overwrites the name match.
            // Nonmatching versions isolate the explicit-name selection branch.
            list=getromlistbyident(99,99,-1,-1,"kICK \xc3\xb1",ROMTYPE_KICK,all);
            assert(list && list[0]->rd==&roms[0] && !list[1]); xfree(list);
            assert(!getromlistbyident(99,99,-1,-1,"kICK \xc3\x91",ROMTYPE_KICK,all));
            assert(!getromlistbyident(1,3,-1,-1,"a500",ROMTYPE_KICK,all));
            assert(!getromlistbyident(1,3,-1,-1,"A500",ROMTYPE_EXTCD32,all));
            list=getromlistbyident(1,3,40,2,"A500",ROMTYPE_KICK,all);
            assert(list && list[0]->rd==&roms[1] && !list[1]); xfree(list);
        }
        for (uae_u32 crc : {uae_u32(0),uae_u32(0xffffffff),uae_u32(123)}) {
            roms[0].crc32=crc;
            assert(getfrombydefaultname("kICK.ROM",32)==(crc==123 ? nullptr : &roms[0]));
            assert(!getfrombydefaultname("Kick.rom",31));
            assert(!getfrombydefaultname("Absent.rom",64));
        }
    }
}
'''


class RomTextTests(unittest.TestCase):
    def test_real_metadata_queries(self):
        subprocess.run(["python3", "tools/generate_cpu.py"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE, timeout=120)
        tree = ROOT / "build/upstream"
        source = (tree / "rommgr.cpp").read_text()
        header = ''.join(function(source, signature) for signature in (
            "struct romlist **getromlistbyident (", "STATIC_INLINE int notcrc32 (",
            "struct romdata *getfrombydefaultname("))
        with tempfile.TemporaryDirectory(prefix="p6-rom-text-") as directory:
            directory = Path(directory)
            (directory / "rom_text_under_test.h").write_text(header)
            (directory / "probe.cpp").write_text(PROBE)
            for optimization in ("-O0", "-O2"):
                exe = directory / "probe"
                deps = directory / "probe.d"
                subprocess.run(["clang++", "-std=c++17", optimization, "-UNDEBUG",
                                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                "-I" + str(ROOT / "ports/portable"),
                                "-I" + str(tree / "include"), "-MMD", "-MF", str(deps),
                                str(directory / "probe.cpp"), "-o", str(exe)], check=True)
                self.assertIn(str(ROOT / "ports/portable/tchar.h"), deps.read_text())
                self.assertNotIn("od-win32", deps.read_text())
                subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
