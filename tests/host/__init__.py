"""Shared helpers for the host tests."""
import os
from pathlib import Path
import subprocess

_ROOT = Path(__file__).resolve().parents[2]
_generated = False


def ensure_generated():
    """Materialize the patched upstream tree and generated sources once per process.

    Most source tests need build/upstream and build/generated. generate_cpu.py
    re-verifies both on every call (~15 s), which is what made the suite crawl when
    forty modules each ran it. A parent runner that already did it exports
    P6_TESTS_GENERATED=1 so worker processes skip the repeat.
    """
    global _generated
    if _generated or os.environ.get("P6_TESTS_GENERATED") == "1":
        return
    subprocess.run(["python3", "tools/generate_cpu.py"], cwd=_ROOT, check=True,
                   stdout=subprocess.PIPE, timeout=300)
    _generated = True
