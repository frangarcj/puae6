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
    # and ROM path change nothing) are the one module tests.portability.test_portability. Its coherence tests always run (the report, plan/runners.json,
    # the toolchain files and the recorded evidence say the same thing); the tests that build and run the rows are opt-in (P6_TEST_PORTABILITY=1: the
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
    # T32c: the differential corpus in the portability matrix (P6_MATRIX_SET=differential: its own programs, media and evidence file, evidence-differential.json), the same rows and
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
# T34 optimizes what the adapters do around the engine. What the measurement found (reports/optimizations/T34.md) is in the BizHawk core (the names asked of the controller and the copy of
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
TASK_SUITE_TIMEOUT = {('DET01', 'T34'): 4 * 3600, ('DET02', 'T34'): 4 * 3600, ('SAFE01', 'T34'): 4 * 3600,
                      ('DET04', 'T25'): 6 * 3600, ('DET06', 'T25'): 6 * 3600,
                      ('DET04', 'T32c'): 6 * 3600, ('DET06', 'T32c'): 6 * 3600,
                      ('DET04', 'T32'): 6 * 3600, ('DET06', 'T32'): 6 * 3600}
TASK_SUITE_ENV = {'P6_TEST_SANITIZERS': '1', 'P6_RELEASE_GATE': '1'}      # the second is read only by the OpenDivergenceGate tests of T32
DRIVER = '''import json, pathlib, sys, unittest
# T34a: the suites of tests/waterbox-* import their neighbours by the bare name (discover -s DIR puts the directory on the path): so does loading them by dotted name
_file = pathlib.Path(*sys.argv[1].split('.')).with_suffix('.py')
if _file.is_file():
    sys.path.append(str(_file.parent.resolve()))
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
