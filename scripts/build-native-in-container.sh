#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Runs inside the pinned WP5 native toolchain image. It is never meant to be
# run on a host: `scripts/build-native.sh` supplies the mounts, the fixed
# container paths and the determinism environment.
#
#   /src   read-only export of the pinned ioq3 commit, without Git metadata
#   /work  writable CMake binary directory; the only writable mount

set -euo pipefail

: "${ARENA_NATIVE_TARGET:?expected build target was not supplied}"
: "${ARENA_BUILD_JOBS:?build parallelism was not supplied}"
: "${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH was not supplied}"

export LC_ALL=C
export LANG=C
export TZ=UTC

case "${ARENA_NATIVE_TARGET}" in
  server)
    # The dedicated target compiles with DEDICATED/BOTLIB and the null client
    # stubs and links only ${CMAKE_DL_LIBS} and m. No renderer, no client, and
    # deliberately no game modules: the QVMs this server loads are the accepted
    # WP1 artifacts, so building a second set here would create a second
    # identity for the same bytecode and would run the restricted lcc build
    # tool for no reason.
    configure_options=(
      -DBUILD_SERVER=ON
      -DBUILD_CLIENT=OFF
      -DBUILD_RENDERER_GL1=OFF
      -DBUILD_RENDERER_GL2=OFF
      -DBUILD_GAME_LIBRARIES=OFF
      -DBUILD_GAME_QVMS=OFF
    )
    ;;
  client)
    # The matching native test client. It is the census instrument, not a
    # distributed artifact, and it loads the same accepted WP1 QVMs.
    configure_options=(
      -DBUILD_SERVER=OFF
      -DBUILD_CLIENT=ON
      -DBUILD_GAME_LIBRARIES=OFF
      -DBUILD_GAME_QVMS=OFF
    )
    ;;
  *)
    printf 'unknown native build target %s\n' "${ARENA_NATIVE_TARGET}" >&2
    exit 2
    ;;
esac

{
  printf 'target: %s\n' "${ARENA_NATIVE_TARGET}"
  printf 'cc: %s\n' "$(cc --version | head -1)"
  printf 'cmake: %s\n' "$(cmake --version | head -1)"
  printf 'make: %s\n' "$(make --version | head -1)"
  printf 'ld: %s\n' "$(ld --version | head -1)"
  printf 'libc: %s\n' "$(dpkg-query -W -f='${Version}' libc6)"
  printf 'source-date-epoch: %s\n' "${SOURCE_DATE_EPOCH}"
} >/work/toolchain.txt

cat /work/toolchain.txt

cmake -S /src -B /work -DCMAKE_BUILD_TYPE=Release "${configure_options[@]}"
cmake --build /work --parallel "${ARENA_BUILD_JOBS}"

# The QVM build tools are host executables built from code/tools/lcc, whose
# 1998 terms restrict commercial use. This configuration does not build the
# game modules at all, so none of them should exist; prove it rather than
# assume it.
#
# The result is captured in a variable rather than piped: under `pipefail` a
# `find | grep -q` pipeline can report the writer's SIGPIPE status and let a
# real match pass as a non-match.
stray_tool="$(find /work \
  \( -name 'q3lcc*' -o -name 'q3rcc*' -o -name 'q3cpp*' -o -name 'lburg*' \
  -o -name 'q3asm*' \) -print -quit)"
if [[ -n "${stray_tool}" ]]; then
  printf 'refusing to accept build: QVM build tool %s was produced\n' \
    "${stray_tool}" >&2
  exit 1
fi
