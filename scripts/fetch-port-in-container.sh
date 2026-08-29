#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Runs inside the pinned Emscripten builder image, with network access, for
# `scripts/fetch-emscripten-ports.sh --fetch`. It is never meant to be run on a
# host: the caller supplies the port identity and the writable `/ports` mount.

set -euo pipefail

: "${ARENA_PORT_NAME:?port name was not supplied}"
: "${ARENA_PORT_SHA512:?port SHA-512 was not supplied}"
: "${ARENA_PORT_VERSION:?port version was not supplied}"
: "${EMSDK:?the builder image did not set EMSDK}"

# Ask the SDK to state its own pinned port identity before asking it to
# download anything. VERSION and HASH are the SDK's; a disagreement means this
# repository's record has drifted from the toolchain that consumes it.
python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.environ["EMSDK"], "upstream", "emscripten"))
from tools.ports import sdl2

expected = {
    "VERSION": os.environ["ARENA_PORT_VERSION"],
    "HASH": os.environ["ARENA_PORT_SHA512"],
}
for name, value in expected.items():
    actual = getattr(sdl2, name)
    if actual != value:
        raise SystemExit(
            f"pinned builder disagrees with the arena-web port pin: "
            f"{name} is {actual!r}, this repository records {value!r}"
        )
print(f"pinned builder agrees: sdl2 {sdl2.VERSION}")
PY

# The SDK downloads the archive and verifies it against that same SHA-512.
embuilder build "${ARENA_PORT_NAME}"
