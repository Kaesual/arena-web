# SPDX-License-Identifier: GPL-2.0-or-later
"""The WP6 network-sizing arithmetic, as executable derivation rather than prose.

This module answers one question: which ioq3 datagrams fit through the routed
browser path this repository measured, and what would have to change so that
all of them do. It is the machine-checkable half of
`docs/wp6-network-sizing.md`; the document states the decision, this module
recomputes every number in it from committed evidence.

Three kinds of input meet here.

1. **The measured path.** `records/wp2-routed-measurement.json` — five pinned-
   browser sessions through a real relay. The module reads the reported
   datagram maximum, the relay's framing overhead and the *contiguous* inner
   floor out of that record through `relay_probe`, so it consumes exactly the
   numbers WP2 validated rather than a transcription of them.
2. **The observed game traffic.** `records/wp5-packet-census.json` — 41,833
   datagrams of a driven native session. The module reads per-direction maxima,
   the netchan header asymmetry and the fragmented gamestate out of it.
3. **The engine's own bounds**, restated here as named constants with a
   `file:line` citation each. A short driven session cannot produce the largest
   datagram the code can emit, so the boundary cases below are derived from the
   sources at the pinned engine commit and not from any capture.

Two properties are deliberate. Nothing here reads the network, a clock, the
environment or Git, so the derivation is a pure function of the committed bytes;
and nothing here hardcodes a conclusion. Every verdict is computed from record
values, so a doctored record produces a different verdict rather than the same
one. `tests/test_network_sizing.py` exercises exactly that.

The module decides nothing. It produces the arithmetic; selecting a strategy and
freezing thresholds is the operator's, and both candidate sizing targets are
carried side by side precisely so the choice stays open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from relay_probe import summarize_report, validate_report

CLIENT_TO_SERVER = "client-to-server"
SERVER_TO_CLIENT = "server-to-client"
DIRECTIONS = (CLIENT_TO_SERVER, SERVER_TO_CLIENT)

NETCHAN = "netchan"
CONNECTIONLESS = "connectionless"


class NetworkSizingError(ValueError):
    """Raised when an input record cannot support the derivation."""


def _fail(message: str) -> None:
    raise NetworkSizingError(message)


# --------------------------------------------------------------------------
# Engine constants, restated with citations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineConstant:
    """One numeric bound taken from the pinned engine sources."""

    name: str
    value: int
    citation: str
    note: str

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "citation": self.citation,
            "note": self.note,
        }


# The engine commit these citations were read at. The census was driven against
# the upstream base commit; the current baseline pin adds one renderer-only
# commit on top, which touches no file named here. The derivation asserts the
# census agrees with these constants, so a pin that did change packet logic
# would fail rather than pass silently.
ENGINE_COMMIT = "92351b8f0543448b9defaac25c552274eecbf15b"
ENGINE_CENSUS_COMMIT = "588393618dbc82e7207c21c6ddecca229944a03a"

ENGINE_CONSTANTS: tuple[EngineConstant, ...] = (
    EngineConstant(
        "MAX_MSGLEN",
        16384,
        "code/qcommon/qcommon.h:184",
        "largest engine message; also the out-of-band send buffer, which is why "
        "a connectionless datagram has no packet-sized ceiling of its own",
    ),
    EngineConstant(
        "MAX_PACKETLEN",
        1400,
        "code/qcommon/net_chan.c:50",
        "size of the netchan send buffer (net_chan.c:111 and :179); it bounds a "
        "netchan datagram but is never applied to out-of-band traffic",
    ),
    EngineConstant(
        "FRAGMENT_SIZE",
        1300,
        "code/qcommon/net_chan.c:52",
        "defined as MAX_PACKETLEN - 100; both the emitted fragment payload and "
        "the receiver's more-fragments-follow test (net_chan.c:373)",
    ),
    EngineConstant(
        "MAX_INFO_STRING",
        1024,
        "code/qcommon/q_shared.h:237",
        "bounds serverinfo and userinfo, hence statusResponse, infoResponse and "
        "the connect packet",
    ),
    EngineConstant(
        "MAX_NAME_LENGTH",
        32,
        "code/qcommon/q_shared.h:253",
        "client name buffer; a status line can carry 31 name bytes",
    ),
    EngineConstant(
        "MAX_STRING_CHARS",
        1024,
        "code/qcommon/q_shared.h:233",
        "bounds a tokenized command string",
    ),
)

_CONSTANTS_BY_NAME = {constant.name: constant for constant in ENGINE_CONSTANTS}


def engine_constant(name: str) -> EngineConstant:
    """Return one restated engine constant, or fail."""
    if name not in _CONSTANTS_BY_NAME:
        _fail(f"no engine constant named {name!r} is restated here")
    return _CONSTANTS_BY_NAME[name]


def engine_value(name: str) -> int:
    return engine_constant(name).value


# --------------------------------------------------------------------------
# Netchan datagram geometry
# --------------------------------------------------------------------------

# Every netchan datagram opens with the sequence number; a client adds its
# qport, the challenge checksum follows, and a fragment adds the two fragment
# fields. This is the same decomposition `packet_census.classify` applies to
# captured bytes, which is why the derived header widths below and the census's
# observed `headerAsymmetry` must agree — and the derivation checks that they do.
SEQUENCE_BYTES = 4  # code/qcommon/net_chan.c:118 and :207
QPORT_BYTES = 2  # code/qcommon/net_chan.c:123, client to server only
FRAGMENT_FIELD_BYTES = 4  # code/qcommon/net_chan.c:137-138, start + length

# The challenge checksum is **not** unconditional, and getting this wrong would
# understate nothing but misdescribe the protocol. Both write sites
# (code/qcommon/net_chan.c:129 and :210) and the matching read
# (code/qcommon/net_chan.c:270-278) sit under `#ifdef LEGACY_PROTOCOL` /
# `if(!chan->compat)`, and LEGACY_PROTOCOL *is* defined in this build:
# code/qcommon/q_shared.h:52 defines it in the non-STANDALONE branch, and
# neither build script sets STANDALONE. So the four widths this module derives
# are the protocol-71 non-compat path — which is what the census observed on all
# 41,833 datagrams, and what the WP7 profile pins by refusing the legacy path.
#
# The direction of safety is worth stating: a compat (protocol-68) connection
# *omits* these four bytes, so every header is 4 bytes SMALLER and every bound
# derived here remains a valid upper bound. Sizing can never be broken by the
# legacy path; only the header geometry's description would be.
CHECKSUM_BYTES = 4  # code/qcommon/net_chan.c:129 and :210, NETCHAN_GENCHECKSUM

OUT_OF_BAND_PREFIX_BYTES = 4  # the 0xffffffff sequence, net_chan.c:578-581


def _check_direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        _fail(f"unknown direction {direction!r}")
    return direction


def netchan_header_bytes(
    direction: str, *, fragmented: bool, compat: bool = False
) -> int:
    """Return the netchan header width for one direction and framing.

    `compat` models a legacy protocol-68 connection, which omits the challenge
    checksum. It exists so the direction-of-safety claim above is computed
    rather than asserted in prose; the fixed profile refuses that path.
    """
    _check_direction(direction)
    header = SEQUENCE_BYTES + (0 if compat else CHECKSUM_BYTES)
    if direction == CLIENT_TO_SERVER:
        header += QPORT_BYTES
    if fragmented:
        header += FRAGMENT_FIELD_BYTES
    return header


def largest_unfragmented_datagram_bytes(fragment_size: int, direction: str) -> int:
    """Largest datagram the unfragmented netchan path can emit.

    `Netchan_Transmit` fragments when the message length is *greater than or
    equal to* FRAGMENT_SIZE (code/qcommon/net_chan.c:187), so the largest
    message that stays in one piece is one byte short of it.
    """
    _require_positive(fragment_size, "fragment size")
    return (fragment_size - 1) + netchan_header_bytes(direction, fragmented=False)


def largest_fragment_datagram_bytes(fragment_size: int, direction: str) -> int:
    """Largest datagram the fragmented netchan path can emit.

    A fragment carries exactly FRAGMENT_SIZE payload bytes unless it is the last
    one (code/qcommon/net_chan.c:132-138), so this is the binding case in both
    directions: it exceeds the unfragmented maximum by the two fragment fields
    plus one byte.
    """
    _require_positive(fragment_size, "fragment size")
    return fragment_size + netchan_header_bytes(direction, fragmented=True)


def largest_engine_datagram_bytes(fragment_size: int) -> int:
    """Largest netchan datagram either endpoint can emit at this fragment size."""
    return max(
        max(
            largest_unfragmented_datagram_bytes(fragment_size, direction),
            largest_fragment_datagram_bytes(fragment_size, direction),
        )
        for direction in DIRECTIONS
    )


def _require_positive(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _fail(f"{name} must be a positive integer, not {value!r}")
    return value


def fragment_payloads(message_bytes: int, fragment_size: int) -> tuple[int, ...]:
    """Return the fragment payload lengths one message is split into.

    This reproduces `Netchan_TransmitNextFragment` exactly, including the rule
    that a message whose length is an exact multiple of FRAGMENT_SIZE needs a
    trailing zero-length fragment so the receiver can tell the message ended
    (code/qcommon/net_chan.c:158-163, matched by the receiver's test at :373).
    A message below the threshold is not fragmented at all.
    """
    _require_positive(fragment_size, "fragment size")
    if message_bytes < 0:
        _fail(f"message size must not be negative, not {message_bytes!r}")
    if message_bytes < fragment_size:
        return ()
    payloads: list[int] = []
    sent = 0
    while True:
        length = min(fragment_size, message_bytes - sent)
        payloads.append(length)
        sent += length
        if sent == message_bytes and length != fragment_size:
            break
    return tuple(payloads)


@dataclass(frozen=True)
class MessageCost:
    """What one engine message costs on the wire at a given fragment size."""

    message_bytes: int
    fragment_size: int
    direction: str
    fragmented: bool
    datagrams: int
    largest_datagram_bytes: int
    total_datagram_bytes: int
    largest_frame_bytes: int
    total_frame_bytes: int

    def as_json(self) -> dict[str, Any]:
        return {
            "messageBytes": self.message_bytes,
            "fragmentSize": self.fragment_size,
            "direction": self.direction,
            "fragmented": self.fragmented,
            "datagrams": self.datagrams,
            "largestDatagramBytes": self.largest_datagram_bytes,
            "totalDatagramBytes": self.total_datagram_bytes,
            "largestFrameBytes": self.largest_frame_bytes,
            "totalFrameBytes": self.total_frame_bytes,
        }


def message_cost(
    message_bytes: int,
    fragment_size: int,
    direction: str,
    framing: "RelayFraming",
) -> MessageCost:
    """Cost one engine message end to end, in datagrams and in relay frames."""
    _check_direction(direction)
    payloads = fragment_payloads(message_bytes, fragment_size)
    if payloads:
        header = netchan_header_bytes(direction, fragmented=True)
        sizes = [header + payload for payload in payloads]
        fragmented = True
    else:
        sizes = [message_bytes + netchan_header_bytes(direction, fragmented=False)]
        fragmented = False
    frames = [framing.frame_bytes(size) for size in sizes]
    return MessageCost(
        message_bytes=message_bytes,
        fragment_size=fragment_size,
        direction=direction,
        fragmented=fragmented,
        datagrams=len(sizes),
        largest_datagram_bytes=max(sizes),
        total_datagram_bytes=sum(sizes),
        largest_frame_bytes=max(frames),
        total_frame_bytes=sum(frames),
    )


# --------------------------------------------------------------------------
# The measured path
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayFraming:
    """The relay's frame overhead, taken from the committed record.

    Kept as data rather than as a constant so that a record describing a
    different framing changes the arithmetic instead of being ignored.
    """

    relay_header_bytes: int
    length_prefix_bytes: int
    single_datagram_overhead_bytes: int

    def frame_bytes(self, inner_bytes: int) -> int:
        """Bytes on the wire for a frame carrying exactly one inner datagram."""
        if inner_bytes < 0:
            _fail(f"inner datagram size must not be negative, not {inner_bytes!r}")
        return inner_bytes + self.single_datagram_overhead_bytes

    def inner_bytes(self, frame_bytes: int) -> int:
        """Largest single inner datagram a frame of this size can carry."""
        return frame_bytes - self.single_datagram_overhead_bytes

    def as_json(self) -> dict[str, Any]:
        return {
            "relayHeaderBytes": self.relay_header_bytes,
            "datagramLengthPrefixBytes": self.length_prefix_bytes,
            "singleDatagramOverheadBytes": self.single_datagram_overhead_bytes,
        }


@dataclass(frozen=True)
class PathFacts:
    """Everything the derivation needs from the routed measurement."""

    framing: RelayFraming
    sessions: int
    reported_maximum_bytes: int
    reported_maximum_constant: bool
    reported_maxima: tuple[int, ...]
    record_backed_inner_floor_bytes: int
    largest_echoed_inner_bytes: int
    smallest_refused_inner_bytes: int | None
    untested_inner_range: tuple[int, int] | None
    derived_inner_budget_bytes: int
    monotonic: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "framing": self.framing.as_json(),
            "sessions": self.sessions,
            "reportedDatagramMaximumBytes": self.reported_maximum_bytes,
            "reportedMaximumConstantAcrossSessions": self.reported_maximum_constant,
            "reportedMaximaObserved": list(self.reported_maxima),
            "recordBackedInnerFloorBytes": self.record_backed_inner_floor_bytes,
            "largestEchoedInnerBytes": self.largest_echoed_inner_bytes,
            "smallestRefusedInnerBytes": self.smallest_refused_inner_bytes,
            "untestedInnerRange": (
                list(self.untested_inner_range) if self.untested_inner_range else None
            ),
            "derivedInnerBudgetBytes": self.derived_inner_budget_bytes,
            "monotonic": self.monotonic,
        }


def read_path_facts(report: Any, plan: Any = None) -> PathFacts:
    """Reduce a validated routed measurement report to the WP6 path facts.

    The report is re-validated here rather than trusted: WP6's whole claim is
    that a reviewer can recompute the decision from committed evidence, and a
    record that no longer satisfies the WP2 contract cannot support a decision.
    """
    validate_report(report, plan)
    summary = summarize_report(report, plan)
    sessions = summary["sessions"]
    if not sessions:
        _fail("routed measurement: the report contains no session")

    # The framing block needs no checking here. `validate_report` above compares
    # it field for field against the contract's own constants and rejects any
    # other value, so by this point it is known good; re-deriving the overhead
    # from it would be a check that can never fire. It is still *read* rather
    # than assumed, so the arithmetic below follows the record rather than a
    # second copy of the same numbers.
    framing_record = report["framing"]
    framing = RelayFraming(
        relay_header_bytes=framing_record["relayHeaderBytes"],
        length_prefix_bytes=framing_record["datagramLengthPrefixBytes"],
        single_datagram_overhead_bytes=framing_record["singleDatagramOverheadBytes"],
    )

    floor = summary["conservativeInnerFloorBytes"]
    if not isinstance(floor, int) or floor <= 0:
        _fail(
            "routed measurement: the report has no conservative inner floor, so "
            "it cannot size anything"
        )

    maxima = tuple(session["maxDatagramSizeBytes"] for session in sessions)
    for value in maxima:
        _require_positive(value, "reported datagram maximum")
    reported = min(maxima)
    largest_echoed = min(
        session["largestEchoedInnerBytes"]
        for session in sessions
        if session["largestEchoedInnerBytes"] is not None
    )
    refused = [
        session["smallestFailedInnerBytes"]
        for session in sessions
        if session["smallestFailedInnerBytes"] is not None
    ]
    smallest_refused = min(refused) if refused else None
    untested = None
    if smallest_refused is not None and smallest_refused > floor + 1:
        untested = (floor + 1, smallest_refused - 1)

    budget = framing.inner_bytes(reported)
    if budget <= 0:
        _fail(
            f"routed measurement: a reported maximum of {reported} bytes leaves "
            "no room for an inner datagram after relay overhead"
        )
    return PathFacts(
        framing=framing,
        sessions=len(sessions),
        reported_maximum_bytes=reported,
        reported_maximum_constant=len(set(maxima)) == 1,
        reported_maxima=tuple(sorted(set(maxima))),
        record_backed_inner_floor_bytes=floor,
        largest_echoed_inner_bytes=largest_echoed,
        smallest_refused_inner_bytes=smallest_refused,
        untested_inner_range=untested,
        derived_inner_budget_bytes=budget,
        monotonic=all(session["monotonic"] for session in sessions),
    )


# --------------------------------------------------------------------------
# The observed game traffic
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedClass:
    """One class of datagram the census actually saw."""

    name: str
    direction: str
    kind: str
    fragmentable: bool
    inner_bytes: int
    count: int
    note: str

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "kind": self.kind,
            "fragmentable": self.fragmentable,
            "innerBytes": self.inner_bytes,
            "count": self.count,
            "note": self.note,
        }


@dataclass(frozen=True)
class CensusFacts:
    """Everything the derivation needs from the packet census."""

    datagrams: int
    fragment_size: int
    max_packet_len: int
    max_msg_len: int
    header_bytes: dict[tuple[str, bool], int]
    observed: tuple[ObservedClass, ...]
    maximum_by_direction: dict[str, int]
    fragmented_messages: tuple[dict[str, Any], ...]
    largest_unfragmented_by_direction: dict[str, int]

    def as_json(self) -> dict[str, Any]:
        return {
            "datagrams": self.datagrams,
            "engineBounds": {
                "fragmentSize": self.fragment_size,
                "maxPacketLen": self.max_packet_len,
                "maxMsgLen": self.max_msg_len,
            },
            "headerBytes": {
                f"{direction}{'-fragmented' if fragmented else ''}": value
                for (direction, fragmented), value in sorted(self.header_bytes.items())
            },
            "maximumByDirection": dict(sorted(self.maximum_by_direction.items())),
            "largestUnfragmentedByDirection": dict(
                sorted(self.largest_unfragmented_by_direction.items())
            ),
            "fragmentedMessages": [dict(message) for message in self.fragmented_messages],
            "observedClasses": [item.as_json() for item in self.observed],
        }


def read_census_facts(census: Any) -> CensusFacts:
    """Reduce the committed packet census to the WP6 traffic facts.

    Only the census's own reduction is read — this does not re-derive the
    summary from raw records, because the committed record carries no raw
    capture. What it does check is that the summary is internally consistent
    with the engine constants restated above, so a census taken against a
    different engine cannot silently size this decision.
    """
    if not isinstance(census, dict):
        _fail("packet census: the record is not an object")
    summary = census.get("summary")
    if not isinstance(summary, dict):
        _fail("packet census: the record has no summary")
    bounds = summary.get("engineBounds")
    if not isinstance(bounds, dict):
        _fail("packet census: the summary has no engineBounds")

    fragment_size = _positive_census_field(bounds, "fragmentSize")
    max_packet_len = _positive_census_field(bounds, "maxPacketLen")
    max_msg_len = _positive_census_field(bounds, "maxMsgLen")
    for name, observed in (
        ("FRAGMENT_SIZE", fragment_size),
        ("MAX_PACKETLEN", max_packet_len),
        ("MAX_MSGLEN", max_msg_len),
    ):
        restated = engine_value(name)
        if observed != restated:
            _fail(
                f"packet census: engineBounds says {name} is {observed}, but the "
                f"pinned engine sources restate it as {restated}; the census does "
                "not describe this engine"
            )

    asymmetry = summary.get("headerAsymmetry")
    if not isinstance(asymmetry, dict):
        _fail("packet census: the summary has no headerAsymmetry")
    header_bytes: dict[tuple[str, bool], int] = {}
    for direction in DIRECTIONS:
        for fragmented in (False, True):
            key = f"{direction}-fragmented" if fragmented else direction
            block = asymmetry.get(key)
            if not isinstance(block, dict):
                _fail(f"packet census: headerAsymmetry has no {key!r} block")
            widths = block.get("headerBytes")
            if not isinstance(widths, list):
                _fail(f"packet census: headerAsymmetry.{key}.headerBytes is not a list")
            derived = netchan_header_bytes(direction, fragmented=fragmented)
            if len(set(widths)) > 1:
                _fail(
                    f"packet census: {key} shows more than one header width "
                    f"({sorted(set(widths))}); the netchan header is not variable"
                )
            if widths and widths[0] != derived:
                _fail(
                    f"packet census: {key} observed a {widths[0]} byte header, but "
                    f"the pinned engine sources derive {derived}"
                )
            header_bytes[(direction, fragmented)] = derived

    by_direction = summary.get("byDirection")
    if not isinstance(by_direction, dict):
        _fail("packet census: the summary has no byDirection block")

    observed: list[ObservedClass] = []
    maximum_by_direction: dict[str, int] = {}
    largest_unfragmented: dict[str, int] = {}
    for direction in DIRECTIONS:
        block = by_direction.get(direction)
        if not isinstance(block, dict):
            _fail(f"packet census: byDirection has no {direction!r} block")
        statistics = block.get("all")
        if not isinstance(statistics, dict) or "maximum" not in statistics:
            _fail(f"packet census: byDirection.{direction}.all has no maximum")
        maximum_by_direction[direction] = int(statistics["maximum"])
        by_class = block.get("byClass")
        if not isinstance(by_class, dict):
            _fail(f"packet census: byDirection.{direction} has no byClass block")

        netchan_block = by_class.get(NETCHAN)
        if isinstance(netchan_block, dict):
            distribution = netchan_block.get("distribution") or []
            fragment_datagram_sizes = _fragment_datagram_sizes(
                summary, direction, header_bytes
            )
            unfragmented = [
                size
                for size, _count in distribution
                if size not in fragment_datagram_sizes
            ]
            largest_unfragmented[direction] = max(unfragmented) if unfragmented else 0
            statistics = netchan_block.get("statistics") or {}
            observed.append(
                ObservedClass(
                    name="netchan",
                    direction=direction,
                    kind=NETCHAN,
                    fragmentable=True,
                    inner_bytes=int(statistics.get("maximum", 0)),
                    count=int(statistics.get("count", 0)),
                    note=(
                        "largest netchan datagram observed in this direction; the "
                        "engine may fragment it, so its size is a function of "
                        "FRAGMENT_SIZE rather than a fixed property of the traffic"
                    ),
                )
            )

        connectionless = by_class.get(CONNECTIONLESS)
        if isinstance(connectionless, dict):
            commands = connectionless.get("commands") or {}
            for command, values in sorted(commands.items()):
                observed.append(
                    ObservedClass(
                        name=command,
                        direction=direction,
                        kind=CONNECTIONLESS,
                        fragmentable=False,
                        inner_bytes=int(values.get("maximum", 0)),
                        count=int(values.get("count", 0)),
                        note=(
                            "out-of-band datagram; netchan fragmentation does not "
                            "apply, so this size must fit the budget as it stands"
                        ),
                    )
                )

    fragmented_messages = summary.get("fragmentedMessages")
    if not isinstance(fragmented_messages, list):
        _fail("packet census: the summary has no fragmentedMessages list")

    overall = summary.get("overall")
    if not isinstance(overall, dict) or "count" not in overall:
        _fail("packet census: the summary has no overall count")

    return CensusFacts(
        datagrams=int(overall["count"]),
        fragment_size=fragment_size,
        max_packet_len=max_packet_len,
        max_msg_len=max_msg_len,
        header_bytes=header_bytes,
        observed=tuple(observed),
        maximum_by_direction=maximum_by_direction,
        fragmented_messages=tuple(dict(message) for message in fragmented_messages),
        largest_unfragmented_by_direction=largest_unfragmented,
    )


def _fragment_datagram_sizes(
    summary: dict[str, Any],
    direction: str,
    header_bytes: dict[tuple[str, bool], int],
) -> set[int]:
    """Sizes in the distribution that are fragments of a fragmented message.

    The census distribution mixes ordinary datagrams with the fragments of the
    gamestate, and the two answer different questions: how big ordinary traffic
    gets, and how big a fragment gets. Reconstructing the fragment sizes from
    the recorded message lengths separates them without needing a raw capture.
    """
    sizes: set[int] = set()
    header = header_bytes[(direction, True)]
    for message in summary.get("fragmentedMessages") or []:
        if not isinstance(message, dict) or message.get("direction") != direction:
            continue
        message_bytes = message.get("messageBytes")
        fragment_size = summary.get("engineBounds", {}).get("fragmentSize")
        if not isinstance(message_bytes, int) or not isinstance(fragment_size, int):
            continue
        for payload in fragment_payloads(message_bytes, fragment_size):
            sizes.add(header + payload)
    return sizes


def _positive_census_field(block: dict[str, Any], name: str) -> int:
    value = block.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _fail(f"packet census: engineBounds.{name} is not a positive integer")
    return value


# --------------------------------------------------------------------------
# Code-level boundary cases for the fixed prototype profile
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PrototypeProfile:
    """The fixed prototype server and client profile the bounds are derived for.

    Everything here is a property of the committed WP5 server configuration and
    the WP0/WP3 content, not a tunable: `sv_maxclients 8`, `sv_pure 0`, no
    downloads, one FFA map. The bounds below are worst cases *within* that
    profile, which is the only reason a connectionless datagram can be bounded
    at all — the engine itself bounds them only by MAX_MSGLEN.
    """

    max_clients: int = 8
    signed_integer_digits: int = 11  # "%i" of INT_MIN, e.g. "-2147483648"
    ping_digits: int = 3  # SV_CalcPings clamps to 999, code/server/sv_main.c:930-932
    # GAMENAME_FOR_MASTER, code/qcommon/q_shared.h:49, used as the com_gamename
    # default at code/qcommon/common.c:2796. Deriving the getchallenge bound
    # with this value reproduces the census's observed 40-byte maximum exactly.
    game_name_bytes: int = len("Quake3Arena")
    server_browser_queries_on_relay_path: bool = False

    @property
    def info_string_bytes(self) -> int:
        """Longest serverinfo or userinfo string, without its terminator."""
        return engine_value("MAX_INFO_STRING") - 1

    @property
    def name_bytes(self) -> int:
        """Longest client name, without its terminator."""
        return engine_value("MAX_NAME_LENGTH") - 1

    def as_json(self) -> dict[str, Any]:
        return {
            "maxClients": self.max_clients,
            "signedIntegerDigits": self.signed_integer_digits,
            "pingDigits": self.ping_digits,
            "gameNameBytes": self.game_name_bytes,
            "infoStringBytes": self.info_string_bytes,
            "nameBytes": self.name_bytes,
            "serverBrowserQueriesOnRelayPath": self.server_browser_queries_on_relay_path,
        }


PROTOTYPE_PROFILE = PrototypeProfile()


@dataclass(frozen=True)
class BoundaryCase:
    """One datagram size the code can produce but the census did not observe.

    Two sizes are carried, and the difference between them is the whole point of
    the connectionless analysis. `inner_bytes` is the worst case *within the
    fixed profile* — eight clients, MAX_INFO_STRING info strings, the committed
    QVM's own strings. `code_ceiling_bytes` is what the engine alone permits,
    which for every out-of-band class is the out-of-band send buffer: the engine
    applies no packet-sized ceiling to this traffic at all.

    `on_relay_path` records whether the profile lets the class reach the relay
    in the first place. A class that cannot occur there is not a sizing problem,
    but it only stays off the path because the profile keeps it off, so it is
    recorded rather than dropped.
    """

    name: str
    direction: str
    kind: str
    fragmentable: bool
    inner_bytes: int
    terms: tuple[tuple[str, int], ...]
    citation: str
    code_ceiling_bytes: int
    ceiling_citation: str
    note: str
    on_relay_path: bool = True
    off_path_reason: str = ""
    observed_bytes: int | None = None
    compressed: bool = False
    variable_term: str = ""

    @property
    def fixed_bytes(self) -> int:
        """Bytes of this case that no profile setting can reduce."""
        if not self.variable_term:
            return self.inner_bytes
        return self.inner_bytes - self._variable_bytes()

    def _variable_bytes(self) -> int:
        for label, value in self.terms:
            if label == self.variable_term:
                return value
        _fail(
            f"{self.name}: no term named {self.variable_term!r} to bound"
        )
        return 0  # unreachable; _fail raises

    def required_cap_bytes(self, budget: int) -> int | None:
        """Largest the variable term may be for this case to fit the budget.

        A negative result means no cap on that term can make the case fit, which
        is the honest answer for a class whose fixed part alone is over budget.
        """
        if not self.variable_term:
            return None
        return budget - self.fixed_bytes

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "kind": self.kind,
            "fragmentable": self.fragmentable,
            "innerBytes": self.inner_bytes,
            "terms": [{"label": label, "bytes": value} for label, value in self.terms],
            "citation": self.citation,
            "codeCeilingBytes": self.code_ceiling_bytes,
            "ceilingCitation": self.ceiling_citation,
            "note": self.note,
            "onRelayPath": self.on_relay_path,
            "offPathReason": self.off_path_reason,
            "observedBytes": self.observed_bytes,
            "compressedOnTheWire": self.compressed,
            "variableTerm": self.variable_term,
            "fixedBytes": self.fixed_bytes,
        }


def _sum_terms(terms: Sequence[tuple[str, int]]) -> int:
    return sum(value for _label, value in terms)


# The out-of-band send path applies no packet-sized ceiling at all: it formats
# into a MAX_MSGLEN buffer and sends `strlen` of it. This single number is why
# connectionless traffic has to be bounded by the profile rather than assumed
# safe, and it is quoted on every connectionless case below.
OOB_PRINT_CEILING_BYTES = engine_value("MAX_MSGLEN") - 1
OOB_PRINT_CEILING_CITATION = "code/qcommon/net_chan.c:575,589"
OOB_DATA_CEILING_BYTES = engine_value("MAX_MSGLEN") * 2
OOB_DATA_CEILING_CITATION = "code/qcommon/net_chan.c:600,616"

# `Huff_Compress` sets cursize to (bloc >> 3) + 12 with bloc starting at 16 bits
# (code/qcommon/huffman.c:421,431). The bit emitter clamps against the input
# size (huffman.c:312-315), but the not-yet-transmitted literal path
# (huffman.c:329-331) and the trailing flush (huffman.c:429) are not clamped, so
# the coder can expand its input by a small constant rather than only shrink it.
# Two bytes is that constant. It is derived from the coder's structure, not
# measured, which is exactly why the decision requires the emitted size to be
# checked on the wire instead of trusted.
HUFFMAN_EXPANSION_BYTES = 2

# The server's rcon output redirect buffer, code/server/sv_main.c:714.
SV_OUTPUTBUF_LENGTH = 1024 - 16

# `getchallenge` and `connect` are formatted into the same local buffer,
# `char data[MAX_INFO_STRING + 10]` at code/client/cl_main.c:2373. Com_sprintf
# truncates to it, so the emitted datagram cannot exceed the buffer's string
# length plus the out-of-band prefix.
GETCHALLENGE_BUFFER_CEILING_BYTES = (
    engine_value("MAX_INFO_STRING") + 10 - 1 + OUT_OF_BAND_PREFIX_BYTES
)


def connectionless_boundary_cases(
    profile: PrototypeProfile = PROTOTYPE_PROFILE,
) -> tuple[BoundaryCase, ...]:
    """Derive the worst-case out-of-band datagram of every class in the profile.

    None of these can be fragmented: `Netchan_Transmit` is not on this path at
    all, and the send functions apply no MAX_PACKETLEN check. Each case is the
    literal format string plus the widest value each conversion can produce
    under the profile.
    """
    digits = profile.signed_integer_digits
    info = profile.info_string_bytes

    # "statusResponse\n%s\n%s" with the serverinfo and one line per connected
    # client. The status buffer is MAX_MSGLEN, so with eight clients the loop's
    # own overflow guard never fires and all eight lines are emitted.
    #
    # The score is a plain int with no engine-side clamp, so it takes the full
    # width of "%i"; the ping is clamped to 999 in SV_CalcPings
    # (code/server/sv_main.c:930-932) and therefore takes three digits, not
    # eleven. The name cannot contain a quote: it arrives as the tokenizer's
    # quoted argument, which any quote would have terminated.
    player_line_terms = (
        ('score, "%i" of an unclamped int', digits),
        ("space", 1),
        ('ping, "%i" clamped to 999', profile.ping_digits),
        ("space", 1),
        ("opening quote", 1),
        ("name, MAX_NAME_LENGTH - 1", profile.name_bytes),
        ("closing quote", 1),
        ("newline", 1),
    )
    player_line_bytes = _sum_terms(player_line_terms)
    status_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "statusResponse\\n"', len("statusResponse\n")),
        ("serverinfo, MAX_INFO_STRING - 1", info),
        ("separating newline", 1),
        (
            f"{profile.max_clients} player lines of {player_line_bytes} bytes",
            profile.max_clients * player_line_bytes,
        ),
    )

    info_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "infoResponse\\n"', len("infoResponse\n")),
        ("serverinfo, MAX_INFO_STRING - 1", info),
    )

    challenge_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "challengeResponse"', len("challengeResponse")),
        ('space and challenge, "%d" of an int', 1 + digits),
        ('space and client challenge, "%d" of an int', 1 + digits),
        ('space and protocol, "%d" of an int', 1 + digits),
    )

    connect_response_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "connectResponse"', len("connectResponse")),
        ('space and challenge, "%d" of an int', 1 + digits),
    )

    # The longest rejection the engine itself can print before a netchan exists.
    # The literals in sv_client.c are all short; the longest is the userinfo
    # length message at sv_client.c:395-397. The game module's own reject string
    # also reaches this format, and the engine bounds it by nothing smaller than
    # the out-of-band buffer — but the committed QVM's longest is 32 bytes
    # ("You are banned from this server.", code/game/g_client.c:907), which is
    # exactly why this bound belongs to the profile and not to the engine.
    longest_engine_reject = (
        "Userinfo string length exceeded.  Try removing setu cvars from your config.\n"
    )
    print_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "print\\n"', len("print\n")),
        ("longest engine rejection literal", len(longest_engine_reject)),
    )

    # The connect packet is the one out-of-band datagram that is compressed
    # before it is sent, so this is the size of the *pre-compression* image and
    # an upper bound only if the Huffman coder never expands. The engine itself
    # reserves twice MAX_MSGLEN for the result, so expansion is something the
    # code allows for; the emitted size therefore has to be checked, not assumed.
    connect_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "connect \\""', len('connect "')),
        ("userinfo, MAX_INFO_STRING - 1", info),
        ("closing quote", 1),
        ("Huffman coder's worst-case expansion", HUFFMAN_EXPANSION_BYTES),
    )

    get_challenge_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "getchallenge "', len("getchallenge ")),
        ('client challenge, "%d" of an int', digits),
        ("space", 1),
        ("com_gamename, GAMENAME_FOR_MASTER default", profile.game_name_bytes),
    )

    get_status_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "getstatus"', len("getstatus")),
    )

    get_info_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "getinfo xxx"', len("getinfo xxx")),
    )

    rcon_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ("message, MAX_RCON_MESSAGE - 4", 1024 - OUT_OF_BAND_PREFIX_BYTES),
    )

    # The rcon *answer*. SV_FlushRedirect prints the accumulated command output
    # back out of band, bounded by the redirect buffer rather than by anything
    # rcon itself declares. It is a second server-to-client consequence of rcon
    # and is off the relayed path for exactly the same reason rcon is.
    rcon_print_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        ('literal "print\\n"', len("print\n")),
        ("output, SV_OUTPUTBUF_LENGTH - 1", SV_OUTPUTBUF_LENGTH - 1),
    )

    # The one out-of-band class the *server* can elicit from the client. The
    # client answers an `echo` command by sending its argument straight back,
    # so unlike the server-browser classes it cannot be excluded by saying the
    # client never originates it — the trigger belongs to the destination.
    #
    # The bound is the read line, not the info string: CL_ConnectionlessPacket
    # reads the command with MSG_ReadStringLine, which truncates at
    # MAX_STRING_CHARS - 1 (code/qcommon/msg.c:508,526), and the command token
    # plus its separating space consume five of those bytes.
    echo_argument_bytes = engine_value("MAX_STRING_CHARS") - 1 - len("echo ")
    echo_terms = (
        ("out-of-band prefix", OUT_OF_BAND_PREFIX_BYTES),
        (
            'Cmd_Argv(1), the read line less "echo "',
            echo_argument_bytes,
        ),
    )

    browser_note = (
        "the profile keeps the server browser off the relayed path: the browser "
        "client is launched at one pinned virtual destination and issues no "
        "server-browser query, so this class never traverses the relay"
    )
    cases = (
        BoundaryCase(
            name="statusResponse",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(status_terms),
            terms=status_terms,
            citation="code/server/sv_main.c:533-590",
            variable_term="serverinfo, MAX_INFO_STRING - 1",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=profile.server_browser_queries_on_relay_path,
            off_path_reason=(
                "" if profile.server_browser_queries_on_relay_path else browser_note
            ),
            note=(
                "the largest datagram this profile can emit, and larger than "
                "MAX_PACKETLEN itself: the status buffer is MAX_MSGLEN "
                "(sv_main.c:535), so eight player lines never trip the loop's "
                "own overflow guard, and nothing on this path applies a "
                "packet-sized ceiling"
            ),
        ),
        BoundaryCase(
            name="infoResponse",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(info_terms),
            terms=info_terms,
            citation="code/server/sv_main.c:604,687",
            variable_term="serverinfo, MAX_INFO_STRING - 1",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=profile.server_browser_queries_on_relay_path,
            off_path_reason=(
                "" if profile.server_browser_queries_on_relay_path else browser_note
            ),
            note=(
                "SVC_Info builds its own MAX_INFO_STRING serverinfo, so the bound "
                "is the info string rather than the player list"
            ),
        ),
        BoundaryCase(
            name="challengeResponse",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(challenge_terms),
            terms=challenge_terms,
            citation="code/server/sv_client.c:200-201",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            note="three integers, so it is small under every candidate budget",
        ),
        BoundaryCase(
            name="connectResponse",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(connect_response_terms),
            terms=connect_response_terms,
            citation="code/server/sv_client.c:574",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            note="one integer",
        ),
        BoundaryCase(
            name="print",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(print_terms),
            terms=print_terms,
            citation="code/server/sv_client.c:395-397,566",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            note=(
                "every rejection the server can send before a netchan exists. The "
                "engine's own literals top out at this size; the game module's "
                "reject string also reaches the same format and the engine bounds "
                "it by nothing smaller than the out-of-band buffer, so this figure "
                "holds only for the committed QVM, whose longest is 32 bytes"
            ),
        ),
        BoundaryCase(
            name="connect",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(connect_terms),
            terms=connect_terms,
            citation="code/client/cl_main.c:2370-2428",
            variable_term="userinfo, MAX_INFO_STRING - 1",
            code_ceiling_bytes=OOB_DATA_CEILING_BYTES,
            ceiling_citation=OOB_DATA_CEILING_CITATION,
            compressed=True,
            note=(
                "the largest datagram the client can originate, and the one that "
                "decides whether a connection can be made at all. "
                "NET_OutOfBandData Huffman-compresses the payload and reserves "
                "twice MAX_MSGLEN for the result, so compression is not a bound: "
                "the figure is the pre-compression image plus the coder's "
                "worst-case expansion, and the emitted size has to be checked "
                "rather than assumed"
            ),
        ),
        BoundaryCase(
            name="getchallenge",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(get_challenge_terms),
            terms=get_challenge_terms,
            citation="code/client/cl_main.c:2404-2406",
            code_ceiling_bytes=GETCHALLENGE_BUFFER_CEILING_BYTES,
            ceiling_citation="code/client/cl_main.c:2373",
            note=(
                "the realized size in the fixed profile, where com_gamename keeps "
                "its GAMENAME_FOR_MASTER default and the derivation therefore "
                "reproduces the census's observed 40-byte maximum exactly. The "
                "ceiling beside it is what bounds the command in general: the "
                "MAX_INFO_STRING + 10 buffer it is formatted into, which "
                "Com_sprintf truncates to"
            ),
        ),
        BoundaryCase(
            name="getstatus",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(get_status_terms),
            terms=get_status_terms,
            citation="code/client/cl_main.c:4063,4075,4678",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=profile.server_browser_queries_on_relay_path,
            off_path_reason=(
                "" if profile.server_browser_queries_on_relay_path else browser_note
            ),
            note="a fixed literal; it is the request whose answer does not fit",
        ),
        BoundaryCase(
            name="getinfo",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(get_info_terms),
            terms=get_info_terms,
            citation="code/client/cl_main.c:4522,4590",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=profile.server_browser_queries_on_relay_path,
            off_path_reason=(
                "" if profile.server_browser_queries_on_relay_path else browser_note
            ),
            note="a fixed literal",
        ),
        BoundaryCase(
            name="echo",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(echo_terms),
            terms=echo_terms,
            citation="code/client/cl_main.c:2784-2791",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=True,
            note=(
                "the only out-of-band class the destination can elicit from the "
                "client, so it cannot be excluded the way the server-browser "
                "classes are. The client answers an `echo` from its own server "
                "address by sending the argument straight back. On the relayed "
                "path the eliciting datagram is itself budget-bounded and the "
                "reply is strictly smaller than it, but WP2's method cannot "
                "attribute a budget per direction, so that is not a bound this "
                "decision may rely on: the emitted-size check is"
            ),
        ),
        BoundaryCase(
            name="print-rcon-redirect",
            direction=SERVER_TO_CLIENT,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(rcon_print_terms),
            terms=rcon_print_terms,
            citation="code/server/sv_main.c:696-698,714-715,744",
            code_ceiling_bytes=OOB_PRINT_CEILING_BYTES,
            ceiling_citation=OOB_PRINT_CEILING_CITATION,
            on_relay_path=False,
            off_path_reason=(
                "it is the answer to an rcon command, and the profile sets no "
                "rcon password and exposes no client rcon command, so nothing "
                "can elicit it"
            ),
            note=(
                "the rcon answer, bounded by the redirect buffer rather than by "
                "the out-of-band path; listed so that both consequences of rcon "
                "are visible together"
            ),
        ),
        BoundaryCase(
            name="rcon",
            direction=CLIENT_TO_SERVER,
            kind=CONNECTIONLESS,
            fragmentable=False,
            inner_bytes=_sum_terms(rcon_terms),
            terms=rcon_terms,
            citation="code/client/cl_main.c:1850-1892",
            code_ceiling_bytes=1024,
            ceiling_citation="code/client/cl_main.c:1779,1851",
            on_relay_path=False,
            off_path_reason=(
                "the profile sets no rcon password and the browser client exposes "
                "no rcon command, so this class cannot be originated"
            ),
            note=(
                "listed because it is the one out-of-band class that bypasses "
                "NET_OutOfBandPrint and calls NET_SendPacket directly, so a future "
                "profile that enables it would also bypass any check placed there"
            ),
        ),
    )
    return cases


def netchan_boundary_cases(fragment_size: int) -> tuple[BoundaryCase, ...]:
    """Derive the largest netchan datagram each direction and framing can emit."""
    cases: list[BoundaryCase] = []
    for direction in DIRECTIONS:
        unfragmented_header = netchan_header_bytes(direction, fragmented=False)
        fragmented_header = netchan_header_bytes(direction, fragmented=True)
        cases.append(
            BoundaryCase(
                name="netchan-unfragmented",
                direction=direction,
                kind=NETCHAN,
                fragmentable=True,
                inner_bytes=largest_unfragmented_datagram_bytes(
                    fragment_size, direction
                ),
                terms=(
                    ("message, FRAGMENT_SIZE - 1", fragment_size - 1),
                    ("netchan header", unfragmented_header),
                ),
                citation="code/qcommon/net_chan.c:179-232",
                code_ceiling_bytes=engine_value("MAX_PACKETLEN"),
                ceiling_citation="code/qcommon/net_chan.c:179",
                note=(
                    "the fragmentation test is >= FRAGMENT_SIZE (net_chan.c:187), "
                    "so a message one byte short of it still goes in one piece"
                ),
            )
        )
        cases.append(
            BoundaryCase(
                name="netchan-fragment",
                direction=direction,
                kind=NETCHAN,
                fragmentable=True,
                inner_bytes=largest_fragment_datagram_bytes(fragment_size, direction),
                terms=(
                    ("fragment payload, FRAGMENT_SIZE", fragment_size),
                    ("netchan header with fragment fields", fragmented_header),
                ),
                citation="code/qcommon/net_chan.c:107-165",
                code_ceiling_bytes=engine_value("MAX_PACKETLEN"),
                ceiling_citation="code/qcommon/net_chan.c:111",
                note=(
                    "the binding netchan case in both directions, and the one a "
                    "fragment-size change moves"
                ),
            )
        )
    return tuple(cases)


# --------------------------------------------------------------------------
# Strategy evaluation
# --------------------------------------------------------------------------


def _fits(inner_bytes: int, budget: int) -> bool:
    return inner_bytes <= budget


@dataclass(frozen=True)
class SizingTarget:
    """One candidate inner-datagram budget and the fragment size it implies."""

    key: str
    label: str
    budget_bytes: int
    basis: str
    reserve_bytes: int
    alignment_bytes: int
    candidate_fragment_size: int
    largest_datagram_bytes: int
    largest_frame_bytes: int
    margin_bytes: int
    binding_direction: str

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "innerBudgetBytes": self.budget_bytes,
            "basis": self.basis,
            "reserveBytes": self.reserve_bytes,
            "alignmentBytes": self.alignment_bytes,
            "candidateFragmentSize": self.candidate_fragment_size,
            "largestDatagramBytes": self.largest_datagram_bytes,
            "largestFrameBytes": self.largest_frame_bytes,
            "marginBytes": self.margin_bytes,
            "bindingDirection": self.binding_direction,
        }


DEFAULT_RESERVE_BYTES = 64
DEFAULT_ALIGNMENT_BYTES = 64


def candidate_fragment_size(
    budget_bytes: int,
    *,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    alignment_bytes: int = DEFAULT_ALIGNMENT_BYTES,
) -> int:
    """Return the proposed FRAGMENT_SIZE for one inner-datagram budget.

    The rule is deliberately arithmetic rather than judged, so that a reviewer
    who disagrees with the reserve can change one number and recompute: take the
    budget, subtract a fixed reserve that covers the widest netchan header with
    room to spare, and round down to a multiple of the alignment so the value
    stays a round number in the source. The reserve is not a header calculation —
    the widest header is 14 bytes — it is header plus deliberate headroom, which
    is what makes it a safety margin rather than an exact fit.
    """
    _require_positive(budget_bytes, "inner budget")
    _require_positive(reserve_bytes, "reserve")
    _require_positive(alignment_bytes, "alignment")
    usable = budget_bytes - reserve_bytes
    if usable <= 0:
        _fail(
            f"an inner budget of {budget_bytes} bytes leaves nothing after a "
            f"{reserve_bytes} byte reserve"
        )
    size = (usable // alignment_bytes) * alignment_bytes
    if size <= 0:
        _fail(
            f"an inner budget of {budget_bytes} bytes rounds down to zero at "
            f"{alignment_bytes} byte alignment"
        )
    return size


def build_sizing_target(
    key: str,
    label: str,
    budget_bytes: int,
    basis: str,
    framing: RelayFraming,
    *,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    alignment_bytes: int = DEFAULT_ALIGNMENT_BYTES,
) -> SizingTarget:
    size = candidate_fragment_size(
        budget_bytes,
        reserve_bytes=reserve_bytes,
        alignment_bytes=alignment_bytes,
    )
    largest = largest_engine_datagram_bytes(size)
    binding = max(
        DIRECTIONS,
        key=lambda direction: largest_fragment_datagram_bytes(size, direction),
    )
    return SizingTarget(
        key=key,
        label=label,
        budget_bytes=budget_bytes,
        basis=basis,
        reserve_bytes=reserve_bytes,
        alignment_bytes=alignment_bytes,
        candidate_fragment_size=size,
        largest_datagram_bytes=largest,
        largest_frame_bytes=framing.frame_bytes(largest),
        margin_bytes=budget_bytes - largest,
        binding_direction=binding,
    )


def _class_fit_rows(
    cases: Iterable[BoundaryCase | ObservedClass],
    budgets: dict[str, int],
) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        row = case.as_json()
        row["fits"] = {
            key: _fits(case.inner_bytes, budget) for key, budget in budgets.items()
        }
        rows.append(row)
    return rows


def derive(
    routed_report: Any,
    census: Any,
    *,
    plan: Any = None,
    profile: PrototypeProfile = PROTOTYPE_PROFILE,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    alignment_bytes: int = DEFAULT_ALIGNMENT_BYTES,
) -> dict[str, Any]:
    """Recompute the whole WP6 decision from the two committed records."""
    path = read_path_facts(routed_report, plan)
    facts = read_census_facts(census)
    framing = path.framing

    targets = {
        "recordBackedFloor": build_sizing_target(
            "recordBackedFloor",
            "record-backed contiguous floor",
            path.record_backed_inner_floor_bytes,
            (
                "the largest single inner datagram every measured session echoed, "
                "with every smaller planned size also echoed"
            ),
            framing,
            reserve_bytes=reserve_bytes,
            alignment_bytes=alignment_bytes,
        ),
        "derivedReportedMaximum": build_sizing_target(
            "derivedReportedMaximum",
            "derived reported-maximum budget",
            path.derived_inner_budget_bytes,
            (
                "the transport's reported datagram maximum less the relay's "
                "single-datagram overhead; never exercised above the floor"
            ),
            framing,
            reserve_bytes=reserve_bytes,
            alignment_bytes=alignment_bytes,
        ),
    }
    budgets = {key: target.budget_bytes for key, target in targets.items()}

    stock_netchan = netchan_boundary_cases(facts.fragment_size)
    connectionless = connectionless_boundary_cases(profile)
    stock_largest = largest_engine_datagram_bytes(facts.fragment_size)

    # Strategy 1 — intact datagrams, no engine change.
    intact = {
        "requiredInnerBytes": stock_largest,
        "requiredFrameBytes": framing.frame_bytes(stock_largest),
        "observedRequiredInnerBytes": max(facts.maximum_by_direction.values()),
        "observedRequiredFrameBytes": framing.frame_bytes(
            max(facts.maximum_by_direction.values())
        ),
        "viableAtRecordBackedFloor": _fits(stock_largest, budgets["recordBackedFloor"]),
        "viableAtDerivedBudget": _fits(
            stock_largest, budgets["derivedReportedMaximum"]
        ),
        "observedTrafficViableAtRecordBackedFloor": _fits(
            max(facts.maximum_by_direction.values()), budgets["recordBackedFloor"]
        ),
        "observedTrafficViableAtDerivedBudget": _fits(
            max(facts.maximum_by_direction.values()),
            budgets["derivedReportedMaximum"],
        ),
        "refutedByMeasuredCases": _refuting_cases(
            routed_report,
            sorted(
                {
                    max(facts.maximum_by_direction.values()),
                    stock_largest,
                    budgets["derivedReportedMaximum"] + 1,
                }
            ),
        ),
    }

    # Strategy 2 — symmetric fragment-size reduction, evaluated at both targets.
    gamestates = [
        message
        for message in facts.fragmented_messages
        if isinstance(message.get("messageBytes"), int)
    ]
    reduction: dict[str, Any] = {}
    observed_connectionless = tuple(
        item for item in facts.observed if item.kind == CONNECTIONLESS
    )
    for key, target in targets.items():
        # The observed netchan maxima are deliberately not scored here. A
        # netchan datagram's size is a function of FRAGMENT_SIZE, so the
        # census's 1,312-byte maximum is a fact about the stock fragment size
        # and not a shape that survives into a candidate one; the derived
        # netchan cases at the candidate size are what bound this traffic.
        # Observed connectionless sizes are scored, because those are real
        # shapes that no fragment-size change moves.
        rows = _class_fit_rows(
            netchan_boundary_cases(target.candidate_fragment_size)
            + connectionless
            + observed_connectionless,
            budgets,
        )
        # The verdict comes from the derived boundary cases alone. Each of them
        # is an upper bound on the matching observed class — the suite asserts
        # that — so scoring the observed sizes as well would either say the same
        # thing or name the same class twice under two different applicabilities.
        # The observed sizes stay in `rows` for the fit table, and any that were
        # over budget would be reported separately rather than silently merged.
        over_budget = [
            case
            for case in connectionless
            if not _fits(case.inner_bytes, target.budget_bytes)
        ]
        # An out-of-band class that cannot reach the relay is not a transport
        # problem, but it is only kept away by the profile, so the two
        # populations are reported separately rather than merged into one count.
        unfit = [case.name for case in over_budget if case.on_relay_path]
        unfit_off_path = [
            case.name for case in over_budget if not case.on_relay_path
        ]
        observed_over_budget = [
            item.name
            for item in observed_connectionless
            if not _fits(item.inner_bytes, target.budget_bytes)
        ]
        reduction[key] = {
            "target": target.as_json(),
            "fitsEveryNetchanCase": all(
                row["fits"][key] for row in rows if row["kind"] == NETCHAN
            ),
            "connectionlessCasesOverBudget": unfit,
            "connectionlessCasesOverBudgetOffRelayPath": unfit_off_path,
            "observedConnectionlessOverBudget": observed_over_budget,
            "requiresProfileBounds": bool(unfit),
            # For every out-of-band class that is over budget, the largest its
            # one profile-controllable term may be for the class to fit. This is
            # the number WP7 has to enforce; a value at or below zero would mean
            # no cap can rescue the class and the strategy fails on it.
            "requiredProfileCaps": [
                {
                    "name": case.name,
                    "direction": case.direction,
                    "term": case.variable_term,
                    "fixedBytes": case.fixed_bytes,
                    "requiredCapBytes": case.required_cap_bytes(target.budget_bytes),
                    "onRelayPath": case.on_relay_path,
                    "achievable": (case.required_cap_bytes(target.budget_bytes) or 0)
                    > 0,
                }
                for case in connectionless
                if case.variable_term
                and not _fits(case.inner_bytes, target.budget_bytes)
            ],
            "messageCosts": [
                {
                    "source": "census fragmentedMessages",
                    "direction": message.get("direction", SERVER_TO_CLIENT),
                    "atStockFragmentSize": message_cost(
                        message["messageBytes"],
                        facts.fragment_size,
                        message.get("direction", SERVER_TO_CLIENT),
                        framing,
                    ).as_json(),
                    "atCandidateFragmentSize": message_cost(
                        message["messageBytes"],
                        target.candidate_fragment_size,
                        message.get("direction", SERVER_TO_CLIENT),
                        framing,
                    ).as_json(),
                }
                for message in gamestates
            ],
            "worstCaseMessageCost": message_cost(
                facts.max_msg_len,
                target.candidate_fragment_size,
                SERVER_TO_CLIENT,
                framing,
            ).as_json(),
            # How much ordinary traffic the smaller fragment size newly splits.
            # The census's largest datagram that is not already a fragment
            # answers this: if it stays below the candidate size, nothing except
            # the messages that already fragmented begins to fragment.
            "newlyFragmentingObservedTraffic": {
                direction: {
                    "largestNonFragmentDatagramBytes": largest,
                    "largestNonFragmentMessageBytes": max(
                        largest - facts.header_bytes[(direction, False)], 0
                    ),
                    "beginsToFragment": (
                        max(largest - facts.header_bytes[(direction, False)], 0)
                        >= target.candidate_fragment_size
                    ),
                }
                for direction, largest in (
                    facts.largest_unfragmented_by_direction.items()
                )
            },
        }

    # Strategy 3 — bounded engine-pair tunnel fragmentation.
    still_over = sorted(
        {
            name
            for key in targets
            for name in reduction[key]["connectionlessCasesOverBudget"]
        }
    )
    tunnel = {
        "requiredForNetchanTraffic": not all(
            reduction[key]["fitsEveryNetchanCase"] for key in targets
        ),
        "connectionlessCasesItWouldCover": still_over,
        "requiredIfProfileBoundsAreRejected": bool(still_over),
        "note": (
            "netchan traffic needs no tunnel once FRAGMENT_SIZE fits the budget, "
            "because the engine already fragments and reassembles it. A tunnel is "
            "only a candidate for the out-of-band classes, which the engine never "
            "fragments — and for those, bounding the profile is a smaller change "
            "than adding a second reassembly path on both endpoints."
        ),
    }

    return {
        "kind": "arena-web-network-sizing-derivation",
        "formatVersion": 1,
        "engine": {
            "commit": ENGINE_COMMIT,
            "censusCommit": ENGINE_CENSUS_COMMIT,
            "constants": [constant.as_json() for constant in ENGINE_CONSTANTS],
            "netchanHeaderBytes": {
                f"{direction}{'-fragmented' if fragmented else ''}": (
                    netchan_header_bytes(direction, fragmented=fragmented)
                )
                for direction in DIRECTIONS
                for fragmented in (False, True)
            },
        },
        "path": path.as_json(),
        "census": facts.as_json(),
        "profile": profile.as_json(),
        "budgets": budgets,
        "boundaryCases": _class_fit_rows(stock_netchan + connectionless, budgets),
        "observedClasses": _class_fit_rows(facts.observed, budgets),
        "strategies": {
            "intactDatagrams": intact,
            "symmetricFragmentSizeReduction": reduction,
            "boundedTunnelFragmentation": tunnel,
        },
    }


def _refuting_cases(report: Any, sizes: Sequence[int]) -> list[dict[str, Any]]:
    """Find measured cases at exactly these inner sizes, and say what happened.

    A refutation carries much more weight when the record already contains the
    attempt: the sizes an unchanged engine needs were not merely computed to be
    over budget, they were sent to a real relay and refused.
    """
    wanted = set(sizes)
    found: dict[int, dict[str, Any]] = {}
    for session in report.get("sessions") or []:
        for case in session.get("cases") or []:
            if case.get("kind") != "single":
                continue
            inner = case.get("sentInnerBytes") or []
            if len(inner) != 1 or inner[0] not in wanted:
                continue
            found.setdefault(
                inner[0],
                {
                    "innerBytes": inner[0],
                    "frameBytes": case.get("sentFrameBytes"),
                    "outcome": case.get("outcome"),
                },
            )
    return [found[size] for size in sorted(found)]


__all__ = [
    "BoundaryCase",
    "CensusFacts",
    "CLIENT_TO_SERVER",
    "CONNECTIONLESS",
    "DEFAULT_ALIGNMENT_BYTES",
    "DEFAULT_RESERVE_BYTES",
    "ENGINE_COMMIT",
    "ENGINE_CONSTANTS",
    "MessageCost",
    "NETCHAN",
    "NetworkSizingError",
    "ObservedClass",
    "PROTOTYPE_PROFILE",
    "PathFacts",
    "PrototypeProfile",
    "RelayFraming",
    "SERVER_TO_CLIENT",
    "SizingTarget",
    "build_sizing_target",
    "candidate_fragment_size",
    "connectionless_boundary_cases",
    "derive",
    "engine_constant",
    "engine_value",
    "fragment_payloads",
    "largest_engine_datagram_bytes",
    "largest_fragment_datagram_bytes",
    "largest_unfragmented_datagram_bytes",
    "message_cost",
    "netchan_boundary_cases",
    "netchan_header_bytes",
    "read_census_facts",
    "read_path_facts",
]
