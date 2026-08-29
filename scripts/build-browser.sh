#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# One accepted browser build of the pinned, unmodified ioquake3 Emscripten
# target, inside the WP0 builder image, from a deleted build tree, offline.
#
# Every identity comes from locks/baseline.json; nothing here re-states a
# digest that the lock already owns.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-docker}"
output_dir="${repo_dir}/build/browser"
ports_dir="${repo_dir}/build/emscripten-ports"
jobs="$(nproc)"
print_image_only=0

# Reproducible timestamps. CMake turns SOURCE_DATE_EPOCH into the compiled-in
# PRODUCT_DATE; without it the engine embeds __DATE__ and no two builds agree.
# The value is the pinned engine commit's own committer timestamp, so it is
# derived from the baseline rather than from the moment of the build.
expected_source_date_epoch=1784478090

usage() {
  cat <<'EOF'
usage: build-browser.sh [options]

  --output-dir DIR   build root (default: build/browser)
  --jobs N           compiler parallelism (default: nproc)
  --print-image      print the locked builder image reference and exit
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      output_dir="$(readlink -m "${2:?--output-dir needs a directory}")"
      # The build root is deleted on every run, so it may only ever be inside
      # the gitignored build directory.
      if [[ "${output_dir}" != "${repo_dir}/build/"?* ]]; then
        printf 'refusing to use %s: build output must live under %s/build\n' \
          "${output_dir}" "${repo_dir}" >&2
        exit 2
      fi
      shift 2
      ;;
    --jobs)
      jobs="${2:?--jobs needs a number}"
      shift 2
      ;;
    --print-image)
      print_image_only=1
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

baseline_input() {
  python3 "${repo_dir}/scripts/baseline-inputs.py" "$1"
}

builder_image="$(baseline_input builder-image)"
if [[ ${print_image_only} -eq 1 ]]; then
  printf '%s\n' "${builder_image}"
  exit 0
fi

builder_version="$(baseline_input builder-version)"
engine_commit="$(baseline_input engine-commit)"
engine_path="$(baseline_input engine-submodule-path)"
engine_dir="${repo_dir}/${engine_path}"

# The lock-to-submodule binding, the clean ioq3 checkout and every committed
# metadata record are gates for an accepted build, not an afterthought.
python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null
git -C "${repo_dir}" diff --check
git -C "${repo_dir}" diff --cached --check

if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1)" ]]; then
  printf 'refusing to build: the arena-web checkout is not clean\n' >&2
  printf 'an accepted build records its producing commit, so it needs one\n' >&2
  exit 1
fi
producer_commit="$(git -C "${repo_dir}" rev-parse HEAD)"

source_date_epoch="$(git -C "${engine_dir}" show -s --format=%ct HEAD)"
if [[ "${source_date_epoch}" != "${expected_source_date_epoch}" ]]; then
  printf 'refusing to build: pinned engine commit timestamp is %s, expected %s\n' \
    "${source_date_epoch}" "${expected_source_date_epoch}" >&2
  exit 1
fi

if [[ ! -d "${ports_dir}/sdl2" ]]; then
  printf 'refusing to build: %s is missing\n' "${ports_dir}/sdl2" >&2
  printf 'run scripts/fetch-emscripten-ports.sh once; accepted builds are offline\n' >&2
  exit 1
fi
"${repo_dir}/scripts/fetch-emscripten-ports.sh" --check >/dev/null

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
  --env "ARENA_EMSCRIPTEN_VERSION=${builder_version}"
  --env "EM_PORTS=/ports"
  --env "HOME=/tmp"
  --env "SOURCE_DATE_EPOCH=${source_date_epoch}"
  --volume "${output_dir}/source:/src:ro"
  --volume "${output_dir}/tree:/work:rw"
  --volume "${ports_dir}:/ports:ro"
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

printf 'building %s with %s (%s)\n' "${engine_commit}" "${builder_image}" "${builder_version}"
"${runtime}" run "${runtime_arguments[@]}" \
  "${builder_image}" \
  /arena-scripts/build-browser-in-container.sh 2>&1 |
  tee "${output_dir}/build.log"

python3 "${repo_dir}/scripts/emit-artifact-manifest.py" \
  --artifact-root "${output_dir}/tree/Release" \
  --output "${output_dir}/artifact-manifest.json" \
  --producer-commit "${producer_commit}" \
  --port-archive "$("${repo_dir}/scripts/fetch-emscripten-ports.sh" --print-archive)"

printf 'wrote %s\n' "${output_dir}/artifact-manifest.json"
