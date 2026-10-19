"""CPU restart options and the PUAE5 (CE) filename override use one resolved config."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CpuTagTests(unittest.TestCase):
    def test_ce_forces_exact_from_faster_levels_and_disabled_tags_keep_the_chosen_level(self):
        source = r'''
#include "model.h"
#include <cassert>
int main() {
    for (unsigned machine : {0u, P6_MACHINE_A500, P6_MACHINE_A1200}) {
        for (unsigned level : {P6_CPU_EXACT, P6_CPU_COMPATIBLE, P6_CPU_NORMAL}) {
            p6_options::Settings chosen;
            chosen.machine = machine;
            chosen.cpu_compatibility = level;
            const auto resolved = p6_libretro::resolve_model(chosen, false, {"Game (CE).adf", "disk (CE).adf"});
            p6_config config{};
            p6_options::to_config(resolved.settings, &config);
            assert(config.cpu_compatibility == P6_CPU_EXACT);
            assert(resolved.force_cycle_exact);
            assert(chosen.cpu_compatibility == level && resolved.warnings.empty());
            unsigned notes = 0;
            for (const auto &note : resolved.notes) if (note.find("the tag (CE)") != std::string::npos) ++notes;
            assert(notes == 1);
            assert(p6_libretro::resolve_model(chosen, false, {"Game.adf"}).settings.cpu_compatibility == level);
            chosen.filename_tags = 0;
            const auto ignored = p6_libretro::resolve_model(chosen, false, {"Game (CE).adf"});
            assert(ignored.settings.cpu_compatibility == level && !ignored.force_cycle_exact);
        }
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="p6-cpu-tags-") as temporary:
            path = Path(temporary)
            cpp, exe = path / "tags.cpp", path / "tags"
            cpp.write_text(source)
            includes = ("adapters/libretro", "bridge/include", "references/libretro-api/include")
            built = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    *("-I" + str(ROOT / name) for name in includes), str(cpp), "-o", str(exe)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
