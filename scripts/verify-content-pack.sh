#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# WP3 acceptance: two clean assemblies must produce byte-identical PK3 files,
# provenance records and manifests, and must still agree with the committed
# identities.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
verify_dir="${repo_dir}/build/content-verify"
extra_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-dirty-worktree)
      extra_args+=(--allow-dirty-worktree)
      shift
      ;;
    -h | --help)
      printf 'usage: %s [--allow-dirty-worktree]\n' "$0"
      exit 0
      ;;
    *)
      printf 'usage: %s [--allow-dirty-worktree]\n' "$0" >&2
      exit 2
      ;;
  esac
done

rm -rf "${verify_dir}"
mkdir -p "${verify_dir}"

for attempt in 1 2; do
  printf '\n=== clean assembly %s of 2 ===\n' "${attempt}"
  "${repo_dir}/scripts/build-content-pack.sh" \
    --output-dir "${verify_dir}/assembly-${attempt}" \
    "${extra_args[@]}"
done

first="${verify_dir}/assembly-1"
second="${verify_dir}/assembly-2"

printf '\n=== comparing the two assemblies ===\n'
if ! diff --recursive --brief "${first}" "${second}"; then
  printf 'the two clean assemblies differ\n' >&2
  exit 1
fi
printf 'two clean assemblies are byte-identical\n'

# The committed records are the evidence of an accepted assembly. A rebuild from
# a later commit records its own producing commit, so everything except
# `producer` must still agree exactly.
python3 - "${repo_dir}" "${first}" <<'PYTHON'
import json
import sys
from pathlib import Path

repo_dir, assembly = Path(sys.argv[1]), Path(sys.argv[2])
pairs = (
    ("content-provenance.json", "provenance/arena-web-ffa-content.json", None),
    ("content-manifest.json", "provenance/arena-web-ffa-content-manifest.json", "producer"),
)
status = 0
for generated_name, committed_name, ignored in pairs:
    generated = json.loads((assembly / generated_name).read_text(encoding="utf-8"))
    committed_path = repo_dir / committed_name
    if not committed_path.is_file():
        print(f"missing committed record {committed_name}", file=sys.stderr)
        status = 1
        continue
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    if ignored:
        if generated[ignored] != committed[ignored]:
            print(
                f"{committed_name}: {ignored} differs "
                f"({committed[ignored]} committed, {generated[ignored]} rebuilt)"
            )
        generated.pop(ignored)
        committed.pop(ignored)
    if generated != committed:
        print(f"{committed_name} disagrees with the rebuilt record", file=sys.stderr)
        status = 1
    else:
        print(f"{committed_name} agrees with the rebuilt record")
sys.exit(status)
PYTHON
