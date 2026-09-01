# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic tests for the browser canvas-to-SDL resize bridge."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not available to run the resize bridge")
class CanvasResizeBridgeTests(unittest.TestCase):
    def test_deterministic_resize_harness(self) -> None:
        result = subprocess.run(
            [NODE, str(ROOT / "tests/canvas_resize_harness.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["checks"], 16)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
