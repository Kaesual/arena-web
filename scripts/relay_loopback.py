# SPDX-License-Identifier: GPL-2.0-or-later
"""An in-memory relay and echo destination for the routed datagram contract.

This is the adapter that makes the contract testable without a network. It
models exactly what `docs/relay-datagram-contract.md` says a relay does to
traffic for one game destination: accept a browser-to-server frame, unpack its
inner datagrams, hand each to a destination that echoes UDP payloads unchanged,
and return each echo as its own server-to-browser frame carrying exactly one
inner datagram.

It is not a relay implementation and makes no attempt to be one: there is no
authorization, no address mapping, no congestion behaviour and no routing table.
It exists so that the framing and measurement logic can be exercised
deterministically, and so that an independent implementation of the contract can
be driven through the same conformance cases.

The fault settings deliberately produce traffic the contract forbids, so the
receiver's rejection paths are exercised rather than assumed.
"""

from __future__ import annotations

from relay_probe import (
    BROWSER_TO_SERVER,
    MAX_LENGTH_PREFIX_VALUE,
    RELAY_HEADER_BYTES,
    SERVER_TO_BROWSER,
    AdapterSendError,
    decode_frame,
    encode_frame,
)

# A synthetic prefix. The contract treats these 40 bytes as opaque, and no real
# routing prefix, address or port may ever appear in this repository.
SYNTHETIC_PREFIX = bytes(range(RELAY_HEADER_BYTES))
SYNTHETIC_RETURN_PREFIX = bytes(
    (value + 0x80) & 0xFF for value in range(RELAY_HEADER_BYTES)
)

FAULT_NONE = ""
FAULT_TRUNCATED_RETURN = "truncatedReturn"
FAULT_PACKED_RETURN = "packedReturn"
FAULT_CORRUPT_PAYLOAD = "corruptPayload"
FAULT_FOREIGN_PREFIX = "foreignPrefix"
FAULT_HEADER_ONLY_RETURN = "headerOnlyReturn"
FAULT_DECLARED_OVERSIZE = "declaredOversize"
FAULTS = (
    FAULT_NONE,
    FAULT_CORRUPT_PAYLOAD,
    FAULT_DECLARED_OVERSIZE,
    FAULT_FOREIGN_PREFIX,
    FAULT_HEADER_ONLY_RETURN,
    FAULT_PACKED_RETURN,
    FAULT_TRUNCATED_RETURN,
)


class LoopbackAdapter:
    """One client's view of the in-memory relay."""

    def __init__(self, relay: "LoopbackRelay", max_datagram_size_bytes: int) -> None:
        self._relay = relay
        self.max_datagram_size_bytes = max_datagram_size_bytes
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
        return_prefix: bytes = SYNTHETIC_RETURN_PREFIX,
        echo: bool = True,
        fault: str = FAULT_NONE,
        drop_inner_sizes=(),
        refuse_send: bool = False,
        crosstalk: bool = False,
    ) -> None:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}")
        if len(return_prefix) != RELAY_HEADER_BYTES:
            raise ValueError("return prefix must be 40 bytes")
        self.max_datagram_size_bytes = max_datagram_size_bytes
        self.return_prefix = bytes(return_prefix)
        self.echo = echo
        self.fault = fault
        self.drop_inner_sizes = frozenset(drop_inner_sizes)
        self.refuse_send = refuse_send
        self.crosstalk = crosstalk
        self.adapters: list = []
        self.received_frames = 0
        self.received_datagrams = 0
        self.undeliverable_returns = 0

    def attach(self, max_datagram_size_bytes: int = None) -> LoopbackAdapter:
        adapter = LoopbackAdapter(
            self,
            (
                self.max_datagram_size_bytes
                if max_datagram_size_bytes is None
                else max_datagram_size_bytes
            ),
        )
        self.adapters.append(adapter)
        return adapter

    def deliver(self, source: LoopbackAdapter, frame: bytes) -> None:
        """Accept one browser-to-server frame and queue its echoes."""
        if self.refuse_send:
            raise AdapterSendError("the transport refused the frame")
        # A malformed browser-to-server frame is a defect in the client under
        # test, not something to absorb quietly.
        decoded = decode_frame(frame, BROWSER_TO_SERVER, MAX_LENGTH_PREFIX_VALUE)
        self.received_frames += 1
        self.received_datagrams += len(decoded.datagrams)
        if not self.echo:
            return
        for payload in decoded.datagrams:
            if len(payload) in self.drop_inner_sizes:
                continue
            for returned in self._return_frames(payload):
                self._queue(source, returned)

    def _return_frames(self, payload: bytes) -> list:
        prefix = self.return_prefix
        if self.fault == FAULT_FOREIGN_PREFIX:
            prefix = bytes((value ^ 0xFF) for value in self.return_prefix)
        if self.fault == FAULT_CORRUPT_PAYLOAD and payload:
            payload = payload[:-1] + bytes([payload[-1] ^ 0xFF])
        if self.fault == FAULT_HEADER_ONLY_RETURN:
            return [prefix]
        if self.fault == FAULT_PACKED_RETURN:
            # Two inner datagrams in one return frame, which the contract's
            # exactly-one rule forbids.
            return [
                encode_frame(prefix, (payload, payload), BROWSER_TO_SERVER),
            ]
        if self.fault == FAULT_DECLARED_OVERSIZE:
            return [prefix + b"\xff\xff" + payload]
        frame = encode_frame(prefix, (payload,), SERVER_TO_BROWSER)
        if self.fault == FAULT_TRUNCATED_RETURN:
            frame = frame[:-1]
        return [frame]

    def _queue(self, source: LoopbackAdapter, frame: bytes) -> None:
        targets = self.adapters if self.crosstalk else [source]
        for adapter in targets:
            if len(frame) > adapter.max_datagram_size_bytes:
                # The return path cannot carry it either. A real path drops it
                # and the case times out; that is a measurement result.
                self.undeliverable_returns += 1
                continue
            adapter.inbox.append(frame)


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
