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
GUEST_SHA256 = "a244f88642163ab6163d92d3ba3ed004a5e02c5eb2f94b99f41f89484c80a2b2"
SOURCES_SHA256 = "dfaf5c5ec4fc81cc95ccc59f4ce5e88fb2c74e11d1784bdb1fa817e15aff0c0b"


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


def apply(target, guest, sources):
	target = target.resolve(strict=True)
	guest = guest.resolve(strict=True)
	sources = sources.resolve(strict=True)
	verify_file(guest, GUEST_SHA256)
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
		run_git(target, "apply", "--ignore-space-change", "--check", str(PATCH))
		run_git(target, "apply", "--ignore-space-change", str(PATCH))
	except subprocess.CalledProcessError as error:
		raise IntegrationError(error.stderr.strip() or "el parche no se puede aplicar") from error
	destination = target / "waterbox/puae61"
	shutil.copyfile(guest, destination / "puae61-x64.wbx")
	shutil.copyfile(sources, destination / "source.tar.gz")
	verify_file(destination / "puae61-x64.wbx", GUEST_SHA256)
	verify_file(destination / "source.tar.gz", SOURCES_SHA256)
	print("PUAE61 beta21 aplicado; revise el diff y añada puae61-x64.wbx con git add -f")


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("target", type=Path, help="checkout limpio de LongHawk")
	parser.add_argument("guest", type=Path, help="guest beta21 puae6.wbx normalizado")
	parser.add_argument("sources", type=Path, help="source.tar.gz correspondiente")
	args = parser.parse_args()
	apply(args.target, args.guest, args.sources)


if __name__ == "__main__":
	main()
