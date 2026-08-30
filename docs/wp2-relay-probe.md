<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP2 evidence: relay conformance probe

**Status:** complete — the deterministic part, the 2026-08-30 in-band
amendment and the routed acceptance round of 2026-08-30 are done; the
validated measurement record is committed as
[`records/wp2-routed-measurement.json`](../records/wp2-routed-measurement.json)

This document records what exists for WP2 and how each part was accepted. The
public contract, the browser probe, the in-memory adapter and the
deterministic tests are built and green, and the routed acceptance — a real
endpoint, real measurements, a machine-readable report from the pinned
browser and a payload budget derived from repeated sessions — happened on
2026-08-30 against an operator-provisioned integration environment; its
record and numbers are in the "Routed acceptance" section below.

`ioq3/` is untouched at its pinned commit. No lock, schema, WP0/WP1/WP3 script
or committed manifest was changed, and the committed measurement vector is
byte-identical: still
`sha256:546a9a859f92d72d0a2d7dc14acdd80410e0873148500b2d0e26765a6182a064`, still
46 cases.

## Amendment 2026-08-30: the in-band session profile

The routed-readiness check recorded in
[`wp2-routed-readiness-2026-08-30.md`](wp2-routed-readiness-2026-08-30.md) found
that the contract's *integration* profile did not match the session model of the
relay the routed round will run against. The framing was compatible; the way a
session is authorized and addressed was not. This amendment replaces that part of
the profile. It is a deliberate change of a normative document, made before any
routed measurement existed, and it changes no measured size, no case and no
committed identity.

**Withdrawn**

- *Authorization in the endpoint URL.* The endpoint was an `https` template
  containing exactly one `{authorization}` placeholder, substituted verbatim at
  connect time.
- *Refusal by handshake.* An invalid authorization was expected to be refused by
  refusing or closing the WebTransport session, with no in-band answer.
- *The opaque 40-byte routing prefix.* The header was runtime input supplied as
  80 hexadecimal characters, never parsed, pinned on first sight, and its
  destination port was an operator acknowledgement rather than a check.
- *The keep-alive expressed as the smallest legal frame.* Byte-identical to the
  0-byte measurement case, so it needed sequencing rules to stay apart from it.

**Adopted**

- *A plain endpoint URL and an in-band exchange.* The endpoint carries no
  authorization and no placeholder — a URL still containing one is refused with
  an explanation. A session begins with one `REQUEST_ADDRESS` carrying the
  authorization verbatim as UTF-8, and is unusable until `ADDRESS_ASSIGNED`
  names its 16-byte virtual client address.
- *In-band refusal, then termination.* An invalid or refused authorization is
  answered with `ERROR` code `0x00000002`, after which the relay closes the
  session. The acceptance case for invalid authorization changed accordingly:
  it now requires that exact in-band code followed by a session that carries
  nothing further. Neither the authorization nor the endpoint nor the relay's own
  error text may appear in a log or a report; only the code does.
- *A public, self-contained routing header.* The 40 bytes are type, destination
  address and port, source address and port. The probe builds the outbound header
  from runtime destination values and its assigned address, and validates a return
  header on three of its four fields. The fourth, the return source port, is the
  destination's own reply port, which a client cannot predict and therefore does
  not check. The first-frame pin and the destination-port acknowledgement are
  both gone: the client writes the port itself.
- *A keep-alive datagram type.* `KEEP_ALIVE` is its own type, so it can never be
  confused with a 0-byte measurement datagram. The probe still sends none, but it
  recognises and counts an inbound one instead of calling it malformed.
- *Zero-length inner datagrams, stated behaviourally.* A conforming relay carries
  a zero-length inner datagram to the destination and wraps a zero-length
  response as a return frame declaring length 0. Since 2026-08-30 the relay this
  repository measures against behaves that way in both directions. A relay that
  discards it leaves the committed vector's 0-byte case unanswered, which a
  conforming client records as a case that did not complete — never as an
  acceptance.

**Consequences for the artifacts**

- The measurement report's `formatVersion` is **2**. The opaque
  `prefixMismatchFrames` counter became `headerMismatchFrames`, and the session
  record gained `errorDatagrams`, `keepAliveDatagrams` and
  `unexpectedControlDatagrams`. No routed report of either version exists; the
  bump is there so none can be misread if one turns up.
- The runtime configuration changed shape: `endpointTemplate`,
  `routingPrefixHex`, `expectedReturnPrefixHex` and
  `destinationPortMatchesProjection` are gone; `endpointUrl`,
  `destinationAddressHex`, `destinationPort`, `clientSourcePort` and
  `assignmentTimeoutMilliseconds` replace them.
- The conformance vectors grew from 54 to 82 cases and their `formatVersion` is
  2. The measurement vector did not change at all.

## What was built

| Path | Role |
| --- | --- |
| [`relay-datagram-contract.md`](relay-datagram-contract.md) | the normative public specification of the game-destination subset, including the 2026-08-30 session and header profile |
| [`scripts/relay_probe.py`](../scripts/relay_probe.py) | the reference implementation: datagram types, session setup, relay header, frame grammar, payload tag, measurement plan, session driver, report validator |
| [`scripts/relay_loopback.py`](../scripts/relay_loopback.py) | the in-memory relay and echo destination, modelling the session semantics, with deliberate contract violations for the rejection paths |
| [`scripts/relay_vectors.py`](../scripts/relay_vectors.py) + [`emit-relay-conformance-vectors.py`](../scripts/emit-relay-conformance-vectors.py) | the portable conformance vectors and their emitter |
| [`probe/conformance-vectors.json`](../probe/conformance-vectors.json) | 82 committed cases: 19 encoded frames, 4 ceiling acceptances, 15 frame rejections, 8 tags, 8 payloads, 10 control datagrams, 7 control rejections, 3 headers, 3 header rejections, 5 return-header decisions |
| `probe/relay-framing.js`, `probe/measurement.js`, `probe/adapters.js`, `probe/probe.js`, `probe/index.html` | the standalone browser probe, including its own report validator so the page never renders or offers an unvalidated report |
| [`scripts/serve-probe.sh`](../scripts/serve-probe.sh) | loopback static server for the probe |
| [`tests/test_relay_probe.py`](../tests/test_relay_probe.py) | 157 deterministic tests for this work package; the repository suite is 667 |
| [`tests/js_conformance_harness.mjs`](../tests/js_conformance_harness.mjs) | runs the browser sources under Node so the suite can compare the two implementations |

The tests run in `scripts/check.sh` and in the containerized
`scripts/check-container.sh` like every other test in this repository, with no
third-party dependency and no network.

## The framing this repository now fixes

- Every datagram opens with a 4-byte big-endian type. A session begins with
  `REQUEST_ADDRESS` and `ADDRESS_ASSIGNED`; `RELAY_PACKET` carries measurement
  traffic; `ERROR` and `KEEP_ALIVE` are recognised, counted and ignored.
- One relay frame is one WebTransport datagram. There is no framing above the
  datagram and no fragmentation.
- A frame opens with a 40-byte relay header — type, destination address and
  port, source address and port — followed by inner UDP datagrams each
  introduced by a 2-byte big-endian unsigned length.
- Browser to server carries **one or more** inner datagrams; server to browser
  carries **exactly one**.
- No padding, no trailing bytes. A frame that ends inside a length field or
  inside a payload is malformed, and so is one with a byte left over.
- A frame carrying sizes `n1..nk` occupies `40 + sum(2 + ni)` bytes, so a single
  datagram occupies `n + 42`. The probe verifies that 42-byte overhead against
  every frame it builds and accepts rather than measuring it.
- A packed browser-to-server frame of `k` datagrams is answered by `k` separate
  server-to-browser frames, so the return direction pays the overhead `k` times.
- The client writes the destination UDP port into the header itself, from
  runtime configuration; a wrong port is answered by the relay as an
  unavailable destination rather than assumed away.

## How the plan exercises both directions

The measurement vector lists sizes for both directions and the contract requires
single-datagram cases in both, but the traffic goes through a destination that
echoes UDP payloads unchanged. A browser cannot therefore ask for a
server-to-browser size independently: the return size is whatever it sent. One
round trip at `n` bytes consequently exercises the browser-to-server direction
at `n` and the server-to-browser direction at `n` at the same time, and the
plan's single cases are the union of the two direction lists — all 42 sizes of
the committed vector, which happen to be identical in both.

A packed case is where the two directions separate. Its `k` inner datagrams
leave in one frame and come back as `k` frames, so it measures a large
browser-to-server frame against several small server-to-browser ones. That
asymmetry is the reason the vector has packed cases in one direction only, and
the plan refuses a packed case declared for the other.

The plan also refuses a packed case containing a datagram below the tag length,
because every datagram of a packed case is outstanding at once and an untagged
one could not be attributed to its return frame. The committed vector satisfies
that rule; the check exists so a future vector cannot quietly break correlation.

## Decisions taken inside the contract

The work-package text and the WP0 measurement vector determine the framing
completely. Four things they leave open were decided here, and each is recorded
in the specification rather than buried in code. Two of the four were revised by
the 2026-08-30 amendment above; both are stated here in their current form, with
what they replaced.

**The 40-byte header is a public, self-contained profile.** It is type,
destination address and port, source address and port. The probe builds the
outbound header from the destination values it is given and the client address
the relay assigned, and accepts a return frame only when its type, destination
address, destination port and source address are the ones this session must see.
The return header's source port is the destination's own reply port, which a
client cannot predict from the virtual port it addressed, so it is reported
rather than checked. None of this is a security mechanism: it establishes that a
frame belongs to this session's destination pair, and what attributes a datagram
to a *case* is the payload tag.

*This replaced an opaque prefix.* Until 2026-08-30 the header was 80 hexadecimal
characters of runtime input that a client never parsed. Only one of its two
required properties was checkable from a client — the probe pinned the first
accepted return prefix and refused any later frame that differed — and the
other, that the prefix addressed one destination on the projected port, was an
operator acknowledgement the probe refused to run without. Both are now
unnecessary: the client writes the port itself, and a wrong one is answered by
the relay rather than assumed away.

**The 16-byte payload tag is a session nonce plus a datagram ordinal.** The
vector fixes the length, the payload-prefix placement and that shorter payloads
run sequentially, but not the interior. Measurement needs both session identity,
for the concurrent-session isolation evidence, and per-datagram identity, so
several datagrams can be outstanding at once. The tag is therefore 12 random
session bytes followed by a 4-byte big-endian ordinal, assigned in send order
across the whole plan. Every remaining payload byte is the filler `i mod 256`,
which makes an echo byte-comparable.

**Authorization travels in the session's first datagram.** A browser
`WebTransport` construction offers no request-header control, so a
browser-presented authorization can only be in the URL or in the session's first
message. This profile uses the first message: one `REQUEST_ADDRESS` carrying the
value verbatim as UTF-8, answered by the assignment that makes the session
usable, or by an in-band `ERROR` code `0x00000002` after which the relay closes
the session. The value is opaque here — nothing in this repository parses it or
knows how it is issued — and it is single-use by construction: the handshake
holds the only copy, drops it when the request datagram is built, and refuses to
build a second one. The page clears its field after every attempt, successful or
not, because a refused attempt spent the value just as a completed one did.

*This replaced a URL placeholder.* Until 2026-08-30 the operator supplied an
endpoint template containing exactly one `{authorization}` placeholder and the
probe substituted the value verbatim at connect time. That put a secret into a
URL, which platform errors quote; it required the operator to supply a token
already safe at its position in the URL; and it did not match the session model
the routed round has to speak. A template still carrying the placeholder is now
refused with an explanation rather than opened with a literal brace in its path.

**A keep-alive has its own datagram type, and the probe still sends none.**
`KEEP_ALIVE` is the 4-byte type plus meaningless padding, and a relay answers one
with a keep-alive of its own. Because it is a distinct type it can never be
confused with the committed vector's 0-byte case — which is exactly what the
withdrawn profile's "smallest legal frame" keep-alive was byte-identical to, and
why that version needed sequencing rules and an outstanding-keep-alive count to
keep the two apart. Those rules are gone.

The probe **sends none**, and the deterministic round carries no keep-alive
timer or configuration field. A measurement plan is never idle: a case is always
either outstanding or ready to start, so a keep-alive could never fire during a
run. An earlier version shipped the sending mechanism anyway; it was
unreachable, its counter appeared in every report proving nothing, and an
unanswered keep-alive would have blocked the sequential untagged cases
indefinitely because nothing timed it out. A mechanism that cannot fire is worse
than an absent one, so it was removed rather than left as decoration. Keeping a
session alive matters when a session is *held* open — which this probe does not
do, and which the routed round introduces.

The probe does **receive** keep-alives, because a relay answers one, so the
driver recognises the type and counts it rather than calling ordinary traffic
malformed.

## What the deterministic tests prove

The tests cover the acceptance evidence that does not need a network:

- **Missing runtime configuration** — every required field, unknown fields, a
  non-`https` endpoint, an endpoint carrying a fragment or the withdrawn
  `{authorization}` placeholder, an empty authorization, a destination address
  that is not 16 hexadecimal bytes or is the unspecified address, a port outside
  1–65535, a certificate hash that is not a SHA-256 digest, and out-of-range
  numbers are each refused.
- **Session setup** — the authorization is spent exactly once and a second
  request datagram is refused; a datagram accepted before the request was built
  is refused; an assignment of the wrong length, the wrong type or the
  unspecified address is refused; a keep-alive during setup is ignored rather
  than fatal; an in-band `ERROR` raises a refusal carrying **only** its code,
  with neither the authorization nor the relay's text in the message; the
  in-memory relay then closes the session, and nothing further can be sent on
  it. Relay traffic sent before an assignment is never answered, and a driver
  built without a routing context is refused.
- **Malformed relay frames and headers** — short frames, a frame ending inside a
  length field or a payload, a trailing byte, a server-to-browser frame with none
  or two inner datagrams, a browser-to-server frame with none, a header of the
  wrong length or the wrong type, an unknown direction, and a declared length
  above the accepted ceiling. A return header is checked field by field: a
  foreign destination address, a foreign destination port and a foreign source
  address are each refused while the relay's own header still completes the case,
  and the return source port is accepted whatever it is. The in-memory relay
  injects truncated, packed, header-only, oversize-declaring and
  foreign-header return frames, and none of them completes a case.
- **Control datagrams inside a measuring session** — an answered keep-alive, a
  second address assignment and an in-band destination refusal are each counted
  in their own bucket, an unknown type and a datagram too short to carry one
  land together in the malformed-frame counter, and none of them completes a
  case. An unauthorized destination is answered in band
  and its cases still time out, because an unknown and an unauthorized
  destination are the same answer.
- **Mismatched nonces** — two concurrent sessions on a deliberately
  cross-delivering relay each complete only their own **nonce-tagged** cases and
  count the other's traffic as foreign. The qualifier is the whole point. A
  payload below the tag length carries no session nonce, so two sessions running
  such a case at the same time cannot tell their echoes apart; the committed
  vector's 0- and 1-byte cases are exactly that, which is why it runs them
  sequentially and why WP0 already records that they are not concurrent-session
  isolation evidence. The test uses a plan containing those untagged sizes and
  asserts isolation only for the tagged cases. Within one session an untagged
  echo is attributed by sequencing alone, and a returned length that does not
  match the single outstanding datagram is treated as unattributable — a late
  echo from a case that already timed out must not be charged as a defect
  against the case now waiting. A corrupted echo is reported as a payload
  mismatch; a corrupted *tag* is reported as unattributable rather than being
  allowed to complete anything.
- **Out-of-range measurement records** — the report validator rejects unknown
  and missing fields, a wrong kind, version or framing block, a non-SHA-256
  vector digest, a sent frame size that disagrees with its inner sizes, a
  returned frame that does not carry the 42-byte overhead, a returned size the
  case never sent or one counted more often than it was sent, a negative,
  infinite or `NaN` round-trip time, a round-trip time on a case that did not
  echo, an
  echoed case that returned other sizes, more returned frames than datagrams
  sent, reused ordinals, non-ascending case or session indices, a size above the
  plan ceiling, a case whose kind disagrees with its datagram count, a refusal
  that would have fitted the reported transport maximum, an echoed case *larger*
  than that maximum, a failed send or a never-run case that nonetheless returned
  frames, a case wider than the outstanding-datagram bound the session reports,
  and any case that does not match the plan it claims. The browser carries the
  same validator and refuses to render a summary or offer a download for a
  report that fails it; the Python validator stays authoritative and the tests
  compare against it.
- **Cases that never ran** — a case the run never reached is recorded as never
  run, not as a timeout. One case in between is worth naming: a case that *was*
  sent but whose timeout had not expired when the caller stopped driving is
  recorded as a timeout, because within that run it was sent and never answered.
  The error is one-directional and safe — such a case is reported as not
  accepted, never as accepted — so it can only understate the usable range. The
  summary treats a never-run case as a gap that stops the contiguous accepted
  range rather than as either an acceptance or a refusal.
  The browser sizes its time budget from the plan so that a path answering
  nothing at all still reaches every case, because an unrun case is a hole in
  the very range WP6 reads.
- **Boundary behaviour in both directions** — a 0-byte inner datagram is a legal
  measured case, not an error; a frame exactly at the transport maximum is sent
  and one byte larger is recorded as refused without being attempted; the
  largest length a `u16` can express round-trips; and a frame declaring 65,535
  bytes inside a 44-byte datagram is rejected after two comparisons. That last
  one is asserted rather than asserted-about: the test measures peak allocation
  with `tracemalloc` and fails if the decoder allocates anything close to what
  the declared length asks for.
- **The zero-byte case, on the wire in both directions.** The outbound frame is
  asserted to be exactly 42 bytes ending in `0x0000`; the relay is asserted to
  have seen an inner datagram of length 0; the return frame is asserted to be
  exactly 42 bytes ending in `0x0000` and to decode to one empty datagram; and
  the case completes. The failing side is pinned too: an in-memory relay that
  discards a zero-length inner datagram — the behaviour the amendment removed —
  leaves that case unanswered while every larger case still echoes, so a
  non-conforming relay cannot quietly produce a conforming measurement. Both
  implementations are compared on that pair of runs, and the browser's own
  startup self-test refuses to pass unless a zero-length inner datagram actually
  crossed its loopback relay.

Two properties the driver enforces are tested through the relay rather than by
inspection: payloads below the tag length run with nothing else outstanding in
either direction, and the number of simultaneously outstanding datagrams never
exceeds the configured bound — in every state, including an empty window. That
bound used to be skipped whenever nothing was outstanding, which let a packed
case wider than the limit go out whole. A packed case is atomic, so the bound is
now unconditional and a plan containing a case wider than it is refused when the
plan is built; the committed vector's widest packed case is four datagrams
against a default bound of eight, and a test asserts that relationship.

What these tests do **not** prove is anything about a network. Every outcome
above was produced by an in-memory adapter.

## How the published contract is shown to be independent

The acceptance evidence requires the published contract and adapter to be
complete enough for an independent implementation to satisfy the same tests
without access to any relay source. Three things make that checkable here:

1. **The vectors are the conformance suite.** `probe/conformance-vectors.json`
   fixes the exact bytes of every control datagram, every encoded frame, every
   outbound header, every rejection and every tag and payload derivation, plus
   the return headers a session must accept and must refuse. A second
   implementation needs only this file and the specification.
2. **A second implementation already exists and the suite checks it.** The
   browser probe's JavaScript is a separate implementation of the same contract
   — frame grammar, tag, plan, session driver and report validator. It is not
   generated from the Python and does not read it at runtime. The probe runs the
   committed vectors and a complete loopback plan through its own code before
   the page will open a session, and refuses to connect if anything disagrees,
   and `tests/test_relay_probe.py` does the same thing offline by executing the
   browser sources under Node.
3. **The two implementations are compared record by record, not by eye.** For
   two transport limits, one above the whole plan and one that forces 34
   refusals, the JavaScript driver's session record is asserted to be *equal* to
   the Python driver's, including every ordinal, frame size, returned size,
   outcome and round-trip time. The resulting report then validates against the
   Python plan validator unchanged. The comparison also covers the places
   the languages differ rather than agree by construction: the late untagged
   echo, where both must refuse to attribute the frame; the authorization on the
   wire, where `TextEncoder` and `str.encode` must produce the same bytes for
   values containing `$&`, `` $` ``, `$'`, `$$`, `$1` and non-ASCII characters;
   the single-use rule, where both must refuse a second request datagram; the
   in-band refusal, where both must raise a refusal carrying only its code and
   then refuse to send anything else on that session; and the zero-byte case,
   preserved and dropped, where both must produce the same outcomes.
   These tests skip where Node is absent and run in the pinned container image,
   which ships Node 24.
4. **The browser's rejection side is driven, not assumed.** The browser is what
   takes the routed measurement, and its `foreignFrames` counter is the
   concurrent-session evidence, so leaving its accounting untested would have
   left the load-bearing part unproven. The published in-memory adapter carries
   the same *fault* settings in both languages — its cross-delivery mode, used
   for the two-session isolation test, exists only in the Python one — and the
   suite drives the
   browser through truncated, packed, header-only, oversize-declaring,
   corrupting, dropping, refusing, foreign-header, unauthorized-destination and
   zero-length-dropping relays, plus a foreign-nonce frame and a run of control
   datagrams, asserting its counters and outcomes equal the reference's run for
   run.
   Every counter the browser owns — foreign, malformed, header-mismatch,
   unattributed, relay errors, keep-alives, unexpected control datagrams and
   write failures — is reached by that set, together with the
   echoed, payload-mismatch, send-failed and timed-out outcomes. The remaining
   two are covered separately: refused-for-size by the record-equality run at a
   1,200-byte transport limit, and never-run by a run deliberately stopped
   before it reaches its last cases, whose never-run/timed-out split is asserted
   equal to the reference's for the same schedule. Its validator is driven over
   the same mutations the reference's is, in the same test, and its summary is
   compared with the reference's.

Point 3 is the strongest statement available without a network: two
independently written implementations of the published contract, driven by the
published adapter, produce the same bytes and the same records.

The in-memory adapter is published for the same reason. It is not a relay: it
has no authorization, no address mapping and no routing table. It exists so that
the contract can be exercised, and so that its fault settings can produce the
traffic the contract forbids.

## The probe in the pinned browser

The page was opened in the exact WP0 acceptance browser, Chrome for Testing
`152.0.7977.64`, served from a clean checkout by `scripts/serve-probe.sh` on
loopback. Its startup self-test reported:

```text
measurement vector sha256:546a9a859f92d72d0a2d7dc14acdd80410e0873148500b2d0e26765a6182a064 — 46 cases, ceiling 16384 bytes
self-test passed: 54 conformance vectors, 46 loopback cases
```

That digest is exactly `sha256sum locks/relay-measurement-vector.json`, so the
identity the browser will stamp into a report is the committed WP0 vector and
not some other copy. The line also shows that the ES modules resolve, that
`crypto.subtle` is available, that the browser's own implementation accepts every
committed conformance vector, and that a complete 46-case plan runs through
the in-memory loopback inside the browser.

**That observation predates the 2026-08-30 amendment and its second line no
longer reproduces.** The vector count is now 82, and the loopback session the
self-test runs goes through the in-band setup exchange and asserts that a
zero-length inner datagram actually crossed the relay. The fresh observation
was taken the same day, in the same pinned browser, before the routed round:

```text
measurement vector sha256:546a9a859f92d72d0a2d7dc14acdd80410e0873148500b2d0e26765a6182a064 — 46 cases, ceiling 16384 bytes
self-test passed: 82 conformance vectors, 46 loopback cases
```

The first line is unchanged, because the measurement vector is; the earlier
record above is kept as what was seen then.

This is **not** a measurement and involved no relay: no WebTransport session was
opened, and the only network traffic was the loopback page load. It is evidence
that the probe is ready to be pointed at an endpoint, nothing more. Real-browser
acceptance of a routed session belongs to the pending work below.

## Routed acceptance — completed 2026-08-30

The routed round ran on 2026-08-30 against an operator-provisioned
integration environment: a conforming relay on a separate rehearsal machine,
a byte-exact UDP echo destination behind a temporary mapping, short-lived
single-use authorizations minted immediately before each session, and
certificate-hash trust — every value supplied at runtime and none of it
recorded anywhere, exactly as the contract requires. The path was the
operator's workstation to that machine over one routed LAN hop, which is what
the committed record's `pathNotes` says and all it says.

What the round did, in the exact pinned WP0 browser (Chrome for Testing
`152.0.7977.64`, driven headless in its new-headless mode):

- **Three sequential sessions** in one browser context, each opened with a
  fresh single-use authorization and closed before the next.
- **Two concurrent sessions** in two separate browser contexts, opened
  back-to-back on authorizations naming distinct virtual client addresses;
  both completed their session while the other was live. A shared address
  would have been refused by the relay's ownership rule, so two completed
  concurrent sessions are themselves the distinct-address evidence.
- **The invalid-authorization case**: a syntactically fresh authorization
  with a deliberately damaged signature was answered in band with `ERROR`
  code `0x00000002` and the session then carried nothing further — the
  amended acceptance behaviour, observed on a real relay.
- The five session records were merged with `merge_reports`, validated, and
  committed as
  [`records/wp2-routed-measurement.json`](../records/wp2-routed-measurement.json);
  `tests/test_relay_probe.py` re-validates the committed bytes against the
  committed measurement vector on every gate run and pins that no runtime
  value leaked into the record's free-text fields.

**The numbers, identical across all five sessions:**

- the browser-reported datagram maximum was **1,024 bytes**, so the inner
  UDP budget was 982 bytes after the 42-byte overhead;
- the largest echoed inner datagram at the vector's granularity was **768
  bytes** (an 810-byte frame), the smallest refused single size 1,024, and
  every size at or below 768 echoed — `monotonic` is true in every session
  and the merged `conservativeInnerFloorBytes` is **768**;
- the **zero-length inner datagram completed in both directions** through
  the routed relay — the boundary case the 2026-08-30 relay-side change
  exists for, observed end-to-end from a real browser;
- write failures were zero everywhere: every refused size was refused by the
  size pre-check against the reported maximum, not by a failed write.

Two observations about that 1,024-byte maximum. It was byte-identical in an
earlier same-day rehearsal of the identical procedure against a relay on the
workstation itself (loopback), so it is a property of the implementations at
the two ends, not of the path between them. And a separate probe that held a
session open and re-read the transport's reported maximum every 500 ms for
six seconds saw the value constant, so the "connect-time sample" caveat below
did not bite on this path. Which end imposes 1,024 — the pinned browser or
the relay's QUIC stack — is deliberately left unattributed here; the floor is
what WP6 consumes either way, and only a different browser or a different
relay implementation could separate the two.

The provisioning the readiness report lists as steps 5 to 7 was supplied by
the operator for the round and dismantled afterwards; none of it is, or ever
was, something this repository carries.

The report format, the summary reduction and the per-session floor are
implemented and tested, so the routed round produces the report by running the
probe rather than by writing new code.

One hazard in that reduction is closed in the data rather than only in prose.
Two concurrent sessions send byte-identical untagged payloads, so in a report
holding more than one session an untagged case may have been completed by the
other session's echo — and a falsely completed 0-byte case would have lifted
`contiguousInnerBytes` from nothing to a number. Both implementations therefore
drop sizes below the tag length from the contiguous walk as soon as a report
holds more than one session, **and** every summary names its
`untaggedSingleSizes` and sets `contiguousExcludesUntagged`. The caveat now
travels in the JSON the floor travels in, instead of living only here.

The floor is deliberately per session and carries no safety margin; choosing a
margin is WP6's decision, not this document's.

Two concurrent sessions need two browser contexts, and each context numbers its
own sessions from zero, so their reports cannot simply be concatenated —
identical session indices would collide. `merge_reports` in
[`scripts/relay_probe.py`](../scripts/relay_probe.py) renumbers them in the
order given, refuses inputs that name different measurement vectors or describe
the path differently, leaves its inputs untouched, and validates the result
against the measurement plan it is given — the merged report is the routed
round's actual deliverable, so it is the one that most needs checking. That path
is committed and tested.
The routed round therefore produces the concurrent-session evidence as one valid
report without new code.

### What the operator supplied

Per the plan's own list, and refined by what the probe turned out to need after
the 2026-08-30 amendment, the round required — and received, at runtime only:

- a compatible integration relay endpoint, as a plain `https` WebTransport URL —
  **no** authorization, placeholder or fragment in it;
- a browser-compatible trust mechanism: public Web PKI, or the SHA-256
  fingerprint of an ECDSA P-256 certificate whose total validity is at most 14
  days, as recorded in [`immutable-baseline.md`](immutable-baseline.md);
- a UDP echo destination behind the relay, reachable at the pinned virtual
  destination;
- that destination's virtual IPv6 address, as 32 hexadecimal characters, and its
  UDP port as a number. The probe writes both into every outbound header, so
  there is nothing left to acknowledge;
- one fresh single-use authorization per session, and enough of them for
  repeated sessions. Each must permit only the echo destination used for the
  run, and each must assign a virtual client address — the concurrent-session
  evidence needs the two sessions to be assigned **distinct** addresses, which
  is a property of the authorizations, not something the probe requests.

The virtual client address is no longer an operator input: the relay assigns it
during setup and the probe reads it from the assignment.

None of these values may be committed. The probe takes all of them at runtime,
and the report has no field in which any of them could be recorded — including
the assigned address, which the page also keeps out of its log.

### Open items, as the routed round left them

- ~~**The relay header's interior.**~~ Settled by the 2026-08-30 amendment: the
  header is a public, self-contained profile, the probe builds it and validates
  the return, and the destination-port acknowledgement is gone.
- ~~**How the endpoint expects authorization.**~~ Settled by the same amendment:
  in the session's first datagram, refused in band, terminal.
- **Whether a keep-alive is required at all**, and at what interval. The
  datagram type exists and inbound ones are recognised; nothing periodic is
  sent. The routed round's sessions each completed in well under a minute, so
  it could not answer this — only a held-open routed session can, and the
  question passes to the work that holds sessions open (the browser backend
  and the two-browser acceptance).
- **Write-failure attribution.** A WebTransport datagram write resolves after
  the datagram is queued, so a rejected write is observed one case late: the
  adapter marks itself failed and refuses later sends, but the case that caused
  the rejection is recorded as a timeout. The routed round observed zero write
  failures — every refusal came from the size pre-check — so whether the
  browser ever rejects a write that fits `maxDatagramSize` remains unobserved.
- **`maxDatagramSizeBytes` is a connect-time sample.** The probe reads the
  transport's reported maximum once, when the session opens, and records that.
  On the routed path this did not bite: a separate held-open probe re-read the
  value every 500 ms for six seconds and saw it constant. The caveat stands
  for other paths; re-reading per send remains a small change if one turns out
  to move it.
- **Write failures are counted, not attributed.** A rejected datagram write is
  observed after the fact, so the affected case shows as a timeout. The count
  now travels in the report as a session counter rather than only in the page
  log, so a reader of the JSON can see that a session had failed writes at all.
- **The floor is not per direction, and cannot be made so by this method.**
  Every single case is one round trip through a destination that echoes payloads
  unchanged, so an accepted size means the browser-to-server frame *and* the
  matching server-to-browser frame both survived, and a refusal does not say
  which of the two refused it. `conservativeInnerFloorBytes` is therefore a
  single round-trip-derived number with no per-direction attribution. WP6 asks
  for per-direction budgets and this methodology cannot supply them: separating
  the directions needs a destination that can be asked to reply at a size other
  than the one it received, which the contract's echo destination is not. WP6
  inherits this gap knowingly, and the packed cases are the only asymmetry the
  present plan produces — a large browser-to-server frame answered by several
  small server-to-browser ones.

## Repeating what exists

```bash
scripts/check.sh                                  # 667 tests, no network
CONTAINER_RUNTIME=podman scripts/check-container.sh
python3 scripts/emit-relay-conformance-vectors.py --check
scripts/serve-probe.sh                            # then open http://127.0.0.1:8173/probe/
```

Opening the probe runs its self-test and reports the measurement vector's
SHA-256, the plan size and the number of conformance vectors and loopback cases
it checked. Without runtime configuration it will not open a session, which is
the intended state until the values above exist.
