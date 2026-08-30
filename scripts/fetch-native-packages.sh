#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Obtain the exact .deb files pinned by locks/native-toolchain-packages.conf.
#
# This is the only networked step of the native toolchain. Every file comes
# from the immutable snapshot pool path the lock records and is accepted only
# if its byte length and SHA-256 match. Downloads land in the gitignored build
# directory and are never committed; the toolchain image is then assembled from
# this verified directory with the network off.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
package_dir="${repo_dir}/build/native-packages"
mode="fetch"

usage() {
  cat <<'EOF'
usage: fetch-native-packages.sh [--check] [--package-dir DIR]

  --check         verify what is already present, download nothing
  --package-dir   destination (default: build/native-packages)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      mode="check"
      shift
      ;;
    --package-dir)
      package_dir="$(readlink -m "${2:?--package-dir needs a path}")"
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

mkdir -p "${package_dir}"

# Materialise the list first: a process substitution's exit status is not
# observable in the loop, so a malformed lock would otherwise let --check
# report success having verified nothing.
packages="$(python3 - "${repo_dir}" <<'PYTHON'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))

from native_toolchain import (
    load_index_lock,
    load_package_lock,
    package_file_name,
    package_url,
)

lock = load_package_lock(Path(sys.argv[1]))
# The sidecar is not fetched here, but a malformed or disagreeing one is a
# broken trust root, so it fails the step that reads the lock rather than
# sitting unread beside it.
load_index_lock(Path(sys.argv[1]), lock)
for package in lock["packages"]:
    print(
        "\t".join(
            (
                package_file_name(package),
                package_url(lock, package),
                str(package["size"]),
                package["sha256"],
            )
        )
    )
PYTHON
)"

expected_count="$(python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from native_toolchain import load_package_lock
print(len(load_package_lock(Path(sys.argv[1]))["packages"]))
' "${repo_dir}")"
actual_count="$(printf '%s\n' "${packages}" | grep -c .)"
if [[ -z "${packages}" || "${actual_count}" -ne "${expected_count}" ]]; then
  printf 'the lock pins %s packages but %s were resolved\n' \
    "${expected_count}" "${actual_count}" >&2
  exit 1
fi

status=0
fetched=0
while IFS=$'\t' read -r name url size sha256; do
  target="${package_dir}/${name}"
  if [[ ! -f "${target}" ]]; then
    if [[ "${mode}" == "check" ]]; then
      printf 'missing  %s\n' "${name}" >&2
      status=1
      continue
    fi
    curl --fail --location --silent --show-error \
      --output "${target}.part" "${url}"
    mv "${target}.part" "${target}"
    fetched=$((fetched + 1))
  fi
  actual_size="$(stat -c %s "${target}")"
  actual_sha="$(sha256sum "${target}" | cut -d' ' -f1)"
  if [[ "${actual_size}" != "${size}" || "${actual_sha}" != "${sha256}" ]]; then
    printf 'MISMATCH %s: got %s bytes sha256:%s, lock pins %s bytes sha256:%s\n' \
      "${name}" "${actual_size}" "${actual_sha}" "${size}" "${sha256}" >&2
    status=1
    continue
  fi
done <<< "${packages}"

if [[ "${status}" -ne 0 ]]; then
  printf 'the pinned native toolchain packages are not usable\n' >&2
  exit "${status}"
fi

# The directory is the install set, so an unpinned file lying beside the pinned
# ones would be installed without review. Reject it here, not at install time.
python3 - "${repo_dir}" "${package_dir}" <<'PYTHON'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))

from native_toolchain import (
    NativeToolchainError,
    load_package_lock,
    verify_package_directory,
)

try:
    lock = load_package_lock(Path(sys.argv[1]))
    names = verify_package_directory(lock, Path(sys.argv[2]))
except NativeToolchainError as error:
    print(f"native toolchain packages refused: {error}", file=sys.stderr)
    raise SystemExit(1)
print(f"verified {len(names)} pinned packages in {sys.argv[2]}")
PYTHON

printf 'downloaded %s of %s packages\n' "${fetched}" "${expected_count}"
