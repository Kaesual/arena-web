#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Runs inside the pinned Emscripten builder image. It is never meant to be run
# on a host: `scripts/build-browser.sh` supplies the mounts, the fixed
# container paths and the determinism environment.
#
#   /src    read-only export of the pinned ioq3 commit, without Git metadata
#   /ports  read-only, pre-fetched Emscripten port sources (offline build)
#   /work   writable output tree; /work/tree is the CMake binary directory

set -euo pipefail

: "${ARENA_EMSCRIPTEN_VERSION:?expected Emscripten version was not supplied}"
: "${ARENA_BUILD_JOBS:?build parallelism was not supplied}"
: "${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH was not supplied}"

export LC_ALL=C
export LANG=C
export TZ=UTC

actual_version="$(emcc -dumpversion)"
if [[ "${actual_version}" != "${ARENA_EMSCRIPTEN_VERSION}" ]]; then
  printf 'refusing to build: builder reports Emscripten %s, baseline requires %s\n' \
    "${actual_version}" "${ARENA_EMSCRIPTEN_VERSION}" >&2
  exit 1
fi

{
  printf 'emscripten-version: %s\n' "${actual_version}"
  printf 'emcc: %s\n' "$(emcc --version | head -1)"
  printf 'cmake: %s\n' "$(cmake --version | head -1)"
  printf 'host-c-compiler: %s\n' "$(cc --version | head -1)"
  printf 'node: %s\n' "$(node --version)"
  printf 'source-date-epoch: %s\n' "${SOURCE_DATE_EPOCH}"
  printf 'em-ports: %s\n' "${EM_PORTS:-<default>}"
} >/work/toolchain.txt

cat /work/toolchain.txt

# The official upstream Emscripten target, with no product CMake arguments
# beyond the build type upstream itself documents.
emcmake cmake -S /src -B /work/tree -DCMAKE_BUILD_TYPE=Release
cmake --build /work/tree --parallel "${ARENA_BUILD_JOBS}"

# The restrictively licensed QVM build tools are host executables produced in
# the CMake binary tree. Prove they never reach the distributable directory.
if find /work/tree/Release \
  \( -name 'q3lcc*' -o -name 'q3rcc*' -o -name 'q3cpp*' -o -name 'lburg*' \) \
  -print | grep -q .; then
  printf 'refusing to accept build: QVM build tools reached the distributable tree\n' >&2
  exit 1
fi
