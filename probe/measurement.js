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
// A case the run never reached. Not evidence about the path in either
// direction, so the summary excludes it from the accepted range.
export const OUTCOME_NOT_RUN = "notRun";
export const OUTCOMES = [
  OUTCOME_ECHOED,
  OUTCOME_NOT_RUN,
  OUTCOME_NOT_SENT,
  OUTCOME_PAYLOAD_MISMATCH,
  OUTCOME_SEND_FAILED,
  OUTCOME_TIMED_OUT,
];

// A packed case is atomic, so its width is outstanding at once. The plan is
// built against the same bound the driver enforces.
export const DEFAULT_MAX_IN_FLIGHT_DATAGRAMS = 8;

export const AUTHORIZATION_PLACEHOLDER = "{authorization}";

export class MeasurementPlanError extends Error {}
export class ProbeConfigError extends Error {}
export class AdapterSendError extends Error {}

function isInteger(value) {
  return Number.isInteger(value);
}

export function buildPlan(
  vector,
  maxInFlightDatagrams = DEFAULT_MAX_IN_FLIGHT_DATAGRAMS,
) {
  if (!isInteger(maxInFlightDatagrams) || maxInFlightDatagrams < 1) {
    throw new MeasurementPlanError("maxInFlightDatagrams must be at least 1");
  }
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
    if (entry.sizes.length > maxInFlightDatagrams) {
      throw new MeasurementPlanError(
        `a packed case of ${entry.sizes.length} datagrams cannot respect the ` +
          `${maxInFlightDatagrams} outstanding-datagram bound`,
      );
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
  maxInFlightDatagrams: DEFAULT_MAX_IN_FLIGHT_DATAGRAMS,
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
    maxInFlightDatagrams: boundedInteger(mapping, "maxInFlightDatagrams", 1),
    pathNotes,
    // String.replace() interprets $-patterns in the replacement, so an
    // authorization containing $&, $`, $', $$ or $1 would be rewritten and the
    // wrong bytes would go on the wire. Split and join instead.
    endpointUrl() {
      return template.split(AUTHORIZATION_PLACEHOLDER).join(authorization);
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
    for (const item of plan.cases) {
      if (item.datagrams.length > config.maxInFlightDatagrams) {
        throw new RelayProbeError(
          `case ${item.index} carries ${item.datagrams.length} datagrams, more ` +
            `than the configured bound of ${config.maxInFlightDatagrams}`,
        );
      }
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
    this.foreignFrames = 0;
    this.malformedFrames = 0;
    this.prefixMismatchFrames = 0;
    this.unmatchedFrames = 0;
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
        if (this.inflight.size > 0) {
          return;
        }
      } else if (
        this.inflight.size + item.datagrams.length >
        this.config.maxInFlightDatagrams
      ) {
        // Unconditional: the plan guarantees no case is wider than the bound,
        // so this can never refuse a case forever.
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
    item.datagrams.forEach((datagram, position) => {
      state.outstanding.add(datagram.ordinal);
      this.inflight.set(datagram.ordinal, {
        caseIndex: item.index,
        payload: payloads[position],
      });
    });
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

  // Only sequencing identifies an untagged echo, and exactly one untagged
  // datagram is ever outstanding. It is still only a candidate: a late echo
  // from a case that already timed out arrives while the next one is
  // outstanding, and its length is what separates the two. A length that does
  // not match is unattributable rather than a mismatch, because attributing it
  // would report a defect against the wrong case.
  receiveUntagged(payload, now) {
    if (this.inflight.size !== 1) {
      this.unmatchedFrames += 1;
      return;
    }
    const [ordinal, entry] = this.inflight.entries().next().value;
    if (
      entry.payload.length >= MINIMUM_TAGGED_INNER_BYTES ||
      payload.length !== entry.payload.length
    ) {
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
        // A case with no outcome either waited for an answer that never came,
        // or was never reached at all. Those are different facts.
        outcome:
          state.outcome ||
          (state.sentAt >= 0 ? OUTCOME_TIMED_OUT : OUTCOME_NOT_RUN),
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
      malformedFrames: this.malformedFrames,
      maxDatagramSizeBytes: this.adapter.maxDatagramSizeBytes,
      maxInFlightDatagrams: this.config.maxInFlightDatagrams,
      prefixMismatchFrames: this.prefixMismatchFrames,
      sessionIndex: this.sessionIndex,
      unmatchedFrames: this.unmatchedFrames,
      // Reported by the transport, not the driver: a datagram write can fail
      // after the driver has handed the frame over.
      writeFailures: this.adapter.writeFailures || 0,
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

export class MeasurementReportError extends Error {}

const REPORT_FIELDS = [
  "formatVersion",
  "framing",
  "kind",
  "measurementVectorSha256",
  "pathNotes",
  "sessions",
];
const SESSION_FIELDS = [
  "caseTimeoutMilliseconds",
  "cases",
  "foreignFrames",
  "malformedFrames",
  "maxDatagramSizeBytes",
  "maxInFlightDatagrams",
  "prefixMismatchFrames",
  "sessionIndex",
  "unmatchedFrames",
  "writeFailures",
];
const CASE_FIELDS = [
  "caseIndex",
  "kind",
  "ordinals",
  "outcome",
  "receivedFrames",
  "roundTripMilliseconds",
  "sentFrameBytes",
  "sentInnerBytes",
];
const SESSION_COUNTERS = [
  "foreignFrames",
  "malformedFrames",
  "prefixMismatchFrames",
  "unmatchedFrames",
  "writeFailures",
];

function requireFields(record, fields, label) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw new MeasurementReportError(`${label} is not an object`);
  }
  for (const name of fields) {
    if (!(name in record)) {
      throw new MeasurementReportError(`${label} is missing ${name}`);
    }
  }
  for (const name of Object.keys(record)) {
    if (!fields.includes(name)) {
      throw new MeasurementReportError(`${label} has unknown field ${name}`);
    }
  }
}

function requireInt(record, name, label, minimum) {
  const value = record[name];
  if (!isInteger(value)) {
    throw new MeasurementReportError(`${label}.${name} is not an integer`);
  }
  if (value < minimum) {
    throw new MeasurementReportError(`${label}.${name} is below ${minimum}`);
  }
  return value;
}

// The Python validator in scripts/relay_probe.py stays authoritative and the
// tests compare against it. This exists so the page cannot present a summary or
// offer a download for a report it has not checked.
export function validateReport(report, plan = null) {
  requireFields(report, REPORT_FIELDS, "report");
  if (report.formatVersion !== REPORT_FORMAT_VERSION) {
    throw new MeasurementReportError("report formatVersion is unsupported");
  }
  if (report.kind !== REPORT_KIND) {
    throw new MeasurementReportError("report kind is unsupported");
  }
  const framing = report.framing;
  requireFields(
    framing,
    ["datagramLengthPrefixBytes", "relayHeaderBytes", "singleDatagramOverheadBytes"],
    "report.framing",
  );
  if (
    framing.datagramLengthPrefixBytes !== LENGTH_PREFIX_BYTES ||
    framing.relayHeaderBytes !== RELAY_HEADER_BYTES ||
    framing.singleDatagramOverheadBytes !== SINGLE_DATAGRAM_OVERHEAD_BYTES
  ) {
    throw new MeasurementReportError("report framing does not match the contract");
  }
  const digest = report.measurementVectorSha256;
  if (typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) {
    throw new MeasurementReportError(
      "measurementVectorSha256 is not a SHA-256 digest",
    );
  }
  if (typeof report.pathNotes !== "string") {
    throw new MeasurementReportError("pathNotes is not a string");
  }
  if (!Array.isArray(report.sessions) || report.sessions.length === 0) {
    throw new MeasurementReportError("report has no sessions");
  }
  let previous = -1;
  for (const session of report.sessions) {
    const index = validateSession(session, plan);
    if (index <= previous) {
      throw new MeasurementReportError("session indices are not ascending");
    }
    previous = index;
  }
  return report;
}

function validateSession(session, plan) {
  requireFields(session, SESSION_FIELDS, "session");
  const index = requireInt(session, "sessionIndex", "session", 0);
  const ceiling = Math.min(
    plan === null ? MAX_LENGTH_PREFIX_VALUE : plan.maxInnerDatagramBytes,
    MAX_LENGTH_PREFIX_VALUE,
  );
  const maxDatagram = requireInt(session, "maxDatagramSizeBytes", "session", 1);
  requireInt(session, "caseTimeoutMilliseconds", "session", 1);
  const bound = requireInt(session, "maxInFlightDatagrams", "session", 1);
  for (const name of SESSION_COUNTERS) {
    requireInt(session, name, "session", 0);
  }
  if (!Array.isArray(session.cases) || session.cases.length === 0) {
    throw new MeasurementReportError("session has no cases");
  }
  if (plan !== null && session.cases.length !== plan.cases.length) {
    throw new MeasurementReportError(
      `session has ${session.cases.length} cases, but the plan has ${plan.cases.length}`,
    );
  }
  let previousCase = -1;
  const seenOrdinals = new Set();
  session.cases.forEach((item, position) => {
    const planned = plan === null ? null : plan.cases[position];
    const caseIndex = validateCase(
      item,
      ceiling,
      maxDatagram,
      bound,
      planned,
      seenOrdinals,
    );
    if (caseIndex <= previousCase) {
      throw new MeasurementReportError("case indices are not ascending");
    }
    previousCase = caseIndex;
  });
  return index;
}

function validateCase(item, ceiling, maxDatagram, bound, planned, seenOrdinals) {
  requireFields(item, CASE_FIELDS, "case");
  const caseIndex = requireInt(item, "caseIndex", "case", 0);
  if (item.kind !== CASE_SINGLE && item.kind !== CASE_PACKED) {
    throw new MeasurementReportError(`case kind ${item.kind} is unknown`);
  }
  if (!OUTCOMES.includes(item.outcome)) {
    throw new MeasurementReportError(`case outcome ${item.outcome} is unknown`);
  }

  const sizes = item.sentInnerBytes;
  if (!Array.isArray(sizes) || sizes.length === 0) {
    throw new MeasurementReportError("case sent no inner datagram");
  }
  for (const size of sizes) {
    if (!isInteger(size) || size < 0 || size > ceiling) {
      throw new MeasurementReportError(`sent inner size ${size} is out of range`);
    }
  }
  if (item.kind === CASE_SINGLE && sizes.length !== 1) {
    throw new MeasurementReportError("a single case carries exactly one datagram");
  }
  if (item.kind === CASE_PACKED && sizes.length < 2) {
    throw new MeasurementReportError("a packed case carries at least two datagrams");
  }
  if (sizes.length > bound) {
    throw new MeasurementReportError(
      `a case of ${sizes.length} datagrams exceeds the session's ${bound} outstanding-datagram bound`,
    );
  }

  const ordinals = item.ordinals;
  if (!Array.isArray(ordinals) || ordinals.length !== sizes.length) {
    throw new MeasurementReportError("ordinals do not match the sent datagrams");
  }
  for (const ordinal of ordinals) {
    if (!isInteger(ordinal) || ordinal < 0) {
      throw new MeasurementReportError("ordinals hold a non-ordinal value");
    }
    if (seenOrdinals.has(ordinal)) {
      throw new MeasurementReportError(`ordinal ${ordinal} is reused in one session`);
    }
    seenOrdinals.add(ordinal);
  }

  const expectedFrame = frameBytesForSizes(sizes);
  if (item.sentFrameBytes !== expectedFrame) {
    throw new MeasurementReportError(
      `sentFrameBytes does not equal the ${expectedFrame} bytes its inner sizes require`,
    );
  }

  const received = item.receivedFrames;
  if (!Array.isArray(received)) {
    throw new MeasurementReportError("receivedFrames is not a list");
  }
  if (received.length > sizes.length) {
    throw new MeasurementReportError("more frames returned than datagrams sent");
  }
  const remaining = new Map();
  for (const size of sizes) {
    remaining.set(size, (remaining.get(size) || 0) + 1);
  }
  for (const entry of received) {
    requireFields(entry, ["frameBytes", "innerBytes"], "received frame");
    const inner = requireInt(entry, "innerBytes", "received frame", 0);
    if (inner > ceiling) {
      throw new MeasurementReportError(`received inner size ${inner} is out of range`);
    }
    if (entry.frameBytes !== inner + SINGLE_DATAGRAM_OVERHEAD_BYTES) {
      throw new MeasurementReportError(
        "a returned frame does not carry the 42-byte single-datagram overhead",
      );
    }
    if (!remaining.get(inner)) {
      throw new MeasurementReportError(
        `a returned frame reports ${inner} bytes, which this case did not send`,
      );
    }
    remaining.set(inner, remaining.get(inner) - 1);
  }

  const roundTrip = item.roundTripMilliseconds;
  if (item.outcome === OUTCOME_ECHOED) {
    if (received.length !== sizes.length) {
      throw new MeasurementReportError("an echoed case is missing a return frame");
    }
    if (typeof roundTrip !== "number" || !Number.isFinite(roundTrip) || roundTrip < 0) {
      throw new MeasurementReportError("round-trip time is out of range");
    }
    if (item.sentFrameBytes > maxDatagram) {
      throw new MeasurementReportError(
        "a case that echoed is larger than the reported datagram maximum",
      );
    }
  } else if (roundTrip !== null) {
    throw new MeasurementReportError("only an echoed case carries a round-trip time");
  }

  if (item.outcome === OUTCOME_NOT_SENT) {
    if (received.length > 0) {
      throw new MeasurementReportError("an unsent case returned frames");
    }
    if (item.sentFrameBytes <= maxDatagram) {
      throw new MeasurementReportError(
        "a case refused for size fits the reported datagram maximum",
      );
    }
  }
  if (item.outcome === OUTCOME_SEND_FAILED && received.length > 0) {
    throw new MeasurementReportError("a case whose send failed returned frames");
  }
  if (item.outcome === OUTCOME_NOT_RUN && received.length > 0) {
    throw new MeasurementReportError("a case that never ran returned frames");
  }

  if (planned !== null) {
    const sameSizes =
      planned.sizes.length === sizes.length &&
      planned.sizes.every((size, at) => size === sizes[at]);
    const sameOrdinals =
      planned.ordinals.length === ordinals.length &&
      planned.ordinals.every((ordinal, at) => ordinal === ordinals[at]);
    if (
      caseIndex !== planned.index ||
      !sameSizes ||
      !sameOrdinals ||
      item.kind !== planned.kind
    ) {
      throw new MeasurementReportError(`case ${caseIndex} does not match the plan`);
    }
  }
  return caseIndex;
}

// Per session and per path on purpose. `contiguousInnerBytes` is the largest
// single-datagram size below which every smaller planned size also echoed, so an
// intermittently accepted large size cannot raise it, and the floor carries no
// safety margin. A case that never ran is a gap, not an acceptance: it stops the
// contiguous range like a failure does, but is counted separately because it is
// an absence of evidence rather than evidence of refusal. None of this is a
// universal transport constant, and none of it is per direction: one round trip
// through an echoing destination cannot say which direction refused a size.
export function summarizeReport(report, plan = null) {
  validateReport(report, plan);
  const summaries = [];
  const floors = [];
  // An untagged payload carries no session nonce, so in a report holding more
  // than one session an untagged case could have been completed by the other
  // session's identical echo. Such a size is not isolation-grade evidence and
  // must not lift the floor, and the sizes are named in the output because the
  // floor travels as JSON.
  const concurrent = report.sessions.length > 1;
  for (const session of report.sessions) {
    const echoed = new Set();
    const failed = new Set();
    const notRun = new Set();
    const untagged = new Set();
    let largestFrame = null;
    for (const item of session.cases) {
      if (item.kind !== CASE_SINGLE) {
        continue;
      }
      const size = item.sentInnerBytes[0];
      if (size < MINIMUM_TAGGED_INNER_BYTES) {
        untagged.add(size);
      }
      if (item.outcome === OUTCOME_ECHOED) {
        echoed.add(size);
        largestFrame =
          largestFrame === null
            ? item.sentFrameBytes
            : Math.max(largestFrame, item.sentFrameBytes);
      } else if (item.outcome === OUTCOME_NOT_RUN) {
        notRun.add(size);
      } else {
        failed.add(size);
      }
    }
    let contiguous = null;
    let walked = new Set([...echoed, ...failed, ...notRun]);
    if (concurrent) {
      walked = new Set([...walked].filter((size) => !untagged.has(size)));
    }
    const ordered = Array.from(walked).sort((left, right) => left - right);
    for (const size of ordered) {
      if (!echoed.has(size)) {
        break;
      }
      contiguous = size;
    }
    const echoedValues = Array.from(echoed);
    const failedValues = Array.from(failed);
    summaries.push({
      contiguousInnerBytes: contiguous,
      contiguousExcludesUntagged: concurrent && untagged.size > 0,
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
      notRunSingleCases: notRun.size,
      sessionIndex: session.sessionIndex,
      smallestFailedInnerBytes: failed.size ? Math.min(...failedValues) : null,
      untaggedSingleSizes: Array.from(untagged).sort((left, right) => left - right),
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
