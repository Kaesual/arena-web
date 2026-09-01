<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP8-Mini two-client acceptance — 2026-09-01

**Result:** WP8-Mini passed. Every multiplayer, reconnect, relay and reduced
network-analysis gate passed. After the completed timed gameplay interval,
client B recorded two transient KDE/Wayland pointer-lock reacquisition denials;
they interrupted neither client and the operator explicitly accepted them as a
KDE/Wayland Mini variation on 2026-09-01.

This report retains only public artifact identities and non-identifying
aggregates. The relay endpoint, certificate hash, authorizations, virtual
assignments, game destination, concrete addresses, qports and UDP source ports
were runtime-only and were deleted after the rehearsal.

## Scope and exact inputs

The operator authorized the Mini amendment on 2026-09-01: one fresh temporary
profile in the exact pinned browser, one normal and one incognito top-level
context on Fedora Linux 44 `x86_64` under KDE/Wayland, at least five minutes of
concurrent active FFA, one real player-vs-player frag, a targeted reconnect of
each client and the reduced network analysis. No network namespace, interface
or route on the workstation was changed.

WP8 changed no owning source or build input and reused the exact accepted WP7
artifacts:

| Input | Identity |
| --- | --- |
| Google Chrome for Testing | `152.0.7977.64` |
| ioq3 `web` engine | `968eeb44294aa0003c430430cf32a6540f9a81e4` |
| Browser artifact manifest | `sha256:8abd6b7a6f7d278ad95c753a5db9f1eff6be8ff08645c2f8ac4d91d7665e3f09` |
| Server artifact manifest | `sha256:640933d6beecd79b88c02d73301de0ab60b7b3037937a690fc4a33f10aeefa1f` |
| Native server image | `sha256:ab6cd95dfed886778be5e5063a9f3669313fed3787d6a71b696e3a170d4f07bf` |
| Content manifest | `sha256:f1e5453e6ecab0b251512cadee8f1a16de446bcc11c9038c93961f045765c7e1` |
| Final-pin WP7 census | `sha256:98888c108cd7a2d17b734c3425f4ec6ef39a5d2e5ffc1777b6c7f3d7b7bde606` |

The server used the committed launch profile, including
`sv_rateLimitPerPort=1`. Its readiness was established by a binary ioquake3
`getstatus` exchange, not by a log substring.

## Witnessed multiplayer and reconnects

The normal context was visibly labelled A and the incognito context B. Both
initial connections completed on their first attempt and reached the same live
FFA arena. Before either planned drop, the measured concurrent interval was
451.3 seconds. The operator moved and fired in both clients, witnessed a real
player-vs-player frag and reported the round green.

The acceptance harness then ended only A's relay transport. A used a fresh
one-time authorization and returned to `running` in 0.268 seconds; the operator
confirmed that A and B were both playable. The same exercise for B completed in
0.292 seconds, after which the operator again confirmed both clients playable.
The other session was never targeted during either exercise. Both reconnects
were well below the 10-second Mini limit.

These were transport-only resumptions beneath each still-running engine and
netchan, not new game joins. The server-side game sessions therefore continued
without a second gamestate; the post-resumption packet flow and the operator's
movement/fire checks establish return to in-game play.

Each context recorded one initial attempt plus one planned reconnect, with two
successful assignments and no additional disconnect. Concurrent assignment of
the two sessions proves distinct virtual addresses because the relay refuses
one address to two live owners. No assigned value is retained.

| Retained checkpoint | Client A | Client B |
| --- | --- | --- |
| Initial measured state | `running` / relay `open`; attempts/reconnects/assignments 1/0/1; enforced inner budget ≥768 (`acceptedInnerFloor=768`, no `path_budget`) | `running` / relay `open`; attempts/reconnects/assignments 1/0/1; enforced inner budget ≥768 (`acceptedInnerFloor=768`, no `path_budget`) |
| A transport resumed | `running`; reconnect ordinal 1; 0.268 seconds | `running` and operator-confirmed playable |
| B transport resumed | `running` and operator-confirmed playable | `running`; reconnect ordinal 1; 0.292 seconds |
| Final measured state | `running` / relay `open`; attempts/reconnects/assignments 2/1/2; enforced inner budget ≥768 (`acceptedInnerFloor=768`, no `path_budget`) | `running` / relay `open`; attempts/reconnects/assignments 2/1/2; enforced inner budget ≥768 (`acceptedInnerFloor=768`, no `path_budget`) |

The reconnect driver retained the completion predicates and times shown above
rather than a third copy of every cumulative counter. The complete
non-sensitive start/final projection of the browser snapshots is:

## Browser and relay counters

| Observation | A start | A final | B start | B final |
| --- | ---: | ---: | ---: | ---: |
| Attempts / reconnects / assignments | 1 / 0 / 1 | 2 / 1 / 2 | 1 / 0 / 1 | 2 / 1 / 2 |
| Inner datagrams accepted / written / received | 55 / 55 / 54 | 29,833 / 29,833 / 20,300 | 7 / 5 / 28 | 29,766 / 29,766 / 20,250 |
| Receive queue depth / high-water / overflow | 256 / 13 / 0 | 256 / 13 / 0 | 256 / 21 / 0 | 256 / 21 / 0 |
| Write queue depth / high-water / overflow / pending | 256 / 4 / 0 / 0 | 256 / 4 / 0 / 0 | 256 / 4 / 0 / 2 | 256 / 4 / 0 / 0 |
| Write failures / invalid returns / cancelled accepted writes | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Originated / elicited refusals | 0 / 0 | 400 / 0 | 0 / 0 | 407 / 0 |
| Engine receive invalid / capacity / poll refusals | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Keep-alives sent / received | 0 / 0 | 202 / 202 | 0 / 0 | 202 / 202 |
| Browser / pointer-lock / WebGL errors | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 2 / 2 / 0 |
| Unexpected files / dropped engine-log lines | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Current / previous terminal reason | none / none | none / `relay_closed` | none / none | none / `relay_closed` |

Every refusal reason except `originated.closed` was zero. A recorded 400 and B
407 closed-session refusals during their respective deliberate gaps. The
engine continued to send while the operator confirmation and controlled
reconnect were coordinated; the backend refused and counted every such write
as designed. These are surfaced planned-gap events, not silent packet loss or
unexpected disconnects.

The Mini runtime explicitly enabled the existing browser backend's
application-level keep-alive at a 5,000 ms interval, producing the paired counts
above. WP7 had accepted interval zero because no keep-alive was needed in its
shorter round. WP8 does not reverse that finding or claim the keep-alive was
necessary; it reports the configured Mini traffic separately from game
datagrams.

The accepted inner floor remained 768 bytes, both final relay states were open,
and neither client recorded a `path_budget` refusal or terminal reason. Together
with the live send-budget enforcement accepted in WP7, this is the Mini
start/end budget observation without claiming a long-run maximum-size stability
measurement.

## Reduced server-side network analysis

The server-network capture began only after both clients had entered the arena
and ended after both reconnects. Its private packet-level input was deleted
after reduction; the retained observations contain no address, port or qport
value.

| Observation | Result |
| --- | ---: |
| Game UDP datagrams | 96,879 |
| Client → server / server → client | 57,199 / 39,680 |
| Maximum game UDP payload, client → server | 147 bytes |
| Maximum game UDP payload, server → client | 624 bytes |
| Distinct client qports | 2 |
| Distinct concurrently live server-facing source ports | 2 |
| Qports observed with a translated source-port change | 0 |
| Relay-visible peer addresses at the server | 1 |
| Post-join connectionless `print` responses | 0 |
| Unknown connectionless commands | 0 |
| Whole netchan headers, client/server | 10 / 8 bytes |

Both directional maxima are below the selected 768-byte inner limit. The two
source-port activity intervals overlap, rather than merely occurring at
different times. Stable one-to-one qport/source-port observation is the packet
evidence that no `SV_PacketEvent` translated-port fixup occurred. With both
initial engine connections succeeding on their first attempt under
`sv_rateLimitPerPort=1`, no connection outcome was attributable to a silent
rate-bucket drop. The post-join capture's absence of a `print` response is not
used to infer that result because the server's rate limiter drops silently.

This post-join capture naturally contains only whole 8/10-byte netchan headers.
The exact unchanged artifact's final-pin WP7 census supplies the matching
fragmented 12/14-byte geometry and four-fragment gamestate. WP8 adds zero queue
overflow, zero invalid/receive refusal and successful live traffic after both
planned reconnects; no failed reassembly or truncation was observed.

The capture's one peer address was the relay. No workstation address was
visible at the game server, and no environment-specific value appears in this
report.

## Accepted browser-behaviour variation

The required timed gameplay interval itself completed without a browser error.
About six seconds after that checkpoint, while the operator changed between
the two top-level KDE/Wayland windows, B twice recorded the browser's
`SecurityError` for requesting pointer lock immediately after it had just been
released. B's explicit `pointerlockerror` counter is therefore 2. No engine,
WebGL, relay, queue or network failure accompanied either event; both clients
remained responsive and the later B reconnect passed.

This is a narrow interaction-timing observation, not a network failure. The
operator explicitly accepted it as a KDE/Wayland Mini variation after reviewing
the recorded count and confirming both clients remained playable. The amended
threshold continues to require zero unexpected or gameplay-affecting browser
errors and requires any immediate pointer-lock reacquisition denial to remain
separately counted rather than silently discarded.

## Review

The focused external GPT-5.6 Sol evidence review initially returned fix-first.
It required the report to distinguish silent rate-limit drops from explicit
responses, transport resumption from a new game join, the post-join capture from
the incorporated WP7 census, WP8's configured keep-alive from WP7's interval
zero, and public evidence from operational detail. Its re-review additionally
required the complete non-sensitive start/final counter projection and removal
of stale historical status wording. After those findings were fixed, the final
read-only review returned **PASS with no findings** and independently confirmed
the public identities, checkpoint evidence, redaction and combined WP7/WP8
packet geometry.

## Cleanup

The acceptance environment was restored to its pre-run state. Temporary
acceptance resources and every retained runtime-sensitive input or packet-level
artifact were deleted. WP9 has not started.
