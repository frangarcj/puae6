"""What the opt-in tests of the real WHDLoad flow (T30b) share: the prepared image of Fatal Mission and the owner's Kickstart 3.1, both read from where the environment says.

    P6_WHDLOAD_LHA          WHDLoad_usr.lha (https://whdload.de/whdload/WHDLoad_usr.lha)
    P6_FATALMISSION_LHA     FatalMission.lha (https://whdload.de/games/FatalMission.lha)
    P6_KICKSTART_A1200_ROM  the owner's Kickstart 3.1 for the A1200 (read in place or written to a temporary system directory; never copied into the repository, hashed or kept)

The image is prepared by tools/prepare_whdload.py from the config of the repository, in memory, and handed to the adapters as the one file they get; the timeline of the game is the one the config
describes under "run": the Kickstart and the volume boot in about 170 units, WHDLoad shows the title of the game by unit ~290, fire (port 1) starts it, F10 (WHDLoad's QuitKey) ends it and the
script of the volume writes exit.log.
"""
import os
from pathlib import Path

from tests.whdload import lha_fixture      # noqa: F401  (puts tools/ on sys.path)
import prepare_whdload as prep
from whdload import ffs

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/whdload/fatal-mission.json"
WHDLOAD, GAME = os.environ.get("P6_WHDLOAD_LHA"), os.environ.get("P6_FATALMISSION_LHA")
KICKSTART = os.environ.get("P6_KICKSTART_A1200_ROM")
INPUTS = bool(WHDLOAD and GAME and Path(WHDLOAD).is_file() and Path(GAME).is_file())
AVAILABLE = INPUTS and bool(KICKSTART and Path(KICKSTART).is_file())
SKIP = "BLOCKED: set P6_WHDLOAD_LHA, P6_FATALMISSION_LHA and P6_KICKSTART_A1200_ROM (the free downloads of config/whdload/fatal-mission.json and the owner's Kickstart 3.1)"

FIRE_AT, FIRE_UNTIL = 400, 404          # port 1: button 1 down from unit 400, up from 404
QUIT_AT, QUIT_UNTIL, F10 = 1500, 1504, 0x59
BOOT_UNITS = 170                        # DOS has started the script of the volume
TITLE_UNITS = 330                       # WHDLoad has loaded the game and the title is on screen


def prepare():
    """(image bytes, manifest dict) of the real Fatal Mission volume, built the way the command line builds it."""
    config = prep.load_config(CONFIG)
    image, manifest, missing = prep.prepare(config, {"whdload": WHDLOAD, "game": GAME}, {})
    assert not missing
    return image, manifest


def fire():
    return ["--port-at", str(FIRE_AT), "1:1:1:0:0", "--port-at", str(FIRE_UNTIL), "1:1:0:0:0"]


def quit_key():
    return ["--key", str(QUIT_AT), hex(F10), "down", "--key", str(QUIT_UNTIL), hex(F10), "up"]


def volume(image):
    """What the volume holds (ffs.read_volume checks every checksum and the bitmap on the way)."""
    return ffs.read_volume(image)
