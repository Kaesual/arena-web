<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP2 routed-readiness and shared-relay compatibility — 2026-08-30

**Result:** the browser probe's deterministic self-test passed in the
operator-selected Brave browser, but the routed round did not run. The one
available shared relay is a viable basis for both WP2 and the browser ioquake3
client, although the current public probe cannot use it unchanged. Session
authorization and the zero-length boundary case must be reconciled first, and
an integration-only UDP echo destination and fresh one-time authorizations
still have to be supplied.

This report records the guided readiness check performed after WP4 and WP5. It
is a handoff for the WP2 owner; it does not amend the normative contract, close
WP2, or claim a network measurement.

No endpoint, certificate hash, authorization, virtual address, routing prefix,
credential or other environment-specific value is recorded here. Those values
remain runtime-only as required by [`wp2-relay-probe.md`](wp2-relay-probe.md).

## Witnessed browser self-test

The standalone probe was served on loopback and opened in a new temporary
profile of the operator's locally installed Brave browser. The operator
confirmed these exact lines:

```text
measurement vector sha256:546a9a859f92d72d0a2d7dc14acdd80410e0873148500b2d0e26765a6182a064 — 46 cases, ceiling 16384 bytes
self-test passed: 54 conformance vectors, 46 loopback cases
```

This proves that the browser loaded the committed measurement-vector identity,
accepted all 54 portable conformance vectors and completed all 46 cases through
the probe's in-memory loopback adapter. It does **not** prove that WebTransport
opened, that any packet crossed a relay or UDP socket, or that any payload size
works on a routed path.

This guided observation used Brave by the operator's explicit choice. It is not
a new run in the literal WP0 Chrome-for-Testing environment. The WP2 owner must
keep that browser variation explicit if this observation is used as acceptance
evidence; it neither supersedes nor independently verifies any earlier
pinned-browser self-test.

## Why runtime values alone are not enough

The current public probe expects an integration profile with these properties:

- authorization is substituted into an endpoint URL containing exactly one
  `{authorization}` placeholder;
- refusal of invalid authorization happens by refusing or closing that session,
  with no in-band authorization response;
- the 40-byte routing header is supplied as an opaque runtime prefix; and
- a zero-length inner UDP datagram is valid and is echoed like every other
  measured size.

The only available shared relay differs in two load-bearing places. It opens a
normal WebTransport endpoint and performs authorization and virtual-address
assignment in the session's first datagram exchange. It also rejects a
zero-length game-destination sub-datagram rather than sending it to UDP. There
is consequently no truthful URL template or set of runtime values that can make
the unmodified probe complete its approved vector against that endpoint.

The earlier description that WP2 was waiting only for runtime values is
therefore incomplete. The routed round is blocked on a small compatibility
increment as well as on integration provisioning.

## Compatibility findings

Most of the data path already agrees. The following table separates the parts
that can be reused from the parts that need a decision or implementation.

| Concern | Public WP2 profile | Available shared relay | Assessment |
| --- | --- | --- | --- |
| WebTransport data plane | Unreliable datagrams only | Unreliable datagrams | Compatible |
| Game-destination frame | 40-byte relay header followed by `u16`-length-prefixed UDP datagrams | Same framing for non-empty game UDP datagrams | Compatible |
| Browser-to-server packing | One or more inner UDP datagrams | Unpacks one or more records and sends each as one UDP datagram | Compatible |
| Server-to-browser return | Exactly one prefixed UDP datagram per relay frame | Wraps each UDP response in one prefixed return frame | Compatible |
| UDP payload integrity | Each inner datagram remains intact | Payload is forwarded without game-protocol interpretation | Compatible |
| Authorization | URL placeholder before session establishment | In-band first-message authorization | Probe change required |
| Virtual client address | Supplied as runtime context; no negotiation profile | Explicit assignment acknowledgement during session setup | Probe change required |
| Invalid authorization | Handshake refusal or session close | In-band error followed by termination | Probe and acceptance change required |
| Zero-byte UDP case | Valid and required by the committed vector | Rejected on the game-destination path | Relay compatibility change required |
| Idle keep-alive | Zero-byte echo shape specified, but the probe sends none during a measurement | Separate relay/transport keep-alive behavior | Not a measurement blocker once the zero-byte case itself is fixed |
| Trust | Public PKI or a browser-compatible SHA-256 certificate hash | Can be supplied by the integration environment | Compatible runtime input |

The 40-byte interior is no longer merely unknowable integration input: an
ioq3 browser adapter must be able to construct and validate it. If that adapter
is implemented in this public repository, the adopted session and header
profile must be documented here in a self-contained way. The relay
implementation may remain elsewhere; the browser-facing wire contract cannot
depend on access to non-public source or operational documentation.

## What must exist before the routed WP2 round

The smallest coherent compatibility increment is:

1. Extend the public probe transport adapter to open the normal endpoint, send
   the in-band one-time authorization, require the address-assignment response,
   and clear the authorization after the attempt just as strictly as today.
2. Change the invalid-authorization case to require the expected in-band
   refusal and terminal session behavior without putting the authorization or
   endpoint into logs or reports.
3. Adopt a public, self-contained routing-header profile. The probe can then
   construct the outbound header from runtime destination and port values and
   validate the stable return header instead of accepting an unexplained
   80-character hexadecimal blob plus an operator acknowledgement.
4. Make the shared relay's game-destination path preserve a zero-length UDP
   datagram in both directions, or return to plan review and deliberately amend
   the approved vector and contract. Silently skipping the 0-byte case is not a
   conforming measurement.
5. Run a byte-exact UDP echo service behind a temporary integration mapping.
   This is not a second WebTransport relay or a second public endpoint; it is
   merely the controlled UDP destination required by the measurement method.
6. Issue enough short-lived, single-use authorizations for repeated sessions,
   including two distinct virtual client addresses for the concurrent round.
   Each authorization must permit only the echo destination used for the run.
7. Supply endpoint trust at runtime, execute sequential and concurrent sessions,
   merge the two concurrent-context reports with the existing checked tool, and
   validate the final machine-readable report before deriving WP6's conservative
   floor.

No integration value from steps 5–7 belongs in a commit or in the resulting
measurement report.

**Later on the same day:** steps 1 to 4 were carried out. The public contract
and probe adopted the in-band session profile and the self-contained routing
header, the invalid-authorization case became an in-band refusal followed by
session termination, and the shared relay's game path preserves a zero-length
UDP datagram in both directions. The amendment is recorded in
[`wp2-relay-probe.md`](wp2-relay-probe.md). Steps 5 to 7 remain open, and the
observations recorded above are left exactly as they were made.

## ioquake3 compatibility assessment

Using the same shared relay for the browser ioquake3 client is feasible and is
the preferred convergence path. The relay moves opaque UDP payloads; it does
not require the payload to be a Luanti packet. An ioquake3-specific relay server
would duplicate infrastructure and create a second wire behavior to maintain.

The browser fork needs a small WebTransport network backend at ioquake3's UDP
boundary:

- perform the same in-band authorization and virtual-address setup as the
  corrected WP2 adapter;
- preserve each datagram passed to `NET_SendPacket` as one inner UDP datagram;
- create the relay header for the selected virtual server address and port;
- unpack each valid return record and enqueue one intact datagram for
  `NET_GetPacket`; and
- own bounded queueing, reconnect, authorization refresh and keep-alive behavior
  without changing the Quake game protocol.

One unresolved constraint is packet size. The pinned ioquake3 netchan permits a
network packet of up to 1,400 bytes. A single such UDP datagram occupies 1,442
bytes in the current relay profile: 1,400 payload bytes, a 2-byte inner length
and the 40-byte relay header. The routed WP2 report must establish whether the
browser and path can carry that intact; the 16,384-byte vector ceiling is only a
test-plan ceiling and says nothing about the real WebTransport maximum.

The result selects the already approved WP6 strategy:

- if every accepted session provides at least a 1,442-byte outer-datagram
  budget, the ioquake3 adapter can preserve all observed game packets directly;
- if the usable floor is smaller, the native packet census determines whether
  the first profile can still preserve every packet it actually emits;
- otherwise WP6 must choose a symmetric reduction in the matching browser and
  dedicated-server engine pair, or bounded engine-pair tunnel fragmentation.

Changing only the browser client's Quake fragmentation threshold would not be a
safe shortcut because the matching server interprets the same netchan fragment
semantics. Tunnel fragmentation must likewise not be added speculatively: the
repository instructions require the packet census and browser-path measurement
to demonstrate that it is necessary first.

## Recommended ownership and sequence

1. Treat this report as a readiness finding, not as WP2 acceptance.
2. Decide and publish the shared session/header compatibility profile in the
   public contract.
3. Implement the minimal probe and shared-relay zero-length changes and review
   them together against the unchanged conformance vector.
4. Provision the temporary UDP echo path and one-time authorizations without
   committing their values.
5. Repeat the real-browser round, including two concurrent contexts, and retain
   only the validated, redacted measurement report.
6. Let WP6 consume that report together with the WP5 packet census before any
   ioquake3 packet-size or tunnel-fragmentation change is selected.

No existing source or evidence document was changed while producing this
readiness report. This new file records observations and the compatibility
assessment only.
