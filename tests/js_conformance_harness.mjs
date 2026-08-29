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

const maxInFlight = Number(process.env.HARNESS_MAX_IN_FLIGHT);
const plan = measurement.buildPlan(vector, maxInFlight);
const prefix = new Uint8Array(40).map((_value, index) => index);
const returnPrefix = new Uint8Array(40).map(
  (_value, index) => (index + 0x80) & 0xff,
);
const config = measurement.parseConfig({
  authorization: "harness",
  destinationPortMatchesProjection: true,
  endpointTemplate: "https://harness.invalid/{authorization}",
  routingPrefixHex: framing.bytesToHex(prefix),
});

const records = {};
for (const limit of JSON.parse(process.env.HARNESS_LIMITS)) {
  const adapter = new adapters.LoopbackAdapter(limit, returnPrefix);
  const driver = new measurement.SessionDriver(
    plan,
    adapter,
    new Uint8Array(12),
    config,
  );
  adapters.runLoopbackSession(driver, adapter);
  records[String(limit)] = driver.sessionRecord();
}

// The rejection and accounting side of the driver. The published adapter can
// misbehave in exactly the ways scripts/relay_loopback.py can, so a fault run
// here is comparable with the reference implementation's.
const faultPlan = measurement.buildPlan(
  {
    $schema: "../schemas/relay-measurement-vector.schema.json",
    directions: { browserToServer: [16, 17, 18], serverToBrowser: [16, 17, 18] },
    formatVersion: 1,
    framing: vector.framing,
    packedCases: [{ direction: "browserToServer", sizes: [16, 17] }],
    payloadIdentification: vector.payloadIdentification,
    requiredBoundaryBytes: [17],
  },
  maxInFlight,
);

function runFault(options) {
  const adapter = new adapters.LoopbackAdapter(20000, returnPrefix, options);
  const driver = new measurement.SessionDriver(
    faultPlan,
    adapter,
    new Uint8Array(12),
    config,
  );
  adapters.runLoopbackSession(driver, adapter);
  const record = driver.sessionRecord();
  return {
    foreignFrames: record.foreignFrames,
    malformedFrames: record.malformedFrames,
    outcomes: record.cases.map((item) => item.outcome),
    prefixMismatchFrames: record.prefixMismatchFrames,
    unmatchedFrames: record.unmatchedFrames,
    writeFailures: record.writeFailures,
  };
}

const faultRuns = {
  clean: runFault({}),
  truncatedReturn: runFault({ fault: adapters.FAULT_TRUNCATED_RETURN }),
  packedReturn: runFault({ fault: adapters.FAULT_PACKED_RETURN }),
  headerOnlyReturn: runFault({ fault: adapters.FAULT_HEADER_ONLY_RETURN }),
  declaredOversize: runFault({ fault: adapters.FAULT_DECLARED_OVERSIZE }),
  corruptPayload: runFault({ fault: adapters.FAULT_CORRUPT_PAYLOAD }),
  dropped: runFault({ dropInnerSizes: [17] }),
  refused: runFault({ refuseSend: true }),
};

// A foreign prefix is only detectable when the expected one was stated, and a
// foreign nonce needs traffic from another session's driver.
const foreignPrefixConfig = measurement.parseConfig({
  authorization: "harness",
  destinationPortMatchesProjection: true,
  endpointTemplate: "https://harness.invalid/{authorization}",
  expectedReturnPrefixHex: framing.bytesToHex(returnPrefix),
  routingPrefixHex: framing.bytesToHex(prefix),
});
const prefixAdapter = new adapters.LoopbackAdapter(20000, returnPrefix, {
  fault: adapters.FAULT_FOREIGN_PREFIX,
});
const prefixDriver = new measurement.SessionDriver(
  faultPlan,
  prefixAdapter,
  new Uint8Array(12),
  foreignPrefixConfig,
);
adapters.runLoopbackSession(prefixDriver, prefixAdapter);
faultRuns.foreignPrefix = {
  foreignFrames: prefixDriver.foreignFrames,
  malformedFrames: prefixDriver.malformedFrames,
  outcomes: prefixDriver.sessionRecord().cases.map((item) => item.outcome),
  prefixMismatchFrames: prefixDriver.prefixMismatchFrames,
  unmatchedFrames: prefixDriver.unmatchedFrames,
  writeFailures: prefixDriver.sessionRecord().writeFailures,
};

// A frame carrying another session's nonce must be counted as foreign and must
// complete nothing. This is the concurrent-session acceptance evidence.
const foreignAdapter = new adapters.LoopbackAdapter(20000, returnPrefix, {
  echo: false,
});
const foreignDriver = new measurement.SessionDriver(
  faultPlan,
  foreignAdapter,
  new Uint8Array(12),
  config,
);
foreignDriver.pump(0);
const otherNonce = new Uint8Array(12).fill(9);
foreignDriver.receive(
  framing.encodeFrame(
    returnPrefix,
    [framing.buildPayload(otherNonce, 0, 16)],
    framing.SERVER_TO_BROWSER,
  ),
  1,
);
faultRuns.foreignNonce = {
  foreignFrames: foreignDriver.foreignFrames,
  malformedFrames: foreignDriver.malformedFrames,
  outcomes: foreignDriver.sessionRecord().cases.map((item) => item.outcome),
  prefixMismatchFrames: foreignDriver.prefixMismatchFrames,
  unmatchedFrames: foreignDriver.unmatchedFrames,
  writeFailures: foreignDriver.sessionRecord().writeFailures,
};

// The browser's own validator and summary, over a report it produced and over
// the same mutations the Python report tests use.
const cleanAdapter = new adapters.LoopbackAdapter(20000, returnPrefix);
const cleanDriver = new measurement.SessionDriver(
  faultPlan,
  cleanAdapter,
  new Uint8Array(12),
  config,
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
    r.sessions[0].foreignFrames = -1;
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

// Configuration rejections both implementations must agree on. The spaced hex
// is the interesting one: it has the right character length but decodes short.
function configRefused(overrides) {
  try {
    measurement.parseConfig({
      authorization: "harness",
      destinationPortMatchesProjection: true,
      endpointTemplate: "https://harness.invalid/{authorization}",
      routingPrefixHex: framing.bytesToHex(prefix),
      ...overrides,
    });
  } catch (error) {
    return error instanceof measurement.ProbeConfigError;
  }
  return false;
}

const spacedPrefixHex = `  ${framing.bytesToHex(prefix).slice(2)}`;
const configRejections = {
  spacedRoutingPrefix: configRefused({ routingPrefixHex: spacedPrefixHex }),
  spacedReturnPrefix: configRefused({ expectedReturnPrefixHex: spacedPrefixHex }),
  shortRoutingPrefix: configRefused({
    routingPrefixHex: framing.bytesToHex(prefix).slice(2),
  }),
  nonHexRoutingPrefix: configRefused({ routingPrefixHex: "zz".repeat(40) }),
  unacknowledgedPort: configRefused({ destinationPortMatchesProjection: false }),
  emptyAuthorization: configRefused({ authorization: "" }),
  templateWithoutPlaceholder: configRefused({
    endpointTemplate: "https://harness.invalid/none",
  }),
  boundBelowOne: configRefused({ maxInFlightDatagrams: 0 }),
};

// A late untagged echo: case 0 (0 bytes) times out, then its echo arrives while
// case 1 (1 byte) is outstanding. Only the length separates them, so the frame
// must be unattributable rather than a defect charged to case 1.
const lateAdapter = new adapters.LoopbackAdapter(20000, returnPrefix);
const lateDriver = new measurement.SessionDriver(
  plan,
  lateAdapter,
  new Uint8Array(12),
  config,
);
lateDriver.pump(0);
lateAdapter.drain();
lateDriver.pump(2000);
lateAdapter.drain();
lateDriver.receive(
  framing.encodeFrame(
    returnPrefix,
    [new Uint8Array(0)],
    framing.SERVER_TO_BROWSER,
  ),
  2001,
);
const lateUntaggedEcho = {
  unmatchedFrames: lateDriver.unmatchedFrames,
  outcomes: lateDriver.sessionRecord().cases.slice(0, 2).map((c) => c.outcome),
};

// The authorization is substituted into the endpoint template. JavaScript's
// String.replace() would interpret $-patterns in it, so these deliberately
// $-heavy synthetic values are compared against the Python implementation.
const endpointUrls = {};
for (const value of JSON.parse(process.env.HARNESS_AUTHORIZATIONS)) {
  endpointUrls[value] = measurement
    .parseConfig({
      authorization: value,
      destinationPortMatchesProjection: true,
      endpointTemplate: "https://harness.invalid/p?a={authorization}&b=x",
      routingPrefixHex: framing.bytesToHex(prefix),
    })
    .endpointUrl();
}

process.stdout.write(
  JSON.stringify({
    conformanceChecked,
    endpointUrls,
    configRejections,
    faultRuns,
    faultSummary,
    lateUntaggedEcho,
    validatorRejections,
    planCases: plan.cases.length,
    planDatagrams: plan.cases.reduce(
      (total, item) => total + item.datagrams.length,
      0,
    ),
    maxInnerDatagramBytes: plan.maxInnerDatagramBytes,
    records,
  }),
);
