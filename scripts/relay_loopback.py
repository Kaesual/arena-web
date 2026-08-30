# SPDX-License-Identifier: GPL-2.0-or-later
"""An in-memory relay and echo destination for the routed datagram contract.

This is the adapter that makes the contract testable without a network. It
models what `docs/relay-datagram-contract.md` says a relay does to one client's
traffic for one game destination: answer the in-band address request, refuse an
invalid authorization in band and then terminate, accept a browser-to-server
frame addressed to the permitted destination, unpack its inner datagrams —
including a zero-length one — hand each to a destination that echoes UDP
payloads unchanged, and return each echo as its own server-to-browser frame
carrying exactly one inner datagram.

It is not a relay implementation and makes no attempt to be one: it validates no
authorization beyond string equality against a synthetic value, has no address
allocator, no congestion behaviour and no routing table. It exists so that the
framing, the session setup and the measurement logic can be exercised
deterministically, and so that an independent implementation of the contract can
be driven through the same cases.

The fault settings deliberately produce traffic the contract forbids, so the
receiver's rejection paths are exercised rather than assumed. One of them,
`FAULT_DROP_ZERO_LENGTH`, models a relay that refuses a zero-length inner
datagram on the game path: the committed measurement vector requires that case,
so a probe must record it as unanswered rather than quietly skipping it.
"""

from __future__ import annotations

from relay_probe import (
    BROWSER_TO_SERVER,
    ERROR_DESTINATION_UNAVAILABLE,
    ERROR_INVALID_AUTHORIZATION,
    MAX_LENGTH_PREFIX_VALUE,
    RELAY_HEADER_BYTES,
    SERVER_TO_BROWSER,
    TYPE_KEEP_ALIVE,
    TYPE_RELAY_PACKET,
    TYPE_REQUEST_ADDRESS,
    AdapterSendError,
    RelayHeader,
    SessionDriver,
    SessionHandshake,
    datagram_type,
    decode_frame,
    decode_relay_header,
    encode_address_assignment,
    encode_error,
    encode_frame,
    encode_keep_alive,
    encode_relay_header,
)

# A synthetic prefix, used only where the 40 header bytes are exercised as pure
# frame grammar. No real address, port or endpoint may ever appear here.
SYNTHETIC_PREFIX = bytes(range(RELAY_HEADER_BYTES))
SYNTHETIC_RETURN_PREFIX = bytes(
    (value + 0x80) & 0xFF for value in range(RELAY_HEADER_BYTES)
)

# Addresses from the IPv6 documentation prefix 2001:db8::/32 (RFC 3849), which
# is reserved for examples and is never routable. They are placeholders for
# values the integration environment supplies at runtime.
SYNTHETIC_CLIENT_ADDRESS = bytes.fromhex("20010db8000000000000000000000001")
SYNTHETIC_OTHER_CLIENT_ADDRESS = bytes.fromhex("20010db8000000000000000000000011")
SYNTHETIC_DESTINATION_ADDRESS = bytes.fromhex("20010db8000000000000000000000002")
SYNTHETIC_FOREIGN_ADDRESS = bytes.fromhex("20010db8000000000000000000000003")
SYNTHETIC_CLIENT_PORT = 49152
SYNTHETIC_DESTINATION_PORT = 40000

# A synthetic one-time value standing in for the opaque authorization the
# integration environment issues. Nothing here inspects its interior.
SYNTHETIC_AUTHORIZATION = "loopback-one-time-authorization"

FAULT_NONE = ""
FAULT_TRUNCATED_RETURN = "truncatedReturn"
FAULT_PACKED_RETURN = "packedReturn"
FAULT_CORRUPT_PAYLOAD = "corruptPayload"
FAULT_FOREIGN_HEADER = "foreignHeader"
FAULT_HEADER_ONLY_RETURN = "headerOnlyReturn"
FAULT_DECLARED_OVERSIZE = "declaredOversize"
FAULT_DROP_ZERO_LENGTH = "dropZeroLengthInner"
FAULTS = (
    FAULT_NONE,
    FAULT_CORRUPT_PAYLOAD,
    FAULT_DECLARED_OVERSIZE,
    FAULT_DROP_ZERO_LENGTH,
    FAULT_FOREIGN_HEADER,
    FAULT_HEADER_ONLY_RETURN,
    FAULT_PACKED_RETURN,
    FAULT_TRUNCATED_RETURN,
)


class LoopbackAdapter:
    """One client's view of the in-memory relay."""

    def __init__(
        self,
        relay: "LoopbackRelay",
        max_datagram_size_bytes: int,
        client_address: bytes,
        client_port: int,
    ) -> None:
        self._relay = relay
        self.max_datagram_size_bytes = max_datagram_size_bytes
        self.client_address = bytes(client_address)
        self.client_port = client_port
        self.assigned_address: bytes = b""
        self.terminated = False
        self.inbox: list = []
        self.sent_frames = 0
        self.write_failures = 0

    def send(self, frame: bytes) -> None:
        if len(frame) > self.max_datagram_size_bytes:
            self.write_failures += 1
            raise AdapterSendError(
                f"frame of {len(frame)} bytes exceeds the "
                f"{self.max_datagram_size_bytes} byte transport maximum"
            )
        try:
            self._relay.deliver(self, frame)
        except AdapterSendError:
            self.write_failures += 1
            raise
        self.sent_frames += 1

    def drain(self) -> list:
        frames, self.inbox = self.inbox, []
        return frames


class LoopbackRelay:
    """A relay plus a UDP echo destination, with optional contract violations."""

    def __init__(
        self,
        max_datagram_size_bytes: int = 1200,
        destination_address: bytes = SYNTHETIC_DESTINATION_ADDRESS,
        destination_port: int = SYNTHETIC_DESTINATION_PORT,
        destination_reply_port: int = None,
        echo: bool = True,
        fault: str = FAULT_NONE,
        drop_inner_sizes=(),
        refuse_send: bool = False,
        crosstalk: bool = False,
        authorization: str = SYNTHETIC_AUTHORIZATION,
        refuse_authorization: bool = False,
    ) -> None:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}")
        self.max_datagram_size_bytes = max_datagram_size_bytes
        self.destination_address = bytes(destination_address)
        self.destination_port = destination_port
        # The destination answers from its own UDP port, which a client cannot
        # predict from the virtual port it addressed. Making the two differ by
        # default is what keeps the return-header check honest.
        self.destination_reply_port = (
            destination_port + 1
            if destination_reply_port is None
            else destination_reply_port
        )
        self.echo = echo
        self.fault = fault
        self.drop_inner_sizes = frozenset(drop_inner_sizes)
        self.refuse_send = refuse_send
        self.crosstalk = crosstalk
        self.authorization = authorization
        self.refuse_authorization = refuse_authorization
        self.adapters: list = []
        self.address_requests = 0
        self.refused_authorizations = 0
        self.refused_destinations = 0
        self.dropped_before_assignment = 0
        self.dropped_zero_length = 0
        self.keep_alives_answered = 0
        self.received_frames = 0
        self.received_datagrams = 0
        self.received_inner_sizes: list = []
        self.undeliverable_returns = 0

    def attach(
        self,
        max_datagram_size_bytes: int = None,
        client_address: bytes = SYNTHETIC_CLIENT_ADDRESS,
        client_port: int = SYNTHETIC_CLIENT_PORT,
    ) -> LoopbackAdapter:
        adapter = LoopbackAdapter(
            self,
            (
                self.max_datagram_size_bytes
                if max_datagram_size_bytes is None
                else max_datagram_size_bytes
            ),
            client_address,
            client_port,
        )
        self.adapters.append(adapter)
        return adapter

    # -- inbound -------------------------------------------------------------

    def deliver(self, source: LoopbackAdapter, datagram: bytes) -> None:
        """Accept one datagram from a client and queue whatever answers it."""
        if source.terminated:
            # The relay closed this session after refusing its authorization.
            raise AdapterSendError("the relay closed this session")
        kind = datagram_type(datagram)
        if kind == TYPE_RELAY_PACKET and self.refuse_send:
            # A transport that starts refusing writes once the session is
            # measuring. Refusing the setup exchange as well would model a
            # session that never opened, which is not what this fault is for.
            raise AdapterSendError("the transport refused the datagram")
        if kind == TYPE_REQUEST_ADDRESS:
            self._handle_address_request(source, datagram)
            return
        if kind == TYPE_KEEP_ALIVE:
            self.keep_alives_answered += 1
            self._deliver_to(source, encode_keep_alive(b"\x00" * 16))
            return
        if kind == TYPE_RELAY_PACKET:
            self._handle_relay(source, datagram)
            return
        self._deliver_to(source, encode_error(ERROR_INVALID_AUTHORIZATION, "unknown"))

    def _handle_address_request(self, source: LoopbackAdapter, datagram: bytes) -> None:
        self.address_requests += 1
        offered = datagram[4:].decode("utf-8", errors="replace")
        if self.refuse_authorization or offered != self.authorization:
            # In band, then terminal: the session is unusable afterwards.
            self.refused_authorizations += 1
            source.terminated = True
            self._deliver_to(
                source,
                encode_error(ERROR_INVALID_AUTHORIZATION, "Invalid or expired token"),
            )
            return
        source.assigned_address = source.client_address
        self._deliver_to(source, encode_address_assignment(source.assigned_address))

    def _handle_relay(self, source: LoopbackAdapter, frame: bytes) -> None:
        if not source.assigned_address:
            # A relay packet before assignment is dropped without an answer,
            # exactly as an unaddressed session is.
            self.dropped_before_assignment += 1
            return
        # A malformed browser-to-server frame is a defect in the client under
        # test, not something to absorb quietly.
        decoded = decode_frame(frame, BROWSER_TO_SERVER, MAX_LENGTH_PREFIX_VALUE)
        header = decode_relay_header(decoded.prefix)
        if (
            header.destination_address != self.destination_address
            or header.destination_port != self.destination_port
        ):
            # An unknown and an unauthorized destination are the same answer.
            self.refused_destinations += 1
            self._deliver_to(
                source,
                encode_error(ERROR_DESTINATION_UNAVAILABLE, "Destination not connected"),
            )
            return
        self.received_frames += 1
        self.received_datagrams += len(decoded.datagrams)
        self.received_inner_sizes.extend(
            len(payload) for payload in decoded.datagrams
        )
        if not self.echo:
            return
        for payload in decoded.datagrams:
            if not payload and self.fault == FAULT_DROP_ZERO_LENGTH:
                # The behaviour the amendment removed: a zero-length inner
                # datagram never reaches the destination, so the committed
                # vector's 0-byte case can only be recorded as unanswered.
                self.dropped_zero_length += 1
                continue
            if len(payload) in self.drop_inner_sizes:
                continue
            self._queue(source, header.source_port, payload)

    # -- outbound ------------------------------------------------------------

    def _return_frames(
        self, target: LoopbackAdapter, client_port: int, payload: bytes
    ) -> list:
        source_address = self.destination_address
        if self.fault == FAULT_FOREIGN_HEADER:
            source_address = SYNTHETIC_FOREIGN_ADDRESS
        prefix = encode_relay_header(
            RelayHeader(
                destination_address=target.client_address,
                destination_port=client_port,
                source_address=source_address,
                source_port=self.destination_reply_port,
            )
        )
        if self.fault == FAULT_CORRUPT_PAYLOAD and payload:
            payload = payload[:-1] + bytes([payload[-1] ^ 0xFF])
        if self.fault == FAULT_HEADER_ONLY_RETURN:
            return [prefix]
        if self.fault == FAULT_PACKED_RETURN:
            # Two inner datagrams in one return frame, which the contract's
            # exactly-one rule forbids.
            return [encode_frame(prefix, (payload, payload), BROWSER_TO_SERVER)]
        if self.fault == FAULT_DECLARED_OVERSIZE:
            return [prefix + b"\xff\xff" + payload]
        frame = encode_frame(prefix, (payload,), SERVER_TO_BROWSER)
        if self.fault == FAULT_TRUNCATED_RETURN:
            frame = frame[:-1]
        return [frame]

    def _queue(
        self, source: LoopbackAdapter, client_port: int, payload: bytes
    ) -> None:
        # Crosstalk delivers one client's echoed *payload* to every session,
        # each in a header addressed to that session. A real relay routes by
        # assigned address and cannot misdeliver a header, so this models the
        # case the payload tag actually defends against: another session's
        # bytes arriving in an otherwise valid frame.
        targets = self.adapters if self.crosstalk else [source]
        for adapter in targets:
            port = client_port if adapter is source else adapter.client_port
            for frame in self._return_frames(adapter, port, payload):
                self._deliver_to(adapter, frame)

    def _deliver_to(self, adapter: LoopbackAdapter, datagram: bytes) -> None:
        if len(datagram) > adapter.max_datagram_size_bytes:
            # The return path cannot carry it either. A real path drops it and
            # the case times out; that is a measurement result.
            self.undeliverable_returns += 1
            return
        adapter.inbox.append(datagram)


def open_session(
    relay: LoopbackRelay,
    plan,
    config,
    session_nonce: bytes,
    session_index: int = 0,
    adapter: LoopbackAdapter = None,
):
    """Run the in-band setup exchange and return `(driver, adapter)`.

    Every loopback session goes through the same handshake a routed one does:
    one REQUEST_ADDRESS carrying the single-use authorization, then the
    assignment that names the virtual client address the routing context needs.
    A refusal raises out of here rather than producing a half-open session.
    """
    adapter = relay.attach() if adapter is None else adapter
    handshake = SessionHandshake(config)
    adapter.send(handshake.request_datagram())
    for datagram in adapter.drain():
        if handshake.accept(datagram) is not None:
            break
    driver = SessionDriver(
        plan,
        adapter,
        session_nonce,
        config,
        handshake.routing_context(),
        session_index,
    )
    return driver, adapter


def run_session(
    driver, adapter, step_ms: float = 1.0, max_steps: int = 100000
) -> float:
    """Drive one session to completion on a virtual clock.

    The clock is virtual on purpose: the deterministic tests must not depend on
    wall-clock timing, and the driver never reads a clock of its own.
    """
    now = 0.0
    driver.pump(now)
    for _ in range(max_steps):
        if driver.finished:
            return now
        for frame in adapter.drain():
            driver.receive(frame, now)
        now += step_ms
        driver.pump(now)
    raise RuntimeError("session did not finish within the step budget")


def run_sessions(pairs, step_ms: float = 1.0, max_steps: int = 100000) -> float:
    """Drive several `(driver, adapter)` pairs concurrently on one clock."""
    now = 0.0
    for driver, _ in pairs:
        driver.pump(now)
    for _ in range(max_steps):
        if all(driver.finished for driver, _ in pairs):
            return now
        for driver, adapter in pairs:
            for frame in adapter.drain():
                driver.receive(frame, now)
        now += step_ms
        for driver, _ in pairs:
            driver.pump(now)
    raise RuntimeError("sessions did not finish within the step budget")
