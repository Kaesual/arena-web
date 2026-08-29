#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The one networked step of the browser build.
#
# ioquake3's Emscripten target links SDL2 through `-sUSE_SDL=2`, and the
# Emscripten SDK implements that flag as a port whose source it downloads on
# first use. An accepted arena-web build must not depend on a live network, so
# the port source is fetched once, verified, and afterwards mounted read-only
# into offline builds.
#
# What the build compiles is the unpacked tree, not the archive, and the SDK
# does not re-verify anything on reuse: its `up_to_date()` check returns true on
# the presence of a `.emscripten_url` marker alone and never reads or re-hashes
# the archive again. `--stage` therefore re-creates the tree from the
# digest-verified archive before every build, so the bytes the compiler sees are
# always derived from the bytes this script checked.
#
# The archive is a build input, not a committed artifact: it lands under the
# gitignored build directory.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="${CONTAINER_RUNTIME:-docker}"
ports_dir="${repo_dir}/build/emscripten-ports"

# The exact port identity.
#
# `port_version` and `port_sha512` are the Emscripten SDK's own values:
# `--fetch` reads `VERSION` and `HASH` out of the pinned image's
# `tools/ports/sdl2.py` and refuses to continue if either disagrees, and the
# SDK then verifies its download against that same SHA-512. The remaining
# values are arena-web's own records of what that produces — the archive's
# SHA-256, the file name and top-level directory the SDK's unpacking creates,
# and the URL the SDK composes from its VERSION — and they are enforced here
# rather than by the SDK.
port_name="sdl2"
port_version="2.32.10"
port_url="https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip"
port_subdir="SDL-release-2.32.10"
port_archive="sdl2.32.10.zip"
port_sha512="001738b610b42a8f8badfd6af3402f0a1a8601034adef0b8c702dd2b1951dc1b71b733a6779d97499b6f7314d226ec0c8dcffeb753f35a5c51e995ca20bdd459"
port_sha256="7a3c207b8509edc487d658df357ad764cd852d68fe248d307b25c0741d52fdf0"

usage() {
  cat <<'EOF'
usage: fetch-emscripten-ports.sh [--fetch | --check | --stage | --print-archive]

  --fetch          download and verify the pinned port sources (needs network)
  --check          verify the local port archive offline (default)
  --stage          re-create the port tree from the verified archive
  --print-archive  print the path of the verified port archive
EOF
}

mode="check"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) mode="check" ;;
    --fetch) mode="fetch" ;;
    --stage) mode="stage" ;;
    --print-archive) mode="print-archive" ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
  shift
done

archive_path="${ports_dir}/${port_archive}"
port_dir="${ports_dir}/${port_name}"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

check_archive() {
  [[ -f "${archive_path}" ]] ||
    fail "missing port archive ${archive_path}; run scripts/fetch-emscripten-ports.sh --fetch"
  printf '%s  %s\n' "${port_sha256}" "${archive_path}" | sha256sum --check --status ||
    fail "port archive ${archive_path} does not match the pinned SHA-256"
  printf '%s  %s\n' "${port_sha512}" "${archive_path}" | sha512sum --check --status ||
    fail "port archive ${archive_path} does not match the pinned SHA-512"
}

# Reproduce exactly what the SDK's own unpacking produces: the archive expanded
# with the same `shutil.unpack_archive` call, and the marker file whose content
# its `up_to_date()` compares against the port URL.
stage_port_tree() {
  ARENA_PORT_ARCHIVE="${archive_path}" \
    ARENA_PORT_DIR="${port_dir}" \
    ARENA_PORT_SUBDIR="${port_subdir}" \
    ARENA_PORT_URL="${port_url}" \
    python3 - <<'PY'
import os
import pathlib
import shutil

port_dir = pathlib.Path(os.environ["ARENA_PORT_DIR"])
subdir = os.environ["ARENA_PORT_SUBDIR"]
if port_dir.exists():
    shutil.rmtree(port_dir)
port_dir.mkdir(parents=True)
shutil.unpack_archive(
    filename=os.environ["ARENA_PORT_ARCHIVE"], extract_dir=str(port_dir)
)
if not (port_dir / subdir).is_dir():
    raise SystemExit(f"port archive does not contain {subdir}")
(port_dir / ".emscripten_url").write_text(
    os.environ["ARENA_PORT_URL"] + "\n", encoding="utf-8"
)
PY
}

case "${mode}" in
  print-archive)
    check_archive
    printf '%s\n' "${archive_path}"
    exit 0
    ;;
  check)
    check_archive
    printf 'verified pinned Emscripten port archive %s %s\n' "${port_name}" "${port_version}"
    exit 0
    ;;
  stage)
    check_archive
    stage_port_tree
    printf 'staged pinned Emscripten port %s %s from the verified archive\n' \
      "${port_name}" "${port_version}"
    exit 0
    ;;
esac

builder_image="$(python3 "${repo_dir}/scripts/baseline-inputs.py" builder-image)"
mkdir -p "${ports_dir}"

runtime_arguments=(
  --rm
  --cap-drop all
  --platform linux/amd64
  --pull never
  --security-opt label=disable
  --security-opt no-new-privileges
  --user "$(id -u):$(id -g)"
  --env "ARENA_PORT_NAME=${port_name}"
  --env "ARENA_PORT_SHA512=${port_sha512}"
  --env "ARENA_PORT_VERSION=${port_version}"
  --env "EM_PORTS=/ports"
  --env "HOME=/tmp"
  --volume "${ports_dir}:/ports:rw"
  --volume "${repo_dir}/scripts:/arena-scripts:ro"
  --workdir /tmp
  --entrypoint /bin/bash
)
if [[ "${runtime}" == *podman* ]]; then
  runtime_arguments+=(--userns=keep-id)
fi

"${runtime}" run "${runtime_arguments[@]}" \
  "${builder_image}" \
  /arena-scripts/fetch-port-in-container.sh

check_archive
stage_port_tree
printf 'fetched and verified pinned Emscripten port %s %s\n' "${port_name}" "${port_version}"
printf 'archive: %s\n' "${archive_path}"
