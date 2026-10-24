"""T30a, MED03: two legal preparations from the same inputs make the same image and the same manifest, missing licences and assets are visible, nothing of an input is run and the replay
mounts no host folder.

The inputs here are synthetic (an LHA written by lha_fixture.py and directories); the real ones, WHDLoad's package and Fatal Mission's install, are the opt-in tests of test_real.py.
"""
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from tests.whdload import lha_fixture as fx
import prepare_whdload as prep
from whdload import ffs

ROOT = Path(__file__).resolve().parents[2]
GAME = {"Game/Game.slave": b"slave" * 100, "Game/data/level1": bytes(range(256)) * 20, "Game/readme": b"Public domain.\n"}
TOOL = {"Tool/C/Run": b"\x00\x00\x03\xf3" * 300, "Tool/Docs/copy": b"Copyright.\n"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(members, level=1):
    out = []
    for path, data in members.items():
        directory, _, name = path.rpartition("/")
        out.append(fx.member(name.encode(), data, b"-lh5-", level, (directory.replace("/", "\xff") + "\xff").encode("latin-1") if directory else b""))
    return fx.archive(*out)


def config(**overrides):
    base = {
        "schema_version": 1, "id": "synthetic",
        "volume": {"name": "Synth", "dostype": "DOS1", "min_kib": 256, "timestamp": "2026-01-01T00:00:00Z"},
        "inputs": {"tool": {"kind": "lha", "env": "P6_TEST_TOOL", "license": "whdload", "source_url": "https://example.invalid/tool.lha"},
                   "game": {"kind": "lha", "env": "P6_TEST_GAME", "license": "fatal-mission"}},
        "files": [
            {"to": "C/Run", "from": "tool:Tool/C/Run", "sha256": sha(TOOL["Tool/C/Run"]), "role": "tool", "license": "whdload"},
            {"to": "Game/Game.slave", "from": "game:Game/Game.slave", "sha256": sha(GAME["Game/Game.slave"]), "role": "game-data", "license": "fatal-mission"},
            {"to": "Game/data/level1", "from": "game:Game/data/level1", "sha256": sha(GAME["Game/data/level1"]), "role": "game-data", "license": "fatal-mission"},
            {"to": "S/Startup-Sequence", "text": ["Run Game/Game.slave", "Echo done >DH0:done"], "role": "generated", "license": "generated"},
        ],
        "user_assets": [],
    }
    base.update(overrides)
    return base


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="p6-whd-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.game, self.tool = self.temp / "game.lha", self.temp / "tool.lha"
        self.game.write_bytes(archive(GAME))
        self.tool.write_bytes(archive(TOOL, 2))
        self.inputs = {"game": str(self.game), "tool": str(self.tool)}

    def make(self, cfg=None, inputs=None, assets=None, **kwargs):
        return prep.prepare(cfg or config(), self.inputs if inputs is None else inputs, assets or {}, **kwargs)

    def fails(self, fragment, cfg=None, **kwargs):
        with self.assertRaises(prep.PrepareError) as caught:
            self.make(cfg, **kwargs)
        self.assertIn(fragment, str(caught.exception))
        return str(caught.exception)


class DeterminismTests(Case):
    def test_two_preparations_with_the_same_inputs_are_the_same_bytes_and_the_same_manifest(self):
        a, b = self.make(), self.make()
        self.assertEqual(a[0], b[0])
        self.assertEqual(prep.encode_manifest(a[1]), prep.encode_manifest(b[1]))
        volume = ffs.read_volume(a[0])
        self.assertEqual({"C/Run": TOOL["Tool/C/Run"], "Game/Game.slave": GAME["Game/Game.slave"], "Game/data/level1": GAME["Game/data/level1"],
                          "S/Startup-Sequence": b"Run Game/Game.slave\nEcho done >DH0:done\n"}, volume.files)

    def test_the_paths_the_clock_and_the_directory_the_tool_runs_in_change_nothing(self):
        first = self.make()
        other = self.temp / "elsewhere" / "deeper"
        other.mkdir(parents=True)
        moved = {"game": str(other / "renamed-game.dat"), "tool": str(other / "t.lha")}
        shutil.copy(self.game, moved["game"])
        shutil.copy(self.tool, moved["tool"])
        os.utime(moved["game"], (1, 1))
        os.utime(moved["tool"], (4102444800, 4102444800))
        before = os.getcwd()
        os.chdir(other)
        self.addCleanup(os.chdir, before)
        second = self.make(inputs=moved)
        self.assertEqual(first[0], second[0])
        self.assertEqual(prep.encode_manifest(first[1]), prep.encode_manifest(second[1]))

    def test_a_directory_input_gives_the_image_of_the_archive_whatever_its_creation_order_and_times(self):
        folder = self.temp / "installed"
        for index, path in enumerate(sorted(GAME, reverse=True)):
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(GAME[path])
            os.utime(target, (index * 1000, index * 7777))
        cfg = config()
        cfg["inputs"]["game"]["kind"] = "dir"
        cfg["files"][1]["from"], cfg["files"][2]["from"] = "game:Game/Game.slave", "game:Game/data/level1"
        # the directory holds Game/..., exactly the member names of the archive
        from_dir = self.make(cfg, inputs={"game": str(folder), "tool": str(self.tool)})
        self.assertEqual(self.make()[0], from_dir[0])
        self.assertEqual([None, "lha"], [r["sha256"] and "lha" for r in from_dir[1]["inputs"]])

    def test_the_manifest_has_no_host_path_no_user_and_no_time_but_the_config_s(self):
        text = prep.encode_manifest(self.make()[1])
        for forbidden in (str(self.temp), str(Path.home()), os.environ.get("USER", "\0"), "tmp", "/Users"):
            self.assertNotIn(forbidden, text)
        self.assertEqual({"2026-01-01T00:00:00Z"}, set(re.findall(r"\d{4}-\d\d-\d\dT[\d:]+Z?", text)))
        manifest = json.loads(text)
        self.assertEqual(sorted(manifest["files"], key=lambda f: f["path"]), manifest["files"])
        self.assertEqual(["C/Run", "Game/Game.slave", "Game/data/level1", "S/Startup-Sequence"], [f["path"] for f in manifest["files"]])
        self.assertEqual(hashlib.sha256(self.make()[0]).hexdigest(), manifest["image"]["sha256"])

    def test_a_pinned_image_hash_is_enforced(self):
        image, manifest, _ = self.make()
        cfg = config(image_sha256=manifest["image"]["sha256"])
        self.make(cfg)
        cfg["image_sha256"] = "0" * 64
        self.fails("identifies", cfg)

    def test_the_config_s_inputs_decide_the_manifest(self):
        changed = config()
        changed["volume"]["name"] = "Other"
        self.assertNotEqual(self.make()[1]["config_sha256"], self.make(changed)[1]["config_sha256"])


class VisibleFailureTests(Case):
    def test_a_file_that_is_not_the_pinned_one_names_both_hashes(self):
        cfg = config()
        cfg["files"][0]["sha256"] = "0" * 64
        message = self.fails("another version", cfg)
        self.assertIn(sha(TOOL["Tool/C/Run"]), message)
        self.assertIn("0" * 64, message)

    def test_a_member_that_is_not_in_the_input(self):
        cfg = config()
        cfg["files"][1]["from"] = "game:Game/Missing.slave"
        self.fails("Missing.slave is not in input game", cfg)

    def test_a_licence_without_evidence_stops_the_preparation(self):
        cfg = config()
        cfg["files"][0]["license"] = "unknown-licence"
        self.fails("unknown-licence", cfg)

    def test_a_missing_input_is_named_with_where_it_comes_from_and_nothing_is_written(self):
        message = self.fails("input tool", inputs={"game": str(self.game)})
        self.assertIn("P6_TEST_TOOL", message)
        self.assertIn("https://example.invalid/tool.lha", message)
        out = self.temp / "out"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = prep.main(["prepare", str(self.write_config(config())), "--input", "game=" + str(self.game), "--out", str(out)])
        self.assertEqual(2, rc)
        self.assertIn("input tool", err.getvalue())
        self.assertFalse(list(out.glob("*")) if out.exists() else [])

    def write_config(self, cfg):
        path = self.temp / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_allowing_missing_assets_still_says_incomplete_and_exits_with_3(self):
        out = self.temp / "out"
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            rc = prep.main(["prepare", str(self.write_config(config())), "--input", "game=" + str(self.game), "--out", str(out), "--allow-missing-assets"])
        self.assertEqual(3, rc)
        self.assertIn("INCOMPLETE", err.getvalue())
        manifest = json.loads((out / "synthetic.manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["complete"])
        self.assertTrue(any("input tool" in m for m in manifest["missing"]))
        self.assertTrue((out / "synthetic.hdf").is_file())

    def test_a_corrupt_archive_and_an_unknown_config_key(self):
        broken = self.temp / "broken.lha"
        broken.write_bytes(self.tool.read_bytes()[:-30])
        self.fails("not a usable LHA archive", inputs={"game": str(self.game), "tool": str(broken)})
        with self.assertRaises(prep.PrepareError):
            prep.load_config(self.write_config(dict(config(), surprise=1)))
        cfg = config()
        cfg["files"][3]["from"] = "tool:x"
        with self.assertRaises(prep.PrepareError):
            prep.load_config(self.write_config(cfg))

    def test_two_files_for_one_place_and_names_that_are_not_amigados_names(self):
        cfg = config()
        cfg["files"].append(dict(cfg["files"][0], to="c/run"))
        self.fails("placed twice", cfg)
        cfg = config()
        cfg["files"][0]["to"] = "C/" + "x" * 31
        self.fails("AmigaDOS name", cfg)


class UserAssetTests(Case):
    def asset_config(self):
        cfg = config()
        cfg["user_assets"] = [{"to": "Devs/Kickstarts/kick.rom", "env": "P6_TEST_KICK", "size": 4096, "why": "the slave asks for the owner's Kickstart", "license": "user-kickstart"}]
        return cfg

    def test_a_missing_asset_is_named_with_its_reason_and_variable(self):
        message = self.fails("Devs/Kickstarts/kick.rom", self.asset_config())
        self.assertIn("P6_TEST_KICK", message)
        self.assertIn("the slave asks for the owner's Kickstart", message)

    def test_a_given_asset_goes_into_the_image_is_not_hashed_and_makes_the_image_non_redistributable(self):
        secret = bytes((i * 31 + 7) & 255 for i in range(4096))
        path = self.temp / "owner.rom"
        path.write_bytes(secret)
        image, manifest, missing = self.make(self.asset_config(), assets={"Devs/Kickstarts/kick.rom": str(path)})
        self.assertEqual([], missing)
        self.assertEqual(secret, ffs.read_volume(image).files["Devs/Kickstarts/kick.rom"])
        self.assertNotIn(sha(secret), prep.encode_manifest(manifest))
        self.assertEqual([{"license": "user-kickstart", "path": "Devs/Kickstarts/kick.rom", "size": 4096}], manifest["user_assets"])
        self.assertFalse(manifest["redistributable"])
        path.write_bytes(secret[:100])
        self.fails("expected 4096", self.asset_config(), assets={"Devs/Kickstarts/kick.rom": str(path)})

    def test_licences_that_are_not_free_make_the_image_non_redistributable(self):
        self.assertFalse(self.make()[1]["redistributable"])          # the licences of WHDLoad and of the game are "unverified"
        cfg = config()
        cfg["files"], cfg["inputs"] = cfg["files"][-1:], {}
        self.assertTrue(self.make(cfg, inputs={})[1]["redistributable"])         # only text generated here


class NoExecutionTests(Case):
    def test_nothing_of_an_input_can_run_the_preparation_never_starts_a_process(self):
        forbidden_imports = {"subprocess", "ctypes", "socket", "urllib", "multiprocessing", "pty", "importlib"}
        forbidden_calls = {"exec", "eval", "compile", "__import__", "system", "popen", "spawn", "execv", "run", "Popen", "check_output"}
        for path in [ROOT / "tools/prepare_whdload.py", *sorted((ROOT / "tools/whdload").glob("*.py"))]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({a.name.split(".")[0] for a in node.names} & forbidden_imports, path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], forbidden_imports, path.name)
                elif isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    self.assertNotIn(name, forbidden_calls, (path.name, node.lineno))

    def test_a_script_inside_an_input_is_data_the_config_decides_what_boots(self):
        hostile = dict(GAME, **{"S/Startup-Sequence": b"Delete DH0:#? ALL\n", "Game/run.sh": b"#!/bin/sh\ntouch /tmp/pwned\n"})
        self.game.write_bytes(archive(hostile))
        image = self.make()[0]
        self.assertEqual(b"Run Game/Game.slave\nEcho done >DH0:done\n", ffs.read_volume(image).files["S/Startup-Sequence"])
        self.assertNotIn("Game/run.sh", ffs.read_volume(image).files)

    def test_a_directory_input_never_follows_a_link(self):
        folder = self.temp / "installed"
        (folder / "Game").mkdir(parents=True)
        (folder / "Game/Game.slave").write_bytes(GAME["Game/Game.slave"])
        os.symlink("/etc/hosts", folder / "Game/link")
        cfg = config()
        cfg["inputs"]["game"]["kind"] = "dir"
        self.fails("link or not a regular file", cfg, inputs={"game": str(folder), "tool": str(self.tool)})


class CommandLineTests(Case):
    def test_prepare_then_identify_and_inspect(self):
        out = self.temp / "out"
        cfg = self.temp / "config.json"
        cfg.write_text(json.dumps(config()), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, prep.main(["prepare", str(cfg), "--input", "game=" + str(self.game), "--input", "tool=" + str(self.tool), "--out", str(out)]))
        manifest = json.loads((out / "synthetic.manifest.json").read_text(encoding="utf-8"))
        image = out / "synthetic.hdf"
        self.assertEqual(manifest["image"]["sha256"], sha(image.read_bytes()))
        self.assertEqual(manifest["image"]["size"], image.stat().st_size)
        self.assertFalse(list(out.glob("*.part")))
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            self.assertEqual(0, prep.main(["identify", str(image), "--sha256", manifest["image"]["sha256"]]))
            self.assertEqual(0, prep.main(["inspect", str(image)]))
        self.assertIn("Game/Game.slave", text.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, prep.main(["identify", str(image), "--sha256", "0" * 64]))


class RepositoryConfigTests(unittest.TestCase):
    def test_every_config_loads_and_its_licences_have_evidence(self):
        licenses = prep.load_licenses()
        configs = sorted((ROOT / "config/whdload").glob("*.json"))
        self.assertTrue(configs)
        for path in configs:
            cfg = prep.load_config(path)
            used = {i["license"] for i in cfg["inputs"].values()} | {f["license"] for f in cfg["files"]} | {a["license"] for a in cfg.get("user_assets", [])}
            self.assertFalse(used - set(licenses), path.name)
            self.assertTrue(all(licenses[name]["license_evidence"] for name in used), path.name)

    def test_the_repository_holds_no_game_tool_or_kickstart_bytes(self):
        for path in (ROOT / "config/whdload").rglob("*"):
            if path.is_file():
                self.assertEqual(".json", path.suffix, path.name)
                self.assertLess(path.stat().st_size, 64 * 1024, path.name)
        for path in (ROOT / "config/whdload").glob("*.json"):
            for entry in json.loads(path.read_text(encoding="utf-8"))["files"]:
                self.assertIn(entry["role"], ("tool", "game-data", "generated"))
                if entry["role"] != "generated":
                    self.assertIn("from", entry)             # data is named and pinned, never embedded

    def test_the_replay_is_given_images_and_mounts_no_host_folder(self):
        header = (ROOT / "bridge/include/p6.h").read_text(encoding="utf-8")
        struct = re.search(r"typedef struct p6_media \{(.*?)\} p6_media;", header, re.S).group(1)
        self.assertEqual(["type", "flags", "content_id", "image"], re.findall(r"(\w+)(?:\[[^\]]*\])?;", re.sub(r"/\*.*?\*/", "", struct, flags=re.S)))
        self.assertEqual({"P6_MEDIA_ADF", "P6_MEDIA_HDF"}, set(re.findall(r"#define (P6_MEDIA_(?:ADF|HDF|DIR\w*|FS\w*|FOLDER\w*|HOST\w*))\b", header)))
        patch = (ROOT / "patches/winuae/0163-filesys-hard-files-only.patch").read_text(encoding="utf-8")
        self.assertIn("only hard files on the UAE controller are supported", patch)
        self.assertIn("host file system", (ROOT / "ports/portable/hostfs_unsupported.cpp").read_text(encoding="utf-8"))
        for adapter in ("adapters", "bridge"):
            for path in (ROOT / adapter).rglob("*"):
                if path.suffix in (".cpp", ".h", ".c", ".cs") and path.is_file():
                    self.assertNotIn("prepare_whdload", path.read_text(encoding="utf-8", errors="replace"), str(path))


if __name__ == "__main__":
    unittest.main()
