"""Infrastructure only; no compiler, emulator, network or commercial ROM runs."""
from pathlib import Path
import ast
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.ci import linux_native as ci


class LinuxNativeTests(unittest.TestCase):
    def test_recipe_keeps_assertions_two_jobs_and_all_real_targets(self):
        configure, build = ci.build_commands()
        self.assertIn("-DCMAKE_BUILD_TYPE=RelWithDebInfo", configure)
        for language in ("C", "CXX"):
            self.assertIn(f"-DCMAKE_{language}_FLAGS_RELWITHDEBINFO=-O2 -g", configure)
        self.assertNotIn("NDEBUG", " ".join(configure))
        self.assertEqual(build[-2:], ["-j", "2"])
        self.assertEqual(tuple(build[build.index("--target") + 1:-2]), ci.TARGETS)

    def test_wrong_runner_or_missing_tool_blocks_before_build(self):
        with mock.patch.object(ci.platform, "system", return_value="Darwin"), self.assertRaisesRegex(ValueError, "Linux"):
            ci.preflight()
        with mock.patch.object(ci.platform, "system", return_value="Linux"), mock.patch.object(ci.platform, "machine", return_value="x86_64"), mock.patch.object(ci.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "preinstalled"):
            ci.preflight()

    def test_all_fifteen_rows_use_their_own_compiler_isa_and_instrumentation(self):
        self.assertEqual(set(ci.HOSTED_RUNNERS), set(ci.ROWS))
        self.assertEqual(len(ci.HOSTED_RUNNERS) * len(ci.VARIANTS), 15)
        for row in ci.HOSTED_RUNNERS:
            for opt in ci.VARIANTS:
                with self.subTest(row=row, optimization=opt):
                    spec, mode = ci.selection(row, opt)
                    configure, build = ci.build_commands(row, opt)
                    self.assertIn('-DCMAKE_TOOLCHAIN_FILE='+str(ci.ROOT/spec['toolchain']), configure)
                    self.assertIn('-DCMAKE_BUILD_TYPE='+mode['build_type'], configure)
                    self.assertIn('-DP6_SANITIZERS='+('ON' if opt=='san' else 'OFF'), configure)
                    self.assertNotIn('NDEBUG', ' '.join(configure))
                    self.assertEqual(build[-2:], ['-j','2'])
                    system='Darwin' if row.startswith('macos-') else 'Linux'
                    isa='aarch64' if system=='Linux' and spec['isa']=='arm64' else spec['isa']
                    with mock.patch.object(ci.platform,'system',return_value=system), mock.patch.object(ci.platform,'machine',return_value=isa), mock.patch.object(ci.shutil,'which',return_value='/tool'):
                        ci.preflight(row,opt)
                    self.assertEqual(ci.compilers(row)[1], '/usr/bin/g++' if row.endswith('-gcc') else '/usr/bin/clang++')
                    self.assertEqual(ci.core_name(row), 'puae6_libretro'+('.dylib' if system=='Darwin' else '.so'))
        for row,opt in (('windows-x86_64','O2'),(ci.DEFAULT_ROW,'O3')):
            with self.assertRaisesRegex(ValueError,'unknown'):
                ci.build_commands(row,opt)

    def test_wrong_architecture_is_not_a_cross_build(self):
        with mock.patch.object(ci.platform,'system',return_value='Linux'), mock.patch.object(ci.platform,'machine',return_value='x86_64'), self.assertRaisesRegex(ValueError,'arm64'):
            ci.preflight('linux-arm64-clang','O0')

    def test_failed_process_or_protocol_violation_cannot_pass(self):
        for code, output in ((1, ""), (-9, "timeout"), (0, "VIOLATION")):
            with mock.patch.object(ci, "command", return_value=subprocess.CompletedProcess([], code, output, "")), self.assertRaises(RuntimeError):
                ci.execute(["unused"])
        for output in ("", "run 1 ticks=1\nrun 1 ticks=2", "run 1 malformed"):
            with self.assertRaises(RuntimeError):
                ci.rows(output, "run", 2 if "run 1 ticks" in output else 1)

    def test_native_future_requires_complete_hardware_video_and_pcm(self):
        def text(unit):
            return "\n".join([f"digest {unit} {name} {'a'*64} 4" for name in (*ci.HARDWARE, "video")]
                             + [f"chip {unit} {name} {'b'*64}" for name in (*ci.CHIP_GROUPS,"registers")]
                             + [f"pcm {unit} {'c'*64} 882", f"picture {unit} none"])
        self.assertEqual(ci.native_future(text(5), 5, 1), ci.native_future(text(1), 1, 1))
        for bad in ("", text(1).replace("digest 1 cpu", "ignored 1 cpu"), text(1).replace("pcm 1", "ignored 1")):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                ci.native_future(bad, 1, 1)

    def test_restore_excludes_only_the_existing_noncausal_debug_shadow(self):
        source=ast.parse((ci.ROOT/'tests/state/test_det02.py').read_text())
        groups=next(node.value for node in source.body if isinstance(node,ast.Assign)
                    and any(isinstance(target,ast.Name) and target.id=='CHIP_GROUPS' for target in node.targets))
        self.assertEqual(ci.CHIP_GROUPS,ast.literal_eval(groups))
        original='\n'.join([f'digest 1 {name} {"a"*64} 4' for name in (*ci.HARDWARE,'video')]
                           + [f'chip 1 {name} {"b"*64}' for name in (*ci.CHIP_GROUPS,'registers')]
                           + [f'pcm 1 {"c"*64} 882','picture 1 none'])
        changed=original.replace(f'digest 1 chipset {"a"*64}',f'digest 1 chipset {"f"*64}').replace(f'chip 1 registers {"b"*64}',f'chip 1 registers {"f"*64}')
        self.assertEqual(ci.native_future(original,1,1),ci.native_future(changed,1,1))
        changed=original.replace(f'chip 1 irq_dma {"b"*64}',f'chip 1 irq_dma {"f"*64}')
        self.assertNotEqual(ci.native_future(original,1,1),ci.native_future(changed,1,1))
        for bad in (original.replace('chip 1 irq_dma','unknown 1 irq_dma'),original+f'\nchip 1 beam {"b"*64}'):
            with self.assertRaisesRegex(RuntimeError,'incomplete'):
                ci.native_future(bad,1,1)

    def test_cold_native_must_load_at_zero_and_reemit_fullstates(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, data in (("cut.state", b"cut"), ("final.state", b"final"), ("again.state", b"cut"), ("restored.state", b"different")):
                (work / name).write_bytes(data)
            with mock.patch.object(ci, "execute", side_effect=["", "load 0 0\n"]) as execute, self.assertRaisesRegex(RuntimeError, "fullstate"):
                ci.check_native(work, work / "public.rom")
            args = execute.call_args_list[1].args[0]
            self.assertEqual(args[args.index("--public-load-at") + 1], "0")

    def test_manual_workflow_has_no_cache_upload_or_extra_action(self):
        workflow = (ci.ROOT / ".github/workflows/native-linux.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("runs-on: ${{ matrix.platform.runner }}", workflow)
        self.assertEqual(dict(re.findall(r"- row: (\S+)\n\s+runner: (\S+)",workflow)),ci.HOSTED_RUNNERS)
        variants=re.search(r"optimization: \[([^\]]+)\]",workflow)
        self.assertEqual(tuple(v.strip() for v in variants[1].split(',')),ci.VARIANTS)
        self.assertIn('fail-fast: false',workflow)
        self.assertIn('max-parallel: 4',workflow)
        self.assertIn('options: [all, san]',workflow)
        self.assertIn('default: all',workflow)
        self.assertIn('inputs.variants == \'san\' && \'[{"optimization":"O0"},{"optimization":"O2"}]\'',workflow)
        self.assertIn("--row '${{ matrix.platform.row }}' --opt '${{ matrix.optimization }}'",workflow)
        self.assertIn("timeout-minutes: 45", workflow)
        self.assertEqual(workflow.count("uses:"), 1)
        self.assertIn("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683", workflow)
        self.assertNotRegex(workflow, r"upload-artifact|actions/cache|secrets\.|pull_request:|schedule:|continue-on-error")
