#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Maintenance command: regenerate locks/native-toolchain-packages.conf.
#
# This is the only step of the native toolchain that needs the network and a
# dependency resolver. It runs `apt-get` inside the WP0 native builder base
# against one immutable Ubuntu snapshot, resolves the requested package set,
# and prints every resolved package with the exact version, pool path, size and
# SHA-256 the snapshot's GPG-signed index states for it.
#
# The result is a lock: `scripts/fetch-native-packages.sh` downloads exactly
# those pool paths and refuses any byte that does not match the recorded digest,
# and `scripts/build-native-toolchain.sh` installs only from that verified
# directory with the network off. No accepted build resolves anything.
#
# Rerunning this is a deliberate toolchain change, reviewed like any other pin.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="${CONTAINER_RUNTIME:-docker}"

# The snapshot timestamp and the requested set are the two decisions this
# command makes; everything else is resolved from them.
snapshot_timestamp="20260824T000000Z"
suites=(noble noble-security noble-updates)
components=(main universe)
requests=(cmake gcc libgl1-mesa-dri libglx-mesa0 libsdl2-dev make tcpdump xvfb)

usage() {
  cat <<'EOF'
usage: resolve-native-packages.sh [--output FILE]

  --output FILE   write the regenerated lock here
                  (default: locks/native-toolchain-packages.conf)

Needs network access and the host's CA bundle. The WP0 builder base ships no
ca-certificates package, so the bundle is mounted into the container for the
one resolution request; nothing else in this repository fetches over TLS from
inside that image.
EOF
}

output="${repo_dir}/locks/native-toolchain-packages.conf"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      output="$(readlink -m "${2:?--output needs a path}")"
      shift 2
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

builder_image="$(python3 "${repo_dir}/scripts/baseline-inputs.py" native-builder-image)"
snapshot_url="https://snapshot.ubuntu.com/ubuntu/${snapshot_timestamp}"

host_ca=""
for candidate in \
  /etc/ssl/certs/ca-bundle.crt \
  /etc/pki/tls/certs/ca-bundle.crt \
  /etc/ssl/certs/ca-certificates.crt; do
  if [[ -r "${candidate}" ]]; then
    host_ca="${candidate}"
    break
  fi
done
if [[ -z "${host_ca}" ]]; then
  printf 'no host CA bundle found; cannot resolve over TLS\n' >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
cp "${host_ca}" "${work}/ca.crt"
chmod 0755 "${work}"
chmod 0644 "${work}/ca.crt"

printf 'resolving %s\n' "${requests[*]}" >&2
printf 'against  %s\n' "${snapshot_url}" >&2

"${runtime}" run --rm \
  --platform linux/amd64 \
  --pull never \
  --security-opt label=disable \
  --security-opt no-new-privileges \
  --env "ARENA_SNAPSHOT_URL=${snapshot_url}" \
  --env "ARENA_SUITES=${suites[*]}" \
  --env "ARENA_COMPONENTS=${components[*]}" \
  --env "ARENA_REQUESTS=${requests[*]}" \
  --volume "${work}:/hostw:rw" \
  --workdir /tmp \
  --entrypoint /bin/bash \
  "${builder_image}" \
  -c '
set -euo pipefail
rm -f /etc/apt/sources.list.d/ubuntu.sources
{
  printf "Types: deb\n"
  printf "URIs: %s\n" "${ARENA_SNAPSHOT_URL}"
  printf "Suites: %s\n" "${ARENA_SUITES}"
  printf "Components: %s\n" "${ARENA_COMPONENTS}"
  printf "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
} > /etc/apt/sources.list.d/arena-snapshot.sources
printf "Acquire::https::CAInfo \"/hostw/ca.crt\";\n" > /etc/apt/apt.conf.d/99arena-ca
apt-get update -qq
# --print-uris resolves the closure without installing anything. The index it
# resolves against is the one apt has just verified against the Ubuntu archive
# key, so the versions below are the signed ones.
apt-get install -y --no-install-recommends --print-uris ${ARENA_REQUESTS} \
  | sed -n "s/^.\(https:[^ ]*\). \([^ ]*\) .*/\2/p" > /tmp/files.txt
: > /hostw/rows.txt
while read -r file_name; do
  name="${file_name%%_*}"
  rest="${file_name#*_}"
  version="${rest%%_*}"
  # apt percent-encodes the epoch separator in a .deb file name.
  version="${version//%3a/:}"
  apt-cache show "${name}=${version}" \
    | awk -v n="${name}" -v v="${version}" "
        /^Filename: / { f = \$2 }
        /^SHA256: /   { s = \$2 }
        /^Size: /     { z = \$2 }
        END { if (f == \"\" || s == \"\" || z == \"\") exit 1; print \"package\", n, v, s, z, f }
      " >> /hostw/rows.txt
done < /tmp/files.txt
'

if [[ ! -s "${work}/rows.txt" ]]; then
  printf 'resolution produced no packages\n' >&2
  exit 1
fi

{
  cat <<EOF
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The exact package set the WP5 native toolchain image installs on top of the
# WP0 native builder base. Regenerate with scripts/resolve-native-packages.sh;
# every other command treats this file as immutable input.
#
# The Ubuntu builder pin is not permission to resolve packages during an
# accepted build. The snapshot below is an immutable Canonical archive at one
# timestamp, every package is pinned by version and SHA-256, and the accepted
# build installs from a verified local directory with the network off.
#
# This toolchain is build-and-test only. It compiles the dedicated server and
# the native test client, runs that client for the packet census and carries
# the capture tool; none of its bytes enter the distributed server image, which
# is built from the separately pinned Debian runtime base.
#
# Rows:
#   snapshot  <immutable archive base URL>
#   suite     <suite>            (sorted; the resolver used exactly these)
#   component <component>        (sorted)
#   request   <package>          (sorted; the set that was asked for)
#   package   <name> <version> <sha256> <size> <pool path>
EOF
  printf 'snapshot %s\n' "${snapshot_url}"
  for suite in "${suites[@]}"; do printf 'suite %s\n' "${suite}"; done
  for component in "${components[@]}"; do printf 'component %s\n' "${component}"; done
  for request in "${requests[@]}"; do printf 'request %s\n' "${request}"; done
  LC_ALL=C sort -k2,2 "${work}/rows.txt"
} > "${output}"

printf 'wrote %s (%s packages)\n' \
  "${output}" "$(grep -c '^package ' "${output}")" >&2
