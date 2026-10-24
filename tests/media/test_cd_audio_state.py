"""Private player state with real audio consumption and a new Disc/owner."""
from tests.media import test_cd_audio as fixture


class CdPlayerStateTests(fixture.CdPlayerTests):
    PROBE = fixture.fixture.ROOT / 'tests/media/cd_audio_state_probe.cpp'
