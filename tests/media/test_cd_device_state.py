"""Device state with real AudioPlayer, mixer exhaustion and another Disc binding."""
from tests.media import test_cd_device as fixture


class CdDeviceStateTests(fixture.CdDeviceTests):
    PROBE = fixture.ROOT / 'tests/media/cd_device_state_probe.cpp'
