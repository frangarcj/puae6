#!/usr/bin/env python3
"""Apply the pinned PUAE61 beta21 integration to a clean LongHawk checkout."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
PATCH = HERE / "0001-puae61-beta21.patch"
LONGHAWK_BASE = "8de36990487237db3c50bf446e6af69671a89a9e"
PUAE6_GUEST_SHA256 = "c3aaf5889c7002b6a905a0b8af517d9627e772de255238d4b1c319216a958be2"
PUAE61_GUEST_SHA256 = "6893d33541cf28cf27a244aec2950f6f4f3bde5f3d3036a5acdbdeb1a83c8f46"
SOURCES_SHA256 = "791a9fdf28bd0f710b5d52d834d407a9e401ceb4598cfeed9bbec3ded4d2b171"


class IntegrationError(RuntimeError):
	pass


def run_git(target, *args):
	return subprocess.run(
		["git", *args], cwd=target, check=True, text=True,
		stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def verify_file(path, expected):
	if path.is_symlink() or not path.is_file():
		raise IntegrationError(f"falta un archivo regular: {path}")
	with path.open("rb") as stream:
		actual = hashlib.file_digest(stream, "sha256").hexdigest()
	if actual != expected:
		raise IntegrationError(f"SHA-256 inesperado para {path.name}: {actual}")


def apply(target, puae6_guest, puae61_guest, sources):
	target = target.resolve(strict=True)
	puae6_guest = puae6_guest.resolve(strict=True)
	puae61_guest = puae61_guest.resolve(strict=True)
	sources = sources.resolve(strict=True)
	verify_file(puae6_guest, PUAE6_GUEST_SHA256)
	verify_file(puae61_guest, PUAE61_GUEST_SHA256)
	verify_file(sources, SOURCES_SHA256)
	try:
		root = Path(run_git(target, "rev-parse", "--show-toplevel")).resolve()
		head = run_git(target, "rev-parse", "HEAD")
		status = run_git(target, "status", "--porcelain", "--untracked-files=all")
	except subprocess.CalledProcessError as error:
		raise IntegrationError(error.stderr.strip() or "el destino no es un checkout Git") from error
	if root != target:
		raise IntegrationError(f"el destino debe ser la raiz del checkout: {root}")
	if head != LONGHAWK_BASE:
		raise IntegrationError(f"LongHawk debe estar exactamente en {LONGHAWK_BASE}, no {head}")
	if status:
		raise IntegrationError("el checkout de LongHawk no esta limpio")
	try:
		# El checkout fijado conserva varios ficheros CRLF; el parche se guarda LF
		# para que el artefacto textual sea estable en cualquier host.
		run_git(target, "apply", "--unidiff-zero", "--ignore-space-change", "--check", str(PATCH))
		run_git(target, "apply", "--unidiff-zero", "--ignore-space-change", str(PATCH))
	except subprocess.CalledProcessError as error:
		raise IntegrationError(error.stderr.strip() or "el parche no se puede aplicar") from error
	puae6_destination = target / "waterbox/puae6/puae6-x64.wbx"
	puae61_destination = target / "waterbox/puae61"
	shutil.copyfile(puae6_guest, puae6_destination)
	shutil.copyfile(puae61_guest, puae61_destination / "puae61-x64.wbx")
	shutil.copyfile(sources, puae61_destination / "source.tar.gz")
	verify_file(puae6_destination, PUAE6_GUEST_SHA256)
	verify_file(puae61_destination / "puae61-x64.wbx", PUAE61_GUEST_SHA256)
	verify_file(puae61_destination / "source.tar.gz", SOURCES_SHA256)
	print("PUAE6/PUAE61 aplicados; revise el diff y añada ambos guests x64 con git add -f")


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("target", type=Path, help="checkout limpio de LongHawk")
	parser.add_argument("puae6_guest", type=Path, help="guest PUAE6 x64 normalizado")
	parser.add_argument("puae61_guest", type=Path, help="guest PUAE61 beta21 x64 normalizado")
	parser.add_argument("sources", type=Path, help="source.tar.gz correspondiente")
	args = parser.parse_args()
	apply(args.target, args.puae6_guest, args.puae61_guest, args.sources)


if __name__ == "__main__":
	main()
