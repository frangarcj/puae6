"""The differential corpus (T32b): the scenarios against which the product is compared with the reference (REF01), restored (DET02) and moved to another process (DET03).

Each entry names a program, a machine and a video standard, the probe arguments that make its inputs and events, how long REF01 runs it, and where DET02 takes its state. The
programs are micro-ROMs and generator output of this repository (nothing here is a Kickstart or a commercial game): the micro-ROMs of tests/fixtures/microrom, the display
programs of tests/scenarios/{a500,ecs,aga}, the tones of tests/scenarios/audio, the programs of tests/state, the floppy writer of tests/media-cow, the hard file loop of
tests/hdf and the register-ring programs of tests/scenarios/corpus/rings.py. A scenario that needs an asset the repository does not carry is not in this file: it is an opt-in
test of its own (the owner's Kickstart through an environment variable) and stays BLOCKED without it.

docs/04-validation.md asks for at least 20 scenarios with 4 OCS, 4 ECS, 4 AGA, PAL and NTSC, keyboard/mouse/joystick, two of audio/DMA, two of reset/swap and two of write
and restore, "without hiding a missing category": `CATEGORIES` and `REQUIRED` are checked by tests/reference/test_corpus.py against what the entries really declare, and the
entries declare only what their program and arguments do.

`documents()` builds the strict v1 scenario of every entry (tools/scenario_schema.py) for tests/scenarios/corpus/*.json; they are generated, never edited, and the test compares them
with the generator. A scenario file says what the scenario is; it is not evidence that an emulator ran it.
"""
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tests.scenarios.corpus import rings  # noqa: E402

CATEGORIES = ("ocs", "ecs", "aga", "pal", "ntsc", "audio", "dma", "mouse", "joystick", "keyboard", "reset", "swap", "write", "restore", "ring", "copper", "blitter", "interlace", "irq", "cpu")
REQUIRED = {"ocs": 4, "ecs": 4, "aga": 4, "pal": 1, "ntsc": 1, "audio": 2, "mouse": 1, "joystick": 1, "keyboard": 1, "reset": 1, "swap": 1, "write": 2, "restore": 2}
SCENARIO_DIR = Path(__file__).resolve().parent
MACHINES = {"a500": "A500", "a600": "A600", "a1200": "A1200"}
MEMORY = {"a500": (524288, 524288), "a600": (1048576, 0), "a1200": (2097152, 0)}

# The modes of a DET02 scenario are tests/state/test_det02.py's names; they are repeated here as strings so that this module imports without the probe harness.
SAME_BEAM, RESET_BEAM, NEW = "same-image-beam-set", "reset-image-beam-set", "new-process"
LATER_BEAM, BOOT_A, BOOT_B = "landed-image-beam-set", "new-process-boot-a", "new-process-boot-b"


@dataclass(frozen=True)
class Entry:
    id: str
    machine: str            # a500, a600 or a1200
    standard: str           # pal or ntsc
    categories: tuple
    program: str            # key of programs()
    extra: object = ()      # probe arguments, or a key of MEDIA ("disk", "swap", "hdf", "floppy-write")
    units: int = 8          # units of the REF01 run and of the scenario file
    points: tuple = (2, 4)  # units at which DET02 takes the state
    modes: tuple = (SAME_BEAM, NEW)
    summary: str = ""

    @property
    def args(self):
        return list(self.extra) if isinstance(self.extra, (list, tuple)) else []


# Media keys and the arguments they expand to are the harness's (tests/reference/test_corpus_det02.py); the scenario files describe them by content.
MEDIA = ("disk", "swap", "hdf", "floppy-write")

ENTRIES = (
    # --- OCS (A500) ------------------------------------------------------------------------------------------------------------------------------------------------
    Entry("ocs-vsync-irq-pal", "a500", "pal", ("ocs", "pal", "irq", "cpu"), "vsync", points=(2, 3), summary="level-3 vertical-blank handler counting in D1 while the main loop counts in D0"),
    Entry("ocs-copper-irq-ntsc", "a500", "ntsc", ("ocs", "ntsc", "copper", "irq"), "copper_irq", ("--ntsc",), points=(2,), summary="a Copper WAIT at line 4 raises the level-3 interrupt"),
    Entry("ocs-lores-bars-pal", "a500", "pal", ("ocs", "pal"), "a500-lores-bars", units=6, summary="lowres bars, the picture predicted from the program"),
    Entry("ocs-hires-lace-ntsc", "a500", "ntsc", ("ocs", "ntsc", "interlace"), "a500-hires-lace-fields", ("--ntsc",), units=6, points=(2, 3, 4), summary="hires interlace with two fields of different colour"),
    Entry("ocs-blitter-dma-pal", "a500", "pal", ("ocs", "pal", "dma", "blitter"), "busy", points=(2, 5), summary="every DMA channel on, a blit in flight under contention, the disk motor running"),
    Entry("ocs-halt-pal", "a500", "pal", ("ocs", "pal", "cpu"), "halt", points=(2,), modes=(SAME_BEAM, NEW), summary="a double bus fault halts the 68000; the chipset goes on (REF01 declares the difference to the upstream)"),
    Entry("ocs-ring-cpu-pal", "a500", "pal", ("ocs", "pal", "ring"), "ring-cpu", points=(2, 4, 6), modes=(SAME_BEAM, RESET_BEAM, NEW), summary="the CPU rewrites BPLCON0/1/2 and the colours without stopping, so Denise's register ring is never idle"),
    Entry("ocs-ring-copper-ntsc", "a500", "ntsc", ("ocs", "ntsc", "ring", "copper"), "ring-copper", ("--ntsc",), points=(2, 4, 6), modes=(SAME_BEAM, RESET_BEAM, NEW), summary="a Copper list rewrites the same registers around the vertical blank"),
    # --- ECS (A600) ------------------------------------------------------------------------------------------------------------------------------------------------
    Entry("ecs-beam-var-wide-pal", "a600", "pal", ("ecs", "pal"), "ecs-beam-var-wide-pal", units=6, summary="programmed beam (BEAMCON0, HTOTAL) with a wider line"),
    Entry("ecs-shres-ntsc", "a600", "ntsc", ("ecs", "ntsc"), "ecs-shres-pal", ("--ntsc",), units=6, summary="super-hires picture"),
    Entry("ecs-diwhigh-v-pal", "a600", "pal", ("ecs", "pal"), "ecs-diwhigh-v-pal", units=6, summary="the vertical window set through DIWHIGH"),
    Entry("ecs-ring-cpu-pal", "a600", "pal", ("ecs", "pal", "ring"), "ring-cpu-ecs", points=(2, 4, 6), modes=(SAME_BEAM, RESET_BEAM, NEW), summary="the CPU also rewrites BPLCON3 and DIWHIGH in the loop"),
    Entry("ecs-ring-copper-ntsc", "a600", "ntsc", ("ecs", "ntsc", "ring", "copper"), "ring-copper-ecs", ("--ntsc",), points=(2, 4, 6), modes=(SAME_BEAM, RESET_BEAM, NEW), summary="the Copper rewrites BPLCON3 too"),
    # --- AGA (A1200) -----------------------------------------------------------------------------------------------------------------------------------------------
    Entry("aga-planes8-fmode3-pal", "a1200", "pal", ("aga", "pal"), "aga-planes8-fmode3-pal", units=6, points=(2,), summary="eight bitplanes fetched 64 bits at a time"),
    Entry("aga-ham8-ntsc", "a1200", "ntsc", ("aga", "ntsc"), "aga-ham8-pal", ("--ntsc",), units=6, points=(2,), summary="HAM8"),
    Entry("aga-sprite64-pal", "a1200", "pal", ("aga", "pal"), "aga-sprite64-pal", units=6, points=(2,), summary="64-bit wide sprites"),
    Entry("aga-bars24-ntsc", "a1200", "ntsc", ("aga", "ntsc", "copper"), "aga-bars24-pal", ("--ntsc",), units=6, points=(2,), summary="24-bit colour bars from the Copper"),
    # --- audio and DMA ---------------------------------------------------------------------------------------------------------------------------------------------
    Entry("audio-tone4-pal", "a500", "pal", ("ocs", "pal", "audio", "dma"), "audio-tone_all", points=(3,), summary="four channels of audio DMA at period 124"),
    Entry("audio-volume-ntsc", "a500", "ntsc", ("ocs", "ntsc", "audio", "irq"), "tone-vol", ("--ntsc",), points=(5, 6), summary="a tone whose volume the vertical-blank handler writes between period loads (mixvol)"),
    Entry("audio-modulated-pal", "a500", "pal", ("ocs", "pal", "audio", "dma"), "tone-mod", points=(3, 4), summary="channel 0 modulates the volume of channel 1, LED filter on"),
    # --- input -----------------------------------------------------------------------------------------------------------------------------------------------------
    Entry("input-mouse-joystick-pal", "a500", "pal", ("ocs", "pal", "mouse", "joystick"), "input_all", ["--port", "0:2:1:40:-25", "--port", "1:1:65:0:0"], points=(4,), summary="a mouse that keeps moving in port 0 and a joystick with a button in port 1"),
    Entry("input-keyboard-pal", "a500", "pal", ("ocs", "pal", "keyboard"), "vsync", ["--key", "2", "0x20", "down", "--key", "3", "0x21", "down", "--key", "3", "0x40", "down", "--key", "4", "0x20", "up", "--key", "5", "0x45", "down"], points=(3, 6), summary="keys pressed and released across the keyboard handshake"),
    Entry("input-pot-pal", "a500", "pal", ("ocs", "pal", "joystick"), "potgo", ["--port", "1:1:6:0:0"], points=(6,), summary="a pot measurement started by the guest with a joystick button held"),
    Entry("input-types-pal", "a500", "pal", ("ocs", "pal", "mouse", "joystick"), "input_all", ["--port", "0:2:1:5:3", "--port-at", "3", "0:1:5:0:0", "--port-at", "5", "1:1:6:0:0", "--port-at", "6", "0:2:2:-4:9"], units=9, points=(4,), summary="a port that changes from mouse to joystick and back"),
    # --- reset and swap --------------------------------------------------------------------------------------------------------------------------------------------
    Entry("reset-mid-run-pal", "a500", "pal", ("ocs", "pal", "reset", "restore"), "vsync", ["--reset-at", "3"], units=8, points=(5,), modes=(SAME_BEAM, RESET_BEAM), summary="p6_reset after unit 3; the state is taken after it"),
    Entry("swap-disk-pal", "a500", "pal", ("ocs", "pal", "swap", "dma"), "busy", "swap", units=14, points=(12,), modes=(LATER_BEAM, BOOT_A, BOOT_B), summary="disk A is ejected after unit 9 and B inserted after unit 11 (the change lands 100 frames later)"),
    # --- write and restore -----------------------------------------------------------------------------------------------------------------------------------------
    Entry("floppy-write-pal", "a500", "pal", ("ocs", "pal", "write", "restore"), "floppy-write", "floppy-write", units=110, points=(105,), modes=(SAME_BEAM, NEW), summary="the guest writes whole tracks to a writable disk; the state in the middle takes the later writes back"),
    Entry("hdf-write-pal", "a500", "pal", ("ocs", "pal", "write", "restore", "dma"), "hdfloop", "hdf", points=(3, 5), summary="hard file requests every vertical blank, writes into the overlay in 4 KiB chunks"),
)

# Programs that are not in tests/portability/corpus.py or whose arguments the corpus fixes.
EXTRA_PROGRAMS = ("busy", "tone-vol", "tone-mod", "potgo", "halt", "ring-cpu", "ring-cpu-ecs", "ring-copper", "ring-copper-ecs", "floppy-write", "hdfloop")


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def programs():
    """{program name: assembly text with its defines applied}: the base programs of tests/portability/corpus.py and the ones the corpus adds."""
    from tests.portability import corpus as portability
    from tests.state import test_det02 as det02
    from tests.state import test_state_input as input_tests
    from tests.state import test_state_upstream as upstream
    from tests.hdf import hdf_rom

    texts = dict(portability.sources())
    texts["busy"] = upstream.BUSY
    texts["tone-vol"] = det02.TONE_VOLUME
    texts["tone-mod"] = det02.TONE_MODULATED
    texts["potgo"] = input_tests.POTGO.replace("@NEXT@", "again")
    texts["ring-cpu"], texts["ring-cpu-ecs"] = rings.cpu_ring(False), rings.cpu_ring(True)
    texts["ring-copper"], texts["ring-copper-ecs"] = rings.copper_ring(False), rings.copper_ring(True)
    texts["hdfloop"] = hdf_rom.loop_program()
    med02 = _load(ROOT / "tests/media-cow/med02.py") if "med02" not in sys.modules else sys.modules["med02"]
    texts["floppy-write"] = med02.rom_source()
    return texts


def by_id():
    return {entry.id: entry for entry in ENTRIES}


def category_counts():
    counts = {name: 0 for name in CATEGORIES}
    for entry in ENTRIES:
        for name in entry.categories:
            counts[name] += 1
    return counts


# --- the scenario files -----------------------------------------------------------------------------------------------------------------------------------------------

def _inputs(args, units):
    """(packets, resets) of the strict scenario for the unit-addressed probe arguments: `--port N:type:b:dx:dy`, `--port-at U spec`, `--key U code down|up`, `--reset-at U`.
    Probe inputs number the first unit 1, with 0 aliasing 1. Scenario inputs are zero-based;
    keys/buttons hold, but motion needs an explicit packet for every unit. Resets name boundaries."""
    idle = {"type": "joystick", "buttons": 0, "dx": 0, "dy": 0}
    kinds = {"0": "none", "1": "joystick", "2": "mouse"}
    ports = [dict(idle), dict(idle)]
    pending, resets, i = {}, [], 0

    def port(spec):
        number, kind, buttons, dx, dy = spec.split(":")
        return int(number), {"type": kinds[kind], "buttons": int(buttons), "dx": int(dx), "dy": int(dy)}

    while i < len(args):
        option = args[i]
        if option == "--port":
            # All --port presets are parsed before the probe applies any timed event,
            # including events 0/1 that precede a preset in argv.
            number, value = port(args[i + 1])
            ports[number] = value
            i += 2
        elif option == "--port-at":
            unit = max(0, int(args[i + 1]) - 1)
            pending.setdefault(unit, []).append(("port", port(args[i + 2])))
            i += 3
        elif option == "--key":
            unit = max(0, int(args[i + 1]) - 1)
            pending.setdefault(unit, []).append(("key", (int(args[i + 2], 0), args[i + 3])))
            i += 4
        elif option == "--reset-at":
            resets.append({"unit": int(args[i + 1]), "sequence": 0, "type": "reset"})
            i += 2
        else:
            i += 1
    keys, packets = 0, []
    for unit in range(units):
        for kind, value in pending.get(unit, ()):
            if kind == "port":
                number, state = value
                ports[number] = state
            else:
                code, direction = value
                keys = keys | (1 << code) if direction == "down" else keys & ~(1 << code)
        if unit == 0 or unit in pending or any(p["dx"] or p["dy"] for p in ports):
            packets.append({"unit": unit, "keys": "%032x" % keys, "ports": [dict(p) for p in ports]})
    return packets, resets


def adf_bytes(label):
    """The two disks of the "disk" and "swap" scenarios, as tests/state/test_det02.py builds them."""
    return hashlib.sha256(("disk " + label).encode()).digest() * (901120 // 32)


def media_of(entry):
    shas = []
    if entry.extra in ("disk",):
        shas = [hashlib.sha256(adf_bytes("a")).hexdigest()]
    elif entry.extra == "swap":
        shas = [hashlib.sha256(adf_bytes("a")).hexdigest(), hashlib.sha256(adf_bytes("b")).hexdigest()]
    return [{"format": "adf", "sha256": sha} for sha in shas]


def events_of(entry):
    """Media events of the scenario: the "swap" key ejects A after unit 9 and mounts B after unit 11, as tests/state/test_det02.py does."""
    if entry.extra == "swap":
        return [{"unit": 0, "sequence": 1, "type": "mount", "drive": 0, "media_index": 0},
                {"unit": 9, "sequence": 2, "type": "eject", "drive": 0, "media_index": None},
                {"unit": 11, "sequence": 3, "type": "mount", "drive": 0, "media_index": 1}]
    if entry.extra == "disk":
        return [{"unit": 0, "sequence": 1, "type": "mount", "drive": 0, "media_index": 0}]
    return []


def document(entry, rom_sha256):
    packets, resets = _inputs(entry.args, entry.units)
    events = sorted(resets + events_of(entry), key=lambda e: (e["unit"], e["sequence"]))
    chip, slow = MEMORY[entry.machine]
    return {
        "schema_version": 1, "scenario_id": entry.id, "profile": "deterministic-a500",
        "config": {"machine": MACHINES[entry.machine], "video_standard": entry.standard.upper(), "audio_rate_hz": 48000, "random_seed": 1,
                   "rtc_epoch_seconds": 946684800, "chip_ram_bytes": chip, "slow_ram_bytes": slow},
        "firmware": [rom_sha256], "media": media_of(entry), "program": None, "units": entry.units,
        "inputs": packets, "events": events, "checkpoints": list(range(1, entry.units + 1)), "probes": [], "render": True, "audio": True,
    }


def build_rom(text):
    builder = _load(ROOT / "tests/fixtures/microrom/build_rom.py")
    return builder.build(text)


def documents():
    """{entry id: scenario document}, each with the SHA-256 of the micro-ROM its program assembles to."""
    texts = programs()
    out = {}
    for entry in ENTRIES:
        rom = build_rom(texts[entry.program])
        out[entry.id] = document(entry, hashlib.sha256(rom).hexdigest())
    return out


def index():
    """The index file: what each entry is, by category, with the scenario file that describes it."""
    return {
        "schema_version": 1,
        "purpose": "T32b: the differential corpus. Generated by tests/scenarios/corpus/corpus.py; tests/reference/test_corpus.py compares it with the generator and checks the categories of docs/04-validation.md.",
        "required": REQUIRED,
        "entries": [{"id": e.id, "machine": e.machine, "standard": e.standard, "categories": list(e.categories), "program": e.program,
                     "probe_args": e.extra if isinstance(e.extra, str) else list(e.args), "units": e.units, "det02_units": list(e.points),
                     "det02_modes": list(e.modes), "scenario": f"{e.id}.json", "summary": e.summary} for e in ENTRIES],
    }


# Where each program comes from (the file whose text or generator produces it) and under which terms; every one is original to this repository.
SOURCES = {
    "vsync": "tests/fixtures/microrom/vsync.s", "copper_irq": "tests/fixtures/microrom/copper_irq.s (HP=2)", "halt": "tests/fixtures/microrom/halt.s", "input_all": "tests/fixtures/microrom/input_all.s",
    "a500-lores-bars": "tests/scenarios/a500/display.py", "a500-hires-lace-fields": "tests/scenarios/a500/display.py",
    "audio-tone_all": "tests/scenarios/audio/audio.py", "tone-vol": "tests/state/test_det02.py (TONE_VOLUME)", "tone-mod": "tests/state/test_det02.py (TONE_MODULATED)",
    "busy": "tests/state/test_state_upstream.py (BUSY)", "potgo": "tests/state/test_state_input.py (POTGO)", "floppy-write": "tests/media-cow/med02.py",
    "hdfloop": "tests/hdf/hdf_rom.py (loop_program)", "ring-cpu": "tests/scenarios/corpus/rings.py", "ring-cpu-ecs": "tests/scenarios/corpus/rings.py",
    "ring-copper": "tests/scenarios/corpus/rings.py", "ring-copper-ecs": "tests/scenarios/corpus/rings.py",
}
OPT_IN = {
    "P6_KICKSTART_ROM": "an authorized Kickstart 1.3 image (A500); read in place, never copied or hashed",
    "P6_KICKSTART_A600_ZIP / P6_WB205_ZIP": "an authorized Kickstart 2.05 and Workbench 2.05 disk in zips (A600); read in memory",
    "P6_KICKSTART_A1200_ROM / P6_AGA_ADF": "an authorized Kickstart 3.1 and an AGA demo disk (A1200)",
    "P6_WHDLOAD_LHA / P6_FATALMISSION_LHA": "the free WHDLoad distribution and the Fatal Mission archive (docs/whdload.md)",
}


def fixtures():
    """The provenance of what the corpus runs: every program with the file it comes from, its licence and the SHA-256 of the 256 KiB image it assembles to; the generated media; the
    opt-in firmware that is not here. A hash that changes is a program that changed: the test compares this with the committed file and a person updates it knowingly."""
    from tests.hdf import hdf_image
    texts = programs()
    used = {}
    for entry in ENTRIES:
        used.setdefault(entry.program, []).append(entry.id)
    rows = []
    for name in sorted(used):
        source = SOURCES.get(name) or ("tests/scenarios/ecs/ecs.py" if name.startswith("ecs-") else "tests/scenarios/aga/aga.py" if name.startswith("aga-") else None)
        if source is None:
            raise ValueError(f"program {name} has no recorded source")
        rows.append({"program": name, "source": source, "license": "MIT (original to this repository)", "kind": "kickstart-shaped micro-ROM, 256 KiB",
                     "rom_sha256": hashlib.sha256(build_rom(texts[name])).hexdigest(), "used_by": used[name]})
    return {
        "schema_version": 1,
        "purpose": "Provenance of the fixtures of the differential corpus (T32b). Generated by tests/scenarios/corpus/corpus.py; tests/reference/test_corpus.py compares it with the generator. No firmware, game or commercial asset is listed with a hash or kept here.",
        "assembler": {"script": "tests/fixtures/microrom/build_rom.py", "sha256": hashlib.sha256((ROOT / "tests/fixtures/microrom/build_rom.py").read_bytes()).hexdigest()},
        "programs": rows,
        "media": {"adf_a": hashlib.sha256(adf_bytes("a")).hexdigest(), "adf_b": hashlib.sha256(adf_bytes("b")).hexdigest(),
                  "hdf_rdb_64_cylinders": hashlib.sha256(hdf_image.rdb_image(cylinders=64)).hexdigest(),
                  "note": "generated in memory by the tests; the floppy-write scenario builds its disks with tests/scenarios/disk/disk.py make_adf (labels 0x41 and 0x42)"},
        "opt_in_environment": OPT_IN,
    }


def write():
    for scenario_id, doc in documents().items():
        (SCENARIO_DIR / f"{scenario_id}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (SCENARIO_DIR / "index.json").write_text(json.dumps(index(), indent=2) + "\n", encoding="utf-8")
    (ROOT / "manifests/fixtures.json").write_text(json.dumps(fixtures(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write()
    print(f"{len(ENTRIES)} scenarios written to {SCENARIO_DIR}")
