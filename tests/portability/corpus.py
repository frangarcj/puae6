"""The corpus of the portability matrix (T25): micro-ROMs and p6_state_probe argument lists, made on the host from sources that are already
in the repository, so that every row of the matrix (a native build, a Linux/ARM64 or Linux/x86_64 container) runs exactly the same bytes.

`write_corpus(directory)` writes `<id>.rom` files and `cases.json`. The cases are a plain list of
    {"id", "rom", "args": [probe arguments after the ROM], "group"}
and carry no expectation: the matrix compares what each row prints with what the other rows print (tests/portability/run_matrix.py),
and the expected values of a picture or of a sample come from the tests that own those programs (tests/step, tests/audio), not from here.

Only micro-ROMs of this repository are used. An authorized Kickstart is added by the runner when P6_KICKSTART_ROM names one: it is read
where it is, mounted read-only for the duration of a container run, and neither copied nor hashed.
"""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("microrom_builder", ROOT / "tests/fixtures/microrom/build_rom.py")
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

FIXTURES = ROOT / "tests/fixtures/microrom"
UNITS = 8                      # units of a micro-ROM case: past the reset head, several vertical blanks, a few audio periods
VIDEO_UNITS = 6                # the units of the scenario files of tests/scenarios (display, ECS and AGA programs)


def _fixture(name):
    return (FIXTURES / f"{name}.s").read_text(encoding="utf-8")


def _machine(machine, *extra):
    return [] if machine == "a500" else ["--machine", machine, *extra]


def sources():
    """{rom id: assembly text} of every program of the corpus."""
    from tests.scenarios.a500.display import Display, rom_source as a500_source
    from tests.scenarios.aga import aga
    from tests.scenarios.audio.audio import Tone, rom_source as tone_source
    from tests.scenarios.ecs import ecs
    from tests.state.harness import TRACE, USER

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    programs = {"boot": _fixture("boot")}
    for name, entry in manifest["scenarios"].items():
        text = _fixture(name)
        for key, value in entry["defines"].items():
            text = text.replace(f"@{key}@", value)
        programs[name] = text
    programs["trace"], programs["user"] = TRACE, USER
    bars = ((64, 0x00F), (128, 0xF00), (192, 0x0FF))
    for name, display in (("a500-lores-bars", Display("lores-bars", mode="lores", bg=0x008, bars=bars)),
                          ("a500-hires-bars", Display("hires-bars", mode="hires", bg=0x008, bars=bars)),
                          ("a500-hires-lace-fields", Display("hires-lace-fields", mode="hires", lace=True, bg=0x008, cycle_fg=True)),
                          ("a500-flip-lace", Display("flip-lace", mode="hires", flip="lace", bg=0x008, bars=bars))):
        programs[name] = a500_source(display)
    for tone in (Tone("tone0", (0,)), Tone("tone2", (2,)), Tone("tone_all", (0, 1, 2, 3), period=124)):
        programs["audio-" + tone.name] = tone_source(tone)
    for scenario_id, (source, _, _) in ecs.scenario_programs().items():
        if scenario_id.endswith("-pal"):
            programs[scenario_id] = source
    for scenario_id, (source, _, _) in aga.scenario_programs().items():
        if scenario_id.endswith("-pal"):
            programs[scenario_id] = source
    return programs


def cases(programs):
    """The list of cases. PAL for everything, NTSC and other machines where the program is about that standard or machine."""
    out = []

    def add(group, case_id, rom, *args):
        out.append({"id": case_id, "group": group, "rom": rom, "args": list(args)})

    for name in ("boot", "vsync", "copper_irq", "long_ops", "halt", "stop", "lof_stuck", "long_frame", "input_all", "input_rotate", "trace", "user"):
        add("cpu-a500", f"a500-{name}-pal", name, "--units", str(UNITS))
    for name in ("vsync", "copper_irq", "long_frame", "lof_stuck"):
        add("cpu-a500", f"a500-{name}-ntsc", name, "--units", str(UNITS), "--ntsc")
    add("cpu-a500", "a500-vsync-seed7", "vsync", "--units", str(UNITS), "--seed", "7")
    add("memory", "a500-vsync-slow-none", "vsync", "--units", str(UNITS), "--slow", "none")
    add("memory", "a500-vsync-chip1m", "vsync", "--units", str(UNITS), "--chip", "1024", "--slow", "none")
    add("memory", "a1200-vsync-fast8m", "vsync", "--units", str(UNITS), "--machine", "a1200", "--fast", "8192")
    add("memory", "a600-vsync-chip1m", "vsync", "--units", str(UNITS), "--machine", "a600")
    for machine in ("a600", "a1200"):
        for name in ("boot", "copper_irq", "long_ops"):
            add("cpu-" + machine, f"{machine}-{name}-pal", name, "--units", str(UNITS), "--machine", machine)
    add("input", "input-mouse", "input_all", "--units", "5", "--port", "0:2:1:40:-25", "--port", "1:1:65:0:0")
    add("input", "input-types", "input_all", "--units", "7", "--port", "0:2:1:5:3", "--port-at", "3", "0:1:5:0:0",
        "--port-at", "5", "1:1:6:0:0", "--port-at", "6", "0:2:2:-4:9")
    add("input", "input-keys", "input_rotate", "--units", "9", "--key", "2", "0x40", "down", "--key", "5", "0x40", "up", "--key", "6", "0x45", "down")
    for name in ("a500-lores-bars", "a500-hires-bars", "a500-hires-lace-fields", "a500-flip-lace"):
        add("video-a500", f"{name}-pal", name, "--units", str(VIDEO_UNITS))
        add("video-a500", f"{name}-ntsc", name, "--units", str(VIDEO_UNITS), "--ntsc")
    for rom in programs:
        if rom.startswith("ecs-"):
            add("video-a600", rom, rom, "--units", str(VIDEO_UNITS), "--machine", "a600")
            if rom.startswith(("ecs-lores", "ecs-shres", "ecs-ident")):
                add("video-a600", rom.replace("-pal", "-ntsc"), rom, "--units", str(VIDEO_UNITS), "--machine", "a600", "--ntsc")
        if rom.startswith("aga-"):
            add("video-a1200", rom, rom, "--units", str(VIDEO_UNITS), "--machine", "a1200")
            if rom.startswith(("aga-bars24", "aga-ham8-", "aga-planes8-fmode3")):
                add("video-a1200", rom.replace("-pal", "-ntsc"), rom, "--units", str(VIDEO_UNITS), "--machine", "a1200", "--ntsc")
    for name in ("audio-tone0", "audio-tone2", "audio-tone_all"):
        add("audio", f"{name}-48000", name, "--units", str(UNITS))
        add("audio", f"{name}-44100", name, "--units", str(UNITS), "--rate", "44100")
        add("audio", f"{name}-44100-ntsc", name, "--units", str(UNITS), "--rate", "44100", "--ntsc")
    add("audio", "audio-tone_all-a1200", "audio-tone_all", "--units", str(UNITS), "--machine", "a1200")
    return out


def write_corpus(directory):
    """Writes the ROMs and cases.json into `directory` (created) and returns the list of cases."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    programs = sources()
    for name, text in programs.items():
        (directory / f"{name}.rom").write_bytes(builder.build(text))
    listing = cases(programs)
    for case in listing:
        if not (directory / f"{case['rom']}.rom").is_file():
            raise ValueError(f"case {case['id']} names a program that does not exist: {case['rom']}")
    ids = [case["id"] for case in listing]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    (directory / "cases.json").write_text(json.dumps(listing, indent=1) + "\n", encoding="utf-8")
    return listing


if __name__ == "__main__":
    target = Path(sys.argv[1])
    print(f"{len(write_corpus(target))} cases in {target}")


# --- the differential corpus (T32c) ----------------------------------------------------------------------------------------------------------------------------------
# The 28 scenarios of tests/scenarios/corpus (T32b) as a second corpus of the matrix: the same rows, the same builds, their own directory, results and evidence
# (tests/portability/evidence-differential.json), so that the evidence of T25 and its report stay as they were. Media the scenarios name are files of the corpus
# directory and the arguments name them as `@corpus/<file>`: row_driver.py resolves the token against the directory the ROMs are in, which in the `host` variant
# (DET06) is the odd one.

def write_differential(directory):
    """Writes the ROMs, the media and cases.json of the differential corpus into `directory` and returns the list of cases."""
    from tests.reference import corpus_run
    from tests.scenarios.corpus import corpus as differential

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    assets = corpus_run.Assets(directory)
    prefix = str(directory) + "/"
    texts = differential.programs()
    listing = []
    for program in sorted({entry.program for entry in differential.ENTRIES}):
        (directory / f"{program}.rom").write_bytes(differential.build_rom(texts[program]))
    for entry in differential.ENTRIES:
        args = [("@corpus/" + a[len(prefix):]) if a.startswith(prefix) else a for a in map(str, corpus_run.probe_arguments(entry, assets))]
        listing.append({"id": entry.id, "group": "differential-" + entry.machine, "rom": entry.program, "args": args})
    ids = [case["id"] for case in listing]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    (directory / "cases.json").write_text(json.dumps(listing, indent=1) + "\n", encoding="utf-8")
    return listing


CORPORA = {"": write_corpus, "differential": write_differential}
