#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-docker}"
validation_image="docker.io/emscripten/emsdk@sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475"

case "${1:-}" in
  "")
    ;;
  --print-image)
    printf '%s\n' "${validation_image}"
    exit 0
    ;;
  *)
    printf 'usage: %s [--print-image]\n' "$0" >&2
    exit 2
    ;;
esac

# Git metadata is intentionally not exposed to the read-only source container,
# so verify the lock-to-index-gitlink/clean-checkout binding on the host first.
python3 "${repo_dir}/scripts/validate-metadata.py"
git -C "${repo_dir}" diff --check
git -C "${repo_dir}" diff --cached --check

exec "${runtime}" run --rm \
  --cap-drop all \
  --network none \
  --platform linux/amd64 \
  --pull never \
  --read-only \
  --security-opt label=disable \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev \
  --user "$(id -u):$(id -g)" \
  --volume "${repo_dir}:/src:ro" \
  --workdir /src \
  --entrypoint /bin/bash \
  "${validation_image}" \
  /src/scripts/check.sh --without-git-metadata
