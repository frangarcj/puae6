"""Original blkdev reader assertions on the shared real-audio fixture."""
from pathlib import Path
from tests.media import test_cd_audio as fixture
ROOT = Path(__file__).resolve().parents[2]


class CdDeviceTests(fixture.CdPlayerTests):
    PROBE = ROOT / 'tests/media/cd_device_probe.cpp'
    EXTRA_SOURCES = fixture.CdPlayerTests.EXTRA_SOURCES + tuple(
        ROOT / 'ports/portable/media' / (name + '.cpp') for name in ('cd_device', 'cd_akiko'))
