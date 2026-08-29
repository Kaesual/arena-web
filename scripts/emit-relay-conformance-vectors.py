#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Emit or check the committed routed datagram conformance vectors."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from relay_vectors import encode_conformance_vectors

DEFAULT_OUTPUT = Path("probe") / "conformance-vectors.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="destination file (default: probe/conformance-vectors.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the committed file is current, writing nothing",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = arguments.output or root / DEFAULT_OUTPUT
    encoded = encode_conformance_vectors()
    if arguments.check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError as error:
            print(f"conformance vectors unreadable: {error}", file=sys.stderr)
            return 1
        if current != encoded:
            print(f"{output} is stale; re-run without --check", file=sys.stderr)
            return 1
        print(f"{output} is current")
        return 0
    output.write_text(encoded, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
