#!/bin/sh
set -e
if [ -z "$SYSROOT" ]; then export SYSROOT="$(realpath "$(dirname "$0")/../sysroot")"; fi
if [ -z "$CC" ]; then export CC="clang"; fi

export CFLAGS="-target aarch64-linux-gnu --sysroot=/usr/aarch64-linux-gnu -Werror -fvisibility=hidden -mcmodel=large -mstack-protector-guard=global -fno-pic -fno-pie -fcf-protection=none -ffixed-x18 -ffixed-x28" # TODO: Add -flto
export LDFLAGS="--target=aarch64-linux-musl --sysroot=/usr/aarch64-linux-gnu"

./configure --target=waterbox_aarch64 --build=waterbox_aarch64 --disable-shared --prefix="$SYSROOT" --syslibdir="$SYSROOT/syslib"
