// SPDX-License-Identifier: GPL-2.0-or-later
//
// The measurement plan, runtime configuration and session driver.
//
// This mirrors scripts/relay_probe.py. The driver never reads a clock: `pump`
// and `receive` take the current time in milliseconds from the caller, and the
// transport is any object with `send(frame)` and a `maxDatagramSizeBytes`
// property. That is what lets the same driver run over WebTransport and over
// the in-memory loopback used by the startup self-test.
//
// It is game-neutral: it moves opaque payloads of chosen sizes and never
// inspects them beyond the 16-byte tag.

import {
  BROWSER_TO_SERVER,
  LENGTH_PREFIX_BYTES,
  MAX_LENGTH_PREFIX_VALUE,
  MINIMUM_TAGGED_INNER_BYTES,
  NONCE_BYTES,
  RELAY_HEADER_BYTES,
  SERVER_TO_BROWSER,
  SESSION_NONCE_BYTES,
  SINGLE_DATAGRAM_OVERHEAD_BYTES,
  RelayFrameError,
  RelayProbeError,
  buildPayload,
  bytesEqual,
  decodeFrame,
  encodeFrame,
  frameBytesForSizes,
  hexToBytes,
  readTag,
} from "./relay-framing.js";

export const REPORT_KIND = "arena-web-routed-datagram-measurement";
export const REPORT_FORMAT_VERSION = 1;

export const CASE_SINGLE = "single";
export const CASE_PACKED = "packed";

export const OUTCOME_ECHOED = "echoed";
export const OUTCOME_PAYLOAD_MISMATCH = "payloadMismatch";
export const OUTCOME_TIMED_OUT = "timedOut";
export const OUTCOME_SEND_FAILED = "sendFailed";
export const OUTCOME_NOT_SENT = "notSentFrameExceedsTransportLimit";

export const AUTHORIZATION_PLACEHOLDER = "{authorization}";

export class MeasurementPlanError extends Error {}
export class ProbeConfigError extends Error {}
export class AdapterSendError extends Error {}

function isInteger(value) {
  return Number.isInteger(value);
}

export function buildPlan(vector) {
  if (vector === null || typeof vector !== "object" || Array.isArray(vector)) {
    throw new MeasurementPlanError("measurement vector is not an object");
  }
  const framing = vector.framing;
  if (!framing || typeof framing !== "object") {
    throw new MeasurementPlanError("measurement vector has no framing record");
  }
  const expectedFraming = {
    datagramLengthPrefixBytes: LENGTH_PREFIX_BYTES,
    relayHeaderBytes: RELAY_HEADER_BYTES,
    singleDatagramOverheadBytes: SINGLE_DATAGRAM_OVERHEAD_BYTES,
  };
  for (const [name, expected] of Object.entries(expectedFraming)) {
    if (framing[name] !== expected) {
      throw new MeasurementPlanError(
        `measurement vector framing.${name} is not ${expected}`,
      );
    }
  }
  const identification = vector.payloadIdentification;
  if (!identification || typeof identification !== "object") {
    throw new MeasurementPlanError("measurement vector has no payload record");
  }
  const expectedIdentification = {
    minimumTaggedInnerBytes: MINIMUM_TAGGED_INNER_BYTES,
    nonceBytes: NONCE_BYTES,
    placement: "payload-prefix",
    smallerCasesRunSequentially: true,
  };
  for (const [name, expected] of Object.entries(expectedIdentification)) {
    if (identification[name] !== expected) {
      throw new MeasurementPlanError(
        `measurement vector payloadIdentification.${name} is not ${expected}`,
      );
    }
  }

  const directions = vector.directions;
  if (!directions || typeof directions !== "object") {
    throw new MeasurementPlanError("measurement vector has no direction lists");
  }
  const lists = {};
  for (const name of [BROWSER_TO_SERVER, SERVER_TO_BROWSER]) {
    const sizes = directions[name];
    if (!Array.isArray(sizes) || sizes.length === 0) {
      throw new MeasurementPlanError(`directions.${name} is not a size list`);
    }
    for (const size of sizes) {
      if (!isInteger(size) || size < 0 || size > MAX_LENGTH_PREFIX_VALUE) {
        throw new MeasurementPlanError(`directions.${name} has a bad size`);
      }
    }
    lists[name] = sizes;
  }

  const boundaries = vector.requiredBoundaryBytes;
  if (!Array.isArray(boundaries) || boundaries.length === 0) {
    throw new MeasurementPlanError("measurement vector has no required boundaries");
  }
  for (const boundary of boundaries) {
    for (const name of [BROWSER_TO_SERVER, SERVER_TO_BROWSER]) {
      const present = new Set(lists[name]);
      for (const needed of [boundary - 1, boundary, boundary + 1]) {
        if (!present.has(needed)) {
          throw new MeasurementPlanError(
            `directions.${name} is missing ${needed}, which the ${boundary} byte boundary requires`,
          );
        }
      }
    }
  }

  const singleSizes = Array.from(
    new Set([...lists[BROWSER_TO_SERVER], ...lists[SERVER_TO_BROWSER]]),
  ).sort((left, right) => left - right);
  const ceiling = singleSizes[singleSizes.length - 1];
  const cases = [];
  let ordinal = 0;
  for (const size of singleSizes) {
    cases.push(makeCase(cases.length, CASE_SINGLE, [{ ordinal, size }]));
    ordinal += 1;
  }

  const packed = vector.packedCases;
  if (!Array.isArray(packed) || packed.length === 0) {
    throw new MeasurementPlanError("measurement vector has no packed cases");
  }
  for (const entry of packed) {
    if (!entry || typeof entry !== "object") {
      throw new MeasurementPlanError("packed case is not an object");
    }
    if (entry.direction !== BROWSER_TO_SERVER) {
      throw new MeasurementPlanError(
        "packed cases exist only in the browser-to-server direction",
      );
    }
    if (!Array.isArray(entry.sizes) || entry.sizes.length < 2) {
      throw new MeasurementPlanError("a packed case needs at least two sizes");
    }
    const datagrams = [];
    for (const size of entry.sizes) {
      if (!isInteger(size)) {
        throw new MeasurementPlanError("packed case holds a non-integer size");
      }
      if (size < MINIMUM_TAGGED_INNER_BYTES) {
        throw new MeasurementPlanError(
          `packed case size ${size} cannot carry the ${MINIMUM_TAGGED_INNER_BYTES} byte tag`,
        );
      }
      if (size > ceiling) {
        throw new MeasurementPlanError(
          `packed case size ${size} exceeds the ${ceiling} byte ceiling`,
        );
      }
      datagrams.push({ ordinal, size });
      ordinal += 1;
    }
    cases.push(makeCase(cases.length, CASE_PACKED, datagrams));
  }
  return { cases, maxInnerDatagramBytes: ceiling };
}

function makeCase(index, kind, datagrams) {
  const sizes = datagrams.map((entry) => entry.size);
  return {
    index,
    kind,
    datagrams,
    sizes,
    ordinals: datagrams.map((entry) => entry.ordinal),
    tagged: sizes.every((size) => size >= MINIMUM_TAGGED_INNER_BYTES),
    frameBytes: frameBytesForSizes(sizes),
  };
}

const CONFIG_FIELDS = [
  "authorization",
  "caseTimeoutMilliseconds",
  "certificateHashes",
  "destinationPortMatchesProjection",
  "endpointTemplate",
  "expectedReturnPrefixHex",
  "keepAliveMilliseconds",
  "maxInFlightDatagrams",
  "pathNotes",
  "routingPrefixHex",
];

const REQUIRED_CONFIG_FIELDS = [
  "authorization",
  "destinationPortMatchesProjection",
  "endpointTemplate",
  "routingPrefixHex",
];

const CONFIG_DEFAULTS = {
  caseTimeoutMilliseconds: 2000,
  certificateHashes: [],
  expectedReturnPrefixHex: "",
  keepAliveMilliseconds: 0,
  maxInFlightDatagrams: 8,
  pathNotes: "",
};

function boundedInteger(mapping, name, minimum) {
  const value = name in mapping ? mapping[name] : CONFIG_DEFAULTS[name];
  if (!isInteger(value)) {
    throw new ProbeConfigError(`${name} is not an integer`);
  }
  if (value < minimum) {
    throw new ProbeConfigError(`${name} must be at least ${minimum}`);
  }
  return value;
}

function fixedHex(value, name, expectedBytes) {
  if (typeof value !== "string") {
    throw new ProbeConfigError(`${name} is not a string`);
  }
  if (value.length !== expectedBytes * 2) {
    throw new ProbeConfigError(
      `${name} is ${value.length} characters, not ${expectedBytes * 2}`,
    );
  }
  try {
    return hexToBytes(value);
  } catch (error) {
    throw new ProbeConfigError(`${name} is not hexadecimal`);
  }
}

// None of these values is committed anywhere. The authorization is substituted
// into the endpoint only at connect time and is never logged or reported.
export function parseConfig(mapping) {
  if (!mapping || typeof mapping !== "object" || Array.isArray(mapping)) {
    throw new ProbeConfigError("runtime configuration is not an object");
  }
  for (const name of Object.keys(mapping)) {
    if (!CONFIG_FIELDS.includes(name)) {
      throw new ProbeConfigError(`unknown configuration field ${name}`);
    }
  }
  for (const name of REQUIRED_CONFIG_FIELDS) {
    if (!(name in mapping)) {
      throw new ProbeConfigError(`${name} is required`);
    }
  }

  const template = mapping.endpointTemplate;
  if (typeof template !== "string" || template.trim() === "") {
    throw new ProbeConfigError("endpointTemplate is empty");
  }
  if (!template.startsWith("https://")) {
    throw new ProbeConfigError("endpointTemplate must be an https URL");
  }
  if (template.split(AUTHORIZATION_PLACEHOLDER).length - 1 !== 1) {
    throw new ProbeConfigError(
      `endpointTemplate must contain exactly one ${AUTHORIZATION_PLACEHOLDER} placeholder`,
    );
  }

  const authorization = mapping.authorization;
  if (typeof authorization !== "string" || authorization.trim() === "") {
    throw new ProbeConfigError("authorization is empty");
  }

  if (mapping.destinationPortMatchesProjection !== true) {
    throw new ProbeConfigError(
      "destinationPortMatchesProjection must be acknowledged as true",
    );
  }

  const hashes =
    "certificateHashes" in mapping
      ? mapping.certificateHashes
      : CONFIG_DEFAULTS.certificateHashes;
  if (!Array.isArray(hashes)) {
    throw new ProbeConfigError("certificateHashes is not a list");
  }
  for (const entry of hashes) {
    fixedHex(entry, "certificateHashes entry", 32);
  }

  const expectedHex =
    "expectedReturnPrefixHex" in mapping
      ? mapping.expectedReturnPrefixHex
      : CONFIG_DEFAULTS.expectedReturnPrefixHex;
  if (typeof expectedHex !== "string") {
    throw new ProbeConfigError("expectedReturnPrefixHex is not a string");
  }
  const expectedReturnPrefix = expectedHex
    ? fixedHex(expectedHex, "expectedReturnPrefixHex", RELAY_HEADER_BYTES)
    : new Uint8Array(0);

  const pathNotes =
    "pathNotes" in mapping ? mapping.pathNotes : CONFIG_DEFAULTS.pathNotes;
  if (typeof pathNotes !== "string") {
    throw new ProbeConfigError("pathNotes is not a string");
  }

  return {
    authorization,
    endpointTemplate: template,
    routingPrefix: fixedHex(
      mapping.routingPrefixHex,
      "routingPrefixHex",
      RELAY_HEADER_BYTES,
    ),
    expectedReturnPrefix,
    certificateHashes: hashes.slice(),
    caseTimeoutMilliseconds: boundedInteger(mapping, "caseTimeoutMilliseconds", 1),
    keepAliveMilliseconds: boundedInteger(mapping, "keepAliveMilliseconds", 0),
    maxInFlightDatagrams: boundedInteger(mapping, "maxInFlightDatagrams", 1),
    pathNotes,
    endpointUrl() {
      return template.replace(AUTHORIZATION_PLACEHOLDER, authorization);
    },
  };
}

export class SessionDriver {
  constructor(plan, adapter, sessionNonce, config, sessionIndex = 0) {
    if (sessionNonce.length !== SESSION_NONCE_BYTES) {
      throw new RelayProbeError(
        `session nonce is ${sessionNonce.length} bytes, not ${SESSION_NONCE_BYTES}`,
      );
    }
    this.plan = plan;
    this.adapter = adapter;
    this.sessionNonce = sessionNonce;
    this.config = config;
    this.sessionIndex = sessionIndex;
    this.pending = plan.cases.slice();
    this.states = new Map();
    for (const item of plan.cases) {
      this.states.set(item.index, {
        outcome: "",
        sentAt: -1,
        roundTrip: -1,
        received: [],
        outstanding: new Set(),
      });
    }
    this.inflight = new Map();
    this.returnPrefix =
      config.expectedReturnPrefix.length > 0 ? config.expectedReturnPrefix : null;
    this.outstandingKeepAlives = 0;
    this.lastSendMs = -1;
    this.foreignFrames = 0;
    this.malformedFrames = 0;
    this.prefixMismatchFrames = 0;
    this.unmatchedFrames = 0;
    this.keepAliveFramesSent = 0;
  }

  get finished() {
    for (const state of this.states.values()) {
      if (!state.outcome) {
        return false;
      }
    }
    return true;
  }

  payloadFor(datagram) {
    return buildPayload(this.sessionNonce, datagram.ordinal, datagram.size);
  }

  pump(now) {
    this.expire(now);
    this.startReady(now);
    this.sendKeepAliveIfDue(now);
  }

  expire(now) {
    for (const item of this.plan.cases) {
      const state = this.states.get(item.index);
      if (state.outcome || state.sentAt < 0) {
        continue;
      }
      if (now - state.sentAt >= this.config.caseTimeoutMilliseconds) {
        for (const ordinal of state.outstanding) {
          this.inflight.delete(ordinal);
        }
        state.outstanding.clear();
        state.outcome = OUTCOME_TIMED_OUT;
      }
    }
  }

  untaggedOutstanding() {
    for (const entry of this.inflight.values()) {
      if (entry.payload.length < MINIMUM_TAGGED_INNER_BYTES) {
        return true;
      }
    }
    return false;
  }

  startReady(now) {
    while (this.pending.length > 0) {
      const item = this.pending[0];
      // An untagged payload cannot be attributed to a return frame, so it runs
      // alone: nothing outstanding when it starts, nothing started while it is.
      if (this.untaggedOutstanding()) {
        return;
      }
      if (!item.tagged) {
        if (this.inflight.size > 0 || this.outstandingKeepAlives > 0) {
          return;
        }
      } else if (
        this.inflight.size > 0 &&
        this.inflight.size + item.datagrams.length >
          this.config.maxInFlightDatagrams
      ) {
        return;
      }
      this.pending.shift();
      this.sendCase(item, now);
    }
  }

  sendCase(item, now) {
    const state = this.states.get(item.index);
    const payloads = item.datagrams.map((datagram) => this.payloadFor(datagram));
    const frame = encodeFrame(
      this.config.routingPrefix,
      payloads,
      BROWSER_TO_SERVER,
      this.plan.maxInnerDatagramBytes,
    );
    if (frame.length > this.adapter.maxDatagramSizeBytes) {
      state.outcome = OUTCOME_NOT_SENT;
      return;
    }
    try {
      this.adapter.send(frame);
    } catch (error) {
      if (!(error instanceof AdapterSendError)) {
        throw error;
      }
      state.outcome = OUTCOME_SEND_FAILED;
      return;
    }
    state.sentAt = now;
    this.lastSendMs = now;
    item.datagrams.forEach((datagram, position) => {
      state.outstanding.add(datagram.ordinal);
      this.inflight.set(datagram.ordinal, {
        caseIndex: item.index,
        payload: payloads[position],
      });
    });
  }

  // Never while a case is outstanding: a keep-alive echo is a 0-byte inner
  // datagram and would otherwise be indistinguishable from a 0-byte case.
  sendKeepAliveIfDue(now) {
    if (this.config.keepAliveMilliseconds <= 0 || this.inflight.size > 0) {
      return;
    }
    if (
      this.lastSendMs >= 0 &&
      now - this.lastSendMs < this.config.keepAliveMilliseconds
    ) {
      return;
    }
    const frame = encodeFrame(
      this.config.routingPrefix,
      [new Uint8Array(0)],
      BROWSER_TO_SERVER,
      this.plan.maxInnerDatagramBytes,
    );
    try {
      this.adapter.send(frame);
    } catch (error) {
      if (!(error instanceof AdapterSendError)) {
        throw error;
      }
      return;
    }
    this.lastSendMs = now;
    this.outstandingKeepAlives += 1;
    this.keepAliveFramesSent += 1;
  }

  receive(frame, now) {
    let decoded;
    try {
      decoded = decodeFrame(
        frame,
        SERVER_TO_BROWSER,
        this.plan.maxInnerDatagramBytes,
      );
    } catch (error) {
      if (!(error instanceof RelayFrameError)) {
        throw error;
      }
      this.malformedFrames += 1;
      return;
    }
    if (this.returnPrefix === null) {
      this.returnPrefix = decoded.prefix.slice();
    } else if (!bytesEqual(decoded.prefix, this.returnPrefix)) {
      this.prefixMismatchFrames += 1;
      return;
    }
    const payload = decoded.datagrams[0];
    const tag = readTag(payload);
    if (tag === null) {
      this.receiveUntagged(payload, now);
      return;
    }
    if (!bytesEqual(tag.nonce, this.sessionNonce)) {
      this.foreignFrames += 1;
      return;
    }
    const entry = this.inflight.get(tag.ordinal);
    if (entry === undefined) {
      this.unmatchedFrames += 1;
      return;
    }
    this.complete(entry.caseIndex, tag.ordinal, payload, entry.payload, now);
  }

  receiveUntagged(payload, now) {
    if (payload.length === 0 && this.outstandingKeepAlives > 0) {
      this.outstandingKeepAlives -= 1;
      return;
    }
    if (this.inflight.size !== 1) {
      this.unmatchedFrames += 1;
      return;
    }
    const [ordinal, entry] = this.inflight.entries().next().value;
    if (entry.payload.length >= MINIMUM_TAGGED_INNER_BYTES) {
      this.unmatchedFrames += 1;
      return;
    }
    this.complete(entry.caseIndex, ordinal, payload, entry.payload, now);
  }

  complete(caseIndex, ordinal, payload, expected, now) {
    const state = this.states.get(caseIndex);
    this.inflight.delete(ordinal);
    state.outstanding.delete(ordinal);
    if (!bytesEqual(payload, expected)) {
      state.outstanding.clear();
      for (const [other, entry] of Array.from(this.inflight.entries())) {
        if (entry.caseIndex === caseIndex) {
          this.inflight.delete(other);
        }
      }
      state.outcome = OUTCOME_PAYLOAD_MISMATCH;
      return;
    }
    state.received.push(payload.length);
    if (state.outstanding.size === 0) {
      state.outcome = OUTCOME_ECHOED;
      state.roundTrip = now - state.sentAt;
    }
  }

  sessionRecord() {
    const cases = this.plan.cases.map((item) => {
      const state = this.states.get(item.index);
      return {
        caseIndex: item.index,
        kind: item.kind,
        ordinals: item.ordinals.slice(),
        outcome: state.outcome || OUTCOME_TIMED_OUT,
        receivedFrames: state.received.map((size) => ({
          frameBytes: size + SINGLE_DATAGRAM_OVERHEAD_BYTES,
          innerBytes: size,
        })),
        roundTripMilliseconds:
          state.outcome === OUTCOME_ECHOED ? state.roundTrip : null,
        sentFrameBytes: item.frameBytes,
        sentInnerBytes: item.sizes.slice(),
      };
    });
    return {
      caseTimeoutMilliseconds: this.config.caseTimeoutMilliseconds,
      cases,
      foreignFrames: this.foreignFrames,
      keepAliveFramesSent: this.keepAliveFramesSent,
      keepAliveMilliseconds: this.config.keepAliveMilliseconds,
      malformedFrames: this.malformedFrames,
      maxDatagramSizeBytes: this.adapter.maxDatagramSizeBytes,
      maxInFlightDatagrams: this.config.maxInFlightDatagrams,
      prefixMismatchFrames: this.prefixMismatchFrames,
      sessionIndex: this.sessionIndex,
      unmatchedFrames: this.unmatchedFrames,
    };
  }
}

// The report deliberately has no field for an endpoint, address, port,
// certificate or authorization, so none can be written into it by accident.
export function buildReport(sessions, measurementVectorSha256, pathNotes = "") {
  return {
    formatVersion: REPORT_FORMAT_VERSION,
    framing: {
      datagramLengthPrefixBytes: LENGTH_PREFIX_BYTES,
      relayHeaderBytes: RELAY_HEADER_BYTES,
      singleDatagramOverheadBytes: SINGLE_DATAGRAM_OVERHEAD_BYTES,
    },
    kind: REPORT_KIND,
    measurementVectorSha256,
    pathNotes,
    sessions,
  };
}

// Per session and per path on purpose. `contiguousInnerBytes` is the largest
// single-datagram size for which every smaller planned size also echoed, so an
// intermittently accepted large size cannot raise it, and the floor carries no
// safety margin. None of this is a universal transport constant.
export function summarizeReport(report) {
  const summaries = [];
  const floors = [];
  for (const session of report.sessions) {
    const echoed = new Set();
    const failed = new Set();
    let largestFrame = null;
    for (const item of session.cases) {
      if (item.kind !== CASE_SINGLE) {
        continue;
      }
      const size = item.sentInnerBytes[0];
      if (item.outcome === OUTCOME_ECHOED) {
        echoed.add(size);
        largestFrame =
          largestFrame === null
            ? item.sentFrameBytes
            : Math.max(largestFrame, item.sentFrameBytes);
      } else {
        failed.add(size);
      }
    }
    let contiguous = null;
    const ordered = Array.from(new Set([...echoed, ...failed])).sort(
      (left, right) => left - right,
    );
    for (const size of ordered) {
      if (failed.has(size)) {
        break;
      }
      contiguous = size;
    }
    const echoedValues = Array.from(echoed);
    const failedValues = Array.from(failed);
    summaries.push({
      contiguousInnerBytes: contiguous,
      echoedSingleCases: echoed.size,
      failedSingleCases: failed.size,
      largestEchoedFrameBytes: largestFrame,
      largestEchoedInnerBytes: echoed.size ? Math.max(...echoedValues) : null,
      maxDatagramSizeBytes: session.maxDatagramSizeBytes,
      monotonic: !(
        echoed.size &&
        failed.size &&
        Math.max(...echoedValues) > Math.min(...failedValues)
      ),
      sessionIndex: session.sessionIndex,
      smallestFailedInnerBytes: failed.size ? Math.min(...failedValues) : null,
    });
    floors.push(contiguous);
  }
  return {
    conservativeInnerFloorBytes:
      floors.length && floors.every((value) => value !== null)
        ? Math.min(...floors)
        : null,
    sessions: summaries,
  };
}
