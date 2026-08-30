#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# One accepted native build of the pinned ioquake3 tree, inside the WP5 native
# toolchain image, from a deleted build tree, offline.
#
# The pin is the fork's `web` branch: the upstream base the lock names, plus
# the patch series it enumerates beside it. The build target itself is still
# upstream's, unchanged.
#
#   --target server   the dedicated server that the server image carries
#   --target client   the native test client that drives the packet census
#
# Every identity comes from locks/baseline.json and
# locks/native-toolchain-packages.conf; nothing here re-states a digest that a
# lock already owns.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# The WP5 native steps use Podman-only constructs; arena_require_container_runtime
# is called before the first container use, so a metadata query still works
# without one.
# shellcheck source=scripts/container-runtime.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/container-runtime.sh"
runtime="$(arena_container_runtime)"
target="server"
output_dir=""
jobs="$(nproc)"
require_clean=1

# Reproducible timestamps. CMake turns SOURCE_DATE_EPOCH into the compiled-in
# PRODUCT_DATE; without it the engine embeds __DATE__ and no two builds agree.
# The value is the committer timestamp of the lock's upstream *base* commit, so
# it is derived from the baseline rather than from the moment of the build. It
# is the same value the browser build asserts, and it is the base rather than
# the pin for the reason build-browser.sh records: a renderer-only patch must
# not move a dedicated server that does not compile a line of it.
expected_source_date_epoch=1784478090

usage() {
  cat <<'EOF'
usage: build-native.sh [options]

  --target server|client    what to build (default: server)
  --output-dir DIR          build root, deleted first
                            (default: build/native-<target>)
  --jobs N                  compiler parallelism (default: nproc)
  --allow-dirty-worktree    rehearsal only; the manifest records the commit
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      target="${2:?--target needs server or client}"
      shift 2
      ;;
    --output-dir)
      output_dir="$(readlink -m "${2:?--output-dir needs a directory}")"
      shift 2
      ;;
    --jobs)
      jobs="${2:?--jobs needs a number}"
      if [[ ! "${jobs}" =~ ^[1-9][0-9]*$ ]]; then
        printf -- '--jobs must be a positive integer, got %s\n' "${jobs}" >&2
        exit 2
      fi
      shift 2
      ;;
    --allow-dirty-worktree)
      require_clean=0
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

if [[ -z "${output_dir}" ]]; then
  output_dir="${repo_dir}/build/native-${target}"
fi
# The build root is deleted on every run, so it may only ever be inside the
# gitignored build directory.
if [[ "${output_dir}" != "${repo_dir}/build/"?* ]]; then
  printf 'refusing to use %s: build output must live under %s/build\n' \
    "${output_dir}" "${repo_dir}" >&2
  exit 2
fi

baseline_input() {
  python3 "${repo_dir}/scripts/baseline-inputs.py" "$1"
}

toolchain_image="$("${repo_dir}/scripts/build-native-toolchain.sh" --print-tag)"
engine_commit="$(baseline_input engine-commit)"
engine_base_commit="$(baseline_input engine-upstream-base-commit)"
engine_path="$(baseline_input engine-submodule-path)"
engine_dir="${repo_dir}/${engine_path}"

# The lock-to-submodule binding, the clean ioq3 checkout and every committed
# metadata record are gates for an accepted build, not an afterthought.
python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null
git -C "${repo_dir}" diff --check
git -C "${repo_dir}" diff --cached --check

producer_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
if [[ "${require_clean}" -eq 1 ]]; then
  if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1)" ]]; then
    printf 'refusing to build: the arena-web checkout is not clean\n' >&2
    printf 'an accepted build records its producing commit, so it needs one\n' >&2
    exit 1
  fi
fi

arena_require_container_runtime "${runtime}"
if ! "${runtime}" image exists "${toolchain_image}"; then
  printf 'the native toolchain image %s does not exist\n' "${toolchain_image}" >&2
  printf 'build it with scripts/build-native-toolchain.sh\n' >&2
  exit 1
fi

source_date_epoch="$(git -C "${engine_dir}" show -s --format=%ct "${engine_base_commit}")"
if [[ "${source_date_epoch}" != "${expected_source_date_epoch}" ]]; then
  printf 'refusing to build: upstream base commit timestamp is %s, expected %s\n' \
    "${source_date_epoch}" "${expected_source_date_epoch}" >&2
  exit 1
fi

# A clean build starts from a deleted tree, and the build never writes into
# either Git source tree.
rm -rf "${output_dir}"
mkdir -p "${output_dir}/tree" "${output_dir}/source"

# Export exactly the pinned commit. This drops the submodule's Git metadata,
# which both pins the compiled content to the lock and keeps the engine's
# optional `git describe` product version out of the artifacts.
git -C "${engine_dir}" archive --format=tar "${engine_commit}" |
  tar -x -C "${output_dir}/source"

runtime_arguments=(
  --rm
  --cap-drop all
  --network none
  --platform linux/amd64
  --pull never
  --security-opt label=disable
  --security-opt no-new-privileges
  --user "$(id -u):$(id -g)"
  --env "ARENA_BUILD_JOBS=${jobs}"
  --env "ARENA_NATIVE_TARGET=${target}"
  --env "HOME=/tmp"
  --env "SOURCE_DATE_EPOCH=${source_date_epoch}"
  --volume "${output_dir}/source:/src:ro"
  --volume "${output_dir}/tree:/work:rw"
  --volume "${repo_dir}/scripts:/arena-scripts:ro"
  --workdir /work
  --entrypoint /bin/bash
)

# Rootless Podman maps the invoking user to container UID 0 by default, which
# would leave root-owned files in the build tree. Keeping the ID mapping lets
# the same non-root user contract hold for both runtimes.
if [[ "${runtime}" == *podman* ]]; then
  runtime_arguments+=(--userns=keep-id)
fi

printf 'building %s %s with %s\n' "${target}" "${engine_commit}" "${toolchain_image}"
"${runtime}" run "${runtime_arguments[@]}" \
  "${toolchain_image}" \
  /arena-scripts/build-native-in-container.sh 2>&1 |
  tee "${output_dir}/build.log"

printf 'producer commit %s\n' "${producer_commit}" > "${output_dir}/producer.txt"
printf 'built %s\n' "${output_dir}/tree/Release"
ls -l "${output_dir}/tree/Release"
