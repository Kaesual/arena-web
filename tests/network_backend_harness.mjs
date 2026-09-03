// SPDX-License-Identifier: GPL-2.0-or-later
// Deterministic, network-free tests for the product WebTransport backend.

import assert from "node:assert/strict";
import {
  ArenaNetworkBackend,
  ArenaNetworkSession,
  CLOSE_DRAIN_MILLISECONDS,
  PathBudgetError,
  RelayClosedError,
  RelayConfigurationError,
  SEND_ACCEPTED,
  SEND_BACKPRESSURE,
  SEND_BUDGET,
  SEND_DESTINATION,
  SEND_OVERSIZE,
  SEND_UNAVAILABLE,
} from "../arena/network-backend.js";
import {
  BROWSER_TO_SERVER,
  SERVER_TO_BROWSER,
  TYPE_KEEP_ALIVE,
  TYPE_RELAY_PACKET,
  TYPE_REQUEST_ADDRESS,
  datagramType,
  decodeFrame,
  decodeRelayHeader,
  encodeAddressAssignment,
  encodeError,
  encodeFrame,
  encodeKeepAlive,
  encodeRelayHeader,
  hexToBytes,
} from "../probe/relay-framing.js";

const CLIENT = hexToBytes("20010db8000000000000000000000001");
const DESTINATION = hexToBytes("20010db8000000000000000000000002");
const FOREIGN = hexToBytes("20010db8000000000000000000000003");
const DESTINATION_HEX = "20010db8000000000000000000000002";
const DESTINATION_PORT = 40000;
let nextSourcePort = 50000;

class QueueReader {
  constructor() {
    this.items = [];
    this.waiters = [];
    this.done = false;
  }

  read() {
    if (this.items.length > 0) {
      return Promise.resolve({ value: this.items.shift(), done: false });
    }
    if (this.done) {
      return Promise.resolve({ value: undefined, done: true });
    }
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  push(value) {
    if (this.waiters.length > 0) {
      this.waiters.shift()({ value, done: false });
    } else {
      this.items.push(value);
    }
  }

  finish() {
    this.done = true;
    for (const resolve of this.waiters.splice(0)) {
      resolve({ value: undefined, done: true });
    }
  }

  async cancel() {
    this.finish();
  }
}

const transportPlans = [];

class FakeWebTransport {
  constructor() {
    const plan = transportPlans.shift() ?? {};
    this.reader = new QueueReader();
    this.writes = [];
    this.writerAborts = 0;
    this.writerCloses = 0;
    this.transportCloses = 0;
    this.datagrams = {
      maxDatagramSize: plan.maxDatagramSize ?? 1024,
      readable: { getReader: () => this.reader },
      writable: {
        getWriter: () => ({
          write: async (datagram) => {
            if (
              plan.rejectWrites ||
              (plan.rejectRelayWrites && datagramType(datagram) === TYPE_RELAY_PACKET)
            ) {
              throw new Error("synthetic write failure");
            }
            if (
              plan.relayWriteGate &&
              datagramType(datagram) === TYPE_RELAY_PACKET
            ) {
              await plan.relayWriteGate;
            }
            const copy = datagram.slice();
            this.writes.push(copy);
            if (datagramType(copy) === TYPE_REQUEST_ADDRESS) {
              if (plan.keepAliveBeforeAssignment) {
                this.reader.push(encodeKeepAlive());
              }
              if (plan.beforeAssignment) {
                this.reader.push(plan.beforeAssignment);
              } else if (plan.assignmentError) {
                this.reader.push(encodeError(7, "synthetic refusal"));
              } else if (!plan.noAssignment) {
                this.reader.push(encodeAddressAssignment(CLIENT));
              }
            }
          },
          close: async () => {
            this.writerCloses += 1;
            if (plan.hangWriterClose) {
              await new Promise(() => {});
            }
          },
          abort: async () => {
            this.writerAborts += 1;
          },
        }),
      },
    };
    this.ready = plan.rejectReady
      ? Promise.reject(new Error("synthetic ready failure"))
      : Promise.resolve();
    this.closed = new Promise((resolve) => {
      this.resolveClosed = resolve;
    });
    plan.capture?.(this);
  }

  close() {
    this.transportCloses += 1;
    this.reader.finish();
    this.resolveClosed();
  }
}

function config(overrides = {}) {
  return {
    endpointUrl: "https://relay.invalid/session",
    certificateHashes: [],
    destinationAddressHex: DESTINATION_HEX,
    destinationPort: DESTINATION_PORT,
    clientSourcePort: nextSourcePort++,
    keepAliveIntervalMilliseconds: 0,
    assignmentTimeoutMilliseconds: 100,
    tokenProvider: async () => "synthetic-single-use-token",
    ...overrides,
  };
}

async function openBackend(overrides = {}, plan = {}) {
  let transport;
  const events = [];
  transportPlans.push({ ...plan, capture: (value) => (transport = value) });
  const backend = new ArenaNetworkBackend(config(overrides), {
    WebTransportClass: FakeWebTransport,
    onEvent: (kind, detail) => events.push({ kind, detail }),
  });
  await backend.open();
  return { backend, transport, events };
}

function engineAddress(overrides = {}) {
  return {
    type: 5,
    port: DESTINATION_PORT,
    scopeId: 0,
    ipv4: [0, 0, 0, 0],
    ipv6: Array.from(DESTINATION),
    ...overrides,
  };
}

function returnFrame(payload, sourcePort, sourceAddress = DESTINATION) {
  const header = encodeRelayHeader({
    destinationAddress: CLIENT,
    destinationPort: sourcePort,
    sourceAddress,
    sourcePort: DESTINATION_PORT + 1,
  });
  return encodeFrame(header, [payload], SERVER_TO_BROWSER);
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
}

let checks = 0;

{
  const { backend } = await openBackend({}, { keepAliveBeforeAssignment: true });
  assert.equal(backend.snapshot().assignments, 1);
  assert.equal(backend.snapshot().keepAlivesReceived, 1);
  await backend.close();
  checks += 1;
}

{
  let tokenCalls = 0;
  const secret = "never-report-this-token";
  const { backend, transport } = await openBackend({
    tokenProvider: async () => {
      tokenCalls += 1;
      return secret;
    },
  });
  assert.equal(tokenCalls, 1);
  assert.equal(datagramType(transport.writes[0]), TYPE_REQUEST_ADDRESS);
  assert.equal(new TextDecoder().decode(transport.writes[0].subarray(4)), secret);
  assert.equal(JSON.stringify(backend.snapshot()).includes(secret), false);
  assert.equal(backend.snapshot().assignments, 1);
  await backend.close();
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  const payload = Uint8Array.from({ length: 704 }, (_, index) => index & 0xff);
  assert.equal(backend.sendFromEngine(engineAddress(), payload, 0), SEND_ACCEPTED);
  await settle();
  const decoded = decodeFrame(transport.writes.at(-1), BROWSER_TO_SERVER, 982);
  assert.equal(decoded.datagrams.length, 1);
  assert.deepEqual(decoded.datagrams[0], payload);
  assert.equal(transport.writes.at(-1).length, payload.length + 42);
  assert.equal(sourcePort > 32767, true);
  assert.equal(decodeRelayHeader(decoded.prefix).sourcePort, sourcePort);
  await backend.close();
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  transport.reader.push(encodeError(9, "x").slice(0, 5));
  await settle();
  assert.equal(backend.snapshot().terminalReason, "invalid_return_frame");
  assert.equal(backend.snapshot().invalidReturnFrames, 1);
  checks += 1;
}

{
  const { backend } = await openBackend();
  assert.equal(
    backend.sendFromEngine(engineAddress({ port: DESTINATION_PORT + 1 }), new Uint8Array(1), 0),
    SEND_DESTINATION,
  );
  assert.equal(backend.snapshot().originatedRefusals, 1);
  assert.equal(backend.snapshot().refusals.originated.destination, 1);
  assert.equal(
    backend.sendFromEngine(engineAddress(), new Uint8Array(983), 1),
    SEND_OVERSIZE,
  );
  assert.equal(backend.snapshot().elicitedRefusals, 1);
  assert.equal(backend.snapshot().refusals.elicited.oversize, 1);
  assert.equal(backend.snapshot().state, "open");
  await backend.close();
  checks += 1;
}

{
  let releaseWrite;
  const relayWriteGate = new Promise((resolve) => {
    releaseWrite = resolve;
  });
  const { backend } = await openBackend({}, { relayWriteGate });
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(1), 0),
    SEND_ACCEPTED,
  );
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(2), 0),
    SEND_ACCEPTED,
  );
  await settle();
  await backend.close();
  releaseWrite();
  await settle();
  await settle();
  assert.equal(backend.snapshot().cancelledAcceptedWrites, 2);
  assert.equal(backend.snapshot().refusals.originated.closed, 2);
  checks += 1;
}

{
  const relayWriteGate = new Promise(() => {});
  const { backend, transport } = await openBackend(
    {},
    { relayWriteGate, hangWriterClose: true },
  );
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(1), 0),
    SEND_ACCEPTED,
  );
  await settle();
  await backend.close();
  assert.equal(transport.transportCloses, 1);
  assert.equal(transport.writerCloses, 0);
  assert.equal(transport.writerAborts, 1);
  assert.equal(backend.snapshot().pendingWrites, 0);
  checks += 1;
}

{
  let releaseWrite;
  const relayWriteGate = new Promise((resolve) => {
    releaseWrite = resolve;
  });
  const { backend, transport } = await openBackend({}, { relayWriteGate });
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(1), 0),
    SEND_ACCEPTED,
  );
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(2), 0),
    SEND_ACCEPTED,
  );
  await settle();
  transport.datagrams.maxDatagramSize = 809;
  releaseWrite();
  await settle();
  await settle();
  assert.equal(
    transport.writes.filter((frame) => datagramType(frame) === TYPE_RELAY_PACKET).length,
    1,
  );
  assert.equal(backend.snapshot().terminalReason, "path_budget");
  assert.equal(backend.snapshot().refusals.originated.path_budget, 1);
  checks += 1;
}

{
  for (const endpointUrl of [
    "https://relay.invalid/session?authorization=forbidden",
    "https://relay.invalid/session#forbidden",
  ]) {
    assert.throws(
      () => new ArenaNetworkBackend(config({ endpointUrl })),
      RelayConfigurationError,
    );
  }
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  transport.reader.push(encodeKeepAlive());
  await settle();
  assert.equal(backend.snapshot().keepAlivesReceived, 1);
  assert.equal(backend.snapshot().state, "open");
  transport.reader.push(encodeAddressAssignment(CLIENT));
  await settle();
  assert.equal(backend.snapshot().terminalReason, "invalid_return_frame");
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  transport.reader.push(encodeError(9, "synthetic terminal error"));
  await settle();
  assert.equal(backend.snapshot().terminalReason, "relay_error");
  checks += 1;
}

{
  transportPlans.push({ noAssignment: true });
  const backend = new ArenaNetworkBackend(config({ assignmentTimeoutMilliseconds: 1 }), {
    WebTransportClass: FakeWebTransport,
  });
  await assert.rejects(backend.open());
  assert.equal(backend.snapshot().terminalReason, "open_failed");
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const premature = returnFrame(Uint8Array.of(1), sourcePort);
  transportPlans.push({ beforeAssignment: premature });
  const backend = new ArenaNetworkBackend(config({ clientSourcePort: sourcePort }), {
    WebTransportClass: FakeWebTransport,
  });
  await assert.rejects(backend.open());
  assert.equal(backend.snapshot().terminalReason, "open_failed");
  checks += 1;
}

{
  const { backend } = await openBackend();
  assert.equal(
    backend.sendFromEngine(engineAddress({ type: 4 }), Uint8Array.of(1), 0),
    SEND_DESTINATION,
  );
  assert.equal(
    backend.sendFromEngine(engineAddress(), "not bytes", 0),
    SEND_UNAVAILABLE,
  );
  assert.equal(backend.snapshot().refusals.originated.destination, 1);
  assert.equal(backend.snapshot().refusals.originated.unavailable, 1);
  await backend.close();
  checks += 1;
}

{
  let tokenCalls = 0;
  transportPlans.push({ maxDatagramSize: 809 });
  const backend = new ArenaNetworkBackend(
    config({
      tokenProvider: async () => {
        tokenCalls += 1;
        return "one-use";
      },
    }),
    { WebTransportClass: FakeWebTransport },
  );
  await assert.rejects(
    backend.open(),
    (error) => error instanceof PathBudgetError && error.retry === false,
  );
  assert.equal(tokenCalls, 1);
  assert.equal(backend.snapshot().terminalReason, "path_budget");
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  const header = encodeRelayHeader({
    destinationAddress: CLIENT,
    destinationPort: sourcePort,
    sourceAddress: DESTINATION,
    sourcePort: DESTINATION_PORT + 1,
  });
  transport.reader.push(header);
  await settle();
  assert.equal(backend.snapshot().terminalReason, "invalid_return_frame");
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  const header = encodeRelayHeader({
    destinationAddress: CLIENT,
    destinationPort: sourcePort,
    sourceAddress: DESTINATION,
    sourcePort: DESTINATION_PORT + 1,
  });
  const two = new Uint8Array(header.length + 6);
  two.set(header);
  two.set([0, 1, 1, 0, 1, 2], header.length);
  transport.reader.push(two);
  await settle();
  assert.equal(backend.snapshot().terminalReason, "invalid_return_frame");
  checks += 1;
}

{
  const { backend } = await openBackend();
  backend.refuseReceivedForEngine("engine_capacity");
  backend.refuseReceivedForEngine("invalid_payload");
  assert.equal(backend.snapshot().engineReceiveRefusals.engine_capacity, 1);
  assert.equal(backend.snapshot().engineReceiveRefusals.invalid_payload, 1);
  await backend.close();
  checks += 1;
}

{
  const { backend, transport } = await openBackend();
  transport.datagrams.maxDatagramSize = 809;
  assert.equal(
    backend.sendFromEngine(engineAddress(), new Uint8Array(1), 0),
    SEND_BUDGET,
  );
  await settle();
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "path_budget");
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  transport.datagrams.maxDatagramSize = 809;
  transport.reader.push(returnFrame(Uint8Array.of(1), sourcePort));
  await settle();
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "path_budget");
  assert.equal(backend.snapshot().invalidReturnFrames, 0);
  checks += 1;
}

{
  let tokenCalls = 0;
  transportPlans.push({}, {});
  const session = new ArenaNetworkSession(
    config({
      tokenProvider: async () => {
        tokenCalls += 1;
        return `fresh-attempt-${tokenCalls}`;
      },
    }),
    { WebTransportClass: FakeWebTransport },
  );
  await session.open();
  assert.equal(
    session.sendFromEngine(
      engineAddress({ port: DESTINATION_PORT + 1 }),
      Uint8Array.of(1),
      0,
    ),
    SEND_DESTINATION,
  );
  await session.reconnect();
  assert.equal(tokenCalls, 2);
  assert.equal(session.snapshot().sessionAttempts, 2);
  assert.equal(session.snapshot().sessionReconnects, 1);
  assert.equal(session.snapshot().originatedRefusals, 1);
  assert.equal(session.snapshot().refusals.originated.destination, 1);
  await session.close();
  checks += 1;
}

{
  const sourcePort = nextSourcePort;
  const { backend, transport } = await openBackend({ clientSourcePort: sourcePort });
  for (let index = 0; index < 257; index += 1) {
    transport.reader.push(returnFrame(Uint8Array.of(index & 0xff), sourcePort));
  }
  await settle();
  assert.equal(backend.snapshot().queueHighWatermark, 256);
  assert.equal(backend.snapshot().queueOverflows, 1);
  for (let index = 0; index < 256; index += 1) {
    assert.deepEqual(backend.receiveForEngine(), Uint8Array.of(index & 0xff));
  }
  assert.equal(backend.receiveForEngine(), null);
  await backend.close();
  checks += 1;
}

// A frame that parses but is addressed elsewhere is counted and dropped, and
// the session goes on. Both shapes are here because both are real: a foreign
// source is another game server, and a stale destination port is this client's
// own predecessor, which is what the relay's own reconnect defect produced.
{
  const sourcePort = nextSourcePort;
  const { backend, transport, events } = await openBackend({ clientSourcePort: sourcePort });
  const before = events.length;
  transport.reader.push(returnFrame(Uint8Array.of(1), sourcePort, FOREIGN));
  transport.reader.push(returnFrame(Uint8Array.of(2), sourcePort + 1));
  transport.reader.push(returnFrame(Uint8Array.of(3), sourcePort));
  await settle();
  assert.equal(backend.snapshot().state, "open");
  assert.equal(backend.snapshot().terminalReason, null);
  assert.equal(backend.snapshot().foreignReturnFrames, 2);
  assert.equal(backend.snapshot().invalidReturnFrames, 0);
  assert.equal(backend.snapshot().receivedInnerDatagrams, 1);
  assert.deepEqual(backend.receiveForEngine(), Uint8Array.of(3));
  assert.equal(backend.receiveForEngine(), null);
  // Counted in the snapshot and announced nowhere. A predecessor's tail is a
  // burst, and the host publishes a whole snapshot per event, so an event each
  // would cost a sort and a cross-window render per stray packet and would
  // crowd real faults out of the bounded event list.
  assert.equal(events.length, before);
  await backend.close();
  checks += 1;
}

// The engine's own close flushes what the engine already queued. This is the
// ghost-client fix: ioquake3 enqueues its `disconnect` and shuts the network
// down in the same synchronous call, so the datagrams are still pending here.
{
  let releaseWrite;
  const relayWriteGate = new Promise((resolve) => {
    releaseWrite = resolve;
  });
  const { backend, transport } = await openBackend({}, { relayWriteGate });
  for (const value of [1, 2, 3]) {
    assert.equal(
      backend.sendFromEngine(engineAddress(), Uint8Array.of(value), 0),
      SEND_ACCEPTED,
    );
  }
  await settle();
  backend.closeFromEngine();
  await settle();
  assert.equal(backend.snapshot().state, "open");
  releaseWrite();
  // The host closes after the engine has, which is the order the loader stops
  // in; it must join the engine's drain rather than terminate underneath it.
  await backend.close();
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "engine_shutdown");
  assert.equal(backend.snapshot().cancelledAcceptedWrites, 0);
  assert.equal(backend.snapshot().writtenInnerDatagrams, 3);
  assert.equal(
    transport.writes.filter((frame) => datagramType(frame) === TYPE_RELAY_PACKET).length,
    3,
  );
  checks += 1;
}

// ...and it is bounded, so a path that never accepts the write cannot hold the
// session open. The queued datagram is cancelled exactly as any other close
// cancels it; what the drain buys is the case where the path still works.
{
  const relayWriteGate = new Promise(() => {});
  const { backend } = await openBackend({}, { relayWriteGate });
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(1), 0),
    SEND_ACCEPTED,
  );
  await settle();
  const startedAt = Date.now();
  backend.closeFromEngine();
  await backend.close();
  const elapsed = Date.now() - startedAt;
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "engine_shutdown");
  assert.equal(backend.snapshot().cancelledAcceptedWrites, 1);
  assert.ok(
    elapsed >= CLOSE_DRAIN_MILLISECONDS - 20 && elapsed < CLOSE_DRAIN_MILLISECONDS * 8,
    `the bounded drain took ${elapsed}ms`,
  );
  checks += 1;
}

{
  let tokenCalls = 0;
  const sourcePort = nextSourcePort++;
  const first = await openBackend({
    clientSourcePort: sourcePort,
    tokenProvider: async () => {
      tokenCalls += 1;
      return "first-one-use";
    },
  });
  const concurrent = new ArenaNetworkBackend(
    config({
      clientSourcePort: sourcePort,
      tokenProvider: async () => {
        tokenCalls += 1;
        return "must-not-be-requested-concurrently";
      },
    }),
    { WebTransportClass: FakeWebTransport },
  );
  await assert.rejects(concurrent.open(), RelayConfigurationError);
  assert.equal(tokenCalls, 1);
  await first.backend.close();
  const second = await openBackend({
    clientSourcePort: sourcePort,
    tokenProvider: async () => {
      tokenCalls += 1;
      return "fresh-token-after-close";
    },
  });
  assert.equal(tokenCalls, 2);
  await second.backend.close();
  checks += 1;
}

{
  const sourcePort = nextSourcePort++;
  let releaseToken;
  let markTokenRequested;
  const tokenRequested = new Promise((resolve) => {
    markTokenRequested = resolve;
  });
  const token = new Promise((resolve) => {
    releaseToken = resolve;
  });
  const backend = new ArenaNetworkBackend(
    config({
      clientSourcePort: sourcePort,
      tokenProvider: async () => {
        markTokenRequested();
        return token;
      },
    }),
    { WebTransportClass: FakeWebTransport },
  );
  const opening = backend.open();
  await tokenRequested;
  await backend.close();
  releaseToken("must-not-resurrect-the-closed-attempt");
  await assert.rejects(opening, RelayClosedError);
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "client_close");

  const replacement = await openBackend({ clientSourcePort: sourcePort });
  assert.equal(replacement.backend.snapshot().state, "open");
  await replacement.backend.close();
  checks += 1;
}

{
  const { backend } = await openBackend();
  for (let index = 0; index < 256; index += 1) {
    assert.equal(
      backend.sendFromEngine(engineAddress(), Uint8Array.of(index & 0xff), 0),
      SEND_ACCEPTED,
    );
  }
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(0xff), 0),
    SEND_BACKPRESSURE,
  );
  assert.equal(backend.snapshot().writeQueueHighWatermark, 256);
  assert.equal(backend.snapshot().writeQueueOverflows, 1);
  assert.equal(backend.snapshot().originatedRefusals, 1);
  await settle();
  assert.equal(backend.snapshot().writtenInnerDatagrams, 256);
  assert.equal(backend.snapshot().pendingWrites, 0);
  await backend.close();
  checks += 1;
}

{
  const timers = [];
  const realSetInterval = globalThis.setInterval;
  const realClearInterval = globalThis.clearInterval;
  globalThis.setInterval = (callback, milliseconds) => {
    const timer = { callback, milliseconds, cleared: false };
    timers.push(timer);
    return timer;
  };
  globalThis.clearInterval = (timer) => {
    timer.cleared = true;
  };
  try {
    const { backend, transport } = await openBackend({
      keepAliveIntervalMilliseconds: 5000,
    });
    timers.find((timer) => timer.milliseconds === 5000).callback();
    await settle();
    assert.equal(datagramType(transport.writes.at(-1)), TYPE_KEEP_ALIVE);
    assert.equal(backend.snapshot().keepAlivesSent, 1);
    await backend.close();
    assert.equal(timers.every((timer) => timer.cleared), true);
  } finally {
    globalThis.setInterval = realSetInterval;
    globalThis.clearInterval = realClearInterval;
  }
  checks += 1;
}

{
  const { backend } = await openBackend({}, { rejectRelayWrites: true });
  assert.equal(
    backend.sendFromEngine(engineAddress(), Uint8Array.of(1, 2, 3), 0),
    SEND_ACCEPTED,
  );
  await settle();
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "write_failure");
  assert.equal(backend.snapshot().writeFailures, 1);
  checks += 1;
}

{
  const { backend } = await openBackend();
  backend.closeFromEngine();
  await settle();
  assert.equal(backend.snapshot().state, "closed");
  assert.equal(backend.snapshot().terminalReason, "engine_shutdown");
  assert.equal(backend.receiveForEngine(), null);
  checks += 1;
}

process.stdout.write(JSON.stringify({ checks, passed: true }));
