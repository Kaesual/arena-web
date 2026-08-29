#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# One clean assembly of the audited content pack.
#
# The assembly is offline: it reads only the digest-verified archives already in
# the archive directory and the pinned ioquake3 checkout. The build tree is
# deleted first, so no earlier member can survive into an accepted pack.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
archive_dir="${repo_dir}/build/content-sources"
output_dir="${repo_dir}/build/content-pack"
require_clean=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive-dir)
      archive_dir="${2:?--archive-dir needs a path}"
      shift 2
      ;;
    --output-dir)
      output_dir="${2:?--output-dir needs a path}"
      shift 2
      ;;
    --allow-dirty-worktree)
      # Only for a rehearsal assembly whose manifest is not committed.
      require_clean=0
      shift
      ;;
    -h | --help)
      printf 'usage: %s [--archive-dir DIR] [--output-dir DIR] [--allow-dirty-worktree]\n' "$0"
      exit 0
      ;;
    *)
      printf 'usage: %s [--archive-dir DIR] [--output-dir DIR] [--allow-dirty-worktree]\n' "$0" >&2
      exit 2
      ;;
  esac
done

python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null

producer_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
if [[ "${require_clean}" -eq 1 ]]; then
  if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1 --untracked-files=no)" ]]; then
    printf 'the arena-web worktree must be clean: the manifest records commit %s\n' \
      "${producer_commit}" >&2
    exit 1
  fi
fi

rm -rf "${output_dir}"
mkdir -p "${output_dir}"

python3 "${repo_dir}/scripts/build-content-pack.py" \
  --archive-dir "${archive_dir}" \
  --output-dir "${output_dir}" \
  --provenance-output "${output_dir}/content-provenance.json" \
  --manifest-output "${output_dir}/content-manifest.json" \
  --producer-commit "${producer_commit}"
