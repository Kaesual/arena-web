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
| `probe/relay-framing.js`, `probe/measurement.js`, `probe/adapters.js`, `probe/probe.js`, `probe/index.html` | the standalone browser probe |
| [`scripts/serve-probe.sh`](../scripts/serve-probe.sh) | loopback static server for the probe |
| [`tests/test_relay_probe.py`](../tests/test_relay_probe.py) | 88 deterministic tests, raising the suite from 162 to 250 |

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

## Decisions taken inside the contract

The work-package text and the WP0 measurement vector determine the framing
completely. Four things they leave open were decided here, and each is recorded
in the specification rather than buried in code:

**The 40-byte header is opaque.** Its size and position are fixed; its interior
is not defined by any input this repository is allowed to use, and it was not
reverse-engineered. The contract therefore treats it as a routing prefix that a
client never parses, and the probe takes it as runtime configuration. The two
properties the contract does require of it — that it addresses exactly one
destination and stays constant for the session — are enforced: the probe pins
the first accepted return prefix and refuses any later frame that differs.

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

**A keep-alive is the smallest legal frame.** The vector already requires a
0-byte inner datagram to be a valid measured case, so an idle keep-alive needs
no new message type: it is the routing prefix plus one 0-byte inner datagram, 42
bytes. It is never sent while a case is outstanding, and a returned 0-byte
datagram is consumed against the outstanding keep-alive count before it can
complete a 0-byte case.

One consequence is worth stating plainly: because the routing prefix is opaque,
the probe **cannot** read the destination port out of it and cannot verify the
port equality the contract requires. It therefore demands an explicit operator
acknowledgement and refuses to run without it. That is an acknowledgement, not a
verification, and the routed round should confirm the port by other means.

## What the deterministic tests prove

The 88 tests cover the acceptance evidence that does not need a network:

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
  cross-delivering relay each complete only their own cases, count the other's
  traffic as foreign, and record no unattributed or malformed frame. A corrupted
  echo is reported as a payload mismatch; a corrupted *tag* is reported as
  unattributable rather than being allowed to complete anything.
- **Out-of-range measurement records** — the report validator rejects unknown
  and missing fields, a wrong kind, version or framing block, a non-SHA-256
  vector digest, a sent frame size that disagrees with its inner sizes, a
  returned frame that does not carry the 42-byte overhead, a negative, infinite
  or `NaN` round-trip time, a round-trip time on a case that did not echo, an
  echoed case that returned other sizes, more returned frames than datagrams
  sent, reused ordinals, non-ascending case or session indices, a size above the
  plan ceiling, a case whose kind disagrees with its datagram count, a refusal
  that would have fitted the reported transport maximum, and any case that does
  not match the plan it claims.
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
exceeds the configured bound.

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
2. **A second implementation already exists and is checked against them.** The
   browser probe's JavaScript is a separate implementation of the same contract.
   It is not generated from the Python and does not read it at runtime; it runs
   the committed vectors and a complete loopback plan through its own code
   before the page will open a session, and refuses to connect if anything
   disagrees.
3. **The report format is proven to be shared.** A report produced by the
   JavaScript driver validates against the Python plan validator unchanged,
   including its per-case ordinals, frame arithmetic and outcomes.

The in-memory adapter is published for the same reason. It is not a relay: it
has no authorization, no address mapping and no routing table. It exists so that
the contract can be exercised, and so that its fault settings can produce the
traffic the contract forbids.

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

## Repeating what exists

```bash
scripts/check.sh                                  # 250 tests, no network
CONTAINER_RUNTIME=podman scripts/check-container.sh
python3 scripts/emit-relay-conformance-vectors.py --check
scripts/serve-probe.sh                            # then open http://127.0.0.1:8173/probe/
```

Opening the probe runs its self-test and reports the measurement vector's
SHA-256, the plan size and the number of conformance vectors and loopback cases
it checked. Without runtime configuration it will not open a session, which is
the intended state until the values above exist.
