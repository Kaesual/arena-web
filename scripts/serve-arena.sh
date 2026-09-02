#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Stage, verify and serve the offline vertical slice on loopback.
#
# The served directory is not the repository: it is a tree assembled by
# scripts/stage-arena.py that contains the product loader, the committed
# content configuration, the two committed manifests and exactly the artifacts
# those manifests declare, each verified by digest before it is copied. Nothing
# else is reachable over this server.
#
# http://127.0.0.1 is a secure context, so crypto.subtle is available and the
# loader can verify every artifact it fetches.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
host="127.0.0.1"
port="8174"
engine_dir="${repo_dir}/build/browser/tree/Release"
content_dir="${repo_dir}/build/content-pack"
target="${repo_dir}/build/arena-serve"
stage_only=0

usage() {
  cat <<'EOF'
usage: serve-arena.sh [options]

  --port PORT         loopback port (default: 8174)
  --engine-dir DIR    accepted browser build output
                      (default: build/browser/tree/Release)
  --content-dir DIR   accepted content assembly (default: build/content-pack)
  --target DIR        staged serve root, deleted first
                      (default: build/arena-serve)
  --stage-only        stage and verify, then exit without serving
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      port="${2:?--port needs a number}"
      if ! [[ "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
        printf 'invalid port: %s\n' "${port}" >&2
        exit 2
      fi
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
    --target)
      target="$(readlink -m "${2:?--target needs a path}")"
      shift 2
      ;;
    --stage-only)
      stage_only=1
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

python3 "${repo_dir}/scripts/stage-arena.py" \
  --engine-dir "${engine_dir}" \
  --content-dir "${content_dir}" \
  --target "${target}"

if [[ ${stage_only} -eq 1 ]]; then
  exit 0
fi

# The loader requires the rotation it is being opened for; there is no default,
# because the two plausible ones are "download everything" and "download less
# than the server will play". The profile commits no map any more — it is a
# launch argument — so ARENA_ROTATION picks one and the first published map is
# what a bare `serve-arena.sh` offers, derived from the release rather than
# typed here so this URL cannot go stale.
rotation="${ARENA_ROTATION:-$(python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from arena_runtime import published_maps
print(published_maps(Path(sys.argv[2]), sys.argv[3])[0])
' "${repo_dir}/scripts" "${repo_dir}" provenance/arena-web-ffa-content-manifest.json)}"

printf 'serving %s\n' "${target}"
printf 'arena:   http://%s:%s/?maps=%s\n' "${host}" "${port}" "${rotation}"
exec python3 -m http.server --bind "${host}" --directory "${target}" "${port}"
