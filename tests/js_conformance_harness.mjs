// SPDX-License-Identifier: GPL-2.0-or-later
//
// Runs the browser probe's implementation of the routed datagram contract under
// Node so that tests/test_relay_probe.py can compare it with the Python one.
//
// This is a test harness, not part of the probe. It takes the probe directory
// and the repository root as arguments, touches no network and writes only to
// stdout.

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [probeDir, repoRoot] = process.argv.slice(2);
const load = (name) => import(pathToFileURL(path.join(probeDir, name)).href);

const framing = await load("relay-framing.js");
const measurement = await load("measurement.js");
const adapters = await load("adapters.js");

const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const vectors = readJson(path.join(probeDir, "conformance-vectors.json"));
const vector = readJson(
  path.join(repoRoot, "locks", "relay-measurement-vector.json"),
);

let conformanceChecked = 0;
for (const item of vectors.encodeCases) {
  const payloads = item.payloadHexes.map(framing.hexToBytes);
  const frame = framing.encodeFrame(
    framing.hexToBytes(item.prefixHex),
    payloads,
    item.direction,
  );
  if (framing.bytesToHex(frame) !== item.frameHex) {
    throw new Error(`encode case ${item.name} produced other bytes`);
  }
  if (frame.length !== item.frameBytes) {
    throw new Error(`encode case ${item.name} has the wrong length`);
  }
  const decoded = framing.decodeFrame(frame, item.direction);
  if (decoded.datagrams.length !== payloads.length) {
    throw new Error(`decode of ${item.name} lost or invented a datagram`);
  }
  decoded.datagrams.forEach((datagram, index) => {
    if (framing.bytesToHex(datagram) !== item.payloadHexes[index]) {
      throw new Error(`decode of ${item.name} changed a payload`);
    }
  });
  conformanceChecked += 1;
}
for (const item of vectors.decodeAcceptances) {
  const decoded = framing.decodeFrame(
    framing.hexToBytes(item.frameHex),
    item.direction,
    item.maxInnerDatagramBytes,
  );
  if (decoded.datagrams.length !== item.payloadHexes.length) {
    throw new Error(`acceptance ${item.name} decoded the wrong datagram count`);
  }
  decoded.datagrams.forEach((datagram, index) => {
    if (framing.bytesToHex(datagram) !== item.payloadHexes[index]) {
      throw new Error(`acceptance ${item.name} changed a payload`);
    }
  });
  conformanceChecked += 1;
}
for (const item of vectors.decodeRejections) {
  let rejected = false;
  try {
    framing.decodeFrame(
      framing.hexToBytes(item.frameHex),
      item.direction,
      item.maxInnerDatagramBytes,
    );
  } catch (error) {
    rejected = error instanceof framing.RelayFrameError;
  }
  if (!rejected) {
    throw new Error(`decode rejection ${item.name} was accepted`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.encodeRejections) {
  let rejected = false;
  try {
    framing.encodeFrame(
      framing.hexToBytes(item.prefixHex),
      item.payloadHexes.map(framing.hexToBytes),
      item.direction,
      item.maxInnerDatagramBytes,
    );
  } catch (error) {
    rejected = error instanceof framing.RelayFrameError;
  }
  if (!rejected) {
    throw new Error(`encode rejection ${item.name} was accepted`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.tagCases) {
  const tag = framing.datagramTag(
    framing.hexToBytes(item.sessionNonceHex),
    item.ordinal,
  );
  if (framing.bytesToHex(tag) !== item.tagHex) {
    throw new Error(`tag case ${item.name} produced other bytes`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.payloadCases) {
  const payload = framing.buildPayload(
    framing.hexToBytes(item.sessionNonceHex),
    item.ordinal,
    item.size,
  );
  if (framing.bytesToHex(payload) !== item.payloadHex) {
    throw new Error(`payload case ${item.name} produced other bytes`);
  }
  conformanceChecked += 1;
}

// The 2026-08-30 session and header profile, driven from the same file the
// reference implementation is driven from.
const contextOf = (item) =>
  framing.routingContext(
    framing.hexToBytes(item.clientAddressHex),
    item.clientPort,
    framing.hexToBytes(item.destinationAddressHex),
    item.destinationPort,
  );

for (const item of vectors.addressRequestCases) {
  if (
    framing.bytesToHex(framing.encodeAddressRequest(item.authorization)) !==
    item.datagramHex
  ) {
    throw new Error(`${item.name} produced other bytes`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.addressAssignmentCases) {
  const address = framing.hexToBytes(item.addressHex);
  if (
    framing.bytesToHex(framing.encodeAddressAssignment(address)) !==
    item.datagramHex
  ) {
    throw new Error(`${item.name} produced other bytes`);
  }
  const decoded = framing.decodeAddressAssignment(
    framing.hexToBytes(item.datagramHex),
  );
  if (framing.bytesToHex(decoded) !== item.addressHex) {
    throw new Error(`${item.name} decoded another address`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.errorCases) {
  if (
    framing.bytesToHex(framing.encodeError(item.code, item.message)) !==
    item.datagramHex
  ) {
    throw new Error(`${item.name} produced other bytes`);
  }
  const decoded = framing.decodeError(framing.hexToBytes(item.datagramHex));
  if (decoded.code !== item.code || decoded.message !== item.message) {
    throw new Error(`${item.name} decoded another error`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.keepAliveCases) {
  const padding = framing.hexToBytes(item.paddingHex);
  if (
    framing.bytesToHex(framing.encodeKeepAlive(padding)) !== item.datagramHex
  ) {
    throw new Error(`${item.name} produced other bytes`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.sessionRejections) {
  const decode =
    item.decoder === "error"
      ? framing.decodeError
      : framing.decodeAddressAssignment;
  let rejected = false;
  try {
    decode(framing.hexToBytes(item.datagramHex));
  } catch (error) {
    rejected = error instanceof framing.RelaySessionError;
  }
  if (!rejected) {
    throw new Error(`session rejection ${item.name} was accepted`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.headerCases) {
  if (
    framing.bytesToHex(contextOf(item).outboundHeader()) !== item.headerHex
  ) {
    throw new Error(`header case ${item.name} produced other bytes`);
  }
  const decoded = framing.decodeRelayHeader(framing.hexToBytes(item.headerHex));
  if (
    framing.bytesToHex(decoded.destinationAddress) !== item.destinationAddressHex ||
    decoded.destinationPort !== item.destinationPort ||
    framing.bytesToHex(decoded.sourceAddress) !== item.clientAddressHex ||
    decoded.sourcePort !== item.clientPort
  ) {
    throw new Error(`header case ${item.name} decoded other fields`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.headerRejections) {
  let rejected = false;
  try {
    framing.decodeRelayHeader(framing.hexToBytes(item.headerHex));
  } catch (error) {
    rejected = error instanceof framing.RelayFrameError;
  }
  if (!rejected) {
    throw new Error(`header rejection ${item.name} was accepted`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.returnHeaderAcceptances) {
  const header = framing.decodeRelayHeader(
    framing.hexToBytes(item.returnHeaderHex),
  );
  if (!contextOf(item).acceptsReturn(header)) {
    throw new Error(`return header ${item.name} was refused`);
  }
  conformanceChecked += 1;
}
for (const item of vectors.returnHeaderRejections) {
  const header = framing.decodeRelayHeader(
    framing.hexToBytes(item.returnHeaderHex),
  );
  if (contextOf(item).acceptsReturn(header)) {
    throw new Error(`return header ${item.name} was accepted`);
  }
  conformanceChecked += 1;
}

const maxInFlight = Number(process.env.HARNESS_MAX_IN_FLIGHT);
const plan = measurement.buildPlan(vector, maxInFlight);

// The synthetic session context. Nothing here is routable and nothing here
// describes any environment; the addresses come from the IPv6 documentation
// prefix and the authorization is a fixed string the in-memory relay compares
// literally.
const baseConfig = {
  authorization: adapters.SYNTHETIC_AUTHORIZATION,
  destinationAddressHex: framing.bytesToHex(adapters.SYNTHETIC_DESTINATION_ADDRESS),
  destinationPort: adapters.SYNTHETIC_DESTINATION_PORT,
  endpointUrl: "https://harness.invalid/probe",
};
// One configuration per attempt: a handshake spends the configuration's
// authorization when it builds the request, so a session must never reuse
// another session's configuration object.
const freshConfig = () => measurement.parseConfig(baseConfig);
const config = freshConfig();
const returnHeader = framing.encodeRelayHeader({
  destinationAddress: adapters.SYNTHETIC_CLIENT_ADDRESS,
  destinationPort: adapters.SYNTHETIC_CLIENT_PORT,
  sourceAddress: adapters.SYNTHETIC_DESTINATION_ADDRESS,
  sourcePort: adapters.SYNTHETIC_DESTINATION_PORT + 1,
});

const records = {};
for (const limit of JSON.parse(process.env.HARNESS_LIMITS)) {
  const adapter = new adapters.LoopbackAdapter(limit);
  const driver = adapters.openLoopbackSession(
    adapter,
    plan,
    freshConfig(),
    new Uint8Array(12),
  );
  adapters.runLoopbackSession(driver, adapter);
  records[String(limit)] = driver.sessionRecord();
}

// The rejection and accounting side of the driver. The published adapter can
// misbehave in exactly the ways scripts/relay_loopback.py can, so a fault run
// here is comparable with the reference implementation's.
function makePlan(sizes, packed) {
  return measurement.buildPlan(
    {
      $schema: "../schemas/relay-measurement-vector.schema.json",
      directions: { browserToServer: sizes, serverToBrowser: sizes },
      formatVersion: 1,
      framing: vector.framing,
      packedCases: [{ direction: "browserToServer", sizes: packed }],
      payloadIdentification: vector.payloadIdentification,
      requiredBoundaryBytes: [17],
    },
    maxInFlight,
  );
}

const faultPlan = makePlan([16, 17, 18], [16, 17]);
// A plan whose first case is the committed vector's zero-byte boundary, so the
// 0-byte inner datagram is exercised in both directions rather than inferred.
const zeroPlan = makePlan([0, 1, 16, 17, 18], [16, 17]);

function accounting(driver) {
  const record = driver.sessionRecord();
  return {
    errorDatagrams: record.errorDatagrams,
    foreignFrames: record.foreignFrames,
    headerMismatchFrames: record.headerMismatchFrames,
    keepAliveDatagrams: record.keepAliveDatagrams,
    malformedFrames: record.malformedFrames,
    outcomes: record.cases.map((item) => item.outcome),
    unexpectedControlDatagrams: record.unexpectedControlDatagrams,
    unmatchedFrames: record.unmatchedFrames,
    writeFailures: record.writeFailures,
  };
}

function runFault(options, sessionPlan = faultPlan) {
  const adapter = new adapters.LoopbackAdapter(20000, options);
  const driver = adapters.openLoopbackSession(
    adapter,
    sessionPlan,
    freshConfig(),
    new Uint8Array(12),
  );
  adapters.runLoopbackSession(driver, adapter);
  return accounting(driver);
}

const faultRuns = {
  clean: runFault({}),
  truncatedReturn: runFault({ fault: adapters.FAULT_TRUNCATED_RETURN }),
  packedReturn: runFault({ fault: adapters.FAULT_PACKED_RETURN }),
  headerOnlyReturn: runFault({ fault: adapters.FAULT_HEADER_ONLY_RETURN }),
  declaredOversize: runFault({ fault: adapters.FAULT_DECLARED_OVERSIZE }),
  corruptPayload: runFault({ fault: adapters.FAULT_CORRUPT_PAYLOAD }),
  foreignHeader: runFault({ fault: adapters.FAULT_FOREIGN_HEADER }),
  dropped: runFault({ dropInnerSizes: [17] }),
  refused: runFault({ refuseSend: true }),
  // A destination the session was not authorized for is answered in band, and
  // the answer must not complete anything.
  refusedDestination: runFault({
    destinationAddress: adapters.SYNTHETIC_FOREIGN_ADDRESS,
  }),
  zeroLengthPreserved: runFault({}, zeroPlan),
  // The behaviour the amendment removed: the 0-byte case can only time out.
  zeroLengthDropped: runFault({ fault: adapters.FAULT_DROP_ZERO_LENGTH }, zeroPlan),
};

// The zero-byte boundary in both directions, observed at the relay rather than
// inferred from the case outcome.
const zeroAdapter = new adapters.LoopbackAdapter(20000);
const zeroDriver = adapters.openLoopbackSession(
  zeroAdapter,
  zeroPlan,
  freshConfig(),
  new Uint8Array(12),
);
adapters.runLoopbackSession(zeroDriver, zeroAdapter);
const zeroLengthBoundary = {
  innerSizesSeenByRelay: zeroAdapter.receivedInnerSizes.slice().sort((a, b) => a - b),
  outcomes: zeroDriver.sessionRecord().cases.map((item) => item.outcome),
  sentFrameBytes: zeroDriver.sessionRecord().cases[0].sentFrameBytes,
  returnedFrameBytes: zeroDriver.sessionRecord().cases[0].receivedFrames[0].frameBytes,
};

// An invalid authorization is refused in band and the session is then closed.
// The value is spent by the attempt either way.
const refusedAdapter = new adapters.LoopbackAdapter(20000, {
  refuseAuthorization: true,
});
const refusedHandshake = new measurement.SessionHandshake(freshConfig());
refusedAdapter.send(refusedHandshake.requestDatagram());
let refusalCode = null;
let refusalKind = "";
try {
  for (const datagram of refusedAdapter.drain()) {
    refusedHandshake.accept(datagram);
  }
} catch (error) {
  refusalKind = error instanceof framing.RelayRefusedError ? "refused" : "other";
  refusalCode = error.code === undefined ? null : error.code;
}
let sentAfterRefusal = "accepted";
try {
  refusedAdapter.send(framing.encodeKeepAlive());
} catch (error) {
  sentAfterRefusal =
    error instanceof measurement.AdapterSendError ? "refused" : "other";
}
const authorizationRefusal = {
  code: refusalCode,
  completed: refusedHandshake.completed,
  kind: refusalKind,
  sentAfterRefusal,
  spent: refusedHandshake.spent,
};

// A frame carrying another session's nonce must be counted as foreign and must
// complete nothing. This is the concurrent-session acceptance evidence.
const foreignAdapter = new adapters.LoopbackAdapter(20000, { echo: false });
const foreignDriver = adapters.openLoopbackSession(
  foreignAdapter,
  faultPlan,
  freshConfig(),
  new Uint8Array(12),
);
foreignDriver.pump(0);
const otherNonce = new Uint8Array(12).fill(9);
foreignDriver.receive(
  framing.encodeFrame(
    returnHeader,
    [framing.buildPayload(otherNonce, 0, 16)],
    framing.SERVER_TO_BROWSER,
  ),
  1,
);
faultRuns.foreignNonce = accounting(foreignDriver);

// The control datagrams a measuring session can meet: the relay answers a
// keep-alive with a keep-alive, a second assignment is not part of a measured
// session, and an unknown type or a datagram too short to carry one is
// malformed. None of them may complete a case.
const controlAdapter = new adapters.LoopbackAdapter(20000, { echo: false });
const controlDriver = adapters.openLoopbackSession(
  controlAdapter,
  faultPlan,
  freshConfig(),
  new Uint8Array(12),
);
controlDriver.pump(0);
controlAdapter.drain();
controlAdapter.send(framing.encodeKeepAlive(new Uint8Array(8)));
for (const datagram of controlAdapter.drain()) {
  controlDriver.receive(datagram, 1);
}
controlDriver.receive(
  framing.encodeAddressAssignment(adapters.SYNTHETIC_CLIENT_ADDRESS),
  2,
);
controlDriver.receive(new Uint8Array([0, 0, 0, 9, 1, 2, 3]), 3);
controlDriver.receive(new Uint8Array([0, 0, 0]), 4);
faultRuns.controlDatagrams = accounting(controlDriver);

// The browser's own validator and summary, over a report it produced and over
// the same mutations the Python report tests use.
const cleanAdapter = new adapters.LoopbackAdapter(20000);
const cleanDriver = adapters.openLoopbackSession(
  cleanAdapter,
  faultPlan,
  freshConfig(),
  new Uint8Array(12),
);
adapters.runLoopbackSession(cleanDriver, cleanAdapter);
const faultReport = measurement.buildReport(
  [cleanDriver.sessionRecord()],
  "ab".repeat(32),
  "harness",
);

function rejects(mutate) {
  const copy = JSON.parse(JSON.stringify(faultReport));
  mutate(copy);
  try {
    measurement.validateReport(copy, faultPlan);
  } catch (error) {
    return error instanceof measurement.MeasurementReportError;
  }
  return false;
}

const validatorRejections = {
  unknownField: rejects((r) => {
    r.extra = 1;
  }),
  missingField: rejects((r) => {
    delete r.framing;
  }),
  badKind: rejects((r) => {
    r.kind = "something-else";
  }),
  badFormatVersion: rejects((r) => {
    r.formatVersion = 1;
  }),
  missingSessionCounter: rejects((r) => {
    delete r.sessions[0].keepAliveDatagrams;
  }),
  uppercaseDigest: rejects((r) => {
    r.measurementVectorSha256 = "AB".repeat(32);
  }),
  spacedDigest: rejects((r) => {
    r.measurementVectorSha256 = ` ${"ab".repeat(32)}`.slice(0, 64);
  }),
  frameArithmetic: rejects((r) => {
    r.sessions[0].cases[0].sentFrameBytes += 1;
  }),
  overheadArithmetic: rejects((r) => {
    const item = r.sessions[0].cases.find((c) => c.receivedFrames.length);
    item.receivedFrames[0].frameBytes += 1;
  }),
  foreignReturnedSize: rejects((r) => {
    const item = r.sessions[0].cases.find((c) => c.receivedFrames.length);
    item.outcome = "timedOut";
    item.roundTripMilliseconds = null;
    item.receivedFrames = [{ frameBytes: 99 + 42, innerBytes: 99 }];
  }),
  echoedAboveTransportMaximum: rejects((r) => {
    r.sessions[0].maxDatagramSizeBytes = 43;
  }),
  sendFailedWithFrames: rejects((r) => {
    const item = r.sessions[0].cases.find((c) => c.receivedFrames.length);
    item.outcome = "sendFailed";
    item.roundTripMilliseconds = null;
  }),
  notRunWithFrames: rejects((r) => {
    const item = r.sessions[0].cases.find((c) => c.receivedFrames.length);
    item.outcome = "notRun";
    item.roundTripMilliseconds = null;
  }),
  negativeCounter: rejects((r) => {
    r.sessions[0].headerMismatchFrames = -1;
  }),
  reusedOrdinal: rejects((r) => {
    r.sessions[0].cases[1].ordinals = r.sessions[0].cases[0].ordinals.slice();
  }),
  caseWiderThanBound: rejects((r) => {
    r.sessions[0].maxInFlightDatagrams = 1;
  }),
  collidingSessionIndices: rejects((r) => {
    r.sessions.push(JSON.parse(JSON.stringify(r.sessions[0])));
  }),
  planMismatch: rejects((r) => {
    r.sessions[0].cases.pop();
  }),
};

const faultSummary = measurement.summarizeReport(faultReport, faultPlan);

// A run stopped before it reached every case, so the JS notRun/timedOut split
// is exercised rather than inferred: with a bound of two, one pump starts the
// first two cases and never reaches the rest.
const partialConfig = measurement.parseConfig({
  ...baseConfig,
  maxInFlightDatagrams: 2,
});
const partialAdapter = new adapters.LoopbackAdapter(20000, { echo: false });
const partialDriver = adapters.openLoopbackSession(
  partialAdapter,
  faultPlan,
  partialConfig,
  new Uint8Array(12),
);
partialDriver.pump(0);
const partialRun = {
  datagramsSent: partialAdapter.receivedDatagrams,
  outcomes: partialDriver.sessionRecord().cases.map((item) => item.outcome),
};

// Configuration rejections both implementations must agree on. The spaced hex
// is the interesting one: it has the right character length but decodes short.
function configRefused(overrides) {
  try {
    measurement.parseConfig({ ...baseConfig, ...overrides });
  } catch (error) {
    return error instanceof measurement.ProbeConfigError;
  }
  return false;
}

const destinationHex = framing.bytesToHex(adapters.SYNTHETIC_DESTINATION_ADDRESS);
const spacedAddressHex = `  ${destinationHex.slice(2)}`;
const configRejections = {
  spacedDestinationAddress: configRefused({
    destinationAddressHex: spacedAddressHex,
  }),
  shortDestinationAddress: configRefused({
    destinationAddressHex: destinationHex.slice(2),
  }),
  nonHexDestinationAddress: configRefused({
    destinationAddressHex: "zz".repeat(16),
  }),
  unspecifiedDestinationAddress: configRefused({
    destinationAddressHex: "00".repeat(16),
  }),
  destinationPortZero: configRefused({ destinationPort: 0 }),
  destinationPortAboveRange: configRefused({ destinationPort: 65536 }),
  clientSourcePortZero: configRefused({ clientSourcePort: 0 }),
  emptyAuthorization: configRefused({ authorization: "" }),
  endpointWithWithdrawnPlaceholder: configRefused({
    endpointUrl: "https://harness.invalid/p?a={authorization}",
  }),
  endpointWithFragment: configRefused({
    endpointUrl: "https://harness.invalid/p#fragment",
  }),
  endpointNotHttps: configRefused({ endpointUrl: "http://harness.invalid/p" }),
  boundBelowOne: configRefused({ maxInFlightDatagrams: 0 }),
  assignmentTimeoutBelowOne: configRefused({
    assignmentTimeoutMilliseconds: 0,
  }),
};

// A late untagged echo: case 0 (0 bytes) times out, then its echo arrives while
// case 1 (1 byte) is outstanding. Only the length separates them, so the frame
// must be unattributable rather than a defect charged to case 1.
const lateAdapter = new adapters.LoopbackAdapter(20000);
const lateDriver = adapters.openLoopbackSession(
  lateAdapter,
  plan,
  freshConfig(),
  new Uint8Array(12),
);
lateDriver.pump(0);
lateAdapter.drain();
lateDriver.pump(2000);
lateAdapter.drain();
lateDriver.receive(
  framing.encodeFrame(
    returnHeader,
    [new Uint8Array(0)],
    framing.SERVER_TO_BROWSER,
  ),
  2001,
);
const lateUntaggedEcho = {
  unmatchedFrames: lateDriver.unmatchedFrames,
  outcomes: lateDriver.sessionRecord().cases.slice(0, 2).map((c) => c.outcome),
};

// The authorization travels as UTF-8 in one REQUEST_ADDRESS datagram, so the
// two implementations have to agree on the encoding rather than on the URL
// substitution the withdrawn profile used. The synthetic values include
// $-patterns and non-ASCII for exactly that reason.
const addressRequests = {};
for (const value of JSON.parse(process.env.HARNESS_AUTHORIZATIONS)) {
  addressRequests[value] = framing.bytesToHex(
    new measurement.SessionHandshake(
      measurement.parseConfig({ ...baseConfig, authorization: value }),
    ).requestDatagram(),
  );
}

// The authorization is single-use by construction: the handshake drops it as
// soon as the request datagram exists and refuses to build a second one.
const spendConfig = measurement.parseConfig(baseConfig);
const spendHandshake = new measurement.SessionHandshake(spendConfig);
const spentBefore = spendHandshake.spent;
spendHandshake.requestDatagram();
let secondRequest = "accepted";
try {
  spendHandshake.requestDatagram();
} catch (error) {
  secondRequest =
    error instanceof framing.RelaySessionError ? "refused" : "other";
}
let reusedConfigRequest = "accepted";
try {
  new measurement.SessionHandshake(spendConfig).requestDatagram();
} catch (error) {
  reusedConfigRequest =
    error instanceof framing.RelaySessionError ? "refused" : "other";
}
const singleUseAuthorization = {
  configAuthorizationAfter: spendConfig.authorization,
  reusedConfigRequest,
  secondRequest,
  spentAfter: spendHandshake.spent,
  spentBefore,
};

process.stdout.write(
  JSON.stringify({
    addressRequests,
    authorizationRefusal,
    conformanceChecked,
    configRejections,
    faultRuns,
    faultSummary,
    lateUntaggedEcho,
    partialRun,
    singleUseAuthorization,
    validatorRejections,
    zeroLengthBoundary,
    planCases: plan.cases.length,
    planDatagrams: plan.cases.reduce(
      (total, item) => total + item.datagrams.length,
      0,
    ),
    maxInnerDatagramBytes: plan.maxInnerDatagramBytes,
    records,
  }),
);
