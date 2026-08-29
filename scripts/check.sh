#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

metadata_arguments=()
case "${1:-}" in
  "")
    ;;
  --without-git-metadata)
    metadata_arguments+=(--without-git-metadata)
    export ARENA_WITHOUT_GIT_METADATA=1
    ;;
  *)
    printf 'usage: %s [--without-git-metadata]\n' "$0" >&2
    exit 2
    ;;
esac

python3 "${repo_dir}/scripts/validate-metadata.py" "${metadata_arguments[@]}"
python3 -m unittest discover -s "${repo_dir}/tests" -p 'test_*.py'
if [[ ${#metadata_arguments[@]} -eq 0 ]]; then
  git -C "${repo_dir}" diff --check
  git -C "${repo_dir}" diff --cached --check
fi
