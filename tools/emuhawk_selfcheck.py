#!/usr/bin/env python3
"""Execute the actual complete EmuHawk EXE's explicit CLI; never build implicitly."""
import argparse
import base64
import io
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("emuhawk_build_tree", ROOT / "integration/bizhawk/patches/build_tree.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
MARKER = "P6_EMUHAWK_BOOTSTRAP "
FRAMEWORK = ".NETFramework,Version=v4.8"
AMIGA = [{"name": "PUAE6", "type": "Puae6.Puae6Core", "priority": 100},
         {"name": "UAE", "type": "BizHawk.Emulation.Cores.Computers.Amiga.UAE", "priority": 0}]



def runtime_json_asset():
    # .NET 10 needs the modern asset of the SAME pinned package, not its CAS/net45 asset.
    packages = json.loads((ROOT / "tools/ci/managed-inputs.json").read_text())["packages"]
    selected = [p for p in packages if p["id"] == "newtonsoft.json"]
    if len(selected) != 1 or selected[0]["version"] != "13.0.3":
        raise ValueError("Unexpected Newtonsoft runtime package")
    pin = selected[0]
    archive = ROOT / "build/hosted-managed-feed/newtonsoft.json.13.0.3.nupkg"
    if archive.is_symlink() or not archive.is_file():
        raise FileNotFoundError("Pinned offline Newtonsoft archive is required")
    data = archive.read_bytes()
    if len(data) != pin["bytes"] or base64.b64encode(hashlib.sha512(data).digest()).decode() != pin["archive_sha512_base64"]:
        raise ValueError("Newtonsoft archive differs from the pinned bytes/SHA512")
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        asset = package.read("lib/net6.0/Newtonsoft.Json.dll")
    return asset


def validate(record, stage, files):
    expected = {"schema_version": 1, "kind": "emuhawk-cli-bootstrap", "runtime": "10.0.13",
        "entry_name": "BizHawk.Client.EmuHawk", "entry_type": "BizHawk.Client.EmuHawk.Program",
        "entry_path": str(stage / "EmuHawk.exe"), "entry_sha256": files["EmuHawk.exe"],
        "cores_path": str(stage / "dll/BizHawk.Emulation.Cores.dll"),
        "cores_sha256": files["dll/BizHawk.Emulation.Cores.dll"], "framework": FRAMEWORK,
        "cores_framework": FRAMEWORK, "amiga": AMIGA}
    if (set(record) != set(expected) | {"types_count", "types_sha256"}
            or any(record.get(k) != v for k, v in expected.items())
            or type(record["types_count"]) is not int or record["types_count"] < 2
            or not isinstance(record["types_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", record["types_sha256"])):
        raise ValueError("EmuHawk provenance/framework/inventory differs")


def run(*, without_virtu=False):
    builder.check_source()
    output = builder.TREE_EMUHAWK / "output"
    receipt = output / "bootstrap-receipt.json"
    if receipt.is_symlink() or not receipt.is_file():
        raise FileNotFoundError("Build the current EmuHawk tree with --emuhawk --skip-chd-host first")
    files = builder.output_closure(output)
    if json.loads(receipt.read_text()) != {"tree_key": builder.tree_key(), "commit": builder.pinned_commit(), "files": files}:
        raise ValueError("EmuHawk build receipt differs")
    if "dll/Virtu.dll" not in files:
        raise ValueError("EmuHawk closure lacks Virtu.dll")
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise FileNotFoundError("dotnet with Microsoft.NETCore.App 10.0.13 is required")
    dotnet = Path(dotnet).resolve()
    host_sha = hashlib.sha256(dotnet.read_bytes()).hexdigest()
    json_asset = runtime_json_asset()
    json_sha = hashlib.sha256(json_asset).hexdigest()
    with tempfile.TemporaryDirectory(prefix="emuhawk-selfcheck-", dir=ROOT / "build") as temporary:
        stage = Path(temporary)
        for name in files:
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output / name, path)
        expected = dict(files)
        (stage / "dll/Newtonsoft.Json.dll").write_bytes(json_asset)
        expected["dll/Newtonsoft.Json.dll"] = json_sha
        if without_virtu:
            (stage / "dll/Virtu.dll").unlink(); del expected["dll/Virtu.dll"]
        config = stage / "EmuHawk.runtimeconfig.json"
        config.write_text(json.dumps({"runtimeOptions": {"tfm": "net10.0", "rollForward": "Disable",
            "framework": {"name": "Microsoft.NETCore.App", "version": "10.0.13"}}}))
        if builder.output_closure(stage) != expected:
            raise ValueError("Staged EmuHawk closure differs")
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("DOTNET_", "COMPlus_", "COREHOST_"))}
        environment.update(DOTNET_ROOT=str(dotnet.parent), DOTNET_MULTILEVEL_LOOKUP="0", DOTNET_EnableDiagnostics="0")
        result = subprocess.run([str(dotnet), "exec", "--runtimeconfig", str(config), str(stage / "EmuHawk.exe"),
            "--puae6-self-check"], cwd=stage, env=environment, capture_output=True, text=True, timeout=60)
        if (builder.output_closure(stage) != expected or builder.output_closure(output) != files
                or hashlib.sha256(dotnet.read_bytes()).hexdigest() != host_sha
                or runtime_json_asset() != json_asset
                or len(result.stdout) + len(result.stderr) > 1024 * 1024):
            raise ValueError("EmuHawk inputs changed or output exceeded limit")
        lines = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
        if without_virtu:
            if result.returncode == 0 or lines or "ReflectionTypeLoadException" not in result.stderr or "Virtu" not in result.stderr:
                raise ValueError("Missing Virtu did not reject the complete inventory: " + result.stderr[-3000:])
            return {"status": "EXPECTED_REJECTION", "omitted": "dll/Virtu.dll", "exit_code": result.returncode}
        if result.returncode or len(lines) != 1 or "VIOLATION" in result.stdout + result.stderr:
            raise ValueError("EmuHawk bootstrap failed: " + result.stdout[-1500:] + result.stderr[-3000:])
        record = json.loads(lines[0]); validate(record, stage, files)
        return {"status": "PASS", "scope": "bootstrap-only", "tree_key": builder.tree_key(), "runtime": record["runtime"],
            "dotnet_sha256": host_sha, "files": files, "runtime_assets": {"dll/Newtonsoft.Json.dll": {
                "package": "newtonsoft.json/13.0.3", "asset": "lib/net6.0/Newtonsoft.Json.dll", "sha256": json_sha}}, "types_count": record["types_count"], "types_sha256": record["types_sha256"], "amiga": record["amiga"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--without-virtu", action="store_true", help="negative probe in its own staging, never an accepted partial inventory")
    args = parser.parse_args()
    try:
        print(json.dumps(run(without_virtu=args.without_virtu), sort_keys=True))
    except (OSError, ValueError, builder.RecipeError, subprocess.TimeoutExpired) as error:
        sys.exit("BLOCKED: " + str(error))


if __name__ == "__main__":
    main()
