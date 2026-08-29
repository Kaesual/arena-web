#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Emit the artifact manifest of one accepted browser engine build."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from artifact_manifest import build_manifest, collect_artifacts, file_sha256
from metadata import MetadataError, _load_json, validate_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help="distributable build output directory",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-commit", required=True)
    parser.add_argument(
        "--port-archive",
        required=True,
        type=Path,
        help="verified Emscripten port archive consumed by the build",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        baseline = validate_baseline(
            _load_json(root / "locks" / "baseline.json"),
            "locks/baseline.json",
        )
        port_input = {
            "id": "emscripten-port-sdl2",
            "identity": f"sha256:{file_sha256(arguments.port_archive)}",
            "kind": "archive",
        }
        manifest = build_manifest(
            baseline,
            collect_artifacts(arguments.artifact_root),
            arguments.producer_commit,
            extra_inputs=[port_input],
        )
    except (MetadataError, OSError) as error:
        print(f"artifact manifest failed: {error}", file=sys.stderr)
        return 1
    encoded = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    arguments.output.write_text(encoded, encoding="utf-8")
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    print(f"artifact manifest sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
