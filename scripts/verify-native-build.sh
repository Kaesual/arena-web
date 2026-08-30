#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# WP5 acceptance: two clean native builds of the same target, in the same pinned
# toolchain, must produce byte-identical binaries — and for the server, two
# image builds from them must produce the same image id and an artifact manifest
# that still agrees with the committed provenance/arena-web-server.json.
#
# The dedicated server is a distributed artifact, so its identity has to be a
# property of its inputs rather than of the moment it was compiled, and a
# rebuild that no longer matches the committed record has to say so.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=scripts/container-runtime.sh
source "${repo_dir}/scripts/container-runtime.sh"
runtime="$(arena_container_runtime)"
target="server"
verify_dir=""
extra_args=()
committed_manifest="${repo_dir}/provenance/arena-web-server.json"

usage() {
  cat <<'EOF'
usage: verify-native-build.sh [--target server|client] [--allow-dirty-worktree]

For --target server this additionally builds the server image twice, compares
the two image ids, and compares the regenerated artifact manifest with the
committed provenance/arena-web-server.json, ignoring only `producer`.
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

if [[ "${target}" != "server" ]]; then
  exit 0
fi

# The binary is only half of the distributed artifact. Build the image from each
# of the two identical binaries and require the same image id, and require the
# regenerated manifest to still be the committed one.
image_ids=()
for attempt in 1 2; do
  printf '\n=== server image build %s of 2 ===\n' "${attempt}"
  "${repo_dir}/scripts/build-server-image.sh" \
    --server-dir "${verify_dir}/build-${attempt}/tree/Release" \
    --output-dir "${verify_dir}/image-${attempt}" \
    --tag "arena-web-server:verify-${attempt}" \
    "${extra_args[@]}"
  image_ids+=("$("${runtime}" image inspect --format '{{.Id}}' \
    "arena-web-server:verify-${attempt}")")
done

printf '\n=== comparing the two images ===\n'
if [[ "${image_ids[0]}" != "${image_ids[1]}" ]]; then
  printf 'the two image builds differ: %s vs %s\n' \
    "${image_ids[0]}" "${image_ids[1]}" >&2
  exit 1
fi
printf 'two image builds produced the same image id %s\n' "${image_ids[0]}"

if ! diff --brief \
  "${verify_dir}/image-1/artifact-manifest.json" \
  "${verify_dir}/image-2/artifact-manifest.json"; then
  printf 'the two image builds emitted different artifact manifests\n' >&2
  exit 1
fi

# The committed manifest is the evidence of an earlier accepted build. A rebuild
# from a later commit records its own producing commit, so everything except
# `producer` must still agree exactly.
python3 - "${committed_manifest}" "${verify_dir}/image-1/artifact-manifest.json" <<'PY'
import json
import sys

committed_path, rebuilt_path = sys.argv[1:3]
committed, rebuilt = (
    json.loads(open(path, encoding="utf-8").read()) for path in (committed_path, rebuilt_path)
)
if committed.pop("producer") != rebuilt["producer"]:
    print("note: the rebuild records a different producing commit")
rebuilt.pop("producer")
if committed != rebuilt:
    raise SystemExit(
        f"rebuild disagrees with the committed artifact manifest {committed_path}"
    )
print("rebuild agrees with the committed artifact manifest")
PY
