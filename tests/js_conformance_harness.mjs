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
    lateUntaggedEcho,
    planCases: plan.cases.length,
    planDatagrams: plan.cases.reduce(
      (total, item) => total + item.datagrams.length,
      0,
    ),
    maxInnerDatagramBytes: plan.maxInnerDatagramBytes,
    records,
  }),
);
