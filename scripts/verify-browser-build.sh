#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# WP1 acceptance: two complete clean builds of the pinned browser target must
# produce identical artifact manifests and byte-identical engine artifacts.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verify_dir="${repo_dir}/build/verify"
jobs="$(nproc)"

if [[ $# -gt 0 ]]; then
  case "$1" in
    --jobs)
      jobs="${2:?--jobs needs a number}"
      ;;
    *)
      printf 'usage: %s [--jobs N]\n' "$0" >&2
      exit 2
      ;;
  esac
fi

rm -rf "${verify_dir}"
mkdir -p "${verify_dir}"

for attempt in 1 2; do
  printf '\n=== clean build %s of 2 ===\n' "${attempt}"
  "${repo_dir}/scripts/build-browser.sh" \
    --output-dir "${verify_dir}/build-${attempt}" \
    --jobs "${jobs}"
done

first="${verify_dir}/build-1"
second="${verify_dir}/build-2"

printf '\n=== comparing the two builds ===\n'
if ! cmp --silent "${first}/artifact-manifest.json" "${second}/artifact-manifest.json"; then
  printf 'artifact manifests differ between the two clean builds\n' >&2
  diff -u "${first}/artifact-manifest.json" "${second}/artifact-manifest.json" >&2 || true
  exit 1
fi
if ! diff --recursive --brief "${first}/tree/Release" "${second}/tree/Release"; then
  printf 'distributable engine artifacts differ between the two clean builds\n' >&2
  exit 1
fi

manifest_digest="$(sha256sum "${first}/artifact-manifest.json" | cut -d' ' -f1)"
printf 'identical artifact manifest sha256:%s\n' "${manifest_digest}"
printf 'byte-identical distributable artifacts:\n'
(cd "${first}/tree/Release" && find . -type f -printf '%P\n' | sort |
  while IFS= read -r artifact; do
    printf '  %s  %s\n' "$(sha256sum "${artifact}" | cut -d' ' -f1)" "${artifact}"
  done)
