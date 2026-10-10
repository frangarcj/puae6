"""Renders the generated part of reports/platform-matrix.md from tests/portability/evidence.json, waterbox-evidence.json and rows.py.

Nothing here runs an emulator: it only turns recorded evidence into text, so that the report cannot say more than the evidence does
(tests/portability/test_portability.py compares this output with what is between the markers of the report).
"""
import hashlib
import json
from pathlib import Path

from tests.portability.rows import DATE, OPT_ORDER, OPTS, OUT_OF_SCOPE, ROWS, SCOPE_DECISION, WATERBOX_ROW

ROOT = Path(__file__).resolve().parents[2]
BEGIN, END = "<!-- matrix:begin -->", "<!-- matrix:end -->"


def load(name):
    return json.loads((ROOT / "tests/portability" / name).read_text(encoding="utf-8"))


def short(text, n=12):
    return text[:n]


def totals(evidence):
    """SHA-256 over every case's hardware hashes, and over every case's A/V hashes, in id order: one number each for the whole corpus."""
    hardware, av = hashlib.sha256(), hashlib.sha256()
    for case_id, case in sorted(evidence["cases"].items()):
        hardware.update(f"{case_id} {case['hardware']}\n".encode())
        av.update(f"{case_id} {case['av']}\n".encode())
    return hardware.hexdigest(), av.hexdigest()


def row_status(evidence, name):
    """(status text, supported flag): supported only when every closing optimization level of the row built, ran every case and agreed."""
    entries = [evidence["executions"].get(f"{name}/{opt}") for opt in OPT_ORDER]
    if not all(entries):
        return "SIN EJECUTAR", False
    for e in entries:
        b, d = e.get("baseline"), e.get("det06")
        if e.get("build", {}).get("exit") != 0 or not b or not d:
            return "FALLO", False
        if b["errors"] or b["hardware_equal"] != b["cases"] or b["av_equal"] != b["cases"]:
            return "DIFERENCIA", False
        if d["errors"] or d["hardware_equal"] != d["cases"] or d["av_equal"] != d["cases"]:
            return "DIFERENCIA DET06", False
        if not e.get("headless") or e["headless"]["equal"] != e["headless"]["cases"]:
            return "DIFERENCIA p6_headless", False
    return "SOPORTADA", True


def render(evidence=None, waterbox=None):
    evidence = evidence or load("evidence.json")
    waterbox = waterbox or load("waterbox-evidence.json")
    hw_total, av_total = totals(evidence)
    out = [f"Generado por `tests/portability/render.py` desde `tests/portability/evidence.json` (fecha {evidence['date']}); no editar a mano.", ""]
    out += [f"Corpus: {evidence['corpus']['cases']} casos, SHA-256 del corpus (casos + ROMs) `{evidence['corpus']['sha256']}`.",
            f"Referencia: fila `{evidence['reference']['row']}` en {evidence['reference']['opt']}. Hash de conjunto del hardware canonico "
            f"`{hw_total}`; hash de conjunto de A/V canonico `{av_total}` (calculados sobre los hashes por caso de `evidence.json`).", ""]
    out += ["### Filas ejecutadas", "",
            "| Fila | Plataforma | Ejecucion | Build (O0 / O2) | Hardware = ref (O0 / O2) | A/V = ref (O0 / O2) | `p6_headless` = ref (O0 / O2) | Kickstart 1.3 hw+A/V = ref (O0 / O2) | DET06 (O0 / O2) | Estados nativos entre filas (O0) | Estado |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, spec in ROWS.items():
        status, _ = row_status(evidence, name)
        cells = {"build": [], "hw": [], "av": [], "det06": [], "hl": [], "ks": []}
        for opt in OPT_ORDER:
            e = evidence["executions"].get(f"{name}/{opt}")
            if not e or "baseline" not in e:
                for key in cells:
                    cells[key].append("-")
                continue
            b, d = e["baseline"], e.get("det06")
            cells["build"].append("ok" if e.get("build", {}).get("exit") == 0 else "FALLO")
            cells["hw"].append(f"{b['hardware_equal']}/{b['cases']}")
            cells["av"].append(f"{b['av_equal']}/{b['cases']}")
            cells["det06"].append(f"{d['hardware_equal']}+{d['av_equal']}/{d['cases']}" if d else "-")
            hl = e.get("headless")
            cells["hl"].append(f"{hl['equal']}/{hl['cases']}" if hl else "-")
            k = evidence.get("kickstart", {}).get("rows", {}).get(f"{name}/{opt}")
            cells["ks"].append(("si" if k["hardware_equal"] and k["av_equal"] else "NO") if k else "-")
        platform = f"{spec['os']}, {spec['isa']}, {spec['compiler']}" + ("" if spec["native_hardware"] else " (x86_64 traducido)")
        states = evidence.get("states")
        tag = f"{name}-O0"
        if states and tag in states["as_loader"]:
            cl, sv = states["as_loader"][tag], states["as_saver"][tag]
            state_cell = f"carga {cl[0]}/{cl[1]} de los de otras filas; los suyos los cargan {sv[0]}/{sv[1]}; no prometido"
        else:
            state_cell = "no probado; no prometido"
        out.append(f"| `{name}` | {platform} | {spec['execution']} | {' / '.join(cells['build'])} | {' / '.join(cells['hw'])} | "
                   f"{' / '.join(cells['av'])} | {' / '.join(cells['hl'])} | {' / '.join(cells['ks'])} | {' / '.join(cells['det06'])} | {state_cell} | **{status}** |")
    out += ["", "Las dos fracciones de DET06 son hardware+A/V iguales (de N casos) entre la variante `host` y la `baseline` de la misma fila.", ""]
    audit = {k: e for k, e in sorted(evidence["executions"].items()) if k.split("/")[1] not in OPT_ORDER}
    if audit:
        out += ["### Ejecuciones de auditoria", "",
                "Mismas construcciones con otras banderas o sanitizers; se comparan con la referencia como las anteriores. Un caso con error "
                "es un hallazgo del sanitizer o del proceso, no un hash distinto.", "",
                "| Fila / variante | Banderas | Build | Hardware = ref | A/V = ref | Casos con error | Avisos (proyecto / motor) |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for key, e in audit.items():
            opt = key.split("/")[1]
            b = e.get("baseline")
            origin = e.get("build", {}).get("warning_origin", {})
            project = sum(v for k, v in origin.items() if k.startswith("project "))
            engine = sum(v for k, v in origin.items() if k.startswith("engine "))
            out.append(f"| `{key}` | {OPTS[opt]['label']} | {'ok' if e.get('build', {}).get('exit') == 0 else 'FALLO'} | "
                       f"{(str(b['hardware_equal']) + '/' + str(b['cases'])) if b else '-'} | {(str(b['av_equal']) + '/' + str(b['cases'])) if b else '-'} | "
                       f"{len(b['errors']) if b else '-'} | {project} / {engine} |")
        out += [""]
    out += ["### Fila Waterbox (guest ARM64 en MacHawk Darwin)", "",
            f"| Fila | Check | Comando | Resultado ({waterbox['date']}) |", "| --- | --- | --- | --- |"]
    for check, entry in waterbox["checks"].items():
        out.append(f"| `{WATERBOX_ROW}` | {check} | `{entry['command']}` | {entry['result']} |")
    out += [""]
    out += ["### Filas fuera del alcance declarado", "", f"Cambio de alcance: {SCOPE_DECISION}. Ninguna tiene evidencia ni se declara soportada.", "",
            "| Fila | Plataforma | Motivo |", "| --- | --- | --- |"]
    for name, spec in OUT_OF_SCOPE.items():
        out.append(f"| `{name}` | {spec['os']} {spec['isa']}, {spec['kind']} | {spec['reason']} |")
    out += [""]
    states = evidence.get("states")
    if states:
        out += ["### Estados entre filas (no prometido)", "", states["summary"], ""]
    return "\n".join(out).strip("\n")


def splice(text, generated):
    start, end = text.index(BEGIN), text.index(END)
    return text[:start + len(BEGIN)] + "\n" + generated + "\n" + text[end:]


def current_block(text):
    start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
    return text[start:end].strip("\n")
