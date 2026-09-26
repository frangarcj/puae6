#!/usr/bin/env python3
"""Inspect a planning backlog. Never execute tasks or certify emulator correctness."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"pending", "in_progress", "blocked", "done"}
ID_PATTERN = re.compile(r"T[0-9]{2,}(?:[A-Za-z0-9_.-]+)?\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: Any, *, empty_ok: bool = False) -> bool:
    return (isinstance(value, list) and (empty_ok or bool(value))
            and all(nonempty_string(item) for item in value))


def safe_path(root: Path, value: Any) -> Path | None:
    """Only project-relative paths; resolve symlinks before accepting reads."""
    if not nonempty_string(value) or "\\" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        return None
    try:
        resolved = (root / value).resolve()
        resolved.relative_to(root.resolve())
        return resolved
    except (ValueError, OSError, RuntimeError):
        return None


def validate_plan(root: Path, data: Any, lock: Any, release: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return ["tasks.json: schema_version debe ser 1 y la raiz un objeto"]
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["tasks.json: tasks debe ser una lista no vacia"]
    catalog = data.get("check_ids")
    if not string_list(catalog):
        errors.append("check_ids debe ser una lista no vacia de strings")
        catalog = []
    if len(catalog) != len(set(catalog)):
        errors.append("check_ids contiene duplicados")
    catalog_set = set(catalog)
    by_id: dict[str, dict[str, Any]] = {}
    progress = []

    for index, task in enumerate(tasks):
        label = f"tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: debe ser objeto")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not ID_PATTERN.fullmatch(task_id):
            errors.append(f"{label}: ID invalido")
            continue
        label = task_id
        if task_id in by_id:
            errors.append(f"{label}: ID duplicado")
            continue
        by_id[task_id] = task
        for field in ("title", "milestone", "stop"):
            if not nonempty_string(task.get(field)):
                errors.append(f"{label}: falta string no vacio {field}")
        if not isinstance(task.get("required"), bool):
            errors.append(f"{label}: required debe ser booleano")
        status = task.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{label}: estado invalido")
        if status == "in_progress":
            progress.append(task_id)
        for field in ("depends", "read", "touch", "steps", "checks", "acceptance"):
            value = task.get(field)
            if not string_list(value, empty_ok=(field == "depends")):
                errors.append(f"{label}: {field} debe ser lista de strings no vacios")
                continue
            if field in ("depends", "read", "touch", "checks") and len(value) != len(set(value)):
                errors.append(f"{label}: duplicados en {field}")
            if field == "checks":
                for check in value:
                    if check not in catalog_set:
                        errors.append(f"{label}: check desconocido {check}")
            if field in ("read", "touch"):
                for item in value:
                    resolved = safe_path(root, item)
                    if resolved is None:
                        errors.append(f"{label}: ruta fuera del proyecto en {field}: {item}")
                    elif field == "read" and not resolved.is_file():
                        errors.append(f"{label}: lectura inexistente: {item}")
        evidence = task.get("evidence")
        if evidence is not None:
            expected = f"reports/tasks/{task_id}.md"
            resolved = safe_path(root, evidence)
            if evidence != expected or resolved is None:
                errors.append(f"{label}: evidence debe ser {expected}")
            elif not resolved.is_file() or not resolved.read_text(encoding="utf-8").strip():
                errors.append(f"{label}: nota de tarea inexistente o vacia")
        if status == "blocked" and evidence is None:
            errors.append(f"{label}: blocked requiere nota de tarea")

    if len(progress) > 1:
        errors.append("Mas de una tarea in_progress: " + ", ".join(progress))

    graph: dict[str, list[str]] = {}
    for task_id, task in by_id.items():
        deps = task.get("depends")
        if not string_list(deps, empty_ok=True):
            deps = []
        graph[task_id] = []
        for dependency in deps:
            if dependency == task_id:
                errors.append(f"{task_id}: dependencia de si misma")
            if dependency not in by_id:
                errors.append(f"{task_id}: dependencia desconocida {dependency}")
                continue
            if dependency not in graph[task_id]:
                graph[task_id].append(dependency)
            if task.get("status") in ("done", "in_progress") and by_id[dependency].get("status") != "done":
                errors.append(f"{task_id}: dependencia {dependency} no terminada")

    indegree = {task_id: len(deps) for task_id, deps in graph.items()}
    children: dict[str, list[str]] = {task_id: [] for task_id in graph}
    for task_id, deps in graph.items():
        for dependency in deps:
            children[dependency].append(task_id)
    queue = deque(task_id for task_id, count in indegree.items() if count == 0)
    visited = 0
    while queue:
        task_id = queue.popleft()
        visited += 1
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(graph):
        errors.append("Ciclo de dependencias: " + ", ".join(sorted(k for k, v in indegree.items() if v)))

    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        errors.append("upstream.lock.json: schema_version debe ser 1 y la raiz un objeto")
        return errors
    sources = lock.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("upstream.lock.json: sources debe ser lista no vacia")
        sources = []
    source_ids = set()
    for source in sources:
        if not isinstance(source, dict) or not nonempty_string(source.get("id")):
            errors.append("Fuente sin ID valido")
            continue
        source_id = source["id"]
        if source_id in source_ids:
            errors.append(f"Fuente duplicada: {source_id}")
        source_ids.add(source_id)
        if not isinstance(source.get("required"), bool):
            errors.append(f"{source_id}: required debe ser booleano")
        commit = source.get("commit")
        if commit is not None and (not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit)):
            errors.append(f"{source_id}: commit debe ser SHA completo de 40 hex o null pendiente")
        repository = source.get("repository")
        local = source.get("local_path")
        if commit is not None and not (nonempty_string(repository) or nonempty_string(local)):
            errors.append(f"{source_id}: falta repository o local_path de procedencia")
        reference = source.get("reference_manifest")
        if reference is not None:
            target = safe_path(root, reference)
            if target is None or not target.is_file():
                errors.append(f"{source_id}: reference_manifest inexistente o inseguro")
        if release and source.get("required") and commit is None:
            errors.append(f"Release: pin obligatorio pendiente: {source_id}")
    unresolved = lock.get("unresolved")
    if not string_list(unresolved, empty_ok=True):
        errors.append("upstream.lock.json: unresolved debe ser lista de strings")
    elif release and unresolved:
        errors.append(f"Release: {len(unresolved)} pendientes explicitos en el lock")
    if release:
        pending = [t["id"] for t in by_id.values() if t.get("required") and t.get("status") != "done"]
        if pending:
            errors.append("Release: tareas obligatorias sin terminar: " + ", ".join(pending))
    return errors


def ready_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data["tasks"]
    by_id = {task["id"]: task for task in tasks}
    return [task for task in tasks if task["status"] == "pending"
            and all(by_id[dep]["status"] == "done" for dep in task["depends"])]


def show_task(task: dict[str, Any]) -> None:
    print(f"{task['id']} - {task['title']}")
    print(f"Estado: {task['status']} | Hito: {task['milestone']} | Obligatoria: {task['required']}")
    print("Dependencias: " + (", ".join(task["depends"]) or "ninguna"))
    for field, title in (("read", "Leer"), ("touch", "Zonas editables"),
                         ("steps", "Pasos"), ("checks", "Checks (scope de la ficha)"),
                         ("acceptance", "Aceptacion")):
        print(f"\n{title}:")
        for index, value in enumerate(task[field], 1):
            print(f"  {index}. {value}")
    print("\nBloqueo: " + task["stop"])
    note = task.get("evidence")
    print("Registro: " + (note or "opcional; Git + checks son el registro normal"))


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validator = sub.add_parser("validate", help="Verificar coherencia, no el emulador")
    validator.add_argument("--release", action="store_true", help="Exigir tareas/pins obligatorios completos")
    sub.add_parser("next", help="Primera tarea preparada segun prioridad del backlog")
    show = sub.add_parser("show", help="Mostrar una ficha sin ejecutarla")
    show.add_argument("task_id")
    args = parser.parse_args(argv)
    try:
        data = json.loads((root / "plan/tasks.json").read_text(encoding="utf-8"))
        lock = json.loads((root / "plan/upstream.lock.json").read_text(encoding="utf-8"))
        errors = validate_plan(root, data, lock, release=getattr(args, "release", False))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"ERROR de lectura: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    if args.command == "validate":
        total = len(data["tasks"])
        required = sum(task["required"] for task in data["tasks"])
        print(f"PLAN VALIDO: {total} tareas ({required} obligatorias), DAG sin ciclos, referencias coherentes.")
        print("Este resultado no ejecuta ni certifica pruebas del emulador.")
        missing = [source["id"] for source in lock["sources"] if source["required"] and source["commit"] is None]
        if missing:
            print("Pins de implementacion pendientes: " + ", ".join(missing))
        return 0
    if args.command == "show":
        task = next((item for item in data["tasks"] if item["id"] == args.task_id), None)
        if task is None:
            print(f"ERROR: tarea desconocida {args.task_id}", file=sys.stderr)
            return 2
        show_task(task)
        return 0
    progress = [task for task in data["tasks"] if task["status"] == "in_progress"]
    candidates = progress or ready_tasks(data)
    if not candidates:
        unfinished = [task for task in data["tasks"] if task["status"] != "done"]
        print("No hay tarea preparada; revisar bloqueos." if unfinished else "Todas las tareas estan terminadas.")
        return 1 if unfinished else 0
    first = candidates[0]
    print(("CONTINUAR: " if progress else "SIGUIENTE: ") + f"{first['id']} - {first['title']}")
    print(f"Leer: python3 tools/plan.py show {first['id']}")
    print("No se ha ejecutado ni cambiado el estado de ninguna tarea.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
