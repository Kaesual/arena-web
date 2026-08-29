# SPDX-License-Identifier: GPL-2.0-or-later
"""The portable conformance vectors for the routed datagram contract.

`docs/relay-datagram-contract.md` describes the frame grammar in prose; this
module expresses the same thing as bytes, so that a second implementation can be
checked against it without reading this one. The browser probe runs the emitted
file through its own JavaScript before it is allowed to open a session, which is
what keeps the two implementations from drifting apart.

Every routing prefix here is a synthetic byte pattern and every payload is the
contract's own deterministic filler. No endpoint, address, port, certificate or
authorization value appears in the vectors, and none may be added.
"""

from __future__ import annotations

from relay_loopback import SYNTHETIC_PREFIX, SYNTHETIC_RETURN_PREFIX
from relay_probe import (
    BROWSER_TO_SERVER,
    LENGTH_PREFIX_BYTES,
    MAX_LENGTH_PREFIX_VALUE,
    MINIMUM_TAGGED_INNER_BYTES,
    NONCE_BYTES,
    ORDINAL_BYTES,
    RELAY_HEADER_BYTES,
    SERVER_TO_BROWSER,
    SESSION_NONCE_BYTES,
    SINGLE_DATAGRAM_OVERHEAD_BYTES,
    build_payload,
    datagram_tag,
    encode_frame,
    frame_bytes_for_sizes,
)

import json

VECTOR_KIND = "arena-web-routed-datagram-conformance-vectors"
VECTOR_FORMAT_VERSION = 1

# A fixed, obviously synthetic session nonce. Real sessions draw 12 random bytes.
VECTOR_SESSION_NONCE = bytes.fromhex("00112233445566778899aabb")
OTHER_SESSION_NONCE = bytes.fromhex("ffeeddccbbaa998877665544")

_SINGLE_SIZES = (0, 1, 15, 16, 17, 64, 1300, 1314)
_PACKED_SIZES = ((16, 17), (64, 64), (16, 1300))
_TAG_ORDINALS = (0, 1, 7, 4294967295)


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


def build_conformance_vectors() -> dict:
    """Return the whole vector document."""
    return {
        "decodeAcceptances": _decode_acceptances(),
        "decodeRejections": _decode_rejections(),
        "encodeCases": _encode_cases(),
        "encodeRejections": _encode_rejections(),
        "formatVersion": VECTOR_FORMAT_VERSION,
        "framing": {
            "datagramLengthPrefixBytes": LENGTH_PREFIX_BYTES,
            "relayHeaderBytes": RELAY_HEADER_BYTES,
            "singleDatagramOverheadBytes": SINGLE_DATAGRAM_OVERHEAD_BYTES,
        },
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
