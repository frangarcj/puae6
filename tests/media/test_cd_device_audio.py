"""Actual blkdev CDDA commands driven by actual mixer exhaustion."""
from tests.media import test_cd_device as fixture


class CdDeviceAudioTests(fixture.CdDeviceTests):
    PROBE = fixture.ROOT / 'tests/media/cd_device_audio_probe.cpp'
