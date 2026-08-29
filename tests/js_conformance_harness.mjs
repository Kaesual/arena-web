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

const plan = measurement.buildPlan(vector);
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

process.stdout.write(
  JSON.stringify({
    conformanceChecked,
    planCases: plan.cases.length,
    planDatagrams: plan.cases.reduce(
      (total, item) => total + item.datagrams.length,
      0,
    ),
    maxInnerDatagramBytes: plan.maxInnerDatagramBytes,
    records,
  }),
);
