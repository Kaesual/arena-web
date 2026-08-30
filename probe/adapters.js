// SPDX-License-Identifier: GPL-2.0-or-later
//
// The two transports the session driver can run over.
//
// `LoopbackAdapter` is an in-memory relay plus echo destination. It models the
// same session semantics a routed relay has — an in-band address exchange, an
// in-band refusal that terminates the session, structured relay headers and a
// return path that wraps each echo on its own — so the probe can prove its
// framing, its setup and its driver before any network is touched.
// `WebTransportAdapter` is the real one. Both expose the same three things the
// probe uses: `maxDatagramSizeBytes`, `send(datagram)` and a way to read.

import {
  BROWSER_TO_SERVER,
  ERROR_DESTINATION_UNAVAILABLE,
  ERROR_INVALID_AUTHORIZATION,
  MAX_LENGTH_PREFIX_VALUE,
  SERVER_TO_BROWSER,
  TYPE_KEEP_ALIVE,
  TYPE_RELAY_PACKET,
  TYPE_REQUEST_ADDRESS,
  RelayProbeError,
  RelaySessionError,
  datagramType,
  decodeFrame,
  decodeRelayHeader,
  encodeAddressAssignment,
  encodeError,
  encodeFrame,
  encodeKeepAlive,
  encodeRelayHeader,
  hexToBytes,
} from "./relay-framing.js";
import { AdapterSendError, SessionDriver, SessionHandshake } from "./measurement.js";

// The same fault names scripts/relay_loopback.py uses, so a fault run can be
// compared across the two implementations.
export const FAULT_NONE = "";
export const FAULT_TRUNCATED_RETURN = "truncatedReturn";
export const FAULT_PACKED_RETURN = "packedReturn";
export const FAULT_CORRUPT_PAYLOAD = "corruptPayload";
export const FAULT_FOREIGN_HEADER = "foreignHeader";
export const FAULT_HEADER_ONLY_RETURN = "headerOnlyReturn";
export const FAULT_DECLARED_OVERSIZE = "declaredOversize";
// The behaviour the 2026-08-30 amendment removed: a zero-length inner datagram
// never reaches the destination, so the committed vector's 0-byte case can only
// be recorded as unanswered.
export const FAULT_DROP_ZERO_LENGTH = "dropZeroLengthInner";

// Addresses from the IPv6 documentation prefix 2001:db8::/32 (RFC 3849), which
// is reserved for examples and is never routable. They stand in for values the
// integration environment supplies at runtime.
export const SYNTHETIC_CLIENT_ADDRESS = hexToBytes(
  "20010db8000000000000000000000001",
);
export const SYNTHETIC_DESTINATION_ADDRESS = hexToBytes(
  "20010db8000000000000000000000002",
);
export const SYNTHETIC_FOREIGN_ADDRESS = hexToBytes(
  "20010db8000000000000000000000003",
);
export const SYNTHETIC_CLIENT_PORT = 49152;
export const SYNTHETIC_DESTINATION_PORT = 40000;
export const SYNTHETIC_AUTHORIZATION = "loopback-one-time-authorization";

export class LoopbackAdapter {
  constructor(maxDatagramSizeBytes, options = {}) {
    this.maxDatagramSizeBytes = maxDatagramSizeBytes;
    this.clientAddress = options.clientAddress || SYNTHETIC_CLIENT_ADDRESS;
    this.clientPort =
      options.clientPort === undefined ? SYNTHETIC_CLIENT_PORT : options.clientPort;
    this.destinationAddress =
      options.destinationAddress || SYNTHETIC_DESTINATION_ADDRESS;
    this.destinationPort =
      options.destinationPort === undefined
        ? SYNTHETIC_DESTINATION_PORT
        : options.destinationPort;
    // The destination answers from its own UDP port, which a client cannot
    // predict from the virtual port it addressed. Making the two differ by
    // default is what keeps the return-header check honest.
    this.destinationReplyPort =
      options.destinationReplyPort === undefined
        ? this.destinationPort + 1
        : options.destinationReplyPort;
    this.authorization =
      options.authorization === undefined
        ? SYNTHETIC_AUTHORIZATION
        : options.authorization;
    this.refuseAuthorization = options.refuseAuthorization === true;
    this.echo = options.echo === undefined ? true : options.echo;
    this.fault = options.fault || FAULT_NONE;
    this.dropInnerSizes = new Set(options.dropInnerSizes || []);
    this.refuseSend = options.refuseSend === true;
    this.assignedAddress = null;
    this.terminated = false;
    this.inbox = [];
    this.addressRequests = 0;
    this.refusedAuthorizations = 0;
    this.refusedDestinations = 0;
    this.droppedBeforeAssignment = 0;
    this.droppedZeroLength = 0;
    this.keepAlivesAnswered = 0;
    this.receivedDatagrams = 0;
    this.receivedFrames = 0;
    this.receivedInnerSizes = [];
    this.undeliverableReturns = 0;
    this.writeFailures = 0;
  }

  send(datagram) {
    if (datagram.length > this.maxDatagramSizeBytes) {
      this.writeFailures += 1;
      throw new AdapterSendError("datagram exceeds the transport maximum");
    }
    if (this.terminated) {
      this.writeFailures += 1;
      throw new AdapterSendError("the relay closed this session");
    }
    if (datagram.length < 4) {
      // Too short to carry a type: reported in band, not a close — the same
      // non-terminal report an unrecognised type receives.
      this.deliver(
        encodeError(ERROR_INVALID_AUTHORIZATION, "loopback-malformed-datagram"),
      );
      return;
    }
    const kind = datagramType(datagram);
    if (kind === TYPE_RELAY_PACKET && this.refuseSend) {
      // A transport that starts refusing writes once the session is measuring.
      // Refusing the setup exchange as well would model a session that never
      // opened, which is not what this fault is for.
      this.writeFailures += 1;
      throw new AdapterSendError("the transport refused the datagram");
    }
    if (kind === TYPE_REQUEST_ADDRESS) {
      this.handleAddressRequest(datagram);
      return;
    }
    if (kind === TYPE_KEEP_ALIVE) {
      this.keepAlivesAnswered += 1;
      this.deliver(encodeKeepAlive(new Uint8Array(16)));
      return;
    }
    if (kind === TYPE_RELAY_PACKET) {
      this.handleRelay(datagram);
      return;
    }
    this.deliver(encodeError(ERROR_INVALID_AUTHORIZATION, "loopback-unrecognised-type"));
  }

  handleAddressRequest(datagram) {
    this.addressRequests += 1;
    const offered = new TextDecoder().decode(datagram.subarray(4));
    if (this.refuseAuthorization || offered !== this.authorization) {
      // In band, then terminal: the session is unusable afterwards.
      this.refusedAuthorizations += 1;
      this.terminated = true;
      this.deliver(
        encodeError(ERROR_INVALID_AUTHORIZATION, "loopback-refusal-message"),
      );
      return;
    }
    this.assignedAddress = this.clientAddress;
    this.deliver(encodeAddressAssignment(this.assignedAddress));
  }

  handleRelay(frame) {
    if (this.assignedAddress === null) {
      // A relay packet before assignment is dropped without an answer.
      this.droppedBeforeAssignment += 1;
      return;
    }
    const decoded = decodeFrame(frame, BROWSER_TO_SERVER, MAX_LENGTH_PREFIX_VALUE);
    const header = decodeRelayHeader(decoded.prefix);
    if (
      !bytesMatch(header.destinationAddress, this.destinationAddress) ||
      header.destinationPort !== this.destinationPort
    ) {
      // An unknown and an unauthorized destination are the same answer.
      this.refusedDestinations += 1;
      this.deliver(
        encodeError(ERROR_DESTINATION_UNAVAILABLE, "loopback-unavailable-message"),
      );
      return;
    }
    this.receivedFrames += 1;
    this.receivedDatagrams += decoded.datagrams.length;
    for (const payload of decoded.datagrams) {
      this.receivedInnerSizes.push(payload.length);
    }
    if (!this.echo) {
      return;
    }
    for (const payload of decoded.datagrams) {
      if (payload.length === 0 && this.fault === FAULT_DROP_ZERO_LENGTH) {
        this.droppedZeroLength += 1;
        continue;
      }
      if (this.dropInnerSizes.has(payload.length)) {
        continue;
      }
      for (const returned of this.returnFrames(payload, header.sourcePort)) {
        this.deliver(returned);
      }
    }
  }

  returnFrames(payload, clientPort) {
    const sourceAddress =
      this.fault === FAULT_FOREIGN_HEADER
        ? SYNTHETIC_FOREIGN_ADDRESS
        : this.destinationAddress;
    const prefix = encodeRelayHeader({
      destinationAddress: this.clientAddress,
      destinationPort: clientPort,
      sourceAddress,
      sourcePort: this.destinationReplyPort,
    });
    if (this.fault === FAULT_CORRUPT_PAYLOAD && payload.length > 0) {
      const copy = payload.slice();
      copy[copy.length - 1] ^= 0xff;
      payload = copy;
    }
    if (this.fault === FAULT_HEADER_ONLY_RETURN) {
      return [prefix.slice()];
    }
    if (this.fault === FAULT_PACKED_RETURN) {
      return [encodeFrame(prefix, [payload, payload], BROWSER_TO_SERVER)];
    }
    if (this.fault === FAULT_DECLARED_OVERSIZE) {
      const frame = new Uint8Array(prefix.length + 2 + payload.length);
      frame.set(prefix, 0);
      frame[prefix.length] = 0xff;
      frame[prefix.length + 1] = 0xff;
      frame.set(payload, prefix.length + 2);
      return [frame];
    }
    const frame = encodeFrame(prefix, [payload], SERVER_TO_BROWSER);
    if (this.fault === FAULT_TRUNCATED_RETURN) {
      return [frame.subarray(0, frame.length - 1)];
    }
    return [frame];
  }

  deliver(datagram) {
    if (datagram.length > this.maxDatagramSizeBytes) {
      // The return path cannot carry it either. A real path drops it and the
      // case times out; that is a measurement result.
      this.undeliverableReturns += 1;
      return;
    }
    this.inbox.push(datagram);
  }

  drain() {
    const frames = this.inbox;
    this.inbox = [];
    return frames;
  }
}

function bytesMatch(left, right) {
  if (left.length !== right.length) {
    return false;
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false;
    }
  }
  return true;
}

// Runs the in-band setup exchange against the in-memory relay and returns the
// driver it produced. Every loopback session goes through the same handshake a
// routed one does, so the setup path is exercised rather than assumed.
export function openLoopbackSession(
  adapter,
  plan,
  config,
  sessionNonce,
  sessionIndex = 0,
) {
  const handshake = new SessionHandshake(config);
  adapter.send(handshake.requestDatagram());
  for (const datagram of adapter.drain()) {
    if (handshake.accept(datagram) !== null) {
      break;
    }
  }
  return new SessionDriver(
    plan,
    adapter,
    sessionNonce,
    config,
    handshake.routingContext(),
    sessionIndex,
  );
}

// Drives one session to completion on a virtual clock, with no timers and no
// network. Used by the startup self-test.
export function runLoopbackSession(driver, adapter, stepMs = 1, maxSteps = 100000) {
  let now = 0;
  driver.pump(now);
  for (let step = 0; step < maxSteps; step += 1) {
    if (driver.finished) {
      return now;
    }
    for (const frame of adapter.drain()) {
      driver.receive(frame, now);
    }
    now += stepMs;
    driver.pump(now);
  }
  throw new RelayProbeError(
    "loopback session did not finish within the step budget",
  );
}

export class WebTransportAdapter {
  constructor(transport, writer, reader, maxDatagramSizeBytes) {
    this.transport = transport;
    this.writer = writer;
    this.reader = reader;
    this.maxDatagramSizeBytes = maxDatagramSizeBytes;
    this.writeFailed = false;
    this.writeFailures = 0;
  }

  // Construction and readiness are wrapped because platform errors quote the
  // URL they failed on: Chromium's TypeError for an invalid URL, or for a URL
  // carrying a fragment, embeds the whole thing. That message must never escape
  // this function. Only the error's class name is carried out; the message is
  // dropped on the floor here. The endpoint no longer carries the
  // authorization, but it is still environment detail that must not be logged.
  static async connect(config) {
    if (typeof WebTransport === "undefined") {
      throw new RelayProbeError("this browser has no WebTransport");
    }
    const options = {};
    if (config.certificateHashes.length > 0) {
      options.serverCertificateHashes = config.certificateHashes.map((value) => ({
        algorithm: "sha-256",
        value: hexToBytes(value),
      }));
    }
    let transport;
    try {
      transport = new WebTransport(config.endpointUrl, options);
      await transport.ready;
    } catch (error) {
      const name = error && error.name ? ` (${error.name})` : "";
      throw new RelayProbeError(
        `the transport refused to open a session${name}`,
      );
    }
    const maxDatagramSizeBytes = transport.datagrams.maxDatagramSize;
    if (!Number.isInteger(maxDatagramSizeBytes) || maxDatagramSizeBytes <= 0) {
      try {
        transport.close();
      } catch (error) {
        // Already gone; nothing to close.
      }
      throw new RelayProbeError(
        "the transport did not report a usable maxDatagramSize; refusing to " +
          "guess one",
      );
    }
    // One reader for the whole session: the setup exchange and the measurement
    // read the same datagram stream, and a second getReader() would throw.
    const writer = transport.datagrams.writable.getWriter();
    const reader = transport.datagrams.readable.getReader();
    return new WebTransportAdapter(
      transport,
      writer,
      reader,
      maxDatagramSizeBytes,
    );
  }

  // Datagram writes resolve once the datagram is queued, so a rejection is
  // observed after the fact. A rejected write marks the adapter failed and every
  // later send is refused; the case that caused it is recorded as a timeout
  // rather than a send failure. The size check above catches the failure mode
  // this probe is actually measuring.
  send(frame) {
    if (this.writeFailed) {
      throw new AdapterSendError("an earlier datagram write failed");
    }
    this.writer.write(frame).catch(() => {
      this.writeFailed = true;
      this.writeFailures += 1;
    });
  }

  // One datagram, or null once the deadline passes. Used only for the setup
  // exchange, which is strictly request/response.
  async receiveOne(timeoutMs) {
    let timer = null;
    const expiry = new Promise((resolve) => {
      timer = setTimeout(() => resolve({ expired: true }), timeoutMs);
    });
    try {
      const result = await Promise.race([this.reader.read(), expiry]);
      if (result.expired || result.done) {
        return null;
      }
      return result.value;
    } finally {
      if (timer !== null) {
        clearTimeout(timer);
      }
    }
  }

  async readFrames(onFrame, shouldStop) {
    for (;;) {
      if (shouldStop()) {
        return;
      }
      const { value, done } = await this.reader.read();
      if (done) {
        return;
      }
      onFrame(value);
    }
  }

  async close() {
    try {
      this.reader.cancel();
    } catch (error) {
      // The session is already gone; nothing to cancel.
    }
    try {
      await this.writer.close();
    } catch (error) {
      // Closing an already-failed writer is not an error worth reporting.
    }
    try {
      this.transport.close();
    } catch (error) {
      // Same for an already-closed session.
    }
  }
}

// A session's setup exchange over a real transport: one REQUEST_ADDRESS
// carrying the single-use authorization, then the assignment that names the
// virtual client address. A refusal or a silent relay ends the attempt here,
// before any measurement traffic exists.
export async function establishSession(adapter, config) {
  const handshake = new SessionHandshake(config);
  adapter.send(handshake.requestDatagram());
  const deadline = Date.now() + config.assignmentTimeoutMilliseconds;
  for (;;) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      throw new RelaySessionError("the relay assigned no address in time");
    }
    const datagram = await adapter.receiveOne(remaining);
    if (datagram === null) {
      throw new RelaySessionError("the relay assigned no address in time");
    }
    if (handshake.accept(datagram) !== null) {
      return handshake;
    }
  }
}
