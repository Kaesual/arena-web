# SPDX-License-Identifier: GPL-2.0-or-later
"""The portable conformance vectors for the routed datagram contract.

`docs/relay-datagram-contract.md` describes the frame grammar in prose; this
module expresses the same thing as bytes, so that a second implementation can be
checked against it without reading this one. The browser probe runs the emitted
file through its own JavaScript before it is allowed to open a session, which is
what keeps the two implementations from drifting apart.

Every address here is drawn from the IPv6 documentation prefix, every routing
prefix used as pure frame grammar is a synthetic byte pattern, and every payload
is the contract's own deterministic filler. No real endpoint, address, port,
certificate or authorization value appears in the vectors, and none may be
added.
"""

from __future__ import annotations

import json

from relay_loopback import (
    SYNTHETIC_CLIENT_ADDRESS,
    SYNTHETIC_CLIENT_PORT,
    SYNTHETIC_DESTINATION_ADDRESS,
    SYNTHETIC_DESTINATION_PORT,
    SYNTHETIC_FOREIGN_ADDRESS,
    SYNTHETIC_OTHER_CLIENT_ADDRESS,
    SYNTHETIC_PREFIX,
    SYNTHETIC_RETURN_PREFIX,
)
from relay_probe import (
    ADDRESS_ASSIGNED_BYTES,
    BROWSER_TO_SERVER,
    DATAGRAM_TYPE_BYTES,
    ERROR_DESTINATION_UNAVAILABLE,
    ERROR_INVALID_AUTHORIZATION,
    ERROR_PREAMBLE_BYTES,
    LENGTH_PREFIX_BYTES,
    MAX_LENGTH_PREFIX_VALUE,
    MINIMUM_TAGGED_INNER_BYTES,
    NONCE_BYTES,
    ORDINAL_BYTES,
    RELAY_HEADER_BYTES,
    SERVER_TO_BROWSER,
    SESSION_NONCE_BYTES,
    SINGLE_DATAGRAM_OVERHEAD_BYTES,
    TYPE_ADDRESS_ASSIGNED,
    TYPE_ERROR,
    TYPE_KEEP_ALIVE,
    TYPE_RELAY_PACKET,
    TYPE_REQUEST_ADDRESS,
    VIRTUAL_ADDRESS_BYTES,
    RelayHeader,
    RoutingContext,
    build_payload,
    datagram_tag,
    encode_address_assignment,
    encode_address_request,
    encode_error,
    encode_frame,
    encode_keep_alive,
    encode_relay_header,
    frame_bytes_for_sizes,
)

VECTOR_KIND = "arena-web-routed-datagram-conformance-vectors"
# Version 2 is the 2026-08-30 in-band session profile, which added the datagram
# types, the structured relay header and the session-setup vectors.
VECTOR_FORMAT_VERSION = 2

# A fixed, obviously synthetic session nonce. Real sessions draw 12 random bytes.
VECTOR_SESSION_NONCE = bytes.fromhex("00112233445566778899aabb")
OTHER_SESSION_NONCE = bytes.fromhex("ffeeddccbbaa998877665544")

# Synthetic one-time values standing in for the opaque authorization an
# integration environment issues. The last one is deliberately non-ASCII so that
# two implementations have to agree on UTF-8 rather than on Latin-1.
VECTOR_AUTHORIZATIONS = (
    ("plain", "vector-one-time-authorization"),
    ("dollarPatterns", "$&$1$`"),
    ("nonAscii", "tökén-ünïcøde"),
)

_SINGLE_SIZES = (0, 1, 15, 16, 17, 64, 1300, 1314)
_PACKED_SIZES = ((16, 17), (64, 64), (16, 1300))
_TAG_ORDINALS = (0, 1, 7, 4294967295)

_VECTOR_ROUTING = RoutingContext(
    client_address=SYNTHETIC_CLIENT_ADDRESS,
    client_port=SYNTHETIC_CLIENT_PORT,
    destination_address=SYNTHETIC_DESTINATION_ADDRESS,
    destination_port=SYNTHETIC_DESTINATION_PORT,
)


def _encode_cases() -> list:
    cases = []
    for size in _SINGLE_SIZES:
        payload = build_payload(VECTOR_SESSION_NONCE, 0, size)
        cases.append(
            {
                "direction": BROWSER_TO_SERVER,
                "frameBytes": frame_bytes_for_sizes((size,)),
                "frameHex": encode_frame(
                    SYNTHETIC_PREFIX, (payload,), BROWSER_TO_SERVER
                ).hex(),
                "name": f"browserSingle{size}",
                "payloadHexes": [payload.hex()],
                "prefixHex": SYNTHETIC_PREFIX.hex(),
            }
        )
        cases.append(
            {
                "direction": SERVER_TO_BROWSER,
                "frameBytes": frame_bytes_for_sizes((size,)),
                "frameHex": encode_frame(
                    SYNTHETIC_RETURN_PREFIX, (payload,), SERVER_TO_BROWSER
                ).hex(),
                "name": f"serverSingle{size}",
                "payloadHexes": [payload.hex()],
                "prefixHex": SYNTHETIC_RETURN_PREFIX.hex(),
            }
        )
    for sizes in _PACKED_SIZES:
        payloads = [
            build_payload(VECTOR_SESSION_NONCE, ordinal, size)
            for ordinal, size in enumerate(sizes)
        ]
        cases.append(
            {
                "direction": BROWSER_TO_SERVER,
                "frameBytes": frame_bytes_for_sizes(sizes),
                "frameHex": encode_frame(
                    SYNTHETIC_PREFIX, payloads, BROWSER_TO_SERVER
                ).hex(),
                "name": "browserPacked" + "x".join(str(size) for size in sizes),
                "payloadHexes": [payload.hex() for payload in payloads],
                "prefixHex": SYNTHETIC_PREFIX.hex(),
            }
        )
    return cases


def _decode_rejections() -> list:
    prefix = SYNTHETIC_RETURN_PREFIX
    one = build_payload(VECTOR_SESSION_NONCE, 0, 16)
    single = encode_frame(prefix, (one,), SERVER_TO_BROWSER)
    return [
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": prefix[:-1].hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "shorterThanRoutingPrefix",
            "rule": "a frame carries at least 40 header bytes",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": "",
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "emptyFrame",
            "rule": "a frame carries at least 40 header bytes",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": prefix.hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "serverFrameWithoutDatagram",
            "rule": "a server-to-browser frame carries exactly one inner datagram",
        },
        {
            "direction": BROWSER_TO_SERVER,
            "frameHex": SYNTHETIC_PREFIX.hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "browserFrameWithoutDatagram",
            "rule": "a browser-to-server frame carries at least one inner datagram",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": encode_frame(prefix, (one, one), BROWSER_TO_SERVER).hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "serverFrameWithTwoDatagrams",
            "rule": "a server-to-browser frame carries exactly one inner datagram",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": (prefix + b"\x00").hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "endsInsideLengthPrefix",
            "rule": "a frame does not end inside a length prefix",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": single[:-1].hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "endsInsideInnerDatagram",
            "rule": "a frame does not end inside an inner datagram",
        },
        {
            "direction": BROWSER_TO_SERVER,
            "frameHex": (single + b"\x00").hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "trailingByteAfterLastDatagram",
            "rule": "a frame carries no trailing byte after the last inner "
            "payload; one trailing byte cannot begin a length field",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": (prefix + b"\xff\xff" + b"\x00\x01\x02\x03").hex(),
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "declaredLengthBeyondFrame",
            "rule": "a declared length is checked against the bytes present "
            "before anything is allocated",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "frameHex": (prefix + b"\x04\x00" + bytes(1024)).hex(),
            "maxInnerDatagramBytes": 1023,
            "name": "declaredLengthAboveCeiling",
            "rule": "an inner datagram above the accepted ceiling is refused",
        },
    ]


def _decode_acceptances() -> list:
    """Cases that must be accepted, including exactly at the ceiling.

    The rejection vectors alone let an implementation refuse a datagram *at*
    the ceiling and still pass, which would then refuse the measurement plan's
    largest size. These pin the boundary from the other side.
    """
    prefix = SYNTHETIC_RETURN_PREFIX
    cases = []
    for size in (0, 1, 1023):
        payload = build_payload(VECTOR_SESSION_NONCE, 0, size)
        cases.append(
            {
                "direction": SERVER_TO_BROWSER,
                "frameHex": encode_frame(
                    prefix, (payload,), SERVER_TO_BROWSER, size
                ).hex(),
                "maxInnerDatagramBytes": size,
                "name": f"innerDatagramExactlyAtCeiling{size}",
                "payloadHexes": [payload.hex()],
                "prefixHex": prefix.hex(),
            }
        )
    packed = [build_payload(VECTOR_SESSION_NONCE, index, 64) for index in range(2)]
    cases.append(
        {
            "direction": BROWSER_TO_SERVER,
            "frameHex": encode_frame(
                SYNTHETIC_PREFIX, packed, BROWSER_TO_SERVER, 64
            ).hex(),
            "maxInnerDatagramBytes": 64,
            "name": "packedDatagramsExactlyAtCeiling64",
            "payloadHexes": [payload.hex() for payload in packed],
            "prefixHex": SYNTHETIC_PREFIX.hex(),
        }
    )
    return cases


def _encode_rejections() -> list:
    one = build_payload(VECTOR_SESSION_NONCE, 0, 16)
    return [
        {
            "direction": BROWSER_TO_SERVER,
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "routingPrefixTooShort",
            "payloadHexes": [one.hex()],
            "prefixHex": SYNTHETIC_PREFIX[:-1].hex(),
            "rule": "the routing prefix is exactly 40 bytes",
        },
        {
            "direction": BROWSER_TO_SERVER,
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "routingPrefixTooLong",
            "payloadHexes": [one.hex()],
            "prefixHex": (SYNTHETIC_PREFIX + b"\x00").hex(),
            "rule": "the routing prefix is exactly 40 bytes",
        },
        {
            "direction": SERVER_TO_BROWSER,
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "packedServerFrame",
            "payloadHexes": [one.hex(), one.hex()],
            "prefixHex": SYNTHETIC_RETURN_PREFIX.hex(),
            "rule": "packed frames exist only in the browser-to-server direction",
        },
        {
            "direction": BROWSER_TO_SERVER,
            "maxInnerDatagramBytes": MAX_LENGTH_PREFIX_VALUE,
            "name": "emptyBrowserFrame",
            "payloadHexes": [],
            "prefixHex": SYNTHETIC_PREFIX.hex(),
            "rule": "a browser-to-server frame carries at least one inner datagram",
        },
        {
            "direction": BROWSER_TO_SERVER,
            "maxInnerDatagramBytes": 15,
            "name": "innerDatagramAboveCeiling",
            "payloadHexes": [one.hex()],
            "prefixHex": SYNTHETIC_PREFIX.hex(),
            "rule": "an inner datagram above the accepted ceiling is refused",
        },
    ]


def _routing(context: RoutingContext) -> dict:
    return {
        "clientAddressHex": context.client_address.hex(),
        "clientPort": context.client_port,
        "destinationAddressHex": context.destination_address.hex(),
        "destinationPort": context.destination_port,
    }


def _header_cases() -> list:
    """The outbound header a routing context produces, byte for byte."""
    contexts = (
        ("primaryDestination", _VECTOR_ROUTING),
        (
            "otherClient",
            RoutingContext(
                client_address=SYNTHETIC_OTHER_CLIENT_ADDRESS,
                client_port=SYNTHETIC_CLIENT_PORT + 1,
                destination_address=SYNTHETIC_DESTINATION_ADDRESS,
                destination_port=SYNTHETIC_DESTINATION_PORT,
            ),
        ),
        (
            "extremePorts",
            RoutingContext(
                client_address=SYNTHETIC_CLIENT_ADDRESS,
                client_port=65535,
                destination_address=SYNTHETIC_DESTINATION_ADDRESS,
                destination_port=1,
            ),
        ),
    )
    return [
        dict(_routing(context), name=name, headerHex=context.outbound_header().hex())
        for name, context in contexts
    ]


def _header_rejections() -> list:
    """Bytes that are not a relay header at all."""
    header = _VECTOR_ROUTING.outbound_header()
    keep_alive_header = bytes.fromhex("00000005") + header[4:]
    return [
        {
            "headerHex": header[:-1].hex(),
            "name": "headerOneByteShort",
            "rule": f"a relay header is exactly {RELAY_HEADER_BYTES} bytes",
        },
        {
            "headerHex": (header + b"\x00").hex(),
            "name": "headerOneByteLong",
            "rule": f"a relay header is exactly {RELAY_HEADER_BYTES} bytes",
        },
        {
            "headerHex": keep_alive_header.hex(),
            "name": "headerWithForeignType",
            "rule": "a relay header opens with the relay-packet type",
        },
    ]


def _return_header_cases() -> list:
    """Return headers a session must accept, and ones it must not.

    Three of the four fields are known in advance; the return header's source
    port is the destination's own reply port and is deliberately not checked,
    which the `differingSourcePort` acceptance pins from the other side.
    """
    def header(**overrides) -> str:
        fields = {
            "destination_address": SYNTHETIC_CLIENT_ADDRESS,
            "destination_port": SYNTHETIC_CLIENT_PORT,
            "source_address": SYNTHETIC_DESTINATION_ADDRESS,
            "source_port": SYNTHETIC_DESTINATION_PORT,
        }
        fields.update(overrides)
        return encode_relay_header(RelayHeader(**fields)).hex()

    accepted = [
        ("matchingReturn", header()),
        ("differingSourcePort", header(source_port=SYNTHETIC_DESTINATION_PORT + 7)),
    ]
    rejected = [
        (
            "foreignDestinationAddress",
            header(destination_address=SYNTHETIC_OTHER_CLIENT_ADDRESS),
            "a return header names this session's assigned client address",
        ),
        (
            "foreignDestinationPort",
            header(destination_port=SYNTHETIC_CLIENT_PORT + 1),
            "a return header names the source port this session sends from",
        ),
        (
            "foreignSourceAddress",
            header(source_address=SYNTHETIC_FOREIGN_ADDRESS),
            "a return header names the pinned virtual destination",
        ),
    ]
    return [
        [
            dict(_routing(_VECTOR_ROUTING), name=name, returnHeaderHex=value)
            for name, value in accepted
        ],
        [
            dict(
                _routing(_VECTOR_ROUTING),
                name=name,
                returnHeaderHex=value,
                rule=rule,
            )
            for name, value, rule in rejected
        ],
    ]


def _address_request_cases() -> list:
    return [
        {
            "authorization": value,
            "datagramHex": encode_address_request(value).hex(),
            "name": f"addressRequest{name[0].upper()}{name[1:]}",
        }
        for name, value in VECTOR_AUTHORIZATIONS
    ]


def _address_assignment_cases() -> list:
    return [
        {
            "addressHex": address.hex(),
            "datagramHex": encode_address_assignment(address).hex(),
            "name": name,
        }
        for name, address in (
            ("addressAssignedClient", SYNTHETIC_CLIENT_ADDRESS),
            ("addressAssignedOtherClient", SYNTHETIC_OTHER_CLIENT_ADDRESS),
        )
    ]


def _error_cases() -> list:
    return [
        {
            "code": code,
            "datagramHex": encode_error(code, message).hex(),
            "message": message,
            "name": name,
        }
        for name, code, message in (
            (
                "errorInvalidAuthorization",
                ERROR_INVALID_AUTHORIZATION,
                "Invalid or expired token",
            ),
            (
                "errorDestinationUnavailable",
                ERROR_DESTINATION_UNAVAILABLE,
                "Destination not connected",
            ),
            ("errorWithEmptyMessage", ERROR_INVALID_AUTHORIZATION, ""),
        )
    ]


def _keep_alive_cases() -> list:
    return [
        {
            "datagramHex": encode_keep_alive(padding).hex(),
            "name": name,
            "paddingHex": padding.hex(),
        }
        for name, padding in (
            ("keepAliveBare", b""),
            ("keepAlivePadded", bytes(range(16))),
        )
    ]


def _session_rejections() -> list:
    """Control datagrams a conforming client refuses.

    `decoder` names which of the two decoders must refuse the bytes, so an
    independent implementation can drive these without guessing.
    """
    assignment = encode_address_assignment(SYNTHETIC_CLIENT_ADDRESS)
    error = encode_error(ERROR_INVALID_AUTHORIZATION, "Invalid or expired token")
    return [
        {
            "datagramHex": assignment[:-1].hex(),
            "decoder": "addressAssignment",
            "name": "assignmentOneByteShort",
            "rule": f"an address assignment is exactly {ADDRESS_ASSIGNED_BYTES} bytes",
        },
        {
            "datagramHex": (assignment + b"\x00").hex(),
            "decoder": "addressAssignment",
            "name": "assignmentOneByteLong",
            "rule": f"an address assignment is exactly {ADDRESS_ASSIGNED_BYTES} bytes",
        },
        {
            "datagramHex": (
                bytes.fromhex("00000005") + assignment[DATAGRAM_TYPE_BYTES:]
            ).hex(),
            "decoder": "addressAssignment",
            "name": "assignmentWithForeignType",
            "rule": "an address assignment opens with the address-assigned type",
        },
        {
            "datagramHex": (
                assignment[:DATAGRAM_TYPE_BYTES] + bytes(VIRTUAL_ADDRESS_BYTES)
            ).hex(),
            "decoder": "addressAssignment",
            "name": "assignmentOfUnspecifiedAddress",
            "rule": "the unspecified address is not an assignment",
        },
        {
            "datagramHex": error[: ERROR_PREAMBLE_BYTES - 1].hex(),
            "decoder": "error",
            "name": "errorShorterThanPreamble",
            "rule": f"an error carries at least {ERROR_PREAMBLE_BYTES} bytes",
        },
        {
            "datagramHex": (error + b"\x00").hex(),
            "decoder": "error",
            "name": "errorMessageLengthDisagrees",
            "rule": "the declared message length describes the remaining bytes exactly",
        },
        {
            "datagramHex": (
                error[: ERROR_PREAMBLE_BYTES - 2] + b"\x00\x01" + b"\xff"
            ).hex(),
            "decoder": "error",
            "name": "errorMessageIsNotUtf8",
            "rule": "an error message is valid UTF-8",
        },
    ]


def build_conformance_vectors() -> dict:
    """Return the whole vector document."""
    return_acceptances, return_rejections = _return_header_cases()
    return {
        "addressAssignmentCases": _address_assignment_cases(),
        "addressRequestCases": _address_request_cases(),
        "datagramTypes": {
            "addressAssigned": TYPE_ADDRESS_ASSIGNED,
            "error": TYPE_ERROR,
            "keepAlive": TYPE_KEEP_ALIVE,
            "relayPacket": TYPE_RELAY_PACKET,
            "requestAddress": TYPE_REQUEST_ADDRESS,
        },
        "decodeAcceptances": _decode_acceptances(),
        "decodeRejections": _decode_rejections(),
        "encodeCases": _encode_cases(),
        "encodeRejections": _encode_rejections(),
        "errorCases": _error_cases(),
        "formatVersion": VECTOR_FORMAT_VERSION,
        "framing": {
            "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
            "datagramTypeBytes": DATAGRAM_TYPE_BYTES,
            "relayHeaderBytes": RELAY_HEADER_BYTES,
            "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
            "virtualAddressBytes": VIRTUAL_ADDRESS_BYTES,
        },
        "headerCases": _header_cases(),
        "headerRejections": _header_rejections(),
        "keepAliveCases": _keep_alive_cases(),
        "kind": VECTOR_KIND,
        "payloadCases": [
            {
                "name": f"payload{size}",
                "ordinal": ordinal,
                "payloadHex": build_payload(VECTOR_SESSION_NONCE, ordinal, size).hex(),
                "sessionNonceHex": VECTOR_SESSION_NONCE.hex(),
                "size": size,
            }
            for ordinal, size in enumerate(_SINGLE_SIZES)
        ],
        "payloadIdentification": {
            "minimumTaggedInnerBytes": MINIMUM_TAGGED_INNER_BYTES,
            "nonceBytes": NONCE_BYTES,
            "ordinalBytes": ORDINAL_BYTES,
            "placement": "payload-prefix",
            "sessionNonceBytes": SESSION_NONCE_BYTES,
        },
        "returnHeaderAcceptances": return_acceptances,
        "returnHeaderRejections": return_rejections,
        "sessionRejections": _session_rejections(),
        # Documentation-prefix addresses (RFC 3849) and arbitrary ports. Nothing
        # here is routable and nothing here describes any environment.
        "syntheticAddresses": {
            "clientAddressHex": SYNTHETIC_CLIENT_ADDRESS.hex(),
            "clientPort": SYNTHETIC_CLIENT_PORT,
            "destinationAddressHex": SYNTHETIC_DESTINATION_ADDRESS.hex(),
            "destinationPort": SYNTHETIC_DESTINATION_PORT,
            "foreignAddressHex": SYNTHETIC_FOREIGN_ADDRESS.hex(),
            "otherClientAddressHex": SYNTHETIC_OTHER_CLIENT_ADDRESS.hex(),
        },
        "syntheticPrefixHex": SYNTHETIC_PREFIX.hex(),
        "syntheticReturnPrefixHex": SYNTHETIC_RETURN_PREFIX.hex(),
        "tagCases": [
            {
                "name": f"tagSession{session}Ordinal{ordinal}",
                "ordinal": ordinal,
                "sessionNonceHex": nonce.hex(),
                "tagHex": datagram_tag(nonce, ordinal).hex(),
            }
            for session, nonce in enumerate((VECTOR_SESSION_NONCE, OTHER_SESSION_NONCE))
            for ordinal in _TAG_ORDINALS
        ],
    }


def encode_conformance_vectors() -> str:
    """Return the exact committed serialization of the vector document.

    The emitter and the test that guards the committed file share this, so the
    committed bytes are pinned rather than only the parsed structure.
    """
    return (
        json.dumps(
            build_conformance_vectors(), indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n"
    )
