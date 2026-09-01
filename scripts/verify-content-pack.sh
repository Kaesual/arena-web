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
grown_map=am_galmevish
grown_fragment="${repo_dir}/content/maps/${grown_map}.json"
if [[ -e "${grown_fragment}" ]]; then
  printf 'refusing to run the growth check: %s already exists\n' \
    "${grown_fragment}" >&2
  exit 1
fi
# The release index is a statement about the *published* archive set, and this
# check deliberately assembles an unpublished one — so the index's own fragment
# gate would refuse the assembly, correctly. It is set aside for the duration
# and restored by the same trap that removes the scratch fragment.
release_index="${repo_dir}/release/browser-release.json"
held_index="${verify_dir}/browser-release.held.json"
cleanup_growth() {
  rm -f "${grown_fragment}"
  if [[ -f "${held_index}" && ! -f "${release_index}" ]]; then
    mv "${held_index}" "${release_index}"
  fi
}
trap cleanup_growth EXIT
if [[ -f "${release_index}" ]]; then
  mv "${release_index}" "${held_index}"
fi

# A map from the already pinned sources, so the check measures set growth and
# not a new upstream input. Its six sky acceptances are §13.4's class 1.
cat > "${grown_fragment}" <<'JSON'
{
  "acceptedUnresolved": [
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_bk"
    },
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_dn"
    },
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_ft"
    },
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_lf"
    },
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_rt"
    },
    {
      "reason": "OpenArena sky shaders write 'skyParms full <height> -' and ParseSkyParms expands that outerbox name; a missing outerbox image becomes tr.defaultImage and tr_sky.c then skips the box entirely, so the cloud layers still draw",
      "reference": "full_up"
    }
  ],
  "arena": {
    "bots": "Skelebot Rai Sly",
    "fraglimit": "20",
    "longname": "GalMevish",
    "map": "am_galmevish",
    "type": "ffa"
  },
  "generatedMembers": [
    "NOTICE-arena-web.txt",
    "scripts/am_galmevish.arena"
  ],
  "map": "am_galmevish"
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
