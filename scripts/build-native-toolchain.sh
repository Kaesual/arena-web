#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Assemble the WP5 native toolchain image: the WP0 native builder base plus
# exactly the packages locks/native-toolchain-packages.conf pins.
#
# The image is build-and-test only. The distributed server image does not
# inherit it; it starts from the separately pinned Debian runtime base.
#
# Nothing here resolves a dependency or contacts an archive: the packages are
# fetched and digest-verified by scripts/fetch-native-packages.sh first, and
# this build runs with the network off.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# The WP5 native steps use Podman-only constructs; arena_require_container_runtime
# is called before the first container use, so a metadata query still works
# without one.
# shellcheck source=scripts/container-runtime.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/container-runtime.sh"
runtime="$(arena_container_runtime)"
package_dir="${repo_dir}/build/native-packages"
print_tag_only=0

usage() {
  cat <<'EOF'
usage: build-native-toolchain.sh [options]

  --package-dir DIR   verified packages (default: build/native-packages)
  --print-tag         print the toolchain image tag and exit
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --package-dir)
      package_dir="$(readlink -m "${2:?--package-dir needs a path}")"
      shift 2
      ;;
    --print-tag)
      print_tag_only=1
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

# The lock's own bytes identify the toolchain: adding, removing or moving a
# package moves the tag, so a stale image can never be mistaken for this one.
lock_identity="$(python3 - "${repo_dir}" <<'PYTHON'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))

from native_toolchain import load_package_lock

print(load_package_lock(Path(sys.argv[1]))["identity"][:16])
PYTHON
)"
image_tag="arena-web-native-toolchain:${lock_identity}"

if [[ ${print_tag_only} -eq 1 ]]; then
  printf '%s\n' "${image_tag}"
  exit 0
fi

arena_require_container_runtime "${runtime}"

base_image="$(python3 "${repo_dir}/scripts/baseline-inputs.py" native-builder-image)"

python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null
"${repo_dir}/scripts/fetch-native-packages.sh" --check --package-dir "${package_dir}"

printf 'assembling %s\n' "${image_tag}"
printf 'from       %s\n' "${base_image}"

# --timestamp fixes every layer's mtime, so two assemblies of the same lock and
# base produce the same image rather than two images that differ only in when
# they were made.
"${runtime}" build \
  --file "${repo_dir}/native/toolchain.Containerfile" \
  --build-arg "ARENA_NATIVE_BUILDER_BASE=${base_image}" \
  --network none \
  --platform linux/amd64 \
  --pull=never \
  --timestamp 0 \
  --tag "${image_tag}" \
  "${package_dir}"

printf 'built %s (%s)\n' \
  "${image_tag}" \
  "$("${runtime}" image inspect --format '{{.Id}}' "${image_tag}")"
