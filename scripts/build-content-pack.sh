#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# One clean assembly of the audited content pack, inside the WP0 builder image,
# from a deleted build tree, offline.
#
# The assembly runs in a container for the same reason the browser build does:
# the output bytes depend on the interpreter and the zlib it links, so a
# reproducibility claim needs those pinned rather than inherited from whatever
# host happens to run the script. The builder image is the one the baseline
# already pins; nothing here re-states its digest.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="${CONTAINER_RUNTIME:-docker}"
archive_dir="${repo_dir}/build/content-sources"
output_dir="${repo_dir}/build/content-pack"
require_clean=1
print_image_only=0

producer_commit_override=""

usage() {
  cat <<'EOF'
usage: build-content-pack.sh [options]

  --archive-dir DIR         verified upstream archives (default: build/content-sources)
  --output-dir DIR          assembly root, deleted first (default: build/content-pack)
  --allow-dirty-worktree    rehearsal only; the manifest records the commit
  --print-image             print the locked builder image reference and exit
  --producer-commit SHA     reproduce a recorded build: stamp this commit
                            instead of HEAD. The checkout must still be clean
                            and is what is actually built; this only names the
                            source commit the committed records attribute the
                            artifacts to, which for a reissue is the commit
                            before the one carrying those records.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive-dir)
      archive_dir="$(readlink -m "${2:?--archive-dir needs a path}")"
      shift 2
      ;;
    --output-dir)
      output_dir="$(readlink -m "${2:?--output-dir needs a path}")"
      # The assembly root is deleted on every run, so it may only ever be
      # inside this repository's gitignored build directory.
      case "${output_dir}" in
        "${repo_dir}/build/"*) ;;
        *)
          printf -- '--output-dir must be inside %s/build\n' "${repo_dir}" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --allow-dirty-worktree)
      require_clean=0
      shift
      ;;
    --print-image)
      print_image_only=1
      shift
      ;;
    --producer-commit)
      producer_commit_override="${2:?--producer-commit needs a commit}"
      if [[ ! "${producer_commit_override}" =~ ^[0-9a-f]{40}$ ]]; then
        printf -- '--producer-commit must be a full 40-character commit id\n' >&2
        exit 2
      fi
      shift 2
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

builder_image="$(python3 "${repo_dir}/scripts/baseline-inputs.py" builder-image)"
if [[ ${print_image_only} -eq 1 ]]; then
  printf '%s\n' "${builder_image}"
  exit 0
fi

python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null

producer_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
if [[ -n "${producer_commit_override}" ]]; then
  # Reproducing a recorded build. The tree that is compiled is still this
  # checkout — nothing is spoofed about *what* is built — but the stamp becomes
  # the commit the committed records name as the producer. A reissue cannot
  # avoid needing this: its records are written one commit after the sources
  # they describe, so the checkout that publishes them is never the checkout
  # they were built from. scripts/reproduce-release.sh reads that commit out of
  # the records rather than taking it from whoever runs the build.
  producer_commit="${producer_commit_override}"
fi
if [[ "${require_clean}" -eq 1 ]]; then
  # The default --porcelain already honours .gitignore, so the gitignored build
  # directory stays invisible while an untracked source file does not.
  if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1)" ]]; then
    printf 'the arena-web worktree must be clean: the manifest records commit %s\n' \
      "${producer_commit}" >&2
    exit 1
  fi
fi

rm -rf "${output_dir}"
mkdir -p "${output_dir}"

runtime_arguments=(
  --rm
  --cap-drop all
  --network none
  --platform linux/amd64
  --pull never
  --security-opt label=disable
  --security-opt no-new-privileges
  --user "$(id -u):$(id -g)"
  --env "ARENA_PRODUCER_COMMIT=${producer_commit}"
  --env "HOME=/tmp"
  --volume "${repo_dir}:/src:ro"
  --volume "${archive_dir}:/archives:ro"
  --volume "${output_dir}:/work:rw"
  --workdir /work
  --entrypoint /bin/bash
)

# Rootless Podman maps the invoking user to container UID 0 by default, which
# would leave root-owned files in the assembly tree.
if [[ "${runtime}" == *podman* ]]; then
  runtime_arguments+=(--userns=keep-id)
fi

printf 'assembling with %s\n' "${builder_image}"
"${runtime}" run "${runtime_arguments[@]}" \
  "${builder_image}" \
  /src/scripts/build-content-pack-in-container.sh
