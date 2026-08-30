// SPDX-License-Identifier: GPL-2.0-or-later
//
// The datagram types, the relay header, the frame grammar and the payload tag
// of docs/relay-datagram-contract.md.
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

// Every datagram of this profile opens with a 4-byte big-endian type. The
// 2026-08-30 amendment replaced the URL-carried authorization and the opaque
// routing prefix with this in-band session profile.
export const DATAGRAM_TYPE_BYTES = 4;
export const TYPE_REQUEST_ADDRESS = 0x00000001;
export const TYPE_ADDRESS_ASSIGNED = 0x00000002;
export const TYPE_RELAY_PACKET = 0x00000003;
export const TYPE_ERROR = 0x00000004;
export const TYPE_KEEP_ALIVE = 0x00000005;

export const VIRTUAL_ADDRESS_BYTES = 16;
export const PORT_BYTES = 2;
export const ADDRESS_ASSIGNED_BYTES = DATAGRAM_TYPE_BYTES + VIRTUAL_ADDRESS_BYTES;
export const ERROR_CODE_BYTES = 4;
export const ERROR_MESSAGE_LENGTH_BYTES = 2;
export const ERROR_PREAMBLE_BYTES =
  DATAGRAM_TYPE_BYTES + ERROR_CODE_BYTES + ERROR_MESSAGE_LENGTH_BYTES;

export const ERROR_INVALID_AUTHORIZATION = 0x00000002;
export const ERROR_DESTINATION_UNAVAILABLE = 0x00000003;

export const MAX_PORT = 65535;
// Neutral and synthetic: the relay stores this value and echoes it back as the
// return header's destination port, and nothing else depends on it.
export const DEFAULT_CLIENT_SOURCE_PORT = 49152;

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
export class RelaySessionError extends RelayProbeError {}

// Only the error code travels in the message. The authorization, the endpoint
// and the relay's own error text must never reach a log or a report.
export class RelayRefusedError extends RelaySessionError {
  constructor(code) {
    super(
      `the relay refused the session with error code 0x${(code >>> 0)
        .toString(16)
        .padStart(8, "0")}`,
    );
    this.code = code;
  }
}

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

function readUint32(bytes, offset) {
  return (
    ((bytes[offset] << 24) |
      (bytes[offset + 1] << 16) |
      (bytes[offset + 2] << 8) |
      bytes[offset + 3]) >>>
    0
  );
}

function writeUint32(bytes, offset, value) {
  bytes[offset] = (value >>> 24) & 0xff;
  bytes[offset + 1] = (value >>> 16) & 0xff;
  bytes[offset + 2] = (value >>> 8) & 0xff;
  bytes[offset + 3] = value & 0xff;
}

function checkAddress(address, name) {
  if (!address || address.length !== VIRTUAL_ADDRESS_BYTES) {
    throw new RelayFrameError(
      `${name} is ${address ? address.length : 0} bytes, not ${VIRTUAL_ADDRESS_BYTES}`,
    );
  }
  return address;
}

function checkPort(port, name) {
  if (!Number.isInteger(port) || port < 0 || port > MAX_PORT) {
    throw new RelayFrameError(`${name} ${port} is out of range`);
  }
  return port;
}

// The 40 header bytes of a RELAY_PACKET: type, destination address and port,
// source address and port. The relay always overwrites the source address with
// the sender's assigned one.
export function encodeRelayHeader(header) {
  const bytes = new Uint8Array(RELAY_HEADER_BYTES);
  writeUint32(bytes, 0, TYPE_RELAY_PACKET);
  bytes.set(checkAddress(header.destinationAddress, "destination address"), 4);
  const destinationPort = checkPort(header.destinationPort, "destination port");
  bytes[20] = (destinationPort >> 8) & 0xff;
  bytes[21] = destinationPort & 0xff;
  bytes.set(checkAddress(header.sourceAddress, "source address"), 22);
  const sourcePort = checkPort(header.sourcePort, "source port");
  bytes[38] = (sourcePort >> 8) & 0xff;
  bytes[39] = sourcePort & 0xff;
  return bytes;
}

export function decodeRelayHeader(header) {
  if (header.length !== RELAY_HEADER_BYTES) {
    throw new RelayFrameError(
      `a relay header is ${RELAY_HEADER_BYTES} bytes, not ${header.length}`,
    );
  }
  const kind = readUint32(header, 0);
  if (kind !== TYPE_RELAY_PACKET) {
    throw new RelayFrameError("datagram type is not a relay packet");
  }
  return {
    destinationAddress: header.slice(4, 20),
    destinationPort: (header[20] << 8) | header[21],
    sourceAddress: header.slice(22, 38),
    sourcePort: (header[38] << 8) | header[39],
  };
}

// Everything a session needs in order to address one game destination. The
// client address is not configuration: it is what the relay assigned in the
// session's first exchange.
export function routingContext(
  clientAddress,
  clientPort,
  destinationAddress,
  destinationPort,
) {
  return {
    clientAddress,
    clientPort,
    destinationAddress,
    destinationPort,
    outboundHeader() {
      return encodeRelayHeader({
        destinationAddress,
        destinationPort,
        sourceAddress: clientAddress,
        sourcePort: clientPort,
      });
    },
    // The return header's source port is the destination's own reply port,
    // which a client cannot predict from the virtual port it addressed, so it
    // is reported rather than checked. The other three fields are known.
    acceptsReturn(header) {
      return (
        bytesEqual(header.destinationAddress, clientAddress) &&
        header.destinationPort === clientPort &&
        bytesEqual(header.sourceAddress, destinationAddress)
      );
    },
  };
}

export function datagramType(datagram) {
  if (datagram.length < DATAGRAM_TYPE_BYTES) {
    throw new RelayFrameError(
      `datagram of ${datagram.length} bytes is shorter than its ${DATAGRAM_TYPE_BYTES} byte type`,
    );
  }
  return readUint32(datagram, 0);
}

// The authorization is opaque runtime input, placed in the datagram verbatim as
// UTF-8. This repository does not parse it and never stores or reports it.
export function encodeAddressRequest(authorization) {
  if (typeof authorization !== "string" || authorization.length === 0) {
    throw new RelaySessionError(
      "an address request carries a non-empty authorization",
    );
  }
  const text = new TextEncoder().encode(authorization);
  const datagram = new Uint8Array(DATAGRAM_TYPE_BYTES + text.length);
  writeUint32(datagram, 0, TYPE_REQUEST_ADDRESS);
  datagram.set(text, DATAGRAM_TYPE_BYTES);
  return datagram;
}

export function encodeAddressAssignment(address) {
  const datagram = new Uint8Array(ADDRESS_ASSIGNED_BYTES);
  writeUint32(datagram, 0, TYPE_ADDRESS_ASSIGNED);
  datagram.set(checkAddress(address, "assigned address"), DATAGRAM_TYPE_BYTES);
  return datagram;
}

export function decodeAddressAssignment(datagram) {
  if (datagramType(datagram) !== TYPE_ADDRESS_ASSIGNED) {
    throw new RelaySessionError("datagram is not an address assignment");
  }
  if (datagram.length !== ADDRESS_ASSIGNED_BYTES) {
    throw new RelaySessionError(
      `an address assignment is ${ADDRESS_ASSIGNED_BYTES} bytes, not ${datagram.length}`,
    );
  }
  const address = datagram.slice(DATAGRAM_TYPE_BYTES);
  if (address.every((value) => value === 0)) {
    throw new RelaySessionError("the relay assigned the unspecified address");
  }
  return address;
}

export function encodeError(code, message) {
  const text = new TextEncoder().encode(message);
  if (text.length > MAX_LENGTH_PREFIX_VALUE) {
    throw new RelaySessionError("error message is too long to describe");
  }
  const datagram = new Uint8Array(ERROR_PREAMBLE_BYTES + text.length);
  writeUint32(datagram, 0, TYPE_ERROR);
  writeUint32(datagram, DATAGRAM_TYPE_BYTES, code);
  datagram[DATAGRAM_TYPE_BYTES + ERROR_CODE_BYTES] = (text.length >> 8) & 0xff;
  datagram[DATAGRAM_TYPE_BYTES + ERROR_CODE_BYTES + 1] = text.length & 0xff;
  datagram.set(text, ERROR_PREAMBLE_BYTES);
  return datagram;
}

// The message is decoded so the grammar can be checked exactly, and is then the
// caller's problem: a conforming client never logs or reports it, because it is
// text the relay chose and may describe its environment.
export function decodeError(datagram) {
  if (datagramType(datagram) !== TYPE_ERROR) {
    throw new RelaySessionError("datagram is not an error");
  }
  if (datagram.length < ERROR_PREAMBLE_BYTES) {
    throw new RelaySessionError("error datagram is shorter than its own preamble");
  }
  const code = readUint32(datagram, DATAGRAM_TYPE_BYTES);
  const length =
    (datagram[DATAGRAM_TYPE_BYTES + ERROR_CODE_BYTES] << 8) |
    datagram[DATAGRAM_TYPE_BYTES + ERROR_CODE_BYTES + 1];
  if (datagram.length - ERROR_PREAMBLE_BYTES !== length) {
    throw new RelaySessionError(
      "the error message length does not describe the remaining bytes",
    );
  }
  let message;
  try {
    message = new TextDecoder("utf-8", { fatal: true }).decode(
      datagram.subarray(ERROR_PREAMBLE_BYTES),
    );
  } catch (error) {
    throw new RelaySessionError("the error message is not valid UTF-8");
  }
  return { code, message };
}

export function encodeKeepAlive(padding = new Uint8Array(0)) {
  const datagram = new Uint8Array(DATAGRAM_TYPE_BYTES + padding.length);
  writeUint32(datagram, 0, TYPE_KEEP_ALIVE);
  datagram.set(padding, DATAGRAM_TYPE_BYTES);
  return datagram;
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
