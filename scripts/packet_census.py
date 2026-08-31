# SPDX-License-Identifier: GPL-2.0-or-later
"""The WP5 packet census: what ioquake3 actually puts on the wire.

The census is taken from a packet capture rather than from the engine, so the
instrumentation is outside the game protocol: no engine source is patched, no
cvar changes what is sent, and the observed sizes are the sizes the kernel saw.
This module owns everything that turns those bytes into evidence, and it is
pure — it reads a capture and a phase timeline and returns records, so every
classification rule below is unit-testable without a container.

Three things are distinguished, because WP6 needs them separately:

* **direction**, by UDP port. The client-to-server and server-to-client netchan
  headers differ by the two-byte qport that only a client writes
  (ioq3 code/qcommon/net_chan.c Netchan_Transmit), so a size is meaningless
  without a direction.
* **connectionless versus netchan**, by the first four bytes. A sequence of -1
  marks an out-of-band message (net_chan.c:35), and those are the packets that
  fragmentation would never protect: they carry no fragment fields at all.
* **fragmented versus whole**, by the high bit of the sequence
  (`FRAGMENT_BIT`, net_chan.c:55).

Sizes are recorded at the engine/UDP boundary: `udpPayloadBytes` is exactly the
buffer the engine handed to `sendto` or read from `recvfrom`
(code/qcommon/net_ip.c NET_SendPacket / NET_GetPacket). The IP and link-layer
totals are recorded beside it so a reader can see the difference rather than
having to assume one.
"""

from __future__ import annotations

import ipaddress
import struct
from typing import Any, Iterable

# pcap link types this module understands. The census captures on the server
# container's own Ethernet interface, which is LINKTYPE_ETHERNET; the "any"
# pseudo-interface's cooked encapsulation is accepted too so that a capture
# taken that way is readable rather than silently mis-parsed.
LINKTYPE_ETHERNET = 1
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276

PCAP_MAGIC_MICROSECONDS = 0xA1B2C3D4
PCAP_MAGIC_NANOSECONDS = 0xA1B23C4D

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_VLAN = 0x8100
IPPROTO_UDP = 17

UDP_HEADER_BYTES = 8

# ioq3 code/qcommon/net_chan.c. A connectionless datagram opens with a sequence
# of -1; everything else is netchan traffic.
CONNECTIONLESS_SEQUENCE = 0xFFFFFFFF
FRAGMENT_BIT = 0x80000000

# The netchan header, from Netchan_Transmit: sequence, then the qport a client
# writes and a server does not, then the challenge checksum, then the two
# fragment fields when the message is fragmented.
SEQUENCE_BYTES = 4
QPORT_BYTES = 2
CHALLENGE_CHECKSUM_BYTES = 4
FRAGMENT_FIELD_BYTES = 4

CLIENT_TO_SERVER = "client-to-server"
SERVER_TO_CLIENT = "server-to-client"

CONNECTIONLESS = "connectionless"
NETCHAN = "netchan"

# ioq3 code/qcommon/net_chan.c:50-52. Recorded so the census can say whether an
# observation approached the engine's own bounds instead of leaving a reader to
# look them up.
MAX_PACKETLEN = 1400
FRAGMENT_SIZE = 704

# ioq3 code/qcommon/qcommon.h:184.
MAX_MSGLEN = 16384

# The connectionless commands this profile can legitimately produce. Anything
# else is reported rather than folded into a bucket, because an unexpected
# out-of-band command is exactly what a census should surface.
KNOWN_CONNECTIONLESS_COMMANDS = (
    "challengeResponse",
    "connect",
    "connectResponse",
    "disconnect",
    "echo",
    "getchallenge",
    "getinfo",
    "getstatus",
    "infoResponse",
    "print",
    "statusResponse",
)


class PacketCensusError(ValueError):
    """Raised when a capture cannot be read as the census requires."""


def _fail(message: str) -> None:
    raise PacketCensusError(message)


def _read_pcap_header(data: bytes) -> tuple[str, int, int]:
    if len(data) < 24:
        _fail("capture is shorter than a pcap file header")
    (magic,) = struct.unpack("<I", data[:4])
    if magic == PCAP_MAGIC_MICROSECONDS:
        endian, resolution = "<", 1_000_000
    elif magic == PCAP_MAGIC_NANOSECONDS:
        endian, resolution = "<", 1_000_000_000
    else:
        (magic,) = struct.unpack(">I", data[:4])
        if magic == PCAP_MAGIC_MICROSECONDS:
            endian, resolution = ">", 1_000_000
        elif magic == PCAP_MAGIC_NANOSECONDS:
            endian, resolution = ">", 1_000_000_000
        else:
            _fail("capture is not a classic pcap file")
    link_type = struct.unpack(f"{endian}I", data[20:24])[0]
    if link_type not in (LINKTYPE_ETHERNET, LINKTYPE_LINUX_SLL, LINKTYPE_LINUX_SLL2):
        _fail(f"capture link type {link_type} is not supported")
    return endian, resolution, link_type


def _strip_link_layer(frame: bytes, link_type: int) -> bytes | None:
    """Return the IPv4 datagram of a captured frame, or None if it is not one."""
    if link_type == LINKTYPE_ETHERNET:
        if len(frame) < 14:
            return None
        ethertype = struct.unpack(">H", frame[12:14])[0]
        offset = 14
        while ethertype == ETHERTYPE_VLAN:
            if len(frame) < offset + 4:
                return None
            ethertype = struct.unpack(">H", frame[offset + 2 : offset + 4])[0]
            offset += 4
        if ethertype != ETHERTYPE_IPV4:
            return None
        return frame[offset:]
    if link_type == LINKTYPE_LINUX_SLL:
        if len(frame) < 16:
            return None
        if struct.unpack(">H", frame[14:16])[0] != ETHERTYPE_IPV4:
            return None
        return frame[16:]
    if len(frame) < 20:
        return None
    if struct.unpack(">H", frame[0:2])[0] != ETHERTYPE_IPV4:
        return None
    return frame[20:]


def parse_pcap(data: bytes) -> list[dict[str, Any]]:
    """Read every IPv4/UDP datagram out of a classic pcap capture."""
    endian, resolution, link_type = _read_pcap_header(data)
    packets: list[dict[str, Any]] = []
    offset = 24
    record_header = struct.Struct(f"{endian}IIII")
    while offset < len(data):
        if offset + record_header.size > len(data):
            _fail("capture ends inside a packet record header")
        seconds, fraction, captured, original = record_header.unpack_from(data, offset)
        offset += record_header.size
        if offset + captured > len(data):
            _fail("capture ends inside a packet")
        frame = data[offset : offset + captured]
        offset += captured
        if captured != original:
            _fail(
                "capture is truncated: a census of packet sizes cannot be taken "
                "from a snapshot length that cut a packet short"
            )
        datagram = _strip_link_layer(frame, link_type)
        if datagram is None or len(datagram) < 20:
            continue
        version_ihl = datagram[0]
        if version_ihl >> 4 != 4:
            continue
        header_bytes = (version_ihl & 0x0F) * 4
        if header_bytes < 20 or len(datagram) < header_bytes:
            continue
        total_length = struct.unpack(">H", datagram[2:4])[0]
        flags_fragment = struct.unpack(">H", datagram[6:8])[0]
        more_fragments = bool(flags_fragment & 0x2000)
        fragment_offset = (flags_fragment & 0x1FFF) * 8
        protocol = datagram[9]
        if protocol != IPPROTO_UDP:
            continue
        if more_fragments or fragment_offset:
            _fail(
                "the capture contains an IP-fragmented UDP datagram; the census "
                "measures whole engine datagrams and cannot reassemble them"
            )
        source = str(ipaddress.IPv4Address(datagram[12:16]))
        destination = str(ipaddress.IPv4Address(datagram[16:20]))
        udp = datagram[header_bytes:total_length]
        if len(udp) < UDP_HEADER_BYTES:
            continue
        source_port, destination_port, udp_length = struct.unpack(">HHH", udp[:6])
        payload = udp[UDP_HEADER_BYTES:udp_length]
        if len(payload) != udp_length - UDP_HEADER_BYTES:
            _fail("a UDP datagram is shorter than its own length field")
        packets.append(
            {
                "timestamp": seconds + fraction / resolution,
                "source": source,
                "sourcePort": source_port,
                "destination": destination,
                "destinationPort": destination_port,
                "payload": payload,
                "ipv4DatagramBytes": total_length,
                "linkFrameBytes": original,
            }
        )
    return packets


def _connectionless_command(payload: bytes) -> str:
    body = payload[SEQUENCE_BYTES:]
    token = bytearray()
    for byte in body:
        if byte in b" \t\r\n\x00":
            break
        token.append(byte)
    return token.decode("ascii", errors="replace") or "<empty>"


def classify(payload: bytes, direction: str) -> dict[str, Any]:
    """Classify one engine datagram at the UDP boundary."""
    if direction not in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
        _fail(f"unknown direction {direction!r}")
    if len(payload) < SEQUENCE_BYTES:
        _fail("an engine datagram is shorter than its own sequence field")
    (sequence,) = struct.unpack("<I", payload[:SEQUENCE_BYTES])
    if sequence == CONNECTIONLESS_SEQUENCE:
        return {
            "class": CONNECTIONLESS,
            "command": _connectionless_command(payload),
            "fragmented": False,
            "headerBytes": SEQUENCE_BYTES,
            "sequence": None,
            "qport": None,
        }
    fragmented = bool(sequence & FRAGMENT_BIT)
    header = SEQUENCE_BYTES + CHALLENGE_CHECKSUM_BYTES
    if direction == CLIENT_TO_SERVER:
        header += QPORT_BYTES
    if fragmented:
        header += FRAGMENT_FIELD_BYTES
    if len(payload) < header:
        _fail("an engine datagram is shorter than its own netchan header")
    qport = None
    if direction == CLIENT_TO_SERVER:
        (qport,) = struct.unpack(
            "<H", payload[SEQUENCE_BYTES : SEQUENCE_BYTES + QPORT_BYTES]
        )
    return {
        "class": NETCHAN,
        "command": None,
        "fragmented": fragmented,
        "headerBytes": header,
        "sequence": sequence & ~FRAGMENT_BIT,
        "qport": qport,
    }


def _phase_for(timestamp: float, phases: list[dict[str, Any]]) -> str:
    current = "before-start"
    for phase in phases:
        if timestamp >= phase["startedAt"]:
            current = phase["name"]
    return current


def _statistics(sizes: list[int]) -> dict[str, Any]:
    if not sizes:
        return {
            "count": 0,
            "maximum": None,
            "mean": None,
            "median": None,
            "minimum": None,
            "percentile95": None,
            "percentile99": None,
            "totalBytes": 0,
        }
    ordered = sorted(sizes)
    count = len(ordered)

    def percentile(fraction: float) -> int:
        # Nearest-rank: the smallest observed value at or above the requested
        # fraction of the sample. No interpolation, because an interpolated
        # packet size is a size nothing sent.
        rank = max(1, min(count, int(round(fraction * count))))
        return ordered[rank - 1]

    return {
        "count": count,
        "maximum": ordered[-1],
        "mean": round(sum(ordered) / count, 3),
        "median": percentile(0.5),
        "minimum": ordered[0],
        "percentile95": percentile(0.95),
        "percentile99": percentile(0.99),
        "totalBytes": sum(ordered),
    }


def _distribution(sizes: Iterable[int]) -> list[list[int]]:
    counts: dict[int, int] = {}
    for size in sizes:
        counts[size] = counts.get(size, 0) + 1
    return [[size, counts[size]] for size in sorted(counts)]


def build_records(
    packets: list[dict[str, Any]],
    *,
    server_address: str,
    server_port: int,
    phases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn captured datagrams into classified census records."""
    ordered_phases = sorted(phases, key=lambda phase: phase["startedAt"])
    records: list[dict[str, Any]] = []
    # A netchan's outgoing sequence starts at 1 for a new connection (ioq3
    # code/qcommon/net_chan.c Netchan_Setup), so a client-to-server sequence
    # that goes *backwards* opens a new connection. Every record carries that
    # index, because a sequence number alone repeats across connections and
    # would merge two gamestates into one.
    #
    # It must be a strict decrease, not "does not advance":
    # Netchan_TransmitNextFragment writes the same outgoingSequence on every
    # fragment of one message and increments only after the last one
    # (net_chan.c:118, :162-165), so a repeated sequence is the rest of a
    # fragmented message and not a reconnect. This profile produced no
    # fragmented client message, but WP6 has to generate exactly that case.
    connection_index = -1
    previous_sequence: int | None = None
    for packet in packets:
        to_server = (
            packet["destination"] == server_address
            and packet["destinationPort"] == server_port
        )
        from_server = (
            packet["source"] == server_address and packet["sourcePort"] == server_port
        )
        if to_server == from_server:
            _fail(
                f"a datagram between {packet['source']}:{packet['sourcePort']} and "
                f"{packet['destination']}:{packet['destinationPort']} is neither to "
                "nor from the server under census"
            )
        direction = CLIENT_TO_SERVER if to_server else SERVER_TO_CLIENT
        classification = classify(packet["payload"], direction)
        payload_bytes = len(packet["payload"])
        if classification["class"] == NETCHAN:
            if direction == CLIENT_TO_SERVER:
                sequence = classification["sequence"]
                if previous_sequence is not None and sequence < previous_sequence:
                    connection_index += 1
                elif connection_index < 0:
                    connection_index = 0
                previous_sequence = sequence
            elif connection_index < 0:
                # A capture that starts with the server's half of a connection
                # still belongs to a connection; only a later sequence that does
                # not advance opens the next one.
                connection_index = 0
        records.append(
            {
                "class": classification["class"],
                "command": classification["command"],
                "connectionIndex": connection_index,
                "direction": direction,
                "fragmented": classification["fragmented"],
                "headerBytes": classification["headerBytes"],
                "ipv4DatagramBytes": packet["ipv4DatagramBytes"],
                "messageBytes": payload_bytes - classification["headerBytes"],
                "peerPort": packet["sourcePort"] if to_server else packet["destinationPort"],
                "phase": _phase_for(packet["timestamp"], ordered_phases),
                "qport": classification["qport"],
                "sequence": classification["sequence"],
                "timestamp": packet["timestamp"],
                "udpDatagramBytes": payload_bytes + UDP_HEADER_BYTES,
                "udpPayloadBytes": payload_bytes,
            }
        )
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The census summary: per direction, per class, and the engine's own bounds."""
    directions = (CLIENT_TO_SERVER, SERVER_TO_CLIENT)
    classes = (CONNECTIONLESS, NETCHAN)
    summary: dict[str, Any] = {"byDirection": {}, "overall": {}}
    for direction in directions:
        in_direction = [item for item in records if item["direction"] == direction]
        entry: dict[str, Any] = {
            "all": _statistics([item["udpPayloadBytes"] for item in in_direction]),
            "byClass": {},
            "headerBytes": sorted(
                {
                    item["headerBytes"]
                    for item in in_direction
                    if item["class"] == NETCHAN
                }
            ),
        }
        for name in classes:
            subset = [item for item in in_direction if item["class"] == name]
            entry["byClass"][name] = {
                "distribution": _distribution(
                    item["udpPayloadBytes"] for item in subset
                ),
                "fragmented": sum(1 for item in subset if item["fragmented"]),
                "statistics": _statistics([item["udpPayloadBytes"] for item in subset]),
            }
        entry["byClass"][CONNECTIONLESS]["commands"] = _command_summary(
            [item for item in in_direction if item["class"] == CONNECTIONLESS]
        )
        entry["byPhase"] = {
            phase: _statistics(
                [
                    item["udpPayloadBytes"]
                    for item in in_direction
                    if item["phase"] == phase
                ]
            )
            for phase in sorted({item["phase"] for item in in_direction})
        }
        summary["byDirection"][direction] = entry

    summary["overall"] = {
        "count": len(records),
        "maximumUdpPayloadBytes": max(
            (item["udpPayloadBytes"] for item in records), default=None
        ),
        "qports": sorted(
            {item["qport"] for item in records if item["qport"] is not None}
        ),
        "unknownConnectionlessCommands": sorted(
            {
                item["command"]
                for item in records
                if item["class"] == CONNECTIONLESS
                and item["command"] not in KNOWN_CONNECTIONLESS_COMMANDS
            }
        ),
    }
    summary["engineBounds"] = {
        "fragmentSize": FRAGMENT_SIZE,
        "maxMsgLen": MAX_MSGLEN,
        "maxPacketLen": MAX_PACKETLEN,
        "observedAtOrAboveFragmentSize": sum(
            1 for item in records if item["udpPayloadBytes"] >= FRAGMENT_SIZE
        ),
        "observedAtOrAboveMaxPacketLen": sum(
            1 for item in records if item["udpPayloadBytes"] >= MAX_PACKETLEN
        ),
    }
    summary["headerAsymmetry"] = _header_asymmetry(records)
    summary["milestones"] = _milestones(records)
    summary["fragmentedMessages"] = _fragmented_messages(records)
    summary["connections"] = _connections(records)
    summary["overall"]["clientSourcePorts"] = sorted(
        {item["peerPort"] for item in records}
    )
    return summary


def _milestones(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The first observation of each protocol step the census has to cover.

    Reported from the capture rather than from the driver's timeline, so the
    coverage claim rests on what was on the wire and not on what was asked for.
    """
    def first(predicate) -> dict[str, Any] | None:
        for index, item in enumerate(records):
            if predicate(item):
                return {
                    "index": index,
                    "direction": item["direction"],
                    "timestamp": item["timestamp"],
                    "udpPayloadBytes": item["udpPayloadBytes"],
                }
        return None

    def command(name: str):
        return lambda item: item["class"] == CONNECTIONLESS and item["command"] == name

    milestones = {
        name: first(command(name))
        for name in (
            "getinfo",
            "infoResponse",
            "getstatus",
            "statusResponse",
            "getchallenge",
            "challengeResponse",
            "connect",
            "connectResponse",
        )
    }
    milestones["firstNetchanClientToServer"] = first(
        lambda item: item["class"] == NETCHAN
        and item["direction"] == CLIENT_TO_SERVER
    )
    milestones["firstNetchanServerToClient"] = first(
        lambda item: item["class"] == NETCHAN
        and item["direction"] == SERVER_TO_CLIENT
    )
    milestones["firstFragment"] = first(lambda item: item["fragmented"])
    return milestones


def _fragmented_messages(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every message the netchan had to fragment, grouped by its sequence.

    The gamestate is the one such message in this profile, and it is the largest
    thing the protocol sends, so it is reported whole rather than only as the
    biggest single datagram.
    """
    groups: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    order: list[tuple[int, str, int]] = []
    for item in records:
        if not item["fragmented"]:
            continue
        key = (item["connectionIndex"], item["direction"], item["sequence"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    messages = []
    for key in order:
        group = groups[key]
        connection_index, direction, sequence = key
        messages.append(
            {
                "connectionIndex": connection_index,
                "direction": direction,
                "fragments": len(group),
                "largestDatagramBytes": max(item["udpPayloadBytes"] for item in group),
                "messageBytes": sum(item["messageBytes"] for item in group),
                "sequence": sequence,
                "startedAt": min(item["timestamp"] for item in group),
                "totalUdpPayloadBytes": sum(item["udpPayloadBytes"] for item in group),
            }
        )
    return messages


def _connections(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize each netchan connection the capture contains.

    Disconnect and reconnect are therefore observable from the wire, without
    trusting the driver's timeline and without reading a payload the netchan
    Huffman-codes. The segmentation itself is done in `build_records`, where a
    strictly decreasing client sequence — not merely a repeated one — opens the
    next connection.
    """
    segments: dict[int, dict[str, Any]] = {}
    for item in records:
        if item["class"] != NETCHAN or item["connectionIndex"] < 0:
            continue
        current = segments.get(item["connectionIndex"])
        if current is None:
            current = {
                "clientSourcePorts": [],
                "clientToServer": 0,
                "endedAt": item["timestamp"],
                "index": item["connectionIndex"],
                "qports": [],
                "serverToClient": 0,
                "startedAt": item["timestamp"],
            }
            segments[item["connectionIndex"]] = current
        if item["direction"] == CLIENT_TO_SERVER:
            current["clientToServer"] += 1
            if item["qport"] not in current["qports"]:
                current["qports"].append(item["qport"])
        else:
            current["serverToClient"] += 1
        if item["peerPort"] not in current["clientSourcePorts"]:
            current["clientSourcePorts"].append(item["peerPort"])
        current["endedAt"] = item["timestamp"]
    return [segments[index] for index in sorted(segments)]


def _command_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    commands: dict[str, list[int]] = {}
    for item in records:
        commands.setdefault(item["command"], []).append(item["udpPayloadBytes"])
    return {
        name: {
            "count": len(sizes),
            "maximum": max(sizes),
            "minimum": min(sizes),
            "sizes": sorted(set(sizes)),
        }
        for name, sizes in sorted(commands.items())
    }


def _header_asymmetry(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The measured client/server netchan header difference.

    WP0 records 10 bytes client-to-server and 8 server-to-client, with a
    fragment adding 2 + 2. This reports what the capture actually shows, so the
    number in the plan is confirmed by observation rather than repeated.
    """
    result: dict[str, Any] = {}
    for direction in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
        for fragmented in (False, True):
            subset = [
                item
                for item in records
                if item["direction"] == direction
                and item["class"] == NETCHAN
                and item["fragmented"] is fragmented
            ]
            key = f"{direction}{'-fragmented' if fragmented else ''}"
            result[key] = {
                "count": len(subset),
                "headerBytes": sorted({item["headerBytes"] for item in subset}),
            }
    whole_client = result[CLIENT_TO_SERVER]["headerBytes"]
    whole_server = result[SERVER_TO_CLIENT]["headerBytes"]
    result["difference"] = (
        whole_client[0] - whole_server[0]
        if len(whole_client) == 1 and len(whole_server) == 1
        else None
    )
    return result
