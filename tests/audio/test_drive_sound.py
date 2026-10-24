"""T74a: mechanical PCM is presentation; phases replay without changing hardware."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tests.state.harness import ProbeCase, ROOT
from tests.state.test_state_upstream import upstream_bodies, with_subs


class KernelTests(unittest.TestCase):
    def test_integer_mixer_gain_restore_and_invalid_phase(self):
        with tempfile.TemporaryDirectory(prefix="p6-drive-sound-") as tmp:
            source, exe = Path(tmp) / "check.cpp", Path(tmp) / "check"
            source.write_text(r'''
#include "sysconfig.h"
#include "sysdeps.h"
#include "options.h"
#include "audio.h"
#include "driveclick.h"
#include "drive_sound.h"
#include <array>
#include <cassert>
uae_prefs currprefs{}, changed_prefs{};
using State = std::array<uint32_t, p6_drive_sound::kWords>;
State state() { State s{}; assert(p6_drive_sound::get(s.data(),s.size())); return s; }
std::array<int16_t,2048> mix(unsigned gain) {
    p6_drive_sound::gain(gain); std::array<int16_t,2048> out{};
    for(unsigned i=0;i<out.size();i+=2) driveclick_mix(&out[i],SND_STEREO,3);
    return out;
}
int main() {
    for (int rate : {44100,48000}) {
        currprefs.sound_freq = rate;
        for (auto &d:currprefs.floppyslots) d.dfxclick=1;
        changed_prefs=currprefs; driveclick_init(); driveclick_check_prefs();
        assert(p6_drive_sound::healthy());
        auto quiet=mix(100); for(auto v:quiet) assert(v==0);
        int16_t paula[2]={9029,-9029}; p6_drive_sound::gain(0);
        driveclick_mix(paula,SND_STEREO,3); assert(paula[0]==9029 && paula[1]==-9029);
        driveclick_insert(0,0); driveclick_motor(0,2); driveclick_click(0,1);
        const auto before=state();
        auto loud=mix(100); const auto after=state();
        bool heard=false; for(auto v:loud) heard|=v!=0; assert(heard);
        assert(p6_drive_sound::set(before.data(),before.size()));
        assert(loud==mix(100)); assert(after==state());
        assert(p6_drive_sound::set(before.data(),before.size()));
        auto muted=mix(0); for(auto v:muted) assert(v==0); assert(after==state());
        assert(p6_drive_sound::set(before.data(),before.size()));
        auto half=mix(50); for(unsigned i=0;i<half.size();++i) assert(half[i]==loud[i]/2);
        for(unsigned at : {0,1,2,3,4,5,19,39}) {
            auto bad=state(); bad[at]=0xffffffffu; const auto live=state();
            assert(!p6_drive_sound::set(bad.data(),bad.size())); assert(state()==live);
        }
        driveclick_motor(0,0); driveclick_click(0,2); mix(100);
        quiet=mix(100); for(auto v:quiet) assert(v==0);
        for(int d=1;d<4;++d) { driveclick_motor(d,1); assert(state()[d*10+1]==1); }
        driveclick_free(); driveclick_init(); assert(mix(0)==muted);
        changed_prefs.floppyslots[0].dfxclick=2; driveclick_check_prefs();
        assert(!p6_drive_sound::healthy());
    }
}
''')
            cc = shutil.which("clang++")
            self.assertIsNotNone(cc)
            includes = [ROOT / p for p in ("ports/portable", "build/upstream/include", "build/upstream", "build/generated")]
            command = [cc, "-std=c++17", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       *["-I" + str(p) for p in includes], str(source), str(ROOT / "ports/portable/drive_sound.cpp"), "-o", str(exe)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])


MECHANICAL = """entry:
    MOVE.W #$2700,SR
    LEA $400.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVE.B #$FF,$BFD100.L
    MOVE.B #$FF,$BFD300.L
    MOVE.B #$75,$BFD100.L
    MOVE.L #handler,$6C.L
    MOVE.W #$C020,$DFF09A.L
    MOVE.W #$2000,SR
loop:
    ADDQ.L #1,D0
    BRA.S loop
handler:
    MOVE.W #$0020,$DFF09C.L
    MOVE.B #$74,$BFD100.L
    MOVE.B #$75,$BFD100.L
    ADDQ.L #1,D1
    RTE
"""


class DriveSoundTests(ProbeCase):
    ROMS = {"mechanical": (MECHANICAL, None, None)}

    def test_gain_changes_only_pcm_and_phases_replay_in_same_and_new_process(self):
        args = ("--units", "14", "--every", "1")
        silent = self.probe("mechanical", *args)
        path = self.dir / "motor.state"
        original = self.probe("mechanical", *args, "--drive-sound", "100", "--save-at", "5", str(path))
        self.assertEqual(silent.digests, original.digests)
        self.assertEqual(silent.pictures, original.pictures)
        self.assertNotEqual(silent.pcm, original.pcm)
        words = struct.unpack(">40I", upstream_bodies(path.read_bytes())[b"P6AU"]["DCLK"])
        self.assertEqual(words[1], 1, "guest actually starts DF0's motor")
        self.assertGreater(words[4], 0, "guest steps the head")
        for load_at in (5, 10):
            run = self.probe("mechanical", *args, "--drive-sound", "100", "--load-at", str(load_at), str(path))
            self.assertEqual(run.loads, [(load_at, 0, "")])
            for unit in range(load_at+1,15):
                saved_unit=5+unit-load_at
                self.assertEqual(run.unit(unit), original.unit(saved_unit))
                self.assertEqual(run.pcm[unit], original.pcm[saved_unit])

    def test_bad_phase_is_refused_before_hardware_or_pcm_changes(self):
        good = self.state_of("valid.state", "mechanical", 5, "--drive-sound", "100")
        def corrupt(subs):
            for sub in subs:
                if sub[0] == "DCLK": sub[1]=sub[1][:20]+b"\xff"*4+sub[1][24:]
        bad = with_subs(good, b"P6AU", corrupt)
        run = self.refusals([("bad sample position", bad, 9, "invalid mechanical sound phase")],
                            rom="mechanical", extra=("--drive-sound", "100"))
        control = self.probe("mechanical", "--units", "12", "--every", "1", "--drive-sound", "100")
        self.assertEqual(control.pcm, run.pcm)
