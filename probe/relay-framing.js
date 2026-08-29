// SPDX-License-Identifier: GPL-2.0-or-later
//
// The frame grammar and payload tag of docs/relay-datagram-contract.md.
//
// This is the browser-side implementation of the same rules that
// scripts/relay_probe.py implements for the deterministic tests. Neither is
// derived from the other at runtime, so probe.js checks this file against the
// committed conformance vectors before the probe is allowed to open a session.
//
// Nothing here touches the network. It turns byte arrays into byte arrays.

export const RELAY_HEADER_BYTES = 40;
export const LENGTH_PREFIX_BYTES = 2;
export const SINGLE_DATAGRAM_OVERHEAD_BYTES = 42;

export const NONCE_BYTES = 16;
export const MINIMUM_TAGGED_INNER_BYTES = 16;
export const SESSION_NONCE_BYTES = 12;
export const ORDINAL_BYTES = 4;

export const MAX_LENGTH_PREFIX_VALUE = 65535;

export const BROWSER_TO_SERVER = "browserToServer";
export const SERVER_TO_BROWSER = "serverToBrowser";
export const DIRECTIONS = [BROWSER_TO_SERVER, SERVER_TO_BROWSER];

export class RelayFrameError extends Error {}
export class RelayProbeError extends Error {}

export function hexToBytes(text) {
  if (typeof text !== "string" || text.length % 2 !== 0) {
    throw new RelayProbeError("hexadecimal input must have an even length");
  }
  if (text.length > 0 && !/^[0-9a-fA-F]+$/.test(text)) {
    throw new RelayProbeError("input is not hexadecimal");
  }
  const bytes = new Uint8Array(text.length / 2);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = parseInt(text.substr(index * 2, 2), 16);
  }
  return bytes;
}

export function bytesToHex(bytes) {
  let text = "";
  for (let index = 0; index < bytes.length; index += 1) {
    text += bytes[index].toString(16).padStart(2, "0");
  }
  return text;
}

export function bytesEqual(left, right) {
  if (left.length !== right.length) {
    return false;
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false;
    }
  }
  return true;
}

export function frameBytesForSizes(sizes) {
  let total = RELAY_HEADER_BYTES;
  for (const size of sizes) {
    total += LENGTH_PREFIX_BYTES + size;
  }
  return total;
}

function checkDirection(direction) {
  if (!DIRECTIONS.includes(direction)) {
    throw new RelayFrameError(`unknown direction ${direction}`);
  }
}

export function encodeFrame(
  prefix,
  datagrams,
  direction,
  maxInnerDatagramBytes = MAX_LENGTH_PREFIX_VALUE,
) {
  checkDirection(direction);
  if (prefix.length !== RELAY_HEADER_BYTES) {
    throw new RelayFrameError(
      `routing prefix is ${prefix.length} bytes, not ${RELAY_HEADER_BYTES}`,
    );
  }
  if (direction === SERVER_TO_BROWSER && datagrams.length !== 1) {
    throw new RelayFrameError(
      "a server-to-browser frame carries exactly one inner datagram",
    );
  }
  if (direction === BROWSER_TO_SERVER && datagrams.length === 0) {
    throw new RelayFrameError(
      "a browser-to-server frame carries at least one inner datagram",
    );
  }
  const ceiling = Math.min(maxInnerDatagramBytes, MAX_LENGTH_PREFIX_VALUE);
  let total = RELAY_HEADER_BYTES;
  for (const datagram of datagrams) {
    if (datagram.length > ceiling) {
      throw new RelayFrameError(
        `inner datagram of ${datagram.length} bytes exceeds the ${ceiling} byte ceiling`,
      );
    }
    total += LENGTH_PREFIX_BYTES + datagram.length;
  }
  const frame = new Uint8Array(total);
  frame.set(prefix, 0);
  let offset = RELAY_HEADER_BYTES;
  for (const datagram of datagrams) {
    frame[offset] = (datagram.length >> 8) & 0xff;
    frame[offset + 1] = datagram.length & 0xff;
    offset += LENGTH_PREFIX_BYTES;
    frame.set(datagram, offset);
    offset += datagram.length;
  }
  return frame;
}

// Every length is checked against the bytes present and against the ceiling
// before any view is taken, so a frame declaring 65,535 bytes in a 44-byte
// datagram costs two comparisons rather than a 64 KiB allocation. The returned
// datagrams are views into the frame; nothing is copied.
export function decodeFrame(
  frame,
  direction,
  maxInnerDatagramBytes = MAX_LENGTH_PREFIX_VALUE,
) {
  checkDirection(direction);
  const total = frame.length;
  if (total < RELAY_HEADER_BYTES) {
    throw new RelayFrameError(
      `frame of ${total} bytes is shorter than the ${RELAY_HEADER_BYTES} byte routing prefix`,
    );
  }
  const ceiling = Math.min(maxInnerDatagramBytes, MAX_LENGTH_PREFIX_VALUE);
  const spans = [];
  let offset = RELAY_HEADER_BYTES;
  while (offset < total) {
    if (total - offset < LENGTH_PREFIX_BYTES) {
      throw new RelayFrameError("frame ends inside a length prefix");
    }
    const length = (frame[offset] << 8) | frame[offset + 1];
    offset += LENGTH_PREFIX_BYTES;
    if (length > ceiling) {
      throw new RelayFrameError(
        `inner datagram of ${length} bytes exceeds the ${ceiling} byte ceiling`,
      );
    }
    if (total - offset < length) {
      throw new RelayFrameError("frame ends inside an inner datagram");
    }
    spans.push([offset, length]);
    offset += length;
  }
  if (direction === SERVER_TO_BROWSER && spans.length !== 1) {
    throw new RelayFrameError(
      "a server-to-browser frame carries exactly one inner datagram",
    );
  }
  if (direction === BROWSER_TO_SERVER && spans.length === 0) {
    throw new RelayFrameError(
      "a browser-to-server frame carries at least one inner datagram",
    );
  }
  return {
    prefix: frame.subarray(0, RELAY_HEADER_BYTES),
    datagrams: spans.map(([start, length]) =>
      frame.subarray(start, start + length),
    ),
  };
}

export function datagramTag(sessionNonce, ordinal) {
  if (sessionNonce.length !== SESSION_NONCE_BYTES) {
    throw new RelayProbeError(
      `session nonce is ${sessionNonce.length} bytes, not ${SESSION_NONCE_BYTES}`,
    );
  }
  if (!Number.isInteger(ordinal) || ordinal < 0 || ordinal > 0xffffffff) {
    throw new RelayProbeError(`datagram ordinal ${ordinal} is out of range`);
  }
  const tag = new Uint8Array(NONCE_BYTES);
  tag.set(sessionNonce, 0);
  tag[SESSION_NONCE_BYTES] = (ordinal >>> 24) & 0xff;
  tag[SESSION_NONCE_BYTES + 1] = (ordinal >>> 16) & 0xff;
  tag[SESSION_NONCE_BYTES + 2] = (ordinal >>> 8) & 0xff;
  tag[SESSION_NONCE_BYTES + 3] = ordinal & 0xff;
  return tag;
}

export function buildPayload(sessionNonce, ordinal, size) {
  if (!Number.isInteger(size) || size < 0 || size > MAX_LENGTH_PREFIX_VALUE) {
    throw new RelayProbeError(`payload size ${size} is out of range`);
  }
  const payload = new Uint8Array(size);
  for (let index = 0; index < size; index += 1) {
    payload[index] = index & 0xff;
  }
  if (size >= MINIMUM_TAGGED_INNER_BYTES) {
    payload.set(datagramTag(sessionNonce, ordinal), 0);
  }
  return payload;
}

export function readTag(payload) {
  if (payload.length < MINIMUM_TAGGED_INNER_BYTES) {
    return null;
  }
  const ordinal =
    ((payload[SESSION_NONCE_BYTES] << 24) |
      (payload[SESSION_NONCE_BYTES + 1] << 16) |
      (payload[SESSION_NONCE_BYTES + 2] << 8) |
      payload[SESSION_NONCE_BYTES + 3]) >>>
    0;
  return { nonce: payload.subarray(0, SESSION_NONCE_BYTES), ordinal };
}
