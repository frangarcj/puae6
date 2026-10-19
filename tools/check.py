#!/usr/bin/env python3
"""Run explicit assertion sets; an unavailable emulator check never passes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scenario_schema import load_json, validate_scenario

ROOT = Path(__file__).resolve().parents[1]
EXIT = {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'NOT_IMPLEMENTED': 3}
# This is T04's complete HARNESS assertion set, not engine or FFI C# evidence.
REQUIRED_ASSERTIONS = ('native_contract', 'scenario_schema', 'fixture_rebuild', 'dispatcher_contract')
SUITES = {
    'native_contract': 'tests.contracts.test_api',
    'scenario_schema': 'tests.scenarios.test_schema',
    'fixture_rebuild': 'tests.fixtures.test_counter',
    'dispatcher_contract': 'tests.contracts.test_dispatcher',
}
# Checks whose assertion sets are real suites for one task (T26). The dispatcher still knows nothing about the
# emulator itself: these suites build and run the sanitizer canaries, the sanitizer builds of the bridge and the
# deterministic fuzz targets. A required assertion that has no suite (the state fuzz target, for T26, which predates the native state) is
# NOT_IMPLEMENTED, and so is any check/task/profile combination that is not listed. T15 closes the gap: FUZZ01 for T15 is the T26 targets plus
# `state`, the seeded mutation of states loaded through the public p6_state_load (truncated, tampered, other build/ROM/config/media), which
# also checks that a refusal leaves the machine bit for bit as it was. T17 adds what the libretro adapter takes from the frontend: `libretro_content`
# (M3U playlists, a VFS that lies and option values, through the adapter's own headers built with the sanitizers) and `normalize_config` (the option
# schema's normalization with random keys, values and types).
TASK_SUITES = {
    ('SAFE01', 'T26'): {
        'canary_self_test': 'tests.canaries.test_canaries',
        'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
        'sanitized_video_guests': 'tests.step.test_step.VideoGuestSanitizerTests',
    },
    # T24 closes the A1200: the T26 suites plus the video guests of the ECS (A600) and AGA (A1200, 68EC020) programs under ASan/UBSan.
    ('SAFE01', 'T24'): {
        'canary_self_test': 'tests.canaries.test_canaries',
        'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
        'sanitized_video_guests': 'tests.step.test_step.VideoGuestSanitizerTests',
        'sanitized_ecs_guests': 'tests.step.test_ecs.EcsGuestSanitizerTests',
        'sanitized_aga_guests': 'tests.step.test_aga.AgaGuestSanitizerTests',
    },
    ('FUZZ01', 'T26'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
    },
    ('FUZZ01', 'T15'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
    },
    ('FUZZ01', 'T17'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
    },
    # T27 adds the write overlay of the floppies: mutated states that carry it and the export / status calls with hostile arguments.
    ('FUZZ01', 'T27'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
    },
    # T29d adds the hard files of the UAE controller: Rigid Disk Blocks with mutated RDSK, PART, FSHD and LSEG blocks, parsed by the filesystem the way the boot ROM asks, then saved and loaded back.
    ('FUZZ01', 'T29d'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T63a adds the hard files of the libretro adapter: `libretro_content` now also decodes hostile .hdf and .hdz (gzip members cut, flipped, bombs, wrong sizes) with the adapter's own header.
    ('FUZZ01', 'T63a'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T64a adds the volumes of the libretro adapter: `libretro_content` now also builds volumes from hostile trees, LHA archives and ZIP files (flipped, cut, claiming more than the caps, with names that leave
    # the volume) with the adapter's own headers, and checks that an accepted source is an image that is exactly its content. T64 (the closure) owns the same check.
    ('FUZZ01', 'T64a'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    ('FUZZ01', 'T64'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T59 adds the archives of disks of the libretro adapter: `libretro_content` now also runs the `archive` target (a ZIP, a 7z and an RP9 package of ADFs flipped, cut, with the 7z checksums put right
    # again, random LZMA and LZMA2 streams, claims beyond the caps, members by name, manifests of random XML and the order of a set; an accepted disk is a disk of the seeds, byte for byte).
    ('FUZZ01', 'T59'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T76 adds the automatic model and the tags in the names of the libretro adapter: `libretro_content` now also runs the `model` target (random, near-miss, glued and hostile names, 100 KB long
    # ones included, against an independent oracle: pure, never automatic, a choice made by hand is never changed by a tag, what is said is bounded).
    ('FUZZ01', 'T76'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T51 adds the Kickstart choice and the ROMs encrypted by Amiga Forever: `libretro_content` now also runs the `firmware` target (AMIROMTYPE1 images, keys of every length, wrong keys, cut and flipped tags,
    # payloads of 0 to 600 KB against an independent oracle, the input never touched, nothing out unless Decrypted, a round trip gives the ROM back, the table of versions has its seven choices).
    ('FUZZ01', 'T51'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
    # T25: the portability matrix. DET04 (the declared ISA/compiler rows agree, hardware and A/V apart) and DET06 (another locale, zone, clock, directory
    # and ROM path change nothing) are the one module tests.portability.test_portability. Configuration and goldens always run (plan/runners.json,
    # the toolchain files and golden corpus hashes agree); the tests that build and run the rows are opt-in (P6_TEST_PORTABILITY=1: the
    # macOS row natively, the Linux rows in Docker, which must be up) and are SKIPPED otherwise, which the dispatcher reports as BLOCKED, never as
    # PASS: a matrix that did not run is not a matrix that agreed. A full run builds five trees and runs ~90 cases per row (x86_64 is translated
    # and slow), hence TASK_SUITE_TIMEOUT.
    ('DET04', 'T25'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'rows_reproduce_hashes': 'tests.portability.test_portability.RowExecutionTests',
    },
    ('DET06', 'T25'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'rows_under_another_host': 'tests.portability.test_portability.RowExecutionTests',
    },
    # T32a: REF01 (the product against the reference, the same WinUAE snapshot without the patches that change what the engine computes, over the micro-ROM
    # corpus of tests/portability/corpus.py; every difference is declared by an ablated patch and nowhere else) and DET01 (two new processes print the same
    # digests over 10000 units). Both build p6_state_probe twice (build/state-opt and build/reference-opt); `compare_runs` is the comparator's own tests.
    ('REF01', 'T32a'): {
        'compare_runs': 'tests.reference.test_compare_runs',
        'patch_classification': 'tests.reference.test_patch_classes',
        'reference_corpus': 'tests.reference.test_ref01',
    },
    ('DET01', 'T32a'): {
        'compare_runs': 'tests.reference.test_compare_runs',
        'repeated_processes': 'tests.reference.test_det01',
    },
    # T32b: the differential corpus (tests/scenarios/corpus: 28 scenarios on the three machines). REF01 checks its coverage of docs/04-validation.md and compares every scenario with the
    # reference; DET02 and DET03 run every scenario through the registry and the public ABI. A divergence the corpus found is PINNED (where it first shows and in which subsystems) and
    # owned by a task, and the test of the pin passes while it is where the table says; the release gate that fails while any is open (OpenDivergenceGate) belongs to the closure of T32.
    ('REF01', 'T32b'): {
        'compare_runs': 'tests.reference.test_compare_runs',
        'patch_classification': 'tests.reference.test_patch_classes',
        'corpus_coverage': 'tests.reference.test_corpus.CoverageTests',
        'corpus_reference': 'tests.reference.test_corpus.Ref01CorpusTests',
    },
    ('DET02', 'T32b'): {
        'corpus_a500': 'tests.reference.test_corpus_det02.CorpusA500Tests',
        'corpus_a600': 'tests.reference.test_corpus_det02.CorpusA600Tests',
        'corpus_a1200': 'tests.reference.test_corpus_det02.CorpusA1200Tests',
    },
    ('DET03', 'T32b'): {
        'corpus_public_a500': 'tests.reference.test_corpus_det03.CorpusDet03A500Tests',
        'corpus_public_a600': 'tests.reference.test_corpus_det03.CorpusDet03A600Tests',
        'corpus_public_a1200': 'tests.reference.test_corpus_det03.CorpusDet03A1200Tests',
    },
    # T32c: the differential corpus in the portability matrix (P6_MATRIX_SET=differential: its own programs, media and regression goldens), the same rows and
    # variants as T25. Coherence always runs; the rows that build and run are opt-in (P6_TEST_PORTABILITY=1, Docker up) and BLOCKED otherwise. SAFE01 adds the 28 scenarios under ASan/UBSan
    # (a run, and a state saved and loaded); FUZZ01 adds the mutation of the 28 scenario files and of the machine files to the parsers of T59.
    ('DET04', 'T32c'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'differential_coherence': 'tests.portability.test_portability.DifferentialMatrixTests',
        'differential_rows_reproduce_hashes': 'tests.portability.test_portability.DifferentialRowExecutionTests',
    },
    ('DET06', 'T32c'): {
        'matrix_coherence': 'tests.portability.test_portability.MatrixCoherenceTests',
        'differential_coherence': 'tests.portability.test_portability.DifferentialMatrixTests',
        'differential_rows_under_another_host': 'tests.portability.test_portability.DifferentialRowExecutionTests',
    },
    ('SAFE01', 'T32c'): {
        'canary_self_test': 'tests.canaries.test_canaries',
        'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
        'sanitized_video_guests': 'tests.step.test_step.VideoGuestSanitizerTests',
        'sanitized_ecs_guests': 'tests.step.test_ecs.EcsGuestSanitizerTests',
        'sanitized_aga_guests': 'tests.step.test_aga.AgaGuestSanitizerTests',
        'sanitized_corpus': 'tests.reference.test_corpus_sanitizers.CorpusSanitizerTests',
    },
    ('FUZZ01', 'T32c'): {
        'fuzz_native': 'tests.fuzz.test_fuzz.NativeFuzzTests',
        'fuzz_scenario': 'tests.fuzz.test_fuzz.ScenarioFuzzTests',
        'fuzz_corpus_scenario': 'tests.fuzz.test_fuzz.CorpusScenarioFuzzTests',
        'state': 'tests.fuzz.test_fuzz.StateFuzzTests',
        'state_writable': 'tests.fuzz.test_fuzz.StateWritableFuzzTests',
        'libretro_content': 'tests.fuzz.test_fuzz.ContentFuzzTests',
        'normalize_config': 'tests.fuzz.test_fuzz.ConfigNormalizeFuzzTests',
        'hard_file': 'tests.fuzz.test_fuzz.HardFileFuzzTests',
    },
}
# T32 closes the differential corpus with the union of its children, each check with the suites of the task that owns it (T32a: the reference and DET01; T32b: REF01 over the corpus,
# DET02, DET03; T32c: the matrix, the sanitizers and the fuzz), plus the gate that fails while a divergence the corpus found is open (OpenDivergenceGate, P6_RELEASE_GATE=1).
TASK_SUITES[('REF01', 'T32')] = {**TASK_SUITES[('REF01', 'T32b')], 'release_gate_det02': 'tests.reference.test_corpus_det02.OpenDivergenceGate',
                                 'release_gate_det03': 'tests.reference.test_corpus_det03.OpenDivergenceGate'}
TASK_SUITES[('DET01', 'T32')] = TASK_SUITES[('DET01', 'T32a')]
TASK_SUITES[('DET02', 'T32')] = TASK_SUITES[('DET02', 'T32b')]
TASK_SUITES[('DET03', 'T32')] = TASK_SUITES[('DET03', 'T32b')]
for _check in ('DET04', 'DET06', 'SAFE01', 'FUZZ01'):
    TASK_SUITES[(_check, 'T32')] = TASK_SUITES[(_check, 'T32c')]
# T57a reads the extended ADF (UAE-1ADF, UAE--ADF) in memory, read only. MED01: the identity and validation of the media list (the formats and their limits), the guest reading raw MFM tracks and
# AmigaDOS tracks of one image word for word, and the images never written. DET02: a state taken before, in the middle of and after the read of a raw track, and with the head walking over raw
# tracks of six lengths, loads into the same image, a restarted one and a new process. FUZZ01 adds the `trackdisk` target: hostile headers and tables against an independent oracle of the format,
# and what is accepted read by the engine under ASan/UBSan. T57b adds FDI to the same checks (its own tests are in the same modules) and T57 owns the union.
TASK_SUITES[('MED01', 'T57a')] = {
    'media_identity_and_events': 'tests.media.test_media',
    'read_only_disks': 'tests.media.test_med01',
    'extended_adf_reads': 'tests.media.test_trackdisk',
    'floppy_formats': 'tests.media.test_trackdisk_formats',
}
TASK_SUITES[('DET02', 'T57a')] = {'raw_tracks_restore': 'tests.state.test_det02_trackdisk'}
TASK_SUITES[('FUZZ01', 'T57a')] = {**TASK_SUITES[('FUZZ01', 'T51')], 'trackdisk': 'tests.fuzz.test_fuzz.TrackDiskFuzzTests'}
# T57b adds the FDI to the same three checks: MED01 reads FDI files written by tests/media/fdi.py (Amiga tracks, low-level tracks of exact pulses and of weak pulses), DET02 covers them with the
# motor on and off (the weak-bit track fetches a revolution at every index; DSKX carries what the reader keeps), and the fuzz target mutates their header, track table and Huffman streams.
TASK_SUITES[('MED01', 'T57b')] = {**TASK_SUITES[('MED01', 'T57a')], 'fdi_reads': 'tests.media.test_fdi'}
TASK_SUITES[('DET02', 'T57b')] = TASK_SUITES[('DET02', 'T57a')]
TASK_SUITES[('FUZZ01', 'T57b')] = TASK_SUITES[('FUZZ01', 'T57a')]
# T66a is only the bounded immutable source/ISO2048 reader, without a CD device or engine.
TASK_SUITES[('MED01', 'T66a')] = {'cd_source': 'tests.media.test_cd_source'}
# T66c exercises only the real 24C08 component, without enabling Akiko/CD32.
TASK_SUITES[('MED02', 'T66c')] = {'eeprom_protocol': 'tests.media.test_cd32_eeprom.Cd32EepromTests.test_protocol_page_and_address_wrap'}
TASK_SUITES[('DET02', 'T66c')] = {
    'eeprom_restore': 'tests.media.test_cd32_eeprom.Cd32EepromTests.test_restore_between_every_pin_transition',
    'eeprom_reject': 'tests.media.test_cd32_eeprom.Cd32EepromTests.test_invalid_states_are_transactional'}
TASK_SUITES[('MED01', 'T66b')] = {'cd_source_regression': 'tests.media.test_cd_source', 'cd_toc_sectors': 'tests.media.test_cd_disc'}
TASK_SUITES[('MED01', 'T66e1')] = {'native_seekable_iso_wave': 'tests.media.test_cd_file'}
TASK_SUITES[('MED01', 'T66e2a')] = {'pinned_cue_metadata': 'tests.media.test_cue_metadata'}
TASK_SUITES[('MED01', 'T66g1')] = {'akiko_toc_qcode': 'tests.media.test_cd_disc'}
TASK_SUITES[('MED01', 'T66g2')] = {'synthetic_subchannels': 'tests.media.test_cd_subchannel'}
TASK_SUITES[('MED01', 'T66g3')] = {'actual_blkdev_offset_reads': 'tests.media.test_cd_device'}
