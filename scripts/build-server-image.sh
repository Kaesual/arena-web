#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Assemble the arena-web dedicated server image from the separately pinned WP5
# runtime base, the native server binary this repository built, the accepted
# WP1 QVM and the audited WP3 content pack.
#
# Every byte the image adds on top of the base is staged only after its SHA-256
# and byte length match a committed manifest entry, and the built image is then
# inspected: the exact content set, its digests, and the per-package copyright
# files the base carries, which arena-web is obliged to preserve.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# The WP5 native steps use Podman-only constructs; arena_require_container_runtime
# is called before the first container use, so a metadata query still works
# without one.
# shellcheck source=scripts/container-runtime.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/container-runtime.sh"
runtime="$(arena_container_runtime)"
server_dir="${repo_dir}/build/native-server/tree/Release"
engine_dir="${repo_dir}/build/browser/tree/Release"
content_dir="${repo_dir}/build/content-pack"
output_dir="${repo_dir}/build/server-image"
image_tag="arena-web-server:latest"
require_clean=1
print_tag_only=0

producer_commit_override=""

usage() {
  cat <<'EOF'
usage: build-server-image.sh [options]

  --server-dir DIR          native server build output
                            (default: build/native-server/tree/Release)
  --engine-dir DIR          accepted browser build output, for the QVM
                            (default: build/browser/tree/Release)
  --content-dir DIR         accepted content assembly (default: build/content-pack)
  --output-dir DIR          staging root, deleted first (default: build/server-image)
  --tag TAG                 image tag (default: arena-web-server:latest)
  --allow-dirty-worktree    rehearsal only; the record states the commit
  --print-tag               print the image tag and exit
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
    --server-dir)
      server_dir="$(readlink -m "${2:?--server-dir needs a path}")"
      shift 2
      ;;
    --engine-dir)
      engine_dir="$(readlink -m "${2:?--engine-dir needs a path}")"
      shift 2
      ;;
    --content-dir)
      content_dir="$(readlink -m "${2:?--content-dir needs a path}")"
      shift 2
      ;;
    --output-dir)
      output_dir="$(readlink -m "${2:?--output-dir needs a path}")"
      case "${output_dir}" in
        "${repo_dir}/build/"*) ;;
        *)
          printf -- '--output-dir must be inside %s/build\n' "${repo_dir}" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --tag)
      image_tag="${2:?--tag needs a value}"
      shift 2
      ;;
    --allow-dirty-worktree)
      require_clean=0
      shift
      ;;
    --print-tag)
      print_tag_only=1
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

if [[ ${print_tag_only} -eq 1 ]]; then
  printf '%s\n' "${image_tag}"
  exit 0
fi

arena_require_container_runtime "${runtime}"

python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null
git -C "${repo_dir}" diff --check
git -C "${repo_dir}" diff --cached --check

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
  if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1)" ]]; then
    printf 'refusing to build: the arena-web checkout is not clean\n' >&2
    printf 'an accepted image records its producing commit, so it needs one\n' >&2
    exit 1
  fi
fi

runtime_base="$(python3 "${repo_dir}/scripts/baseline-inputs.py" server-runtime-image)"
engine_commit="$(python3 "${repo_dir}/scripts/baseline-inputs.py" engine-commit)"
baseline_identity="$(python3 "${repo_dir}/scripts/baseline-inputs.py" baseline-identity)"

context_dir="${output_dir}/context"
rm -rf "${output_dir}"
mkdir -p "${context_dir}"

# Stage the game tree, then the binary. The tree is digest-checked against the
# committed manifests; the binary is this repository's own build output and is
# recorded by digest rather than checked against one.
python3 "${repo_dir}/scripts/stage-server-tree.py" \
  --target "${context_dir}/game" \
  --engine-dir "${engine_dir}" \
  --content-dir "${content_dir}"

server_binary="${server_dir}/$(python3 -c '
import json, sys
print(json.load(open(sys.argv[1]))["serverBinary"])
' "${repo_dir}/native/server-profile.json")"
if [[ ! -f "${server_binary}" ]]; then
  printf 'the native server binary %s does not exist; build it first\n' \
    "${server_binary}" >&2
  exit 1
fi
install -m 0755 "${server_binary}" "${context_dir}/ioq3ded"

printf 'building %s\n' "${image_tag}"
printf 'from     %s\n' "${runtime_base}"

# --timestamp fixes every layer's mtime, so two assemblies of the same content
# and base produce the same image rather than two images that differ only in
# when they were made.
#
# --no-cache is not belt-and-braces. The Containerfile stamps the engine commit,
# the baseline identity and the producing commit into labels from build args,
# and the layer cache was observed matching that LABEL step on its unexpanded
# instruction text: a rebuild after the pin moved produced an image whose id and
# labels were the *previous* build's, so the image described a baseline it was
# not built from and the two-image reproducibility check compared a cache hit
# with itself. An image that carries its own provenance may not be assembled
# from a cache keyed on anything less than that provenance. The build is a base
# plus two COPY layers, so rebuilding it costs almost nothing.
"${runtime}" build \
  --no-cache \
  --file "${repo_dir}/native/server.Containerfile" \
  --build-arg "ARENA_SERVER_RUNTIME_BASE=${runtime_base}" \
  --build-arg "ARENA_ENGINE_COMMIT=${engine_commit}" \
  --build-arg "ARENA_BASELINE_IDENTITY=${baseline_identity}" \
  --build-arg "ARENA_PRODUCER_COMMIT=${producer_commit}" \
  --network none \
  --platform linux/amd64 \
  --pull=never \
  --timestamp 0 \
  --tag "${image_tag}" \
  "${context_dir}"

image_id="$("${runtime}" image inspect --format '{{.Id}}' "${image_tag}")"
printf 'built %s (%s)\n' "${image_tag}" "${image_id}"

# Inspect the built image rather than the context: what a reviewer cares about
# is what the distributed bytes are, not what was handed to the builder.
python3 "${repo_dir}/scripts/verify-server-image.py" \
  --runtime "${runtime}" \
  --tag "${image_tag}" \
  --report "${output_dir}/image-content.json" \
  --server-binary "${server_binary}" \
  --producer-commit "${producer_commit}"
