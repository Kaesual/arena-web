// SPDX-License-Identifier: GPL-2.0-or-later
//
// The probe's user interface: read runtime values, self-test the framing, run
// one measured session per one-time authorization, accumulate a report.
//
// Every environment-specific value lives in the form and in memory only. The
// endpoint URL is composed inside the transport adapter at connect time; the
// log, the report and the page never contain the endpoint, the authorization,
// a certificate hash or the routing prefix.

import {
  bytesToHex,
  frameBytesForSizes,
  buildPayload,
  datagramTag,
  decodeFrame,
  encodeFrame,
  hexToBytes,
  RelayFrameError,
} from "./relay-framing.js";
import {
  OUTCOME_ECHOED,
  MeasurementReportError,
  SessionDriver,
  buildPlan,
  buildReport,
  parseConfig,
  summarizeReport,
} from "./measurement.js";
import {
  LoopbackAdapter,
  WebTransportAdapter,
  runLoopbackSession,
} from "./adapters.js";

const SELF_TEST_PREFIX = new Uint8Array(40).map((_value, index) => index);
const SELF_TEST_RETURN_PREFIX = new Uint8Array(40).map(
  (_value, index) => (index + 0x80) & 0xff,
);

const elements = {};
let plan = null;
let measurementVectorSha256 = null;
let selfTestPassed = false;
const sessionRecords = [];

function log(message) {
  const line = document.createElement("div");
  line.textContent = message;
  elements.log.appendChild(line);
  elements.log.scrollTop = elements.log.scrollHeight;
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return bytesToHex(new Uint8Array(digest));
}

// The committed vectors are the portable expression of the contract. Running
// them through this file proves that the browser implementation and the tested
// Python implementation agree, before any network traffic exists.
function checkConformanceVectors(vectors) {
  for (const item of vectors.encodeCases) {
    const payloads = item.payloadHexes.map((value) => hexToBytes(value));
    const frame = encodeFrame(
      hexToBytes(item.prefixHex),
      payloads,
      item.direction,
    );
    if (bytesToHex(frame) !== item.frameHex) {
      throw new Error(`encode case ${item.name} produced other bytes`);
    }
    if (frame.length !== item.frameBytes) {
      throw new Error(`encode case ${item.name} has the wrong length`);
    }
    const decoded = decodeFrame(frame, item.direction);
    if (decoded.datagrams.length !== payloads.length) {
      throw new Error(`decode of ${item.name} lost a datagram`);
    }
    decoded.datagrams.forEach((datagram, index) => {
      if (bytesToHex(datagram) !== item.payloadHexes[index]) {
        throw new Error(`decode of ${item.name} changed a payload`);
      }
    });
  }
  for (const item of vectors.decodeRejections) {
    let rejected = false;
    try {
      decodeFrame(
        hexToBytes(item.frameHex),
        item.direction,
        item.maxInnerDatagramBytes,
      );
    } catch (error) {
      rejected = error instanceof RelayFrameError;
    }
    if (!rejected) {
      throw new Error(`decode rejection ${item.name} was accepted`);
    }
  }
  for (const item of vectors.encodeRejections) {
    let rejected = false;
    try {
      encodeFrame(
        hexToBytes(item.prefixHex),
        item.payloadHexes.map((value) => hexToBytes(value)),
        item.direction,
        item.maxInnerDatagramBytes,
      );
    } catch (error) {
      rejected = error instanceof RelayFrameError;
    }
    if (!rejected) {
      throw new Error(`encode rejection ${item.name} was accepted`);
    }
  }
  for (const item of vectors.tagCases) {
    const tag = datagramTag(hexToBytes(item.sessionNonceHex), item.ordinal);
    if (bytesToHex(tag) !== item.tagHex) {
      throw new Error(`tag case ${item.name} produced other bytes`);
    }
  }
  for (const item of vectors.payloadCases) {
    const payload = buildPayload(
      hexToBytes(item.sessionNonceHex),
      item.ordinal,
      item.size,
    );
    if (bytesToHex(payload) !== item.payloadHex) {
      throw new Error(`payload case ${item.name} produced other bytes`);
    }
  }
  return (
    vectors.encodeCases.length +
    vectors.decodeRejections.length +
    vectors.encodeRejections.length +
    vectors.tagCases.length +
    vectors.payloadCases.length
  );
}

// A whole plan over the in-memory relay, so the driver itself is proven before
// the network is involved.
function checkDriverAgainstLoopback() {
  const limit = frameBytesForSizes([plan.maxInnerDatagramBytes]);
  const adapter = new LoopbackAdapter(limit, SELF_TEST_RETURN_PREFIX);
  const config = parseConfig({
    authorization: "self-test",
    destinationPortMatchesProjection: true,
    endpointTemplate: "https://self.test/{authorization}",
    routingPrefixHex: bytesToHex(SELF_TEST_PREFIX),
  });
  const driver = new SessionDriver(plan, adapter, new Uint8Array(12), config);
  runLoopbackSession(driver, adapter);
  const record = driver.sessionRecord();
  const unexpected = record.cases.filter(
    (item) => item.outcome !== OUTCOME_ECHOED,
  );
  if (unexpected.length > 0) {
    throw new Error(
      `the loopback self-test did not complete case ${unexpected[0].caseIndex}`,
    );
  }
  if (record.unmatchedFrames || record.malformedFrames || record.foreignFrames) {
    throw new Error("the loopback self-test produced unattributed frames");
  }
  return record.cases.length;
}

function readConfig() {
  const hashes = elements.certificateHashes.value
    .split(/\s+/)
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
  return parseConfig({
    authorization: elements.authorization.value,
    caseTimeoutMilliseconds: Number(elements.caseTimeout.value),
    certificateHashes: hashes,
    destinationPortMatchesProjection: elements.portAcknowledged.checked,
    endpointTemplate: elements.endpointTemplate.value.trim(),
    expectedReturnPrefixHex: elements.expectedReturnPrefix.value.trim(),
    maxInFlightDatagrams: Number(elements.maxInFlight.value),
    pathNotes: elements.pathNotes.value,
    routingPrefixHex: elements.routingPrefix.value.trim(),
  });
}

function describe(record) {
  const counts = new Map();
  for (const item of record.cases) {
    counts.set(item.outcome, (counts.get(item.outcome) || 0) + 1);
  }
  return Array.from(counts.entries())
    .sort()
    .map(([outcome, count]) => `${outcome} ${count}`)
    .join(", ");
}

// Nothing is shown or offered for download before it validates. A report that
// fails its own contract is a defect to surface, not a file to hand out.
function refreshReport() {
  if (sessionRecords.length === 0) {
    return;
  }
  const report = buildReport(
    sessionRecords,
    measurementVectorSha256,
    elements.pathNotes.value,
  );
  let summary;
  try {
    summary = summarizeReport(report, plan);
  } catch (error) {
    if (!(error instanceof MeasurementReportError)) {
      throw error;
    }
    elements.summary.textContent = "";
    elements.report.textContent = "";
    elements.download.hidden = true;
    log(`the report did not validate and is not offered: ${error.message}`);
    return;
  }
  const text = `${JSON.stringify(report, null, 2)}\n`;
  elements.report.textContent = text;
  elements.summary.textContent = `${JSON.stringify(summary, null, 2)}\n`;
  const blob = new Blob([text], { type: "application/json" });
  if (elements.download.href) {
    URL.revokeObjectURL(elements.download.href);
  }
  elements.download.href = URL.createObjectURL(blob);
  elements.download.download = "relay-path-measurement.json";
  elements.download.hidden = false;
}

async function runOneSession() {
  const config = readConfig();
  const sessionIndex = sessionRecords.length;
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  log(`session ${sessionIndex}: connecting`);
  const adapter = await WebTransportAdapter.connect(config);
  log(`session ${sessionIndex}: maxDatagramSize ${adapter.maxDatagramSizeBytes}`);
  const driver = new SessionDriver(plan, adapter, nonce, config, sessionIndex);
  const started = performance.now();
  const nowMs = () => performance.now() - started;
  let stopped = false;
  // At least one case reaches an outcome per timeout round, so this covers the
  // committed plan even on a path that answers nothing at all. Sizing it any
  // tighter would leave cases unrun, and an unrun case is a hole in the very
  // range WP6 reads.
  const budget = config.caseTimeoutMilliseconds * (plan.cases.length + 1) + 5000;
  const reading = adapter
    .readFrames(
      (frame) => driver.receive(frame, nowMs()),
      () => stopped,
    )
    .catch(() => {
      // A closed session ends the read loop; the driver reports the effect.
    });
  driver.pump(nowMs());
  while (!driver.finished && nowMs() < budget) {
    await sleep(5);
    driver.pump(nowMs());
  }
  if (!driver.finished) {
    log(
      `session ${sessionIndex}: time budget exceeded; ` +
        "every unfinished case is recorded as timed out",
    );
  }
  // Stop before closing: pumping a closed session would start cases that can
  // never be answered.
  stopped = true;
  await adapter.close();
  await reading;
  const record = driver.sessionRecord();
  sessionRecords.push(record);
  log(`session ${sessionIndex}: ${describe(record)}`);
  log(
    `session ${sessionIndex}: foreign ${record.foreignFrames}, ` +
      `malformed ${record.malformedFrames}, ` +
      `prefix mismatch ${record.prefixMismatchFrames}, ` +
      `unattributed ${record.unmatchedFrames}`,
  );
  if (adapter.writeFailures > 0) {
    log(`session ${sessionIndex}: ${adapter.writeFailures} datagram writes failed`);
  }
  refreshReport();
}

async function onRun() {
  elements.run.disabled = true;
  try {
    if (!selfTestPassed) {
      throw new Error("the self-test has not passed");
    }
    await runOneSession();
    // A single-use allowance is spent. Make that visible rather than letting a
    // second run silently reuse it.
    elements.authorization.value = "";
    log("authorization field cleared; paste a fresh one for the next session");
  } catch (error) {
    log(`refused: ${error.message}`);
  } finally {
    elements.run.disabled = false;
  }
}

async function start() {
  for (const id of [
    "authorization",
    "caseTimeout",
    "certificateHashes",
    "download",
    "endpointTemplate",
    "expectedReturnPrefix",
    "log",
    "maxInFlight",
    "pathNotes",
    "portAcknowledged",
    "report",
    "routingPrefix",
    "run",
    "summary",
  ]) {
    elements[id] = document.getElementById(id);
  }
  elements.run.addEventListener("click", onRun);
  try {
    const vectorResponse = await fetch("../locks/relay-measurement-vector.json");
    if (!vectorResponse.ok) {
      throw new Error("the measurement vector could not be read");
    }
    const vectorBytes = await vectorResponse.arrayBuffer();
    measurementVectorSha256 = await sha256Hex(vectorBytes);
    plan = buildPlan(JSON.parse(new TextDecoder().decode(vectorBytes)));
    log(
      `measurement vector sha256:${measurementVectorSha256} — ` +
        `${plan.cases.length} cases, ceiling ${plan.maxInnerDatagramBytes} bytes`,
    );
    const conformance = await (await fetch("./conformance-vectors.json")).json();
    const checked = checkConformanceVectors(conformance);
    const driven = checkDriverAgainstLoopback();
    selfTestPassed = true;
    log(`self-test passed: ${checked} conformance vectors, ${driven} loopback cases`);
    elements.run.disabled = false;
  } catch (error) {
    log(`self-test failed: ${error.message}`);
    log("the probe will not open a session until this passes");
  }
}

start();
