"""Package policy checks and opt-in execution of the actual installed preview."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools import package


class PolicyTests(unittest.TestCase):
    def test_sources_do_not_ship_reference_disks_capsimg_or_private_builds(self):
        paths = package.source_paths()
        self.assertIn('upstream/winuae', paths)
        self.assertIn('third_party', paths)
        self.assertIn('references/libretro-api', paths)
        self.assertNotIn('references', paths)
        self.assertNotIn('build', paths)
        policy = json.loads(package.POLICY.read_text())
        for path in policy['licenses'].values():
            self.assertTrue((package.ROOT / path).is_file(), path)

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
