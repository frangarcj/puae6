"""Public XML -> real CHD backend -> PUAE6 Waterbox, including SaveRAM reopen."""
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))
import build_waterbox as wb
from tests.media import test_cd_source_csharp as source, test_chd_reader as reader
from tests.media.test_chd_canonical_csharp import subq


class ChdPublicWaterboxTests(unittest.TestCase):
    def test_xml_chd_reads_saveram_and_cold_restore(self):
        receipt = wb.BUILD / 'puae6/puae6-receipt.json'
        guest = wb.BUILD / 'puae6/release/puae6.wbx'
        main, ext = os.environ.get('P6_CD32_ROM_MAIN'), os.environ.get('P6_CD32_ROM_EXT')
        if not all((main, ext, source.DOTNET, guest.is_file(), receipt.is_file(), wb.runtime_receipt_path().is_file())):
            raise RuntimeError('BLOCKED: verified guest/runtime, dotnet and authorized CD32 main/ext required')
        wb.check_guest_matches_trees(receipt)
        sha = json.loads(receipt.read_text())['guest']['sha256']
        self.assertEqual(wb.sha256(guest), sha)
        runtime = wb.read_runtime_receipt(wb.runtime_receipt_path())['runtime_dylib']
        self.assertEqual(wb.sha256(Path(runtime['path'])), runtime['sha256'])
        spec = importlib.util.spec_from_file_location('public_chd_recipe', ROOT / 'integration/bizhawk/patches/build_tree.py')
        recipe = importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
        recipe.MACHAWK = Path(os.environ.get('P6_MACHAWK_REFERENCE', str(recipe.MACHAWK)))
        recipe.git = functools.partial(recipe.git, cwd=recipe.MACHAWK)
        with tempfile.TemporaryDirectory(prefix='public-chd-wbx-', dir=ROOT / 'build') as temporary:
            work = Path(temporary); tree = work / 'client-common'
            recipe.check_patches(); recipe.export_tree(tree, recipe.EXPORT_EMUHAWK)
            recipe.apply_patches(tree); recipe.install_sources(tree)
            assembly = recipe.build(sha, tree, 'src/BizHawk.Client.Common/BizHawk.Client.Common.csproj',
                                    'src/BizHawk.Client.Common/bin/Debug/BizHawk.Client.Common.dll')
            project = work / 'Cd32Loader.csproj'
            shutil.copy2(ROOT / 'tests/media/csharp-disc/Cd32Loader.csproj', project)
            env = {**os.environ, 'BIZHAWK_HOME': str(work), 'NUGET_PACKAGES': str(ROOT / 'build/bizhawk-core/nuget'),
                   'DOTNET_CLI_HOME': str(ROOT / 'build/waterbox/dotnet-home'), 'DOTNET_NOLOGO': '1',
                   'DOTNET_ROLL_FORWARD': 'Major', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'}
            for name in ('DYLD_LIBRARY_PATH', 'LD_LIBRARY_PATH'): env.pop(name, None)
            result = subprocess.run([source.DOTNET, 'build', str(project), '-m:1', '-v:q', f'-p:Puae6Root={ROOT}',
                                     f'-p:CoreAssemblyDir={assembly.parent}', '-p:DefineConstants=PUBLIC_CHD',
                                     f'-p:RestoreSources={source.CACHE}'], env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = work / 'bin/Debug/net8.0'
            library = 'libp6_chd_host.dylib' if sys.platform == 'darwin' else 'libp6_chd_host.so'
            shutil.copy2(assembly.parent / library, output / library)
            (work / 'dll').mkdir(); shutil.copy2(guest, work / 'dll/puae6.wbx')
            pattern = reader.dependency.load('public_chd_pattern', ROOT / 'tests/fixtures/chd/generate.py')
            fixture = ROOT / 'tests/fixtures/chd/cdzl.chd'
            hashes = json.loads((fixture.parent / 'sha256.json').read_text())
            self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), hashes[fixture.name])
            shutil.copy2(fixture, work / 'disc.chd')
            audio = pattern.audio()
            (work / 'wire.bin').write_bytes(b''.join(audio[n*2352:(n+1)*2352] + subq(1, 1, n, 0, 0) for n in range(40)))
            # A second valid v5 file with recomputed map CRC and different audio, not a corrupt container.
            changed = bytearray(pattern.expected()); changed[0] ^= 1
            metadata = b'TRACK:1 TYPE:AUDIO SUBTYPE:NONE FRAMES:40 PREGAP:0 PGTYPE:AUDIO PGSUB:NONE POSTGAP:0'
            (work / 'other.chd').write_bytes(reader.metadata_chain(reader.cd_map([(4, bytes(changed))], codec=b'cdzl', sector=changed),
                                                                  [metadata], tag=0x43485432))
            for name in ('disc', 'other'):
                (work / (name + '.xml')).write_text(f'<BizHawk-XMLGame System="Amiga" Name="legal"><LoadAssets>'
                                                   f'<LoadAsset FileName="{name}.chd"/></LoadAssets></BizHawk-XMLGame>')
            for mode in ('chdpublicsave', 'chdpublicload'):
                result = subprocess.run([source.DOTNET, str(output / 'Cd32Loader.dll'), str(work), str(work / 'disc.xml'),
                                         runtime['path'], str(guest), sha, mode, main, ext], env=env,
                                        capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
                self.assertIn('Public CHD XML Waterbox reads/SaveRAM/cold owner/new process/full future/FD close PASS', result.stdout)
