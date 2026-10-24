"""Real CDA exhaustion drives the player; source bytes are a declared fixture."""
from tests.audio import test_cd_audio as fixture


class CdPlayerTests(fixture.CdAudioTests):
    PROBE = fixture.ROOT / 'tests/media/cd_audio_probe.cpp'
    UPSTREAM_SOURCES = ('cdrom.cpp',)
    EXTRA_SOURCES = tuple(fixture.ROOT / 'ports/portable/media' / (name + '.cpp') for name in
                          ('cd_audio', 'cd_read', 'cd_subchannel', 'cd_disc', 'cd_source', 'cd_utilities'))
