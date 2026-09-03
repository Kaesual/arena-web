// SPDX-License-Identifier: GPL-2.0-or-later
//
// The product WebTransport datagram backend for the browser ioq3 build.
// ioq3 retains its datagram and netchan semantics; this module only owns the
// relay session, framing and the bounded async-to-polling receive queue.

import {
  BROWSER_TO_SERVER,
  SERVER_TO_BROWSER,
  SINGLE_DATAGRAM_OVERHEAD_BYTES,
  TYPE_ERROR,
  TYPE_KEEP_ALIVE,
  TYPE_RELAY_PACKET,
  bytesEqual,
  datagramType,
  decodeAddressAssignment,
  decodeError,
  decodeFrame,
  decodeRelayHeader,
  encodeAddressRequest,
  encodeFrame,
  encodeKeepAlive,
  hexToBytes,
  routingContext,
} from "../probe/relay-framing.js";

export const INNER_DATAGRAM_FLOOR = 768;
export const RECEIVE_QUEUE_DEPTH = 256;
export const WRITE_QUEUE_DEPTH = 256;
export const ASSIGNMENT_TIMEOUT_MILLISECONDS = 10000;
export const BUDGET_MONITOR_MILLISECONDS = 1000;
// How long a clean close waits for datagrams that were already accepted. A
// write only has to reach this browser's own send path, not the server, so it
// is generous for the handful of frames a shutdown queues; it is bounded at
// all because a stalled path must not be able to hold a stop open.
export const CLOSE_DRAIN_MILLISECONDS = 250;

const REFUSAL_REASONS = [
  "destination",
  "oversize",
  "unavailable",
  "path_budget",
  "closed",
  "backpressure",
  "write_failure",
];
const SESSION_SUM_FIELDS = [
  "opens",
  "assignments",
  "sentInnerDatagrams",
  "writtenInnerDatagrams",
  "receivedInnerDatagrams",
  "queueOverflows",
  "invalidReturnFrames",
  "foreignReturnFrames",
  "writeFailures",
  "writeQueueOverflows",
  "keepAlivesSent",
  "keepAlivesReceived",
  "originatedRefusals",
  "elicitedRefusals",
  "cancelledAcceptedWrites",
];
const SESSION_MAX_FIELDS = ["queueHighWatermark", "writeQueueHighWatermark"];

export const SEND_ACCEPTED = 0;
export const SEND_OVERSIZE = 1;
export const SEND_DESTINATION = 2;
export const SEND_UNAVAILABLE = 3;
export const SEND_BUDGET = 4;
export const SEND_CLOSED = 5;
export const SEND_BACKPRESSURE = 6;

const IOQ3_NA_IP6 = 5;
const activeSourcePorts = new Set();

export class ArenaNetworkError extends Error {}

export class RelayConfigurationError extends ArenaNetworkError {}

export class RelayAuthorizationError extends ArenaNetworkError {
  constructor(code = null) {
    super(code === null ? "relay authorization failed" : "relay authorization was refused");
    this.code = code;
  }
}

export class PathBudgetError extends ArenaNetworkError {
  constructor() {
    super("the WebTransport path is below the required datagram floor");
    this.retry = false;
  }
}

export class RelayClosedError extends ArenaNetworkError {
  constructor() {
    super("the relay session closed");
  }
}

function integer(value, name, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new RelayConfigurationError(`${name} is outside its accepted range`);
  }
  return value;
}

function configuration(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new RelayConfigurationError("relay configuration must be an object");
  }
  if (typeof input.endpointUrl !== "string" || input.endpointUrl.length === 0) {
    throw new RelayConfigurationError("relay endpoint is missing");
  }
  let endpoint;
  try {
    endpoint = new URL(input.endpointUrl);
  } catch (_) {
    throw new RelayConfigurationError("relay endpoint is not a URL");
  }
  if (
    endpoint.protocol !== "https:" ||
    endpoint.username !== "" ||
    endpoint.password !== "" ||
    endpoint.search !== "" ||
    endpoint.hash !== ""
  ) {
    throw new RelayConfigurationError(
      "relay endpoint must be a credential-free HTTPS URL without query or fragment",
    );
  }
  if (!Array.isArray(input.certificateHashes)) {
    throw new RelayConfigurationError("certificate hashes must be an array");
  }
  for (const digest of input.certificateHashes) {
    if (typeof digest !== "string" || !/^[0-9a-fA-F]{64}$/.test(digest)) {
      throw new RelayConfigurationError("a certificate hash is not a SHA-256 digest");
    }
  }
  const destinationAddress = hexToBytes(input.destinationAddressHex);
  if (destinationAddress.length !== 16 || destinationAddress.every((value) => value === 0)) {
    throw new RelayConfigurationError("the relay destination is not a usable IPv6 address");
  }
  if (typeof input.tokenProvider !== "function") {
    throw new RelayConfigurationError("a token-provider function is required");
  }
  const keepAliveIntervalMilliseconds = integer(
    input.keepAliveIntervalMilliseconds,
    "keep-alive interval",
    0,
    24 * 60 * 60 * 1000,
  );
  if (keepAliveIntervalMilliseconds > 0 && keepAliveIntervalMilliseconds < 1000) {
    throw new RelayConfigurationError("an enabled keep-alive interval must be at least one second");
  }
  return {
    endpointUrl: endpoint.href,
    certificateHashes: [...input.certificateHashes],
    destinationAddress,
    destinationPort: integer(input.destinationPort, "destination port", 1, 65535),
    clientSourcePort: integer(input.clientSourcePort, "client source port", 1, 65535),
    tokenProvider: input.tokenProvider,
    keepAliveIntervalMilliseconds,
    assignmentTimeoutMilliseconds:
      input.assignmentTimeoutMilliseconds === undefined
        ? ASSIGNMENT_TIMEOUT_MILLISECONDS
        : integer(
            input.assignmentTimeoutMilliseconds,
            "assignment timeout",
            1,
            60000,
          ),
  };
}

function certificateOptions(digests) {
  if (digests.length === 0) {
    return {};
  }
  return {
    serverCertificateHashes: digests.map((digest) => ({
      algorithm: "sha-256",
      value: hexToBytes(digest),
    })),
  };
}

function timeout(milliseconds) {
  let timer;
  const promise = new Promise((resolve) => {
    timer = setTimeout(() => resolve({ timeout: true }), milliseconds);
  });
  return { promise, cancel: () => clearTimeout(timer) };
}

export class ArenaNetworkBackend {
  constructor(rawConfiguration, options = {}) {
    this.config = configuration(rawConfiguration);
    this.WebTransportClass = options.WebTransportClass ?? globalThis.WebTransport;
    this.onEvent = typeof options.onEvent === "function" ? options.onEvent : () => {};
    this.transport = null;
    this.writer = null;
    this.reader = null;
    this.routing = null;
    this.queue = [];
    this.writeQueue = [];
    this.activeWrite = null;
    this.writeTask = null;
    this.closeTask = null;
    this.sourcePortRegistered = false;
    this.readTask = null;
    this.keepAliveTimer = null;
    this.budgetTimer = null;
    this.state = "new";
    this.stats = {
      state: "new",
      opens: 0,
      assignments: 0,
      sentInnerDatagrams: 0,
      writtenInnerDatagrams: 0,
      receivedInnerDatagrams: 0,
      queueHighWatermark: 0,
      queueOverflows: 0,
      invalidReturnFrames: 0,
      foreignReturnFrames: 0,
      writeFailures: 0,
      writeQueueHighWatermark: 0,
      writeQueueOverflows: 0,
      pendingWrites: 0,
      keepAlivesSent: 0,
      keepAlivesReceived: 0,
      originatedRefusals: 0,
      elicitedRefusals: 0,
      refusals: {
        originated: Object.fromEntries(REFUSAL_REASONS.map((reason) => [reason, 0])),
        elicited: Object.fromEntries(REFUSAL_REASONS.map((reason) => [reason, 0])),
      },
      cancelledAcceptedWrites: 0,
      engineReceiveRefusals: {
        invalid_payload: 0,
        engine_capacity: 0,
        poll_limit: 0,
      },
      terminalReason: null,
      acceptedInnerFloor: INNER_DATAGRAM_FLOOR,
      receiveQueueDepth: RECEIVE_QUEUE_DEPTH,
      writeQueueDepth: WRITE_QUEUE_DEPTH,
      keepAliveIntervalMilliseconds: this.config.keepAliveIntervalMilliseconds,
    };
  }

  #event(kind, detail = null) {
    try {
      this.onEvent(kind, detail);
    } catch (_) {
      // Evidence callbacks cannot be allowed to alter the transport state.
    }
  }

  #setState(state) {
    this.state = state;
    this.stats.state = state;
    this.#event("relay-state", state);
  }

  #reportedMaximum() {
    const value = this.transport?.datagrams?.maxDatagramSize;
    if (!Number.isInteger(value) || value <= SINGLE_DATAGRAM_OVERHEAD_BYTES) {
      throw new PathBudgetError();
    }
    return value;
  }

  currentInnerBudget() {
    if (!this.transport) {
      return 0;
    }
    try {
      return this.#reportedMaximum() - SINGLE_DATAGRAM_OVERHEAD_BYTES;
    } catch (_) {
      return 0;
    }
  }

  #requireBudget() {
    const maximum = this.#reportedMaximum();
    const budget = maximum - SINGLE_DATAGRAM_OVERHEAD_BYTES;
    if (budget < INNER_DATAGRAM_FLOOR) {
      throw new PathBudgetError();
    }
    return { maximum, budget };
  }

  async #readWithTimeout(milliseconds) {
    const deadline = timeout(milliseconds);
    try {
      return await Promise.race([this.reader.read(), deadline.promise]);
    } finally {
      deadline.cancel();
    }
  }

  #requireOpening() {
    if (this.state !== "opening") {
      throw new RelayClosedError();
    }
  }

  async open() {
    if (this.state !== "new") {
      throw new RelayConfigurationError("a backend instance opens exactly one session");
    }
    if (!this.WebTransportClass) {
      throw new RelayConfigurationError("this browser has no WebTransport support");
    }
    if (activeSourcePorts.has(this.config.clientSourcePort)) {
      throw new RelayConfigurationError("the requested source port is already live");
    }
    activeSourcePorts.add(this.config.clientSourcePort);
    this.sourcePortRegistered = true;
    this.stats.opens += 1;
    this.#setState("opening");

    let authorization = null;
    try {
      try {
        authorization = await this.config.tokenProvider();
      } catch (_) {
        this.#requireOpening();
        throw new RelayAuthorizationError();
      }
      this.#requireOpening();
      if (typeof authorization !== "string" || authorization.length === 0) {
        throw new RelayAuthorizationError();
      }
      try {
        this.transport = new this.WebTransportClass(
          this.config.endpointUrl,
          certificateOptions(this.config.certificateHashes),
        );
        await this.transport.ready;
      } catch (_) {
        throw new RelayClosedError();
      }
      this.#requireOpening();

      const { maximum } = this.#requireBudget();
      this.writer = this.transport.datagrams.writable.getWriter();
      this.reader = this.transport.datagrams.readable.getReader();

      const request = encodeAddressRequest(authorization);
      authorization = null;
      if (request.byteLength > maximum) {
        throw new RelayAuthorizationError();
      }
      try {
        await this.writer.write(request);
      } catch (_) {
        throw new RelayClosedError();
      }
      this.#requireOpening();

      const assignmentDeadline =
        Date.now() + this.config.assignmentTimeoutMilliseconds;
      let assignedAddress;
      while (assignedAddress === undefined) {
        const remaining = assignmentDeadline - Date.now();
        if (remaining <= 0) {
          throw new RelayAuthorizationError();
        }
        const result = await this.#readWithTimeout(remaining);
        this.#requireOpening();
        if (result?.timeout || result?.done || !(result?.value instanceof Uint8Array)) {
          throw new RelayAuthorizationError();
        }
        const type = datagramType(result.value);
        if (type === TYPE_KEEP_ALIVE) {
          this.stats.keepAlivesReceived += 1;
          continue;
        }
        if (type === TYPE_ERROR) {
          const { code } = decodeError(result.value);
          throw new RelayAuthorizationError(code);
        }
        assignedAddress = decodeAddressAssignment(result.value);
      }
      this.#requireOpening();
      this.routing = routingContext(
        assignedAddress,
        this.config.clientSourcePort,
        this.config.destinationAddress,
        this.config.destinationPort,
      );
      this.stats.assignments += 1;
      this.#setState("open");
      this.#startBackgroundWork();
      return this;
    } catch (error) {
      authorization = null;
      await this.#terminate(error instanceof PathBudgetError ? "path_budget" : "open_failed");
      throw error;
    }
  }

  #startBackgroundWork() {
    this.readTask = this.#readLoop().catch(() => this.#terminate("read_failure"));
    this.transport.closed.then(
      () => this.#terminate("relay_closed"),
      () => this.#terminate("relay_closed"),
    );
    this.budgetTimer = setInterval(() => {
      try {
        this.#requireBudget();
      } catch (_) {
        void this.#terminate("path_budget");
      }
    }, BUDGET_MONITOR_MILLISECONDS);
    if (this.config.keepAliveIntervalMilliseconds > 0) {
      this.keepAliveTimer = setInterval(
        () => this.#sendKeepAlive(),
        this.config.keepAliveIntervalMilliseconds,
      );
    }
  }

  #sendKeepAlive() {
    if (this.state !== "open") {
      return;
    }
    try {
      const { maximum } = this.#requireBudget();
      const datagram = encodeKeepAlive();
      if (datagram.byteLength > maximum) {
        throw new PathBudgetError();
      }
      if (!this.#enqueueWrite(datagram, "keepalive", null)) {
        void this.#terminate("write_backpressure");
        return;
      }
      this.stats.keepAlivesSent += 1;
    } catch (_) {
      void this.#terminate("path_budget");
    }
  }

  #enqueueWrite(frame, kind, packetClass) {
    const pending = this.writeQueue.length + (this.activeWrite === null ? 0 : 1);
    if (pending >= WRITE_QUEUE_DEPTH) {
      this.stats.writeQueueOverflows += 1;
      this.#event("relay-write-queue-overflow", this.stats.writeQueueOverflows);
      return false;
    }
    this.writeQueue.push({ frame, kind, packetClass, cancelled: false });
    this.stats.pendingWrites = pending + 1;
    this.stats.writeQueueHighWatermark = Math.max(
      this.stats.writeQueueHighWatermark,
      this.stats.pendingWrites,
    );
    this.#startWritePump();
    return true;
  }

  #cancelAcceptedWrite(entry, reason) {
    if (entry.kind !== "inner" || entry.cancelled) {
      return;
    }
    entry.cancelled = true;
    this.stats.cancelledAcceptedWrites += 1;
    this.#countRefusal(entry.packetClass, reason);
    this.#event("relay-write-cancelled", {
      reason,
      count: this.stats.cancelledAcceptedWrites,
    });
  }

  #cancelPendingWrites(reason) {
    if (this.activeWrite !== null) {
      this.#cancelAcceptedWrite(this.activeWrite, reason);
    }
    for (const entry of this.writeQueue) {
      this.#cancelAcceptedWrite(entry, reason);
    }
    this.writeQueue = [];
    this.stats.pendingWrites = 0;
  }

  #startWritePump() {
    if (this.writeTask !== null) {
      return;
    }
    this.writeTask = this.#drainWrites().finally(() => {
      this.writeTask = null;
      if (this.state === "open" && this.writeQueue.length > 0) {
        this.#startWritePump();
      }
    });
  }

  async #drainWrites() {
    while (this.state === "open" && this.writeQueue.length > 0) {
      const entry = this.writeQueue.shift();
      this.activeWrite = entry;
      this.stats.pendingWrites = this.writeQueue.length + 1;

      let limits;
      try {
        limits = this.#requireBudget();
      } catch (_) {
        this.#cancelAcceptedWrite(entry, "path_budget");
        this.activeWrite = null;
        this.stats.pendingWrites = this.writeQueue.length;
        await this.#terminate("path_budget");
        return;
      }
      if (entry.frame.byteLength > limits.maximum) {
        this.#cancelAcceptedWrite(entry, "oversize");
        this.activeWrite = null;
        this.stats.pendingWrites = this.writeQueue.length;
        continue;
      }

      try {
        await this.writer.write(entry.frame);
        if (entry.kind === "inner" && !entry.cancelled) {
          this.stats.writtenInnerDatagrams += 1;
        }
      } catch (_) {
        if (!entry.cancelled) {
          this.stats.writeFailures += 1;
          if (entry.kind === "inner") {
            this.#countRefusal(entry.packetClass, "write_failure");
          }
          this.activeWrite = null;
          this.stats.pendingWrites = this.writeQueue.length;
          await this.#terminate("write_failure");
          return;
        }
      }
      this.activeWrite = null;
      this.stats.pendingWrites = this.writeQueue.length;
    }
  }

  #countRefusal(packetClass, reason) {
    const origin = packetClass === 1 ? "elicited" : "originated";
    const safeReason = REFUSAL_REASONS.includes(reason) ? reason : "unavailable";
    this.stats.refusals[origin][safeReason] += 1;
    if (packetClass === 1) {
      this.stats.elicitedRefusals += 1;
    } else {
      this.stats.originatedRefusals += 1;
    }
  }

  #matchesDestination(address) {
    return (
      address?.type === IOQ3_NA_IP6 &&
      address.scopeId === 0 &&
      address.port === this.config.destinationPort &&
      Array.isArray(address.ipv6) &&
      bytesEqual(Uint8Array.from(address.ipv6), this.config.destinationAddress)
    );
  }

  sendFromEngine(address, payload, packetClass) {
    if (packetClass !== 0 && packetClass !== 1) {
      return SEND_UNAVAILABLE;
    }
    if (this.state !== "open") {
      this.#countRefusal(packetClass, "closed");
      return SEND_CLOSED;
    }
    if (!(payload instanceof Uint8Array)) {
      this.#countRefusal(packetClass, "unavailable");
      return SEND_UNAVAILABLE;
    }
    if (!this.#matchesDestination(address)) {
      this.#countRefusal(packetClass, "destination");
      return SEND_DESTINATION;
    }

    let limits;
    try {
      limits = this.#requireBudget();
    } catch (_) {
      this.#countRefusal(packetClass, "path_budget");
      void this.#terminate("path_budget");
      return SEND_BUDGET;
    }
    if (payload.byteLength > limits.budget) {
      this.#countRefusal(packetClass, "oversize");
      return SEND_OVERSIZE;
    }

    let frame;
    try {
      frame = encodeFrame(
        this.routing.outboundHeader(),
        [payload],
        BROWSER_TO_SERVER,
        limits.budget,
      );
    } catch (_) {
      this.#countRefusal(packetClass, "unavailable");
      return SEND_UNAVAILABLE;
    }
    if (frame.byteLength > limits.maximum) {
      this.#countRefusal(packetClass, "oversize");
      return SEND_OVERSIZE;
    }
    if (!this.#enqueueWrite(frame, "inner", packetClass)) {
      this.#countRefusal(packetClass, "backpressure");
      return SEND_BACKPRESSURE;
    }
    this.stats.sentInnerDatagrams += 1;
    return SEND_ACCEPTED;
  }

  async #readLoop() {
    while (this.state === "open") {
      const { value, done } = await this.reader.read();
      if (this.state !== "open") {
        return;
      }
      if (done) {
        await this.#terminate("relay_closed");
        return;
      }
      if (!(value instanceof Uint8Array)) {
        await this.#terminate("invalid_return_frame");
        return;
      }
      let type;
      try {
        type = datagramType(value);
      } catch (_) {
        await this.#terminate("invalid_return_frame");
        return;
      }
      if (type === TYPE_KEEP_ALIVE) {
        this.stats.keepAlivesReceived += 1;
        continue;
      }
      if (type === TYPE_ERROR) {
        try {
          decodeError(value);
        } catch (_) {
          this.stats.invalidReturnFrames += 1;
          await this.#terminate("invalid_return_frame");
          return;
        }
        await this.#terminate("relay_error");
        return;
      }
      if (type !== TYPE_RELAY_PACKET) {
        await this.#terminate("invalid_return_frame");
        return;
      }

      let budget;
      try {
        ({ budget } = this.#requireBudget());
      } catch (_) {
        await this.#terminate("path_budget");
        return;
      }

      let decoded;
      let header;
      try {
        decoded = decodeFrame(value, SERVER_TO_BROWSER, budget);
        if (decoded.datagrams.length !== 1) {
          throw new ArenaNetworkError("a relay frame must carry exactly one datagram");
        }
        header = decodeRelayHeader(decoded.prefix);
      } catch (_) {
        this.stats.invalidReturnFrames += 1;
        await this.#terminate("invalid_return_frame");
        return;
      }
      // A frame that parses but is addressed elsewhere is dropped, not fatal.
      // One virtual address is reused across sessions, and a game server goes
      // on sending to a client that has left until its own timeout expires,
      // so a live session can be handed the tail of a dead one. Terminating
      // on that gave a session that nobody is playing the power to end the
      // one that somebody is. A non-zero count right after a reconnect is the
      // expected shape rather than a fault; anything the loop could not parse
      // at all still terminates above, because that says the path is not
      // speaking the contract this session opened.
      if (!this.routing.acceptsReturn(header)) {
        // Counted, and deliberately not announced. The predecessor's tail is a
        // burst — one measured reconnect left a game server sending 582
        // packets a minute to nobody — and the host publishes a whole snapshot
        // per event, so an event each would put a sort and a cross-window
        // render on the engine's own thread for every stray packet, and would
        // fill the bounded event list with the one thing this branch calls
        // expected, pushing out the terminal events that diagnose real faults.
        // `foreignReturnFrames` is in every snapshot already.
        this.stats.foreignReturnFrames += 1;
        continue;
      }

      const payload = decoded.datagrams[0].slice();
      if (this.queue.length >= RECEIVE_QUEUE_DEPTH) {
        this.stats.queueOverflows += 1;
        this.#event("relay-queue-overflow", this.stats.queueOverflows);
        continue;
      }
      this.queue.push(payload);
      this.stats.receivedInnerDatagrams += 1;
      this.stats.queueHighWatermark = Math.max(
        this.stats.queueHighWatermark,
        this.queue.length,
      );
    }
  }

  receiveForEngine() {
    return this.queue.shift() ?? null;
  }

  refuseReceivedForEngine(reason) {
    const safeReason = Object.hasOwn(this.stats.engineReceiveRefusals, reason)
      ? reason
      : "invalid_payload";
    this.stats.engineReceiveRefusals[safeReason] += 1;
    this.#event("relay-engine-receive-refusal", {
      reason: safeReason,
      count: this.stats.engineReceiveRefusals[safeReason],
    });
  }

  async #terminate(reason) {
    if (this.state === "closed") {
      return;
    }
    this.stats.terminalReason ??= reason;
    this.queue = [];
    this.#cancelPendingWrites(reason === "path_budget" ? "path_budget" : "closed");
    if (this.keepAliveTimer !== null) {
      clearInterval(this.keepAliveTimer);
      this.keepAliveTimer = null;
    }
    if (this.budgetTimer !== null) {
      clearInterval(this.budgetTimer);
      this.budgetTimer = null;
    }
    this.#setState("closed");
    if (this.sourcePortRegistered) {
      activeSourcePorts.delete(this.config.clientSourcePort);
      this.sourcePortRegistered = false;
    }
    try {
      this.transport?.close();
    } catch (_) {
      // A closed WebTransport has no further state to release.
    }
    try {
      Promise.resolve(this.reader?.cancel()).catch(() => {});
    } catch (_) {
      // The transport close already released the receive side.
    }
    try {
      Promise.resolve(this.writer?.abort(new RelayClosedError())).catch(() => {});
    } catch (_) {
      // The transport close already released the send side.
    }
    this.#event("relay-terminal", reason);
  }

  // The engine's close is the one that has something to flush, and it is why
  // the drain exists. ioquake3's quit path sends its `disconnect` datagrams and
  // then shuts the network down inside one synchronous engine call, so at the
  // moment this runs they are queued and nothing has drained them yet.
  // #terminate cancels accepted writes, and cancelling those left the server
  // holding a client that only sv_timeout removed — 200 seconds of a ghost,
  // still being sent return traffic nobody would read.
  closeFromEngine() {
    void this.#closeOnce("engine_shutdown", true);
  }

  // The host's close does not drain. Either the engine already closed from
  // inside, in which case this awaits that drain rather than starting a
  // second one, or there is nothing of the engine's left to send and waiting
  // would only make a stop slower when the path is the thing that is stuck.
  async close() {
    await this.#closeOnce("client_close", false);
  }

  // One close operation however many callers ask, because two do: the engine
  // closes from inside and the host closes from outside. Without this the
  // second would race the first for the terminal reason and could cut its
  // drain short by terminating underneath it.
  #closeOnce(reason, drain) {
    this.closeTask ??= this.#runClose(reason, drain);
    return this.closeTask;
  }

  async #runClose(reason, drain) {
    // The terminate is in a `finally` because #closeOnce memoizes this
    // operation: a drain that threw would otherwise leave the transport open,
    // the read loop running and the source port registered, with every later
    // close returning the same rejected promise and no way to retry. Releasing
    // the session is the one part of a close that may not be conditional on
    // the rest of it succeeding.
    try {
      if (drain && this.state === "open") {
        // Nothing more may be queued while the queue is being drained. The
        // keep-alive is the only producer left; whatever queued the rest has
        // already stopped, which is why this is a close and not a pause.
        if (this.keepAliveTimer !== null) {
          clearInterval(this.keepAliveTimer);
          this.keepAliveTimer = null;
        }
        await this.#drainAcceptedWrites();
      }
    } finally {
      await this.#terminate(reason);
    }
  }

  async #drainAcceptedWrites() {
    let timer = null;
    let expired = false;
    const deadline = new Promise((resolve) => {
      timer = setTimeout(() => {
        expired = true;
        resolve();
      }, CLOSE_DRAIN_MILLISECONDS);
    });
    try {
      // #startWritePump's own continuation clears writeTask and restarts it if
      // the queue refilled, so "no task" is the drained state and awaiting the
      // task once is not enough.
      while (!expired && this.state === "open" && this.writeTask !== null) {
        await Promise.race([this.writeTask, deadline]);
      }
    } finally {
      clearTimeout(timer);
    }
  }

  snapshot() {
    return {
      ...this.stats,
      refusals: {
        originated: { ...this.stats.refusals.originated },
        elicited: { ...this.stats.refusals.elicited },
      },
      engineReceiveRefusals: { ...this.stats.engineReceiveRefusals },
    };
  }
}

// The engine keeps one stable synchronous adapter while each relay attempt
// owns a fresh backend and therefore a fresh authorization request. No token
// or session object survives an attempt.
export class ArenaNetworkSession {
  constructor(rawConfiguration, options = {}) {
    this.rawConfiguration = {
      ...rawConfiguration,
      certificateHashes: Array.isArray(rawConfiguration?.certificateHashes)
        ? [...rawConfiguration.certificateHashes]
        : rawConfiguration?.certificateHashes,
    };
    this.BackendClass = options.BackendClass ?? ArenaNetworkBackend;
    this.backendOptions = {
      WebTransportClass: options.WebTransportClass,
    };
    this.onEvent = typeof options.onEvent === "function" ? options.onEvent : () => {};
    this.current = null;
    this.lastSnapshot = null;
    this.completed = {
      sums: Object.fromEntries(SESSION_SUM_FIELDS.map((name) => [name, 0])),
      maxima: Object.fromEntries(SESSION_MAX_FIELDS.map((name) => [name, 0])),
      refusals: {
        originated: Object.fromEntries(REFUSAL_REASONS.map((reason) => [reason, 0])),
        elicited: Object.fromEntries(REFUSAL_REASONS.map((reason) => [reason, 0])),
      },
      engineReceiveRefusals: {
        invalid_payload: 0,
        engine_capacity: 0,
        poll_limit: 0,
      },
    };
    this.attempts = 0;
    this.reconnects = 0;
    this.engineClosed = false;
  }

  #forward(backend, kind, detail) {
    if (backend !== this.current) {
      return;
    }
    this.onEvent(kind, detail);
  }

  #archive(backend) {
    const snapshot = backend.snapshot();
    this.lastSnapshot = snapshot;
    for (const name of SESSION_SUM_FIELDS) {
      this.completed.sums[name] += snapshot[name] ?? 0;
    }
    for (const name of SESSION_MAX_FIELDS) {
      this.completed.maxima[name] = Math.max(
        this.completed.maxima[name],
        snapshot[name] ?? 0,
      );
    }
    for (const origin of ["originated", "elicited"]) {
      for (const reason of REFUSAL_REASONS) {
        this.completed.refusals[origin][reason] +=
          snapshot.refusals?.[origin]?.[reason] ?? 0;
      }
    }
    for (const reason of Object.keys(this.completed.engineReceiveRefusals)) {
      this.completed.engineReceiveRefusals[reason] +=
        snapshot.engineReceiveRefusals?.[reason] ?? 0;
    }
  }

  async #openAttempt(isReconnect) {
    let backend;
    backend = new this.BackendClass(this.rawConfiguration, {
      ...this.backendOptions,
      onEvent: (kind, detail) => this.#forward(backend, kind, detail),
    });
    this.current = backend;
    this.attempts += 1;
    if (isReconnect) {
      this.reconnects += 1;
    }
    try {
      await backend.open();
      return this;
    } catch (error) {
      this.#archive(backend);
      if (this.current === backend) {
        this.current = null;
      }
      throw error;
    }
  }

  async open() {
    if (this.attempts !== 0) {
      throw new RelayConfigurationError("the initial relay attempt already ran");
    }
    return this.#openAttempt(false);
  }

  async reconnect() {
    if (this.engineClosed) {
      throw new RelayClosedError();
    }
    if (this.attempts === 0) {
      throw new RelayConfigurationError("the initial relay attempt has not run");
    }
    const previous = this.current;
    if (previous) {
      await previous.close();
      this.#archive(previous);
      if (this.current === previous) {
        this.current = null;
      }
    }
    return this.#openAttempt(true);
  }

  sendFromEngine(address, payload, packetClass) {
    return this.current?.sendFromEngine(address, payload, packetClass) ?? SEND_CLOSED;
  }

  receiveForEngine() {
    return this.current?.receiveForEngine() ?? null;
  }

  refuseReceivedForEngine(reason) {
    this.current?.refuseReceivedForEngine(reason);
  }

  currentInnerBudget() {
    return this.current?.currentInnerBudget() ?? 0;
  }

  closeFromEngine() {
    this.engineClosed = true;
    this.current?.closeFromEngine();
  }

  async close() {
    this.engineClosed = true;
    await this.current?.close();
  }

  snapshot() {
    const current = this.current?.snapshot() ?? null;
    const combined = {
      ...(current ?? this.lastSnapshot ?? { state: "closed", terminalReason: null }),
      sessionAttempts: this.attempts,
      sessionReconnects: this.reconnects,
      sessionCompletedAttempts:
        this.attempts - (current !== null && current.state !== "closed" ? 1 : 0),
      sessionLastTerminalReason: this.lastSnapshot?.terminalReason ?? null,
    };
    for (const name of SESSION_SUM_FIELDS) {
      combined[name] = this.completed.sums[name] + (current?.[name] ?? 0);
    }
    for (const name of SESSION_MAX_FIELDS) {
      combined[name] = Math.max(
        this.completed.maxima[name],
        current?.[name] ?? 0,
      );
    }
    combined.refusals = { originated: {}, elicited: {} };
    for (const origin of ["originated", "elicited"]) {
      for (const reason of REFUSAL_REASONS) {
        combined.refusals[origin][reason] =
          this.completed.refusals[origin][reason]
          + (current?.refusals?.[origin]?.[reason] ?? 0);
      }
    }
    combined.engineReceiveRefusals = {};
    for (const reason of Object.keys(this.completed.engineReceiveRefusals)) {
      combined.engineReceiveRefusals[reason] =
        this.completed.engineReceiveRefusals[reason]
        + (current?.engineReceiveRefusals?.[reason] ?? 0);
    }
    return combined;
  }
}
