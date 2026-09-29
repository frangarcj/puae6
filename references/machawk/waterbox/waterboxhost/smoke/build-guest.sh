#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOST_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TARGET_DIR="$HOST_DIR/target/debug"
MUSL_DIR="$HOST_DIR/target/musl-waterbox-aarch64"
MUSL_COMMIT=22440a465d6f7d3241e73bdf990c2b8026022625

mkdir -p "$TARGET_DIR"

if [ ! -d "$MUSL_DIR/.git" ]; then
	git clone --filter=blob:none --branch waterbox_aarch64 --no-checkout \
		https://github.com/Masterjun3/musl.git "$MUSL_DIR"
	git -C "$MUSL_DIR" checkout "$MUSL_COMMIT"
fi

if [ "$(git -C "$MUSL_DIR" rev-parse HEAD)" != "$MUSL_COMMIT" ]; then
	echo "unexpected Waterbox musl checkout in $MUSL_DIR" >&2
	exit 1
fi

docker run --rm \
	-v "$HOST_DIR/target:/work" \
	-v "$SCRIPT_DIR:/smoke:ro" \
	-w /smoke \
	ubuntu:24.04 sh -eu -c '
		apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
			clang lld make gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu >/dev/null
		if [ ! -f /work/musl-sysroot/lib/libc.a ]; then
			cd /work/musl-waterbox-aarch64
			SYSROOT=/work/musl-sysroot sh ./wbox_configure_aarch64.sh
			make -s -j2
			make -s install
		fi
		/work/musl-sysroot/bin/musl-clang \
			--target=aarch64-linux-musl \
			-O2 -fvisibility=hidden -mcmodel=large \
			-mstack-protector-guard=global -fno-pic -fno-pie \
			-static -no-pie -Wl,--eh-frame-hdr,-O2 \
			-Wl,-T,/smoke/linkscript_aarch64.ld \
			/smoke/smoke.c -o /work/debug/smoke.wbx
	'
