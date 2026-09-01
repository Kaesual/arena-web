# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic checks for the WP11 health and observation vocabulary."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "probe_server_resources", ROOT / "scripts/probe-server-resources.py"
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)


class ServerResourceProbeTests(unittest.TestCase):
    def test_exact_getstatus_profile_is_accepted(self) -> None:
        payload = (
            b"\xff\xff\xff\xffstatusResponse\n"
            b"\\challenge\\a11ce55\\mapname\\oa_pvomit\\g_gametype\\0"
            b"\\fraglimit\\15\\timelimit\\0\\sv_maxclients\\8\n"
            b"user-controlled player tail"
        )
        self.assertEqual(PROBE.parse_getstatus(payload)["mapname"], "oa_pvomit")

    def test_malformed_or_mismatching_health_is_refused(self) -> None:
        for payload in (
            b"statusResponse\n",
            b"\xff\xff\xff\xffstatusResponse\n\\challenge\\wrong",
            b"\xff\xff\xff\xffstatusResponse\n\\challenge",
        ):
            with self.subTest(payload=payload), self.assertRaises(PROBE.ProbeError):
                PROBE.parse_getstatus(payload)

    def test_observation_vocabulary_is_fail_closed(self) -> None:
        self.assertEqual(
            PROBE.observation(
                exists=False, running=False, ready=False, within_deadline=False, failures=0
            ),
            "missing",
        )
        self.assertEqual(
            PROBE.observation(
                exists=True, running=True, ready=False, within_deadline=True, failures=0
            ),
            "preparing",
        )
        self.assertEqual(
            PROBE.observation(
                exists=True, running=True, ready=True, within_deadline=False, failures=2
            ),
            "ready",
        )
        for arguments in (
            dict(exists=True, running=False, ready=False, within_deadline=False, failures=0),
            dict(exists=True, running=True, ready=False, within_deadline=False, failures=0),
            dict(exists=True, running=True, ready=True, within_deadline=False, failures=3),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(PROBE.observation(**arguments), "failed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
