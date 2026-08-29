#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The inside half of one accepted content assembly. Runs in the WP0 builder
# image with no network, a read-only checkout at /src, the digest-pinned
# upstream archives read-only at /archives, and /work as the only writable
# mount.
#
# Pinning the interpreter matters here: the PK3's bytes depend on the CPython
# zipfile writer and the zlib the interpreter links, so "byte-identical" is
# only a property of a fixed toolchain. This image is that fixed toolchain.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export LC_ALL=C
export LANG=C
export TZ=UTC

: "${ARENA_PRODUCER_COMMIT:?ARENA_PRODUCER_COMMIT must be set by the caller}"

python3 - <<'PYTHON' > /work/toolchain.txt
import platform
import sys
import zlib

print(f"python: {platform.python_version()} ({sys.implementation.name})")
print(f"zlib-module-version: {zlib.ZLIB_VERSION}")
print(f"zlib-runtime-version: {zlib.ZLIB_RUNTIME_VERSION}")
PYTHON
cat /work/toolchain.txt

exec python3 /src/scripts/build-content-pack.py \
  --archive-dir /archives \
  --output-dir /work \
  --provenance-output /work/content-provenance.json \
  --manifest-output /work/content-manifest.json \
  --producer-commit "${ARENA_PRODUCER_COMMIT}"
