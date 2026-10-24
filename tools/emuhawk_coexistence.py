#!/usr/bin/env python3
"""Real RomLoader/MovieSession coexistence, using only authenticated local inputs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import emuhawk_selfcheck as s

ROOT = s.ROOT
MARKER = "P6_EMUHAWK_COEXISTENCE "
CASES = {(format, core) for format in ("bk2", "tasproj") for core in ("UAE", "PUAE6")}


def sha(path):
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError("Missing/linked coexistence input: " + str(path))
    with path.open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()


def inputs():
    from tools import build_legacy_guest as legacy, build_waterbox as wb
    receipts = {"libwaterboxhost.dylib": wb.runtime_receipt_path("release"),
                "puae6.wbx": ROOT / "build/waterbox/puae6/puae6-receipt.json",
                "uae.wbx": ROOT / "build/legacy-guest/receipt.json"}
    receipt_hashes = {name: sha(path) for name, path in receipts.items()}
    runtime = wb.read_runtime_receipt(receipts["libwaterboxhost.dylib"])
    guest = json.loads(receipts["puae6.wbx"].read_text())
    wb.check_puae6_guest_abi(guest)
    reference = legacy.verify(ROOT / "build/legacy-guest")
    if runtime["runtime_profile"] != "release" or guest["manual_inputs"] != wb.puae6_manual_inputs(root=ROOT):
        raise ValueError("Current Release runtime/guest sources required")
    if any(sha(ROOT / name) != digest for name, digest in guest["sources"].items()):
        raise ValueError("Guest source receipt differs")
    paths = {"libwaterboxhost.dylib": Path(runtime["runtime_dylib"]["path"]),
             "puae6.wbx": ROOT / "build/waterbox/puae6/release/puae6.wbx",
             "uae.wbx": ROOT / "build/legacy-guest/uae.wbx"}
    result = {name: {"path": str(path), "sha256": sha(path), "receipt_sha256": receipt_hashes[name]} for name, path in paths.items()}
    if (result["libwaterboxhost.dylib"]["sha256"] != runtime["runtime_dylib"]["sha256"]
            or result["puae6.wbx"]["sha256"] != guest["guest"]["sha256"]
            or result["uae.wbx"]["sha256"] != reference["guest"]["sha256"]):
        raise ValueError("Runtime/guest receipt artifact mismatch")
    for name, path in paths.items():
        with path.open("rb") as stream: header = stream.read(64)
        if len(header) != 64: raise ValueError("Truncated native input")
        valid = header[:4] == b"\xcf\xfa\xed\xfe" and struct.unpack_from("<I", header, 4)[0] == 0x100000c if name.endswith("dylib") else header[:7] == b"\x7fELF\x02\x01\x01" and struct.unpack_from("<H", header, 18)[0] == 183
        if not valid: raise ValueError("Wrong Darwin ARM64/runtime or guest ELF ISA")
    if receipt_hashes != {name: sha(path) for name, path in receipts.items()}:
        raise ValueError("Native receipts changed during authentication")
    return result


def prepare(stage):
    from tests.fixtures.microrom import build_rom
    native = inputs()
    for name, item in native.items(): shutil.copyfile(item["path"], stage / "dll" / name)
    (stage / "native.json").write_text(json.dumps({name: item["sha256"] for name, item in native.items()}))
    rom = build_rom.build((ROOT / "tests/fixtures/microrom/input_all.s").read_text())
    disk = bytes(901120)
    (stage / "Firmware").mkdir(); (stage / "Firmware/Own.rom").write_bytes(rom)
    (stage / "Own.adf").write_bytes(disk)
    db = stage / "gamedb"; db.mkdir()
    (db / "gamedb.txt").write_text("".join(hashlib.sha1(data).hexdigest().upper() + "\t" + status + "\tOwn " + name + "\tAmiga\n"
        for data, status, name in ((rom, "I", "firmware"), (disk, "D", "disk"))))
    (db / "mame_machines.txt").write_text("")
    return native


def validate(record, native):
    if (not isinstance(record, dict) or set(record) != {"runtime", "same_process", "native", "cases"} or record["runtime"] != "10.0.13"
            or record["same_process"] is not True or record["native"] != {n: v["sha256"] for n, v in native.items()}
            or not isinstance(record["cases"], list) or len(record["cases"]) != 4):
        raise ValueError("Coexistence runtime/identity/coverage differs")
    seen = set()
    for case in record["cases"]:
        if not isinstance(case, dict): raise ValueError("Invalid coexistence case")
        key = (case.get("format"), case.get("core"))
        if key not in CASES or key in seen or set(case) != {"format", "core", "frames", "replayed", "isolation", "video_sha256", "audio_sha256", "state_sha256"}:
            raise ValueError("Coexistence duplicate/missing case")
        seen.add(key)
        if type(case["frames"]) is not int or case["frames"] != 12 or type(case["replayed"]) is not int or case["replayed"] != 12 or case["isolation"] is not True:
            raise ValueError("Incomplete coexistence frame/isolation check")
        if any(not isinstance(case[k], str) or not re.fullmatch("[0-9a-f]{64}", case[k]) for k in ("video_sha256", "audio_sha256", "state_sha256")):
            raise ValueError("Invalid coexistence observation")


def main():
    try: print(json.dumps(s.run(coexistence=True), sort_keys=True))
    except (OSError, ValueError, s.builder.RecipeError, s.subprocess.TimeoutExpired) as error: sys.exit("BLOCKED: " + str(error))


if __name__ == "__main__": main()
