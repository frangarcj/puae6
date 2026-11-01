import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LONGHAWK = Path(os.environ.get("P6_LONGHAWK_PUAE61", ROOT / "build/longhawk-puae61")).resolve()
INTEGRATION = ROOT / "integration/longhawk"
BASE = "8de36990487237db3c50bf446e6af69671a89a9e"
GUEST_SHA = "a244f88642163ab6163d92d3ba3ed004a5e02c5eb2f94b99f41f89484c80a2b2"
SOURCES_SHA = "dfaf5c5ec4fc81cc95ccc59f4ce5e88fb2c74e11d1784bdb1fa817e15aff0c0b"


def digest(path):
	with path.open("rb") as stream:
		return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args, cwd=ROOT):
	return subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class LongHawkPuae61Tests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		if not (LONGHAWK / ".git").exists():
			raise AssertionError(f"falta el worktree LongHawk de T100: {LONGHAWK}")

	def test_three_distinct_product_identities(self):
		config = (LONGHAWK / "src/BizHawk.Client.Common/config/Config.cs").read_text(encoding="utf-8-sig")
		core = (LONGHAWK / "src/BizHawk.Emulation.Cores/Computers/Amiga/PUAE6/Puae6Core.EmuHawk.cs").read_text()
		movie = (LONGHAWK / "src/BizHawk.Emulation.Cores/Computers/Amiga/PUAE6/Movie.cs").read_text()
		loader = (LONGHAWK / "src/BizHawk.Client.Common/RomLoader.cs").read_text()
		tests = (LONGHAWK / "src/BizHawk.Tests.Client.Common/config/CorePickerStabilityTests.cs").read_text(encoding="utf-8-sig")
		self.assertIn("[ CoreNames.UAE, CoreNames.PUAE6, CoreNames.PUAE61 ]", config)
		self.assertIn("Assert.AreEqual(CoreNames.UAE, DefaultCorePrefDict[VSystemID.Raw.Amiga])", tests)
		self.assertIn("public sealed class Puae61Core : Puae6Core", core)
		self.assertIn('base(lp, Puae61Names.Core, "puae61.wbx"', core)
		self.assertIn('BuiltWithSha256For("Puae61GuestSha256")', core)
		self.assertIn('["Core"] = coreName', movie)
		self.assertIn("recorded != coreName", movie)
		self.assertIn("coreName == CoreNames.PUAE6 || coreName == CoreNames.PUAE61", loader)

	def test_guest_and_corresponding_sources_are_pinned(self):
		guest = LONGHAWK / "waterbox/puae61/puae61-x64.wbx"
		sources = LONGHAWK / "waterbox/puae61/source.tar.gz"
		self.assertEqual(GUEST_SHA, digest(guest))
		self.assertEqual(SOURCES_SHA, digest(sources))
		self.assertNotEqual(digest(LONGHAWK / "waterbox/puae6/puae6-x64.wbx"), digest(guest))
		with tarfile.open(sources, "r:gz") as archive:
			lock = json.load(archive.extractfile("source/plan/upstream.lock.json"))
			members = set(archive.getnames())
		winuae = next(source for source in lock["sources"] if source["id"] == "winuae")
		self.assertEqual("1fb263778ca0b6a6d02993acf900044522fcb76a", winuae["commit"])
		self.assertIn("source/patches/winuae/series.json", members)

	def test_recipe_applies_to_the_pinned_clean_base(self):
		worktree = Path(tempfile.mkdtemp(prefix="longhawk-puae61-recipe-", dir=ROOT / "build"))
		shutil.rmtree(worktree)
		try:
			run("git", "worktree", "add", "--detach", "--no-checkout", str(worktree), BASE, cwd=LONGHAWK)
			run("git", "sparse-checkout", "init", "--cone", cwd=worktree)
			run("git", "sparse-checkout", "set", ".github", "Dist", "src/BizHawk.Client.Common", "src/BizHawk.Client.EmuHawk",
				"src/BizHawk.Emulation.Cores", "src/BizHawk.Tests.Client.Common", "waterbox/puae6", cwd=worktree)
			run("git", "checkout", "--detach", BASE, cwd=worktree)
			run("python3", str(INTEGRATION / "apply_puae61.py"), str(worktree),
				str(LONGHAWK / "waterbox/puae61/puae61-x64.wbx"), str(LONGHAWK / "waterbox/puae61/source.tar.gz"))
			self.assertEqual(GUEST_SHA, digest(worktree / "waterbox/puae61/puae61-x64.wbx"))
			self.assertEqual(SOURCES_SHA, digest(worktree / "waterbox/puae61/source.tar.gz"))
			status = run("git", "status", "--porcelain", "--untracked-files=all", cwd=worktree).stdout
			self.assertIn("waterbox/puae61/source.tar.gz", status)
			ignored = run("git", "check-ignore", "waterbox/puae61/puae61-x64.wbx", cwd=worktree).stdout.strip()
			self.assertEqual("waterbox/puae61/puae61-x64.wbx", ignored)
		finally:
			subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=LONGHAWK,
				text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			if worktree.exists():
				shutil.rmtree(worktree)

	def test_managed_frontend_builds_with_both_pins(self):
		result = run(
			"dotnet", "build", "src/BizHawk.Client.EmuHawk/BizHawk.Client.EmuHawk.csproj",
			"-c", "Release", "--nologo", "-p:InstallGitHooks=false",
			"-p:Puae6GuestSha256=ff64ace000e297e82e06324303fa5dbf5ebf418ce9f0ec83f0efae1023c11d7d",
			f"-p:Puae61GuestSha256={GUEST_SHA}", cwd=LONGHAWK)
		self.assertIn("0 Errores", result.stdout)
		self.assertTrue((LONGHAWK / "output/EmuHawk.exe").is_file())


if __name__ == "__main__":
	unittest.main()
