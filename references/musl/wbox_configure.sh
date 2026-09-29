#!/bin/sh
set -e
if [ -z "$SYSROOT" ]; then export SYSROOT="$(realpath "$(dirname "$0")/../sysroot")"; fi
if [ -z "$CC" ]; then export CC="clang"; fi

export CFLAGS="-Werror -fvisibility=hidden -mcmodel=large -mstack-protector-guard=global -fno-pic -fno-pie -fcf-protection=none" # TODO: Add -flto
./configure --target=waterbox --build=waterbox --disable-shared --prefix="$SYSROOT" --syslibdir="$SYSROOT/syslib"
