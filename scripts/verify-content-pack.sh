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

# The property the split exists for, as a mechanical test rather than an
# argument: adding a map must leave every archive that already existed, and the
# base, byte for byte where they were. Reproducibility above does not imply it —
# it compares two builds of the *same* map set, and every way one map's presence
# could reach another archive's bytes lives in the difference between two
# different sets.
# The fixture map must be one the published set does not contain, or the check
# would refuse to run the moment its map was published. WP-F published
# am_galmevish, which used to be it, so the fixture is a short list of maps that
# are in the pinned sources, have a clean closure with no accepted unresolved
# reference at all, and are held out of the published set because they are CTF
# maps and team play is later scope, not because of any defect. The first one not
# yet published is used.
grown_map=
for candidate in oa_bases3 oa_ctf2 oa_ctf4ish; do
  if [[ ! -e "${repo_dir}/content/maps/${candidate}.json" ]]; then
    grown_map="${candidate}"
    break
  fi
done
if [[ -z "${grown_map}" ]]; then
  printf 'every growth-check fixture map has been published; pick a new one\n' >&2
  exit 1
fi
grown_fragment="${repo_dir}/content/maps/${grown_map}.json"
grown_longname=
case "${grown_map}" in
  oa_bases3) grown_longname="Some Bases" ;;
  oa_ctf2) grown_longname="OA_CTF2" ;;
  oa_ctf4ish) grown_longname="Free Space" ;;
esac
# The release index is a statement about the *published* archive set, and this
# check deliberately assembles an unpublished one — so the index's own fragment
# gate would refuse the assembly, correctly. It is set aside for the duration
# and restored by the same trap that removes the scratch fragment.
release_index="${repo_dir}/release/browser-release.json"
held_index="${verify_dir}/browser-release.held.json"
# Every published map needs a measured peak hunk, and the fixture map is by
# construction not published, so the scratch fragment needs a scratch figure
# beside it. It is restored with the fragment, and the value is deliberately
# the published maximum rather than an invented measurement: the build only
# checks that a figure exists and fits the pinned engine's hunk, and a number
# that looked measured would be the more misleading one to leave behind if a
# restore ever failed.
resources="${repo_dir}/records/map-resource-measurements.json"
held_resources="${verify_dir}/map-resource-measurements.held.json"
cleanup_growth() {
  rm -f "${grown_fragment}"
  if [[ -f "${held_index}" && ! -f "${release_index}" ]]; then
    mv "${held_index}" "${release_index}"
  fi
  if [[ -f "${held_resources}" ]]; then
    mv -f "${held_resources}" "${resources}"
  fi
}
trap cleanup_growth EXIT
if [[ -f "${release_index}" ]]; then
  mv "${release_index}" "${held_index}"
fi
cp "${resources}" "${held_resources}"
python3 - "${resources}" "${grown_map}" <<'PYTHON'
import json
import sys

path, name = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    record = json.load(handle)
peak = max(entry["peakHunkBytes"] for entry in record["maps"].values())
record["maps"][name] = {"peakHunkBytes": peak}
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False))
    handle.write("\n")
PYTHON

# A map from the already pinned sources, so the check measures set growth and
# not a new upstream input. Its arena values are the fixture's own: the map is
# never published, so nothing reads them but the build's own field gate.
cat > "${grown_fragment}" <<JSON
{
  "acceptedUnresolved": [],
  "arena": {
    "bots": "Skelebot Gargoyle Penguin",
    "fraglimit": "20",
    "longname": "${grown_longname}",
    "map": "${grown_map}",
    "type": "ffa"
  },
  "generatedMembers": [
    "NOTICE-arena-web.txt",
    "scripts/${grown_map}.arena"
  ],
  "map": "${grown_map}"
}
JSON

printf '\n=== clean assembly with %s added ===\n' "${grown_map}"
"${repo_dir}/scripts/build-content-pack.sh" \
  --output-dir "${verify_dir}/assembly-grown" \
  --allow-dirty-worktree

cleanup_growth
trap - EXIT

printf '\n=== comparing the archives that already existed ===\n'
moved=0
for archive in "${first}"/baseq3/*.pk3; do
  name="$(basename "${archive}")"
  grown="${verify_dir}/assembly-grown/baseq3/${name}"
  if [[ ! -f "${grown}" ]]; then
    printf '%s vanished when a map was added\n' "${name}" >&2
    moved=1
  elif ! cmp -s "${archive}" "${grown}"; then
    printf '%s moved when a map was added\n' "${name}" >&2
    moved=1
  else
    printf 'unmoved  %s\n' "${name}"
  fi
done
if [[ "${moved}" -ne 0 ]]; then
  printf 'adding a map moved an archive that already existed\n' >&2
  exit 1
fi
printf 'adding a map left every existing archive and the base byte-identical\n'

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
    (
        "content-manifest.json",
        "provenance/arena-web-ffa-content-manifest.json",
        "producer",
    ),
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
