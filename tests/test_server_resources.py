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


PROFILE = {
    "cvars": {
        "g_gametype": "0",
        "fraglimit": "15",
        "timelimit": "0",
        "sv_maxclients": "8",
    }
}


class ServerResourceProbeTests(unittest.TestCase):
    def test_exact_getstatus_profile_is_accepted(self) -> None:
        payload = (
            b"\xff\xff\xff\xffstatusResponse\n"
            b"\\challenge\\a11ce55\\mapname\\oa_pvomit\\g_gametype\\0"
            b"\\fraglimit\\15\\timelimit\\0\\sv_maxclients\\8\n"
            b"user-controlled player tail"
        )
        self.assertEqual(
            PROBE.parse_getstatus(payload, ["oa_pvomit"], PROFILE)["mapname"],
            "oa_pvomit",
        )

    def test_the_map_the_probe_asked_for_is_the_map_readiness_requires(self) -> None:
        """Readiness is not "a server answered" but "the server is playing the
        map this caller launched it with". The map is a launch argument, so the
        expectation is the caller's — a literal here would have made every
        rotation but one fail the probe, and accepting whatever came back would
        have made a server that ignored its rotation look ready."""
        payload = (
            b"\xff\xff\xff\xffstatusResponse\n"
            b"\\challenge\\a11ce55\\mapname\\am_galmevish\\g_gametype\\0"
            b"\\fraglimit\\15\\timelimit\\0\\sv_maxclients\\8\n"
        )
        self.assertEqual(
            PROBE.parse_getstatus(payload, ["am_galmevish"], PROFILE)["mapname"],
            "am_galmevish",
        )
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_getstatus(payload, ["oa_pvomit"], PROFILE)

    def test_liveness_does_not_declare_a_rotating_server_failed(self) -> None:
        """Readiness and liveness ask different things of `mapname`.

        `SV_SpawnServer` sets it afresh on every map change, so a liveness check
        that pinned it to the rotation's first entry would declare every
        rotating server failed about three seconds after its first map change —
        the readiness rule applied where it does not belong. After readiness the
        map only has to still be one of the rotation's own, and a map from
        outside it is still a failure.
        """
        payload = (
            b"\xff\xff\xff\xffstatusResponse\n"
            b"\\challenge\\a11ce55\\mapname\\am_galmevish\\g_gametype\\0"
            b"\\fraglimit\\15\\timelimit\\0\\sv_maxclients\\8\n"
        )
        rotation = ["oa_pvomit", "am_galmevish"]
        # As readiness: the rotation has not started its first map yet.
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_getstatus(payload, rotation, PROFILE)
        # As liveness: the server rotated, which is what it was asked to do.
        self.assertEqual(
            PROBE.parse_getstatus(payload, rotation, PROFILE, started=True)["mapname"],
            "am_galmevish",
        )
        # A map outside the rotation is still a failure, in both phases.
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_getstatus(payload, ["oa_pvomit", "czest1dm"], PROFILE, started=True)

    def test_the_other_fields_come_from_the_profile_and_not_from_literals(self) -> None:
        """`check_match_end_cvars` permits any fraglimit/timelimit pair that is
        not both zero, so a literal here would go quietly wrong on a legal
        profile change."""
        payload = (
            b"\xff\xff\xff\xffstatusResponse\n"
            b"\\challenge\\a11ce55\\mapname\\oa_pvomit\\g_gametype\\0"
            b"\\fraglimit\\30\\timelimit\\0\\sv_maxclients\\8\n"
        )
        with self.assertRaises(PROBE.ProbeError):
            PROBE.parse_getstatus(payload, ["oa_pvomit"], PROFILE)
        moved = {"cvars": dict(PROFILE["cvars"], fraglimit="30")}
        self.assertEqual(
            PROBE.parse_getstatus(payload, ["oa_pvomit"], moved)["fraglimit"], "30"
        )

    def test_malformed_or_mismatching_health_is_refused(self) -> None:
        for payload in (
            b"statusResponse\n",
            b"\xff\xff\xff\xffstatusResponse\n\\challenge\\wrong",
            b"\xff\xff\xff\xffstatusResponse\n\\challenge",
        ):
            with self.subTest(payload=payload), self.assertRaises(PROBE.ProbeError):
                PROBE.parse_getstatus(payload, ["oa_pvomit"], PROFILE)

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
