# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from packet_census import (  # noqa: E402
    CLIENT_TO_SERVER,
    CONNECTIONLESS,
    LINKTYPE_ETHERNET,
    LINKTYPE_LINUX_SLL2,
    NETCHAN,
    SERVER_TO_CLIENT,
    PacketCensusError,
    build_records,
    classify,
    parse_pcap,
    summarize,
)

SERVER = "10.201.27.10"
CLIENT = "10.201.27.20"
SERVER_PORT = 27960
CLIENT_PORT = 27960


def ipv4(address: str) -> bytes:
    return bytes(int(part) for part in address.split("."))


def udp_frame(
    payload: bytes,
    *,
    source: str,
    destination: str,
    source_port: int,
    destination_port: int,
    link_type: int = LINKTYPE_ETHERNET,
) -> bytes:
    udp = struct.pack(
        ">HHHH", source_port, destination_port, 8 + len(payload), 0
    ) + payload
    total_length = 20 + len(udp)
    header = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        total_length,
        0x1234,
        0,
        64,
        17,
        0,
        ipv4(source),
        ipv4(destination),
    )
    datagram = header + udp
    if link_type == LINKTYPE_ETHERNET:
        return b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x0800) + datagram
    return struct.pack(">HHH", 0x0800, 1, 6) + b"\x04" * 8 + b"\x00" * 6 + datagram


def pcap(frames: list[tuple[float, bytes]], link_type: int = LINKTYPE_ETHERNET) -> bytes:
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, link_type)
    for timestamp, frame in frames:
        seconds = int(timestamp)
        microseconds = int(round((timestamp - seconds) * 1_000_000))
        out += struct.pack("<IIII", seconds, microseconds, len(frame), len(frame))
        out += frame
    return out


def to_server(payload: bytes, link_type: int = LINKTYPE_ETHERNET) -> bytes:
    return udp_frame(
        payload,
        source=CLIENT,
        destination=SERVER,
        source_port=CLIENT_PORT,
        destination_port=SERVER_PORT,
        link_type=link_type,
    )


def to_client(payload: bytes, link_type: int = LINKTYPE_ETHERNET) -> bytes:
    return udp_frame(
        payload,
        source=SERVER,
        destination=CLIENT,
        source_port=SERVER_PORT,
        destination_port=CLIENT_PORT,
        link_type=link_type,
    )


def connectionless(command: str, body: bytes = b"") -> bytes:
    return b"\xff\xff\xff\xff" + command.encode() + b" " + body


def client_netchan(sequence: int, qport: int, body: bytes, fragmented: bool = False) -> bytes:
    raw = sequence | (0x80000000 if fragmented else 0)
    payload = struct.pack("<I", raw) + struct.pack("<H", qport) + b"\x00" * 4
    if fragmented:
        payload += struct.pack("<HH", 0, len(body))
    return payload + body


def server_netchan(sequence: int, body: bytes, fragmented: bool = False) -> bytes:
    raw = sequence | (0x80000000 if fragmented else 0)
    payload = struct.pack("<I", raw) + b"\x00" * 4
    if fragmented:
        payload += struct.pack("<HH", 0, len(body))
    return payload + body


class ParsePcapTests(unittest.TestCase):
    def test_ethernet_capture_is_read(self) -> None:
        data = pcap([(1.0, to_server(connectionless("getchallenge")))])
        packets = parse_pcap(data)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["source"], CLIENT)
        self.assertEqual(packets[0]["destination"], SERVER)
        self.assertEqual(packets[0]["destinationPort"], SERVER_PORT)

    def test_cooked_capture_is_read(self) -> None:
        data = pcap(
            [(1.0, to_server(connectionless("getchallenge"), LINKTYPE_LINUX_SLL2))],
            link_type=LINKTYPE_LINUX_SLL2,
        )
        self.assertEqual(len(parse_pcap(data)), 1)

    def test_big_endian_capture_is_read(self) -> None:
        frame = to_server(connectionless("getstatus"))
        data = struct.pack(">IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, LINKTYPE_ETHERNET)
        data += struct.pack(">IIII", 5, 0, len(frame), len(frame)) + frame
        self.assertEqual(len(parse_pcap(data)), 1)

    def test_a_non_pcap_file_is_rejected(self) -> None:
        with self.assertRaisesRegex(PacketCensusError, "not a classic pcap"):
            parse_pcap(b"\x00" * 40)

    def test_a_short_header_is_rejected(self) -> None:
        with self.assertRaisesRegex(PacketCensusError, "shorter than a pcap file header"):
            parse_pcap(b"\x00" * 8)

    def test_an_unsupported_link_type_is_rejected(self) -> None:
        data = pcap([], link_type=105)
        with self.assertRaisesRegex(PacketCensusError, "link type 105"):
            parse_pcap(data)

    def test_a_truncated_snapshot_is_rejected(self) -> None:
        frame = to_server(connectionless("getchallenge"))
        data = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 64, LINKTYPE_ETHERNET)
        data += struct.pack("<IIII", 1, 0, len(frame) - 4, len(frame))
        data += frame[:-4]
        with self.assertRaisesRegex(PacketCensusError, "truncated"):
            parse_pcap(data)

    def test_an_ip_fragment_is_rejected(self) -> None:
        frame = bytearray(to_server(connectionless("getchallenge")))
        # Set the more-fragments flag in the IPv4 header.
        frame[14 + 6] |= 0x20
        with self.assertRaisesRegex(PacketCensusError, "IP-fragmented"):
            parse_pcap(pcap([(1.0, bytes(frame))]))

    def test_non_ipv4_frames_are_skipped(self) -> None:
        frame = b"\x02" * 6 + b"\x03" * 6 + struct.pack(">H", 0x86DD) + b"\x00" * 40
        self.assertEqual(parse_pcap(pcap([(1.0, frame)])), [])

    def test_a_capture_ending_mid_record_is_rejected(self) -> None:
        data = pcap([(1.0, to_server(connectionless("getchallenge")))])
        with self.assertRaisesRegex(PacketCensusError, "ends inside"):
            parse_pcap(data[:-6])


class ClassifyTests(unittest.TestCase):
    def test_connectionless_is_recognised_by_its_sequence(self) -> None:
        result = classify(connectionless("getchallenge", b"1234"), CLIENT_TO_SERVER)
        self.assertEqual(result["class"], CONNECTIONLESS)
        self.assertEqual(result["command"], "getchallenge")
        self.assertEqual(result["headerBytes"], 4)
        self.assertIsNone(result["qport"])

    def test_a_connectionless_command_may_end_at_a_newline_or_nul(self) -> None:
        for terminator in (b"\n", b"\x00", b"\t"):
            with self.subTest(terminator=terminator):
                payload = b"\xff\xff\xff\xff" + b"statusResponse" + terminator + b"rest"
                self.assertEqual(
                    classify(payload, SERVER_TO_CLIENT)["command"], "statusResponse"
                )

    def test_client_netchan_header_is_ten_bytes(self) -> None:
        result = classify(client_netchan(7, 4242, b"x" * 20), CLIENT_TO_SERVER)
        self.assertEqual(result["class"], NETCHAN)
        self.assertEqual(result["headerBytes"], 10)
        self.assertEqual(result["qport"], 4242)
        self.assertEqual(result["sequence"], 7)
        self.assertFalse(result["fragmented"])

    def test_server_netchan_header_is_eight_bytes(self) -> None:
        result = classify(server_netchan(7, b"x" * 20), SERVER_TO_CLIENT)
        self.assertEqual(result["headerBytes"], 8)
        self.assertIsNone(result["qport"])

    def test_fragments_add_four_bytes_in_both_directions(self) -> None:
        self.assertEqual(
            classify(client_netchan(3, 1, b"x" * 8, fragmented=True), CLIENT_TO_SERVER)[
                "headerBytes"
            ],
            14,
        )
        self.assertEqual(
            classify(server_netchan(3, b"x" * 8, fragmented=True), SERVER_TO_CLIENT)[
                "headerBytes"
            ],
            12,
        )

    def test_a_datagram_shorter_than_its_header_is_rejected(self) -> None:
        with self.assertRaisesRegex(PacketCensusError, "shorter than its own netchan"):
            classify(struct.pack("<I", 5) + b"\x00", CLIENT_TO_SERVER)

    def test_a_datagram_shorter_than_its_sequence_is_rejected(self) -> None:
        with self.assertRaisesRegex(PacketCensusError, "shorter than its own sequence"):
            classify(b"\x01\x02", SERVER_TO_CLIENT)

    def test_an_unknown_direction_is_rejected(self) -> None:
        with self.assertRaisesRegex(PacketCensusError, "unknown direction"):
            classify(connectionless("getinfo"), "sideways")


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.phases = [
            {"name": "client-started", "startedAt": 0.0},
            {"name": "driven-play", "startedAt": 10.0},
        ]
        frames = [
            (1.0, to_server(connectionless("getinfo", b"1"))),
            (1.1, to_client(connectionless("infoResponse", b"\\mapname\\oa_pvomit"))),
            (1.2, to_server(connectionless("getstatus", b"1"))),
            (1.3, to_client(connectionless("statusResponse", b"\\sv_hostname\\x"))),
            (2.0, to_server(connectionless("getchallenge", b"1"))),
            (2.1, to_client(connectionless("challengeResponse", b"12345"))),
            (2.2, to_server(connectionless("connect", b"\\name\\x" * 20))),
            (2.3, to_client(connectionless("connectResponse", b"1"))),
            (3.0, to_client(server_netchan(1, b"g" * 1288, fragmented=True))),
            (3.1, to_client(server_netchan(1, b"g" * 40, fragmented=True))),
            (3.2, to_server(client_netchan(1, 4242, b"c" * 20))),
            (11.0, to_server(client_netchan(2, 4242, b"c" * 30))),
            (11.1, to_client(server_netchan(2, b"s" * 100))),
            (12.0, to_server(client_netchan(3, 4242, b"c" * 25))),
            # Reconnect: the sequence restarts.
            (20.0, to_server(client_netchan(1, 4242, b"c" * 20))),
            (20.1, to_client(server_netchan(1, b"g" * 1288, fragmented=True))),
            (20.2, to_client(server_netchan(1, b"g" * 30, fragmented=True))),
            (21.0, to_server(client_netchan(2, 4242, b"c" * 22))),
        ]
        self.records = build_records(
            parse_pcap(pcap(frames)),
            server_address=SERVER,
            server_port=SERVER_PORT,
            phases=self.phases,
        )
        self.summary = summarize(self.records)

    def test_direction_is_derived_from_the_server_endpoint(self) -> None:
        directions = {record["direction"] for record in self.records}
        self.assertEqual(directions, {CLIENT_TO_SERVER, SERVER_TO_CLIENT})

    def test_traffic_that_is_not_the_server_under_census_is_rejected(self) -> None:
        frame = udp_frame(
            connectionless("getinfo"),
            source="10.9.9.9",
            destination="10.9.9.8",
            source_port=1234,
            destination_port=5678,
        )
        with self.assertRaisesRegex(PacketCensusError, "neither to nor from"):
            build_records(
                parse_pcap(pcap([(1.0, frame)])),
                server_address=SERVER,
                server_port=SERVER_PORT,
                phases=[],
            )

    def test_phases_are_assigned_by_timestamp(self) -> None:
        early = [r for r in self.records if r["timestamp"] < 10.0]
        late = [r for r in self.records if r["timestamp"] >= 10.0]
        self.assertTrue(all(r["phase"] == "client-started" for r in early))
        self.assertTrue(all(r["phase"] == "driven-play" for r in late))

    def test_header_asymmetry_is_measured_not_assumed(self) -> None:
        asymmetry = self.summary["headerAsymmetry"]
        self.assertEqual(asymmetry[CLIENT_TO_SERVER]["headerBytes"], [10])
        self.assertEqual(asymmetry[SERVER_TO_CLIENT]["headerBytes"], [8])
        self.assertEqual(asymmetry[f"{SERVER_TO_CLIENT}-fragmented"]["headerBytes"], [12])
        self.assertEqual(asymmetry["difference"], 2)

    def test_connectionless_and_netchan_are_counted_separately(self) -> None:
        client = self.summary["byDirection"][CLIENT_TO_SERVER]["byClass"]
        self.assertEqual(client[CONNECTIONLESS]["statistics"]["count"], 4)
        self.assertEqual(client[NETCHAN]["statistics"]["count"], 5)

    def test_connectionless_commands_are_named(self) -> None:
        commands = self.summary["byDirection"][SERVER_TO_CLIENT]["byClass"][
            CONNECTIONLESS
        ]["commands"]
        self.assertEqual(
            sorted(commands),
            ["challengeResponse", "connectResponse", "infoResponse", "statusResponse"],
        )

    def test_an_unexpected_connectionless_command_is_reported(self) -> None:
        frames = [(1.0, to_server(connectionless("rcon", b"x")))]
        summary = summarize(
            build_records(
                parse_pcap(pcap(frames)),
                server_address=SERVER,
                server_port=SERVER_PORT,
                phases=[],
            )
        )
        self.assertEqual(summary["overall"]["unknownConnectionlessCommands"], ["rcon"])

    def test_reconnect_opens_a_second_connection(self) -> None:
        connections = self.summary["connections"]
        self.assertEqual(len(connections), 2)
        self.assertEqual([item["index"] for item in connections], [0, 1])
        self.assertEqual(connections[0]["qports"], [4242])

    def test_each_connection_keeps_its_own_gamestate(self) -> None:
        messages = self.summary["fragmentedMessages"]
        self.assertEqual(len(messages), 2, "one gamestate per connection")
        self.assertEqual([item["connectionIndex"] for item in messages], [0, 1])
        self.assertEqual(messages[0]["fragments"], 2)
        self.assertEqual(messages[0]["largestDatagramBytes"], 1300)

    def test_milestones_come_from_the_capture(self) -> None:
        milestones = self.summary["milestones"]
        for name in (
            "getinfo",
            "infoResponse",
            "getstatus",
            "statusResponse",
            "getchallenge",
            "challengeResponse",
            "connect",
            "connectResponse",
        ):
            self.assertIsNotNone(milestones[name], name)
        self.assertIsNotNone(milestones["firstFragment"])
        self.assertEqual(milestones["firstNetchanServerToClient"]["udpPayloadBytes"], 1300)

    def test_maximum_and_distribution_are_reported_per_direction(self) -> None:
        server = self.summary["byDirection"][SERVER_TO_CLIENT]
        self.assertEqual(server["all"]["maximum"], 1300)
        distribution = dict(
            tuple(pair) for pair in server["byClass"][NETCHAN]["distribution"]
        )
        self.assertEqual(distribution[1300], 2)
        self.assertEqual(sum(distribution.values()), 5)

    def test_engine_bounds_are_reported(self) -> None:
        bounds = self.summary["engineBounds"]
        self.assertEqual(bounds["maxPacketLen"], 1400)
        self.assertEqual(bounds["fragmentSize"], 1300)
        self.assertEqual(bounds["observedAtOrAboveFragmentSize"], 2)
        self.assertEqual(bounds["observedAtOrAboveMaxPacketLen"], 0)

    def test_statistics_of_an_empty_set_are_explicitly_empty(self) -> None:
        empty = self.summary["byDirection"][CLIENT_TO_SERVER]["byClass"][NETCHAN]
        self.assertGreater(empty["statistics"]["count"], 0)
        summary = summarize([])
        self.assertIsNone(summary["overall"]["maximumUdpPayloadBytes"])
        self.assertEqual(
            summary["byDirection"][CLIENT_TO_SERVER]["all"]["count"], 0
        )

    def test_client_source_ports_are_recorded(self) -> None:
        self.assertEqual(self.summary["overall"]["clientSourcePorts"], [CLIENT_PORT])


class FragmentedClientMessageTests(unittest.TestCase):
    """A fragmented client message is one message, not a reconnect.

    ioq3 code/qcommon/net_chan.c: Netchan_TransmitNextFragment writes the same
    outgoingSequence on every fragment (:118) and increments it only after the
    last one (:162-165), so consecutive client datagrams repeat a sequence.
    Treating "does not advance" as a new connection would invent a phantom
    connection and split the message into one-fragment pieces.
    """

    def setUp(self) -> None:
        frames = [
            (1.0, to_server(client_netchan(1, 4242, b"c" * 20))),
            # One 3-fragment client message: sequence 5 three times.
            (2.0, to_server(client_netchan(5, 4242, b"f" * 1300, fragmented=True))),
            (2.1, to_client(server_netchan(5, b"s" * 40))),
            (2.2, to_server(client_netchan(5, 4242, b"f" * 1300, fragmented=True))),
            (2.3, to_server(client_netchan(5, 4242, b"f" * 12, fragmented=True))),
            (3.0, to_server(client_netchan(6, 4242, b"c" * 20))),
        ]
        self.records = build_records(
            parse_pcap(pcap(frames)),
            server_address=SERVER,
            server_port=SERVER_PORT,
            phases=[],
        )
        self.summary = summarize(self.records)

    def test_a_repeated_sequence_does_not_open_a_connection(self) -> None:
        self.assertEqual({item["connectionIndex"] for item in self.records}, {0})
        self.assertEqual(len(self.summary["connections"]), 1)

    def test_the_fragments_form_one_message(self) -> None:
        messages = self.summary["fragmentedMessages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["direction"], CLIENT_TO_SERVER)
        self.assertEqual(messages[0]["fragments"], 3)
        self.assertEqual(messages[0]["sequence"], 5)
        self.assertEqual(messages[0]["largestDatagramBytes"], 1314)

    def test_the_client_fragment_header_is_fourteen_bytes(self) -> None:
        asymmetry = self.summary["headerAsymmetry"]
        self.assertEqual(
            asymmetry[f"{CLIENT_TO_SERVER}-fragmented"]["headerBytes"], [14]
        )
        self.assertEqual(asymmetry[f"{CLIENT_TO_SERVER}-fragmented"]["count"], 3)

    def test_a_strictly_decreasing_sequence_still_opens_a_connection(self) -> None:
        frames = [
            (1.0, to_server(client_netchan(7, 4242, b"c" * 20))),
            (2.0, to_server(client_netchan(8, 4242, b"c" * 20))),
            (3.0, to_server(client_netchan(1, 4242, b"c" * 20))),
        ]
        summary = summarize(
            build_records(
                parse_pcap(pcap(frames)),
                server_address=SERVER,
                server_port=SERVER_PORT,
                phases=[],
            )
        )
        self.assertEqual(len(summary["connections"]), 2)


class CommittedCensusRecordTests(unittest.TestCase):
    """The committed evidence of the accepted census run.

    A census cannot be reproduced byte for byte — a second session is a second
    game — so what is asserted here is that the committed record is complete,
    internally consistent and identifies the exact artifacts it was taken
    against.
    """

    def setUp(self) -> None:
        # Deliberately not a skip. The record is committed evidence, not a
        # build artifact: if it is missing, the gate has to go red rather than
        # quietly verify nothing.
        path = ROOT / "records" / "wp5-packet-census.json"
        self.assertTrue(path.is_file(), f"{path} is committed evidence and must exist")
        self.record = json.loads(path.read_text(encoding="utf-8"))

    def test_every_required_acceptance_check_passed(self) -> None:
        failed = [
            check["check"]
            for check in self.record["checks"]
            if check["required"] and not check["passed"]
        ]
        self.assertEqual(failed, [])

    def test_every_check_names_its_evidence_and_whether_it_gates(self) -> None:
        for check in self.record["checks"]:
            self.assertIn(
                check["evidence"], ("both logs", "capture", "client log", "server log")
            )
            self.assertIsInstance(check["required"], bool)
            self.assertTrue(check["detail"])

    def test_the_session_identifies_what_it_ran(self) -> None:
        session = self.record["session"]
        baseline = json.loads(
            (ROOT / "locks" / "baseline.json").read_text(encoding="utf-8")
        )
        # The census is the record of a session that was actually driven, so it
        # names the engine commit that session ran and not the current pin. Both
        # commits the lock accounts for are admissible — the pin, and the
        # upstream base the pin declares — and nothing else is, so a measurement
        # taken against an engine this repository never pinned is still refused.
        # This census was taken at the upstream base; what the pin adds on top of
        # it is enumerated in engine.appliedPatches and is confined to
        # code/renderergl2/tr_glsl.c, which neither the dedicated server nor any
        # datagram path compiles.
        self.assertIn(
            session["engineCommit"],
            (
                baseline["engine"]["commit"],
                baseline["engine"]["upstreamBase"]["commit"],
            ),
        )
        profile = json.loads(
            (ROOT / "native" / "server-profile.json").read_text(encoding="utf-8")
        )
        self.assertEqual(session["serverArguments"], profile["serverArguments"])
        self.assertEqual(
            session["clientArguments"],
            profile["clientArguments"]
            + ["+connect", f"{session['serverAddress']}:{session['serverPort']}"],
        )
        for field in ("serverImageId", "toolchainImageId"):
            self.assertRegex(session[field], r"\A[0-9a-f]{64}\Z")

    def test_both_directions_and_both_classes_are_present(self) -> None:
        summary = self.record["summary"]
        for direction in (CLIENT_TO_SERVER, SERVER_TO_CLIENT):
            entry = summary["byDirection"][direction]
            self.assertGreater(entry["all"]["count"], 0)
            for name in (CONNECTIONLESS, NETCHAN):
                self.assertGreater(entry["byClass"][name]["statistics"]["count"], 0)

    def test_the_measured_header_asymmetry_is_the_baseline_one(self) -> None:
        asymmetry = self.record["summary"]["headerAsymmetry"]
        self.assertEqual(asymmetry[CLIENT_TO_SERVER]["headerBytes"], [10])
        self.assertEqual(asymmetry[SERVER_TO_CLIENT]["headerBytes"], [8])
        self.assertEqual(asymmetry["difference"], 2)

    def test_nothing_exceeded_the_engine_packet_bound(self) -> None:
        bounds = self.record["summary"]["engineBounds"]
        self.assertEqual(bounds["observedAtOrAboveMaxPacketLen"], 0)
        self.assertLessEqual(
            self.record["summary"]["overall"]["maximumUdpPayloadBytes"],
            bounds["maxPacketLen"],
        )

    def test_the_session_covered_two_connections(self) -> None:
        self.assertGreaterEqual(len(self.record["summary"]["connections"]), 2)
        self.assertGreaterEqual(len(self.record["summary"]["fragmentedMessages"]), 2)

    def test_the_record_carries_no_unknown_command(self) -> None:
        self.assertEqual(
            self.record["summary"]["overall"]["unknownConnectionlessCommands"], []
        )


if __name__ == "__main__":
    unittest.main()
