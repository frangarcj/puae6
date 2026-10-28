"""FMV-C PCM ownership contract; decoder state and presentation stay closed."""
import hashlib
import json
import re
import unittest

from tests.build_support import ROOT


class FmvAudioTests(unittest.TestCase):
    def test_pcm_owner_patch_is_hash_closed_and_bounded(self):
        series = json.loads((ROOT / "patches/winuae/series.json").read_text())["patches"]
        entry = next(item for item in series if item["id"] == "0419-cd32-fmv-pcm-owner")
        patch_path = ROOT / entry["file"]
        patch = patch_path.read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(patch).hexdigest())
        source = patch.decode()
        self.assertIn("audio_stream[slot].cb_data == cas", source)
        self.assertIn("asd->cb_data != cb_data", source)
        self.assertIn("length > (int)p6_cda::kMaxRuntimeFrames", source)
        self.assertIn("if (!audio_cda_owned(&cas)) return false", source)
        self.assertIn("struct audio_channel_data *cdp = audio_channel + nr", source)
        header = (ROOT / "ports/portable/audio_cda_state.h").read_text()
        self.assertRegex(header, r"kMaxRuntimeFrames\s*=\s*4\s*\*\s*1152")

    def test_pcm_path_has_no_second_clock_mixer_or_frontend_claim(self):
        paths = (
            ROOT / "patches/winuae/0419-cd32-fmv-pcm-owner.patch",
            ROOT / "ports/portable/fmv_audio_buffer.cpp",
        )
        text = "\n".join(path.read_text() for path in paths)
        self.assertIsNone(re.search(
            r"pthread|std::thread|uae_sem|sleep\s*\(|clock_gettime|mach_absolute_time",
            text))
        self.assertIsNone(re.search(
            r"puae6_cartridge|Puae6Cartridge|support_announced|retro_environment|EmuHawk",
            text))
        study = json.loads((ROOT / "plan/extensions/cd.json").read_text())
        self.assertEqual("distinct_cd_audio_state_identity_per_stream",
                         study["pcm_runtime"]["ownership"])
        self.assertEqual("P6FM_value_codec_T43d2", study["pcm_runtime"]["state_codec"])


if __name__ == "__main__":
    unittest.main()
