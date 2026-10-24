"""The already provisioned RetroArch image, verified locally and run offline.

This module does not import tests, probe Docker at import or provision images.
"""
import json
from pathlib import Path
import re
import subprocess

PIN = Path(__file__).resolve().parents[2] / "tests/libretro-host/docker/image.json"


def specification():
    spec = json.loads(PIN.read_text())
    if (spec.get("schema_version") != 1 or spec.get("platform") != "linux/arm64"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.get("id", ""))
            or spec.get("tag") != "puae6-libretro-test"):
        raise ValueError("invalid pinned RetroArch image specification")
    return spec


def verify(execute=None):
    spec = specification()
    if execute is None:
        execute = lambda argv, **kwargs: subprocess.run(argv, capture_output=True, text=True, **kwargs)
    try:
        result = execute(["docker", "image", "inspect", spec["tag"]], timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("required offline RetroArch image unavailable") from error
    if result.returncode:
        raise ValueError("required offline RetroArch image absent; no automatic build or download")
    try:
        image, = json.loads(result.stdout)
        valid = image["Id"] == spec["id"] and image["Os"] + "/" + image["Architecture"] == spec["platform"]
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError("RetroArch image differs from its frozen ID/platform")
    return {key: spec[key] for key in ("tag", "id", "platform")}


def run_arguments(command, options=(), spec=None):
    image = verify() if spec is None else spec
    return ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform", image["platform"],
            *options, image["id"], "bash", "-c", command]
