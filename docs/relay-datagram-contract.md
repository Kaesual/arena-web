<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Routed datagram contract: the game-destination subset

**Status:** WP2 deterministic part, amended 2026-08-30 to the in-band session
profile. Normative for this repository's conformance probe and its tests; routed
acceptance against a real endpoint is pending.

This document specifies the part of a WebTransport-to-UDP relay protocol that a
browser client needs in order to exchange datagrams with **one** game
destination: the datagram types, how a session is authorized and given its
virtual client address, how the single pinned virtual destination is addressed,
how an idle session is kept alive, and — byte for byte — how datagrams are framed
in each direction.

It is written from this repository's own approved work-package contract, from the
committed measurement vector
[`locks/relay-measurement-vector.json`](../locks/relay-measurement-vector.json),
and — since the 2026-08-30 amendment — from the session and header profile a
client must implement to interoperate with a relay of this kind. It is a
client-facing wire specification, not a transcript of any relay implementation,
and this repository contains no relay. Where a value is environment-specific it
is runtime configuration supplied by the integration environment, never a
committed constant.

## What this document fixes, and what it does not

Fixed here, and enforced by
[`scripts/relay_probe.py`](../scripts/relay_probe.py) and its tests:

- the datagram types and the session-setup exchange, including how an invalid
  authorization is refused;
- the interior of the 40-byte relay header, and which of its fields a client
  constructs, which it validates on the return path, and which it does not;
- the frame grammar in both directions, including every rejection rule;
- the direction asymmetry and the resulting overhead arithmetic;
- the interior of the 16-byte payload tag, its placement, and the sequencing
  rule for payloads too small to carry it;
- the receiver's fail-closed obligations, including the ban on allocating from
  an untrusted length field;
- the shape and validation rules of the measurement report.

Not fixed here, and therefore supplied at runtime:

- the **authorization value and how it is issued**. To a client of this subset
  it is an opaque, short-lived, single-use string; nothing here parses it, and
  its issuance policy is out of scope for this repository;
- whether an idle session requires a keep-alive at all, and at what interval.
  The datagram is specified below; sending one periodically is not implemented
  here;
- the endpoint, its trust input, the virtual destination address and its UDP
  port. The virtual client address is not configuration at all: the relay
  assigns it during session setup.

These gaps are deliberate. They are recorded again, with what the routed round
must supply, in [`wp2-relay-probe.md`](wp2-relay-probe.md).

## Datagram types

Every datagram of this subset — in either direction — opens with a 4-byte
big-endian type. There is nothing above the datagram: no stream traffic, no
framing layer, no continuation.

| Type | Name | Direction | Payload |
| --- | --- | --- | --- |
| `0x00000001` | `REQUEST_ADDRESS` | client → relay | the authorization, as UTF-8 |
| `0x00000002` | `ADDRESS_ASSIGNED` | relay → client | the assigned 16-byte virtual client address |
| `0x00000003` | `RELAY_PACKET` | both | 36 further header bytes — two addresses and two ports — then the framed payload |
| `0x00000004` | `ERROR` | relay → client | a 4-byte code, a `u16` message length, the UTF-8 message |
| `0x00000005` | `KEEP_ALIVE` | both | arbitrary padding, carrying no meaning |

A datagram shorter than its 4-byte type is malformed. So is a datagram whose
type is not one of the five above.

`ADDRESS_ASSIGNED` is therefore exactly 20 bytes, and an `ERROR` is at least 10.

Two error codes concern a client of this subset:

| Code | Meaning |
| --- | --- |
| `0x00000002` | the authorization was invalid or refused. **Terminal**: the relay closes the session after sending it. |
| `0x00000003` | the destination is not available. An unknown destination and one the session is not authorized for are answered **identically**, so the error cannot be used to enumerate destinations. |

A client never surfaces the message text of an `ERROR`: it is text the relay
chose and may describe its environment. The code is enough to act on, and the
code is all that may reach a log or a report.

## Session establishment and authorization

A client opens exactly one WebTransport session to the relay endpoint and uses
only its unreliable datagram channel.

**The endpoint URL is a plain `https` WebTransport URL.** It carries no
authorization, no placeholder and no fragment. The browser platform offers no
request-header control over a `WebTransport` construction, so a browser-presented
authorization can only travel inside the URL or inside the session's own first
message; **this profile uses the first message**, which keeps the authorization
out of the URL, out of any platform error that quotes the URL, and out of
whatever the browser does with a URL it failed to open.

The exchange is:

1. the client sends one `REQUEST_ADDRESS` carrying the authorization verbatim as
   UTF-8;
2. the relay answers with `ADDRESS_ASSIGNED` naming the session's 16-byte
   virtual client address, **or** with `ERROR` code `0x00000002`, after which it
   closes the session.

Until the assignment arrives the session is not usable: a `RELAY_PACKET` sent
before it is dropped without an answer. A client therefore requires the
assignment before it sends anything else, and treats an assignment of the
unspecified address (all sixteen bytes zero) as a protocol violation rather than
as an address.

A `KEEP_ALIVE` that arrives during the exchange is ignored and the client keeps
waiting. Any other datagram type ends the attempt.

An authorization is short-lived and **single-use**. A conforming client obtains
one per session, sends it exactly once, never persists it, never writes it into
a log or a report, and never reuses one across sessions — including after a
failed attempt, because a refused attempt spent the value just as a successful
one did. The single-use rule is best enforced structurally: hold the only copy,
drop it the moment the request datagram is built, and refuse to build a second.

Trust is either public Web PKI or WebTransport's `serverCertificateHashes` with
SHA-256, as recorded by
[`immutable-baseline.md`](immutable-baseline.md). The hash is runtime
configuration. This subset never requires a machine-wide trust-store change.

## Addressing

A session has exactly one virtual client address — the one the relay assigned —
and addresses exactly one pinned virtual destination, whose address and UDP port
are runtime configuration. Every `RELAY_PACKET` of the session therefore carries
the same 40-byte header, and that header is **not opaque**: a client constructs
it and validates the one it receives.

```text
+-------------------+
| type: 0x00000003  |  4 bytes, big-endian u32
+-------------------+
| destination IPv6  | 16 bytes
+-------------------+
| destination port  |  2 bytes, big-endian u16
+-------------------+
| source IPv6       | 16 bytes
+-------------------+
| source port       |  2 bytes, big-endian u16
+-------------------+
```

Outbound, a client sets the destination address and port to the pinned virtual
destination, the source address to its assigned virtual client address, and the
source port to a value of its own choosing. **The relay always overwrites the
source address with the sender's assigned address**, so the field cannot be used
to impersonate another client and a client gains nothing by lying in it. The
source port is not overwritten: the relay retains it and returns it, which makes
it a correlation value rather than a routed one.

Inbound, a return header names the client as destination and the virtual
destination as source. A conforming client accepts a return frame only if

1. its type is `RELAY_PACKET`;
2. its destination address equals the assigned virtual client address;
3. its destination port equals the source port the client sends from; and
4. its source address equals the pinned virtual destination.

The header's **source port is deliberately not checked.** It reports the port
the destination actually answered from, which a client cannot predict from the
virtual port it addressed, so requiring the two to match would reject valid
traffic. Every other field is known in advance, which is why this subset no
longer pins the first return header it sees, and no longer needs an operator
acknowledgement that the destination port is right: the client writes that port
itself, and a wrong one is answered by the relay with error `0x00000003` rather
than assumed away.

None of these checks is a security mechanism. They establish that a frame
belongs to this session's destination pair; what attributes a datagram to a
*measurement case* is the payload tag.

## Frame format

One relay frame is one WebTransport datagram. There is no framing above the
datagram, no fragmentation and no continuation: a frame that does not fit in one
datagram is not sent.

```text
+--------------------------------+--------------------------------------------+
| relay header (40 bytes)        | one or more length-delimited UDP datagrams |
+--------------------------------+--------------------------------------------+

each length-delimited UDP datagram:
+-------------------------+---------------------------+
| length: u16 big-endian  | exactly `length` bytes    |
+-------------------------+---------------------------+
```

Normative rules:

1. Every frame begins with exactly 40 header bytes, in the layout fixed under
   "Addressing". A frame shorter than 40 bytes is malformed.
2. The header is followed by zero or more inner datagram records. Each record
   is a 2-byte big-endian unsigned length followed by exactly that many bytes of
   inner UDP payload. The length field is not included in the count.
3. **Browser to server:** a frame carries **one or more** inner datagrams.
   A frame with none is malformed.
4. **Server to browser:** a frame carries **exactly one** inner datagram. A
   frame with none, or with two or more, is malformed.
5. There is no padding and no alignment. A frame that ends in the middle of a
   length field or in the middle of an inner payload is malformed, and so is a
   frame with any trailing byte after the last complete inner payload. Note
   that "trailing byte" is decided by the grammar, not by intent: a single
   trailing byte cannot begin a length field and is malformed, while two
   trailing bytes are read as a further length field and therefore as another
   inner record, which is legal whenever the bytes that follow it are, including
   the zero-length case.
6. An inner length of 0 is legal in both directions. The measurement vector
   requires a 0-byte inner datagram as a measured case in both directions, so a
   0-byte inner datagram is a valid frame, not an error.

   This is a requirement on the **relay** too, not only on the frame grammar: a
   conforming relay carries a zero-length inner datagram to the destination as a
   zero-length UDP datagram, and wraps a zero-length UDP response as a return
   frame declaring length 0. Since 2026-08-30 the relay this repository measures
   against behaves that way in both directions. A relay that instead discards a
   zero-length inner datagram leaves the committed vector's 0-byte case
   unanswered; a conforming client records that as a case that did not complete
   and never as an acceptance, so such a relay cannot produce a conforming
   measurement.
7. A receiver additionally rejects any inner datagram longer than the accepted
   inner-datagram ceiling. The ceiling is the largest size present in the loaded
   measurement vector, which is 16,384 bytes for the committed vector. This is a
   receiver limit derived from the measurement plan, not a game constant; this
   subset is game-neutral and knows nothing about any game's message sizes.

### Overhead arithmetic

A frame carrying inner datagrams of sizes `n1..nk` occupies

```text
40 + sum(2 + ni)  bytes
```

For a single inner datagram this is `n + 42` bytes, which is exactly the
`singleDatagramOverheadBytes` value the measurement vector fixes. That overhead
is **known and checked**, not measured as if it were an unknown transport
property. It is checked in three places: the encoder produces exactly that
length, the decoder's grammar admits a single-datagram frame only at that length
because it allows no trailing byte, and the report validator recomputes both the
sent and the returned frame sizes from the inner sizes and rejects a record that
disagrees.

Because the server-to-browser direction carries exactly one inner datagram, a
browser-to-server frame packing `k` datagrams is answered by `k` separate
server-to-browser frames, and the return direction pays the 42-byte overhead `k`
times. This asymmetry is the reason the measurement vector's packed cases are
browser-to-server only.

## Keep-alive

An idle authorized session may need periodic traffic to stay routable. This
profile has a datagram type for it: `KEEP_ALIVE`, the 4-byte type followed by
arbitrary padding that carries no meaning. A relay answers one with a
`KEEP_ALIVE` of its own, whose padding is likewise meaningless.

Because a keep-alive is its own type, it can never be confused with a
measurement datagram. That is the practical improvement the 2026-08-30 amendment
brought here: the withdrawn profile expressed a keep-alive as the smallest legal
relay frame, which is byte-identical to the committed vector's 0-byte case, and
therefore had to be kept apart from measurement traffic by sequencing rules and
an outstanding-keep-alive count. Those rules are gone; the type does the work.

Whether a keep-alive is needed, and at what interval, belongs to the integration
environment. **The probe in this repository sends none.** A measurement plan is
never idle — a case is always either outstanding or ready to start — so a
keep-alive could not fire during a run, and a mechanism that cannot fire is worse
than none: its counters would appear in every report while proving nothing.
Keeping a session alive matters when a session is *held* open, which this probe
does not do; that belongs to the round that holds a routed session open. See
[`wp2-relay-probe.md`](wp2-relay-probe.md).

A client must nevertheless **recognise** an inbound `KEEP_ALIVE`, because a relay
sends one in answer to a keep-alive and a client that treated it as a relay frame
would count perfectly ordinary traffic as malformed.

## Payload identification

The measurement vector fixes that a payload carries a 16-byte nonce as its
**prefix**, that payloads below 16 bytes carry no nonce, and that those smaller
cases run sequentially. It does not fix the interior of those 16 bytes, and
measurement needs the tag to identify both the session — so that two concurrent
sessions can prove they received only their own traffic — and the individual
datagram, so that several tagged datagrams can be outstanding at once. The
interior is therefore:

```text
byte  0..11  session nonce, 12 random bytes, drawn once per session
byte 12..15  datagram ordinal, u32 big-endian, assigned in send order
```

Every planned inner datagram receives an ordinal, including the ones too small
to carry it and the ones a transport limit prevents from being sent, so ordinals
are a stable property of the measurement plan rather than of what happened. A
keep-alive is not part of the plan and carries no ordinal.

The remaining payload bytes are deterministic filler: byte `i` of the payload
equals `i mod 256`. For a payload shorter than 16 bytes the same rule covers the
whole payload, which is why such payloads are indistinguishable between sessions
and must run one at a time with nothing else outstanding.

A destination that echoes UDP payloads unchanged therefore returns a byte-exact
copy. A client accepts a returned inner datagram only if it equals the payload
it sent, byte for byte; a returned datagram whose session nonce belongs to
another session is counted as foreign traffic and never completes a case.

## Receiver obligations

A conforming receiver:

- **demultiplexes on the datagram type before anything else.** A session carries
  more than relay frames: the relay answers a keep-alive and reports an
  unavailable destination in band. A `KEEP_ALIVE` and an `ERROR` are recognised,
  counted and ignored; an `ADDRESS_ASSIGNED` arriving after setup is counted as
  unexpected; an unknown type, or a datagram too short to carry one, is
  malformed. None of them may complete a measurement case, and an `ERROR` in
  particular must not be turned into a distinct measurement outcome — an
  unavailable destination and a silent one are the same fact about the path,
  and the client cannot tell an unknown destination from an unauthorized one
  anyway;
- validates before it allocates. The declared inner length is compared against
  the inner-datagram ceiling and against the bytes actually present **before**
  any buffer is sized or any payload is copied, so a frame declaring 65,535
  bytes in a 44-byte datagram costs two comparisons;
- treats every rule violation above as a rejection of the whole frame, not a
  partial parse. There is no resynchronisation inside a frame;
- never lets a rejected, foreign or unattributable frame complete a measurement
  case, and counts each of those categories separately. An untagged payload is
  attributable only by sequencing, so a returned untagged datagram whose length
  differs from the one outstanding datagram is unattributable — a late echo from
  a case that already timed out must not be charged to the case now waiting;
- bounds what it holds. The number of simultaneously outstanding datagrams never
  exceeds the configured limit, in every state including an empty window. A
  packed case is atomic, so a plan containing a case wider than that limit is
  refused when the plan is built rather than by breaking the bound at run time.
  A case that is not answered before its configured timeout is completed as a
  timeout rather than retained, and a case the run never reached is recorded as
  never run rather than as a timeout. A caller may stop driving while a case is
  outstanding but before its timeout has expired; that case is recorded as a
  timeout too, because it was sent and never answered *within the run*. The
  error is one-directional and safe: such a case is reported as not accepted,
  never as accepted, so it can only understate the usable range.

## Sending obligations

A sender:

- refuses to send a frame larger than the transport's reported maximum datagram
  size, and records that refusal as a distinct outcome rather than attempting
  the write. The reported maximum is a per-session, per-path observation and is
  never treated as a universal constant;
- never fragments. The absence of a transport large enough for a given inner
  size is a measurement result, not a reason to split the datagram;
- sends packed multi-datagram frames only in the browser-to-server direction.

## Conformance vectors

[`probe/conformance-vectors.json`](../probe/conformance-vectors.json) is the
portable expression of this document. It contains, for both directions, encoded
frames with their exact bytes, the rejection cases with the rule each violates,
the tag and payload derivations, and — since the 2026-08-30 amendment — the
session and header profile: the exact bytes of each control datagram, the
control datagrams a client must refuse and which decoder must refuse them, the
outbound header a routing context produces, the byte patterns that are not a
relay header at all, and the return headers a session must accept and must
refuse. The vectors are generated from
`scripts/relay_probe.py` by
[`scripts/emit-relay-conformance-vectors.py`](../scripts/emit-relay-conformance-vectors.py)
and a test fails if the committed file and the module disagree. The browser
probe runs the same file through its own JavaScript implementation before it is
allowed to open a session.

What that catches is exactly what the vectors cover: the datagram types, the
control-datagram grammar, the relay header and its return-path acceptance rule,
the frame grammar in both directions, the rejection rules, the tag interior and
the payload derivation. It does not cover the session driver, the report shape
or the report validator. Those are compared separately, by executing the browser
sources next to the reference implementation and asserting the two produce equal
session records and mutually acceptable reports; see
[`wp2-relay-probe.md`](wp2-relay-probe.md).

Every address in the vectors is drawn from the IPv6 documentation prefix
`2001:db8::/32` (RFC 3849), which is reserved for examples and is never
routable; every port is arbitrary, every authorization is a fixed synthetic
string, and the 40 bytes used purely as frame grammar are a counting pattern. No
real address, port, endpoint or authorization appears in them, and none may be
added.

An independent implementation of this subset can be checked against the vectors
without access to any relay source: the vectors fix the bytes.

## Non-goals

This subset does not describe a relay server, any control or administrative
destination, more than one destination per session, stream-assisted traffic,
tunnel fragmentation, a WebSocket fallback, or any game protocol. A client of
this subset is game-neutral by construction: it moves opaque payloads of chosen
sizes and never inspects them beyond its own tag.
