#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Rebuild every published artifact of this release from this checkout and prove,
# byte for byte, that it is the artifact the committed records describe.
#
# WHY THIS EXISTS
#
# A reissue is written in two commits, and it cannot be written in one. The
# records name the commit whose sources produced them, and no commit can contain
# a record naming itself. So the *source* commit carries new inputs beside the
# previous release's generated records — which is exactly the state every build
# script's metadata gate refuses, correctly. A clean checkout of the source
# commit therefore cannot run these builds at all.
#
# The way out is not to weaken that gate. It is to build from the *authority*
# checkout — the one that publishes the records, and that does validate — while
# stamping the producer commit the records themselves name. That commit is read
# out of the committed records here, never taken from whoever runs this, so
# there is no version of this script in which an operator asserts what was built
# from where.
#
# Everything else is a byte comparison against the committed records, and every
# comparison fails closed.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="${CONTAINER_RUNTIME:-docker}"
work_dir="${repo_dir}/build/reproduce"
skip_browser=0

usage() {
  cat <<'EOF'
usage: reproduce-release.sh [options]

  --work-dir DIR   build root for this run (default: build/reproduce)
  --skip-browser   reuse build/browser instead of rebuilding the engine; the
                   artifacts are still compared against the committed manifest,
                   so this weakens the evidence, not the check
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work-dir)
      work_dir="$(readlink -m "${2:?--work-dir needs a path}")"
      case "${work_dir}" in
        "${repo_dir}/build/"*) ;;
        *)
          printf -- '--work-dir must be inside %s/build\n' "${repo_dir}" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --skip-browser)
      skip_browser=1
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

fail() {
  printf 'reproduction failed: %s\n' "$1" >&2
  exit 1
}

# 1. The authority checkout has to be exactly that: clean, and valid on its own
#    terms. If this fails, the records being reproduced are not the ones this
#    tree publishes.
if [[ -n "$(git -C "${repo_dir}" status --porcelain=v1)" ]]; then
  fail "the checkout is not clean; a reproduction proves nothing about a modified tree"
fi
authority_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null ||
  fail "this checkout does not validate; it is not an authority checkout"

# 2. The producer commits come out of the records. Three of them, one per
#    artifact set, and they are allowed to differ — a release may reissue one
#    half without the other.
read_producer() {
  python3 - "${repo_dir}/$1" <<'PY'
import json
import sys

record = json.loads(open(sys.argv[1], encoding="utf-8").read())
commit = record["producer"]["commit"]
if not isinstance(commit, str) or len(commit) != 40:
    raise SystemExit(f"{sys.argv[1]}: producer.commit is not a commit id")
print(commit)
PY
}
browser_producer="$(read_producer manifests/browser-client.json)"
content_producer="$(read_producer provenance/arena-web-ffa-content-manifest.json)"
server_producer="$(read_producer provenance/arena-web-server.json)"

printf 'authority checkout  %s\n' "${authority_commit}"
printf 'browser producer    %s\n' "${browser_producer}"
printf 'content producer    %s\n' "${content_producer}"
printf 'server  producer    %s\n' "${server_producer}"
printf '\n'

rm -rf "${work_dir}"
mkdir -p "${work_dir}"

# 3. The browser. Its manifest is compared whole, minus `producer`, which the
#    build stamps from the value read above and which therefore must match too —
#    so it is compared rather than ignored.
browser_dir="${repo_dir}/build/browser"
if [[ "${skip_browser}" -eq 0 ]]; then
  printf '=== browser ===\n'
  browser_dir="${work_dir}/browser"
  "${repo_dir}/scripts/build-browser.sh" \
    --output-dir "${browser_dir}" \
    --producer-commit "${browser_producer}"
else
  printf '=== browser (reusing %s) ===\n' "${browser_dir}"
  [[ -d "${browser_dir}/tree/Release" ]] ||
    fail "--skip-browser needs an existing ${browser_dir}/tree/Release"
  python3 "${repo_dir}/scripts/emit-artifact-manifest.py" \
    --artifact-root "${browser_dir}/tree/Release" \
    --output "${browser_dir}/artifact-manifest.json" \
    --producer-commit "${browser_producer}" \
    --port-archive "$("${repo_dir}/scripts/fetch-emscripten-ports.sh" --print-archive)" \
    >/dev/null
fi

# 4. The content archives, in the pinned builder.
printf '\n=== content ===\n'
"${repo_dir}/scripts/build-content-pack.sh" \
  --output-dir "${work_dir}/content" \
  --producer-commit "${content_producer}"

# 5. The server image, from the two above.
printf '\n=== server image ===\n'
CONTAINER_RUNTIME="${runtime}" "${repo_dir}/scripts/build-server-image.sh" \
  --engine-dir "${browser_dir}/tree/Release" \
  --content-dir "${work_dir}/content" \
  --output-dir "${work_dir}/server-image" \
  --tag "arena-web-server:reproduce" \
  --producer-commit "${server_producer}"

# 6. Compare. Whole records, not selected fields: a reproduction that only
#    checked the digests it expected to change would not be one.
printf '\n=== comparing against the committed records ===\n'
python3 - "${repo_dir}" "${work_dir}" "${browser_dir}" "${runtime}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

repo, work, browser, runtime = (Path(sys.argv[1]), Path(sys.argv[2]),
                                Path(sys.argv[3]), sys.argv[4])
status = 0


def compare(label, rebuilt, committed):
    global status
    if rebuilt == committed:
        print(f"  identical  {label}")
        return
    status = 1
    print(f"  DIFFERS    {label}", file=sys.stderr)
    for key in sorted(set(rebuilt) | set(committed)):
        if rebuilt.get(key) != committed.get(key):
            print(f"             {key}: committed {committed.get(key)!r}",
                  file=sys.stderr)
            print(f"             {key}: rebuilt   {rebuilt.get(key)!r}",
                  file=sys.stderr)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


compare("manifests/browser-client.json",
        load(browser / "artifact-manifest.json"),
        load(repo / "manifests/browser-client.json"))
compare("provenance/arena-web-ffa-content-manifest.json",
        load(work / "content/content-manifest.json"),
        load(repo / "provenance/arena-web-ffa-content-manifest.json"))
compare("provenance/arena-web-ffa-content.json",
        load(work / "content/content-provenance.json"),
        load(repo / "provenance/arena-web-ffa-content.json"))
compare("provenance/arena-web-server.json",
        load(work / "server-image/artifact-manifest.json"),
        load(repo / "provenance/arena-web-server.json"))

# The image ID is the one identity that is not a file in this tree, so it is
# read back off the built image and compared with the accepted record.
def image_digest(value):
    """The bare hex of an image ID.

    The record stores `sha256:<hex>`; `image inspect --format {{.Id}}` returns
    the hex alone on podman and has been seen prefixed elsewhere, so both sides
    are normalised rather than one side being trusted to have a fixed shape.
    """
    value = value.strip()
    digest = value.split(":", 1)[1] if ":" in value else value
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise SystemExit(f"not an image digest: {value!r}")
    return digest


accepted = load(repo / "records/wp11-server-resources.json")["release"]["serverImageId"]
built = subprocess.run(
    [runtime, "image", "inspect", "--format", "{{.Id}}", "arena-web-server:reproduce"],
    capture_output=True, text=True, check=True,
).stdout
accepted, built = image_digest(accepted), image_digest(built)
if built == accepted:
    print(f"  identical  server image ID sha256:{accepted}")
else:
    status = 1
    print(f"  DIFFERS    server image ID\n"
          f"             accepted sha256:{accepted}\n"
          f"             rebuilt  sha256:{built}", file=sys.stderr)

if status:
    print("\nthe rebuild does not reproduce the committed release", file=sys.stderr)
sys.exit(status)
PY

printf '\nthis checkout reproduces every committed record and the accepted image ID\n'
