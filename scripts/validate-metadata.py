#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate every committed arena-web lock, manifest and provenance record."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from metadata import MetadataError, validate_repository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--without-git-metadata",
        action="store_true",
        help="validate metadata mounted without its Git directory",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        validated = validate_repository(
            root,
            verify_git=not arguments.without_git_metadata,
        )
    except MetadataError as error:
        print(f"metadata validation failed: {error}", file=sys.stderr)
        return 1
    for path in validated:
        print(f"validated {path.relative_to(root)}")
    print(f"OK - validated {len(validated)} metadata files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
