#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# WP5 acceptance: two clean native builds of the same target, in the same pinned
# toolchain, must produce byte-identical binaries.
#
# The dedicated server is a distributed artifact, so its identity has to be a
# property of its inputs rather than of the moment it was compiled.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
target="server"
verify_dir=""
extra_args=()

usage() {
  cat <<'EOF'
usage: verify-native-build.sh [--target server|client] [--allow-dirty-worktree]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      target="${2:?--target needs server or client}"
      shift 2
      ;;
    --allow-dirty-worktree)
      extra_args+=(--allow-dirty-worktree)
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

case "${target}" in
  server | client) ;;
  *)
    printf -- '--target must be server or client, got %s\n' "${target}" >&2
    exit 2
    ;;
esac

verify_dir="${repo_dir}/build/native-verify-${target}"
rm -rf "${verify_dir}"
mkdir -p "${verify_dir}"

for attempt in 1 2; do
  printf '\n=== clean %s build %s of 2 ===\n' "${target}" "${attempt}"
  "${repo_dir}/scripts/build-native.sh" \
    --target "${target}" \
    --output-dir "${verify_dir}/build-${attempt}" \
    "${extra_args[@]}"
done

first="${verify_dir}/build-1/tree/Release"
second="${verify_dir}/build-2/tree/Release"

printf '\n=== comparing the two builds ===\n'
if ! diff --recursive --brief "${first}" "${second}"; then
  printf 'the two clean %s builds differ\n' "${target}" >&2
  exit 1
fi
printf 'two clean %s builds are byte-identical:\n' "${target}"
(cd "${first}" && find . -type f -print0 | LC_ALL=C sort -z |
  xargs -0 sha256sum)
