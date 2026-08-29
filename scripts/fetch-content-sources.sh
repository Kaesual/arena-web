#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Obtain the upstream content archives pinned by content/pack-recipe.json.
#
# This is the only step that uses the network. Every archive is fetched from a
# content-addressed snapshot.debian.org URL and accepted only if its size and
# SHA-256 match the recipe exactly. Downloads land in the gitignored build
# directory and are never committed.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
archive_dir="${repo_dir}/build/content-sources"
mode="fetch"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      mode="check"
      shift
      ;;
    --archive-dir)
      archive_dir="${2:?--archive-dir needs a path}"
      shift 2
      ;;
    -h | --help)
      printf 'usage: %s [--check] [--archive-dir DIR]\n' "$0"
      exit 0
      ;;
    *)
      printf 'usage: %s [--check] [--archive-dir DIR]\n' "$0" >&2
      exit 2
      ;;
  esac
done

mkdir -p "${archive_dir}"

# Materialise the list first: a process substitution's exit status is not
# observable in the loop, so a missing or malformed recipe would otherwise let
# --check report success having verified nothing.
sources="$(python3 - "${repo_dir}/content/pack-recipe.json" <<'PYTHON'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    recipe = json.load(handle)
for source in sorted(recipe["sources"], key=lambda item: item["id"]):
    print(
        "\t".join(
            (source["fileName"], source["url"], str(source["size"]), source["sha256"])
        )
    )
PYTHON
)"

expected_count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["sources"]))' \
  "${repo_dir}/content/pack-recipe.json")"
actual_count="$(printf '%s\n' "${sources}" | grep -c .)"
if [[ -z "${sources}" || "${actual_count}" -ne "${expected_count}" ]]; then
  printf 'the recipe lists %s sources but %s were resolved\n' \
    "${expected_count}" "${actual_count}" >&2
  exit 1
fi

status=0
while IFS=$'\t' read -r name url size sha256; do
  target="${archive_dir}/${name}"
  if [[ ! -f "${target}" ]]; then
    if [[ "${mode}" == "check" ]]; then
      printf 'missing  %s\n' "${name}" >&2
      status=1
      continue
    fi
    printf 'fetching %s\n' "${name}"
    curl --fail --location --silent --show-error \
      --output "${target}.part" "${url}"
    mv "${target}.part" "${target}"
  fi
  actual_size="$(stat -c %s "${target}")"
  actual_sha="$(sha256sum "${target}" | cut -d' ' -f1)"
  if [[ "${actual_size}" != "${size}" || "${actual_sha}" != "${sha256}" ]]; then
    printf 'MISMATCH %s: got %s bytes sha256:%s, recipe pins %s bytes sha256:%s\n' \
      "${name}" "${actual_size}" "${actual_sha}" "${size}" "${sha256}" >&2
    status=1
    continue
  fi
  printf 'verified %s (%s bytes)\n' "${name}" "${size}"
done <<< "${sources}"

if [[ "${status}" -ne 0 ]]; then
  printf 'content sources are not usable\n' >&2
fi
exit "${status}"
