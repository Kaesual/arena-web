<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP7 routed browser acceptance — 2026-09-01

**Result:** WP7 passed. The exact browser client reached the matching native
server through a compatible shared relay, completed the single-map FFA profile,
survived a deliberate session drop by reconnecting with fresh authorization,
and remained isolated from a concurrent second browser session. The final
engine pin was rebuilt for both endpoints, independently reviewed and
re-censused with every required check passing.

This report records only public, repeatable identities and redacted results.
The relay endpoint, trust material, authorizations, assigned addresses and game
destination were supplied at runtime, kept out of retained browser and server
evidence, and discarded after the rehearsal. No environment-specific value is
committed to this repository.

## Exact accepted inputs

| Input | Identity |
| --- | --- |
| ioq3 `web` engine | `968eeb44294aa0003c430430cf32a6540f9a81e4` |
| WP7 browser implementation/manifest producer | `45d9a7cedb81a9d1c6ac48ee3132f68cf1b455a1` |
| Browser artifact manifest | `sha256:8abd6b7a6f7d278ad95c753a5db9f1eff6be8ff08645c2f8ac4d91d7665e3f09` |
| Server artifact-manifest producer | `fb58dd54bfe8eee196efd4d7a41950021ddcd141` |
| Server artifact manifest | `sha256:640933d6beecd79b88c02d73301de0ab60b7b3037937a690fc4a33f10aeefa1f` |
| Native server image | `sha256:ab6cd95dfed886778be5e5063a9f3669313fed3787d6a71b696e3a170d4f07bf` |
| Content manifest | `sha256:f1e5453e6ecab0b251512cadee8f1a16de446bcc11c9038c93961f045765c7e1` |
| Final-pin packet-census record | `sha256:98888c108cd7a2d17b734c3425f4ec6ef39a5d2e5ffc1777b6c7f3d7b7bde606` |

The browser and server manifests name the same engine commit; the server
manifest additionally names the exact browser-manifest and content identities.
The server image's canonical identity was checked again in the acceptance
environment before it was started.

## Deterministic and review gates

- Two clean browser builds and two clean native server builds passed their
  exact-build verification on the final engine pin.
- `scripts/check.sh` validated the committed metadata and passed all 789 tests.
- `scripts/derive-network-sizing.py --post-change` accepted the final-pin
  census without reopening any WP6 bound.
- An independent ioq3 diff review passed after confirming the symmetric
  fragment-size change and the absence of another fragmentation layer.
- A separate protocol/security review passed after checking authorization
  lifetime, bounded queueing, live send-budget enforcement, failure behavior,
  CSPRNG failure handling, server-browser exclusion, rate-limit bucketing and
  the behavioral harnesses.

## Final-pin native re-census

[`records/wp7-packet-census.json`](../records/wp7-packet-census.json) is the
machine-readable record committed at
`28d22a0d57072e21a0abebba064d0fee8b65514c`. It observed 61,322 UDP datagrams
across the initial connection and a reconnect. All 15 required checks passed:

- the maximum UDP payload was 718 bytes, below the selected 768-byte inner
  budget;
- the largest client and server fragments were respectively 718 and 716 bytes,
  exactly `FRAGMENT_SIZE = 704` plus their 14- and 12-byte fragmented headers;
- each connection's initial server gamestate used four fragments;
- the complete 8/10/12/14-byte netchan header geometry was present;
- no `getinfo`, `infoResponse`, `getstatus` or `statusResponse` appeared in the
  capture, which covered all client UDP destinations rather than only the game
  server port;
- every observed connectionless class fit the selected budget; and
- no media download, fatal engine error, fragment/reassembly error, size
  refusal or unknown connectionless command occurred.

This is the server-side artifact observation required for protocol symmetry.
The successful exact-browser joins and zero failed reassemblies below are the
matching browser-side observation.

## Routed real-browser round

The accepted browser was Google Chrome for Testing `152.0.7977.64` on Fedora
Linux 44 `x86_64`, under KDE on Wayland. WP0 names Fedora Workstation 44 with
GNOME as the default desktop contract; the operator explicitly accepted
KDE/Wayland for this WP7 round on 2026-09-01. This is a WP7 acceptance
variation, not a claim of general KDE, Wayland or cross-browser support.

Two top-level browser windows, visibly labelled A and B, opened independent
relay sessions to the same exact server. Both entered the running arena and
carried game datagrams. The operator used A to join, move, look, fire and score.
The integration control plane then terminated only A's relay session. B remained
connected and live. A reported the terminal `relay_closed` state, invoked its
token-provider hook again, obtained a fresh one-time authorization, received a
new assignment and rejoined. The operator then confirmed movement and firing
in A and continued liveness in B.

| Observation | Client A | Client B |
| --- | ---: | ---: |
| Connection attempts | 2 | 1 |
| Successful assignments | 2 | 1 |
| Reconnects | 1 | 0 |
| Engine datagrams accepted for write | 41,851 | 41,530 |
| Game datagrams received | 3,528 | 3,490 |
| Queue overflows | 0 | 0 |
| Invalid return frames | 0 | 0 |
| Write failures / write-queue overflows | 0 / 0 | 0 / 0 |
| Cancelled accepted writes | 0 | 0 |
| Engine receive refusals | 0 | 0 |
| Browser errors | 0 | 0 |

A recorded 40 `originated.closed` refusals during the deliberate gap between
its targeted session drop and reconnect. Those datagrams were refused and
surfaced as designed; they are not silent loss. Every other refusal reason was
zero for A, and every refusal reason was zero for B. No unexpected relay-defect
counter changed.

The two sessions stayed bounded and responsive for approximately 168 seconds.
No application-level keep-alive was needed, so the accepted interval is zero.
Frame telemetry had a 4.17 ms 95th percentile in each context; isolated maximum
frames were 695.89 ms for A and 741.72 ms for B. These values support WP7's
no-busy-loop observation only. They are not a WP8 performance or endurance
measurement.

Server-side observation counted three distinct relay source ports: concurrent
A, concurrent B and A after reconnect. No actual port number is retained here.
Together with B's uninterrupted session and the two clients' separate relay
telemetry, this demonstrates the WP7 live-port-uniqueness and cross-session
isolation requirements for the exercised topology.

## Scope boundary and cleanup

This round does **not** close or begin WP8. The two contexts ran on one accepted
workstation, not two independent client networks, and the active round was far
shorter than WP8's frozen 15-minute requirement. WP8 remains a separate work
package requiring explicit approval and its complete topology and thresholds.

After the result was captured, the temporary server, route, imported image,
authorization helper, runtime files and evidence containing environment values
were removed from the rehearsal environment. Runtime-only local endpoint,
destination, trust and authorization inputs were also deleted. Only this
redacted report and the separately reproducible final-pin census are retained
as repository evidence.
