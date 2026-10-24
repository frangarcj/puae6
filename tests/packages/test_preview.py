"""Package policy checks and opt-in execution of the actual installed preview."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import package


class PolicyTests(unittest.TestCase):
    def test_sources_do_not_ship_reference_disks_capsimg_or_private_builds(self):
        paths = package.source_paths()
        self.assertIn('upstream/winuae', paths)
        self.assertIn('third_party', paths)
        self.assertIn('Directory.Build.targets', paths)
        self.assertIn('references/libretro-api', paths)
        self.assertNotIn('references', paths)
        self.assertNotIn('build', paths)
        policy = json.loads(package.POLICY.read_text())
        for spec in policy['licenses'].values():
            for path in ([spec] if isinstance(spec, str) else [p['path'] for p in spec]):
                self.assertTrue((package.ROOT / path).is_file(), path)

    def test_selected_git_snapshot_ignores_python_and_build_outputs_but_rejects_new_sources(self):
        (package.ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='source-selection-', dir=package.ROOT / 'build') as temp:
            origin, snapshot = Path(temp) / 'origin', Path(temp) / 'snapshot'
            origin.mkdir(); snapshot.mkdir()
            files = {'.gitignore': (package.ROOT / '.gitignore').read_bytes(), 'README.md': b'own fixture',
                     'tools/__init__.py': b'', 'tools/fixture.py': b'VALUE = 1\n'}
            for name, data in files.items():
                path = origin / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            def git(root, *args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
            def commit(root):
                git(root, 'init', '-q'); git(root, 'add', '.')
                git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'own sources')
            commit(origin)
            with mock.patch.object(package, 'ROOT', origin):
                selected = git(origin, 'ls-files', '--', *package.source_paths()).decode().splitlines()
            archive = git(origin, 'archive', 'HEAD', '--', *selected)
            with tarfile.open(fileobj=io.BytesIO(archive)) as stream: stream.extractall(snapshot, filter='data')
            commit(snapshot)
            with mock.patch.object(package, 'ROOT', snapshot):
                revision = package.clean_revision()
                env = {k: v for k, v in os.environ.items() if k not in ('PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX')}
                subprocess.run([sys.executable, '-c', 'import tools.fixture'], cwd=snapshot, env=env, check=True)
                output = snapshot / 'build/fixture.o'; output.parent.mkdir(); output.write_bytes(b'own build output')
                self.assertTrue(list(snapshot.rglob('*.pyc')))
                self.assertEqual(revision, package.clean_revision())
                self.assertIn('.gitignore', selected)
                (snapshot / 'unexpected-source.py').write_text('new source')
                with self.assertRaisesRegex(ValueError, 'Commit the public sources'): package.clean_revision()

    def test_linked_component_notices_and_inclusive_slices_preserve_bytes(self):
        policy=json.loads(package.POLICY.read_text()); notices=package.notice_files(policy,lambda p:(package.ROOT/p).read_bytes())
        for name,author in (('LIBCHDR-BSD.txt',b'Romain Tisserand'),('LIBCHDR-AARON-GILES.txt',b'Aaron Giles'),
                           ('ZLIB.txt',b'Mark Adler'),('LZMA-PD.txt',b'Igor Pavlov'),('DR-FLAC-MIT-0.txt',b'David Reid')):
            self.assertIn(author,notices[name])
        for text in (b'John R. Hauser',b'Fabrice Bellard',b'Andreas Grabher',b'GNU GPL'):
            self.assertIn(text,notices['SOFTFLOAT-NOTICES.txt'])
        tiny={'licenses':{'n':[{'path':'p','lines':[2,3]}]}}
        self.assertEqual(package.notice_files(tiny,lambda p:b'a\r\nb\nc\r\n'),{'n':b'Source: p\nb\nc\r\n'})
        for bounds in ([0,1],[2,1],[1,4],[1,2.0]):
            tiny['licenses']['n'][0]['lines']=bounds
            with self.assertRaisesRegex(ValueError,'slice'): package.notice_files(tiny,lambda p:b'a\nb\nc\n')

    def test_archive_policy_rejects_missing_or_changed_notice_with_updated_manifest(self):
        policy=json.loads(package.POLICY.read_text()); read=lambda p:(package.ROOT/p).read_bytes()
        (package.ROOT/'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='package-policy-',dir=package.ROOT/'build') as temp:
            stage=Path(temp); (stage/'licenses').mkdir()
            for name,data in package.notice_files(policy,read).items(): (stage/'licenses'/name).write_bytes(data)
            sources={'tools/package.py',str(package.POLICY.relative_to(package.ROOT))}
            for spec in policy['licenses'].values(): sources.update([spec] if isinstance(spec,str) else [p['path'] for p in spec])
            with tarfile.open(stage/'source.tar.gz','w:gz') as archive:
                for path in sorted(sources): archive.add(package.ROOT/path,arcname='source/'+path)
            def manifest():
                files={p.relative_to(stage).as_posix():package.sha(p) for p in stage.rglob('*') if p.is_file() and p.name!='manifest.json'}
                (stage/'manifest.json').write_text(json.dumps({'platform':'macos-arm64','files':files}))
            # Only notice policy is under test; InstalledPreviewTests exercises binaries.
            with mock.patch.object(package,'verify_binaries'):
                manifest(); package.verify(stage)
                target=stage/'licenses/SOFTFLOAT-NOTICES.txt'; original=target.read_bytes()
                for remove in (True,False):
                    if remove: target.unlink()
                    else: target.write_bytes(original+b'changed')
                    manifest()
                    with self.assertRaisesRegex(ValueError,'notice'): package.verify(stage)
                    target.write_bytes(original)

    def test_archive_path_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('../outside', b'x')
            with self.assertRaisesRegex(ValueError, 'member'):
                package.unpack(path, Path(directory) / 'extract')


@unittest.skipUnless(os.environ.get('P6_PACKAGE_ZIP'), 'P6_PACKAGE_ZIP requires the actual native preview')
class InstalledPreviewTests(unittest.TestCase):
    def test_extracted_core_advances_restores_and_rejects_missing_firmware(self):
        with tempfile.TemporaryDirectory(prefix='p6-package-test-') as directory:
            package.unpack(os.environ['P6_PACKAGE_ZIP'], directory)
            result = package.smoke(directory)
        self.assertEqual(result['restored_units_compared'], 3)
        self.assertTrue(result['missing_firmware_rejected'])
