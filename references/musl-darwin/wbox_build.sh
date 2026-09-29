#!/bin/sh
set -e
if [ -z "$SYSROOT" ]; then export SYSROOT="$(realpath "$(dirname "$0")/../sysroot")"; fi

mkdir -p "$SYSROOT"
make -j
make install
