// SPDX-License-Identifier: GPL-2.0-or-later
//
// The two transports the session driver can run over.
//
// `LoopbackAdapter` is an in-memory relay plus echo destination. It exists so
// the probe can prove its own framing and driver before any network is touched.
// `WebTransportAdapter` is the real one. Both expose the same two things the
// driver uses: `maxDatagramSizeBytes` and `send(frame)`.

import {
  BROWSER_TO_SERVER,
  MAX_LENGTH_PREFIX_VALUE,
  SERVER_TO_BROWSER,
  RelayProbeError,
  decodeFrame,
  encodeFrame,
  hexToBytes,
} from "./relay-framing.js";
import { AdapterSendError } from "./measurement.js";

// The same fault names scripts/relay_loopback.py uses, so a fault run can be
// compared across the two implementations.
export const FAULT_NONE = "";
export const FAULT_TRUNCATED_RETURN = "truncatedReturn";
export const FAULT_PACKED_RETURN = "packedReturn";
export const FAULT_CORRUPT_PAYLOAD = "corruptPayload";
export const FAULT_FOREIGN_PREFIX = "foreignPrefix";
export const FAULT_HEADER_ONLY_RETURN = "headerOnlyReturn";
export const FAULT_DECLARED_OVERSIZE = "declaredOversize";

export class LoopbackAdapter {
  constructor(maxDatagramSizeBytes, returnPrefix, options = {}) {
    this.maxDatagramSizeBytes = maxDatagramSizeBytes;
    this.returnPrefix = returnPrefix;
    this.echo = options.echo === undefined ? true : options.echo;
    this.fault = options.fault || FAULT_NONE;
    this.dropInnerSizes = new Set(options.dropInnerSizes || []);
    this.refuseSend = options.refuseSend === true;
    this.inbox = [];
    this.receivedDatagrams = 0;
    this.receivedFrames = 0;
    this.undeliverableReturns = 0;
    this.writeFailures = 0;
  }

  send(frame) {
    if (frame.length > this.maxDatagramSizeBytes) {
      this.writeFailures += 1;
      throw new AdapterSendError("frame exceeds the transport maximum");
    }
    if (this.refuseSend) {
      this.writeFailures += 1;
      throw new AdapterSendError("the transport refused the frame");
    }
    const decoded = decodeFrame(frame, BROWSER_TO_SERVER, MAX_LENGTH_PREFIX_VALUE);
    this.receivedFrames += 1;
    this.receivedDatagrams += decoded.datagrams.length;
    if (!this.echo) {
      return;
    }
    for (const payload of decoded.datagrams) {
      if (this.dropInnerSizes.has(payload.length)) {
        continue;
      }
      for (const returned of this.returnFrames(payload)) {
        if (returned.length > this.maxDatagramSizeBytes) {
          this.undeliverableReturns += 1;
          continue;
        }
        this.inbox.push(returned);
      }
    }
  }

  returnFrames(payload) {
    let prefix = this.returnPrefix;
    if (this.fault === FAULT_FOREIGN_PREFIX) {
      prefix = this.returnPrefix.map((value) => value ^ 0xff);
    }
    if (this.fault === FAULT_CORRUPT_PAYLOAD && payload.length > 0) {
      const copy = payload.slice();
      copy[copy.length - 1] ^= 0xff;
      payload = copy;
    }
    if (this.fault === FAULT_HEADER_ONLY_RETURN) {
      return [prefix.slice()];
    }
    if (this.fault === FAULT_PACKED_RETURN) {
      return [encodeFrame(prefix, [payload, payload], BROWSER_TO_SERVER)];
    }
    if (this.fault === FAULT_DECLARED_OVERSIZE) {
      const frame = new Uint8Array(prefix.length + 2 + payload.length);
      frame.set(prefix, 0);
      frame[prefix.length] = 0xff;
      frame[prefix.length + 1] = 0xff;
      frame.set(payload, prefix.length + 2);
      return [frame];
    }
    const frame = encodeFrame(prefix, [payload], SERVER_TO_BROWSER);
    if (this.fault === FAULT_TRUNCATED_RETURN) {
      return [frame.subarray(0, frame.length - 1)];
    }
    return [frame];
  }

  drain() {
    const frames = this.inbox;
    this.inbox = [];
    return frames;
  }
}

// Drives one session to completion on a virtual clock, with no timers and no
// network. Used by the startup self-test.
export function runLoopbackSession(driver, adapter, stepMs = 1, maxSteps = 100000) {
  let now = 0;
  driver.pump(now);
  for (let step = 0; step < maxSteps; step += 1) {
    if (driver.finished) {
      return now;
    }
    for (const frame of adapter.drain()) {
      driver.receive(frame, now);
    }
    now += stepMs;
    driver.pump(now);
  }
  throw new RelayProbeError(
    "loopback session did not finish within the step budget",
  );
}

export class WebTransportAdapter {
  constructor(transport, writer, maxDatagramSizeBytes) {
    this.transport = transport;
    this.writer = writer;
    this.maxDatagramSizeBytes = maxDatagramSizeBytes;
    this.writeFailed = false;
    this.writeFailures = 0;
  }

  // The URL is composed here and nowhere else, so the authorization exists only
  // for the duration of this call and is never stored, logged or reported.
  //
  // Construction and readiness are wrapped because platform errors quote the
  // URL they failed on: Chromium's TypeError for an invalid URL, or for a URL
  // carrying a fragment, embeds the whole thing. That message must never escape
  // this function, because the URL contains the authorization. Only the error's
  // class name is carried out; the message is dropped on the floor here.
  static async connect(config) {
    if (typeof WebTransport === "undefined") {
      throw new RelayProbeError("this browser has no WebTransport");
    }
    const options = {};
    if (config.certificateHashes.length > 0) {
      options.serverCertificateHashes = config.certificateHashes.map((value) => ({
        algorithm: "sha-256",
        value: hexToBytes(value),
      }));
    }
    let transport;
    try {
      transport = new WebTransport(config.endpointUrl(), options);
      await transport.ready;
    } catch (error) {
      const name = error && error.name ? ` (${error.name})` : "";
      throw new RelayProbeError(
        `the transport refused to open a session${name}`,
      );
    }
    const maxDatagramSizeBytes = transport.datagrams.maxDatagramSize;
    if (!Number.isInteger(maxDatagramSizeBytes) || maxDatagramSizeBytes <= 0) {
      try {
        transport.close();
      } catch (error) {
        // Already gone; nothing to close.
      }
      throw new RelayProbeError(
        "the transport did not report a usable maxDatagramSize; refusing to " +
          "guess one",
      );
    }
    const writer = transport.datagrams.writable.getWriter();
    return new WebTransportAdapter(transport, writer, maxDatagramSizeBytes);
  }

  // Datagram writes resolve once the datagram is queued, so a rejection is
  // observed after the fact. A rejected write marks the adapter failed and every
  // later send is refused; the case that caused it is recorded as a timeout
  // rather than a send failure. The size check above catches the failure mode
  // this probe is actually measuring.
  send(frame) {
    if (this.writeFailed) {
      throw new AdapterSendError("an earlier datagram write failed");
    }
    this.writer.write(frame).catch(() => {
      this.writeFailed = true;
      this.writeFailures += 1;
    });
  }

  async readFrames(onFrame, shouldStop) {
    const reader = this.transport.datagrams.readable.getReader();
    try {
      for (;;) {
        if (shouldStop()) {
          return;
        }
        const { value, done } = await reader.read();
        if (done) {
          return;
        }
        onFrame(value);
      }
    } finally {
      try {
        reader.releaseLock();
      } catch (error) {
        // The session is already gone; nothing to release.
      }
    }
  }

  async close() {
    try {
      await this.writer.close();
    } catch (error) {
      // Closing an already-failed writer is not an error worth reporting.
    }
    try {
      this.transport.close();
    } catch (error) {
      // Same for an already-closed session.
    }
  }
}
