#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble the game tree of the native server or of the native test client.

Both trees hold the same audited content pack and QVMs from the same accepted
builds; they differ only in which game modules that side loads and in which
`default.cfg` the engine finds. Every artifact is copied out of a build
directory only after its SHA-256 and byte length match the committed manifest
entry, and the staged tree is then re-read and compared to the expected file
set, so an extra, missing or modified file fails before anything is packaged or
run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_server import (  # noqa: E402
    ArenaServerError,
    client_tree_files,
    load_profile,
    server_tree_files,
    stage_tree,
    verify_staged_tree,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE_DIR = REPO_ROOT / "build/browser/tree/Release"
DEFAULT_CONTENT_DIR = REPO_ROOT / "build/content-pack"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        choices=("client", "server"),
        default="server",
        help="which side's game tree to stage (default: server)",
    )
    parser.add_argument(
        "--target", type=Path, required=True, help="staged tree, deleted first"
    )
    parser.add_argument("--engine-dir", type=Path, default=DEFAULT_ENGINE_DIR)
    parser.add_argument("--content-dir", type=Path, default=DEFAULT_CONTENT_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify an existing staged tree instead of rebuilding it",
    )
    parser.add_argument("--json", action="store_true", help="write the report as JSON")
    arguments = parser.parse_args()

    target = arguments.target.resolve()
    build_root = (REPO_ROOT / "build").resolve()
    if build_root not in target.parents:
        print(f"--target must be inside {build_root}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(REPO_ROOT)
        files = (
            server_tree_files(REPO_ROOT, profile)
            if arguments.role == "server"
            else client_tree_files(REPO_ROOT, profile)
        )
        if arguments.check:
            verify_staged_tree(target, files)
            verified = []
        else:
            verified = stage_tree(
                REPO_ROOT,
                target,
                files,
                engine_dir=arguments.engine_dir.resolve(),
                content_dir=arguments.content_dir.resolve(),
            )
    except ArenaServerError as error:
        print(f"native {arguments.role} tree refused: {error}", file=sys.stderr)
        return 1

    report = {
        "role": arguments.role,
        "files": sorted(files),
        "artifacts": verified,
        "target": str(target),
    }
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"staged {len(files)} files in {target}")
        for name in sorted(files):
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
