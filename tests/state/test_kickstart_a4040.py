"""T55c: authorized A4000 firmware boot and restore on both public late CPUs.

Use the same visual/animation, headless parity and state oracle as A4030.
An emulator that returns success while executing Kickstart's alert loop fails.
"""
from tests.state import test_kickstart_a4030 as firmware


class NativeKickstart040Tests(firmware.NativeKickstartTests):
    MACHINE = "a4040"
    CPU_FLAGS = ("--cpu", "68040", "--fpu", "68040")


class NativeKickstart060Tests(firmware.NativeKickstartTests):
    MACHINE = "a4040"
    CPU_FLAGS = ("--cpu", "68060", "--fpu", "68060")


class LibretroKickstart040Tests(firmware.LibretroKickstartTests):
    MACHINE = "a4040"
    PRESET = "a4040_normal"
    CPU_FLAGS = ("--cpu", "68040", "--fpu", "68040")


class LibretroKickstart060Tests(firmware.LibretroKickstartTests):
    MACHINE = "a4040"
    PRESET = "a4060_normal"
    CPU_FLAGS = ("--cpu", "68060", "--fpu", "68060")
