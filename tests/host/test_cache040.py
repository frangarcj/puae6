"""T55c1: real upstream cache maintenance and MOVEC policy, without exposing CPUs.

Extract the actual derived functions, as test_cfgfile_preset does. The writer is a
recording memory bus; dirty cache lines must reach it, so a no-op cannot pass.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.host import ensure_generated
from tests.host.test_cfgfile_preset import body

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>
#define P6_PORTABLE 1
using uae_u32 = uint32_t; using uae_u16 = uint16_t; using uae_u8 = uint8_t; using uaecptr = uint32_t;
struct { uae_u32 cacr, caar, prefetch020addr, cacheholdingaddr020, a[8]; } regs;
struct { int cpu_model; bool cpu_compatible, cpu_memory_cycle_exact, cpu_data_cache; } currprefs;
#define m68k_areg(r,n) ((r).a[n])
'''
GLOBALS = r'''
cache020 caches020[CACHELINES020];
cache030 icaches030[CACHELINES030], dcaches030[CACHELINES030];
cache040 icaches040[CACHESETS060], dcaches040[CACHESETS060];
int icachelinecnt, icachehalfline, dcachelinecnt, cache_lastline;
int cacheisets04060, cacheisets04060mask, cacheitag04060mask;
int cachedsets04060, cachedsets04060mask, cachedtag04060mask;
std::vector<std::pair<uaecptr,uae_u32>> writes;
void dcache_lput(uaecptr address, uae_u32 value) { writes.emplace_back(address,value); }
'''
CASES = r'''
cache040 &seed(uaecptr address, int way=0) {
    auto &c=dcaches040[(address>>4)&cachedsets04060mask];
    c.tag[way]=address&cachedtag04060mask; c.valid[way]=c.gdirty[way]=true;
    for(int i=0;i<4;i++) { c.data[way][i]=0x12340000+i; c.dirty[way][i]=(i==0); }
    return c;
}
int main() {
    for(int model : {68040,68060}) {
        currprefs={model,true,false,true}; regs={}; regs.cacr=0x80008000;
        set_cpu_caches(false); writes.clear();
        for(int reg : {3,4,5,6,7,0x805,0x806,0x807}) assert(movec_illg(reg));
        for(int reg : {0,1,2,0x800,0x801}) assert(!movec_illg(reg));
        if(model==68060) assert(!movec_illg(8) && !movec_illg(0x808));
        regs.a[0]=0x10000;
        auto &line=seed(regs.a[0]);
        flush_cpu_caches_040(0xf468); // CPUSHL DC,(A0): push dirty data then invalidate
        assert(!line.valid[0] && !line.gdirty[0]);
        assert(writes.size()==(model==68060 ? 4u:1u));
        for(unsigned i=0;i<writes.size();i++) assert(writes[i]==std::make_pair(0x10000u+4*i,0x12340000u+i));
        writes.clear(); seed(regs.a[0]);
        flush_cpu_caches_040(0xf448); // CINVL discards without writing dirty data
        assert(!line.valid[0] && writes.empty());
        seed(0x10000); auto &next=seed(0x11000,1); regs.a[0]=0x10ff0;
        flush_cpu_caches_040(0xf470); // CPUSHP: 4 KiB physical page with TC unavailable
        assert(!line.valid[0] && next.valid[1]);
        flush_cpu_caches_040(0xf478); // CPUSHA visits the remaining page too
        assert(!next.valid[1]);
        icaches040[0].valid[0]=true; regs.prefetch020addr=0;
        flush_cpu_caches_040(0xf498); // CINVA IC: invalidate real instruction cache/prefetch
        assert(!icaches040[0].valid[0] && regs.prefetch020addr==0xffffffff);
        std::memset(dcaches040,0,sizeof dcaches040);
    }
    for(int model : {68010,68020,68030}) {
        currprefs.cpu_model=model;
        for(int reg : {0,1,0x800,0x801}) assert(!movec_illg(reg));
    }
}
'''


class Cache040Tests(unittest.TestCase):
    def test_dirty_push_invalidation_page_scope_and_mmu_controls(self):
        ensure_generated()
        source = (ROOT / "build/upstream/newcpu.cpp").read_text()
        common = (ROOT / "build/upstream/newcpu_common.cpp").read_text()
        header = (ROOT / "build/upstream/include/newcpu.h").read_text()
        types = header[header.index("#define CACHELINES020"):header.index("struct mmufixup")]
        functions = "\n".join(body(source, name) for name in (
            "void flush_cpu_caches(bool force)", "static void dcache040_push_line(",
            "static void flush_cpu_caches_040_2(", "void flush_cpu_caches_040(", "void set_cpu_caches ("))
        with tempfile.TemporaryDirectory(prefix="p6-cache040-", dir=ROOT / "build") as tmp:
            cpp, exe = Path(tmp)/"cache.cpp", Path(tmp)/"cache"
            cpp.write_text(PRELUDE + types + GLOBALS + functions + body(common, "int movec_illg (") + CASES)
            result = subprocess.run(["clang++", "-std=c++17", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", str(cpp), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(0,result.returncode,result.stderr)
            result = subprocess.run([str(exe)],capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
