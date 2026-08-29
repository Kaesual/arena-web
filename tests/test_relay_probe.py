# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from relay_loopback import (  # noqa: E402
    FAULT_CORRUPT_PAYLOAD,
    FAULT_NONE,
    FAULT_DECLARED_OVERSIZE,
    FAULT_FOREIGN_PREFIX,
    FAULT_HEADER_ONLY_RETURN,
    FAULT_PACKED_RETURN,
    FAULT_TRUNCATED_RETURN,
    SYNTHETIC_PREFIX,
    SYNTHETIC_RETURN_PREFIX,
    LoopbackRelay,
    run_session,
    run_sessions,
)
from relay_probe import (  # noqa: E402
    BROWSER_TO_SERVER,
    CASE_PACKED,
    CASE_SINGLE,
    LENGTH_PREFIX_BYTES,
    MAX_LENGTH_PREFIX_VALUE,
    MINIMUM_TAGGED_INNER_BYTES,
    NONCE_BYTES,
    DEFAULT_MAX_IN_FLIGHT_DATAGRAMS,
    OUTCOME_ECHOED,
    OUTCOME_NOT_RUN,
    OUTCOME_NOT_SENT,
    OUTCOME_PAYLOAD_MISMATCH,
    OUTCOME_SEND_FAILED,
    OUTCOMES,
    OUTCOME_TIMED_OUT,
    RELAY_HEADER_BYTES,
    SERVER_TO_BROWSER,
    SESSION_NONCE_BYTES,
    SINGLE_DATAGRAM_OVERHEAD_BYTES,
    MeasurementPlan,
    MeasurementPlanError,
    MeasurementReportError,
    ProbeConfigError,
    RelayFrameError,
    RelayProbeError,
    SessionDriver,
    build_payload,
    build_report,
    datagram_tag,
    decode_frame,
    encode_frame,
    frame_bytes_for_sizes,
    merge_reports,
    parse_probe_config,
    read_tag,
    summarize_report,
    validate_report,
)
from relay_vectors import (  # noqa: E402
    build_conformance_vectors,
    encode_conformance_vectors,
)

COMMITTED_VECTOR = json.loads(
    (ROOT / "locks" / "relay-measurement-vector.json").read_text(encoding="utf-8")
)
COMMITTED_CONFORMANCE = json.loads(
    (ROOT / "probe" / "conformance-vectors.json").read_text(encoding="utf-8")
)

NONCE = bytes(range(SESSION_NONCE_BYTES))
OTHER_NONCE = bytes(range(100, 100 + SESSION_NONCE_BYTES))

# Synthetic values, not credentials. Each one is a replacement pattern that
# JavaScript's String.replace() would expand rather than insert literally.
DOLLAR_AUTHORIZATIONS = (
    "plain-value",
    "$&",
    "$`",
    "$'",
    "$$",
    "$1",
    "before$&after",
    "$$$&$1",
)

BASE_CONFIG = {
    "authorization": "one-time-value",
    "destinationPortMatchesProjection": True,
    "endpointTemplate": "https://relay.invalid/probe?a={authorization}",
    "routingPrefixHex": SYNTHETIC_PREFIX.hex(),
}


def make_config(**overrides):
    mapping = dict(BASE_CONFIG)
    mapping.update(overrides)
    return parse_probe_config(mapping)


def make_vector(sizes, packed=((16, 17),), boundaries=(17,)):
    """Return a small measurement vector shaped like the committed one."""
    ordered = sorted(set(sizes))
    return {
        "$schema": "../schemas/relay-measurement-vector.schema.json",
        "directions": {
            "browserToServer": list(ordered),
            "serverToBrowser": list(ordered),
        },
        "formatVersion": 1,
        "framing": {
            "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
            "relayHeaderBytes": RELAY_HEADER_BYTES,
            "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
        },
        "packedCases": [
            {"direction": BROWSER_TO_SERVER, "sizes": list(entry)} for entry in packed
        ],
        "payloadIdentification": {
            "minimumTaggedInnerBytes": MINIMUM_TAGGED_INNER_BYTES,
            "nonceBytes": NONCE_BYTES,
            "placement": "payload-prefix",
            "smallerCasesRunSequentially": True,
        },
        "requiredBoundaryBytes": list(boundaries),
    }


def run_plan(plan, relay=None, config=None, nonce=NONCE):
    relay = relay or LoopbackRelay(max_datagram_size_bytes=20000)
    adapter = relay.attach()
    driver = SessionDriver(plan, adapter, nonce, config or make_config())
    run_session(driver, adapter)
    return driver, relay


def outcomes(record):
    return [case["outcome"] for case in record["cases"]]


class FrameGrammarTests(unittest.TestCase):
    def test_single_datagram_overhead_is_exactly_42_bytes(self) -> None:
        for size in (0, 1, 16, 1300, 1314, 16384):
            payload = bytes(size)
            frame = encode_frame(SYNTHETIC_PREFIX, (payload,), BROWSER_TO_SERVER)
            self.assertEqual(len(frame), size + SINGLE_DATAGRAM_OVERHEAD_BYTES)
            self.assertEqual(len(frame), frame_bytes_for_sizes((size,)))

    def test_packed_frame_pays_the_prefix_once_and_a_length_per_datagram(self) -> None:
        sizes = (16, 1300, 256)
        payloads = [bytes(size) for size in sizes]
        frame = encode_frame(SYNTHETIC_PREFIX, payloads, BROWSER_TO_SERVER)
        self.assertEqual(
            len(frame),
            RELAY_HEADER_BYTES + sum(LENGTH_PREFIX_BYTES + size for size in sizes),
        )
        self.assertEqual(len(frame), frame_bytes_for_sizes(sizes))

    def test_length_prefix_is_big_endian(self) -> None:
        frame = encode_frame(SYNTHETIC_PREFIX, (bytes(1300),), BROWSER_TO_SERVER)
        self.assertEqual(
            frame[RELAY_HEADER_BYTES : RELAY_HEADER_BYTES + 2], b"\x05\x14"
        )

    def test_round_trip_preserves_prefix_and_payloads(self) -> None:
        payloads = (b"\x01\x02", b"", bytes(range(64)))
        frame = encode_frame(SYNTHETIC_PREFIX, payloads, BROWSER_TO_SERVER)
        decoded = decode_frame(frame, BROWSER_TO_SERVER)
        self.assertEqual(decoded.prefix, SYNTHETIC_PREFIX)
        self.assertEqual(decoded.datagrams, payloads)

    def test_server_direction_carries_exactly_one_datagram(self) -> None:
        payload = bytes(16)
        with self.assertRaises(RelayFrameError):
            encode_frame(SYNTHETIC_PREFIX, (payload, payload), SERVER_TO_BROWSER)
        with self.assertRaises(RelayFrameError):
            encode_frame(SYNTHETIC_PREFIX, (), SERVER_TO_BROWSER)
        packed = encode_frame(SYNTHETIC_PREFIX, (payload, payload), BROWSER_TO_SERVER)
        with self.assertRaises(RelayFrameError):
            decode_frame(packed, SERVER_TO_BROWSER)
        with self.assertRaises(RelayFrameError):
            decode_frame(SYNTHETIC_PREFIX, SERVER_TO_BROWSER)

    def test_browser_direction_needs_at_least_one_datagram(self) -> None:
        with self.assertRaises(RelayFrameError):
            encode_frame(SYNTHETIC_PREFIX, (), BROWSER_TO_SERVER)
        with self.assertRaises(RelayFrameError):
            decode_frame(SYNTHETIC_PREFIX, BROWSER_TO_SERVER)

    def test_zero_length_inner_datagram_is_legal_in_both_directions(self) -> None:
        for direction in (BROWSER_TO_SERVER, SERVER_TO_BROWSER):
            frame = encode_frame(SYNTHETIC_PREFIX, (b"",), direction)
            self.assertEqual(len(frame), SINGLE_DATAGRAM_OVERHEAD_BYTES)
            self.assertEqual(decode_frame(frame, direction).datagrams, (b"",))

    def test_frame_shorter_than_the_routing_prefix_is_rejected(self) -> None:
        for length in (0, 1, RELAY_HEADER_BYTES - 1):
            with self.assertRaises(RelayFrameError):
                decode_frame(bytes(length), SERVER_TO_BROWSER)

    def test_frame_ending_inside_a_length_prefix_is_rejected(self) -> None:
        with self.assertRaises(RelayFrameError):
            decode_frame(SYNTHETIC_PREFIX + b"\x00", SERVER_TO_BROWSER)

    def test_frame_ending_inside_a_datagram_is_rejected(self) -> None:
        frame = encode_frame(SYNTHETIC_PREFIX, (bytes(16),), SERVER_TO_BROWSER)
        with self.assertRaises(RelayFrameError):
            decode_frame(frame[:-1], SERVER_TO_BROWSER)

    def test_trailing_byte_after_the_last_datagram_is_rejected(self) -> None:
        frame = encode_frame(SYNTHETIC_PREFIX, (bytes(16),), BROWSER_TO_SERVER)
        with self.assertRaises(RelayFrameError):
            decode_frame(frame + b"\x00", BROWSER_TO_SERVER)

    def test_declared_length_beyond_the_frame_is_rejected(self) -> None:
        frame = SYNTHETIC_PREFIX + b"\xff\xff" + bytes(4)
        with self.assertRaises(RelayFrameError):
            decode_frame(frame, SERVER_TO_BROWSER)

    def test_declared_length_is_checked_before_anything_is_allocated(self) -> None:
        frame = SYNTHETIC_PREFIX + b"\xff\xff" + bytes(4)
        tracemalloc.start()
        try:
            with self.assertRaises(RelayFrameError):
                decode_frame(frame, SERVER_TO_BROWSER)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        # A parser that sized a buffer from the declared length would need at
        # least 65,535 bytes here.
        self.assertLess(peak, 16384)

    def test_inner_datagram_above_the_ceiling_is_rejected(self) -> None:
        frame = SYNTHETIC_PREFIX + b"\x04\x00" + bytes(1024)
        with self.assertRaises(RelayFrameError):
            decode_frame(frame, SERVER_TO_BROWSER, 1023)
        self.assertEqual(len(decode_frame(frame, SERVER_TO_BROWSER, 1024).datagrams), 1)
        with self.assertRaises(RelayFrameError):
            encode_frame(SYNTHETIC_PREFIX, (bytes(1024),), BROWSER_TO_SERVER, 1023)

    def test_largest_representable_datagram_round_trips(self) -> None:
        payload = bytes(MAX_LENGTH_PREFIX_VALUE)
        frame = encode_frame(SYNTHETIC_PREFIX, (payload,), SERVER_TO_BROWSER)
        self.assertEqual(
            len(frame), MAX_LENGTH_PREFIX_VALUE + SINGLE_DATAGRAM_OVERHEAD_BYTES
        )
        self.assertEqual(decode_frame(frame, SERVER_TO_BROWSER).datagrams[0], payload)

    def test_routing_prefix_length_is_enforced(self) -> None:
        for prefix in (SYNTHETIC_PREFIX[:-1], SYNTHETIC_PREFIX + b"\x00"):
            with self.assertRaises(RelayFrameError):
                encode_frame(prefix, (b"",), BROWSER_TO_SERVER)

    def test_unknown_direction_is_rejected(self) -> None:
        with self.assertRaises(RelayFrameError):
            encode_frame(SYNTHETIC_PREFIX, (b"",), "sideways")
        with self.assertRaises(RelayFrameError):
            decode_frame(SYNTHETIC_PREFIX + b"\x00\x00", "sideways")


class PayloadTagTests(unittest.TestCase):
    def test_tag_is_the_session_nonce_and_a_big_endian_ordinal(self) -> None:
        tag = datagram_tag(NONCE, 0x01020304)
        self.assertEqual(len(tag), NONCE_BYTES)
        self.assertEqual(tag[:SESSION_NONCE_BYTES], NONCE)
        self.assertEqual(tag[SESSION_NONCE_BYTES:], b"\x01\x02\x03\x04")

    def test_tagged_payload_opens_with_the_tag_and_continues_with_filler(self) -> None:
        payload = build_payload(NONCE, 3, 20)
        self.assertEqual(payload[:NONCE_BYTES], datagram_tag(NONCE, 3))
        self.assertEqual(payload[NONCE_BYTES:], bytes([16, 17, 18, 19]))

    def test_untagged_payload_is_filler_only(self) -> None:
        self.assertEqual(build_payload(NONCE, 3, 0), b"")
        self.assertEqual(build_payload(NONCE, 3, 1), b"\x00")
        self.assertEqual(build_payload(NONCE, 3, 15), bytes(range(15)))

    def test_filler_wraps_every_256_bytes(self) -> None:
        payload = build_payload(NONCE, 0, 300)
        self.assertEqual(payload[256], 0)
        self.assertEqual(payload[257], 1)

    def test_read_tag_reports_nothing_below_the_minimum(self) -> None:
        self.assertIsNone(read_tag(build_payload(NONCE, 1, 15)))
        self.assertEqual(read_tag(build_payload(NONCE, 1, 16)), (NONCE, 1))

    def test_tag_rejects_a_wrong_nonce_length_or_ordinal(self) -> None:
        with self.assertRaises(RelayProbeError):
            datagram_tag(NONCE[:-1], 0)
        with self.assertRaises(RelayProbeError):
            datagram_tag(NONCE, -1)
        with self.assertRaises(RelayProbeError):
            datagram_tag(NONCE, 1 << 32)

    def test_payload_size_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(RelayProbeError):
            build_payload(NONCE, 0, -1)
        with self.assertRaises(RelayProbeError):
            build_payload(NONCE, 0, MAX_LENGTH_PREFIX_VALUE + 1)


class MeasurementPlanTests(unittest.TestCase):
    def test_module_constants_match_the_committed_vector(self) -> None:
        framing = COMMITTED_VECTOR["framing"]
        self.assertEqual(framing["relayHeaderBytes"], RELAY_HEADER_BYTES)
        self.assertEqual(framing["datagramLengthPrefixBytes"], LENGTH_PREFIX_BYTES)
        self.assertEqual(
            framing["singleDatagramOverheadBytes"], SINGLE_DATAGRAM_OVERHEAD_BYTES
        )
        self.assertEqual(
            RELAY_HEADER_BYTES + LENGTH_PREFIX_BYTES, SINGLE_DATAGRAM_OVERHEAD_BYTES
        )
        identification = COMMITTED_VECTOR["payloadIdentification"]
        self.assertEqual(identification["nonceBytes"], NONCE_BYTES)
        self.assertEqual(
            identification["minimumTaggedInnerBytes"], MINIMUM_TAGGED_INNER_BYTES
        )
        self.assertEqual(SESSION_NONCE_BYTES + 4, NONCE_BYTES)

    def test_committed_vector_builds_a_plan(self) -> None:
        plan = MeasurementPlan.from_vector(COMMITTED_VECTOR)
        singles = [case for case in plan.cases if case.kind == CASE_SINGLE]
        packed = [case for case in plan.cases if case.kind == CASE_PACKED]
        self.assertEqual(len(packed), len(COMMITTED_VECTOR["packedCases"]))
        expected = sorted(
            set(COMMITTED_VECTOR["directions"]["browserToServer"])
            | set(COMMITTED_VECTOR["directions"]["serverToBrowser"])
        )
        self.assertEqual([case.sizes[0] for case in singles], expected)
        self.assertEqual(plan.max_inner_datagram_bytes, max(expected))

    def test_plan_ordinals_are_unique_and_assigned_in_send_order(self) -> None:
        plan = MeasurementPlan.from_vector(COMMITTED_VECTOR)
        ordinals = [ordinal for case in plan.cases for ordinal in case.ordinals]
        self.assertEqual(ordinals, list(range(plan.datagram_count)))

    def test_plan_requires_both_neighbours_of_every_required_boundary(self) -> None:
        vector = copy.deepcopy(COMMITTED_VECTOR)
        vector["directions"]["browserToServer"].remove(1299)
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector)

    def test_plan_rejects_a_vector_that_disagrees_about_framing(self) -> None:
        for field in ("relayHeaderBytes", "datagramLengthPrefixBytes"):
            vector = copy.deepcopy(COMMITTED_VECTOR)
            vector["framing"][field] += 1
            with self.assertRaises(MeasurementPlanError):
                MeasurementPlan.from_vector(vector)

    def test_plan_rejects_a_vector_that_disagrees_about_the_tag(self) -> None:
        vector = copy.deepcopy(COMMITTED_VECTOR)
        vector["payloadIdentification"]["nonceBytes"] = 8
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector)
        vector = copy.deepcopy(COMMITTED_VECTOR)
        vector["payloadIdentification"]["smallerCasesRunSequentially"] = False
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector)

    def test_plan_rejects_an_uncorrelatable_packed_case(self) -> None:
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(make_vector([0, 16, 17, 18], packed=((1, 16),)))

    def test_plan_rejects_a_server_to_browser_packed_case(self) -> None:
        vector = make_vector([16, 17, 18])
        vector["packedCases"][0]["direction"] = SERVER_TO_BROWSER
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector)

    def test_plan_rejects_a_packed_case_wider_than_the_outstanding_bound(self) -> None:
        # A packed case is atomic, so a case wider than the bound could only be
        # started by breaking it. The vector is refused instead.
        vector = make_vector([16, 17, 18], packed=((16, 17, 18),))
        MeasurementPlan.from_vector(vector, max_in_flight_datagrams=3)
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector, max_in_flight_datagrams=2)
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector, max_in_flight_datagrams=0)

    def test_the_committed_vector_fits_the_default_bound(self) -> None:
        widest = max(len(entry["sizes"]) for entry in COMMITTED_VECTOR["packedCases"])
        self.assertLessEqual(widest, DEFAULT_MAX_IN_FLIGHT_DATAGRAMS)

    def test_plan_rejects_a_packed_case_above_the_measured_ceiling(self) -> None:
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(make_vector([16, 17, 18], packed=((16, 4096),)))

    def test_plan_rejects_malformed_vectors(self) -> None:
        for mutate in (
            lambda vector: vector.pop("directions"),
            lambda vector: vector.pop("framing"),
            lambda vector: vector.pop("payloadIdentification"),
            lambda vector: vector.pop("packedCases"),
            lambda vector: vector.pop("requiredBoundaryBytes"),
            lambda vector: vector["directions"].__setitem__("browserToServer", []),
            lambda vector: vector["directions"].__setitem__("serverToBrowser", ["16"]),
            lambda vector: vector["packedCases"].__setitem__(0, {"direction": "x"}),
            lambda vector: vector["packedCases"][0].__setitem__("sizes", [16]),
        ):
            vector = make_vector([16, 17, 18])
            mutate(vector)
            with self.assertRaises(MeasurementPlanError):
                MeasurementPlan.from_vector(vector)
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector("not a vector")

    def test_plan_rejects_a_size_outside_the_length_prefix(self) -> None:
        vector = make_vector([16, 17, 18])
        vector["directions"]["browserToServer"].append(MAX_LENGTH_PREFIX_VALUE + 1)
        with self.assertRaises(MeasurementPlanError):
            MeasurementPlan.from_vector(vector)


class ProbeConfigTests(unittest.TestCase):
    def test_every_required_field_is_required(self) -> None:
        for field in BASE_CONFIG:
            mapping = dict(BASE_CONFIG)
            del mapping[field]
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(mapping)

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(dict(BASE_CONFIG, relayHost="relay.invalid"))

    def test_configuration_must_be_an_object(self) -> None:
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(["endpoint"])

    def test_endpoint_template_must_be_https_with_one_placeholder(self) -> None:
        for template in (
            "",
            "   ",
            "http://relay.invalid/probe?a={authorization}",
            "https://relay.invalid/probe",
            "https://relay.invalid/probe?a={authorization}&b={authorization}",
        ):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, endpointTemplate=template))

    def test_authorization_must_be_present_and_is_only_substituted_at_connect(
        self,
    ) -> None:
        for value in ("", "   ", None, 7):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, authorization=value))
        config = make_config()
        self.assertNotIn("one-time-value", config.endpoint_template)
        self.assertEqual(
            config.endpoint_url(),
            "https://relay.invalid/probe?a=one-time-value",
        )

    def test_substitution_is_literal_for_a_dollar_pattern_authorization(self) -> None:
        # Only the literal value may reach the wire. A naive JavaScript
        # String.replace() would expand these into the surrounding match.
        for value in DOLLAR_AUTHORIZATIONS:
            config = make_config(authorization=value)
            self.assertEqual(
                config.endpoint_url(),
                f"https://relay.invalid/probe?a={value}",
            )

    def test_destination_port_agreement_must_be_acknowledged(self) -> None:
        for value in (False, "true", 1, None):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(
                    dict(BASE_CONFIG, destinationPortMatchesProjection=value)
                )

    def test_routing_prefix_must_be_forty_hexadecimal_bytes(self) -> None:
        for value in ("", SYNTHETIC_PREFIX.hex()[:-2], "zz" * 40, 40):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, routingPrefixHex=value))
        self.assertEqual(make_config().routing_prefix, SYNTHETIC_PREFIX)

    def test_expected_return_prefix_is_optional_but_checked(self) -> None:
        self.assertEqual(make_config().expected_return_prefix, b"")
        config = make_config(expectedReturnPrefixHex=SYNTHETIC_RETURN_PREFIX.hex())
        self.assertEqual(config.expected_return_prefix, SYNTHETIC_RETURN_PREFIX)
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(dict(BASE_CONFIG, expectedReturnPrefixHex="00"))

    def test_certificate_hashes_must_be_sha256_digests(self) -> None:
        make_config(certificateHashes=["ab" * 32])
        for value in (["ab" * 31], ["zz" * 32], "ab" * 32, [None]):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, certificateHashes=value))

    def test_numeric_bounds_are_enforced(self) -> None:
        for field, bad in (
            ("caseTimeoutMilliseconds", 0),
            ("maxInFlightDatagrams", 0),
        ):
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, **{field: bad}))
            with self.assertRaises(ProbeConfigError):
                parse_probe_config(dict(BASE_CONFIG, **{field: True}))

    def test_path_notes_must_be_text(self) -> None:
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(dict(BASE_CONFIG, pathNotes=17))


class ConfigHexStrictnessTests(unittest.TestCase):
    """`bytes.fromhex` skips whitespace, so a value of the right length can
    decode short. A 20-byte routing prefix would then fail deep inside the run,
    and a short expected return prefix would silently reject every frame."""

    def test_whitespace_inside_a_hex_field_is_refused(self) -> None:
        spaced = "  " + SYNTHETIC_PREFIX.hex()[2:]
        self.assertEqual(len(spaced), RELAY_HEADER_BYTES * 2)
        self.assertEqual(len(bytes.fromhex(spaced)), RELAY_HEADER_BYTES - 1)
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(dict(BASE_CONFIG, routingPrefixHex=spaced))
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(dict(BASE_CONFIG, expectedReturnPrefixHex=spaced))
        with self.assertRaises(ProbeConfigError):
            parse_probe_config(
                dict(BASE_CONFIG, certificateHashes=["  " + "ab" * 32][:1])
            )

    def test_uppercase_hex_is_accepted_for_configuration(self) -> None:
        config = make_config(routingPrefixHex=SYNTHETIC_PREFIX.hex().upper())
        self.assertEqual(config.routing_prefix, SYNTHETIC_PREFIX)

    def test_the_authorization_stays_out_of_the_generated_repr(self) -> None:
        self.assertNotIn("one-time-value", repr(make_config()))


class ReportDigestStrictnessTests(unittest.TestCase):
    def test_only_lowercase_unspaced_sha256_is_accepted(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        driver, _ = run_plan(plan)
        record = driver.session_record()
        validate_report(build_report([record], "ab" * 32), plan)
        for digest in ("AB" * 32, " " + "ab" * 32, "ab" * 32 + " ", "ab" * 31 + "g "):
            with self.assertRaises(MeasurementReportError, msg=digest):
                validate_report(build_report([record], digest), plan)


class SessionTests(unittest.TestCase):
    def test_committed_plan_completes_over_a_large_transport(self) -> None:
        plan = MeasurementPlan.from_vector(COMMITTED_VECTOR)
        driver, relay = run_plan(plan)
        record = driver.session_record()
        self.assertEqual(set(outcomes(record)), {OUTCOME_ECHOED})
        self.assertEqual(relay.received_datagrams, plan.datagram_count)
        self.assertEqual(record["unmatchedFrames"], 0)
        self.assertEqual(record["foreignFrames"], 0)
        self.assertEqual(record["malformedFrames"], 0)
        validate_report(build_report([record], "0" * 64), plan)

    def test_packed_frame_is_answered_by_one_frame_per_datagram(self) -> None:
        plan = MeasurementPlan.from_vector(
            make_vector([16, 17, 18], packed=((16, 17, 18),))
        )
        driver, _ = run_plan(plan)
        record = driver.session_record()
        packed = [case for case in record["cases"] if case["kind"] == CASE_PACKED][0]
        self.assertEqual(packed["outcome"], OUTCOME_ECHOED)
        self.assertEqual(len(packed["receivedFrames"]), 3)
        self.assertEqual(
            sorted(entry["innerBytes"] for entry in packed["receivedFrames"]),
            [16, 17, 18],
        )
        for entry in packed["receivedFrames"]:
            self.assertEqual(
                entry["frameBytes"],
                entry["innerBytes"] + SINGLE_DATAGRAM_OVERHEAD_BYTES,
            )

    def test_a_frame_above_the_transport_maximum_is_refused_not_attempted(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18, 64, 65]))
        limit = frame_bytes_for_sizes((64,))
        relay = LoopbackRelay(max_datagram_size_bytes=limit)
        driver, relay = run_plan(plan, relay=relay)
        record = driver.session_record()
        by_size = {
            case["sentInnerBytes"][0]: case
            for case in record["cases"]
            if case["kind"] == CASE_SINGLE
        }
        self.assertEqual(by_size[64]["outcome"], OUTCOME_ECHOED)
        self.assertEqual(by_size[64]["sentFrameBytes"], limit)
        self.assertEqual(by_size[65]["outcome"], OUTCOME_NOT_SENT)
        self.assertEqual(by_size[65]["receivedFrames"], [])
        self.assertEqual(relay.received_datagrams, 4 + 2)

    def test_empty_datagram_is_measured_rather_than_refused(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([0, 16, 17, 18]))
        driver, _ = run_plan(plan)
        record = driver.session_record()
        empty = [case for case in record["cases"] if case["sentInnerBytes"] == [0]][0]
        self.assertEqual(empty["outcome"], OUTCOME_ECHOED)
        self.assertEqual(empty["sentFrameBytes"], SINGLE_DATAGRAM_OVERHEAD_BYTES)

    def test_untagged_cases_run_one_at_a_time(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([0, 1, 16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, echo=False)
        adapter = relay.attach()
        driver = SessionDriver(plan, adapter, NONCE, make_config())
        driver.pump(0)
        self.assertEqual(relay.received_datagrams, 1)
        driver.pump(1999)
        self.assertEqual(relay.received_datagrams, 1)
        driver.pump(2000)
        self.assertEqual(relay.received_datagrams, 2)

    def test_outstanding_tagged_datagrams_are_bounded(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector(range(16, 30)))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, echo=False)
        adapter = relay.attach()
        driver = SessionDriver(
            plan, adapter, NONCE, make_config(maxInFlightDatagrams=4)
        )
        driver.pump(0)
        self.assertEqual(relay.received_datagrams, 4)
        driver.pump(1)
        self.assertEqual(relay.received_datagrams, 4)

    def test_a_silent_destination_times_the_case_out(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, drop_inner_sizes=(17,))
        driver, _ = run_plan(plan, relay=relay)
        record = driver.session_record()
        dropped = [case for case in record["cases"] if case["sentInnerBytes"] == [17]][
            0
        ]
        self.assertEqual(dropped["outcome"], OUTCOME_TIMED_OUT)
        self.assertIsNone(dropped["roundTripMilliseconds"])

    def test_a_refused_transport_write_is_recorded(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, refuse_send=True)
        driver, _ = run_plan(plan, relay=relay)
        self.assertEqual(set(outcomes(driver.session_record())), {OUTCOME_SEND_FAILED})

    def test_a_corrupted_echo_is_a_payload_mismatch(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(
            max_datagram_size_bytes=20000, fault=FAULT_CORRUPT_PAYLOAD
        )
        driver, _ = run_plan(plan, relay=relay)
        record = driver.session_record()
        by_size = {
            case["sentInnerBytes"][0]: case
            for case in record["cases"]
            if case["kind"] == CASE_SINGLE
        }
        # 17 and 18 keep an intact tag, so the corruption is attributable.
        self.assertEqual(by_size[17]["outcome"], OUTCOME_PAYLOAD_MISMATCH)
        self.assertEqual(by_size[18]["outcome"], OUTCOME_PAYLOAD_MISMATCH)
        # A 16-byte payload is nothing but the tag, so corrupting its last byte
        # corrupts the ordinal. That frame belongs to no outstanding datagram and
        # must not be allowed to complete one.
        self.assertEqual(by_size[16]["outcome"], OUTCOME_TIMED_OUT)
        self.assertGreater(record["unmatchedFrames"], 0)

    def test_malformed_return_frames_complete_nothing(self) -> None:
        for fault in (
            FAULT_TRUNCATED_RETURN,
            FAULT_PACKED_RETURN,
            FAULT_HEADER_ONLY_RETURN,
            FAULT_DECLARED_OVERSIZE,
        ):
            plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
            relay = LoopbackRelay(max_datagram_size_bytes=20000, fault=fault)
            driver, _ = run_plan(plan, relay=relay)
            record = driver.session_record()
            self.assertEqual(
                set(outcomes(record)), {OUTCOME_TIMED_OUT}, f"fault {fault}"
            )
            self.assertGreater(record["malformedFrames"], 0, f"fault {fault}")

    def test_an_unexpected_return_prefix_is_refused(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, fault=FAULT_FOREIGN_PREFIX)
        config = make_config(expectedReturnPrefixHex=SYNTHETIC_RETURN_PREFIX.hex())
        driver, _ = run_plan(plan, relay=relay, config=config)
        record = driver.session_record()
        self.assertEqual(set(outcomes(record)), {OUTCOME_TIMED_OUT})
        self.assertGreater(record["prefixMismatchFrames"], 0)

    def test_the_first_return_prefix_is_pinned_for_the_session(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000)
        adapter = relay.attach()
        driver = SessionDriver(plan, adapter, NONCE, make_config())
        driver.pump(0)
        first = adapter.drain()[0]
        driver.receive(first, 1)
        drifted = bytes(RELAY_HEADER_BYTES) + first[RELAY_HEADER_BYTES:]
        driver.receive(drifted, 2)
        self.assertEqual(driver.prefix_mismatch_frames, 1)

    def test_two_sessions_receive_only_their_own_tagged_traffic(self) -> None:
        """Isolation holds for nonce-tagged cases, which is the whole claim.

        The plan deliberately includes the committed vector's untagged 0- and
        1-byte sizes, because they are the interesting part: an untagged payload
        carries no session nonce, so two sessions running one concurrently
        cannot tell their echoes apart. That is why the vector runs those cases
        sequentially and why they are not isolation evidence. This test asserts
        isolation for the tagged cases and only that every case still reaches an
        outcome for the untagged ones.
        """
        relay = LoopbackRelay(max_datagram_size_bytes=20000, crosstalk=True)
        pairs = []
        plans = []
        for index, nonce in enumerate((NONCE, OTHER_NONCE)):
            adapter = relay.attach()
            plan = MeasurementPlan.from_vector(make_vector([0, 1, 16, 17, 18]))
            plans.append(plan)
            pairs.append(
                (
                    SessionDriver(
                        plan, adapter, nonce, make_config(), session_index=index
                    ),
                    adapter,
                )
            )
        run_sessions(pairs)
        for (driver, _), plan in zip(pairs, plans):
            record = driver.session_record()
            tagged = {case.index for case in plan.cases if case.tagged}
            for case in record["cases"]:
                if case["caseIndex"] in tagged:
                    self.assertEqual(case["outcome"], OUTCOME_ECHOED)
                else:
                    self.assertIn(case["outcome"], OUTCOMES)
            self.assertGreater(record["foreignFrames"], 0)
            self.assertEqual(record["malformedFrames"], 0)

    def test_a_late_untagged_echo_is_not_attributed_to_the_next_case(self) -> None:
        # Case 0 sends 0 bytes and times out; its echo arrives while the 1-byte
        # case is outstanding. Length is the only thing separating them, so the
        # late frame must be unattributable rather than a defect reported
        # against a case that did nothing wrong.
        plan = MeasurementPlan.from_vector(make_vector([0, 1, 16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, echo=False)
        adapter = relay.attach()
        driver = SessionDriver(plan, adapter, NONCE, make_config())
        driver.pump(0)
        adapter.drain()
        driver.pump(2000)
        self.assertEqual(relay.received_datagrams, 2)
        adapter.drain()
        late = encode_frame(SYNTHETIC_RETURN_PREFIX, (b"",), SERVER_TO_BROWSER)
        driver.receive(late, 2001)
        self.assertEqual(driver.unmatched_frames, 1)
        record_before = {
            case["caseIndex"]: case["outcome"]
            for case in driver.session_record()["cases"]
        }
        self.assertEqual(record_before[0], OUTCOME_TIMED_OUT)
        self.assertNotEqual(record_before[1], OUTCOME_PAYLOAD_MISMATCH)
        # The correctly sized echo still completes its own case.
        driver.receive(
            encode_frame(SYNTHETIC_RETURN_PREFIX, (b"\x00",), SERVER_TO_BROWSER),
            2002,
        )
        self.assertEqual(driver.session_record()["cases"][1]["outcome"], OUTCOME_ECHOED)
        self.assertEqual(driver.unmatched_frames, 1)

    def test_a_case_wider_than_the_bound_cannot_be_driven(self) -> None:
        plan = MeasurementPlan.from_vector(
            make_vector([16, 17, 18], packed=((16, 17, 18),)),
            max_in_flight_datagrams=3,
        )
        adapter = LoopbackRelay().attach()
        with self.assertRaises(RelayProbeError):
            SessionDriver(plan, adapter, NONCE, make_config(maxInFlightDatagrams=2))

    def test_a_case_never_reached_is_not_reported_as_a_timeout(self) -> None:
        # A case that was never sent is an absence of evidence. Recording it as
        # a timeout would fold it into the accepted range WP6 reads.
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, echo=False)
        adapter = relay.attach()
        driver = SessionDriver(
            plan, adapter, NONCE, make_config(maxInFlightDatagrams=2)
        )
        driver.pump(0)
        outcomes_by_index = {
            case["caseIndex"]: case["outcome"]
            for case in driver.session_record()["cases"]
        }
        # Two cases went out and are waiting; the rest were never reached.
        self.assertEqual(
            [outcomes_by_index[index] for index in (0, 1)],
            [OUTCOME_TIMED_OUT, OUTCOME_TIMED_OUT],
        )
        self.assertEqual(
            [outcomes_by_index[index] for index in (2, 3)],
            [OUTCOME_NOT_RUN, OUTCOME_NOT_RUN],
        )

    def test_session_nonce_length_is_enforced(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        adapter = LoopbackRelay().attach()
        with self.assertRaises(RelayProbeError):
            SessionDriver(plan, adapter, b"short", make_config())


class ReportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = MeasurementPlan.from_vector(make_vector([0, 16, 17, 18]))
        driver, _ = run_plan(self.plan)
        self.report = build_report(
            [driver.session_record()], "ab" * 32, path_notes="loopback"
        )

    def valid(self):
        return copy.deepcopy(self.report)

    def assert_rejected(self, report) -> None:
        with self.assertRaises(MeasurementReportError):
            validate_report(report, self.plan)

    def test_a_real_run_validates(self) -> None:
        validate_report(self.valid(), self.plan)
        validate_report(self.valid())

    def test_unknown_and_missing_fields_are_rejected(self) -> None:
        report = self.valid()
        report["extra"] = 1
        self.assert_rejected(report)
        report = self.valid()
        del report["framing"]
        self.assert_rejected(report)
        report = self.valid()
        report["sessions"][0]["extra"] = 1
        self.assert_rejected(report)
        report = self.valid()
        del report["sessions"][0]["cases"][0]["ordinals"]
        self.assert_rejected(report)

    def test_kind_version_and_framing_are_fixed(self) -> None:
        for field, value in (
            ("kind", "something-else"),
            ("formatVersion", 2),
            ("framing", {"relayHeaderBytes": 40}),
        ):
            report = self.valid()
            report[field] = value
            self.assert_rejected(report)

    def test_vector_digest_must_be_a_sha256(self) -> None:
        for digest in ("ab" * 31, "zz" * 32, 7):
            report = self.valid()
            report["measurementVectorSha256"] = digest
            self.assert_rejected(report)

    def test_sent_frame_size_must_agree_with_the_framing(self) -> None:
        report = self.valid()
        report["sessions"][0]["cases"][0]["sentFrameBytes"] += 1
        self.assert_rejected(report)

    def test_returned_frame_must_carry_the_42_byte_overhead(self) -> None:
        report = self.valid()
        case = next(
            case for case in report["sessions"][0]["cases"] if case["receivedFrames"]
        )
        case["receivedFrames"][0]["frameBytes"] += 1
        self.assert_rejected(report)

    def test_round_trip_times_are_bounded_and_only_on_echoed_cases(self) -> None:
        for value in (-1, float("nan"), float("inf"), None, "12"):
            report = self.valid()
            report["sessions"][0]["cases"][0]["roundTripMilliseconds"] = value
            self.assert_rejected(report)
        report = self.valid()
        case = report["sessions"][0]["cases"][0]
        case["outcome"] = OUTCOME_TIMED_OUT
        case["receivedFrames"] = []
        self.assert_rejected(report)

    def test_an_echoed_case_must_return_what_it_sent(self) -> None:
        report = self.valid()
        case = report["sessions"][0]["cases"][1]
        case["receivedFrames"] = []
        self.assert_rejected(report)
        report = self.valid()
        case = report["sessions"][0]["cases"][1]
        case["receivedFrames"][0]["innerBytes"] += 1
        case["receivedFrames"][0]["frameBytes"] += 1
        self.assert_rejected(report)

    def test_a_returned_size_the_case_never_sent_is_rejected(self) -> None:
        # Not only for echoed cases: a returned frame is recorded after a
        # byte-exact match, so a timed-out case cannot have seen a foreign size.
        report = self.valid()
        case = next(
            item
            for item in report["sessions"][0]["cases"]
            if item["sentInnerBytes"] == [16]
        )
        case["outcome"] = OUTCOME_TIMED_OUT
        case["roundTripMilliseconds"] = None
        case["receivedFrames"] = [
            {"frameBytes": 99 + SINGLE_DATAGRAM_OVERHEAD_BYTES, "innerBytes": 99}
        ]
        self.assert_rejected(report)

    def test_a_returned_size_cannot_be_counted_twice(self) -> None:
        report = self.valid()
        packed = next(
            item
            for item in report["sessions"][0]["cases"]
            if item["kind"] == CASE_PACKED
        )
        packed["outcome"] = OUTCOME_TIMED_OUT
        packed["roundTripMilliseconds"] = None
        packed["receivedFrames"] = [
            {"frameBytes": 16 + SINGLE_DATAGRAM_OVERHEAD_BYTES, "innerBytes": 16}
        ] * 2
        self.assert_rejected(report)

    def test_more_frames_than_datagrams_is_rejected(self) -> None:
        report = self.valid()
        case = report["sessions"][0]["cases"][1]
        case["receivedFrames"] = case["receivedFrames"] * 2
        self.assert_rejected(report)

    def test_sizes_outside_the_plan_ceiling_are_rejected(self) -> None:
        report = self.valid()
        case = report["sessions"][0]["cases"][0]
        case["sentInnerBytes"] = [self.plan.max_inner_datagram_bytes + 1]
        case["sentFrameBytes"] = frame_bytes_for_sizes(case["sentInnerBytes"])
        self.assert_rejected(report)

    def test_case_kind_must_match_the_datagram_count(self) -> None:
        report = self.valid()
        case = report["sessions"][0]["cases"][0]
        case["kind"] = CASE_PACKED
        self.assert_rejected(report)
        report = self.valid()
        packed = next(
            case
            for case in report["sessions"][0]["cases"]
            if case["kind"] == CASE_PACKED
        )
        packed["kind"] = CASE_SINGLE
        self.assert_rejected(report)

    def test_ordinals_are_unique_within_a_session(self) -> None:
        report = self.valid()
        report["sessions"][0]["cases"][1]["ordinals"] = report["sessions"][0]["cases"][
            0
        ]["ordinals"]
        self.assert_rejected(report)

    def test_indices_must_ascend(self) -> None:
        report = self.valid()
        report["sessions"][0]["cases"][1]["caseIndex"] = 0
        self.assert_rejected(report)
        report = self.valid()
        report["sessions"].append(copy.deepcopy(report["sessions"][0]))
        self.assert_rejected(report)

    def test_a_refused_case_must_actually_exceed_the_transport_maximum(self) -> None:
        report = self.valid()
        case = report["sessions"][0]["cases"][0]
        case["outcome"] = OUTCOME_NOT_SENT
        case["receivedFrames"] = []
        case["roundTripMilliseconds"] = None
        self.assert_rejected(report)

    def test_an_echoed_case_cannot_exceed_the_reported_datagram_maximum(self) -> None:
        report = self.valid()
        session = report["sessions"][0]
        session["maxDatagramSizeBytes"] = 43
        self.assert_rejected(report)

    def test_a_failed_send_cannot_have_returned_frames(self) -> None:
        report = self.valid()
        case = next(
            item for item in report["sessions"][0]["cases"] if item["receivedFrames"]
        )
        case["outcome"] = OUTCOME_SEND_FAILED
        case["roundTripMilliseconds"] = None
        self.assert_rejected(report)

    def test_a_case_that_never_ran_cannot_have_returned_frames(self) -> None:
        report = self.valid()
        case = next(
            item for item in report["sessions"][0]["cases"] if item["receivedFrames"]
        )
        case["outcome"] = OUTCOME_NOT_RUN
        case["roundTripMilliseconds"] = None
        self.assert_rejected(report)

    def test_write_failures_are_reported_and_bounded(self) -> None:
        report = self.valid()
        self.assertEqual(report["sessions"][0]["writeFailures"], 0)
        report["sessions"][0]["writeFailures"] = -1
        self.assert_rejected(report)
        report = self.valid()
        del report["sessions"][0]["writeFailures"]
        self.assert_rejected(report)

    def test_a_case_wider_than_the_reported_bound_is_rejected(self) -> None:
        report = self.valid()
        report["sessions"][0]["maxInFlightDatagrams"] = 1
        self.assert_rejected(report)

    def test_unknown_outcomes_and_negative_counters_are_rejected(self) -> None:
        report = self.valid()
        report["sessions"][0]["cases"][0]["outcome"] = "maybe"
        self.assert_rejected(report)
        report = self.valid()
        report["sessions"][0]["foreignFrames"] = -1
        self.assert_rejected(report)
        report = self.valid()
        report["sessions"][0]["maxDatagramSizeBytes"] = 0
        self.assert_rejected(report)

    def test_a_report_must_describe_at_least_one_session_and_case(self) -> None:
        report = self.valid()
        report["sessions"] = []
        self.assert_rejected(report)
        report = self.valid()
        report["sessions"][0]["cases"] = []
        self.assert_rejected(report)

    def test_a_report_must_match_the_plan_it_claims(self) -> None:
        other = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        with self.assertRaises(MeasurementReportError):
            validate_report(self.valid(), other)


class MergeTests(unittest.TestCase):
    """Two concurrent sessions come from two browser contexts, each numbering
    its own sessions from zero, so merging has to renumber them."""

    def setUp(self) -> None:
        self.plan = MeasurementPlan.from_vector(make_vector([16, 17, 18]))
        self.reports = []
        for nonce in (NONCE, OTHER_NONCE):
            driver, _ = run_plan(self.plan, nonce=nonce)
            self.reports.append(build_report([driver.session_record()], "ab" * 32))

    def test_two_single_session_reports_merge_into_one_valid_report(self) -> None:
        merged = merge_reports(self.reports)
        self.assertEqual(
            [session["sessionIndex"] for session in merged["sessions"]], [0, 1]
        )
        validate_report(merged, self.plan)
        self.assertEqual(len(summarize_report(merged, self.plan)["sessions"]), 2)

    def test_merging_leaves_the_inputs_untouched(self) -> None:
        merge_reports(self.reports)
        for report in self.reports:
            self.assertEqual(report["sessions"][0]["sessionIndex"], 0)

    def test_the_merged_report_is_checked_against_the_plan(self) -> None:
        broken = copy.deepcopy(self.reports[1])
        broken["sessions"][0]["cases"].pop()
        with self.assertRaises(MeasurementReportError):
            merge_reports([self.reports[0], broken], self.plan)

    def test_differing_path_notes_are_refused_rather_than_dropped(self) -> None:
        first = copy.deepcopy(self.reports[0])
        second = copy.deepcopy(self.reports[1])
        first["pathNotes"] = "wired"
        second["pathNotes"] = "wireless"
        with self.assertRaises(MeasurementReportError):
            merge_reports([first, second], self.plan)
        merged = merge_reports([first, second], self.plan, path_notes="two paths")
        self.assertEqual(merged["pathNotes"], "two paths")
        # One side carrying notes and the other silent is not a disagreement.
        second["pathNotes"] = ""
        self.assertEqual(
            merge_reports([first, second], self.plan)["pathNotes"], "wired"
        )

    def test_reports_naming_different_vectors_do_not_merge(self) -> None:
        other = copy.deepcopy(self.reports[1])
        other["measurementVectorSha256"] = "cd" * 32
        with self.assertRaises(MeasurementReportError):
            merge_reports([self.reports[0], other])

    def test_merging_nothing_is_refused(self) -> None:
        with self.assertRaises(MeasurementReportError):
            merge_reports([])


class SummaryTests(unittest.TestCase):
    def build(self, plan, relay):
        driver, _ = run_plan(plan, relay=relay)
        return build_report([driver.session_record()], "ab" * 32)

    def test_summary_reports_per_session_ranges(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18, 64, 65]))
        relay = LoopbackRelay(max_datagram_size_bytes=frame_bytes_for_sizes((64,)))
        summary = summarize_report(self.build(plan, relay), plan)
        session = summary["sessions"][0]
        self.assertEqual(session["largestEchoedInnerBytes"], 64)
        self.assertEqual(session["smallestFailedInnerBytes"], 65)
        self.assertEqual(session["contiguousInnerBytes"], 64)
        self.assertTrue(session["monotonic"])
        self.assertEqual(summary["conservativeInnerFloorBytes"], 64)

    def test_a_gap_stops_the_contiguous_range_and_is_flagged(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18, 64]))
        relay = LoopbackRelay(max_datagram_size_bytes=20000, drop_inner_sizes=(17,))
        summary = summarize_report(self.build(plan, relay), plan)
        session = summary["sessions"][0]
        self.assertEqual(session["contiguousInnerBytes"], 16)
        self.assertEqual(session["largestEchoedInnerBytes"], 64)
        self.assertFalse(session["monotonic"])

    def test_a_case_that_never_ran_is_a_gap_not_an_acceptance(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18, 64]))
        report = self.build(plan, LoopbackRelay(max_datagram_size_bytes=20000))
        case = next(
            item
            for item in report["sessions"][0]["cases"]
            if item["sentInnerBytes"] == [17]
        )
        case["outcome"] = OUTCOME_NOT_RUN
        case["receivedFrames"] = []
        case["roundTripMilliseconds"] = None
        summary = summarize_report(report, plan)
        session = summary["sessions"][0]
        self.assertEqual(session["notRunSingleCases"], 1)
        # 17 is unknown, so the contiguous range stops at 16 even though 18 and
        # 64 echoed, and the unknown size is not counted as a failure.
        self.assertEqual(session["contiguousInnerBytes"], 16)
        self.assertEqual(session["failedSingleCases"], 0)
        self.assertEqual(summary["conservativeInnerFloorBytes"], 16)

    def test_untagged_sizes_cannot_lift_the_floor_of_a_multi_session_report(
        self,
    ) -> None:
        """Two concurrent sessions send identical untagged payloads, so an
        untagged case can be completed by the other session's echo. Such a size
        is not isolation-grade and must not raise the floor a JSON-only consumer
        reads."""
        plan = MeasurementPlan.from_vector(make_vector([0, 1, 16, 17, 18]))
        single = self.build(plan, LoopbackRelay(max_datagram_size_bytes=20000))
        lone = summarize_report(single, plan)["sessions"][0]
        self.assertFalse(lone["contiguousExcludesUntagged"])
        self.assertEqual(lone["untaggedSingleSizes"], [0, 1])
        self.assertEqual(lone["contiguousInnerBytes"], 18)

        # The same run, but with the 16-byte case failing, in a two-session
        # report: the walk must start above the untagged sizes and stop at once.
        second = copy.deepcopy(single["sessions"][0])
        second["sessionIndex"] = 1
        merged = build_report(
            [single["sessions"][0], second], single["measurementVectorSha256"]
        )
        for session in merged["sessions"]:
            case = next(
                item for item in session["cases"] if item["sentInnerBytes"] == [16]
            )
            case["outcome"] = OUTCOME_TIMED_OUT
            case["receivedFrames"] = []
            case["roundTripMilliseconds"] = None
        summary = summarize_report(merged, plan)
        for session in summary["sessions"]:
            self.assertTrue(session["contiguousExcludesUntagged"])
            self.assertEqual(session["untaggedSingleSizes"], [0, 1])
            # 0 and 1 echoed but are excluded, and 16 failed, so nothing is
            # contiguously accepted rather than "up to 1 byte".
            self.assertIsNone(session["contiguousInnerBytes"])
        self.assertIsNone(summary["conservativeInnerFloorBytes"])

    def test_the_floor_is_the_minimum_across_the_listed_sessions(self) -> None:
        plan = MeasurementPlan.from_vector(make_vector([16, 17, 18, 64, 65]))
        wide = self.build(plan, LoopbackRelay(max_datagram_size_bytes=20000))
        narrow = self.build(
            plan, LoopbackRelay(max_datagram_size_bytes=frame_bytes_for_sizes((64,)))
        )
        narrow["sessions"][0]["sessionIndex"] = 1
        combined = build_report(
            wide["sessions"] + narrow["sessions"], wide["measurementVectorSha256"]
        )
        summary = summarize_report(combined, plan)
        self.assertEqual(
            [entry["contiguousInnerBytes"] for entry in summary["sessions"]], [65, 64]
        )
        self.assertEqual(summary["conservativeInnerFloorBytes"], 64)


class ConformanceVectorTests(unittest.TestCase):
    def test_the_committed_file_matches_the_contract_module(self) -> None:
        self.assertEqual(COMMITTED_CONFORMANCE, build_conformance_vectors())

    def test_the_vectors_only_use_synthetic_routing_prefixes(self) -> None:
        allowed = {
            COMMITTED_CONFORMANCE["syntheticPrefixHex"],
            COMMITTED_CONFORMANCE["syntheticReturnPrefixHex"],
        }
        self.assertEqual(
            allowed, {SYNTHETIC_PREFIX.hex(), SYNTHETIC_RETURN_PREFIX.hex()}
        )
        for case in COMMITTED_CONFORMANCE["encodeCases"]:
            self.assertIn(case["prefixHex"], allowed)
            self.assertTrue(case["frameHex"].startswith(case["prefixHex"]))

    def test_every_encode_case_reproduces_and_decodes(self) -> None:
        for case in COMMITTED_CONFORMANCE["encodeCases"]:
            payloads = [bytes.fromhex(value) for value in case["payloadHexes"]]
            frame = encode_frame(
                bytes.fromhex(case["prefixHex"]), payloads, case["direction"]
            )
            self.assertEqual(frame.hex(), case["frameHex"], case["name"])
            self.assertEqual(len(frame), case["frameBytes"], case["name"])
            decoded = decode_frame(frame, case["direction"])
            self.assertEqual(list(decoded.datagrams), payloads, case["name"])

    def test_the_committed_file_is_byte_identical_to_the_emitter(self) -> None:
        # The structural comparison above would pass on a reformatted file, and
        # the emitter's --check is a separate command. This pins the bytes.
        self.assertEqual(
            (ROOT / "probe" / "conformance-vectors.json").read_text(encoding="utf-8"),
            encode_conformance_vectors(),
        )

    def test_every_acceptance_case_decodes_at_its_ceiling(self) -> None:
        # Without these an implementation could use `length >= ceiling` and pass
        # every rejection vector while refusing the plan's largest size.
        self.assertTrue(COMMITTED_CONFORMANCE["decodeAcceptances"])
        for case in COMMITTED_CONFORMANCE["decodeAcceptances"]:
            decoded = decode_frame(
                bytes.fromhex(case["frameHex"]),
                case["direction"],
                case["maxInnerDatagramBytes"],
            )
            self.assertEqual(
                [datagram.hex() for datagram in decoded.datagrams],
                case["payloadHexes"],
                case["name"],
            )
            self.assertEqual(
                max(len(datagram) for datagram in decoded.datagrams),
                case["maxInnerDatagramBytes"],
                case["name"],
            )

    def test_every_decode_rejection_is_rejected(self) -> None:
        for case in COMMITTED_CONFORMANCE["decodeRejections"]:
            with self.assertRaises(RelayFrameError, msg=case["name"]):
                decode_frame(
                    bytes.fromhex(case["frameHex"]),
                    case["direction"],
                    case["maxInnerDatagramBytes"],
                )

    def test_every_encode_rejection_is_rejected(self) -> None:
        for case in COMMITTED_CONFORMANCE["encodeRejections"]:
            with self.assertRaises(RelayFrameError, msg=case["name"]):
                encode_frame(
                    bytes.fromhex(case["prefixHex"]),
                    [bytes.fromhex(value) for value in case["payloadHexes"]],
                    case["direction"],
                    case["maxInnerDatagramBytes"],
                )

    def test_tag_and_payload_cases_match_the_derivations(self) -> None:
        for case in COMMITTED_CONFORMANCE["tagCases"]:
            self.assertEqual(
                datagram_tag(
                    bytes.fromhex(case["sessionNonceHex"]), case["ordinal"]
                ).hex(),
                case["tagHex"],
                case["name"],
            )
        for case in COMMITTED_CONFORMANCE["payloadCases"]:
            payload = build_payload(
                bytes.fromhex(case["sessionNonceHex"]), case["ordinal"], case["size"]
            )
            self.assertEqual(payload.hex(), case["payloadHex"], case["name"])
            self.assertEqual(len(payload), case["size"], case["name"])


NODE = shutil.which("node")
HARNESS = ROOT / "tests" / "js_conformance_harness.mjs"


@unittest.skipUnless(NODE, "node is not available to run the browser sources")
class BrowserImplementationTests(unittest.TestCase):
    """The browser probe is a second implementation of the same contract.

    It is not generated from `relay_probe.py` and does not read it. Running its
    sources here is what turns "an independent implementation could satisfy the
    same conformance tests" into something the suite actually checks.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = MeasurementPlan.from_vector(COMMITTED_VECTOR)
        cls.limits = (
            frame_bytes_for_sizes((cls.plan.max_inner_datagram_bytes,)),
            1200,
        )
        completed = subprocess.run(
            [NODE, str(HARNESS), str(ROOT / "probe"), str(ROOT)],
            capture_output=True,
            check=False,
            text=True,
            env={
                **os.environ,
                "HARNESS_AUTHORIZATIONS": json.dumps(list(DOLLAR_AUTHORIZATIONS)),
                "HARNESS_LIMITS": json.dumps(list(cls.limits)),
                "HARNESS_MAX_IN_FLIGHT": str(DEFAULT_MAX_IN_FLIGHT_DATAGRAMS),
            },
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"the browser implementation failed: {completed.stderr.strip()}"
            )
        cls.observed = json.loads(completed.stdout)

    def python_record(self, limit):
        relay = LoopbackRelay(max_datagram_size_bytes=limit)
        adapter = relay.attach()
        driver = SessionDriver(
            self.plan, adapter, bytes(SESSION_NONCE_BYTES), make_config()
        )
        run_session(driver, adapter)
        return driver.session_record()

    def test_browser_sources_pass_every_committed_conformance_vector(self) -> None:
        expected = sum(
            len(COMMITTED_CONFORMANCE[name])
            for name in (
                "decodeAcceptances",
                "decodeRejections",
                "encodeCases",
                "encodeRejections",
                "payloadCases",
                "tagCases",
            )
        )
        self.assertEqual(self.observed["conformanceChecked"], expected)

    def test_browser_builds_the_same_plan(self) -> None:
        self.assertEqual(self.observed["planCases"], len(self.plan.cases))
        self.assertEqual(self.observed["planDatagrams"], self.plan.datagram_count)
        self.assertEqual(
            self.observed["maxInnerDatagramBytes"], self.plan.max_inner_datagram_bytes
        )

    def test_browser_and_reference_sessions_agree_exactly(self) -> None:
        for limit in self.limits:
            observed = self.observed["records"][str(limit)]
            self.assertEqual(
                observed, self.python_record(limit), f"transport limit {limit}"
            )

    def test_browser_substitutes_a_dollar_pattern_authorization_literally(
        self,
    ) -> None:
        for value in DOLLAR_AUTHORIZATIONS:
            expected = parse_probe_config(
                {
                    "authorization": value,
                    "destinationPortMatchesProjection": True,
                    "endpointTemplate": "https://harness.invalid/p?a={authorization}&b=x",
                    "routingPrefixHex": SYNTHETIC_PREFIX.hex(),
                }
            ).endpoint_url()
            self.assertEqual(self.observed["endpointUrls"][value], expected, value)
            self.assertIn(value, expected)

    def test_browser_refuses_a_late_untagged_echo_like_the_reference(self) -> None:
        relay = LoopbackRelay(max_datagram_size_bytes=20000)
        adapter = relay.attach()
        driver = SessionDriver(
            self.plan, adapter, bytes(SESSION_NONCE_BYTES), make_config()
        )
        driver.pump(0)
        adapter.drain()
        driver.pump(2000)
        adapter.drain()
        driver.receive(
            encode_frame(SYNTHETIC_RETURN_PREFIX, (b"",), SERVER_TO_BROWSER), 2001
        )
        expected = {
            "unmatchedFrames": driver.unmatched_frames,
            "outcomes": [
                case["outcome"] for case in driver.session_record()["cases"][:2]
            ],
        }
        self.assertEqual(self.observed["lateUntaggedEcho"], expected)
        self.assertEqual(expected["unmatchedFrames"], 1)
        self.assertNotIn(OUTCOME_PAYLOAD_MISMATCH, expected["outcomes"])

    FAULT_PLAN_SIZES = [16, 17, 18]

    def fault_plan(self):
        return MeasurementPlan.from_vector(
            make_vector(self.FAULT_PLAN_SIZES),
            max_in_flight_datagrams=DEFAULT_MAX_IN_FLIGHT_DATAGRAMS,
        )

    def reference_fault_run(self, relay, config=None):
        plan = self.fault_plan()
        adapter = relay.attach()
        driver = SessionDriver(
            plan, adapter, bytes(SESSION_NONCE_BYTES), config or make_config()
        )
        run_session(driver, adapter)
        record = driver.session_record()
        return {
            "foreignFrames": record["foreignFrames"],
            "malformedFrames": record["malformedFrames"],
            "outcomes": [item["outcome"] for item in record["cases"]],
            "prefixMismatchFrames": record["prefixMismatchFrames"],
            "unmatchedFrames": record["unmatchedFrames"],
            "writeFailures": record["writeFailures"],
        }

    def test_browser_accounting_matches_the_reference_under_every_fault(self) -> None:
        """The browser is what takes the routed measurement, and its counters
        are the concurrent-session evidence. Every rejection path it owns is
        driven here and compared with the reference implementation."""
        cases = {
            "clean": LoopbackRelay(max_datagram_size_bytes=20000),
            "truncatedReturn": LoopbackRelay(
                max_datagram_size_bytes=20000, fault=FAULT_TRUNCATED_RETURN
            ),
            "packedReturn": LoopbackRelay(
                max_datagram_size_bytes=20000, fault=FAULT_PACKED_RETURN
            ),
            "headerOnlyReturn": LoopbackRelay(
                max_datagram_size_bytes=20000, fault=FAULT_HEADER_ONLY_RETURN
            ),
            "declaredOversize": LoopbackRelay(
                max_datagram_size_bytes=20000, fault=FAULT_DECLARED_OVERSIZE
            ),
            "corruptPayload": LoopbackRelay(
                max_datagram_size_bytes=20000, fault=FAULT_CORRUPT_PAYLOAD
            ),
            "dropped": LoopbackRelay(
                max_datagram_size_bytes=20000, drop_inner_sizes=(17,)
            ),
            "refused": LoopbackRelay(max_datagram_size_bytes=20000, refuse_send=True),
        }
        for name, relay in cases.items():
            self.assertEqual(
                self.observed["faultRuns"][name],
                self.reference_fault_run(relay),
                f"fault {name}",
            )
        self.assertEqual(
            self.observed["faultRuns"]["foreignPrefix"],
            self.reference_fault_run(
                LoopbackRelay(
                    max_datagram_size_bytes=20000, fault=FAULT_FOREIGN_PREFIX
                ),
                make_config(expectedReturnPrefixHex=SYNTHETIC_RETURN_PREFIX.hex()),
            ),
        )
        # Every counter the browser owns is actually reached by this set.
        reached = set()
        for run in self.observed["faultRuns"].values():
            reached.update(name for name, value in run.items() if value)
            reached.update(run["outcomes"])
        for name in (
            "foreignFrames",
            "malformedFrames",
            "prefixMismatchFrames",
            "unmatchedFrames",
            "writeFailures",
            OUTCOME_ECHOED,
            OUTCOME_PAYLOAD_MISMATCH,
            OUTCOME_SEND_FAILED,
            OUTCOME_TIMED_OUT,
        ):
            self.assertIn(name, reached)

    def test_browser_counts_a_foreign_nonce_like_the_reference(self) -> None:
        plan = self.fault_plan()
        relay = LoopbackRelay(max_datagram_size_bytes=20000, echo=False)
        adapter = relay.attach()
        driver = SessionDriver(plan, adapter, bytes(SESSION_NONCE_BYTES), make_config())
        driver.pump(0)
        driver.receive(
            encode_frame(
                SYNTHETIC_RETURN_PREFIX,
                (build_payload(bytes([9] * SESSION_NONCE_BYTES), 0, 16),),
                SERVER_TO_BROWSER,
            ),
            1,
        )
        record = driver.session_record()
        self.assertEqual(
            self.observed["faultRuns"]["foreignNonce"],
            {
                "foreignFrames": record["foreignFrames"],
                "malformedFrames": record["malformedFrames"],
                "outcomes": [item["outcome"] for item in record["cases"]],
                "prefixMismatchFrames": record["prefixMismatchFrames"],
                "unmatchedFrames": record["unmatchedFrames"],
                "writeFailures": record["writeFailures"],
            },
        )
        self.assertEqual(record["foreignFrames"], 1)

    def test_browser_refuses_the_same_configurations_as_the_reference(self) -> None:
        spaced = "  " + SYNTHETIC_PREFIX.hex()[2:]
        reference = {
            "spacedRoutingPrefix": dict(BASE_CONFIG, routingPrefixHex=spaced),
            "spacedReturnPrefix": dict(BASE_CONFIG, expectedReturnPrefixHex=spaced),
            "shortRoutingPrefix": dict(
                BASE_CONFIG, routingPrefixHex=SYNTHETIC_PREFIX.hex()[2:]
            ),
            "nonHexRoutingPrefix": dict(BASE_CONFIG, routingPrefixHex="zz" * 40),
            "unacknowledgedPort": dict(
                BASE_CONFIG, destinationPortMatchesProjection=False
            ),
            "emptyAuthorization": dict(BASE_CONFIG, authorization=""),
            "templateWithoutPlaceholder": dict(
                BASE_CONFIG, endpointTemplate="https://relay.invalid/none"
            ),
            "boundBelowOne": dict(BASE_CONFIG, maxInFlightDatagrams=0),
        }
        for name, mapping in reference.items():
            with self.assertRaises(ProbeConfigError, msg=name):
                parse_probe_config(mapping)
            self.assertTrue(
                self.observed["configRejections"][name],
                f"the browser accepted {name}",
            )
        self.assertEqual(sorted(self.observed["configRejections"]), sorted(reference))

    def test_browser_validator_rejects_every_mutation_the_reference_does(self) -> None:
        rejections = self.observed["validatorRejections"]
        self.assertTrue(rejections, "the harness reported no validator cases")
        accepted = sorted(name for name, refused in rejections.items() if not refused)
        self.assertEqual(accepted, [])

    def test_browser_summary_matches_the_reference(self) -> None:
        plan = self.fault_plan()
        driver, _ = run_plan(plan, relay=LoopbackRelay(max_datagram_size_bytes=20000))
        report = build_report([driver.session_record()], "ab" * 32, "harness")
        self.assertEqual(self.observed["faultSummary"], summarize_report(report, plan))

    def test_a_browser_report_validates_against_the_reference_validator(self) -> None:
        for limit in self.limits:
            report = build_report(
                [self.observed["records"][str(limit)]], "ab" * 32, "loopback harness"
            )
            validate_report(report, self.plan)
            summary = summarize_report(report, self.plan)
            self.assertEqual(len(summary["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
