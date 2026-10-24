#!/bin/sh
# Regression checks for the two WHDLoad bugs ScHlAuChi reported on 2026-09-09:
#   1. pressing the disk-swap buttons on a WHDLoad pack threw ArgumentOutOfRangeException out of
#      FrameAdvancePrep, because a pack leaves the floppy list empty
#   2. states made in one session were rejected in the next with "Bad hash for state" (the
#      "savestate core build mismatch" dialog), because the RASystem: volume was rebuilt with
#      different bytes every launch
# The second check has to span two processes: that is the whole point of it.
#
# usage: whdload-regression.sh <firmware-dir> <whdload.lha>
set -e

if [ $# -lt 2 ]; then
	echo "usage: $0 <firmware-dir> <whdload.lha>" >&2
	exit 2
fi

FIRMWARE=$(cd "$1" && pwd)
PACK=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=$HERE/bin/Debug/net8.0/osx-arm64
OUT=$(mktemp -d)
export DOTNET_ROLL_FORWARD=LatestMajor

# the host dylib is dlopen'd by bare name, so the runner has to be started from its own directory
cd "$BIN"

echo "== disk-swap buttons on a pack with no floppies"
dotnet ./Waterbox.UaeCd32Headless.dll "$FIRMWARE" "$PACK" 25 "$OUT" \
	"5-7:Insert Disk,9-11:Next Slot,13-15:Next Drive,17-19:Eject Disk" > "$OUT/keys.log" 2>&1 || {
		tail -20 "$OUT/keys.log"
		echo "FAIL: the core threw while the disk-swap buttons were pressed" >&2
		exit 1
	}

echo "== savestate made in one process, loaded in the next"
STATE_SAVE=$OUT/whdload.state dotnet ./Waterbox.UaeCd32Headless.dll "$FIRMWARE" "$PACK" 30 "$OUT" > "$OUT/save.log" 2>&1
grep -q "state saved to" "$OUT/save.log" || { tail -20 "$OUT/save.log"; echo "FAIL: no state was written" >&2; exit 1; }
STATE_LOAD=$OUT/whdload.state dotnet ./Waterbox.UaeCd32Headless.dll "$FIRMWARE" "$PACK" 5 "$OUT" > "$OUT/load.log" 2>&1 || {
		grep -E "state load" "$OUT/load.log" >&2
		echo "FAIL: a fresh process rejected the state it had just written" >&2
		exit 1
	}

rm -rf "$OUT"
echo "PASS"
