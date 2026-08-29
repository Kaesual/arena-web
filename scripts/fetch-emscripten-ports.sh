#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The one networked step of the browser build.
#
# ioquake3's Emscripten target links SDL2 through `-sUSE_SDL=2`, and the
# Emscripten SDK implements that flag as a port whose source it downloads on
# first use. An accepted arena-web build must not depend on a live network, so
# the port source is fetched once, verified against the exact identity the
# pinned SDK itself pins, and afterwards mounted read-only into offline builds.
#
# The archive is a build input, not a committed artifact: it lands under the
# gitignored build directory.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-docker}"
ports_dir="${repo_dir}/build/emscripten-ports"

# The exact port identity. `--fetch` re-derives all four values from the pinned
# builder image and refuses to continue if the SDK disagrees with them, so this
# block cannot silently drift away from the toolchain that consumes it.
port_name="sdl2"
port_version="2.32.10"
port_url="https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip"
port_subdir="SDL-release-2.32.10"
port_archive="sdl2.32.10.zip"
port_sha512="001738b610b42a8f8badfd6af3402f0a1a8601034adef0b8c702dd2b1951dc1b71b733a6779d97499b6f7314d226ec0c8dcffeb753f35a5c51e995ca20bdd459"
port_sha256="7a3c207b8509edc487d658df357ad764cd852d68fe248d307b25c0741d52fdf0"

usage() {
  cat <<'EOF'
usage: fetch-emscripten-ports.sh [--fetch | --check | --print-archive]

  --fetch          download and verify the pinned port sources (needs network)
  --check          verify the local port sources offline (default)
  --print-archive  print the path of the verified port archive
EOF
}

mode="check"
case "${1:-}" in
  "" | --check) ;;
  --fetch) mode="fetch" ;;
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

archive_path="${ports_dir}/${port_archive}"
unpacked_dir="${ports_dir}/${port_name}/${port_subdir}"
marker_path="${ports_dir}/${port_name}/.emscripten_url"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

check_local_ports() {
  [[ -f "${archive_path}" ]] ||
    fail "missing port archive ${archive_path}; run scripts/fetch-emscripten-ports.sh --fetch"
  [[ -d "${unpacked_dir}" ]] ||
    fail "missing unpacked port ${unpacked_dir}; run scripts/fetch-emscripten-ports.sh --fetch"
  [[ -f "${marker_path}" ]] ||
    fail "missing port marker ${marker_path}; run scripts/fetch-emscripten-ports.sh --fetch"
  [[ "$(cat "${marker_path}")" == "${port_url}" ]] ||
    fail "port marker ${marker_path} does not name ${port_url}"
  printf '%s  %s\n' "${port_sha256}" "${archive_path}" | sha256sum --check --status ||
    fail "port archive ${archive_path} does not match the pinned SHA-256"
  printf '%s  %s\n' "${port_sha512}" "${archive_path}" | sha512sum --check --status ||
    fail "port archive ${archive_path} does not match the pinned SHA-512"
}

if [[ "${mode}" == "print-archive" ]]; then
  check_local_ports
  printf '%s\n' "${archive_path}"
  exit 0
fi

if [[ "${mode}" == "check" ]]; then
  check_local_ports
  printf 'verified pinned Emscripten port %s %s\n' "${port_name}" "${port_version}"
  exit 0
fi

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
  --env "ARENA_PORT_URL=${port_url}"
  --env "ARENA_PORT_VERSION=${port_version}"
  --env "EM_PORTS=/ports"
  --env "HOME=/tmp"
  --volume "${ports_dir}:/ports:rw"
  --workdir /tmp
  --entrypoint /bin/bash
)
if [[ "${runtime}" == *podman* ]]; then
  runtime_arguments+=(--userns=keep-id)
fi

# The SDK is asked to state its own pinned port identity before it is asked to
# download anything, and it verifies the download against that same hash.
# The ARENA_* references below are expanded by the container shell from the
# environment above, not by this one.
# shellcheck disable=SC2016
"${runtime}" run "${runtime_arguments[@]}" "${builder_image}" -c '
set -euo pipefail
python3 - <<PY
import sys
sys.path.insert(0, "/emsdk/upstream/emscripten")
from tools.ports import sdl2
expected = {
    "VERSION": "${ARENA_PORT_VERSION}",
    "HASH": "${ARENA_PORT_SHA512}",
}
for name, value in expected.items():
    actual = getattr(sdl2, name)
    if actual != value:
        raise SystemExit(
            f"pinned builder disagrees with arena-web port pin: {name} is {actual!r}"
        )
url = f"https://github.com/libsdl-org/SDL/archive/release-{sdl2.VERSION}.zip"
if url != "${ARENA_PORT_URL}":
    raise SystemExit(f"pinned builder resolves a different port URL: {url}")
PY
embuilder build "${ARENA_PORT_NAME}"
'

check_local_ports
printf 'fetched and verified pinned Emscripten port %s %s\n' "${port_name}" "${port_version}"
printf 'archive: %s\n' "${archive_path}"
