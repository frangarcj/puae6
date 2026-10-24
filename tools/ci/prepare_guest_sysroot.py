"""Prepare the authenticated hosted guest ABI sysroot; never build an emulator."""
import json
from pathlib import Path

from tools import build_waterbox_guest_sysroot as guest
from tools.ci import prepare_hosted_tools as hosted

ROOT = Path(__file__).resolve().parents[2]
require = hosted.require


def prepare():
    wb = guest.wb; lock = wb.selected_toolchain()
    require(lock['toolchain_profile'] == 'hosted-macos26' and wb.source_audit_mode() == 'snapshot',
            'requires explicit hosted-macos26 snapshot inputs')
    cache = ROOT/'build/hosted-tools'
    require(guest.os.environ.get('P6_WATERBOX_HOSTED_TOOLS') in ('build/hosted-tools',str(cache)),
            'requires the explicit verified hosted tools loader')
    require(hosted.prepare(fetch=False) == cache, 'foreign hosted cache')
    manifest,_ = hosted.inputs()
    archiver = guest.selected_archiver(ROOT,lock['waterbox_guest_sysroot'],lock)
    rows = [row for row in manifest['normalized_macho'] if row['path'] == guest.HOSTED_AR.removeprefix('build/hosted-tools/')]
    require(len(rows) == 1, 'exactly one authenticated hosted archiver row required')
    row = rows[0]
    require(archiver['path'] == str(cache/row['path']) and row['sha256'] == archiver['sha256']
            and archiver['version'] == 'Homebrew LLVM version 23.1.4', 'hosted archiver pin differs')
    before = guest.identity(ROOT)[-1]
    output = ROOT/'build/waterbox-guest-sysroot'
    if not output.exists() and not output.is_symlink():
        result = guest.derive(root=ROOT,execute=True)
        require(result['status'] == 'PASS_DERIVED_ONLY', 'derivation did not execute')
    proof = guest.verify(root=ROOT)
    require(proof['inputs'] == before == guest.identity(ROOT)[-1], 'derived inputs changed')
    return dict(status='PASS_DERIVED_ONLY',scope='guest ABI sysroot preparation only; no emulator build or execution',
                inventory_sha256=proof['inventory_sha256'],libc_sha256=proof['libc_sha256'],receipt_sha256=proof['receipt_sha256'])


if __name__ == '__main__':
    try:
        result=prepare()
    except (OSError,ValueError,KeyError,guest.wb.BuildError) as error:
        print(json.dumps(dict(status='BLOCKED',diagnostic=str(error)))); raise SystemExit(2)
    print(json.dumps(result,sort_keys=True))
