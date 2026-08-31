#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Take the WP7 packet re-census: one driven session between the containerized
# dedicated server and the native test client, captured at the engine/UDP
# boundary on a private container network.
#
# Everything it needs must already exist: the pinned toolchain image, the native
# client build and the server image. The census itself contacts no network
# outside the one it creates. The capture sees every UDP destination in the
# client's isolated network namespace so second-destination traffic cannot be
# hidden by a server-port filter.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# The WP5 native steps use Podman-only constructs; arena_require_container_runtime
# is called before the first container use, so a metadata query still works
# without one.
# shellcheck source=scripts/container-runtime.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/container-runtime.sh"
runtime="$(arena_container_runtime)"

server_image="$("${repo_dir}/scripts/build-server-image.sh" --print-tag)"
toolchain_image="$("${repo_dir}/scripts/build-native-toolchain.sh" --print-tag)"

arena_require_container_runtime "${runtime}"

for image in "${server_image}" "${toolchain_image}"; do
  if ! "${runtime}" image exists "${image}"; then
    printf 'the image %s does not exist; build it first\n' "${image}" >&2
    exit 1
  fi
done

python3 "${repo_dir}/scripts/validate-metadata.py" >/dev/null

exec python3 "${repo_dir}/scripts/census_run.py" \
  --runtime "${runtime}" \
  --server-image "${server_image}" \
  --toolchain-image "${toolchain_image}" \
  "$@"
