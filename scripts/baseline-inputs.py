#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Print one immutable identity from the committed WP0 baseline lock.

Build orchestration reads its toolchain and source identities from here instead
of repeating them, so a renamed or digest-divergent substitute cannot enter an
accepted build without failing the lock first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from metadata import (
    MetadataError,
    _canonical_json_identity,
    _load_json,
    validate_baseline,
)

FIELDS = (
    "baseline-identity",
    "builder-image",
    "builder-version",
    "engine-commit",
    "engine-submodule-path",
)


def _tool(baseline: dict, tool_id: str) -> dict:
    for tool in baseline["tools"]:
        if tool["id"] == tool_id:
            return tool
    raise MetadataError(f"baseline.tools: does not record {tool_id!r}")


def resolve(baseline: dict, field: str) -> str:
    if field == "baseline-identity":
        return _canonical_json_identity(baseline)
    if field == "builder-image":
        return _tool(baseline, "emscripten-builder")["immutableRef"]
    if field == "builder-version":
        return _tool(baseline, "emscripten-builder")["version"]
    if field == "engine-commit":
        return baseline["engine"]["commit"]
    if field == "engine-submodule-path":
        return baseline["engine"]["submodulePath"]
    raise MetadataError(f"baseline-inputs: unknown field {field!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field", choices=FIELDS)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        baseline = validate_baseline(
            _load_json(root / "locks" / "baseline.json"),
            "locks/baseline.json",
        )
        print(resolve(baseline, arguments.field))
    except MetadataError as error:
        print(f"baseline lookup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
