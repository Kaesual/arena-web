// SPDX-License-Identifier: GPL-2.0-or-later
//
// The probe's user interface: read runtime values, self-test the framing, run
// one measured session per one-time authorization, accumulate a report.
//
// Every environment-specific value lives in the form and in memory only. The
// authorization is placed in the session's first datagram once and the field is
// cleared afterwards; the log, the report and the page never contain the
// endpoint, the authorization, a certificate hash, the destination address or
// the virtual client address the relay assigned.

import {
  bytesToHex,
  frameBytesForSizes,
  buildPayload,
  datagramTag,
  decodeAddressAssignment,
  decodeError,
  decodeFrame,
  decodeRelayHeader,
  encodeAddressAssignment,
  encodeAddressRequest,
  encodeError,
  encodeFrame,
  encodeKeepAlive,
  hexToBytes,
  routingContext,
  RelayFrameError,
  RelayProbeError,
  RelayRefusedError,
  RelaySessionError,
} from "./relay-framing.js";
import {
  AdapterSendError,
  MeasurementPlanError,
  MeasurementReportError,
  OUTCOME_ECHOED,
  ProbeConfigError,
  SessionDriver,
  buildPlan,
  buildReport,
  parseConfig,
  summarizeReport,
} from "./measurement.js";
import {
  LoopbackAdapter,
  SYNTHETIC_AUTHORIZATION,
  SYNTHETIC_CLIENT_ADDRESS,
  SYNTHETIC_DESTINATION_ADDRESS,
  SYNTHETIC_DESTINATION_PORT,
  WebTransportAdapter,
  establishSession,
  openLoopbackSession,
  runLoopbackSession,
} from "./adapters.js";

// Only this project's own error classes carry messages that are known not to
// contain runtime configuration. Anything else — a platform error above all —
// contributes its class name and nothing more, because Chromium's WebTransport
// errors quote the URL, and the URL carries the authorization.
const OWN_ERRORS = [
  AdapterSendError,
  MeasurementPlanError,
  MeasurementReportError,
  ProbeConfigError,
  RelayFrameError,
  RelayProbeError,
  RelayRefusedError,
  RelaySessionError,
];

function describeError(error) {
  if (OWN_ERRORS.some((kind) => error instanceof kind)) {
    return error.message;
  }
  return error && error.name ? error.name : "an unexpected failure";
}

const elements = {};
let plan = null;
let measurementVectorSha256 = null;
let selfTestPassed = false;
const sessionRecords = [];
// The validated value, not the raw field: parseConfig is what accepted it.
let pathNotes = "";

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
  if (
    vectors.kind !== "arena-web-routed-datagram-conformance-vectors" ||
    vectors.formatVersion !== 2
  ) {
    throw new RelayProbeError(
      "the conformance-vector file is not the expected kind and version",
    );
  }
  for (const item of vectors.encodeCases) {
    const payloads = item.payloadHexes.map((value) => hexToBytes(value));
    const frame = encodeFrame(
      hexToBytes(item.prefixHex),
      payloads,
      item.direction,
    );
    if (bytesToHex(frame) !== item.frameHex) {
      throw new RelayProbeError(`encode case ${item.name} produced other bytes`);
    }
    if (frame.length !== item.frameBytes) {
      throw new RelayProbeError(`encode case ${item.name} has the wrong length`);
    }
    const decoded = decodeFrame(frame, item.direction);
    if (decoded.datagrams.length !== payloads.length) {
      throw new RelayProbeError(`decode of ${item.name} lost a datagram`);
    }
    decoded.datagrams.forEach((datagram, index) => {
      if (bytesToHex(datagram) !== item.payloadHexes[index]) {
        throw new RelayProbeError(`decode of ${item.name} changed a payload`);
      }
    });
  }
  // Acceptance exactly at the ceiling. Without these, an implementation using
  // `length >= ceiling` would pass every rejection vector and then refuse the
  // plan's largest size.
  for (const item of vectors.decodeAcceptances) {
    const decoded = decodeFrame(
      hexToBytes(item.frameHex),
      item.direction,
      item.maxInnerDatagramBytes,
    );
    if (decoded.datagrams.length !== item.payloadHexes.length) {
      throw new RelayProbeError(
        `acceptance ${item.name} decoded the wrong datagram count`,
      );
    }
    decoded.datagrams.forEach((datagram, index) => {
      if (bytesToHex(datagram) !== item.payloadHexes[index]) {
        throw new RelayProbeError(`acceptance ${item.name} changed a payload`);
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
      throw new RelayProbeError(`decode rejection ${item.name} was accepted`);
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
      throw new RelayProbeError(`encode rejection ${item.name} was accepted`);
    }
  }
  for (const item of vectors.tagCases) {
    const tag = datagramTag(hexToBytes(item.sessionNonceHex), item.ordinal);
    if (bytesToHex(tag) !== item.tagHex) {
      throw new RelayProbeError(`tag case ${item.name} produced other bytes`);
    }
  }
  for (const item of vectors.payloadCases) {
    const payload = buildPayload(
      hexToBytes(item.sessionNonceHex),
      item.ordinal,
      item.size,
    );
    if (bytesToHex(payload) !== item.payloadHex) {
      throw new RelayProbeError(
        `payload case ${item.name} produced other bytes`,
      );
    }
  }
  checkSessionVectors(vectors);
  return (
    vectors.encodeCases.length +
    vectors.decodeAcceptances.length +
    vectors.decodeRejections.length +
    vectors.encodeRejections.length +
    vectors.tagCases.length +
    vectors.payloadCases.length +
    vectors.addressRequestCases.length +
    vectors.addressAssignmentCases.length +
    vectors.errorCases.length +
    vectors.keepAliveCases.length +
    vectors.sessionRejections.length +
    vectors.headerCases.length +
    vectors.headerRejections.length +
    vectors.returnHeaderAcceptances.length +
    vectors.returnHeaderRejections.length
  );
}

function contextOf(item) {
  return routingContext(
    hexToBytes(item.clientAddressHex),
    item.clientPort,
    hexToBytes(item.destinationAddressHex),
    item.destinationPort,
  );
}

// The 2026-08-30 session and header profile, checked from the same file the
// reference implementation is checked against.
function checkSessionVectors(vectors) {
  for (const item of vectors.addressRequestCases) {
    if (bytesToHex(encodeAddressRequest(item.authorization)) !== item.datagramHex) {
      throw new RelayProbeError(`${item.name} produced other bytes`);
    }
  }
  for (const item of vectors.addressAssignmentCases) {
    const datagram = hexToBytes(item.datagramHex);
    if (bytesToHex(encodeAddressAssignment(hexToBytes(item.addressHex))) !== item.datagramHex) {
      throw new RelayProbeError(`${item.name} produced other bytes`);
    }
    if (bytesToHex(decodeAddressAssignment(datagram)) !== item.addressHex) {
      throw new RelayProbeError(`${item.name} decoded another address`);
    }
  }
  for (const item of vectors.errorCases) {
    if (bytesToHex(encodeError(item.code, item.message)) !== item.datagramHex) {
      throw new RelayProbeError(`${item.name} produced other bytes`);
    }
    const decoded = decodeError(hexToBytes(item.datagramHex));
    if (decoded.code !== item.code || decoded.message !== item.message) {
      throw new RelayProbeError(`${item.name} decoded another error`);
    }
  }
  for (const item of vectors.keepAliveCases) {
    if (bytesToHex(encodeKeepAlive(hexToBytes(item.paddingHex))) !== item.datagramHex) {
      throw new RelayProbeError(`${item.name} produced other bytes`);
    }
  }
  for (const item of vectors.sessionRejections) {
    const decode =
      item.decoder === "error" ? decodeError : decodeAddressAssignment;
    let rejected = false;
    try {
      decode(hexToBytes(item.datagramHex));
    } catch (error) {
      rejected = error instanceof RelaySessionError;
    }
    if (!rejected) {
      throw new RelayProbeError(`session rejection ${item.name} was accepted`);
    }
  }
  for (const item of vectors.headerCases) {
    if (bytesToHex(contextOf(item).outboundHeader()) !== item.headerHex) {
      throw new RelayProbeError(`header case ${item.name} produced other bytes`);
    }
    const decoded = decodeRelayHeader(hexToBytes(item.headerHex));
    if (
      bytesToHex(decoded.destinationAddress) !== item.destinationAddressHex ||
      decoded.destinationPort !== item.destinationPort ||
      bytesToHex(decoded.sourceAddress) !== item.clientAddressHex ||
      decoded.sourcePort !== item.clientPort
    ) {
      throw new RelayProbeError(`header case ${item.name} decoded other fields`);
    }
  }
  for (const item of vectors.headerRejections) {
    let rejected = false;
    try {
      decodeRelayHeader(hexToBytes(item.headerHex));
    } catch (error) {
      rejected = error instanceof RelayFrameError;
    }
    if (!rejected) {
      throw new RelayProbeError(`header rejection ${item.name} was accepted`);
    }
  }
  for (const item of vectors.returnHeaderAcceptances) {
    const header = decodeRelayHeader(hexToBytes(item.returnHeaderHex));
    if (!contextOf(item).acceptsReturn(header)) {
      throw new RelayProbeError(`return header ${item.name} was refused`);
    }
  }
  for (const item of vectors.returnHeaderRejections) {
    const header = decodeRelayHeader(hexToBytes(item.returnHeaderHex));
    if (contextOf(item).acceptsReturn(header)) {
      throw new RelayProbeError(`return header ${item.name} was accepted`);
    }
  }
}

// A whole plan over the in-memory relay, including the in-band setup exchange,
// so the session and the driver are both proven before the network is involved.
function checkDriverAgainstLoopback() {
  const limit = frameBytesForSizes([plan.maxInnerDatagramBytes]);
  const adapter = new LoopbackAdapter(limit);
  const config = parseConfig({
    authorization: SYNTHETIC_AUTHORIZATION,
    destinationAddressHex: bytesToHex(SYNTHETIC_DESTINATION_ADDRESS),
    destinationPort: SYNTHETIC_DESTINATION_PORT,
    endpointUrl: "https://self.test/probe",
  });
  const driver = openLoopbackSession(adapter, plan, config, new Uint8Array(12));
  runLoopbackSession(driver, adapter);
  const record = driver.sessionRecord();
  const unexpected = record.cases.filter(
    (item) => item.outcome !== OUTCOME_ECHOED,
  );
  if (unexpected.length > 0) {
    throw new RelayProbeError(
      `the loopback self-test did not complete case ${unexpected[0].caseIndex}`,
    );
  }
  if (
    record.unmatchedFrames ||
    record.malformedFrames ||
    record.foreignFrames ||
    record.headerMismatchFrames ||
    record.errorDatagrams ||
    record.unexpectedControlDatagrams
  ) {
    throw new RelayProbeError(
      "the loopback self-test produced unattributed frames",
    );
  }
  // The committed vector's 0-byte case has to reach the destination and come
  // back; a relay that dropped it would leave the case unanswered instead.
  if (!adapter.receivedInnerSizes.includes(0)) {
    throw new RelayProbeError(
      "the loopback self-test never carried a zero-length inner datagram",
    );
  }
  return record.cases.length;
}

function readConfig() {
  const hashes = elements.certificateHashes.value
    .split(/\s+/)
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
  return parseConfig({
    assignmentTimeoutMilliseconds: Number(elements.assignmentTimeout.value),
    authorization: elements.authorization.value,
    caseTimeoutMilliseconds: Number(elements.caseTimeout.value),
    certificateHashes: hashes,
    clientSourcePort: Number(elements.clientSourcePort.value),
    destinationAddressHex: elements.destinationAddress.value.trim(),
    destinationPort: Number(elements.destinationPort.value),
    endpointUrl: elements.endpointUrl.value.trim(),
    maxInFlightDatagrams: Number(elements.maxInFlight.value),
    pathNotes: elements.pathNotes.value,
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
  const report = buildReport(sessionRecords, measurementVectorSha256, pathNotes);
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
  // Everything that can be refused is refused before the single-use
  // authorization is spent. The driver's own construction can reject a
  // configuration the form permits — a bound below the widest packed case, for
  // one — and doing that after connecting would burn an allowance and leave a
  // session open. A placeholder transport and a placeholder client address
  // carry the plan checks; the real adapter and the assigned address replace
  // them once the session exists.
  new SessionDriver(
    plan,
    { maxDatagramSizeBytes: 1, send: () => {} },
    nonce,
    config,
    routingContext(
      SYNTHETIC_CLIENT_ADDRESS,
      config.clientSourcePort,
      config.destinationAddress,
      config.destinationPort,
    ),
    sessionIndex,
  );
  log(`session ${sessionIndex}: connecting`);
  const adapter = await WebTransportAdapter.connect(config);
  try {
    // One REQUEST_ADDRESS, then the assignment. The authorization is spent
    // here and the assigned address is never logged or reported: it is the
    // session's virtual client address, which is environment detail.
    const handshake = await establishSession(adapter, config);
    log(`session ${sessionIndex}: address assigned`);
    await measureOneSession(
      adapter,
      config,
      nonce,
      sessionIndex,
      handshake.routingContext(),
    );
  } finally {
    // Whatever happened, the session does not outlive this call.
    await adapter.close();
  }
}

async function measureOneSession(adapter, config, nonce, sessionIndex, routing) {
  log(`session ${sessionIndex}: maxDatagramSize ${adapter.maxDatagramSizeBytes}`);
  const driver = new SessionDriver(
    plan,
    adapter,
    nonce,
    config,
    routing,
    sessionIndex,
  );
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
      `session ${sessionIndex}: time budget exceeded; unfinished cases are ` +
        "recorded as timed out where they were sent, and as never run where " +
        "the run did not reach them",
    );
  }
  // Stop before closing: pumping a closed session would start cases that can
  // never be answered.
  stopped = true;
  // The read loop is parked in `reader.read()`, which settles on a datagram or
  // on the session closing — nothing else. Setting `stopped` does not wake it,
  // so the close has to happen HERE, before the await below, or this function
  // never returns. The caller's finally is the exception-path safety net, not a
  // substitute; `close()` tolerates being called twice.
  await adapter.close();
  await reading;
  const record = driver.sessionRecord();
  pathNotes = config.pathNotes;
  sessionRecords.push(record);
  log(`session ${sessionIndex}: ${describe(record)}`);
  log(
    `session ${sessionIndex}: foreign ${record.foreignFrames}, ` +
      `malformed ${record.malformedFrames}, ` +
      `header mismatch ${record.headerMismatchFrames}, ` +
      `unattributed ${record.unmatchedFrames}`,
  );
  log(
    `session ${sessionIndex}: relay errors ${record.errorDatagrams}, ` +
      `keep-alives ${record.keepAliveDatagrams}, ` +
      `unexpected control ${record.unexpectedControlDatagrams}`,
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
      throw new RelayProbeError("the self-test has not passed");
    }
    await runOneSession();
  } catch (error) {
    log(`refused: ${describeError(error)}`);
  } finally {
    // A single-use allowance is spent by the attempt, not by its success: a
    // session refused in band consumed the value just as a completed one did.
    // Clearing it here rather than on the success path is what keeps a retry
    // from silently reusing a spent authorization.
    elements.authorization.value = "";
    log("authorization field cleared; paste a fresh one for the next session");
    elements.run.disabled = false;
  }
}

async function start() {
  for (const id of [
    "assignmentTimeout",
    "authorization",
    "caseTimeout",
    "certificateHashes",
    "clientSourcePort",
    "destinationAddress",
    "destinationPort",
    "download",
    "endpointUrl",
    "log",
    "maxInFlight",
    "pathNotes",
    "report",
    "run",
    "summary",
  ]) {
    elements[id] = document.getElementById(id);
  }
  elements.run.addEventListener("click", onRun);
  try {
    const vectorResponse = await fetch("../locks/relay-measurement-vector.json");
    if (!vectorResponse.ok) {
      throw new RelayProbeError("the measurement vector could not be read");
    }
    const vectorBytes = await vectorResponse.arrayBuffer();
    measurementVectorSha256 = await sha256Hex(vectorBytes);
    plan = buildPlan(JSON.parse(new TextDecoder().decode(vectorBytes)));
    log(
      `measurement vector sha256:${measurementVectorSha256} — ` +
        `${plan.cases.length} cases, ceiling ${plan.maxInnerDatagramBytes} bytes`,
    );
    const conformanceResponse = await fetch("./conformance-vectors.json");
    if (!conformanceResponse.ok) {
      throw new RelayProbeError("the conformance vectors could not be read");
    }
    const conformance = await conformanceResponse.json();
    const checked = checkConformanceVectors(conformance);
    const driven = checkDriverAgainstLoopback();
    selfTestPassed = true;
    log(`self-test passed: ${checked} conformance vectors, ${driven} loopback cases`);
    elements.run.disabled = false;
  } catch (error) {
    log(`self-test failed: ${describeError(error)}`);
    log("the probe will not open a session until this passes");
  }
}

start();
