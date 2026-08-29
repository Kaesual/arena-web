# SPDX-License-Identifier: GPL-2.0-or-later
"""The routed datagram contract and the measurement logic built on it.

This module is the normative implementation of
`docs/relay-datagram-contract.md`: the frame grammar, the payload tag, the
measurement plan derived from `locks/relay-measurement-vector.json`, the session
driver that runs that plan, and the validator for the report it produces.

Nothing here reads the network, the filesystem or the clock. Frames arrive as
bytes, time arrives as a number the caller supplies, and the transport is an
object with a `send` method and a `max_datagram_size_bytes` attribute. That is
what lets the browser probe and the deterministic tests exercise exactly the
same logic, one over WebTransport and one over an in-memory relay.

The module is game-neutral by construction. It moves opaque payloads of chosen
sizes and never inspects them beyond its own 16-byte tag.
"""

from __future__ import annotations

import math
import struct
from collections import Counter, deque
from dataclasses import dataclass, field

# docs/relay-datagram-contract.md, "Frame format". These are the same constants
# the committed measurement vector fixes; `MeasurementPlan.from_vector` refuses a
# vector that disagrees with them.
RELAY_HEADER_BYTES = 40
LENGTH_PREFIX_BYTES = 2
SINGLE_DATAGRAM_OVERHEAD_BYTES = 42

# "Payload identification". The vector fixes the 16-byte prefix placement; this
# contract fixes its interior as a session nonce plus a per-datagram ordinal.
NONCE_BYTES = 16
MINIMUM_TAGGED_INNER_BYTES = 16
SESSION_NONCE_BYTES = 12
ORDINAL_BYTES = 4

# A u16 length prefix cannot describe more than this, whatever a vector asks for.
MAX_LENGTH_PREFIX_VALUE = 65535

BROWSER_TO_SERVER = "browserToServer"
SERVER_TO_BROWSER = "serverToBrowser"
DIRECTIONS = (BROWSER_TO_SERVER, SERVER_TO_BROWSER)

REPORT_KIND = "arena-web-routed-datagram-measurement"
REPORT_FORMAT_VERSION = 1

CASE_SINGLE = "single"
CASE_PACKED = "packed"

OUTCOME_ECHOED = "echoed"
OUTCOME_PAYLOAD_MISMATCH = "payloadMismatch"
OUTCOME_TIMED_OUT = "timedOut"
OUTCOME_SEND_FAILED = "sendFailed"
OUTCOME_NOT_SENT = "notSentFrameExceedsTransportLimit"
OUTCOMES = (
    OUTCOME_ECHOED,
    OUTCOME_NOT_SENT,
    OUTCOME_PAYLOAD_MISMATCH,
    OUTCOME_SEND_FAILED,
    OUTCOME_TIMED_OUT,
)


class RelayProbeError(ValueError):
    """Base class for every fail-closed rejection this module makes."""


class RelayFrameError(RelayProbeError):
    """Raised when bytes do not form a legal frame for their direction."""


class MeasurementPlanError(RelayProbeError):
    """Raised when a measurement vector cannot produce a correlatable plan."""


class ProbeConfigError(RelayProbeError):
    """Raised when the runtime configuration is missing or malformed."""


class MeasurementReportError(RelayProbeError):
    """Raised when a measurement report is inconsistent or out of range."""


class AdapterSendError(RuntimeError):
    """Raised by a transport adapter when one frame could not be written."""


# --------------------------------------------------------------------------
# Frame grammar
# --------------------------------------------------------------------------


def frame_bytes_for_sizes(sizes) -> int:
    """Return the frame length that carries inner datagrams of these sizes."""
    return RELAY_HEADER_BYTES + sum(LENGTH_PREFIX_BYTES + size for size in sizes)


@dataclass(frozen=True)
class DecodedFrame:
    prefix: bytes
    datagrams: tuple[bytes, ...]


def _check_direction(direction: str) -> None:
    if direction not in DIRECTIONS:
        raise RelayFrameError(f"unknown direction {direction!r}")


def encode_frame(
    prefix: bytes,
    datagrams,
    direction: str,
    max_inner_datagram_bytes: int = MAX_LENGTH_PREFIX_VALUE,
) -> bytes:
    """Return one relay frame, refusing anything the contract calls malformed."""
    _check_direction(direction)
    if len(prefix) != RELAY_HEADER_BYTES:
        raise RelayFrameError(
            f"routing prefix is {len(prefix)} bytes, not {RELAY_HEADER_BYTES}"
        )
    datagrams = tuple(datagrams)
    if direction == SERVER_TO_BROWSER and len(datagrams) != 1:
        raise RelayFrameError(
            f"a server-to-browser frame carries exactly one inner datagram, "
            f"not {len(datagrams)}"
        )
    if direction == BROWSER_TO_SERVER and not datagrams:
        raise RelayFrameError("a browser-to-server frame carries at least one datagram")
    ceiling = min(max_inner_datagram_bytes, MAX_LENGTH_PREFIX_VALUE)
    parts = [prefix]
    for datagram in datagrams:
        if len(datagram) > ceiling:
            raise RelayFrameError(
                f"inner datagram of {len(datagram)} bytes exceeds the "
                f"{ceiling} byte ceiling"
            )
        parts.append(struct.pack(">H", len(datagram)))
        parts.append(bytes(datagram))
    return b"".join(parts)


def decode_frame(
    frame: bytes,
    direction: str,
    max_inner_datagram_bytes: int = MAX_LENGTH_PREFIX_VALUE,
) -> DecodedFrame:
    """Return the prefix and inner datagrams of one frame, or reject the frame.

    Every length is compared against the bytes actually present and against the
    ceiling *before* any payload is copied, so a frame that declares 65,535 bytes
    in a 44-byte datagram costs one comparison rather than a 64 KiB allocation.
    A violation rejects the whole frame; there is no partial parse and no
    resynchronisation inside a frame.
    """
    _check_direction(direction)
    total = len(frame)
    if total < RELAY_HEADER_BYTES:
        raise RelayFrameError(
            f"frame of {total} bytes is shorter than the "
            f"{RELAY_HEADER_BYTES} byte routing prefix"
        )
    ceiling = min(max_inner_datagram_bytes, MAX_LENGTH_PREFIX_VALUE)
    spans: list[tuple[int, int]] = []
    offset = RELAY_HEADER_BYTES
    while offset < total:
        if total - offset < LENGTH_PREFIX_BYTES:
            raise RelayFrameError("frame ends inside a length prefix")
        (length,) = struct.unpack_from(">H", frame, offset)
        offset += LENGTH_PREFIX_BYTES
        if length > ceiling:
            raise RelayFrameError(
                f"inner datagram of {length} bytes exceeds the {ceiling} byte ceiling"
            )
        if total - offset < length:
            raise RelayFrameError("frame ends inside an inner datagram")
        spans.append((offset, length))
        offset += length
    if direction == SERVER_TO_BROWSER and len(spans) != 1:
        raise RelayFrameError(
            f"a server-to-browser frame carries exactly one inner datagram, "
            f"not {len(spans)}"
        )
    if direction == BROWSER_TO_SERVER and not spans:
        raise RelayFrameError("a browser-to-server frame carries at least one datagram")
    return DecodedFrame(
        prefix=bytes(frame[:RELAY_HEADER_BYTES]),
        datagrams=tuple(
            bytes(frame[start : start + length]) for start, length in spans
        ),
    )


# --------------------------------------------------------------------------
# Payload identification
# --------------------------------------------------------------------------


def datagram_tag(session_nonce: bytes, ordinal: int) -> bytes:
    """Return the 16-byte payload prefix for one datagram of one session."""
    if len(session_nonce) != SESSION_NONCE_BYTES:
        raise RelayProbeError(
            f"session nonce is {len(session_nonce)} bytes, not {SESSION_NONCE_BYTES}"
        )
    if not 0 <= ordinal <= 0xFFFFFFFF:
        raise RelayProbeError(f"datagram ordinal {ordinal} is out of range")
    return bytes(session_nonce) + struct.pack(">I", ordinal)


def build_payload(session_nonce: bytes, ordinal: int, size: int) -> bytes:
    """Return the exact payload the plan sends for one datagram.

    Payloads of at least `MINIMUM_TAGGED_INNER_BYTES` open with the tag; the
    remaining bytes, and the whole of a shorter payload, are the deterministic
    filler `payload[i] == i mod 256`.
    """
    if not 0 <= size <= MAX_LENGTH_PREFIX_VALUE:
        raise RelayProbeError(f"payload size {size} is out of range")
    filler = bytes(index & 0xFF for index in range(size))
    if size < MINIMUM_TAGGED_INNER_BYTES:
        return filler
    return datagram_tag(session_nonce, ordinal) + filler[NONCE_BYTES:]


def read_tag(payload: bytes):
    """Return `(session_nonce, ordinal)` of a tagged payload, or `None`."""
    if len(payload) < MINIMUM_TAGGED_INNER_BYTES:
        return None
    (ordinal,) = struct.unpack_from(">I", payload, SESSION_NONCE_BYTES)
    return bytes(payload[:SESSION_NONCE_BYTES]), ordinal


# --------------------------------------------------------------------------
# Measurement plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedDatagram:
    ordinal: int
    size: int


@dataclass(frozen=True)
class MeasurementCase:
    index: int
    kind: str
    datagrams: tuple[PlannedDatagram, ...]

    @property
    def sizes(self) -> tuple[int, ...]:
        return tuple(datagram.size for datagram in self.datagrams)

    @property
    def ordinals(self) -> tuple[int, ...]:
        return tuple(datagram.ordinal for datagram in self.datagrams)

    @property
    def tagged(self) -> bool:
        return all(size >= MINIMUM_TAGGED_INNER_BYTES for size in self.sizes)

    @property
    def frame_bytes(self) -> int:
        return frame_bytes_for_sizes(self.sizes)


@dataclass(frozen=True)
class MeasurementPlan:
    cases: tuple[MeasurementCase, ...]
    max_inner_datagram_bytes: int

    @property
    def datagram_count(self) -> int:
        return sum(len(case.datagrams) for case in self.cases)

    @classmethod
    def from_vector(cls, vector) -> "MeasurementPlan":
        """Build the plan the committed measurement vector describes.

        A single round trip through an echoing destination exercises both
        directions at one inner size, so the single-datagram cases are the union
        of the two direction lists. Packed cases are taken as written, and the
        vector's required boundaries must each be present with both adjacent
        sizes or the plan fails rather than silently measuring less.
        """
        if not isinstance(vector, dict):
            raise MeasurementPlanError("measurement vector is not an object")
        framing = vector.get("framing")
        if not isinstance(framing, dict):
            raise MeasurementPlanError("measurement vector has no framing record")
        expected_framing = {
            "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
            "relayHeaderBytes": RELAY_HEADER_BYTES,
            "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
        }
        for name, expected in expected_framing.items():
            if framing.get(name) != expected:
                raise MeasurementPlanError(
                    f"measurement vector framing.{name} is {framing.get(name)!r}, "
                    f"not {expected}"
                )
        identification = vector.get("payloadIdentification")
        if not isinstance(identification, dict):
            raise MeasurementPlanError("measurement vector has no payload record")
        expected_identification = {
            "minimumTaggedInnerBytes": MINIMUM_TAGGED_INNER_BYTES,
            "nonceBytes": NONCE_BYTES,
            "placement": "payload-prefix",
            "smallerCasesRunSequentially": True,
        }
        for name, expected in expected_identification.items():
            if identification.get(name) != expected:
                raise MeasurementPlanError(
                    f"measurement vector payloadIdentification.{name} is "
                    f"{identification.get(name)!r}, not {expected!r}"
                )

        directions = vector.get("directions")
        if not isinstance(directions, dict):
            raise MeasurementPlanError("measurement vector has no direction lists")
        lists = {}
        for name in DIRECTIONS:
            sizes = directions.get(name)
            if not isinstance(sizes, list) or not sizes:
                raise MeasurementPlanError(f"directions.{name} is not a size list")
            for size in sizes:
                if not isinstance(size, int) or isinstance(size, bool):
                    raise MeasurementPlanError(f"directions.{name} holds a non-integer")
                if not 0 <= size <= MAX_LENGTH_PREFIX_VALUE:
                    raise MeasurementPlanError(
                        f"directions.{name} size {size} is out of range"
                    )
            lists[name] = sizes

        boundaries = vector.get("requiredBoundaryBytes")
        if not isinstance(boundaries, list) or not boundaries:
            raise MeasurementPlanError("measurement vector has no required boundaries")
        for boundary in boundaries:
            for name in DIRECTIONS:
                present = set(lists[name])
                for needed in (boundary - 1, boundary, boundary + 1):
                    if needed not in present:
                        raise MeasurementPlanError(
                            f"directions.{name} is missing {needed}, which the "
                            f"{boundary} byte boundary requires"
                        )

        single_sizes = sorted(
            set(lists[BROWSER_TO_SERVER]) | set(lists[SERVER_TO_BROWSER])
        )
        ceiling = max(single_sizes)
        cases: list[MeasurementCase] = []
        ordinal = 0
        for size in single_sizes:
            cases.append(
                MeasurementCase(
                    index=len(cases),
                    kind=CASE_SINGLE,
                    datagrams=(PlannedDatagram(ordinal=ordinal, size=size),),
                )
            )
            ordinal += 1

        packed = vector.get("packedCases")
        if not isinstance(packed, list) or not packed:
            raise MeasurementPlanError("measurement vector has no packed cases")
        for entry in packed:
            if not isinstance(entry, dict):
                raise MeasurementPlanError("packed case is not an object")
            if entry.get("direction") != BROWSER_TO_SERVER:
                raise MeasurementPlanError(
                    "packed cases exist only in the browser-to-server direction"
                )
            sizes = entry.get("sizes")
            if not isinstance(sizes, list) or len(sizes) < 2:
                raise MeasurementPlanError("a packed case needs at least two sizes")
            datagrams = []
            for size in sizes:
                if not isinstance(size, int) or isinstance(size, bool):
                    raise MeasurementPlanError("packed case holds a non-integer size")
                if size < MINIMUM_TAGGED_INNER_BYTES:
                    # Every datagram of a packed case is outstanding at the same
                    # time, so an untagged one could not be attributed to its own
                    # return frame. The committed vector keeps every packed size
                    # at or above the tag length.
                    raise MeasurementPlanError(
                        f"packed case size {size} cannot carry the "
                        f"{MINIMUM_TAGGED_INNER_BYTES} byte tag"
                    )
                if size > ceiling:
                    raise MeasurementPlanError(
                        f"packed case size {size} exceeds the {ceiling} byte ceiling"
                    )
                datagrams.append(PlannedDatagram(ordinal=ordinal, size=size))
                ordinal += 1
            cases.append(
                MeasurementCase(
                    index=len(cases), kind=CASE_PACKED, datagrams=tuple(datagrams)
                )
            )
        return cls(cases=tuple(cases), max_inner_datagram_bytes=ceiling)


# --------------------------------------------------------------------------
# Runtime configuration
# --------------------------------------------------------------------------

AUTHORIZATION_PLACEHOLDER = "{authorization}"

_CONFIG_FIELDS = (
    "authorization",
    "caseTimeoutMilliseconds",
    "certificateHashes",
    "destinationPortMatchesProjection",
    "endpointTemplate",
    "expectedReturnPrefixHex",
    "keepAliveMilliseconds",
    "maxInFlightDatagrams",
    "pathNotes",
    "routingPrefixHex",
)

_REQUIRED_CONFIG_FIELDS = (
    "authorization",
    "destinationPortMatchesProjection",
    "endpointTemplate",
    "routingPrefixHex",
)

_CONFIG_DEFAULTS = {
    "caseTimeoutMilliseconds": 2000,
    "certificateHashes": (),
    "expectedReturnPrefixHex": "",
    "keepAliveMilliseconds": 0,
    "maxInFlightDatagrams": 8,
    "pathNotes": "",
}


def _positive_int(mapping, name: str, minimum: int) -> int:
    value = mapping.get(name, _CONFIG_DEFAULTS[name])
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProbeConfigError(f"{name} is not an integer")
    if value < minimum:
        raise ProbeConfigError(f"{name} must be at least {minimum}")
    return value


def _hex_bytes(value, name: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ProbeConfigError(f"{name} is not a string")
    if len(value) != expected_bytes * 2:
        raise ProbeConfigError(
            f"{name} is {len(value)} characters, not {expected_bytes * 2}"
        )
    try:
        return bytes.fromhex(value)
    except ValueError as error:
        raise ProbeConfigError(f"{name} is not hexadecimal") from error


@dataclass(frozen=True)
class ProbeConfig:
    """The runtime values a probe session needs, none of which are committed."""

    authorization: str
    endpoint_template: str
    routing_prefix: bytes
    expected_return_prefix: bytes = b""
    certificate_hashes: tuple[str, ...] = ()
    case_timeout_ms: int = 2000
    keep_alive_ms: int = 0
    max_in_flight_datagrams: int = 8
    path_notes: str = ""

    def endpoint_url(self) -> str:
        """Return the connect URL. Never store or log the result."""
        return self.endpoint_template.replace(
            AUTHORIZATION_PLACEHOLDER, self.authorization
        )


def parse_probe_config(mapping) -> ProbeConfig:
    """Validate runtime configuration, failing closed on anything missing."""
    if not isinstance(mapping, dict):
        raise ProbeConfigError("runtime configuration is not an object")
    unknown = sorted(set(mapping) - set(_CONFIG_FIELDS))
    if unknown:
        raise ProbeConfigError(f"unknown configuration field {unknown[0]!r}")
    for name in _REQUIRED_CONFIG_FIELDS:
        if name not in mapping:
            raise ProbeConfigError(f"{name} is required")

    template = mapping["endpointTemplate"]
    if not isinstance(template, str) or not template.strip():
        raise ProbeConfigError("endpointTemplate is empty")
    if not template.startswith("https://"):
        raise ProbeConfigError("endpointTemplate must be an https URL")
    if template.count(AUTHORIZATION_PLACEHOLDER) != 1:
        raise ProbeConfigError(
            f"endpointTemplate must contain exactly one "
            f"{AUTHORIZATION_PLACEHOLDER} placeholder"
        )

    authorization = mapping["authorization"]
    if not isinstance(authorization, str) or not authorization.strip():
        raise ProbeConfigError("authorization is empty")

    if mapping["destinationPortMatchesProjection"] is not True:
        # The routing prefix is opaque to this contract, so the probe cannot
        # read the destination port out of it. The port equality the contract
        # requires is therefore an explicit operator acknowledgement, and the
        # probe refuses to run without it.
        raise ProbeConfigError(
            "destinationPortMatchesProjection must be acknowledged as true"
        )

    hashes = mapping.get("certificateHashes", _CONFIG_DEFAULTS["certificateHashes"])
    if not isinstance(hashes, (list, tuple)):
        raise ProbeConfigError("certificateHashes is not a list")
    for entry in hashes:
        _hex_bytes(entry, "certificateHashes entry", 32)

    expected_hex = mapping.get(
        "expectedReturnPrefixHex", _CONFIG_DEFAULTS["expectedReturnPrefixHex"]
    )
    if not isinstance(expected_hex, str):
        raise ProbeConfigError("expectedReturnPrefixHex is not a string")
    expected_prefix = (
        _hex_bytes(expected_hex, "expectedReturnPrefixHex", RELAY_HEADER_BYTES)
        if expected_hex
        else b""
    )

    notes = mapping.get("pathNotes", _CONFIG_DEFAULTS["pathNotes"])
    if not isinstance(notes, str):
        raise ProbeConfigError("pathNotes is not a string")

    return ProbeConfig(
        authorization=authorization,
        endpoint_template=template,
        routing_prefix=_hex_bytes(
            mapping["routingPrefixHex"], "routingPrefixHex", RELAY_HEADER_BYTES
        ),
        expected_return_prefix=expected_prefix,
        certificate_hashes=tuple(hashes),
        case_timeout_ms=_positive_int(mapping, "caseTimeoutMilliseconds", 1),
        keep_alive_ms=_positive_int(mapping, "keepAliveMilliseconds", 0),
        max_in_flight_datagrams=_positive_int(mapping, "maxInFlightDatagrams", 1),
        path_notes=notes,
    )


# --------------------------------------------------------------------------
# Session driver
# --------------------------------------------------------------------------


@dataclass
class _CaseState:
    outcome: str = ""
    sent_at: float = -1.0
    round_trip: float = -1.0
    received: list = field(default_factory=list)
    outstanding: set = field(default_factory=set)


class SessionDriver:
    """Runs one measurement plan over one transport adapter.

    The driver never reads the clock: `pump` and `receive` take the current time
    in milliseconds from the caller. It never fragments, never retries and never
    holds more than `max_in_flight_datagrams` outstanding datagrams.
    """

    def __init__(
        self,
        plan: MeasurementPlan,
        adapter,
        session_nonce: bytes,
        config: ProbeConfig,
        session_index: int = 0,
    ) -> None:
        if len(session_nonce) != SESSION_NONCE_BYTES:
            raise RelayProbeError(
                f"session nonce is {len(session_nonce)} bytes, "
                f"not {SESSION_NONCE_BYTES}"
            )
        self.plan = plan
        self.adapter = adapter
        self.session_nonce = bytes(session_nonce)
        self.config = config
        self.session_index = session_index
        self._pending = deque(plan.cases)
        self._states = {case.index: _CaseState() for case in plan.cases}
        self._inflight: dict[int, tuple[int, bytes]] = {}
        self._return_prefix = config.expected_return_prefix or b""
        self._outstanding_keep_alives = 0
        self._last_send_ms = -1.0
        self.foreign_frames = 0
        self.malformed_frames = 0
        self.prefix_mismatch_frames = 0
        self.unmatched_frames = 0
        self.keep_alive_frames_sent = 0

    # -- state ---------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return all(state.outcome for state in self._states.values())

    def _payload(self, datagram: PlannedDatagram) -> bytes:
        return build_payload(self.session_nonce, datagram.ordinal, datagram.size)

    # -- sending -------------------------------------------------------------

    def pump(self, now: float) -> None:
        """Complete expired cases, start what fits, and keep the session alive."""
        self._expire(now)
        self._start_ready(now)
        self.send_keep_alive_if_due(now)

    def _expire(self, now: float) -> None:
        for case in self.plan.cases:
            state = self._states[case.index]
            if state.outcome or state.sent_at < 0:
                continue
            if now - state.sent_at >= self.config.case_timeout_ms:
                for ordinal in tuple(state.outstanding):
                    self._inflight.pop(ordinal, None)
                state.outstanding.clear()
                state.outcome = OUTCOME_TIMED_OUT

    def _untagged_outstanding(self) -> bool:
        return any(
            len(payload) < MINIMUM_TAGGED_INNER_BYTES
            for _, payload in self._inflight.values()
        )

    def _start_ready(self, now: float) -> None:
        while self._pending:
            case = self._pending[0]
            # An untagged payload cannot be attributed to a return frame, so it
            # runs alone in both directions: nothing may be outstanding when it
            # starts, and nothing may start while it is outstanding.
            if self._untagged_outstanding():
                return
            if not case.tagged:
                if self._inflight or self._outstanding_keep_alives:
                    return
            elif self._inflight and (
                len(self._inflight) + len(case.datagrams)
                > self.config.max_in_flight_datagrams
            ):
                return
            self._pending.popleft()
            self._send_case(case, now)

    def _send_case(self, case: MeasurementCase, now: float) -> None:
        state = self._states[case.index]
        payloads = [self._payload(datagram) for datagram in case.datagrams]
        frame = encode_frame(
            self.config.routing_prefix,
            payloads,
            BROWSER_TO_SERVER,
            self.plan.max_inner_datagram_bytes,
        )
        if len(frame) > self.adapter.max_datagram_size_bytes:
            state.outcome = OUTCOME_NOT_SENT
            return
        try:
            self.adapter.send(frame)
        except AdapterSendError:
            state.outcome = OUTCOME_SEND_FAILED
            return
        state.sent_at = now
        self._last_send_ms = now
        for datagram, payload in zip(case.datagrams, payloads):
            state.outstanding.add(datagram.ordinal)
            self._inflight[datagram.ordinal] = (case.index, payload)

    def send_keep_alive_if_due(self, now: float) -> None:
        """Send the smallest legal frame if the session has been idle.

        Never while a case is outstanding: a keep-alive echo is a 0-byte inner
        datagram and would otherwise be indistinguishable from a 0-byte case.
        """
        if self.config.keep_alive_ms <= 0 or self._inflight:
            return
        if (
            self._last_send_ms >= 0
            and now - self._last_send_ms < self.config.keep_alive_ms
        ):
            return
        frame = encode_frame(
            self.config.routing_prefix,
            (b"",),
            BROWSER_TO_SERVER,
            self.plan.max_inner_datagram_bytes,
        )
        try:
            self.adapter.send(frame)
        except AdapterSendError:
            return
        self._last_send_ms = now
        self._outstanding_keep_alives += 1
        self.keep_alive_frames_sent += 1

    # -- receiving -----------------------------------------------------------

    def receive(self, frame: bytes, now: float) -> None:
        """Attribute one inbound frame, or account for why it was refused."""
        try:
            decoded = decode_frame(
                frame, SERVER_TO_BROWSER, self.plan.max_inner_datagram_bytes
            )
        except RelayFrameError:
            self.malformed_frames += 1
            return
        if not self._return_prefix:
            self._return_prefix = decoded.prefix
        elif decoded.prefix != self._return_prefix:
            self.prefix_mismatch_frames += 1
            return
        payload = decoded.datagrams[0]
        tag = read_tag(payload)
        if tag is None:
            self._receive_untagged(payload, now)
            return
        nonce, ordinal = tag
        if nonce != self.session_nonce:
            self.foreign_frames += 1
            return
        entry = self._inflight.get(ordinal)
        if entry is None:
            self.unmatched_frames += 1
            return
        self._complete(entry[0], ordinal, payload, entry[1], now)

    def _receive_untagged(self, payload: bytes, now: float) -> None:
        if not payload and self._outstanding_keep_alives:
            self._outstanding_keep_alives -= 1
            return
        if len(self._inflight) != 1:
            self.unmatched_frames += 1
            return
        ordinal, (case_index, expected) = next(iter(self._inflight.items()))
        if len(expected) >= MINIMUM_TAGGED_INNER_BYTES:
            self.unmatched_frames += 1
            return
        self._complete(case_index, ordinal, payload, expected, now)

    def _complete(
        self,
        case_index: int,
        ordinal: int,
        payload: bytes,
        expected: bytes,
        now: float,
    ) -> None:
        state = self._states[case_index]
        self._inflight.pop(ordinal, None)
        state.outstanding.discard(ordinal)
        if payload != expected:
            state.outstanding.clear()
            for other in tuple(self._inflight):
                if self._inflight[other][0] == case_index:
                    del self._inflight[other]
            state.outcome = OUTCOME_PAYLOAD_MISMATCH
            return
        state.received.append(len(payload))
        if not state.outstanding:
            state.outcome = OUTCOME_ECHOED
            state.round_trip = now - state.sent_at

    # -- reporting -----------------------------------------------------------

    def session_record(self) -> dict:
        """Return this session's record for the measurement report."""
        cases = []
        for case in self.plan.cases:
            state = self._states[case.index]
            cases.append(
                {
                    "caseIndex": case.index,
                    "kind": case.kind,
                    "ordinals": list(case.ordinals),
                    "outcome": state.outcome or OUTCOME_TIMED_OUT,
                    "receivedFrames": [
                        {
                            "frameBytes": size + SINGLE_DATAGRAM_OVERHEAD_BYTES,
                            "innerBytes": size,
                        }
                        for size in state.received
                    ],
                    "roundTripMilliseconds": (
                        float(state.round_trip)
                        if state.outcome == OUTCOME_ECHOED
                        else None
                    ),
                    "sentFrameBytes": case.frame_bytes,
                    "sentInnerBytes": list(case.sizes),
                }
            )
        return {
            "caseTimeoutMilliseconds": self.config.case_timeout_ms,
            "cases": cases,
            "foreignFrames": self.foreign_frames,
            "keepAliveFramesSent": self.keep_alive_frames_sent,
            "keepAliveMilliseconds": self.config.keep_alive_ms,
            "malformedFrames": self.malformed_frames,
            "maxDatagramSizeBytes": self.adapter.max_datagram_size_bytes,
            "maxInFlightDatagrams": self.config.max_in_flight_datagrams,
            "prefixMismatchFrames": self.prefix_mismatch_frames,
            "sessionIndex": self.session_index,
            "unmatchedFrames": self.unmatched_frames,
        }


def build_report(
    sessions, measurement_vector_sha256: str, path_notes: str = ""
) -> dict:
    """Assemble the machine-readable report from finished session records."""
    return {
        "formatVersion": REPORT_FORMAT_VERSION,
        "framing": {
            "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
            "relayHeaderBytes": RELAY_HEADER_BYTES,
            "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
        },
        "kind": REPORT_KIND,
        "measurementVectorSha256": measurement_vector_sha256,
        "pathNotes": path_notes,
        "sessions": list(sessions),
    }


# --------------------------------------------------------------------------
# Report validation
# --------------------------------------------------------------------------

_REPORT_FIELDS = (
    "formatVersion",
    "framing",
    "kind",
    "measurementVectorSha256",
    "pathNotes",
    "sessions",
)
_SESSION_FIELDS = (
    "caseTimeoutMilliseconds",
    "cases",
    "foreignFrames",
    "keepAliveFramesSent",
    "keepAliveMilliseconds",
    "malformedFrames",
    "maxDatagramSizeBytes",
    "maxInFlightDatagrams",
    "prefixMismatchFrames",
    "sessionIndex",
    "unmatchedFrames",
)
_CASE_FIELDS = (
    "caseIndex",
    "kind",
    "ordinals",
    "outcome",
    "receivedFrames",
    "roundTripMilliseconds",
    "sentFrameBytes",
    "sentInnerBytes",
)
_SESSION_COUNTERS = (
    "foreignFrames",
    "keepAliveFramesSent",
    "malformedFrames",
    "prefixMismatchFrames",
    "unmatchedFrames",
)


def _require_fields(record, fields, label: str) -> None:
    if not isinstance(record, dict):
        raise MeasurementReportError(f"{label} is not an object")
    missing = sorted(set(fields) - set(record))
    if missing:
        raise MeasurementReportError(f"{label} is missing {missing[0]!r}")
    unknown = sorted(set(record) - set(fields))
    if unknown:
        raise MeasurementReportError(f"{label} has unknown field {unknown[0]!r}")


def _require_int(record, name: str, label: str, minimum: int) -> int:
    value = record[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise MeasurementReportError(f"{label}.{name} is not an integer")
    if value < minimum:
        raise MeasurementReportError(f"{label}.{name} is below {minimum}")
    return value


def validate_report(report, plan: MeasurementPlan = None) -> dict:
    """Reject a report whose records are inconsistent or out of range."""
    _require_fields(report, _REPORT_FIELDS, "report")
    if report["formatVersion"] != REPORT_FORMAT_VERSION:
        raise MeasurementReportError("report formatVersion is unsupported")
    if report["kind"] != REPORT_KIND:
        raise MeasurementReportError("report kind is unsupported")
    if report["framing"] != {
        "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
        "relayHeaderBytes": RELAY_HEADER_BYTES,
        "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
    }:
        raise MeasurementReportError("report framing does not match the contract")
    digest = report["measurementVectorSha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise MeasurementReportError("measurementVectorSha256 is not a SHA-256 digest")
    try:
        bytes.fromhex(digest)
    except ValueError as error:
        raise MeasurementReportError(
            "measurementVectorSha256 is not hexadecimal"
        ) from error
    if not isinstance(report["pathNotes"], str):
        raise MeasurementReportError("pathNotes is not a string")
    sessions = report["sessions"]
    if not isinstance(sessions, list) or not sessions:
        raise MeasurementReportError("report has no sessions")
    previous = -1
    for session in sessions:
        index = _validate_session(session, plan)
        if index <= previous:
            raise MeasurementReportError("session indices are not ascending")
        previous = index
    return report


def _validate_session(session, plan) -> int:
    _require_fields(session, _SESSION_FIELDS, "session")
    index = _require_int(session, "sessionIndex", "session", 0)
    ceiling = min(
        plan.max_inner_datagram_bytes if plan is not None else MAX_LENGTH_PREFIX_VALUE,
        MAX_LENGTH_PREFIX_VALUE,
    )
    max_datagram = _require_int(session, "maxDatagramSizeBytes", "session", 1)
    _require_int(session, "caseTimeoutMilliseconds", "session", 1)
    _require_int(session, "keepAliveMilliseconds", "session", 0)
    _require_int(session, "maxInFlightDatagrams", "session", 1)
    for name in _SESSION_COUNTERS:
        _require_int(session, name, "session", 0)
    cases = session["cases"]
    if not isinstance(cases, list) or not cases:
        raise MeasurementReportError("session has no cases")
    if plan is not None and len(cases) != len(plan.cases):
        raise MeasurementReportError(
            f"session has {len(cases)} cases, but the plan has {len(plan.cases)}"
        )
    previous_case = -1
    seen_ordinals: set = set()
    for position, case in enumerate(cases):
        planned = plan.cases[position] if plan is not None else None
        case_index = _validate_case(case, ceiling, max_datagram, planned, seen_ordinals)
        if case_index <= previous_case:
            raise MeasurementReportError("case indices are not ascending")
        previous_case = case_index
    return index


def _validate_case(case, ceiling, max_datagram, planned, seen_ordinals) -> int:
    _require_fields(case, _CASE_FIELDS, "case")
    case_index = _require_int(case, "caseIndex", "case", 0)
    if case["kind"] not in (CASE_SINGLE, CASE_PACKED):
        raise MeasurementReportError(f"case kind {case['kind']!r} is unknown")
    if case["outcome"] not in OUTCOMES:
        raise MeasurementReportError(f"case outcome {case['outcome']!r} is unknown")

    sizes = case["sentInnerBytes"]
    if not isinstance(sizes, list) or not sizes:
        raise MeasurementReportError("case sent no inner datagram")
    for size in sizes:
        if not isinstance(size, int) or isinstance(size, bool):
            raise MeasurementReportError("sentInnerBytes holds a non-integer")
        if not 0 <= size <= ceiling:
            raise MeasurementReportError(
                f"sent inner size {size} is outside 0..{ceiling}"
            )
    if case["kind"] == CASE_SINGLE and len(sizes) != 1:
        raise MeasurementReportError("a single case carries exactly one datagram")
    if case["kind"] == CASE_PACKED and len(sizes) < 2:
        raise MeasurementReportError("a packed case carries at least two datagrams")

    ordinals = case["ordinals"]
    if not isinstance(ordinals, list) or len(ordinals) != len(sizes):
        raise MeasurementReportError("ordinals do not match the sent datagrams")
    for ordinal in ordinals:
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise MeasurementReportError("ordinals hold a non-ordinal value")
        if ordinal in seen_ordinals:
            raise MeasurementReportError(f"ordinal {ordinal} is reused in one session")
        seen_ordinals.add(ordinal)

    expected_frame = frame_bytes_for_sizes(sizes)
    if case["sentFrameBytes"] != expected_frame:
        raise MeasurementReportError(
            f"sentFrameBytes {case['sentFrameBytes']!r} does not equal the "
            f"{expected_frame} bytes its inner sizes require"
        )

    received = case["receivedFrames"]
    if not isinstance(received, list):
        raise MeasurementReportError("receivedFrames is not a list")
    if len(received) > len(sizes):
        raise MeasurementReportError("more frames returned than datagrams sent")
    # A returned frame is only recorded after a byte-exact match with something
    # this case sent, so the returned sizes must be a sub-multiset of the sent
    # ones whatever the outcome, not only when the case completed.
    remaining = Counter(sizes)
    for entry in received:
        _require_fields(entry, ("frameBytes", "innerBytes"), "received frame")
        inner = _require_int(entry, "innerBytes", "received frame", 0)
        if inner > ceiling:
            raise MeasurementReportError(
                f"received inner size {inner} is above {ceiling}"
            )
        if entry["frameBytes"] != inner + SINGLE_DATAGRAM_OVERHEAD_BYTES:
            raise MeasurementReportError(
                "a returned frame does not carry the 42-byte single-datagram "
                "overhead"
            )
        if remaining[inner] <= 0:
            raise MeasurementReportError(
                f"a returned frame reports {inner} bytes, which this case did "
                f"not send"
            )
        remaining[inner] -= 1

    round_trip = case["roundTripMilliseconds"]
    if case["outcome"] == OUTCOME_ECHOED:
        if len(received) != len(sizes):
            raise MeasurementReportError("an echoed case is missing a return frame")
        if not isinstance(round_trip, (int, float)) or isinstance(round_trip, bool):
            raise MeasurementReportError("an echoed case has no round-trip time")
        if not math.isfinite(round_trip) or round_trip < 0:
            raise MeasurementReportError("round-trip time is out of range")
    elif round_trip is not None:
        raise MeasurementReportError("only an echoed case carries a round-trip time")

    if case["outcome"] == OUTCOME_NOT_SENT:
        if received:
            raise MeasurementReportError("an unsent case returned frames")
        if case["sentFrameBytes"] <= max_datagram:
            raise MeasurementReportError(
                "a case refused for size fits the reported datagram maximum"
            )

    if planned is not None:
        if (
            case_index != planned.index
            or list(planned.sizes) != sizes
            or list(planned.ordinals) != ordinals
            or case["kind"] != planned.kind
        ):
            raise MeasurementReportError(f"case {case_index} does not match the plan")
    return case_index


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def summarize_report(report, plan: MeasurementPlan = None) -> dict:
    """Reduce a validated report to per-session ranges.

    The result is deliberately per session and per path. `contiguousInnerBytes`
    is the largest single-datagram inner size for which that session echoed
    every smaller planned single size as well, so a path that accepts a large
    size only intermittently cannot raise it. The report-level floor is the
    minimum of those values across the listed sessions and carries no safety
    margin; it describes the sessions in this report and is not a universal
    transport constant.
    """
    validate_report(report, plan)
    summaries = []
    floors = []
    for session in report["sessions"]:
        echoed: set = set()
        failed: set = set()
        largest_frame = None
        for case in session["cases"]:
            if case["kind"] != CASE_SINGLE:
                continue
            size = case["sentInnerBytes"][0]
            if case["outcome"] == OUTCOME_ECHOED:
                echoed.add(size)
                frame = case["sentFrameBytes"]
                largest_frame = (
                    frame if largest_frame is None else max(largest_frame, frame)
                )
            else:
                failed.add(size)
        contiguous = None
        for size in sorted(echoed | failed):
            if size in failed:
                break
            contiguous = size
        summaries.append(
            {
                "contiguousInnerBytes": contiguous,
                "echoedSingleCases": len(echoed),
                "failedSingleCases": len(failed),
                "largestEchoedFrameBytes": largest_frame,
                "largestEchoedInnerBytes": max(echoed) if echoed else None,
                "maxDatagramSizeBytes": session["maxDatagramSizeBytes"],
                "monotonic": not (echoed and failed and max(echoed) > min(failed)),
                "sessionIndex": session["sessionIndex"],
                "smallestFailedInnerBytes": min(failed) if failed else None,
            }
        )
        floors.append(contiguous)
    return {
        "conservativeInnerFloorBytes": (
            min(floors)
            if floors and all(value is not None for value in floors)
            else None
        ),
        "sessions": summaries,
    }
