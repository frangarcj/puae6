"""Runs inside the container next to RetroArch (tests/libretro-host/test_real_frontend.py): drives it with its network commands.

    python3 drive_retroarch.py LOG [disk|state|rewind]

RetroArch writes the core's log lines to LOG. The script waits for what the core logs, never for a fixed time, and prints "drive-ok" on success; a step
that does not happen within the limit prints what was missing and exits with 1.
  disk (the default): waits for the core to be running, opens the tray, chooses the next disk, closes the tray, waits until the core says it inserted it
      and then asks RetroArch to quit (the configuration must not make it ask twice).
  state: saves a state (SAVE_STATE), lets the core run on, loads it (LOAD_STATE) and lets it run again.
  rewind: lets the core run, then holds the rewind button for a while (REWIND) and lets it run forward again.
"""
import os
import socket
import sys
import time

LIMIT = 120


def units_after(log, offset):
    """The units the core logged after byte `offset` of the log, in order."""
    with open(log, "rb") as handle:
        handle.seek(offset)
        text = handle.read().decode("utf-8", errors="replace")
    return [int(part.split()[0]) for part in (chunk for chunk in text.split("] unit=")[1:])] or [0]


def saved_unit(log):
    """The last unit the core made before RetroArch said it saved the state."""
    unit = 0
    with open(log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "Saving state" in line:
                break
            if "] unit=" in line:
                unit = int(line.split("] unit=")[1].split()[0])
    return unit


def main(log, mode="disk"):
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def wait_for(text):
        end = time.monotonic() + LIMIT
        while time.monotonic() < end:
            try:
                with open(log, encoding="utf-8", errors="replace") as handle:
                    if text in handle.read():
                        return True
            except FileNotFoundError:
                pass
            time.sleep(0.2)
        print("drive-failed: never saw", text)
        return False

    def send(command):
        sender.sendto(command.encode(), ("127.0.0.1", 55355))

    if not wait_for("unit=5 "):
        return 1
    if mode == "state":
        send("SAVE_STATE")
        if not wait_for("Saving state") or not wait_for("unit=%d " % (60 + saved_unit(log))):
            return 1
        # The core has gone back when a unit lower than one it had already made shows up after the command.
        mark = os.path.getsize(log)
        peak = max(units_after(log, 0))
        send("LOAD_STATE")
        end = time.monotonic() + LIMIT
        while not any(unit < peak for unit in units_after(log, mark)[3:]):
            if time.monotonic() > end:
                print("drive-failed: the core never went back after LOAD_STATE")
                return 1
            time.sleep(0.2)
        time.sleep(1)
        send("QUIT")
        print("drive-ok")
        return 0
    if mode == "rewind":
        if not wait_for("unit=150 "):
            return 1
        for _ in range(30):
            send("REWIND")
            time.sleep(0.05)
        time.sleep(1)
        send("QUIT")
        print("drive-ok")
        return 0
    send("DISK_EJECT_TOGGLE")
    if not wait_for("DF0: ejected"):
        return 1
    send("DISK_NEXT")
    time.sleep(1)
    send("DISK_EJECT_TOGGLE")
    ok = wait_for("DF0: medium 1 inserted")
    send("QUIT")
    print("drive-ok" if ok else "drive-failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "disk"))
