"""Runs inside the container next to RetroArch (tests/libretro-host/test_real_frontend.py): changes the disk with RetroArch's network commands.

    python3 drive_retroarch.py LOG

RetroArch writes the core's log lines to LOG. The script waits for the core to be running, opens the tray, chooses the next disk, closes the
tray, waits until the core says it inserted it and then asks RetroArch to quit (the configuration must not make it ask twice). It waits for what the core logs, never for a fixed time, and
prints "drive-ok" on success; a step that does not happen within the limit prints what was missing and exits with 1.
"""
import socket
import sys
import time

LIMIT = 120


def main(log):
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
    sys.exit(main(sys.argv[1]))
