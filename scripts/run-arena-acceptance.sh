#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Automated pre-acceptance of the offline vertical slice in the pinned WP0
# acceptance browser, against a clean local serve of the staged runtime set.
#
# This is not the work package's witnessed acceptance. It proves that the slice
# loads, boots, enters the map with bots, keeps its declared artifact
# identities and produces no console defect; a person at the real acceptance
# desktop still has to play it. Logs, screenshots and the machine-readable
# summary land in the gitignored build/arena-acceptance directory.
#
# The browser is not downloaded here. Obtain the exact archive as
# docs/immutable-baseline.md describes, unpack it, and point ARENA_CHROME (or
# --chrome) at its 'chrome' binary; the run refuses any other version.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
chrome="${ARENA_CHROME:-}"
forwarded=()

usage() {
  cat <<'EOF'
usage: run-arena-acceptance.sh [--chrome PATH] [options passed on]

  --chrome PATH        the pinned Chrome for Testing binary
                       (default: $ARENA_CHROME, else build/chrome/chrome-linux64/chrome)

Every other option is passed to scripts/arena_acceptance.py; see --help there
for --engine-dir, --content-dir, --runs, --play-seconds, --headed and
--skip-stage.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chrome)
      chrome="${2:?--chrome needs a path}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      forwarded+=("$1")
      shift
      ;;
  esac
done

if [[ -z "${chrome}" ]]; then
  chrome="${repo_dir}/build/chrome/chrome-linux64/chrome"
fi

if [[ ! -x "${chrome}" ]]; then
  printf 'the pinned acceptance browser is not at %s\n' "${chrome}" >&2
  printf 'set ARENA_CHROME or pass --chrome; see docs/immutable-baseline.md\n' >&2
  exit 2
fi

exec python3 "${repo_dir}/scripts/arena_acceptance.py" --chrome "${chrome}" "${forwarded[@]+"${forwarded[@]}"}"
