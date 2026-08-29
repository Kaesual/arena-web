<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Routed datagram contract: the game-destination subset

**Status:** WP2 deterministic part. Normative for this repository's conformance
probe and its tests; routed acceptance against a real endpoint is pending.

This document specifies the part of a WebTransport-to-UDP relay protocol that a
browser client needs in order to exchange datagrams with **one** game
destination: how a session is authorized, how the single virtual client address
and the single pinned virtual destination enter the picture, how an idle session
is kept alive, and — byte for byte — how datagrams are framed in each direction.

It is written from this repository's own approved work-package contract and from
the committed measurement vector
[`locks/relay-measurement-vector.json`](../locks/relay-measurement-vector.json).
It is not a transcript of any relay implementation, and this repository does not
contain one. Where an implementation detail is not determined by those two
inputs, this document says so instead of guessing; the affected value is then
runtime configuration supplied by the integration environment, never a committed
constant.

## What this document fixes, and what it does not

Fixed here, and enforced by
[`scripts/relay_probe.py`](../scripts/relay_probe.py) and its tests:

- the frame grammar in both directions, including every rejection rule;
- the direction asymmetry and the resulting overhead arithmetic;
- the interior of the 16-byte payload tag, its placement, and the sequencing
  rule for payloads too small to carry it;
- the receiver's fail-closed obligations, including the ban on allocating from
  an untrusted length field;
- the shape and validation rules of the measurement report.

Not fixed here, and therefore supplied at runtime:

- the **interior of the 40-byte relay header**. This subset treats it as an
  opaque routing prefix. Its field layout belongs to the relay implementation's
  own published contract; a conforming client of this subset never parses it,
  and this repository does not derive it by observing a running relay.
- the **name and location of the authorization parameter** inside the endpoint
  URL;
- whether an idle session requires a keep-alive at all, and at what interval.
  The frame shape is specified below; sending one is not implemented here;
- the endpoint, its trust input, the authorization value, the virtual client
  address, the virtual destination address and its UDP port.

These gaps are deliberate. They are recorded again, with what the routed round
must supply, in [`wp2-relay-probe.md`](wp2-relay-probe.md).

## Session establishment and authorization

A client opens exactly one WebTransport session to the relay endpoint and uses
only its unreliable datagram channel. This subset defines no stream traffic; a
conforming probe opens no stream and sends no message type other than the
datagram frames below.

Authorization is presented once, when the session is established. The browser
platform offers no request-header control over a `WebTransport` construction, so
a browser-presented authorization can only travel inside the endpoint URL or
inside the session's own first message. **This profile uses the URL**: the
operator supplies an endpoint template containing exactly one `{authorization}`
placeholder, and the client substitutes the short-lived authorization value into
it at connect time. The parameter name is part of the template and therefore
environment-specific.

An authorization is expected to be short-lived and single-use. The client
obtains one per session, never persists it, never writes it into a report, and
never reuses one across sessions. A relay that rejects an invalid or expired
authorization does so by refusing or closing the session; this subset defines no
in-band authorization error datagram.

Trust is either public Web PKI or WebTransport's `serverCertificateHashes` with
SHA-256, as recorded by
[`immutable-baseline.md`](immutable-baseline.md). The hash is runtime
configuration. This subset never requires a machine-wide trust-store change.

## Addressing

A session has exactly one virtual client address and addresses exactly one
pinned virtual destination. Both are properties of the authorization and of the
relay's own projection, not values this subset re-negotiates per datagram.
Consequently every frame of a session carries the **same** 40-byte routing
prefix, and this subset requires only two things of that prefix:

1. It addresses exactly one destination, whose UDP port equals the projected
   endpoint's port **exactly**. A frame whose destination port differs from the
   projection is not a frame for this destination and is not routed. This is a
   **precondition the operator asserts**, not something a client checks: the
   port lives inside bytes this subset does not parse. A conforming client
   requires an explicit acknowledgement that the prefix it was given satisfies
   this, and refuses to run without one.
2. It is constant for the direction and session. A client therefore pins the
   prefix of the first accepted server-to-browser frame and rejects any later
   frame whose prefix differs. Where the operator can state the return prefix in
   advance, the client compares against that instead and the first frame is
   checked like every other.

Nothing else about the prefix is interpreted, and neither property is a security
mechanism. The pin detects drift; it cannot authenticate anything, because a
client that has never seen the correct prefix adopts whichever arrives first.
What attributes a datagram to a session is the payload tag, not the prefix.


## Frame format

One relay frame is one WebTransport datagram. There is no framing above the
datagram, no fragmentation and no continuation: a frame that does not fit in one
datagram is not sent.

```text
+--------------------------------+--------------------------------------------+
| relay header (40 bytes, opaque)| one or more length-delimited UDP datagrams |
+--------------------------------+--------------------------------------------+

each length-delimited UDP datagram:
+-------------------------+---------------------------+
| length: u16 big-endian  | exactly `length` bytes    |
+-------------------------+---------------------------+
```

Normative rules:

1. Every frame begins with exactly 40 header bytes. A frame shorter than 40
   bytes is malformed.
2. The header is followed by zero or more inner datagram records. Each record
   is a 2-byte big-endian unsigned length followed by exactly that many bytes of
   inner UDP payload. The length field is not included in the count.
3. **Browser to server:** a frame carries **one or more** inner datagrams.
   A frame with none is malformed.
4. **Server to browser:** a frame carries **exactly one** inner datagram. A
   frame with none, or with two or more, is malformed.
5. There is no padding and no alignment. A frame that ends in the middle of a
   length field or in the middle of an inner payload is malformed, and so is a
   frame with any trailing byte after the last complete inner payload.
6. An inner length of 0 is legal in both directions. The measurement vector
   requires a 0-byte inner datagram as a measured case in both directions, so a
   0-byte inner datagram is a valid frame, not an error.
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
subset adds no message type for it: the keep-alive is the smallest legal
browser-to-server frame, that is, the routing prefix followed by one inner
datagram of length 0, for a total of 42 bytes. Rule 6 already makes such a frame
valid, so a keep-alive is indistinguishable from a 0-byte measurement datagram
on the wire.

Whether a keep-alive is needed, and at what interval, belongs to the integration
environment. A client that sends keep-alives must

- send none while any measurement case is in flight, and
- count outstanding keep-alives and consume a returned 0-byte inner datagram
  against that count before attributing it to a 0-byte measurement case,

so that keep-alive echoes can never be mistaken for measurement results.

**The probe in this repository sends no keep-alives.** A measurement plan is
never idle — a case is always either outstanding or ready to start — so a
keep-alive could not fire during a run, and a mechanism that cannot fire is
worse than none: its counters would appear in every report while proving
nothing. Keeping a session alive matters when a session is *held* open, which
this probe does not do. The rules above stay part of the specification, and
implementing them belongs to the round that holds a routed session open. See
[`wp2-relay-probe.md`](wp2-relay-probe.md).

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
  never run rather than as a timeout.

## Sending obligations

A sender:

- refuses to send a frame larger than the transport's reported maximum datagram
  size, and records that refusal as a distinct outcome rather than attempting the
  write. The reported maximum is a per-session, per-path observation and is never
  treated as a universal constant;
- never fragments. The absence of a transport large enough for a given inner
  size is a measurement result, not a reason to split the datagram;
- sends packed multi-datagram frames only in the browser-to-server direction.

## Conformance vectors

[`probe/conformance-vectors.json`](../probe/conformance-vectors.json) is the
portable expression of this document. It contains, for both directions, encoded
frames with their exact bytes, the rejection cases with the rule each violates,
and the tag and payload derivations. The vectors are generated from
`scripts/relay_probe.py` by
[`scripts/emit-relay-conformance-vectors.py`](../scripts/emit-relay-conformance-vectors.py)
and a test fails if the committed file and the module disagree. The browser
probe runs the same file through its own JavaScript implementation before it is
allowed to open a session.

What that catches is exactly what the vectors cover: the frame grammar in both
directions, the rejection rules, the tag interior and the payload derivation.
It does not cover the session driver, the report shape or the report validator.
Those are compared separately, by executing the browser sources next to the
reference implementation and asserting the two produce equal session records and
mutually acceptable reports; see
[`wp2-relay-probe.md`](wp2-relay-probe.md).

Every routing prefix in the vectors is a synthetic byte pattern. No real prefix,
address, port, endpoint or authorization appears in them, and none may be added.

An independent implementation of this subset can be checked against the vectors
without access to any relay source: the vectors fix the bytes, and the opaque
prefix is supplied as input in each case.

## Non-goals

This subset does not describe a relay server, any control or administrative
destination, more than one destination per session, stream-assisted traffic,
tunnel fragmentation, a WebSocket fallback, or any game protocol. A client of
this subset is game-neutral by construction: it moves opaque payloads of chosen
sizes and never inspects them beyond its own tag.
