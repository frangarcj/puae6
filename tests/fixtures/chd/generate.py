"""CC0 synthetic CD audio fixture; run with chdman 0.287, never an input to product builds.

python3 tests/fixtures/chd/generate.py --chdman /opt/homebrew/bin/chdman
The temporary BIN/CUE live in build/. No firmware/game or downloaded asset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import tempfile

CODECS = ("none", "cdzl", "cdlz", "cdfl")
FRAMES = 40


def audio():
    # Nonzero stereo samples with distinct sectors and compressible repetition.
    result = bytearray()
    for frame in range(FRAMES):
        for sample in range(588):
            left, right = (frame * 317 + sample * 13) % 32000, (frame * 211 - sample * 7) % 32000
            if frame >= 24:
                left, right = ((sample * 1103515245 + frame * 12345) >> 8) & 32767, ((sample * 214013 + frame * 2531011) >> 3) & 32767
            result += left.to_bytes(2, "little") + right.to_bytes(2, "little")
    return bytes(result)


def expected():
    # CUE BINARY input samples are LE, CHD CDDA is BE (chdcd.cpp:914-918).
    # Independent numeric oracle indexed over all samples, not a byte swap of BIN.
    result = bytearray()
    for n in range(FRAMES * 588):
        frame, sample = divmod(n, 588)
        left, right = (317 * frame + 13 * sample) % 32000, (211 * frame - 7 * sample) % 32000
        if n >= 24 * 588:
            left = (12345 * frame + 1103515245 * sample) // 256 % 32768
            right = (2531011 * frame + 214013 * sample) // 8 % 32768
        result += struct.pack(">HH", left, right)
        if sample == 587:
            result += bytes(96)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chdman", required=True)
    args = parser.parse_args()
    version = subprocess.run([args.chdman], capture_output=True, text=True).stdout
    if "manager 0.287 " not in version:
        raise ValueError("Regeneration requires chdman 0.287")
    directory = Path(__file__).resolve().parent
    root = directory.parents[2]
    (root / "build").mkdir(exist_ok=True)
    hashes = {}
    with tempfile.TemporaryDirectory(prefix="chd-generate-", dir=root / "build") as temp:
        work = Path(temp)
        (work / "audio.bin").write_bytes(audio())
        (work / "audio.cue").write_text('FILE "audio.bin" BINARY\n TRACK 01 AUDIO\n INDEX 01 00:00:00\n')
        # With only the first 24 frames, 0.287 emits a CD FLAC map it cannot open
        # itself. Keep five varied hunks and verify every output before publishing.
        for codec in CODECS:
            output = work / (codec + ".chd")
            subprocess.run([args.chdman, "createcd", "-i", str(work / "audio.cue"),
                            "-o", str(output), "-hs", "19584", "-c", codec, "-np", "1"], check=True)
            subprocess.run([args.chdman, "verify", "-i", str(output)], check=True)
            data = output.read_bytes()
            (directory / output.name).write_bytes(data)
            hashes[output.name] = hashlib.sha256(data).hexdigest()
    (directory / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")


if __name__ == "__main__":
    main()
