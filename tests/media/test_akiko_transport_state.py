"""Private transport cut/continuation with real Akiko DMA and CDA/backend dependencies."""
from tests.audio.test_cd_audio import ROOT
from tests.media import test_cd_audio as fixture


class AkikoTransportStateTests(fixture.CdPlayerTests):
    PROBE = ROOT / 'tests/media/akiko_transport_state_probe.cpp'
    UPSTREAM_SOURCES = ('cdrom.cpp', 'crc32.cpp')
    INCLUDED_SOURCES = ('akiko.cpp',)
    EXTRA_SOURCES = fixture.CdPlayerTests.EXTRA_SOURCES + tuple(
        ROOT / 'ports/portable/media' / (name + '.cpp') for name in ('cd_device', 'cd_akiko'))
