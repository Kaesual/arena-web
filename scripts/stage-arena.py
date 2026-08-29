#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble the served runtime set of the offline vertical slice.

The staged tree contains the product loader, the committed content
configuration, the two committed manifests and nothing but the artifacts those
manifests declare — each copied only after its SHA-256 and byte length match
the committed identity. The tree is then re-read and compared to the expected
file set, so an extra, missing or modified file fails before anything is
served.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_runtime import ArenaRuntimeError, stage, verify_staged  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE_DIR = REPO_ROOT / "build/browser/tree/Release"
DEFAULT_CONTENT_DIR = REPO_ROOT / "build/content-pack"
DEFAULT_TARGET = REPO_ROOT / "build/arena-serve"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-dir",
        type=Path,
        default=DEFAULT_ENGINE_DIR,
        help="accepted browser build output (default: build/browser/tree/Release)",
    )
    parser.add_argument(
        "--content-dir",
        type=Path,
        default=DEFAULT_CONTENT_DIR,
        help="accepted content assembly (default: build/content-pack)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help="staged serve root, deleted first (default: build/arena-serve)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify an existing staged tree instead of rebuilding it",
    )
    parser.add_argument("--json", action="store_true", help="write the report as JSON")
    arguments = parser.parse_args()

    target = arguments.target.resolve()
    # The staged tree is deleted on every run, so it may only ever live inside
    # this repository's gitignored build directory.
    build_root = (REPO_ROOT / "build").resolve()
    if build_root not in target.parents:
        print(f"--target must be inside {build_root}", file=sys.stderr)
        return 2

    try:
        if arguments.check:
            report = verify_staged(REPO_ROOT, target)
        else:
            report = stage(
                REPO_ROOT,
                target,
                engine_dir=arguments.engine_dir.resolve(),
                content_dir=arguments.content_dir.resolve(),
            )
    except ArenaRuntimeError as error:
        print(f"arena runtime set refused: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"staged {len(report['servedFiles'])} files in {target}")
        for served in report["servedFiles"]:
            print(f"  {served}")
        if "totalArtifactBytes" in report:
            megabytes = report["totalArtifactBytes"] / (1024 * 1024)
            print(
                f"verified artifact bytes: {report['totalArtifactBytes']} ({megabytes:.1f} MiB)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
