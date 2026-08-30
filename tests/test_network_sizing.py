# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the WP6 network-sizing derivation.

Three things are checked here, and the second matters most.

1. The arithmetic reproduces the engine's behaviour and the committed records:
   header widths, fragment splitting, and the fragmented gamestate's wire cost
   as the census recorded it.
2. **A doctored record changes the verdict.** The derivation is only evidence if
   it depends on the evidence, so several tests damage a copy of a committed
   record in a way that would flip a conclusion and assert that the conclusion
   flips — or that the input is refused outright.
3. The worst-case datagram shapes are replayed through the in-memory relay from
   `relay_loopback`, against the datagram maximum the routed record reports.
   That is the deterministic half of "replay worst-case recorded shapes"; the
   routed half belongs to WP7/WP8, because the integration environment the WP2
   round used no longer exists.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
# The replay reuses the WP2 suite's own loopback helpers rather than a second
# copy of them, so it exercises the same machinery the routed round did.
sys.path.insert(0, str(ROOT / "tests"))

from network_sizing import (  # noqa: E402
    CLIENT_TO_SERVER,
    CONNECTIONLESS,
    NETCHAN,
    SERVER_TO_CLIENT,
    NetworkSizingError,
    PrototypeProfile,
    RelayFraming,
    candidate_fragment_size,
    connectionless_boundary_cases,
    derive,
    engine_value,
    fragment_payloads,
    largest_engine_datagram_bytes,
    largest_fragment_datagram_bytes,
    largest_unfragmented_datagram_bytes,
    message_cost,
    netchan_boundary_cases,
    netchan_header_bytes,
    read_census_facts,
    read_path_facts,
)
from relay_loopback import LoopbackRelay  # noqa: E402
from relay_probe import MeasurementPlan, MeasurementReportError  # noqa: E402

from test_relay_probe import make_config, make_vector, run_plan  # noqa: E402

ROUTED_RECORD = ROOT / "records" / "wp2-routed-measurement.json"
CENSUS_RECORD = ROOT / "records" / "wp5-packet-census.json"
MEASUREMENT_VECTOR = ROOT / "locks" / "relay-measurement-vector.json"

STOCK_FRAGMENT_SIZE = 1300
FRAMING = RelayFraming(
    relay_header_bytes=40,
    length_prefix_bytes=2,
    single_datagram_overhead_bytes=42,
)


def load(path: Path):
    return json.loads(path.read_text())


def committed_plan() -> MeasurementPlan:
    return MeasurementPlan.from_vector(load(MEASUREMENT_VECTOR))


def rebudget(report, maximum: int, refuse=()):
    """Return a copy of a report describing a path with a different maximum.

    The doctored records below have to remain *valid* WP2 reports — a report
    the validator rejects proves nothing about the derivation, because the
    derivation would have refused it either way. So every case is recomputed
    consistently: a frame that fits the new maximum echoes and carries a
    round-trip time, one that does not is refused by the size pre-check and
    carries neither. `refuse` names inner sizes to fail regardless of size,
    which is how a lower measured floor is modelled.
    """
    doctored = copy.deepcopy(report)
    refuse = set(refuse)
    for session in doctored["sessions"]:
        session["maxDatagramSizeBytes"] = maximum
        for case in session["cases"]:
            fits = case["sentFrameBytes"] <= maximum
            failed = any(size in refuse for size in case["sentInnerBytes"])
            if fits and not failed:
                case["outcome"] = "echoed"
                case["receivedFrames"] = [
                    {"frameBytes": size + 42, "innerBytes": size}
                    for size in case["sentInnerBytes"]
                ]
                case["roundTripMilliseconds"] = 1.0
            else:
                case["outcome"] = (
                    "timedOut" if fits else "notSentFrameExceedsTransportLimit"
                )
                case["receivedFrames"] = []
                case["roundTripMilliseconds"] = None
    return doctored


class NetchanGeometryTests(unittest.TestCase):
    def test_header_widths_match_the_census(self):
        # The census observed exactly these widths on 41,833 real datagrams.
        self.assertEqual(
            netchan_header_bytes(SERVER_TO_CLIENT, fragmented=False), 8
        )
        self.assertEqual(
            netchan_header_bytes(CLIENT_TO_SERVER, fragmented=False), 10
        )
        self.assertEqual(netchan_header_bytes(SERVER_TO_CLIENT, fragmented=True), 12)
        self.assertEqual(netchan_header_bytes(CLIENT_TO_SERVER, fragmented=True), 14)

    def test_client_header_exceeds_server_header_by_the_qport(self):
        for fragmented in (False, True):
            self.assertEqual(
                netchan_header_bytes(CLIENT_TO_SERVER, fragmented=fragmented)
                - netchan_header_bytes(SERVER_TO_CLIENT, fragmented=fragmented),
                2,
            )

    def test_unknown_direction_is_refused(self):
        with self.assertRaises(NetworkSizingError):
            netchan_header_bytes("sideways", fragmented=False)

    def test_a_legacy_connection_omits_the_checksum(self):
        # The challenge checksum is written only when the connection is not in
        # legacy-protocol compat mode, and LEGACY_PROTOCOL is defined in this
        # build. A compat connection therefore has four fewer header bytes.
        for direction in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
            for fragmented in (False, True):
                self.assertEqual(
                    netchan_header_bytes(direction, fragmented=fragmented)
                    - netchan_header_bytes(
                        direction, fragmented=fragmented, compat=True
                    ),
                    4,
                )

    def test_every_derived_bound_still_holds_on_the_legacy_path(self):
        # The direction of safety: the legacy path can only make datagrams
        # smaller, so every bound derived for the pinned non-compat geometry
        # remains a valid upper bound. Sizing cannot be broken by it.
        for size in (704, 896, 1300):
            for direction in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
                compat_fragment = size + netchan_header_bytes(
                    direction, fragmented=True, compat=True
                )
                self.assertLess(
                    compat_fragment,
                    largest_fragment_datagram_bytes(size, direction),
                )

    def test_stock_maxima(self):
        self.assertEqual(
            largest_unfragmented_datagram_bytes(STOCK_FRAGMENT_SIZE, SERVER_TO_CLIENT),
            1307,
        )
        self.assertEqual(
            largest_unfragmented_datagram_bytes(STOCK_FRAGMENT_SIZE, CLIENT_TO_SERVER),
            1309,
        )
        self.assertEqual(
            largest_fragment_datagram_bytes(STOCK_FRAGMENT_SIZE, SERVER_TO_CLIENT),
            1312,
        )
        self.assertEqual(
            largest_fragment_datagram_bytes(STOCK_FRAGMENT_SIZE, CLIENT_TO_SERVER),
            1314,
        )
        self.assertEqual(largest_engine_datagram_bytes(STOCK_FRAGMENT_SIZE), 1314)

    def test_the_census_maximum_is_the_server_fragment_case(self):
        # 1,312 is not a coincidence: it is FRAGMENT_SIZE plus the fragmented
        # server-to-client header, which is what makes the census's maximum a
        # bound the derivation can move rather than a mystery.
        facts = read_census_facts(load(CENSUS_RECORD))
        self.assertEqual(facts.maximum_by_direction[SERVER_TO_CLIENT], 1312)
        self.assertEqual(
            largest_fragment_datagram_bytes(facts.fragment_size, SERVER_TO_CLIENT),
            facts.maximum_by_direction[SERVER_TO_CLIENT],
        )

    def test_fragment_size_must_be_positive(self):
        for bad in (0, -1, 1.5, True, "1300"):
            with self.assertRaises(NetworkSizingError):
                largest_fragment_datagram_bytes(bad, SERVER_TO_CLIENT)


class FragmentSplittingTests(unittest.TestCase):
    def test_message_below_the_threshold_is_not_fragmented(self):
        self.assertEqual(fragment_payloads(1299, 1300), ())
        self.assertEqual(fragment_payloads(0, 1300), ())

    def test_message_at_the_threshold_is_fragmented(self):
        # Netchan_Transmit tests `length >= FRAGMENT_SIZE`, so a message of
        # exactly FRAGMENT_SIZE fragments — and then needs the zero-length
        # terminator, costing two datagrams for one full fragment of payload.
        self.assertEqual(fragment_payloads(1300, 1300), (1300, 0))

    def test_exact_multiple_gets_a_zero_length_terminator(self):
        self.assertEqual(fragment_payloads(2600, 1300), (1300, 1300, 0))

    def test_partial_last_fragment_ends_the_message(self):
        self.assertEqual(fragment_payloads(2304, 1300), (1300, 1004))

    def test_negative_message_is_refused(self):
        with self.assertRaises(NetworkSizingError):
            fragment_payloads(-1, 1300)

    def test_message_cost_reproduces_the_recorded_gamestate(self):
        # The census recorded both the fragment count and the total UDP payload
        # bytes for the two gamestates. Reproducing both from the model is what
        # ties the arithmetic to observed behaviour rather than to a reading of
        # the sources alone.
        facts = read_census_facts(load(CENSUS_RECORD))
        self.assertTrue(facts.fragmented_messages)
        for message in facts.fragmented_messages:
            cost = message_cost(
                message["messageBytes"],
                facts.fragment_size,
                message["direction"],
                FRAMING,
            )
            self.assertEqual(cost.datagrams, message["fragments"], message)
            self.assertEqual(
                cost.total_datagram_bytes, message["totalUdpPayloadBytes"], message
            )
            self.assertEqual(
                cost.largest_datagram_bytes, message["largestDatagramBytes"], message
            )

    def test_smaller_fragment_size_costs_more_datagrams(self):
        stock = message_cost(2304, 1300, SERVER_TO_CLIENT, FRAMING)
        reduced = message_cost(2304, 704, SERVER_TO_CLIENT, FRAMING)
        self.assertEqual(stock.datagrams, 2)
        self.assertEqual(reduced.datagrams, 4)
        self.assertGreater(reduced.total_frame_bytes, stock.total_frame_bytes)
        self.assertLess(reduced.largest_frame_bytes, stock.largest_frame_bytes)


class CandidateFragmentSizeTests(unittest.TestCase):
    def test_candidate_for_each_budget(self):
        self.assertEqual(candidate_fragment_size(768), 704)
        self.assertEqual(candidate_fragment_size(982), 896)

    def test_candidate_leaves_room_for_the_widest_header(self):
        for budget in (768, 982):
            size = candidate_fragment_size(budget)
            self.assertLessEqual(largest_engine_datagram_bytes(size), budget)

    def test_reserve_and_alignment_are_arguments_not_assumptions(self):
        self.assertEqual(
            candidate_fragment_size(982, reserve_bytes=14, alignment_bytes=1), 968
        )
        self.assertEqual(
            candidate_fragment_size(768, reserve_bytes=14, alignment_bytes=1), 754
        )

    def test_budget_that_cannot_carry_the_reserve_is_refused(self):
        with self.assertRaises(NetworkSizingError):
            candidate_fragment_size(64)
        with self.assertRaises(NetworkSizingError):
            candidate_fragment_size(100, reserve_bytes=64, alignment_bytes=64)


class ConnectionlessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cases = {
            case.name: case for case in connectionless_boundary_cases()
        }

    def test_status_response_is_the_largest_and_exceeds_max_packetlen(self):
        case = self.cases["statusResponse"]
        self.assertEqual(case.inner_bytes, 1443)
        self.assertGreater(case.inner_bytes, engine_value("MAX_PACKETLEN"))
        self.assertFalse(case.fragmentable)

    def test_info_response(self):
        self.assertEqual(self.cases["infoResponse"].inner_bytes, 1040)

    def test_small_handshake_replies(self):
        self.assertEqual(self.cases["challengeResponse"].inner_bytes, 57)
        self.assertEqual(self.cases["connectResponse"].inner_bytes, 31)

    def test_connect_is_the_largest_client_originated_datagram(self):
        client = [
            case
            for case in self.cases.values()
            if case.direction == CLIENT_TO_SERVER and case.on_relay_path
        ]
        largest = max(client, key=lambda case: case.inner_bytes)
        self.assertEqual(largest.name, "connect")
        self.assertEqual(largest.inner_bytes, 1039)
        self.assertTrue(largest.compressed)

    def test_every_connectionless_case_is_unfragmentable(self):
        for case in self.cases.values():
            self.assertFalse(case.fragmentable, case.name)
            self.assertEqual(case.kind, CONNECTIONLESS, case.name)

    def test_echo_is_on_the_relay_path_and_over_budget(self):
        # The destination triggers it, so "the client never sends it" is not
        # available as an exclusion the way it is for the server browser.
        case = self.cases["echo"]
        self.assertTrue(case.on_relay_path)
        self.assertEqual(case.inner_bytes, 1022)
        self.assertGreater(case.inner_bytes, 982)
        self.assertFalse(case.fragmentable)

    def test_the_rcon_answer_is_bounded_by_the_redirect_buffer(self):
        case = self.cases["print-rcon-redirect"]
        self.assertEqual(case.inner_bytes, 1017)
        self.assertFalse(case.on_relay_path)

    def test_getchallenge_carries_both_the_realized_size_and_its_ceiling(self):
        case = self.cases["getchallenge"]
        self.assertEqual(case.inner_bytes, 40)
        self.assertEqual(case.code_ceiling_bytes, 1037)

    def test_the_engine_ceiling_is_the_out_of_band_buffer(self):
        # The point of the connectionless analysis: the engine applies no
        # packet-sized ceiling here, only the message buffer.
        self.assertEqual(self.cases["statusResponse"].code_ceiling_bytes, 16383)
        self.assertEqual(self.cases["connect"].code_ceiling_bytes, 32768)

    def test_observed_sizes_are_below_the_derived_worst_cases(self):
        # Every class the census observed must be at or below its derived
        # bound, or the derivation is wrong about the code.
        facts = read_census_facts(load(CENSUS_RECORD))
        observed = {
            item.name: item.inner_bytes
            for item in facts.observed
            if item.kind == CONNECTIONLESS
        }
        self.assertTrue(observed)
        for name, size in observed.items():
            self.assertIn(name, self.cases)
            self.assertLessEqual(size, self.cases[name].inner_bytes, name)

    def test_required_cap_makes_the_case_fit(self):
        case = self.cases["connect"]
        cap = case.required_cap_bytes(768)
        self.assertEqual(cap, 752)
        self.assertEqual(case.fixed_bytes + cap, 768)

    def test_profile_can_put_the_server_browser_back_on_the_path(self):
        permissive = PrototypeProfile(server_browser_queries_on_relay_path=True)
        cases = {case.name: case for case in connectionless_boundary_cases(permissive)}
        self.assertTrue(cases["statusResponse"].on_relay_path)
        self.assertTrue(cases["getstatus"].on_relay_path)

    def test_more_clients_make_the_status_response_larger(self):
        bigger = PrototypeProfile(max_clients=16)
        cases = {case.name: case for case in connectionless_boundary_cases(bigger)}
        self.assertEqual(
            cases["statusResponse"].inner_bytes,
            self.cases["statusResponse"].inner_bytes + 8 * 50,
        )


class NetchanBoundaryTests(unittest.TestCase):
    def test_four_cases_are_produced(self):
        cases = netchan_boundary_cases(704)
        self.assertEqual(len(cases), 4)
        for case in cases:
            self.assertEqual(case.kind, NETCHAN)
            self.assertTrue(case.fragmentable)

    def test_fragment_case_binds_in_both_directions(self):
        for size in (704, 896, 1300):
            for direction in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
                self.assertGreater(
                    largest_fragment_datagram_bytes(size, direction),
                    largest_unfragmented_datagram_bytes(size, direction),
                )


class PathFactsTests(unittest.TestCase):
    def setUp(self):
        self.plan = committed_plan()
        self.report = load(ROUTED_RECORD)

    def test_committed_record(self):
        facts = read_path_facts(self.report, self.plan)
        self.assertEqual(facts.sessions, 5)
        self.assertEqual(facts.reported_maximum_bytes, 1024)
        self.assertTrue(facts.reported_maximum_constant)
        self.assertEqual(facts.framing.single_datagram_overhead_bytes, 42)
        self.assertEqual(facts.record_backed_inner_floor_bytes, 768)
        self.assertEqual(facts.largest_echoed_inner_bytes, 768)
        self.assertEqual(facts.smallest_refused_inner_bytes, 1024)
        self.assertEqual(facts.derived_inner_budget_bytes, 982)
        self.assertTrue(facts.monotonic)

    def test_the_untested_range_is_reported(self):
        # The derived budget of 982 sits inside a range the round never sent a
        # case in. Recording that is the difference between a measurement and
        # an extrapolation.
        facts = read_path_facts(self.report, self.plan)
        self.assertEqual(facts.untested_inner_range, (769, 1023))
        self.assertGreater(facts.derived_inner_budget_bytes, 769)
        self.assertLess(facts.derived_inner_budget_bytes, 1023)

    def test_inconsistent_framing_is_refused(self):
        doctored = copy.deepcopy(self.report)
        doctored["framing"]["singleDatagramOverheadBytes"] = 40
        with self.assertRaises(MeasurementReportError):
            read_path_facts(doctored, self.plan)

    def test_missing_framing_is_refused(self):
        doctored = copy.deepcopy(self.report)
        del doctored["framing"]
        with self.assertRaises(Exception):
            read_path_facts(doctored, self.plan)

    def test_a_negative_framing_value_is_refused(self):
        doctored = copy.deepcopy(self.report)
        doctored["framing"]["relayHeaderBytes"] = 0
        with self.assertRaises(MeasurementReportError):
            read_path_facts(doctored, self.plan)


class CensusFactsTests(unittest.TestCase):
    def setUp(self):
        self.census = load(CENSUS_RECORD)

    def test_committed_record(self):
        facts = read_census_facts(self.census)
        self.assertEqual(facts.datagrams, 41833)
        self.assertEqual(facts.fragment_size, 1300)
        self.assertEqual(facts.max_packet_len, 1400)
        self.assertEqual(facts.max_msg_len, 16384)
        self.assertEqual(facts.maximum_by_direction[CLIENT_TO_SERVER], 394)
        self.assertEqual(facts.maximum_by_direction[SERVER_TO_CLIENT], 1312)

    def test_gamestate_fragments_are_separated_from_ordinary_traffic(self):
        # Every server datagram except the four gamestate fragments is small.
        # That is what makes a smaller fragment size cheap, so it is asserted
        # rather than asserted-in-prose.
        facts = read_census_facts(self.census)
        self.assertEqual(facts.largest_unfragmented_by_direction[SERVER_TO_CLIENT], 311)
        self.assertEqual(facts.largest_unfragmented_by_direction[CLIENT_TO_SERVER], 394)

    def test_a_census_from_another_engine_is_refused(self):
        doctored = copy.deepcopy(self.census)
        doctored["summary"]["engineBounds"]["fragmentSize"] = 1024
        with self.assertRaises(NetworkSizingError) as caught:
            read_census_facts(doctored)
        self.assertIn("FRAGMENT_SIZE", str(caught.exception))

    def test_a_disagreeing_header_width_is_refused(self):
        doctored = copy.deepcopy(self.census)
        doctored["summary"]["headerAsymmetry"]["server-to-client"]["headerBytes"] = [9]
        with self.assertRaises(NetworkSizingError) as caught:
            read_census_facts(doctored)
        self.assertIn("header", str(caught.exception))

    def test_a_variable_header_width_is_refused(self):
        doctored = copy.deepcopy(self.census)
        doctored["summary"]["headerAsymmetry"]["client-to-server"]["headerBytes"] = [
            10,
            12,
        ]
        with self.assertRaises(NetworkSizingError):
            read_census_facts(doctored)

    def test_a_missing_summary_is_refused(self):
        with self.assertRaises(NetworkSizingError):
            read_census_facts({"session": {}})


class DerivationTests(unittest.TestCase):
    def setUp(self):
        self.plan = committed_plan()
        self.report = load(ROUTED_RECORD)
        self.census = load(CENSUS_RECORD)
        self.result = derive(self.report, self.census, plan=self.plan)

    def test_budgets(self):
        self.assertEqual(
            self.result["budgets"],
            {"recordBackedFloor": 768, "derivedReportedMaximum": 982},
        )

    def test_intact_datagrams_is_refuted_at_both_budgets(self):
        intact = self.result["strategies"]["intactDatagrams"]
        self.assertEqual(intact["requiredInnerBytes"], 1314)
        self.assertEqual(intact["requiredFrameBytes"], 1356)
        self.assertFalse(intact["viableAtRecordBackedFloor"])
        self.assertFalse(intact["viableAtDerivedBudget"])

    def test_even_observed_traffic_does_not_fit(self):
        intact = self.result["strategies"]["intactDatagrams"]
        self.assertEqual(intact["observedRequiredInnerBytes"], 1312)
        self.assertFalse(intact["observedTrafficViableAtRecordBackedFloor"])
        self.assertFalse(intact["observedTrafficViableAtDerivedBudget"])

    def test_the_refutation_is_backed_by_measured_refusals(self):
        # The sizes an unchanged engine needs were not merely computed to be
        # over budget; the routed round sent them and the transport refused.
        intact = self.result["strategies"]["intactDatagrams"]
        sizes = {case["innerBytes"]: case for case in intact["refutedByMeasuredCases"]}
        self.assertIn(1312, sizes)
        self.assertIn(1314, sizes)
        for case in sizes.values():
            self.assertEqual(case["outcome"], "notSentFrameExceedsTransportLimit")

    def test_candidate_fragment_sizes(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        self.assertEqual(
            reduction["recordBackedFloor"]["target"]["candidateFragmentSize"], 704
        )
        self.assertEqual(
            reduction["derivedReportedMaximum"]["target"]["candidateFragmentSize"], 896
        )

    def test_every_netchan_case_fits_at_both_candidates(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        for key in ("recordBackedFloor", "derivedReportedMaximum"):
            self.assertTrue(reduction[key]["fitsEveryNetchanCase"], key)

    def test_margins_are_positive_and_documented(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        self.assertEqual(reduction["recordBackedFloor"]["target"]["marginBytes"], 50)
        self.assertEqual(
            reduction["derivedReportedMaximum"]["target"]["marginBytes"], 72
        )

    def test_the_on_path_classes_needing_a_bound(self):
        # `connect` is originated by the client and `echo` is elicited from it
        # by the destination. Both are out-of-band, so no fragment-size change
        # touches them, and both are over budget at either target.
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        for key in ("recordBackedFloor", "derivedReportedMaximum"):
            self.assertEqual(
                reduction[key]["connectionlessCasesOverBudget"],
                ["connect", "echo"],
                key,
            )
            self.assertTrue(reduction[key]["requiresProfileBounds"], key)

    def test_rcon_and_its_answer_are_both_off_the_relay_path(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        for key in ("recordBackedFloor", "derivedReportedMaximum"):
            off = reduction[key]["connectionlessCasesOverBudgetOffRelayPath"]
            self.assertIn("rcon", off, key)
            self.assertIn("print-rcon-redirect", off, key)

    def test_no_observed_connectionless_size_is_over_budget(self):
        # The derived bounds are what the verdict rests on, so this is a
        # separate population: if a real observed out-of-band datagram were
        # over budget it would be named here rather than folded into the
        # derived list.
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        for key in ("recordBackedFloor", "derivedReportedMaximum"):
            self.assertEqual(
                reduction[key]["observedConnectionlessOverBudget"], [], key
            )

    def test_the_required_userinfo_cap_is_stated(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        caps = {
            entry["name"]: entry
            for entry in reduction["recordBackedFloor"]["requiredProfileCaps"]
        }
        self.assertEqual(caps["connect"]["requiredCapBytes"], 752)
        self.assertTrue(caps["connect"]["achievable"])

    def test_nothing_else_begins_to_fragment(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        for key in ("recordBackedFloor", "derivedReportedMaximum"):
            facts = reduction[key]["newlyFragmentingObservedTraffic"]
            for direction, fact in facts.items():
                self.assertFalse(fact["beginsToFragment"], (key, direction))

    def test_gamestate_cost_at_each_candidate(self):
        reduction = self.result["strategies"]["symmetricFragmentSizeReduction"]
        floor_cost = reduction["recordBackedFloor"]["messageCosts"][0]
        derived_cost = reduction["derivedReportedMaximum"]["messageCosts"][0]
        self.assertEqual(floor_cost["atStockFragmentSize"]["datagrams"], 2)
        self.assertEqual(floor_cost["atCandidateFragmentSize"]["datagrams"], 4)
        self.assertEqual(derived_cost["atCandidateFragmentSize"]["datagrams"], 3)

    def test_tunnel_is_not_required_for_netchan_traffic(self):
        tunnel = self.result["strategies"]["boundedTunnelFragmentation"]
        self.assertFalse(tunnel["requiredForNetchanTraffic"])
        self.assertEqual(
            tunnel["connectionlessCasesItWouldCover"], ["connect", "echo"]
        )

    def test_the_decision_block_names_the_selected_target(self):
        decision = self.result["decision"]
        self.assertEqual(decision["decidedOn"], "2026-08-30")
        self.assertEqual(decision["selectedTarget"], "recordBackedFloor")
        self.assertEqual(
            decision["selectedStrategy"], "symmetricFragmentSizeReduction"
        )
        self.assertEqual(decision["decidedFragmentSize"], 704)
        self.assertEqual(decision["decidedUserinfoCapBytes"], 512)

    def test_the_decided_value_still_follows_from_the_arithmetic(self):
        # The decided fragment size is restated, never used as an input, so
        # this is a real check: if the selected target's arithmetic stopped
        # producing 704, the derivation says so instead of quietly disagreeing
        # with the document.
        decision = self.result["decision"]
        self.assertTrue(decision["candidateMatchesDecidedFragmentSize"])
        self.assertTrue(decision["userinfoCapWithinSelectedTargetLimit"])
        self.assertEqual(decision["selectedTargetUserinfoCapLimitBytes"], 752)

    def test_the_alternative_target_is_still_computed(self):
        # The road not taken stays recomputable rather than becoming a claim.
        decision = self.result["decision"]
        self.assertEqual(decision["consideredNotSelected"], ["derivedReportedMaximum"])
        self.assertIn(
            "derivedReportedMaximum",
            self.result["strategies"]["symmetricFragmentSizeReduction"],
        )

    def test_the_outstanding_review_is_recorded(self):
        # WP6 does not close on the operator's selection alone.
        self.assertTrue(self.result["decision"]["reviewOutstanding"])

    def test_a_different_reserve_shows_the_decided_value_no_longer_follows(self):
        # Reserve and alignment are script arguments, so a reviewer exploring a
        # different margin must see the mismatch reported, not raised.
        result = derive(
            self.report, self.census, plan=self.plan, reserve_bytes=128
        )
        self.assertFalse(result["decision"]["candidateMatchesDecidedFragmentSize"])

    def test_result_is_json_serialisable_and_deterministic(self):
        first = json.dumps(self.result, sort_keys=True)
        second = json.dumps(
            derive(self.report, self.census, plan=self.plan), sort_keys=True
        )
        self.assertEqual(first, second)


class DoctoredRecordFlipsTheVerdictTests(unittest.TestCase):
    """A derivation that ignored its inputs would pass every test above."""

    def setUp(self):
        self.plan = committed_plan()
        self.report = load(ROUTED_RECORD)
        self.census = load(CENSUS_RECORD)

    def test_a_generous_path_makes_intact_datagrams_viable(self):
        # Raise the reported maximum and let every planned size echo. On such a
        # path an unchanged engine would fit, and the derivation must say so
        # rather than repeating the refutation.
        doctored = rebudget(self.report, 20000)
        result = derive(doctored, self.census, plan=self.plan)
        intact = result["strategies"]["intactDatagrams"]
        self.assertTrue(intact["viableAtRecordBackedFloor"])
        self.assertTrue(intact["viableAtDerivedBudget"])
        self.assertGreater(result["budgets"]["recordBackedFloor"], 1314)

    def test_a_smaller_reported_maximum_shrinks_the_candidate(self):
        doctored = rebudget(self.report, 700)
        result = derive(doctored, self.census, plan=self.plan)
        self.assertEqual(result["path"]["derivedInnerBudgetBytes"], 658)
        self.assertEqual(
            result["strategies"]["symmetricFragmentSizeReduction"][
                "derivedReportedMaximum"
            ]["target"]["candidateFragmentSize"],
            576,
        )

    def test_a_lower_floor_lowers_the_candidate_fragment_size(self):
        # Turn the 768-byte case into a refusal. The contiguous walk then stops
        # at 512, and every downstream number must move with it.
        doctored = rebudget(self.report, 1024, refuse=(768,))
        result = derive(doctored, self.census, plan=self.plan)
        self.assertEqual(result["budgets"]["recordBackedFloor"], 512)
        self.assertEqual(
            result["strategies"]["symmetricFragmentSizeReduction"][
                "recordBackedFloor"
            ]["target"]["candidateFragmentSize"],
            448,
        )

    def test_a_larger_gamestate_costs_more_fragments(self):
        doctored = copy.deepcopy(self.census)
        for message in doctored["summary"]["fragmentedMessages"]:
            message["messageBytes"] = 8192
        result = derive(self.report, doctored, plan=self.plan)
        cost = result["strategies"]["symmetricFragmentSizeReduction"][
            "recordBackedFloor"
        ]["messageCosts"][0]
        self.assertEqual(cost["atCandidateFragmentSize"]["datagrams"], 12)

    def test_a_permissive_profile_puts_the_server_browser_back_in_scope(self):
        permissive = PrototypeProfile(server_browser_queries_on_relay_path=True)
        result = derive(
            self.report, self.census, plan=self.plan, profile=permissive
        )
        reduction = result["strategies"]["symmetricFragmentSizeReduction"]
        over = reduction["recordBackedFloor"]["connectionlessCasesOverBudget"]
        self.assertIn("statusResponse", over)
        self.assertIn("infoResponse", over)
        self.assertIn("connect", over)

    def test_a_bigger_reserve_shrinks_the_candidate(self):
        result = derive(
            self.report, self.census, plan=self.plan, reserve_bytes=128
        )
        self.assertEqual(
            result["strategies"]["symmetricFragmentSizeReduction"][
                "recordBackedFloor"
            ]["target"]["candidateFragmentSize"],
            640,
        )


class WorstCaseReplayTests(unittest.TestCase):
    """Carry the derived worst-case shapes through the in-memory relay.

    The routed integration environment the WP2 round used was dismantled, so the
    worst-case shapes cannot be replayed over the real path in this WP. What can
    be replayed deterministically is the contract: the same frame encoder, the
    same relay model and the same size pre-check, driven at the datagram maximum
    the routed record reports. A shape that this refuses would have been refused
    on the routed path for the same arithmetic reason, and the routed record's
    own refusals at 1,312 and 1,314 bytes are the evidence that the model and
    the path agree at those sizes.
    """

    REPORTED_MAXIMUM = 1024

    def _replay(self, sizes, maximum=REPORTED_MAXIMUM):
        vector = make_vector(sizes)
        plan = MeasurementPlan.from_vector(vector)
        relay = LoopbackRelay(max_datagram_size_bytes=maximum)
        driver, _relay = run_plan(plan, relay=relay, config=make_config())
        record = driver.session_record()
        return {
            case["sentInnerBytes"][0]: case["outcome"]
            for case in record["cases"]
            if case["kind"] == "single"
        }

    def test_candidate_worst_cases_are_carried(self):
        # 716 and 718 are the largest datagrams the engine emits at
        # FRAGMENT_SIZE 704; 910 is the largest at 896.
        sizes = (16, 17, 18, 716, 718, 910)
        results = self._replay(sizes)
        for size in sizes:
            self.assertEqual(results[size], "echoed", size)

    def test_the_record_backed_floor_case_is_carried(self):
        results = self._replay((16, 17, 18, 768))
        self.assertEqual(results[768], "echoed")

    def test_stock_engine_worst_cases_are_refused(self):
        # The same two sizes the routed round could not send.
        sizes = (16, 17, 18, 1312, 1314)
        results = self._replay(sizes)
        self.assertEqual(results[1312], "notSentFrameExceedsTransportLimit")
        self.assertEqual(results[1314], "notSentFrameExceedsTransportLimit")

    def test_the_uncapped_connect_packet_is_refused(self):
        results = self._replay((16, 17, 18, 1039))
        self.assertEqual(results[1039], "notSentFrameExceedsTransportLimit")

    def test_the_capped_connect_packet_is_carried(self):
        # 752 bytes of userinfo plus the 16 fixed bytes is exactly the floor.
        results = self._replay((16, 17, 18, 768))
        self.assertEqual(results[768], "echoed")

    def test_every_gamestate_fragment_is_carried_at_the_candidate_size(self):
        # Replay the actual fragment sequence the recorded gamestate produces
        # at FRAGMENT_SIZE 704, as datagrams rather than as arithmetic.
        facts = read_census_facts(load(CENSUS_RECORD))
        message = facts.fragmented_messages[0]
        payloads = fragment_payloads(message["messageBytes"], 704)
        header = netchan_header_bytes(SERVER_TO_CLIENT, fragmented=True)
        sizes = sorted({16, 17, 18} | {header + payload for payload in payloads})
        results = self._replay(tuple(sizes))
        for size in sizes:
            self.assertEqual(results[size], "echoed", size)
        self.assertEqual(len(payloads), 4)

    def test_a_tighter_transport_refuses_the_larger_candidate(self):
        # If a live session reported a maximum matching the 768-byte floor
        # rather than 1,024, the 896-candidate worst case would stop fitting
        # while the 704-candidate worst case would still be carried.
        results = self._replay((16, 17, 18, 718, 910), maximum=768 + 42)
        self.assertEqual(results[718], "echoed")
        self.assertEqual(results[910], "notSentFrameExceedsTransportLimit")


if __name__ == "__main__":
    unittest.main()
