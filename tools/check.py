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
TASK_SUITES[('MED01', 'T66l1')] = {'pinned_nrg_dao_metadata': 'tests.media.test_nrg_metadata.NrgMetadataTests'}
TASK_SUITES[('MED01', 'T66l2')] = {**TASK_SUITES[('MED01', 'T66l1')], 'native_nrg_source': 'tests.media.test_cd_nrg.CdNrgTests'}
TASK_SUITES[('MED01', 'T66l3')] = {'nrg_mode3_metadata': 'tests.media.test_nrg_metadata.NrgMetadataTests',
                                   'nrg_mode3_source': 'tests.media.test_cd_nrg.CdNrgTests',
                                   'native_cd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66l4')] = {'nrg_mode2_metadata': 'tests.media.test_nrg_metadata.NrgMetadataTests',
                                   'nrg_mode2_source': 'tests.media.test_cd_nrg.CdNrgTests',
                                   'native_cd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66l5')] = {'nrg_etnf_metadata': 'tests.media.test_nrg_metadata.NrgMetadataTests',
                                   'nrg_etnf_source': 'tests.media.test_cd_nrg.CdNrgTests',
                                   'native_cd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66g5')] = {'mode2_synthetic_gap': 'tests.media.test_cd_disc.CdDiscTests',
                                   'mds_mode2_gap_regression': 'tests.media.test_cd_mds.CdMdsTests'}
TASK_SUITES[('MED01', 'T66o1')] = {'native_cd_image_owner': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66p1')] = {'caps_standalone': 'tests.host.test_bridge_caps', 'private_stock_cd32_boot': 'tests.media.test_cd32_boot.Cd32BootTests'}
TASK_SUITES[('LR01', 'T66r1')] = {'direct_cd32_sessions': 'tests.libretro-host.test_cd32_load.Cd32LoadTests'}
TASK_SUITES[('WB03', 'T66r2')] = {'cd32_device_snapshot': 'tests.waterbox-media.test_cd_source.Cd32WaterboxTests',
                               'empty_cd_baseline_regression': 'tests.waterbox-media.test_cd_source.CdTransportTests'}
TASK_SUITES[('ABI01', 'T66r2')] = {'private_cd_layout': 'tests.waterbox-media.test_cd_source.CdLayoutTests'}
TASK_SUITES[('GEN01', 'T66r3')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity',
                               'real_bizhawk_loader_build': 'tests.ffi.test_bizhawk_tree.BizHawkTreeTests.test_each_machine_asks_the_firmware_database_of_bizhawk_for_its_own_kickstart_and_the_database_has_both',
                               'core_registration': 'tests.ffi.test_bizhawk_tree.BizHawkTreeTests.test_both_amiga_cores_are_registered_and_uae_is_still_the_one_a_game_opens_with',
                               'reference_unchanged': 'tests.ffi.test_bizhawk_tree.BizHawkTreeTests.test_macHawk_was_not_touched'}
TASK_SUITES[('LR01', 'T66r3')] = TASK_SUITES[('LR01', 'T66r1')]
TASK_SUITES[('WB03', 'T66r3')] = {'stock_wrapper_restore': 'tests.waterbox-state.test_wrapper_machine.WrapperMachineTests.test_a_state_runs_in_a_new_sandbox_and_in_a_third_as_in_the_first_on_both_machines',
                              'public_cd32_loader_restore': 'tests.waterbox-media.test_cd_source.Cd32PublicCoreTests',
                              'empty_cd_baseline_regression': 'tests.waterbox-media.test_cd_source.CdTransportTests'}
TASK_SUITES[('MED02', 'T66r4')] = {'native_nvram_backing_and_restore': 'tests.media.test_cd32_state.Cd32StateTests',
                               'eeprom_owner_regression': 'tests.media.test_cd32_nvram.Cd32NvramTests'}
TASK_SUITES[('LR01', 'T66r4')] = {'nvram_sram_persistence': 'tests.libretro-host.test_cd32_load.Cd32LoadTests.test_nvram_sram_persistence_reset_and_snapshot_use_the_same_backing'}
TASK_SUITES[('WB03', 'T66r4')] = {'public_cd32_nvram_persistence': 'tests.waterbox-media.test_cd_source.Cd32PublicCoreTests'}
TASK_SUITES[('GEN01', 'T66r6')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity'}
TASK_SUITES[('LR01', 'T66r6')] = {'cd32fr_session_restore': 'tests.libretro-host.test_cd32_load.Cd32LoadTests.test_cd32fr_preset_fast_memory_restore_and_stock_refusal',
                                  'native_cd32fr_autoconfig_state': 'tests.media.test_cd32_state.Cd32StateTests',
                                  'stock_profile_rejections': 'tests.media.test_cd32_boot.Cd32BootTests'}
TASK_SUITES[('WB03', 'T66r6')] = {'public_cd32fr_memory_restore': 'tests.waterbox-media.test_cd_source.Cd32FastCoreTests'}
TASK_SUITES[('LR01', 'T66r7')] = {
    'real_brian_gameplay_restore': 'tests.libretro-host.test_cd32_load.Cd32GameTests.test_brian_gameplay_and_restore',
    'sprite_restore_unsigned_words': 'tests.step.test_sprite_restore.SpriteRestoreTests'}
TASK_SUITES[('WB03', 'T66r7')] = {
    'real_brian_gameplay_snapshot': 'tests.waterbox-media.test_cd_source.Cd32GameCoreTests'}
TASK_SUITES[('LR01', 'T66r8')] = {
    'cold_reload_after_gameplay': 'tests.libretro-host.test_cd32_load.Cd32GameTests.test_brian_cold_reload_matches_fresh_session'}
TASK_SUITES[('LR01', 'T66r9')] = {
    'direct_cd_tag_resolution': 'tests.libretro-host.test_model_tags.CdModelResolutionTests',
    'direct_cd_tag_memory_restore': 'tests.libretro-host.test_cd32_load.Cd32LoadTests.test_direct_cd_filename_tags_fast_memory_and_restore'}
# One integrated scenario covers startup/restore, suppressed A/V and real BK2/TAS seek.
for _gate in ('WB03', 'WB08', 'WB09'):
    TASK_SUITES[(_gate, 'T66r5')] = {'real_cd32_bk2_tas_start_seek': 'tests.bizhawk-movies.test_cd32_start.Cd32MovieStartTests'}
TASK_SUITES[('MED01', 'T66j1')] = {'pinned_ccd_metadata': 'tests.media.test_ccd_metadata.CcdMetadataTests'}
TASK_SUITES[('MED01', 'T66s1')] = {'pinned_mds_metadata': 'tests.media.test_mds_metadata.MdsMetadataTests'}
TASK_SUITES[('MED01', 'T66s2')] = {'native_mds_source': 'tests.media.test_cd_mds.CdMdsTests'}
TASK_SUITES[('MED01', 'T66s3')] = {'native_cd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('LR01', 'T66s3')] = {'public_mds_restore_identity': 'tests.libretro-host.test_cd32_load.Cd32MdsTests.test_mds_public_snapshot_new_process_and_media_identity'}
TASK_SUITES[('MED01', 'T66u1')] = {'cd_media_presence': 'tests.media.test_cd_device.CdDeviceTests',
                                   'cd_device_presence_state': 'tests.media.test_cd_device_state.CdDeviceStateTests'}
TASK_SUITES[('STATE01', 'T66u1')] = {'akiko_presence_and_delivery_state': 'tests.media.test_akiko_state.AkikoStateTests'}
TASK_SUITES[('MED01', 'T66u2')] = TASK_SUITES[('MED01', 'T66u1')]
TASK_SUITES[('STATE01', 'T66u2')] = {'native_cd_catalogue_restore': 'tests.media.test_cd32_state.Cd32StateTests'}
# Both gates exercise one directed public state/control union; callers can select the pair once.
for _gate in ('LR01', 'STATE01'):
    TASK_SUITES[(_gate, 'T66u3')] = {
        'public_cd_playlist_restore': 'tests.libretro-host.test_cd32_load.Cd32PlaylistTests',
        'public_mds_regression': 'tests.libretro-host.test_cd32_load.Cd32MdsTests',
        'callback_deferred_regression': 'tests.libretro-host.test_libretro.LibretroTests.test_a_request_made_inside_a_callback_waits_for_the_next_boundary',
        'same_disc_reinsert_regression': 'tests.libretro-host.test_libretro.LibretroTests.test_opening_and_closing_the_tray_on_the_same_disk_is_still_a_disk_change'}
TASK_SUITES[('ABI01', 'T66u4')] = {'private_cd_layout': 'tests.waterbox-media.test_cd_source.CdLayoutTests'}
TASK_SUITES[('WB03', 'T66u4')] = {
    'cd_catalogue_sandbox_restore': 'tests.waterbox-media.test_cd_source.Cd32CatalogWaterboxTests',
    'single_cd32_regression': 'tests.waterbox-media.test_cd_source.Cd32WaterboxTests',
    'legacy_cd_transport_regression': 'tests.waterbox-media.test_cd_source.CdTransportTests'}
TASK_SUITES[('GEN01', 'T66u5')] = {**TASK_SUITES[('GEN01', 'T66r3')],
    'wrapper_state_inventory': 'tests.waterbox-state.test_wrapper_inventory',
    'mixed_regional_legacy_catalogue': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_xml_amiga_public_selector_xa_and_parameters'}
TASK_SUITES[('WB03', 'T66u5')] = {
    'public_cd_catalogue_restore': 'tests.waterbox-media.test_cd_source.Cd32CatalogCoreTests',
    'public_single_cd_regression': 'tests.waterbox-media.test_cd_source.Cd32PublicCoreTests'}
TASK_SUITES[('WB09', 'T66u6')] = {
    'real_cd32_movie_swap': 'tests.bizhawk-movies.test_cd32_start.Cd32MovieSwapTests',
    'real_cd32_startup_regression': 'tests.bizhawk-movies.test_cd32_start.Cd32MovieStartTests'}
for _gate in ('WB03', 'WB08', 'DET02'):
    TASK_SUITES[(_gate, 'T66t16')] = {'public_chd_cold_restore': 'tests.waterbox-media.test_chd_public.ChdPublicWaterboxTests.test_xml_chd_reads_saveram_and_cold_restore'}
TASK_SUITES[('MED01', 'T66l6')] = {'nrg_metadata': 'tests.media.test_nrg_metadata.NrgMetadataTests',
    'nrg_provider': 'tests.media.test_cd_nrg.CdNrgTests', 'cd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66l7')] = dict(TASK_SUITES[('MED01', 'T66l6')])
TASK_SUITES[('LR01', 'T66t18')] = {'public_htoa_restore': 'tests.libretro-host.test_cd32_load.Cd32ChdTests.test_chd_htoa_public_cold_restore_and_identity'}
for _gate in ('WB03', 'WB08'):
    TASK_SUITES[(_gate, 'T66t18')] = {'public_htoa_restore': 'tests.waterbox-media.test_chd_public.ChdPublicWaterboxTests.test_xml_chd_htoa_saveram_and_cold_restore'}
TASK_SUITES[('DET02', 'T66t18')] = {**TASK_SUITES[('LR01', 'T66t18')], 'public_wb_htoa_restore': TASK_SUITES[('WB03', 'T66t18')]['public_htoa_restore']}
TASK_SUITES[('MED01', 'T66t17')] = {
    'native_htoa': 'tests.media.test_cd_chd.CdChdTests.test_first_stored_pregap_htoa_addresses_toc_subq_and_rebind',
    'canonical_htoa': 'tests.media.test_chd_canonical_csharp.ChdCanonicalCSharpTests.test_first_stored_pregap_keeps_visible_index1_and_full_stream',
    'public_host_htoa': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_first_stored_pregap_public_v2_identity_and_ownership'}
TASK_SUITES[('MED01', 'T66t6')] = {'canonical_chd_provider': 'tests.media.test_cd_chd.CdChdTests'}
TASK_SUITES[('MED01', 'T66t1')] = {'pinned_chd_codecs': 'tests.media.test_chd_dependencies.ChdDependencyTests'}
for _task in ('T66t2', 'T66t3', 'T66t4', 'T66t5'):
    TASK_SUITES[('MED01', _task)] = {'bounded_chd_reader': 'tests.media.test_chd_reader.ChdReaderTests',
                                     'pinned_chd_regression': 'tests.media.test_chd_dependencies.ChdDependencyTests'}
TASK_SUITES[('MED01', 'T66t7')] = {**TASK_SUITES[('MED01', 'T66t5')], 'canonical_chd_provider': 'tests.media.test_cd_chd.CdChdTests'}
TASK_SUITES[('MED01', 'T66t8')] = dict(TASK_SUITES[('MED01', 'T66t7')])
TASK_SUITES[('STATE01', 'T80a')] = {'darwin_host_state_buffers': 'tests.bizhawk-movies.test_host_states.DarwinHostStateTests'}
TASK_SUITES[('MED01', 'T67c3a')] = {'original_cdtv_boot': 'tests.media.test_cdtv_boot.CdtvOriginalBootTests.test_original_iso_hunk_executes_nonce'}
for _gate in ('MED01', 'BUILD01'):
    TASK_SUITES[(_gate, 'T67c1')] = {'cdtv_firmware': 'tests.media.test_cdtv_boot.CdtvFirmwareTests'}
for _gate in ('MED01', 'STATE01', 'DET02'):
    TASK_SUITES[(_gate, 'T67c2')] = {'cdtv_autoconfig_cold_restore': 'tests.media.test_cdtv_state.CdtvAutoconfigTests'}
TASK_SUITES[('MED01', 'T67b1')] = {'cdtv_component': 'tests.media.test_cdtv_state.CdtvStateComponentTests.test_local_roundtrip_and_transactional_refusals', 'cdtv_link_regression': 'tests.media.test_cdtv_link.CdtvLinkTests'}
for _gate in ('STATE01', 'DET02'):
    TASK_SUITES[(_gate, 'T67b2')] = {'cdtv_cold_state': 'tests.media.test_cdtv_state.CdtvStateTests.test_cold_restore_and_future'}
for _gate in ('MED01', 'DET02'):
    TASK_SUITES[(_gate, 'T67a')] = {'real_cdtv_dmac': 'tests.media.test_cdtv_link.CdtvLinkTests.test_real_dmac_commands_dma_and_lifecycle'}
TASK_SUITES[('MED01', 'T66t15b')] = {'typed_regional_wire': 'tests.media.test_chd_canonical_csharp.ChdCanonicalCSharpTests.test_regional_descriptor_materialization_identity_and_rejections', 'real_host_chd_regional': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_four_codecs_real_mount_regions_audio_subq_crossings_and_ownership', 'legacy_source': 'tests.media.test_cd_source_csharp.DiscSourceCSharpTests'}
TASK_SUITES[('BUILD01', 'T66t19')] = {'real_client_common_target': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_real_client_common_target_and_analyzers'}
TASK_SUITES[('WB03', 'T66t19')] = {'direct_chd_cold_restore': 'tests.waterbox-media.test_chd_public.ChdPublicWaterboxTests.test_direct_chd_preference_saveram_and_cold_restore'}
TASK_SUITES[('MED01', 'T66t19')] = {'direct_and_xml_public_selector': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_xml_amiga_public_selector_xa_and_parameters'}
TASK_SUITES[('BUILD01', 'T66t15c')] = {'real_client_common_target': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_real_client_common_target_and_analyzers'}
for _gate in ('MED01', 'STATE01'):
    TASK_SUITES[(_gate, 'T66t15c')] = {'public_xml_amiga_chd': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_xml_amiga_public_selector_xa_and_parameters', 'legacy_source': 'tests.media.test_cd_source_csharp.DiscSourceCSharpTests'}
TASK_SUITES[('BUILD01', 'T66t15a')] = {'real_discsystem_target': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests.test_real_discsystem_target_and_analyzers'}
TASK_SUITES[('MED01', 'T66t15a')] = {'real_chd_host': 'tests.media.test_chd_host_csharp.ChdHostCSharpTests', 'canonical_csharp': 'tests.media.test_chd_canonical_csharp.ChdCanonicalCSharpTests', 'legacy_blob_reads': 'tests.media.test_chd_blob_reads.ChdBlobReadTests'}
TASK_SUITES[('MED01', 'T66t14')] = {'public_chd_factory': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('LR01', 'T66t14')] = {'public_chd_load_catalog_restore': 'tests.libretro-host.test_cd32_load.Cd32ChdTests', 'cue_read_restore_regression': 'tests.libretro-host.test_cd32_load.Cd32LoadTests.test_real_reads_replay_runahead_and_fresh_owner', 'real_chd_game': 'tests.libretro-host.test_cd32_load.Cd32GameTests.test_brian_gameplay_and_restore'}
TASK_SUITES[('DET02', 'T66t14')] = {'real_chd_game': 'tests.libretro-host.test_cd32_load.Cd32GameTests.test_brian_gameplay_and_restore'}
TASK_SUITES[('MED01', 'T66t13')] = {'typed_chd_canonical': 'tests.media.test_chd_canonical_csharp.ChdCanonicalCSharpTests', 'managed_disc_regression': 'tests.media.test_cd_source_csharp.DiscSourceCSharpTests', 'chd_blob_reads': 'tests.media.test_chd_blob_reads.ChdBlobReadTests'}
TASK_SUITES[('STATE01', 'T80b')] = {'media_movie_identity': 'tests.bizhawk-movies.test_media_start.MediaMovieIdentityTests'}
for _gate in ('WB09', 'WB03'):
    TASK_SUITES[(_gate, 'T80b')] = {'real_media_movie_start': 'tests.bizhawk-movies.test_media_start.MediaMovieStartTests'}
TASK_SUITES[('MED01', 'T66t9')] = {
    'explicit_cd_regions': 'tests.media.test_cd_disc.CdDiscTests',
    'chd_regression': 'tests.media.test_cd_chd.CdChdTests',
    'cue_regression': 'tests.media.test_cd_cue.CdCueTests',
    'ccd_regression': 'tests.media.test_cd_ccd.CdCcdTests',
    'nrg_regression': 'tests.media.test_cd_nrg.CdNrgTests',
    'mds_regression': 'tests.media.test_cd_mds.CdMdsTests',
    'factory_regression': 'tests.media.test_cd_image.CdImageTests'}
TASK_SUITES[('MED01', 'T66t10')] = {**TASK_SUITES[('MED01', 'T66t8')], 'common_cd_regions': 'tests.media.test_cd_disc.CdDiscTests'}
TASK_SUITES[('STATE01', 'T66t9')] = {
    'player_region_restore': 'tests.media.test_cd_audio_state.CdPlayerStateTests',
    'device_region_restore': 'tests.media.test_cd_device_state.CdDeviceStateTests'}
TASK_SUITES[('STATE01', 'T66t10')] = dict(TASK_SUITES[('STATE01', 'T66t9')])
TASK_SUITES[('MED01', 'T66t11')] = {'regional_wire_descriptor': 'tests.media.test_cd_disc.CdDiscTests'}
TASK_SUITES[('ABI01', 'T66t11')] = {'legacy_and_regional_cd_layouts': 'tests.waterbox-media.test_cd_source.CdLayoutTests'}
TASK_SUITES[('ABI01', 'T66t12')] = dict(TASK_SUITES[('ABI01', 'T66t11')])
TASK_SUITES[('MED01', 'T66t12')] = dict(TASK_SUITES[('MED01', 'T66t11')])
TASK_SUITES[('WB03', 'T66t12')] = {
    'regional_cd_sandbox_restore': 'tests.waterbox-media.test_cd_source.Cd32RegionalWaterboxTests',
    'legacy_cd_transport': 'tests.waterbox-media.test_cd_source.CdTransportTests',
    'legacy_cd_catalogue': 'tests.waterbox-media.test_cd_source.Cd32CatalogWaterboxTests',
    'legacy_single_cd32': 'tests.waterbox-media.test_cd_source.Cd32WaterboxTests'}
TASK_SUITES[('ABI01', 'T66t9')] = {'legacy_cd_layout': 'tests.waterbox-media.test_cd_source.CdLayoutTests'}
TASK_SUITES[('MED01', 'T66j2')] = {'native_ccd_source': 'tests.media.test_cd_ccd.CdCcdTests', 'pinned_ccd_metadata': 'tests.media.test_ccd_metadata.CcdMetadataTests'}
TASK_SUITES[('MED01', 'T66e2b')] = {'pinned_cue_metadata': 'tests.media.test_cue_metadata', 'native_cue_layout': 'tests.media.test_cd_cue', 'cd_disc_regression': 'tests.media.test_cd_disc'}
TASK_SUITES[('MED01', 'T66e2')] = dict(TASK_SUITES[('MED01', 'T66e2b')])
TASK_SUITES[('MED01', 'T66g1')] = {'akiko_toc_qcode': 'tests.media.test_cd_disc'}
TASK_SUITES[('MED01', 'T66g2')] = {'synthetic_subchannels': 'tests.media.test_cd_subchannel'}
TASK_SUITES[('MED01', 'T66g3')] = {'actual_blkdev_offset_reads': 'tests.media.test_cd_device'}
TASK_SUITES[('MED01', 'T66g4')] = {'canonical_subchannels_and_disc_regression': 'tests.media.test_cd_disc', 'canonical_blkdev': 'tests.media.test_cd_device'}
TASK_SUITES[('MED01', 'T66h1')] = {'actual_akiko_cooperative_service': 'tests.media.test_akiko_service'}
TASK_SUITES[('MED01', 'T66i1')] = {'real_cda_stream': 'tests.audio.test_cd_audio'}
TASK_SUITES[('MED01', 'T66k1')] = {'native_akiko_graph': 'tests.media.test_akiko_link'}
TASK_SUITES[('MED01', 'T66q1')] = {'native_backend_cdda_clock': 'tests.media.test_akiko_link'}
TASK_SUITES[('MED01', 'T66h2')] = {'actual_akiko_reset_and_clock': 'tests.media.test_akiko_clock', 'service_regression': 'tests.media.test_akiko_service'}
TASK_SUITES[('MED01', 'T66h3')] = {'actual_service_cdda': 'tests.media.test_akiko_service', 'clock_regression': 'tests.media.test_akiko_clock', 'nvram_regression': 'tests.media.test_cd32_nvram'}
TASK_SUITES[('MED01', 'T66m1')] = {'actual_akiko_nvram_lifecycle': 'tests.media.test_cd32_nvram'}
TASK_SUITES[('STATE01', 'T66m2')] = {'actual_akiko_transport_cut': 'tests.media.test_akiko_transport_state'}
TASK_SUITES[('STATE01', 'T66m3a')] = {'actual_akiko_controller_cut': 'tests.media.test_akiko_controller_state'}
TASK_SUITES[('STATE01', 'T66m3b')] = {'actual_akiko_joint_restore': 'tests.media.test_akiko_state'}
TASK_SUITES[('STATE01', 'T66m4')] = {'akiko_wire_restore': 'tests.media.test_akiko_state'}
TASK_SUITES[('STATE01', 'T66m5')] = {'owned_cda_paula_phase': 'tests.audio.test_cd_audio', 'joint_phase_restore': 'tests.media.test_akiko_state'}
TASK_SUITES[('STATE01', 'T66m6')] = {'cd32_cia_overlay_latch': 'tests.host.test_cia_overlay_state'}
TASK_SUITES[('STATE01', 'T66m7')] = {'native_cd32_machine_restore': 'tests.media.test_cd32_state', 'legacy_container': 'tests.state.test_state_container'}
TASK_SUITES[('STATE01', 'T66m3')] = {'controller_cut': 'tests.media.test_akiko_controller_state', 'joint_restore': 'tests.media.test_akiko_state'}
TASK_SUITES[('MED01', 'T66i2')] = {'real_cdda_player': 'tests.media.test_cd_audio', 'stream_regression': 'tests.audio.test_cd_audio'}
TASK_SUITES[('MED01', 'T66n1')] = {'blkdev_cdda': 'tests.media.test_cd_device_audio', 'reader_regression': 'tests.media.test_cd_device', 'player_regression': 'tests.media.test_cd_audio'}
TASK_SUITES[('STATE01', 'T66n2a')] = {'player_semantic_restore': 'tests.media.test_cd_audio_state', 'stream_regression': 'tests.audio.test_cd_audio'}
TASK_SUITES[('STATE01', 'T66n2b')] = {'device_semantic_restore': 'tests.media.test_cd_device_state'}
TASK_SUITES[('MED01', 'T66d1')] = {'disc_blob_exact_reads': 'tests.media.test_cd_blob_reads'}
TASK_SUITES[('MED01', 'T66d2')] = {'managed_disc_source': 'tests.media.test_cd_source_csharp'}
TASK_SUITES[('ABI01', 'T66f1')] = {'cd_transport_layout': 'tests.waterbox-media.test_cd_source.CdLayoutTests'}
TASK_SUITES[('WB03', 'T66f1')] = {'cd_transport_restore': 'tests.waterbox-media.test_cd_source.CdTransportTests'}
TASK_SUITES[('MED01', 'T66d3')] = {'chd_hunk_fragmentation': 'tests.media.test_chd_blob_reads'}
# T57 closes both: the formats on the three surfaces. MED01 adds the headless runner (`--disk`), the libretro adapter (the containers hold them, the extension list, the identity of the bytes as given:
# tests/media/test_container.py without an engine and tests/libretro-host/test_containers.py with the core) and the Waterbox sandbox (the guest reads them, a snapshot in the middle of a revolution
# of an FDI with weak pulses comes back in another sandbox); FUZZ01 adds the parsers of the adapter (ContentFuzzTests builds container.h with the sanitizers).
TASK_SUITES[('MED01', 'T57')] = {**TASK_SUITES[('MED01', 'T57b')], 'headless_formats': 'tests.media.test_headless_formats', 'containers': 'tests.media.test_container',
                                 'libretro_containers': 'tests.libretro-host.test_containers', 'libretro_archives': 'tests.libretro-host.test_archives',
                                 'waterbox_trackdisk': 'tests.waterbox-media.test_media_trackdisk'}
TASK_SUITES[('DET02', 'T57')] = TASK_SUITES[('DET02', 'T57b')]
TASK_SUITES[('FUZZ01', 'T57')] = TASK_SUITES[('FUZZ01', 'T57b')]
# T58a is the IPF reader with a free license (the decoder of MAME, third_party/mame-ipf), on its own and without the engine. MED01: the decoder against an oracle that encodes MFM itself (cells, timing
# zones, the write splice, weak and noise cells that differ between revolutions and are the same in every process, the files it opens) and where the files come from (hashes, the license in the files,
# the diff against what MAME has, no capsimg). FUZZ01 adds the `ipf` target: mutated images, with every checksum put right again most of the time, against an independent oracle of which files open,
# under ASan/UBSan. T58b and T58 add the engine, the state and the three surfaces.
TASK_SUITES[('MED01', 'T58a')] = {
    'ipf_decoder': 'tests.media.test_ipf',
    'ipf_provenance': 'tests.media.test_ipf_provenance',
}
TASK_SUITES[('FUZZ01', 'T58a')] = {**TASK_SUITES[('FUZZ01', 'T57')], 'ipf': 'tests.fuzz.test_ipf'}
# T58b puts the reader in the engine (patch 0340). MED01: a guest reads the sectors of an IPF (AmigaDOS tracks with and without weak areas, an index in the middle, a track type with timing zones) and a
# raw track word for word with the write splice where the format puts it, and an IPF the decoder cannot read is refused with its reason. DET02: states before, in the middle of and after the read of an
# IPF track, with weak cells (the reader fetches the next revolution at every index; DSKX carries its count), timing zones, a noise track and a head walking over tracks of different lengths, with the
# motor on and off. FUZZ01: the IPF images of the pool (mutated, with checksums put right again) read by the engine under ASan/UBSan, saved and loaded back.
TASK_SUITES[('MED01', 'T58b')] = {**TASK_SUITES[('MED01', 'T57b')], **TASK_SUITES[('MED01', 'T58a')], 'ipf_engine_reads': 'tests.media.test_ipf_engine'}
TASK_SUITES[('DET02', 'T58b')] = {**TASK_SUITES[('DET02', 'T57')], 'ipf_restore': 'tests.state.test_det02_ipf'}
TASK_SUITES[('FUZZ01', 'T58b')] = TASK_SUITES[('FUZZ01', 'T58a')]
# T58c only changes the bounded IPF index: one-based keys and empty cylinder 83.
# The enclosing T58 gate keeps its full accumulated suites and frontend acceptance.
TASK_SUITES[('MED01', 'T58c')] = {**TASK_SUITES[('MED01', 'T58a')], 'ipf_engine_reads': 'tests.media.test_ipf_engine'}
TASK_SUITES[('DET02', 'T58c')] = {'ipf_restore': 'tests.state.test_det02_ipf'}
TASK_SUITES[('FUZZ01', 'T58c')] = {'ipf': 'tests.fuzz.test_ipf', 'trackdisk': 'tests.fuzz.test_fuzz.TrackDiskFuzzTests'}
# T58 closes both on the three surfaces. MED01 adds the headless runner (`--disk`, a damaged IPF refused with the decoder's reason), the containers of the libretro adapter (an IPF as it is, in an ADZ and in
# a ZIP; CT RAW refused) and the Waterbox sandbox (the guest reads the sectors of an IPF with weak areas and timing zones). DET02 is the union of the restores. WB03 is the Waterbox sandbox: a snapshot taken in
# the middle of a revolution of an IPF with weak cells comes back in the same sandbox and in a new one (test_media_ipf), the same for an FDI and the extended ADF (test_media_trackdisk), and the state of the
# BizHawk core (tests/waterbox-state: save N, advance M, restore, repeat M). FUZZ01 is T58a's: the decoder on its own and the engine reading mutated IPF images.
TASK_SUITES[('MED01', 'T58')] = {**TASK_SUITES[('MED01', 'T58b')], 'headless_formats': 'tests.media.test_headless_formats', 'containers': 'tests.media.test_container',
                                 'libretro_containers': 'tests.libretro-host.test_containers', 'libretro_archives': 'tests.libretro-host.test_archives',
                                 'waterbox_trackdisk': 'tests.waterbox-media.test_media_trackdisk', 'waterbox_ipf': 'tests.waterbox-media.test_media_ipf'}
TASK_SUITES[('DET02', 'T58')] = {**TASK_SUITES[('DET02', 'T58b')], 'libretro_ipf_boot_restore': 'tests.libretro-host.test_ipf_boot',
                                 'pending_blitter_channel_mask': 'tests.state.test_state_upstream.UpstreamBodiesTests.test_a_pending_bltcon_write_does_not_replace_the_saved_channel_mask_on_load'}
TASK_SUITES[('FUZZ01', 'T58')] = TASK_SUITES[('FUZZ01', 'T58b')]
TASK_SUITES[('WB03', 'T58')] = {'ipf_snapshot_in_the_sandbox': 'tests.waterbox-media.test_media_ipf', 'trackdisk_snapshot_in_the_sandbox': 'tests.waterbox-media.test_media_trackdisk',
                                'core_state_save_advance_restore': 'tests.waterbox-state.test_wrapper_state',
                                'ipf_boot_restore_in_the_bizhawk_core': 'tests.waterbox-media.test_ipf_boot'}
# T37, the upstream update tool: a mini upstream with two versions (broken patch, shifted or rewritten patch zone, a new
# relevant file, a generator that changed), the repeatability of the report, a pristine that stays untouched, and the real
# pinned snapshot against itself (no changes, the series byte for byte). It runs the tool; it does not update WinUAE (T38).
TASK_SUITES[('UPD01', 'T37')] = {'update_tool': 'tests.upstream-update.test_update'}
TASK_SUITES[('SRC01', 'T37')] = {'import_sources': 'tests.test_import_sources',
                                 'pinned_snapshot': 'tests.upstream-update.test_update.RealPinnedSnapshotTests',
                                 'new_snapshot_verification': 'tests.upstream-update.test_update.DirectorySnapshotTests'}
TASK_SUITES[('GEN01', 'T37')] = {'regeneration_of_the_candidate': 'tests.upstream-update.test_update.GeneratorTests'}

# T33 measures. PERF01 is the statistics and summary of tools/bench.py (no emulator), the native and libretro probes in the optimized tree (what a sample is, that the clock does not feed the
# engine, no allocation in the steady state) and the committed baseline with the budgets derived from it (a function of the raw files, goals apart from observations, everything advisory
# unless stable). WB12 runs the Waterbox probes (guest ABI and core) in a real sandbox: p50/p95 of a unit and of a snapshot, the dirty unit of Darwin ARM64, the heaps; without a runtime the
# suite skips and the gate is BLOCKED, never PASS. The baseline itself is ORIENTATIVE: no dedicated runner exists (plan/runners.json).
TASK_SUITES[('PERF01', 'T33')] = {
    'bench_statistics': 'tests.bench.test_statistics',
    'native_and_libretro_probes': 'tests.bench.test_native',
    'baseline_and_budgets': 'tests.bench.test_budgets',
}
TASK_SUITES[('WB12', 'T33')] = {
    'waterbox_probes': 'tests.bench.test_wb12',
}
# T34 optimizes what the adapters do around the engine. The retained optimization is in the BizHawk core (the names asked of the controller and the copy of
# the picture); the native bridge, libretro and the guest have nothing to remove. PERF01 is the suites of T33 plus the equivalence and cost of the two forms (tests.bench.test_core_io).
# DET01, DET02 and SAFE01 add what shows that the core still produces the same machine: the picture, the PCM, the timing and the input packet it delivers are those of p6_headless (tests.ffi.test_core),
# what a saved and loaded state brings back (tests.waterbox-state.test_wrapper_state), and the ownership of the sandbox's memory and the canaries of the ABI (test_wrapper_memory, test_ffi).
TASK_SUITES[('PERF01', 'T34')] = {**TASK_SUITES[('PERF01', 'T33')], 'core_io_equivalence_and_cost': 'tests.bench.test_core_io'}
TASK_SUITES[('DET01', 'T34')] = {**TASK_SUITES[('DET01', 'T32a')], 'core_picture_pcm_timing_and_input': 'tests.ffi.test_core'}
TASK_SUITES[('DET02', 'T34')] = {**TASK_SUITES[('DET02', 'T32b')], 'core_state_round_trip': 'tests.waterbox-state.test_wrapper_state'}
TASK_SUITES[('SAFE01', 'T34')] = {'canary_self_test': 'tests.canaries.test_canaries', 'sanitized_lifecycle': 'tests.host.test_bridge_lifecycle',
                                 'core_memory_ownership': 'tests.waterbox-state.test_wrapper_memory', 'core_abi': 'tests.ffi.test_ffi'}
# T34a builds the local Waterbox runtime Release (plan/toolchains.json) and keeps a Debug lane for what only that runtime has (tests/waterbox-smoke/test_runtime_lanes.py runs the ABI/identity
# and host-callback/page suites in it). The gate is the whole Waterbox side with the Release runtime: the build receipts and imports (HOST01), the guest inside a real sandbox on every machine, the
# memory manifest as a whole, the media and hard-file limits, the BizHawk core's state, memory ownership and ABI (tests/waterbox-state, tests/ffi), and the Waterbox probes (WB12) and benchmark suites (PERF01).
# A suite that skips (no Kickstart in the environment) leaves the gate BLOCKED, never PASS.
TASK_SUITES[('HOST01', 'T34a')] = {'imports_and_build_receipts': 'tests.waterbox-smoke.test_build'}
TASK_SUITES[('WB02', 'T34a')] = {
    'memory_manifest_recorded_as_a_whole': 'tests.waterbox-smoke.test_memory_manifest',
    'runtime_lanes': 'tests.waterbox-smoke.test_runtime_lanes',
    'guest_in_waterbox': 'tests.waterbox-smoke.test_puae6',
    'memory_budgets': 'tests.waterbox-smoke.test_puae6_memory',
    'contract': 'tests.waterbox-smoke.test_puae6_contract',
    'hard_files': 'tests.waterbox-smoke.test_puae6_hdf',
    'host_calls_pages_and_restore': 'tests.waterbox-smoke.test_puae6_wb',
    'units_against_headless': 'tests.waterbox-smoke.test_puae6_units',
    'media_frontend': 'tests.waterbox-media.test_media_frontend',
    'media_hdf': 'tests.waterbox-media.test_media_hdf',
    'media_limits': 'tests.waterbox-media.test_media_limits',
    'media_overlay': 'tests.waterbox-media.test_media_overlay',
    'media_volume': 'tests.waterbox-media.test_media_volume',
    'wrapper_audio': 'tests.waterbox-state.test_wrapper_audio',
    'wrapper_floppy': 'tests.waterbox-state.test_wrapper_floppy',
    'wrapper_cpu': 'tests.waterbox-state.test_wrapper_cpu',
    'wrapper_fpu': 'tests.waterbox-state.test_wrapper_fpu',
    'wrapper_inventory': 'tests.waterbox-state.test_wrapper_inventory',
    'wrapper_machine': 'tests.waterbox-state.test_wrapper_machine',
    'wrapper_machines_t50': 'tests.waterbox-state.test_wrapper_machines_t50',
    'wrapper_memory': 'tests.waterbox-state.test_wrapper_memory',
    'wrapper_raster': 'tests.waterbox-state.test_wrapper_raster',
    'wrapper_state': 'tests.waterbox-state.test_wrapper_state',
    'ffi_abi': 'tests.ffi.test_ffi',
    'ffi_core': 'tests.ffi.test_core',
    'ffi_media': 'tests.ffi.test_media',
    'ffi_saveram_format': 'tests.ffi.test_saveram_format',
}
TASK_SUITES[('WB12', 'T34a')] = TASK_SUITES[('WB12', 'T33')]
TASK_SUITES[('PERF01', 'T34a')] = TASK_SUITES[('PERF01', 'T34')]
# T35a evaluates the pixel candidate only. T35 keeps flags and full-frontend
# adoption behind T35b/T35c; opt-in matrix omissions are BLOCKED, never PASS.
TASK_SUITES[('PERF01', 'T35a')] = {'verified_native_pixel_measurement': 'tests.simd.test_decision.PerformanceTests'}
TASK_SUITES[('DET04', 'T35a')] = {'pixel_equivalence_in_declared_rows': 'tests.simd.test_matrix.EquivalenceTests'}
TASK_SUITES[('SAFE01', 'T35a')] = {'pixel_sanitizers_in_declared_rows': 'tests.simd.test_matrix.SafetyTests', 'llvm_pixel_guards_and_selftest': 'tests.simd.test_rgb565'}
TASK_SUITES[('ADR01', 'T35a')] = {'measured_platform_decision': 'tests.simd.test_decision.DecisionTests'}
TASK_SUITES[('DET04', 'T35b')] = {'full_engine_flags_and_restore': 'tests.simd.test_engine_flags.EquivalenceTests'}
TASK_SUITES[('SAFE01', 'T35b')] = {'full_engine_flags_sanitizers': 'tests.simd.test_engine_flags.SafetyTests', 'flag_configuration_fallback': 'tests.simd.test_engine_flags.ConfigurationTests'}
TASK_SUITES[('PERF01', 'T35b')] = {'full_engine_interleaved_measurements': 'tests.simd.test_engine_flags.PerformanceTests'}
TASK_SUITES[('ADR01', 'T35b')] = {'full_engine_flags_decision': 'tests.simd.test_engine_flags.DecisionTests'}
TASK_SUITES[('DET04', 'T35c')] = {'rgb565_frontend_rows_and_restore': 'tests.simd.test_frontend_rgb565.EquivalenceTests'}
TASK_SUITES[('SAFE01', 'T35c')] = {'rgb565_frontend_sanitizer_rows': 'tests.simd.test_frontend_rgb565.SafetyTests', 'rgb565_pixel_guards': 'tests.simd.test_rgb565'}
TASK_SUITES[('PERF01', 'T35c')] = {'rgb565_full_frontend_measurements': 'tests.simd.test_frontend_rgb565.PerformanceTests'}
TASK_SUITES[('ADR01', 'T35c')] = {'rgb565_measured_platform_decision': 'tests.simd.test_frontend_rgb565.DecisionTests'}
# T35 closes the full pixel, engine-flags and product-frontend scopes. The native
# guard module is shared by T35a/T35c: execute it once without dropping any suite.
for _check in ('PERF01', 'DET04', 'SAFE01', 'ADR01'):
    _combined = {}
    for _part in ('T35a', 'T35b', 'T35c'):
        for _name, _module in TASK_SUITES[(_check, _part)].items():
            if _module not in _combined.values():
                _combined[_name] = _module
    TASK_SUITES[(_check, 'T35')] = _combined
TASK_SUITES[('SAFE01', 'T35d')] = {'nearest_sprite_counter_arithmetic': 'tests.step.test_sprite_nearest.NearestCounterTests',
                                'aga_superhires_frontend_sanitizers': 'tests.step.test_sprite_nearest.FrontendSpriteTests.test_actual_sanitized_frontend_and_restores_include_the_failing_aga_superhires_case'}
TASK_SUITES[('AV01', 'T35d')] = {'aga_pixels_and_sprites': 'tests.step.test_aga.PictureTests', 'resolution_rasters': 'tests.step.test_resolution.RasterTests'}
TASK_SUITES[('DET02', 'T35d')] = {'pre_patch_frontend_outputs_and_restore': 'tests.step.test_sprite_nearest.FrontendSpriteTests.test_corrected_outputs_equal_the_pre_patch_observations_without_changing_golden'}
TASK_SUITES[('REF01', 'T35d')] = TASK_SUITES[('REF01', 'T32a')]
TASK_SUITES[('HOST01', 'T35d')] = {'guest_elf_and_imports': 'tests.waterbox-smoke.test_build', 'coherent_guest_memory': 'tests.waterbox-smoke.test_memory_manifest'}
TASK_SUITES[('WB03', 'T35d')] = {'aga_superhires_sprite_sentinel': 'tests.step.test_sprite_nearest.WaterboxSpriteTests',
                              'wrapper_states': 'tests.waterbox-state.test_wrapper_state', 'raster_states': 'tests.waterbox-state.test_wrapper_raster'}
# T36 keeps the safe captured baseline unless the adversarial probe proves an exclusion.
# Each gate exercises the actual sandbox, not the native semantic serializer.
TASK_SUITES[('WB03', 'T36')] = {
    'adversarial_pixel_history_and_new_process': 'tests.waterbox-poison.test_poison',
    'coherent_memory_manifest': 'tests.waterbox-smoke.test_memory_manifest',
}
_wrapper_state = 'tests.waterbox-state.test_wrapper_state.WrapperStateTests'
TASK_SUITES[('WB04', 'T36')] = {
    'alternating_new_sandboxes': _wrapper_state + '.test_a_state_of_one_sandbox_runs_in_a_new_one_and_in_a_third_as_in_the_first_and_they_can_take_turns',
    'before_first_unit': _wrapper_state + '.test_a_state_made_before_the_first_unit_goes_to_a_new_sandbox_too',
}
TASK_SUITES[('WB05', 'T36')] = {
    'dma_drive_held_key_and_pending_swap': _wrapper_state + '.test_save_go_on_load_and_repeat_gives_the_same_units_and_the_same_machine',
    'one_hundred_restores': _wrapper_state + '.test_the_second_load_of_a_state_is_as_good_as_the_first_after_a_hundred_cycles',
    'paula_filter_and_phase': 'tests.waterbox-state.test_wrapper_audio.WrapperAudioTests',
}
TASK_SUITES[('WB08', 'T36')] = {
    'interlaced_suppressed_delivery': 'tests.waterbox-smoke.test_puae6_units.WaterboxUnitTests.test_render_and_audio_switched_off_for_stretches_change_nothing_of_the_units_that_follow',
}
TASK_SUITES[('WB09', 'T36')] = {'movie_seek_branch_and_suppressed_delivery': 'tests.bizhawk-movies.test_movie_playback.MoviePlaybackTests'}
TASK_SUITES[('WB12', 'T36')] = TASK_SUITES[('WB12', 'T33')]
TASK_SUITES[('WB14', 'T36')] = {'actual_guest_physical_groups_and_restore': 'tests.waterbox-smoke.test_puae6_wb'}

# One union of the real callback/page/cold-restore cases covers the three gates.
TASK_SUITES[('BUILD01', 'T28c')] = {
    'runtime_patch_and_cpu_register': 'tests.waterbox-smoke.test_runtime_tpidr',
    'runtime_recipe': 'tests.waterbox-smoke.test_build',
    'runtime_reproducible_binary': 'tests.waterbox-smoke.test_runtime_lanes.RuntimeLaneTests.test_the_release_library_built_from_another_directory_is_the_same_file',
}
for _gate in ('WB13', 'WB14', 'WB15'):
    TASK_SUITES[(_gate, 'T28c')] = {
        'real_guest_callbacks_physical_pages_and_cold_restore': 'tests.waterbox-smoke.test_puae6_wb.RealGuestTests',
    }

# T39a is the local public PR recipe, not the accumulated nightly/release gate of T39b.
TASK_SUITES[('PLAN01', 'T39a')] = {'ci_protocol': 'tests.ci.test_runner', 'plan': 'tests.test_plan'}
TASK_SUITES[('BUILD01', 'T39a')] = {'native_build_and_lifecycle': 'tests.host.test_bridge_lifecycle.BridgeLifecycleTests.test_init_load_apply_unload_and_reload_on_the_real_engine'}
TASK_SUITES[('DET02', 'T39a')] = {
    'ocs_same_image': 'tests.reference.test_corpus_det02.CorpusA500Tests.test_det02_ocs_vsync_irq_pal_at_2_same_image_beam_set',
    'ocs_new_process': 'tests.reference.test_corpus_det02.CorpusA500Tests.test_det02_ocs_vsync_irq_pal_at_2_new_process',
    'ecs_same_image': 'tests.reference.test_corpus_det02.CorpusA600Tests.test_det02_ecs_beam_var_wide_pal_at_2_same_image_beam_set',
    'ecs_new_process': 'tests.reference.test_corpus_det02.CorpusA600Tests.test_det02_ecs_beam_var_wide_pal_at_2_new_process',
    'aga_same_image': 'tests.reference.test_corpus_det02.CorpusA1200Tests.test_det02_aga_planes8_fmode3_pal_at_2_same_image_beam_set',
    'aga_new_process': 'tests.reference.test_corpus_det02.CorpusA1200Tests.test_det02_aga_planes8_fmode3_pal_at_2_new_process',
}
_ci_waterbox = {'actual_guest_snapshots_callbacks_groups_and_new_process': 'tests.waterbox-smoke.test_puae6_wb'}
TASK_SUITES[('WB03', 'T39a')] = {**_ci_waterbox,
    'adversarial_weave_and_mode_changes': 'tests.waterbox-poison.test_poison',
    'core_dma_and_pending_swap': _wrapper_state + '.test_save_go_on_load_and_repeat_gives_the_same_units_and_the_same_machine'}
for _gate in ('WB13', 'WB14', 'WB15'):
    TASK_SUITES[(_gate, 'T39a')] = dict(_ci_waterbox)
TASK_SUITES[('PLAN01', 'T39b')] = {'ci_recipes': 'tests.ci.test_nightly', 'plan': 'tests.test_plan'}
for _gate in ('MED01', 'DET02', 'LR01'):
    TASK_SUITES[(_gate, 'T61')] = {'four_drive_reads_boot_restore_and_identity': 'tests.libretro-host.test_multidrive',
                                 'config_schema_and_defaults': 'tests.config.test_parity'}
TASK_SUITES[('WB03', 'T61')] = {
    'four_drive_snapshot_and_reads': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_four_distinct_boot_disks_restore_reads_and_the_complete_wrapper_in_a_new_sandbox'}
TASK_SUITES[('STATE01', 'T55a')] = {'ricoh_registers_and_transactional_restore': 'tests.state.test_state_ricoh.RicohChunkTests'}
TASK_SUITES[('DET02', 'T55a')] = {
    'ricoh_same_and_new_process': 'tests.state.test_state_ricoh.RicohChunkTests.test_restore_same_and_new_process_preserves_all_subsystems_and_future'}
TASK_SUITES[('DET02', 'T55b1')] = {'a4030_platform_state': 'tests.state.test_a4030'}
TASK_SUITES[('WB03', 'T55b1')] = {
    'a4030_auto_none_sandbox': 'tests.waterbox-state.test_wrapper_cpu.WrapperCpuTests.test_a4030_normal_restores_in_same_and_new_sandbox_with_auto_or_no_fast'}
TASK_SUITES[('GEN01', 'T55c10')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity'}
TASK_SUITES[('LR01', 'T55c10')] = {'a4040_frontend_restore': 'tests.libretro-host.test_a4040.A4040FrontendTests'}
TASK_SUITES[('WB03', 'T55c10')] = {'a4040_sandbox_restore': 'tests.waterbox-state.test_wrapper_a4040.WrapperA4040Tests'}
TASK_SUITES[('STATE01', 'T66r7')] = {'pending_config_countdown': 'tests.state.test_pending_config.PendingConfigTests'}
TASK_SUITES[('GEN01', 'T55b')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity'}
TASK_SUITES[('DET02', 'T55b')] = {
    'a4030_platform_state': 'tests.state.test_a4030',
    'real_a4000_native_restore': 'tests.state.test_kickstart_a4030.NativeKickstartTests'}
TASK_SUITES[('LR01', 'T55b')] = {
    'real_a4000_boot_headless_parity_and_restore': 'tests.state.test_kickstart_a4030.LibretroKickstartTests'}
TASK_SUITES[('WB03', 'T55b')] = {
    'real_a4000_boot_and_snapshot': 'tests.waterbox-state.test_wrapper_machine.WrapperMachineTests.test_wb03_real_a4000_boot_and_snapshot_in_same_new_sandbox_and_process'}
TASK_SUITES[('GEN01', 'T77b1')] = {'generated_mmio_continuation': 'tests.host.test_cpu_continuation.CpuContinuationTests',
                                      'cpu_model_regression': 'tests.codegen.test_gencpu_models.GencpuModelTests'}
for _gate in ('DET02', 'STATE01'):
    TASK_SUITES[(_gate, 'T77b1')] = {'waiting_blits_causal_restore': 'tests.state.test_waiting_blits'}
TASK_SUITES[('STEP01', 'T77b1')] = {'unit_boundary_continuation': 'tests.step.test_step.StepTests'}
TASK_SUITES[('WB03', 'T77b1')] = {'waiting_blits_sandbox_restore': 'tests.waterbox-state.test_waiting_blits.WaitingBlitsWaterboxTests'}
for _gate in ('BUILD01', 'DET02'):
    TASK_SUITES[(_gate, 'T69a')] = {'real_hrtmon_monitor_freeze': 'tests.cartridge.test_hrtmon.HrtMonTests'}
for _gate in ('STATE01', 'DET02'):
    TASK_SUITES[(_gate, 'T69b')] = {'complete_native_hrtmon_restore': 'tests.cartridge.test_hrtmon.HrtMonTests'}
TASK_SUITES[('BUILD01', 'T69c1')] = {'pinned_builtin_generation': 'tests.cartridge.test_hrtmon.HrtMonBuiltinTests.test_generation_is_reproducible_and_rejects_changed_source'}
TASK_SUITES[('STATE01', 'T69c1')] = {'public_builtin_config_restore': 'tests.cartridge.test_hrtmon.HrtMonBuiltinTests.test_public_config_builtin_load_restore_identity_and_teardown'}
for _gate in ('IN01', 'STATE01', 'DET02'):
    TASK_SUITES[(_gate, 'T69c2')] = {'public_freeze_level_restore': 'tests.cartridge.test_hrtmon.HrtMonCommandTests'}
TASK_SUITES[('GEN01', 'T69c3')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity'}
TASK_SUITES[('IN01', 'T69c3')] = {'mapper_levels': 'tests.libretro-ui.test_mapper.MapperProbeTests.test_the_mapper_holds_its_own_checks', 'composed_flags': 'tests.libretro-ui.test_hrtmon.HrtMonOptionTests'}
for _gate in ('LR01', 'STATE01'):
    TASK_SUITES[(_gate, 'T69c3')] = {'real_libretro_hrtmon': 'tests.libretro-ui.test_hrtmon.HrtMonLibretroTests.test_real_hotkey_command_return_and_held_state_in_fresh_process'}
TASK_SUITES[('GEN01', 'T69c4')] = {'schema': 'tests.config.test_schema', 'frontend_parity': 'tests.config.test_parity'}
TASK_SUITES[('STATE01', 'T69c4')] = {'managed_hrtmon_config': 'tests.bizhawk-movies.test_media_start.HrtMonManagedConfigTests'}
for _gate in ('WB03', 'WB04', 'WB09'):
    TASK_SUITES[(_gate, 'T69c4')] = {'public_hrtmon_snapshot_movies': 'tests.bizhawk-movies.test_media_start.HrtMonWrapperTests.test_public_freeze_bk2_tas_and_held_snapshot_in_new_process'}
TASK_SUITES[('HOST01', 'T69c2')] = {'guest_import_recipe': 'tests.waterbox-smoke.test_build.Puae6RecipeTests'}
TASK_SUITES[('IN01', 'T69c2')]['exact_capabilities_regression'] = 'tests.host.test_bridge_caps.BridgeCapsTests'
TASK_SUITES[('STATE01', 'T69c2')].update({
    'parallel_input_restore_rejection_regression': 'tests.input.test_parallel.ParallelTests'})
TASK_SUITES[('STATE01', 'T69b')].update({
    'cpu_interrupt_restore_regression': 'tests.state.test_cpu_modes.CpuModesTests.test_restore_continues_through_interrupts_timers_and_dma',
    'cpu020_restore_regression': 'tests.state.test_cpu_modes.Cpu020ModesTests'})
TASK_SUITES[('DET02', 'T77a')] = {'collision_levels_restore_identity': 'tests.state.test_collisions'}
TASK_SUITES[('BUILD01', 'T55c1')] = {
    'linked_native_and_full_waterbox_exports': 'tests.host.test_cpu_backend_build',
}
TASK_SUITES[('GEN01', 'T55c1')] = {'generic_040_060_no_mmu': 'tests.codegen.test_gencpu_models'}
TASK_SUITES[('DET02', 'T55c1')] = {
    'real_cache_maintenance': 'tests.host.test_cache040',
    'supported_cpu_restore_regression': 'tests.state.test_cpu_modes',
}
TASK_SUITES[('STATE01', 'T55c4')] = {'internal_fpu_owner_and_frames': 'tests.host.test_internal_fpu_state.InternalFpuStateTests', '6888x_state_regression': 'tests.state.test_state_fpu.FpuLayoutTests'}
TASK_SUITES[('STATE01', 'T55c6')] = {
    'p6cp_late_controls_and_internal_fpu': 'tests.host.test_cpu_late_state.CpuLateStateTests',
    'a4030_state_regression': 'tests.state.test_a4030',
    '6888x_state_regression': 'tests.state.test_state_fpu.FpuLayoutTests'}
TASK_SUITES[('STATE01', 'T55c7')] = {
    'frestore040_real_bus_and_arithmetic': 'tests.host.test_frestore040.Frestore040Tests',
    'p6cp040060_regression': 'tests.host.test_cpu_late_state.CpuLateStateTests',
    '6888x_state_regression': 'tests.state.test_state_fpu.FpuLayoutTests',
    'patch_classification': 'tests.reference.test_patch_classes.ClassificationTests'}
TASK_SUITES[('STATE01', 'T55c8')] = dict(TASK_SUITES[('STATE01', 'T55c7')],
    private_frame_regression='tests.host.test_internal_fpu_state.InternalFpuStateTests')
TASK_SUITES[('STATE01', 'T55c11')] = dict(TASK_SUITES[('STATE01', 'T55c8')])
TASK_SUITES[('STATE01', 'T55c12')] = {
    'frestore040_single_denorm': 'tests.host.test_frestore040.Frestore040Tests',
    'private_frame_regression': 'tests.host.test_internal_fpu_state.InternalFpuStateTests'}
TASK_SUITES[('FUZZ01', 'T55c9')] = {
    'public_profile_config_oracle': 'tests.fuzz.test_fuzz.ConfigProfileFuzzTests',
    'dispatch_regression': 'tests.fuzz.test_check.TaskSuiteDispatchTests'}
TASK_SUITES[('GEN01', 'T55c2')] = {'schema_and_generated_defaults': 'tests.config.test_schema', 'frontend_profile_policy': 'tests.config.test_parity'}
TASK_SUITES[('ABI01', 'T55c2')] = {'native_c_cpp_layout': 'tests.contracts.test_api', 'standalone_csharp_layout': 'tests.ffi.test_ffi.ConfigLayoutTests'}
for _gate in ('DET02', 'STATE01'):
    TASK_SUITES[(_gate, 'T55c2')] = {'z3_autoconfig_and_native_restore': 'tests.state.test_z3'}
TASK_SUITES[('ABI01', 'T55c3')] = TASK_SUITES[('ABI01', 'T55c2')]
TASK_SUITES[('LR01', 'T55c3')] = {'z3_shared_option_and_actual_bus_map': 'tests.libretro-host.test_z3'}
TASK_SUITES[('WB03', 'T55c3')] = {'z3_domain_sandbox_and_process_restore': 'tests.waterbox-state.test_wrapper_z3.WrapperZ3Tests.test_domains_and_same_new_sandbox_and_process_restore'}
TASK_SUITES[('PERF01', 'T55c3')] = {'z3_actual_committed_heap_margin': 'tests.waterbox-state.test_wrapper_z3.WrapperZ3Tests.test_actual_heap_commitment_has_the_existing_two_times_margin'}
for _gate in ('GEN01', 'ABI01'):
    TASK_SUITES[(_gate, 'T55c5')] = TASK_SUITES[(_gate, 'T55c2')]
TASK_SUITES[('DET02', 'T55c5')] = {'all_z3_sizes_autoconfig_and_restore': 'tests.state.test_z3'}
TASK_SUITES[('LR01', 'T55c5')] = {'all_z3_sizes_native_frontends': 'tests.libretro-host.test_z3'}
TASK_SUITES[('WB03', 'T55c5')] = TASK_SUITES[('WB03', 'T55c3')]
TASK_SUITES[('PERF01', 'T55c5')] = TASK_SUITES[('PERF01', 'T55c3')]
TASK_SUITES[('PERF01', 'T53')] = {'measured_cpu_levels': 'tests.bench.test_cpu_campaign.CpuCampaignMeasurements'}
TASK_SUITES[('GEN01', 'T53')] = {'supported_cpu_families_and_unchanged_exact': 'tests.codegen.test_gencpu_models'}
for _gate in ('DET02', 'STATE01'):
    TASK_SUITES[(_gate, 'T53')] = {'cpu_modes_restore_identity_and_no_mmu': 'tests.state.test_cpu_modes'}
TASK_SUITES[('LR01', 'T53')] = {
    'cpu_levels_and_restart': 'tests.libretro-host.test_cpu.CpuOptionTests.test_accuracy_is_a_restart_option_and_each_level_runs_the_headless_machine',
    'ce_filename_tag': 'tests.libretro-host.test_model_tags.ModelTagTests.test_ce_overrides_faster_cpu_options_and_uae_keys_unless_filename_tags_are_disabled',
    'raw_cpu_flags': 'tests.libretro-ui.test_uae.UaeProbeTests'}
TASK_SUITES[('WB03', 'T53')] = {
    'cpu_levels_snapshot_and_identity': 'tests.waterbox-state.test_wrapper_cpu.WrapperCpuTests.test_cpu_accuracy_restores_and_refuses_a_state_of_another_level'}
# T60 reuses the floppy COW and explicit atomic persistence; only #SAVEDISK adds
# media. Keep its gate focused on format, real guest writes and timeline rollback.
TASK_SUITES[('MED02', 'T60')] = {'save_disk_format': 'tests.libretro-host.test_savedisk.SaveDiskFormatTests',
                               'save_disk_persistence': 'tests.libretro-host.test_savedisk.SaveDiskFrontendTests',
                               'ordinary_floppy_regression': 'tests.libretro-host.test_floppy_write'}
TASK_SUITES[('LR02', 'T60')] = {
    'save_disk_rewind': 'tests.libretro-host.test_savedisk.SaveDiskFrontendTests.test_rewinding_before_the_writes_discards_them_and_never_creates_a_save_file',
    'save_disk_runahead': 'tests.libretro-host.test_savedisk.SaveDiskFrontendTests.test_the_guest_writes_the_save_disk_and_only_closing_persists_it_then_reopening_uses_it',
    'ordinary_floppy_rewind': 'tests.libretro-host.test_floppy_write.FloppyWriteTests.test_rewind_run_ahead_and_state_loads_write_nothing_and_the_saved_disk_is_the_final_timeline'}
TASK_SUITES[('PKG01', 'T40b')] = {'package_policy': 'tests.packages.test_preview.PolicyTests', 'installed_preview': 'tests.packages.test_preview.InstalledPreviewTests'}
for _gate in ('BUILD01', 'LR01'):
    TASK_SUITES[(_gate, 'T40b')] = {'installed_preview': 'tests.packages.test_preview.InstalledPreviewTests'}
TASK_SUITES[('PKG01', 'T40a')] = {'package_policy': 'tests.packages.test_preview.PolicyTests',
                                'installed_preview': 'tests.packages.test_preview.InstalledPreviewTests'}
for _gate in ('BUILD01', 'LR01'):
    TASK_SUITES[(_gate, 'T40a')] = {'installed_preview': 'tests.packages.test_preview.InstalledPreviewTests'}
# Each CD32 gate exercises its actual boundary. Other T70 devices retain their own tasks.
TASK_SUITES[('IN01', 'T70a')] = {
    'cd32_mapping_and_abi': 'tests.input.test_cd32.Cd32MappingTests',
    'serial_buttons': 'tests.input.test_cd32.Cd32Tests.test_seven_buttons_and_identification_bits_in_both_ports_pal_and_ntsc',
    'dpad_quadrature': 'tests.input.test_cd32.Cd32Tests.test_dpad_quadrature_and_opposite_directions_are_independent_in_both_ports',
    'bk2_codec': 'tests.ffi.test_input_log.InputLogTests.test_cd32_controls_round_trip_through_bizhawks_actual_bk2_codec'}
TASK_SUITES[('DET02', 'T70a')] = {
    'partial_serial_restore': 'tests.input.test_cd32.Cd32Tests.test_partly_read_pad_restores_in_same_and_new_process',
    'transactional_state_rejection': 'tests.input.test_cd32.Cd32Tests.test_malformed_cd32_tail_is_refused_before_touching_the_machine',
    'device_changes': 'tests.input.test_cd32.Cd32Tests.test_device_changes_release_cd32_and_bound_always_covers_both_pads'}
TASK_SUITES[('LR01', 'T70a')] = {'cd32_frontend': 'tests.libretro-host.test_cd32.Cd32FrontendTests'}
TASK_SUITES[('WB03', 'T70a')] = {
    'cd32_snapshot_and_movie': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_cd32_partial_serial_read_restores_and_replays_a_real_bk2_in_both_ports'}
TASK_SUITES[('IN01', 'T70b')] = {
    'mouse_mapping_and_state': 'tests.libretro-host.test_mouse2.Mouse2MappingTests',
    'mouse_pins': 'tests.input.test_mouse2.Mouse2Tests.test_independent_motion_and_three_buttons_reach_both_ports_pal_and_ntsc',
    'mouse_bk2': 'tests.ffi.test_input_log.InputLogTests.test_two_mice_buttons_and_absolute_positions_round_trip_through_the_actual_bk2_codec'}
TASK_SUITES[('DET02', 'T70b')] = {
    'mouse_native_restore': 'tests.input.test_mouse2.Mouse2Tests.test_motion_in_flight_restores_in_same_and_new_process',
    'mouse_device_and_validation': 'tests.input.test_mouse2.Mouse2Tests.test_second_mouse_buttons_are_checked_and_device_changes_release_pending_motion'}
TASK_SUITES[('LR01', 'T70b')] = {'two_mice_and_stick_restore': 'tests.libretro-host.test_mouse2.Mouse2FrontendTests'}
TASK_SUITES[('WB03', 'T70b')] = {
    'mouse_snapshot_and_movie': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_two_mouse_references_motion_and_bk2_restore_in_same_and_new_sandbox_and_process'}
TASK_SUITES[('AV02', 'T74a')] = {'mechanical_pcm_and_native_restore': 'tests.audio.test_drive_sound'}
TASK_SUITES[('IN01', 'T70c')] = {
    'pointer_levels': 'tests.input.test_pointer.PointerTests.test_sensor_position_and_trigger_are_independent_in_both_ports_pal_and_ntsc',
    'pointer_bk2': 'tests.ffi.test_input_log.InputLogTests.test_optical_coordinates_trigger_and_offscreen_round_trip_through_the_actual_bk2_codec'}
TASK_SUITES[('DET02', 'T70c')] = {
    'pointer_restore': 'tests.input.test_pointer.PointerTests.test_optical_latch_and_levels_restore_in_same_and_new_process_even_after_detach',
    'disabled_lpen_pending_event': 'tests.input.test_pointer.PointerTests.test_guest_clearing_lpen_with_a_pending_optical_event_restores_the_latch_even_while_disabled',
    'pointer_transactional_rejection': 'tests.input.test_pointer.PointerTests.test_invalid_pointer_tail_latch_and_pending_event_are_refused_transactionally'}
TASK_SUITES[('LR01', 'T70c')] = {
    'pointer_mapping_and_state': 'tests.libretro-host.test_pointer.PointerMappingTests',
    'pointer_frontend': 'tests.libretro-host.test_pointer.PointerFrontendTests'}
TASK_SUITES[('WB03', 'T70c')] = {
    'pointer_snapshot_and_movie': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_optical_levels_beam_and_bk2_restore_in_same_and_new_sandbox_and_process'}
TASK_SUITES[('AV02', 'T74b')] = {
    'live_libretro_volume': 'tests.libretro-host.test_drive_sound.DriveVolumeTests',
    'live_waterbox_volume': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_drive_volume_is_live_host_preference_and_restore_keeps_mechanical_phase'}
TASK_SUITES[('AV02', 'T74')] = {**TASK_SUITES[('AV02', 'T74a')], **TASK_SUITES[('AV02', 'T74b')]}
TASK_SUITES[('IN01', 'T70d')] = {
    'input_v1_v2_layout': 'tests.contracts.test_api.NativeContractTests',
    'parallel_mapping': 'tests.libretro-host.test_parallel.ParallelMappingTests',
    'parallel_cia_pins': 'tests.input.test_parallel.ParallelTests.test_independent_parallel_direction_and_fire_pins_pal_ntsc_and_v1_disconnect',
    'parallel_bk2': 'tests.ffi.test_input_log.InputLogTests.test_parallel_joysticks_round_trip_through_the_actual_bk2_codec'}
TASK_SUITES[('DET02', 'T70d')] = {
    'parallel_restore': 'tests.input.test_parallel.ParallelTests.test_parallel_levels_and_device_changes_restore_in_same_and_new_process',
    'parallel_rejection': 'tests.input.test_parallel.ParallelTests.test_truncated_parallel_tail_and_invalid_types_or_buttons_are_transactionally_refused'}
TASK_SUITES[('LR01', 'T70d')] = {'parallel_frontend': 'tests.libretro-host.test_parallel.ParallelFrontendTests'}
TASK_SUITES[('WB03', 'T70d')] = {
    'parallel_snapshot_and_movie': 'tests.waterbox-state.test_wrapper_state.WrapperStateTests.test_four_players_parallel_pins_and_bk2_restore_in_same_and_new_sandbox_and_process'}
TASK_SUITES[('MED02', 'T65a')] = {
    'three_role_bundle': 'tests.whdload.test_bundle.BundleTests',
    'real_save_and_immutable_data': 'tests.whdload.test_bundle.BundleLibretroTests.test_real_highscore_save_and_restore_keep_data_immutable'}
TASK_SUITES[('MED02', 'T65c1')] = {'managed_bundle_bytes': 'tests.whdload.test_bundle_csharp'}
TASK_SUITES[('LR01', 'T65c2')] = {'managed_loader': 'tests.whdload.test_loader_csharp.LoaderCSharpTests'}
for _gate in ('MED02', 'DET02', 'WB03'):
    TASK_SUITES[(_gate, 'T65c2')] = {'managed_auto_save_restore': 'tests.whdload.test_loader_csharp.LoaderWaterboxTests'}
TASK_SUITES[('DET02', 'T65a')] = {
    'native_state_and_pcm_replay': 'tests.whdload.test_bundle.BundleLibretroTests.test_real_highscore_save_and_restore_keep_data_immutable'}
TASK_SUITES[('LR01', 'T65a')] = {'real_bundle_frontend': 'tests.whdload.test_bundle.BundleLibretroTests'}
TASK_SUITES[('WB03', 'T65a')] = {
    'real_bundle_new_sandbox': 'tests.whdload.test_bundle.BundleWaterboxTests.test_real_highscore_save_replays_in_same_and_new_sandbox'}
TASK_SUITES[('MED02', 'T65b1')] = {'cpp_bundle_parity': 'tests.whdload.test_cpp_bundle.CppBundleTests'}
TASK_SUITES[('MED02', 'T65b2')] = {'provider_pins': 'tests.whdload.test_autoload.ProviderTests',
    'real_autoload_save': 'tests.whdload.test_autoload.AutoloadTests.test_auto_game_save_restore_and_next_session_keep_data_immutable'}
TASK_SUITES[('DET02', 'T65b2')] = {'autoload_replay': 'tests.whdload.test_autoload.AutoloadTests.test_auto_game_save_restore_and_next_session_keep_data_immutable'}
TASK_SUITES[('LR01', 'T65b2')] = {'autoload_frontend': 'tests.whdload.test_autoload.AutoloadTests'}
TASK_SUITES[('WB03', 'T65b2')] = {'shared_auto_bundle_sandbox': 'tests.whdload.test_loader_csharp.LoaderWaterboxTests'}
# Both builders produce identical media; the actual automatic managed load supplies
# the shared Waterbox gate. Keep every parent gate and run an identical suite once.
for _parent, _children in (('T65b', ('T65b1', 'T65b2')), ('T65c', ('T65c1', 'T65c2')), ('T65', ('T65a', 'T65b', 'T65c'))):
    for _gate in ('MED02', 'DET02', 'LR01', 'WB03'):
        TASK_SUITES[(_gate, _parent)] = {
            _suite: _suite for _child in _children
            for _suite in TASK_SUITES.get((_gate, _child), {}).values()}
for _gate in ('IN01', 'DET02', 'LR01', 'WB03'):
    TASK_SUITES[(_gate, 'T70')] = {
        f'{_task}:{_name}': _suite for _task in ('T70a', 'T70b', 'T70c', 'T70d')
        for _name, _suite in TASK_SUITES[(_gate, _task)].items()}
CI_LANES = {'BUILD01': ('clean', 'matrix', 'optimization'), 'DET02': ('functional',), 'WB03': ('functional',),
            'FUZZ01': ('sanitizers',), 'PERF01': ('performance',), 'WB13': ('functional',), 'WB14': ('functional',), 'WB15': ('functional',)}

TASK_SUITE_TIMEOUT = {('PERF01', 'T53'): 4 * 3600, ('DET01', 'T34'): 4 * 3600, ('DET02', 'T34'): 4 * 3600, ('SAFE01', 'T34'): 4 * 3600,
                      ('DET04', 'T25'): 6 * 3600, ('DET06', 'T25'): 6 * 3600,
                      ('DET04', 'T32c'): 6 * 3600, ('DET06', 'T32c'): 6 * 3600,
                      ('DET04', 'T32'): 6 * 3600, ('DET06', 'T32'): 6 * 3600}
TASK_SUITE_ENV = {'P6_TEST_SANITIZERS': '1', 'P6_RELEASE_GATE': '1'}      # the second is read only by the OpenDivergenceGate tests of T32
DRIVER = '''import json, pathlib, sys, unittest
# T34a: the suites of tests/waterbox-* import their neighbours by the bare name (discover -s DIR puts the directory on the path): so does loading them by dotted name
_parts = sys.argv[1].split('.')
while _parts:
    _file = pathlib.Path(*_parts).with_suffix('.py')
    if _file.is_file():
        sys.path.append(str(_file.parent.resolve()))
        break
    _parts.pop()  # a registry entry can name a TestCase class or a single method
suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = dict(tests=result.testsRun, failures=len(result.failures),
              errors=len(result.errors), skipped=len(result.skipped),
              expected_failures=len(result.expectedFailures),
              unexpected_successes=len(result.unexpectedSuccesses))
print(json.dumps(report))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def _run_suite(module, env=None, timeout=120):
    environment = None if env is None else {**os.environ, **env}
    return subprocess.run([sys.executable, '-c', DRIVER, module], cwd=ROOT, env=environment,
                          capture_output=True, text=True, timeout=timeout)


def _dispatch_task_suites(check_id, task, required, finish, executed):
    suites = TASK_SUITES[(check_id, task)]
    wanted = sorted(set(suites) | set(required))
    missing = [name for name in wanted if name not in suites]
    if missing:
        return finish('NOT_IMPLEMENTED', 'Falta cobertura requerida: ' + ', '.join(missing), wanted)
    if not all(shutil.which(tool) for tool in ('clang', 'clang++', 'cmake')):
        return finish('BLOCKED', 'Faltan compiladores HOST clang/clang++/cmake', wanted, 'HARNESS')
    for name in wanted:
        result = _run_suite(suites[name], TASK_SUITE_ENV, TASK_SUITE_TIMEOUT.get((check_id, task), 3600))
        try:
            counts = json.loads(result.stdout.strip().splitlines()[-1])
            if type(counts) is not dict or any(type(n) is not int or n < 0 for n in counts.values()):
                raise ValueError('Contadores de suite invalidos')
        except (ValueError, TypeError, IndexError) as error:
            return finish('FAIL', f'{name}: salida invalida/truncada: {error}\n{result.stderr[-4000:]}', wanted, 'HARNESS')
        if result.returncode or counts['failures'] or counts['errors'] or counts['unexpected_successes']:
            return finish('FAIL', f'{name}: fallo de suite\n{result.stderr[-4000:]}', wanted, 'HARNESS')
        if not counts['tests']:
            return finish('FAIL', f'{name}: suite vacia; no hay prueba', wanted, 'HARNESS')
        if counts['skipped'] or counts['expected_failures']:
            return finish('BLOCKED', f'{name}: assertions omitidas o pendientes', wanted, 'HARNESS')
        executed.append(name)
    return finish('PASS', 'Suites nativas ejecutadas (sanitizers, fuzzing, matriz de portabilidad segun la tarea); no es un resultado de emulacion', wanted, 'HARNESS')


def dispatch(check_id, task='T04', profile='deterministic-a500', scenario=None, required=()):
    assertions = list(REQUIRED_ASSERTIONS) + sorted(set(required) - set(REQUIRED_ASSERTIONS))
    executed = []
    def finish(status, diagnostic, evidence='NONE', set_override=None):
        return dict(schema_version=1, check_id=check_id, task=task, profile=profile,
                    status=status, exit_code=EXIT[status], evidence=evidence,
                    core_build_id=None, scenario_executed=False,
                    assertion_set=(set_override if set_override is not None else
                                   assertions if check_id == 'HARNESS01' else []),
                    assertions_executed=executed, diagnostic=diagnostic)
    try:
        plan = load_json(ROOT / 'plan/tasks.json')
        if check_id not in plan['check_ids']:
            return finish('FAIL', 'Check desconocido: ' + check_id)
        if task not in {t['id'] for t in plan['tasks']}:
            return finish('FAIL', 'Tarea desconocida: ' + task)
        if scenario is not None:
            validate_scenario(load_json(scenario))
            return finish('NOT_IMPLEMENTED', 'Escenario valido, pero no hay runner de emulacion conectado')
        if task == 'T39b' and check_id in CI_LANES and profile == 'deterministic-a500':
            from ci.run import command
            from ci.nightly import validate_result
            wanted = list(CI_LANES[check_id])
            if set(required) - set(wanted):
                return finish('NOT_IMPLEMENTED', 'Lane requerida ausente', set_override=wanted)
            for lane in wanted:
                process = command([sys.executable, 'tools/ci/nightly.py', '--lane', lane], timeout=24 * 3600)
                try:
                    payload = json.loads(process.stdout.strip().splitlines()[-1])
                    validate_result(payload, lane)
                except (ValueError, TypeError, KeyError, IndexError):
                    return finish('FAIL', 'Veredicto CI invalido/incompleto', set_override=wanted)
                status = payload['status']
                if status not in ('PASS', 'FAIL', 'BLOCKED') or payload.get('lane') != lane:
                    return finish('FAIL', 'Veredicto CI incompleto', set_override=wanted)
                if status != 'PASS' or process.returncode != 0:
                    return finish(status if status != 'PASS' else 'FAIL', json.dumps(payload, ensure_ascii=False), set_override=wanted)
                executed.append(lane)
            return finish('PASS', 'Lanes CI completas ejecutadas: ' + ', '.join(wanted), 'CI_RUNTIME', wanted)
        if (check_id, task) in TASK_SUITES and profile == 'deterministic-a500':
            def task_finish(status, diagnostic, wanted, evidence='NONE'):
                return finish(status, diagnostic, evidence, wanted)
            return _dispatch_task_suites(check_id, task, required, task_finish, executed)
        if check_id != 'HARNESS01' or task != 'T04' or profile != 'deterministic-a500':
            return finish('NOT_IMPLEMENTED', 'No hay assertions implementadas para este check/tarea/perfil')
        missing = set(assertions) - set(SUITES)
        if missing:
            return finish('NOT_IMPLEMENTED', 'Falta cobertura requerida: ' + ', '.join(sorted(missing)))
        if not all(shutil.which(tool) for tool in ('clang', 'clang++')):
            return finish('BLOCKED', 'Faltan compiladores HOST clang/clang++ para contrato nativo', 'HARNESS')
        for assertion in assertions:
            result = _run_suite(SUITES[assertion])
            try:
                counts = json.loads(result.stdout)
                expected = {'tests', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'}
                if (type(counts) is not dict or set(counts) != expected
                        or any(type(n) is not int or n < 0 for n in counts.values())):
                    raise ValueError('Contadores de suite invalidos')
            except (ValueError, TypeError) as error:
                return finish('FAIL', f'{assertion}: salida invalida/truncada: {error}\n{result.stderr[-4000:]}', 'HARNESS')
            if result.returncode or counts['failures'] or counts['errors'] or counts['unexpected_successes']:
                return finish('FAIL', f'{assertion}: fallo de suite\n{result.stderr[-4000:]}', 'HARNESS')
            if not counts['tests']:
                return finish('FAIL', f'{assertion}: suite vacia; no hay prueba', 'HARNESS')
            if counts['skipped'] or counts['expected_failures']:
                return finish('BLOCKED', f'{assertion}: assertions omitidas o pendientes', 'HARNESS')
            executed.append(assertion)
        return finish('PASS', 'Contrato nativo, schemas, fixture y dispatcher probados; no emulacion', 'HARNESS')
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired) as error:
        return finish('FAIL', str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', required=True)
    parser.add_argument('--task', default='T04')
    parser.add_argument('--profile', default='deterministic-a500')
    parser.add_argument('--scenario', type=Path)
    parser.add_argument('--require', action='append', default=[], help='Assertion adicional exigida por la puerta')
    args = parser.parse_args()
    result = dispatch(args.check, args.task, args.profile, args.scenario, args.require)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())
