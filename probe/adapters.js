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
  decodeFrame,
  encodeFrame,
  hexToBytes,
} from "./relay-framing.js";
import { AdapterSendError } from "./measurement.js";

export class LoopbackAdapter {
  constructor(maxDatagramSizeBytes, returnPrefix) {
    this.maxDatagramSizeBytes = maxDatagramSizeBytes;
    this.returnPrefix = returnPrefix;
    this.inbox = [];
    this.receivedDatagrams = 0;
  }

  send(frame) {
    if (frame.length > this.maxDatagramSizeBytes) {
      throw new AdapterSendError("frame exceeds the transport maximum");
    }
    const decoded = decodeFrame(frame, BROWSER_TO_SERVER, MAX_LENGTH_PREFIX_VALUE);
    this.receivedDatagrams += decoded.datagrams.length;
    for (const payload of decoded.datagrams) {
      const returned = encodeFrame(
        this.returnPrefix,
        [payload],
        SERVER_TO_BROWSER,
      );
      if (returned.length <= this.maxDatagramSizeBytes) {
        this.inbox.push(returned);
      }
    }
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
  throw new Error("loopback session did not finish within the step budget");
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
  static async connect(config) {
    if (typeof WebTransport === "undefined") {
      throw new Error("this browser has no WebTransport");
    }
    const options = {};
    if (config.certificateHashes.length > 0) {
      options.serverCertificateHashes = config.certificateHashes.map((value) => ({
        algorithm: "sha-256",
        value: hexToBytes(value),
      }));
    }
    const transport = new WebTransport(config.endpointUrl(), options);
    await transport.ready;
    const maxDatagramSizeBytes = transport.datagrams.maxDatagramSize;
    if (!Number.isInteger(maxDatagramSizeBytes) || maxDatagramSizeBytes <= 0) {
      await transport.close();
      throw new Error(
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
