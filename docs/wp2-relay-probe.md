<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP2 evidence: relay conformance probe

**Status:** deterministic part implemented; routed acceptance **pending**

This document records what exists for WP2 today and, just as precisely, what
does not. The public contract, the browser probe, the in-memory adapter and the
deterministic tests are built and green. The routed acceptance — a real
endpoint, real measurements, a machine-readable report from the pinned browser
and a payload budget derived from repeated sessions — has **not** happened, and
cannot happen until an operator supplies the one-time runtime values listed at
the end.

Nothing here is a measurement result. No number in this document was observed on
a network path.

`ioq3/` is untouched at its pinned commit. No lock, schema, WP0/WP1/WP3 script
or committed manifest was changed.

## What was built

| Path | Role |
| --- | --- |
| [`relay-datagram-contract.md`](relay-datagram-contract.md) | the normative public specification of the game-destination subset |
| [`scripts/relay_probe.py`](../scripts/relay_probe.py) | the reference implementation: frame grammar, payload tag, measurement plan, session driver, report validator |
| [`scripts/relay_loopback.py`](../scripts/relay_loopback.py) | the in-memory relay and echo destination, with deliberate contract violations for the rejection paths |
| [`scripts/relay_vectors.py`](../scripts/relay_vectors.py) + [`emit-relay-conformance-vectors.py`](../scripts/emit-relay-conformance-vectors.py) | the portable conformance vectors and their emitter |
| [`probe/conformance-vectors.json`](../probe/conformance-vectors.json) | 50 committed cases: 19 encoded frames, 15 rejections, 8 tags, 8 payloads |
| `probe/relay-framing.js`, `probe/measurement.js`, `probe/adapters.js`, `probe/probe.js`, `probe/index.html` | the standalone browser probe, including its own report validator so the page never renders or offers an unvalidated report |
| [`scripts/serve-probe.sh`](../scripts/serve-probe.sh) | loopback static server for the probe |
| [`tests/test_relay_probe.py`](../tests/test_relay_probe.py) | 109 deterministic tests, raising the suite from 162 to 271 |
| [`tests/js_conformance_harness.mjs`](../tests/js_conformance_harness.mjs) | runs the browser sources under Node so the suite can compare the two implementations |

The tests run in `scripts/check.sh` and in the containerized
`scripts/check-container.sh` like every other test in this repository, with no
third-party dependency and no network.

## The framing this repository now fixes

- One relay frame is one WebTransport datagram. There is no framing above the
  datagram and no fragmentation.
- A frame opens with a 40-byte relay header, followed by inner UDP datagrams
  each introduced by a 2-byte big-endian unsigned length.
- Browser to server carries **one or more** inner datagrams; server to browser
  carries **exactly one**.
- No padding, no trailing bytes. A frame that ends inside a length field or
  inside a payload is malformed, and so is one with a byte left over.
- A frame carrying sizes `n1..nk` occupies `40 + sum(2 + ni)` bytes, so a single
  datagram occupies `n + 42`. The probe verifies that 42-byte overhead against
  every frame it builds and accepts rather than measuring it.
- A packed browser-to-server frame of `k` datagrams is answered by `k` separate
  server-to-browser frames, so the return direction pays the overhead `k` times.
- The destination UDP port must equal the projected endpoint's port exactly.

## How the plan exercises both directions

The measurement vector lists sizes for both directions and the contract requires
single-datagram cases in both, but the traffic goes through a destination that
echoes UDP payloads unchanged. A browser cannot therefore ask for a
server-to-browser size independently: the return size is whatever it sent. One
round trip at `n` bytes consequently exercises the browser-to-server direction at
`n` and the server-to-browser direction at `n` at the same time, and the plan's
single cases are the union of the two direction lists — all 42 sizes of the
committed vector, which happen to be identical in both.

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
in the specification rather than buried in code:

**The 40-byte header is opaque.** Its size and position are fixed; its interior
is not defined by any input this repository is allowed to use, and it was not
reverse-engineered. The contract therefore treats it as a routing prefix that a
client never parses, and the probe takes it as runtime configuration. Of the two
properties the contract requires of it, only one is checkable from a client: the
probe pins the first accepted return prefix, or compares against an
operator-supplied one, and refuses any later frame that differs. That detects
drift and is not a security mechanism — a probe that has never seen the correct
prefix adopts whichever arrives first, and what attributes a datagram to a
session is the payload tag. The other property, that the prefix addresses one
destination on the projected port, cannot be checked at all from bytes the
contract does not parse.

**The 16-byte payload tag is a session nonce plus a datagram ordinal.** The
vector fixes the length, the payload-prefix placement and that shorter payloads
run sequentially, but not the interior. Measurement needs both session identity,
for the concurrent-session isolation evidence, and per-datagram identity, so
several datagrams can be outstanding at once. The tag is therefore 12 random
session bytes followed by a 4-byte big-endian ordinal, assigned in send order
across the whole plan. Every remaining payload byte is the filler `i mod 256`,
which makes an echo byte-comparable.

**Authorization travels in the endpoint URL.** A browser `WebTransport`
construction offers no request-header control, so a browser-presented
authorization can only be in the URL or in the session's first message. This
profile uses the URL through a single operator-supplied `{authorization}`
placeholder, which keeps the parameter name environment-specific and keeps the
token a separate field that is never stored, logged or reported.

**A keep-alive is specified but not implemented, and its frame shape is the
smallest legal frame.** The vector already requires a 0-byte inner datagram to
be a valid measured case, so an idle keep-alive needs no new message type: it is
the routing prefix plus one 0-byte inner datagram, 42 bytes, never sent while a
case is outstanding, with returned 0-byte datagrams consumed against the
outstanding keep-alive count first. The contract says all of that.

The probe **sends none**, and the deterministic round carries no keep-alive
code, configuration field or report counter. A measurement plan is never idle:
a case is always either outstanding or ready to start, so a keep-alive could
never fire during a run. An earlier version shipped the mechanism anyway; it was
unreachable, its counter appeared in every report proving nothing, and an
unanswered keep-alive would have blocked the sequential untagged cases
indefinitely because nothing timed it out. A mechanism that cannot fire is worse
than an absent one, so it was removed rather than left as decoration. Keeping a
session alive matters when a session is *held* open — which this probe does not
do, and which the routed round introduces.

Because the port equality is unverifiable from an opaque prefix, the probe turns
it into an explicit operator acknowledgement and refuses to run without it. That
is a gate, not a check, and the routed round should confirm the port by other
means.

## What the deterministic tests prove

The tests cover the acceptance evidence that does not need a network:

- **Missing runtime configuration** — every required field, unknown fields, a
  non-`https` endpoint, a template without exactly one placeholder, an empty
  authorization, a prefix that is not 40 hexadecimal bytes, a certificate hash
  that is not a SHA-256 digest, out-of-range numbers, and the unacknowledged
  destination port are each refused.
- **Malformed relay frames** — short frames, a frame ending inside a length
  field or a payload, a trailing byte, a server-to-browser frame with none or
  two inner datagrams, a browser-to-server frame with none, a prefix of the
  wrong length, an unknown direction, and a declared length above the accepted
  ceiling. The in-memory relay injects truncated, packed, header-only,
  oversize-declaring and foreign-prefix return frames, and none of them
  completes a case.
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
  infinite or `NaN` round-trip time, a round-trip time on a case that did not echo, an
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
  run, not as a timeout, and the summary treats it as a gap that stops the
  contiguous accepted range rather than as either an acceptance or a refusal.
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
   fixes the exact bytes of every encoded frame, every rejection and every tag
   and payload derivation, with the opaque routing prefix supplied as input in
   each case. A second implementation needs only this file and the
   specification.
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
   Python plan validator unchanged. The comparison also covers the two places
   the languages differ rather than agree by construction: the late untagged
   echo, where both must refuse to attribute the frame, and endpoint
   substitution, where JavaScript's `String.replace` would expand `$&`, `` $` ``,
   `$'`, `$$` and `$1` in the authorization instead of inserting it. These tests
   skip where Node is absent and run in the pinned container image, which ships
   Node 24.

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
self-test passed: 50 conformance vectors, 46 loopback cases
```

That digest is exactly `sha256sum locks/relay-measurement-vector.json`, so the
identity the browser will stamp into a report is the committed WP0 vector and
not some other copy. The line also shows that the ES modules resolve, that
`crypto.subtle` is available, that the browser's own implementation accepts all
50 committed conformance vectors, and that a complete 46-case plan runs through
the in-memory loopback inside the browser.

This is **not** a measurement and involved no relay: no WebTransport session was
opened, and the only network traffic was the loopback page load. It is evidence
that the probe is ready to be pointed at an endpoint, nothing more. Real-browser
acceptance of a routed session belongs to the pending work below.

## Routed acceptance: pending

None of the following has been done, and none of it is claimed:

- running the probe against a real endpoint over at least one routed network
  path;
- observing the browser-reported datagram size, the successful payload range and
  the failure behaviour of a real path;
- confirming that the endpoint rejects invalid authorization and accepts a fresh
  short-lived one for only the configured destination;
- two concurrent sessions with distinct virtual addresses on a real relay;
- a machine-readable measurement report from the exact WP0 browser;
- the conservative payload budget WP6 needs, derived from repeated sessions.

The report format, the summary reduction and the per-session floor are
implemented and tested, so the routed round produces the report by running the
probe rather than by writing new code. The floor is deliberately per session and
carries no safety margin; choosing a margin is WP6's decision, not this
document's.

Two concurrent sessions need two browser contexts, and each context numbers its
own sessions from zero, so their reports cannot simply be concatenated —
identical session indices would collide. `merge_reports` in
[`scripts/relay_probe.py`](../scripts/relay_probe.py) renumbers them in the
order given, refuses inputs that name different measurement vectors, leaves its
inputs untouched, and validates the result; that path is committed and tested.
The routed round therefore produces the concurrent-session evidence as one valid
report without new code.

### What the operator must supply

Per the plan's own list, and refined by what the probe turned out to need:

- a compatible integration relay endpoint, as an `https` WebTransport URL
  template containing exactly one `{authorization}` placeholder;
- a browser-compatible trust mechanism: public Web PKI, or the SHA-256
  fingerprint of an ECDSA P-256 certificate whose total validity is at most 14
  days, as recorded in [`immutable-baseline.md`](immutable-baseline.md);
- a UDP echo destination behind the relay, reachable at the pinned virtual
  destination;
- the 40-byte routing prefix for that destination, and confirmation that the
  destination port it encodes matches the projected endpoint exactly;
- two distinct virtual addresses for the concurrent-session evidence;
- one fresh single-use authorization per session, and enough of them for
  repeated sessions.

None of these values may be committed. The probe takes all of them at runtime,
and the report has no field in which any of them could be recorded.

### Open items the routed round has to settle

- **The relay header's interior.** If the integration environment publishes it,
  the contract can stop treating it as opaque and the probe can verify the
  destination port itself instead of accepting an acknowledgement.
- **How the endpoint expects authorization.** If it is not a URL parameter, the
  specification needs a second presentation and the probe a second code path.
- **Whether a keep-alive is required at all**, and at what interval.
- **Write-failure attribution.** A WebTransport datagram write resolves after
  the datagram is queued, so a rejected write is observed one case late: the
  adapter marks itself failed and refuses later sends, but the case that caused
  the rejection is recorded as a timeout. The size pre-check covers the failure
  mode this probe is measuring; whether the browser ever rejects a write that
  fits `maxDatagramSize` is a question for the routed round.
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
scripts/check.sh                                  # 271 tests, no network
CONTAINER_RUNTIME=podman scripts/check-container.sh
python3 scripts/emit-relay-conformance-vectors.py --check
scripts/serve-probe.sh                            # then open http://127.0.0.1:8173/probe/
```

Opening the probe runs its self-test and reports the measurement vector's
SHA-256, the plan size and the number of conformance vectors and loopback cases
it checked. Without runtime configuration it will not open a session, which is
the intended state until the values above exist.
