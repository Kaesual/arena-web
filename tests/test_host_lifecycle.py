# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic tests for the public browser-host lifecycle primitive."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not available to run the host lifecycle")
class HostLifecycleTests(unittest.TestCase):
    def test_deterministic_lifecycle_harness(self) -> None:
        result = subprocess.run(
            [NODE, str(ROOT / "tests/host_lifecycle_harness.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertGreaterEqual(report["checks"], 17)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
