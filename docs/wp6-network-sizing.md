<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP6: the measured network-sizing decision

**Status:** **complete and closed.** The decision is made, the independent
protocol/security review has passed on re-verification, and no open decisions
remain. WP7 was subsequently approved and closed on 2026-09-01; its result does
not change any sizing decision recorded here.

The operator selected every open point on 2026-08-30,
each one exactly as this analysis proposed: strategy 2 with its profile bounds,
the record-backed 768-byte sizing target giving `FRAGMENT_SIZE = 704`, the
64-byte reserve and alignment, the 512-byte userinfo cap, and the original ten
WP8 thresholds. The operator explicitly superseded only the WP8 acceptance
topology and evidence thresholds with WP8-Mini on 2026-09-01; no sizing value,
profile bound or implementation requirement changed.

**Independent protocol/security review: fix-first, and the fixes are in.** The
reviewer recomputed the whole derivation independently and found no arithmetic
error, endorsed strategy 2 and the 704 selection, and supplied one argument for
it the analysis had missed (the round-trip point, folded into the rationale
below). What failed was completeness of the packet-class census and the
enforceability of two bounds the strategy leans on: an unlisted 1,038-byte
client-originated class on the connect path, a server-browser exclusion that
nothing enforced and the acceptance evidence could not detect, fail-closed
machinery that ran only on the browser while the largest classes are
server-emitted, and the consequences of every player sharing one server-visible
address. All are addressed here and in the WP7/WP8 contracts. **No sizing was
rederived and no decided value moved.**

**The three extensions the review produced are also settled** (operator,
2026-08-30). `+set cl_motd 0` as defence in depth behind the address rule; the
oversize-refusal counter split, so the frozen zero threshold applies to
client-originated refusals while elicited ones are informational; and — chosen
over the accepted-gap draft this document previously carried — a cvar-gated
port-aware rate-limit bucket added to WP7's scope, defaulting to exact upstream
behaviour and enabled in the managed profile.

**Re-verification: pass.** The independent review re-verified the fixes,
confirmed all four MAJOR findings resolved in substance, reproduced every new
citation against the pin, and found no decided value had drifted. Its eleven
remaining findings were documentation consistency rather than substance and are
folded in here; one of them corrected a claim in the opposite direction from the
way it was first reported, which is recorded at the point it applies. **WP6 is
closed.** WP7 later received explicit approval and passed; see
[`wp7-routed-acceptance-2026-09-01.md`](wp7-routed-acceptance-2026-09-01.md).

WP2 measured what a browser can push through the relay. WP5 measured what the
game actually puts on the wire. This document puts the two together, adds the
datagram sizes the engine can emit but a four-minute session never produced, and
asks one question: **can the pinned ioquake3 client and its matching server talk
to each other through this relay, and if not, what is the smallest change that
makes them?**

The short answer is that they cannot as they stand, that the reason is
measured rather than argued, and that one symmetric constant in both engines
plus a short list of profile bounds is enough to fix it. The long answer is
below — and the profile bounds turn out to matter as much as the constant,
because the traffic that does not fit is largely traffic the engine never
fragments.

## What this document decides, and what it does not

It decides the transport strategy, the exact byte, packet and fragment limits
that strategy needs, what happens when a live session reports a budget the
decision did not anticipate, and the implementation and acceptance contracts
that replace WP7's and WP8's scope-gate text. All of that was selected by the
operator on 2026-08-30 and is decided, not proposed. WP8's acceptance-only
contract was later narrowed by the explicit Mini plan change; the independent
review named in the status header could have reopened the sizing decision but
passed. Only later contradictory evidence can reopen it now.

It does not implement anything. No engine source is touched by this work
package; `ioq3/` is untouched at its pinned commit, and no record, lock,
manifest or provenance file changes. Implementation is WP7's.

## Recomputing every number here

Every figure in this document comes out of one deterministic script:

```bash
scripts/derive-network-sizing.py            # the readable derivation
scripts/derive-network-sizing.py --json     # the same numbers as JSON
```

It reads the two committed records, re-validates the routed one against the
committed measurement vector, restates the engine constants with a `file:line`
citation each, and emits the whole arithmetic. It reads no network, no clock and
no Git, so two runs on the same bytes agree.
[`tests/test_network_sizing.py`](../tests/test_network_sizing.py) exercises it
both ways: that it reproduces the records, and — the part that matters — that a
**doctored record changes the verdict**. A derivation that ignored its inputs
would pass the positive tests, so the suite damages copies of the committed
records in ways that would flip a conclusion and asserts that the conclusion
flips: a generous path makes the unchanged engine viable, a smaller reported
maximum shrinks the candidate fragment size, a refused 768-byte case lowers the
floor and everything below it, and a census taken against a different engine is
refused outright rather than silently sized against.

## Inputs

| Input | Identity |
| --- | --- |
| Engine pin (`ioq3/`) | `92351b8f0543448b9defaac25c552274eecbf15b` |
| Engine commit the census was driven at | `588393618dbc82e7207c21c6ddecca229944a03a` |
| Baseline lock | `locks/baseline.json`, SHA-256 `a9126a609d3f041c60c7ca43b3db0e7be8754b9ef0862a6557e8c523038da5e5` |
| Measurement vector | `locks/relay-measurement-vector.json`, SHA-256 `546a9a859f92d72d0a2d7dc14acdd80410e0873148500b2d0e26765a6182a064` |
| WP2 routed measurement | `records/wp2-routed-measurement.json`, SHA-256 `6e410b4051e23ff81010214f8157f71afb64a85f5617d153449c1d3c314b54d3` |
| WP5 packet census | `records/wp5-packet-census.json`, SHA-256 `05103d7c4d56ec8495406d9453ba0d85a69bcddf370071320bda30b925fd5b10` |
| Census server image | `arena-web-server:latest`, image ID `27a307166f2fad40c73a8a4df2c59e5a1f9db13584383296a84ec5306f42dfc2` |
| Census toolchain image | `arena-web-native-toolchain:d36179bb9342033f`, image ID `e472ec1f90ee255dec1d532479fba43cfd893922be29327eb46c98df2d06f4c3` |
| Relay framing | [`docs/relay-datagram-contract.md`](relay-datagram-contract.md) |

**The two engine commits differ, and that is checked rather than waved
through.** The census ran at `5883936`, the upstream base; the current baseline
pin `92351b8` adds exactly one commit on top of it — the WP4 renderer fix,
`renderergl2: use high float precision in GLSL ES shaders`, whose diff touches
`code/renderergl2/tr_glsl.c` and nothing else. No file in the packet path
differs between the two, so the census describes the pinned engine's network
behaviour. The derivation does not take that on trust: it re-states
`FRAGMENT_SIZE`, `MAX_PACKETLEN` and `MAX_MSGLEN` from the sources and refuses a
census whose `engineBounds` disagree, and it re-derives the netchan header
widths and refuses a census whose observed widths disagree. A pin that did move
packet logic would fail the derivation instead of passing quietly.

## The relay's overhead

One relay frame is one WebTransport datagram: a 40-byte header, then each inner
UDP datagram as a 2-byte big-endian length followed by its bytes. A frame
carrying one inner datagram of `n` bytes is therefore `n + 42` bytes, and the
server-to-browser direction carries **exactly one** inner datagram per frame, so
the return direction always pays the full 42 bytes. That overhead is fixed by
the contract and checked in three places, so it is arithmetic and not a measured
unknown.

Every "inner" number below is a UDP payload — an ioquake3 datagram. Every
"frame" number is what the browser hands to the transport.

## The two budgets, and why they are not the same number

The routed round reported the same two figures in all five sessions:

| | Bytes | What it is |
| --- | --- | --- |
| Reported datagram maximum | 1,024 | what the transport said it would carry, sampled at session open |
| **Derived inner budget** | **982** | 1,024 − 42; arithmetic, never exercised |
| **Record-backed inner floor** | **768** | the largest single inner datagram every session echoed, with every smaller planned size also echoed |

The gap between them is the whole reason this document carries two candidates
rather than one. The measurement vector's granularity jumps straight from 768 to
1,024, so **no case was ever sent at any size between 769 and 1,023 bytes**. 768
is a fact about the path; 982 is a subtraction from a number the browser
reported about itself. Both are honest, and they are not equally strong
evidence. The derivation prints the untested range explicitly so that a reader
cannot mistake the second for the first.

Two further properties of that 1,024 are worth carrying forward. It was
byte-identical against a relay on the workstation itself, so it belongs to the
implementations at the two ends rather than to the path between them; and a
separate held-open probe re-read it every 500 ms for six seconds and saw it
constant, so on this path it did not move mid-session.

**The floor is not per direction, and cannot be made so by this method.** Every
measured case is one round trip through an echoing destination, so an accepted
size means the browser-to-server frame *and* the matching server-to-browser
frame both survived, and a refusal does not say which of the two refused it. WP6
asked for per-direction budgets; this methodology cannot supply them. The
decision therefore holds **both directions to the same single budget**, which is
the conservative reading, and records the gap as inherited rather than closed.

## What the engine puts on the wire

### Netchan geometry

Every netchan datagram opens with the sequence number (4 bytes). A client adds
its 2-byte qport (`net_chan.c:123`), the challenge checksum follows (4 bytes,
`NETCHAN_GENCHECKSUM`, `code/qcommon/qcommon.h:191`, written at
`code/qcommon/net_chan.c:129` and `:210`), and a fragment adds `fragmentStart`
and `fragmentLength` (`net_chan.c:137-138`). That gives four header widths, and
the census observed exactly these on 41,833 real datagrams:

| Direction | Unfragmented | Fragmented |
| --- | --- | --- |
| server → client | 8 | 12 |
| client → server | 10 | 14 |

**The checksum is not unconditional, and the profile is what makes these four
widths the only ones in play.** Both write sites and the matching read
(`net_chan.c:270-278`) sit under `#ifdef LEGACY_PROTOCOL` / `if(!chan->compat)`,
and `LEGACY_PROTOCOL` *is* defined in this build: `q_shared.h:52` defines it in
the non-`STANDALONE` branch, neither build script sets `STANDALONE`, and both
built artifacts contain the `com_legacyprotocol` cvar name, which exists only
under that `#ifdef`. The widths above are therefore the **protocol-71
non-compat** path — which is what the census observed on every one of its
41,833 datagrams.

A legacy protocol-68 connection would omit those four bytes, making **every
header 4 bytes smaller**. The direction of safety is one way only: the legacy
path can only shrink datagrams, so every bound derived here stays a valid upper
bound and no sizing conclusion can be broken by it. What the legacy path would
break is elsewhere: the pinned server accepts protocol-68 by default
(`sv_client.c:354-356` sets `compat` when the client's declared version equals
`com_legacyprotocol`, which defaults to 68 via `PROTOCOL_LEGACY_VERSION`,
`qcommon.h:248`), and a legacy connect both bypasses the gamename check
(`sv_client.c:86-92`) and loses the challenge-checksum spoofing protection. The
decision therefore pins the profile to the non-legacy path — see the profile
bounds below — rather than relying on it not being used.

`Netchan_Transmit` fragments when the message length is **greater than or equal
to** `FRAGMENT_SIZE` (`net_chan.c:187`), so at the stock `FRAGMENT_SIZE` of
1,300 (`net_chan.c:52`, defined as `MAX_PACKETLEN - 100`):

| Case | Inner bytes | Frame bytes |
| --- | --- | --- |
| server → client, unfragmented | 1,307 | 1,349 |
| client → server, unfragmented | 1,309 | 1,351 |
| server → client, fragment | 1,312 | 1,354 |
| **client → server, fragment** | **1,314** | **1,356** |

The fragment case binds in both directions. The census's own maximum, 1,312
bytes, is not a coincidence: it is `FRAGMENT_SIZE` plus the fragmented
server-to-client header, which is what makes it a bound this decision can move
rather than a brute fact about the traffic.

### What the census actually saw

41,833 datagrams. Client to server, the largest was 394 bytes and no datagram
was ever fragmented. Server to client, the largest was 1,312 — and **every
server datagram above 311 bytes was one of the four gamestate fragments**. Two
gamestates were sent, of 2,304 and 2,306 bytes, each split into two fragments,
and the model reproduces both their fragment count and their exact total UDP
payload bytes (2,328 and 2,330) from the message length alone. That agreement is
asserted in the suite, not just stated here.

The practical consequence is the cost story for the whole decision: apart from
the gamestate, *nothing this profile sends is anywhere near any candidate
budget*.

### Boundary cases the session could not produce

A four-minute driven session cannot produce the largest datagram the code can
emit, so these are derived from the sources at the pinned commit. All are for
the fixed prototype profile — `sv_maxclients 8`, `sv_pure 0`, no downloads, one
FFA map, the committed QVM and content.

| Class | Direction | Inner bytes | Frame | Fits 768 | Fits 982 | Fragmentable |
| --- | --- | --- | --- | --- | --- | --- |
| `statusResponse` | s → c | **1,443** | 1,485 | no | no | **no** |
| `connect` | c → s | **1,039** | 1,081 | no | no | **no** |
| `infoResponse` | s → c | 1,040 | 1,082 | no | no | **no** |
| **`getmotd`** † | **c → s** | **1,038** | **1,080** | **no** | **no** | **no** |
| `rcon` | c → s | 1,024 | 1,066 | no | no | **no** |
| **`echo`** | **c → s** | **1,022** | **1,064** | **no** | **no** | **no** |
| `getKeyAuthorize` † | c → s | 64 | 106 | yes | yes | **no** |
| `print` (rcon redirect) | s → c | 1,017 | 1,059 | no | no | **no** |
| `print` (rejection) | s → c | 86 | 128 | yes | yes | **no** |
| `challengeResponse` | s → c | 57 | 99 | yes | yes | **no** |
| `getchallenge` | c → s | 40 (ceiling 1,037) | 82 | yes | yes | **no** |
| `connectResponse` | s → c | 31 | 73 | yes | yes | **no** |
| `getinfo` | c → s | 15 | 57 | yes | yes | **no** |
| `getstatus` | c → s | 13 | 55 | yes | yes | **no** |
| netchan fragment | c → s | 1,314 | 1,356 | no | no | n/a — it *is* the fragment |
| netchan fragment | s → c | 1,312 | 1,354 | no | no | n/a |

† Addressed to a **second destination** — see the section below. Their sizes are
listed for completeness, but size is not what makes them a problem.

`getchallenge` is the one row carrying two numbers, because the two differ for a
reason worth seeing: 40 bytes is what this profile realizes, with `com_gamename`
at its `GAMENAME_FOR_MASTER` default — and the derivation reproducing the
census's observed 40-byte maximum exactly is a useful check on the whole method.
1,037 is what bounds the command in general: the `char data[MAX_INFO_STRING + 10]`
buffer it is formatted into (`cl_main.c:2373`), which `Com_sprintf` truncates to.

The derivation of the two large ones:

```text
statusResponse   4  0xffffffff prefix                    net_chan.c:579-582
              + 15  "statusResponse\n"                   sv_main.c:590
              +1023  serverinfo, MAX_INFO_STRING - 1     sv_main.c:541,566
              +  1  separating newline
              +400  8 player lines of 50 bytes           sv_main.c:575-588
              -----
               1443

  player line = 11 (score, "%i" of an unclamped int)
              +  1 + 3 (ping, clamped to 999 at sv_main.c:930-932)
              +  1 + 1 + 31 (name, MAX_NAME_LENGTH - 1) + 1 + 1
              = 50

connect          4  0xffffffff prefix
              +  9  "connect \""                         cl_main.c:2427
              +1023  userinfo, MAX_INFO_STRING - 1       cl_main.c:2372
              +  1  closing quote
              +  2  Huffman worst-case expansion         huffman.c:312-331,421-431
              -----
               1039
```

Two classes in that table need a word about *why* they sit where they do.

**`disconnect` is not out-of-band traffic at all**, which is worth stating
because it looks like it should be. The server drops a client with a reliable
server command inside the netchan (`SV_SendServerCommand`,
`code/server/sv_client.c:680`, `code/server/sv_init.c:723`), and the client's
own disconnect is likewise a reliable command. `SV_ConnectionlessPacket` only
*ignores* an inbound out-of-band `disconnect` (`code/server/sv_main.c:816`). So
it is bounded by the netchan cases, not by the out-of-band ones, and the census
bears this out: the largest datagram in either disconnect phase was 46 bytes.

**The gamestate is fragmented, and this is the traffic the decision is really
about.** `SV_SendClientGameState` (`code/server/sv_client.c:720-782`) builds
into a `MAX_MSGLEN` buffer and hands it to `SV_SendMessageToClient` →
`SV_Netchan_Transmit` → `Netchan_Transmit` (`sv_snapshot.c:577-587`,
`sv_net_chan.c:224-255`), so it takes the ordinary fragmenting path. The census
recorded exactly this: two gamestates of 2,304 and 2,306 bytes, each in two
fragments, the larger datagram 1,312 bytes. It is bounded by `MAX_MSGLEN`
rather than by anything smaller, which is why the cost tables below also carry
the 16,384-byte worst case even though this profile never approaches it. One
caveat found while deriving it and worth recording: unlike
`SV_SendClientSnapshot`, `SV_SendClientGameState` never inspects
`msg.overflowed`, so a gamestate larger than `MAX_MSGLEN` would be silently
truncated rather than reported. The fixed profile's gamestate is two kilobytes,
so this is a note for whoever changes the content, not a live hazard.

Two further findings matter more than the numbers.

**Connectionless traffic is not protected by netchan fragmentation, and the
engine applies no packet-sized ceiling to it at all.** `NET_OutOfBandPrint`
formats into a `MAX_MSGLEN` buffer and sends `strlen` of it
(`net_chan.c:575,589`) — a hard ceiling of 16,383 bytes, an order of magnitude
above any candidate budget and above `MAX_PACKETLEN` itself.
`NET_OutOfBandData`, the `connect` path, reserves *twice* `MAX_MSGLEN`
(`net_chan.c:600`) and copies its input with no length check at all
(`net_chan.c:610-612`). `statusResponse` at 1,443 bytes is not a hypothetical
overflow of some check — it is what the code emits, and it already exceeds
`MAX_PACKETLEN` on a plain UDP path today without anything noticing. Any
strategy that sizes only `FRAGMENT_SIZE` and stops has not addressed this class.

**Some of this traffic is not addressed to the game server at all.** The relay
profile has exactly one destination. Two connect-path classes are addressed
elsewhere, and both were missed by the first version of this analysis because
the question it asked — "can the client originate it?" — was the wrong one:

| Class | Destination | Bytes | Carries |
| --- | --- | --- | --- |
| `getmotd` | update server, `PORT_UPDATE` | 1,038 | challenge, **renderer string**, `com_version` |
| `getKeyAuthorize` | authorize server, `PORT_AUTHORIZE` | 64 | `cl_anonymous`, the **CD key**'s alphanumerics |

`getmotd` is the sharper case. `CL_Connect_f` calls `CL_RequestMotd`
unconditionally, before it does anything else (`cl_main.c:1721`), so it is
client-originated on the connect path with no user action — the exclusion used
for the server browser is simply unavailable. It is compiled in whenever
`STANDALONE` is not defined (`UPDATE_SERVER_NAME`, `qcommon.h:255-256`), which
this document already establishes for both artifacts, and returns early only
when `cl_motd` is zero. At 1,038 bytes it is over both budgets.

Whether either datagram ever reaches the relay is decided entirely by **how WP7
maps addresses in the browser**, and that is the point: a backend that maps any
`netadr_t` onto the pinned virtual destination — the obvious shortcut when there
is only one — would put a 1,038-byte datagram on the path at every connect *and*
hand the player's GPU string to the game server. The privacy consequence is
worse than the sizing one, and neither is visible in the census: that capture
shares the server container's network namespace and filters `udp port 27960`,
and neither `PORT_UPDATE` nor `PORT_AUTHORIZE` is 27960.

`getKeyAuthorize` is small and additionally gated on `com_standalone` being zero
(`CVAR_ROM`, default 0, `common.c:2708`) and on an `NA_IP` server address, which
a virtual-IPv6 relay destination is not. Those gates are real, but they are
properties of an address mapping WP7 has not written yet. Its 64 bytes are not
the reason it is listed; the CD key it carries is.

**The bound is structural, not a size check.** The decision requires WP7 to
refuse and count any send to an address other than the pinned virtual
destination, never silently re-address it. That rule bounds both classes
whatever their size, and it bounds whatever a future engine change adds to this
list — which a per-class size cap would not. The `cl_motd` setting below is
defence in depth on top of it, deliberately not the primary mechanism.

**Compression is not a bound.** The `connect` packet is the one out-of-band
datagram that is Huffman-compressed before it is sent. It usually shrinks, and
the census saw 263 and 292 bytes. But the coder's bit emitter clamps against the
input size while the literal path and the trailing flush do not, so the output
can exceed the input by a small constant — which is presumably why the engine
reserves twice the buffer. The figure above is the pre-compression image plus
that constant. It is derived from the coder's structure, not measured, and that
is precisely why the decision requires the **emitted** size to be checked on the
wire rather than assumed.

One consistency check worth recording: the derived `getchallenge` bound is 40
bytes and the census observed a 40-byte `getchallenge`, because `com_gamename`
defaults to `Quake3Arena` (`q_shared.h:49`, `common.c:2796`). Every other
observed connectionless size is at or below its derived bound, which the suite
asserts.

## Strategy 1 — intact datagrams, no engine change

**Refuted.**

An unchanged engine can emit a 1,314-byte datagram, which needs a 1,356-byte
frame. The transport reported a 1,024-byte maximum. That settles it on the
arithmetic alone, and the record contains the matching attempts:

| Inner bytes | Frame bytes | Outcome in the committed record |
| --- | --- | --- |
| 1,312 | 1,354 | `notSentFrameExceedsTransportLimit` |
| 1,314 | 1,356 | `notSentFrameExceedsTransportLimit` |

**What those outcomes are, precisely.** They are the probe's own pre-send
comparison against the same 1,024-byte reported maximum — not an observation of
the path. Nothing was put on the wire at those sizes, so this is the arithmetic
restated in the record rather than independent corroboration of it, and an
earlier draft of this document oversold it as the latter.

The conclusion is unaffected, because the refusal happens at the endpoint that
matters: the pinned browser *will not write the frame*. A datagram the client
refuses to send is as fatal to strategy 1 as one the path drops, and it fails
earlier and more deterministically. What the record cannot tell us — whether the
routed path would also have refused a 1,356-byte frame had the browser offered
it — the decision does not need and does not claim.

And the failure is not a rare peak. The census's **observed** maximum, 1,312
bytes, is a gamestate fragment, and the gamestate is the first thing the server
sends after `connectResponse`. An unchanged engine pair would fail during the
connection handshake of every single session, not occasionally under load.

Even at the optimistic 982-byte budget the unchanged engine does not fit; at the
record-backed 768 it is not close. There is no version of this strategy that
survives, and no safety margin that rescues it.

## Strategy 2 — a symmetric documented fragment-size reduction

This is the contract's second preference, and the evidence supports it.

### Why it must be symmetric

`FRAGMENT_SIZE` is not a local send-side tuning knob: it is a **protocol
constant**. The sender stops fragmenting when a fragment comes out shorter than
`FRAGMENT_SIZE` (`net_chan.c:162`), and the receiver decides that more fragments
follow by testing `fragmentLength == FRAGMENT_SIZE` (`net_chan.c:373`). There is
no negotiation and no field carrying the value. Lower it on one side only and
reassembly breaks silently: the receiver treats the first short fragment as the
end of the message and delivers a truncated gamestate. This is exactly what the
WP6 contract's non-goal "quietly lowering only one endpoint's packet sizing"
forbids, and it is why the browser client and its matching native server must be
built from the same engine pin with the same value.

`FRAGMENT_SIZE` is file-static to `net_chan.c` and appears in no header, so the
complete set of places that read it is six lines in one file:
`net_chan.c:33` (comment), `:52` (definition), `:132`, `:162`, `:187` (send) and
`:373` (receive).

**Symmetry has to be observed on artifacts, not asserted in source.** Because
the constant lives in exactly one file, a source-level test that "client and
server agree" compares the value with itself and passes unconditionally. The
failure that actually matters — a deployed server binary that did not come from
the final pin, or a browser artifact built before the change — is invisible to
it. Two artifact-level observations cover it instead. The re-census pins the
**server** artifact: the observed fragment geometry (largest fragment datagram =
`FRAGMENT_SIZE` plus the fragmented header) is a direct measurement of the value
that binary was built with. WP8's zero-failed-reassembly evidence pins the
**browser** artifact, because a browser built with a different value cannot
reassemble the server's gamestate at all. The distinction matters because WP5's
methodology is native-client-against-native-server, so the re-census on its own
says nothing about the browser.

### Invariants a change must preserve

- `MAX_PACKETLEN >= FRAGMENT_SIZE + 14`. `MAX_PACKETLEN` sizes the netchan send
  buffer (`net_chan.c:111`, `:179`) and the 14 bytes are the widest netchan
  header. Reducing `FRAGMENT_SIZE` only widens this margin, so the existing
  `MAX_PACKETLEN` of 1,400 stays valid and **should not be changed**: it is also
  the loopback message buffer (`net_chan.c:427`) and feeds rate accounting, and
  changing it would move more than this decision has measured.
- An overrun of the send buffer would be **silent** — `MSG_WriteBits` sets
  `overflowed` and drops the write (`msg.c:123-124`) and
  `Netchan_TransmitNextFragment` never inspects it. The invariant above is what
  keeps that unreachable; it is not defended by a check.
- `fragmentStart` and `fragmentLength` are 16-bit signed on the wire
  (`net_chan.c:137-138`, `:282-283`), so `FRAGMENT_SIZE` must stay ≤ 32,767.
  Every candidate is far below that.
- Reassembly guards (`net_chan.c:358-365`, `:377-382`) are sized off
  `MAX_MSGLEN` and remain correct for any `FRAGMENT_SIZE` below it.

### The candidate values

The rule is arithmetic rather than judged, so a reviewer who disagrees with the
margin can change one number and recompute: take the budget, subtract a
**64-byte reserve** — the 14-byte widest header plus 50 bytes of deliberate
headroom — and round down to a multiple of 64.

| Target | Budget | `FRAGMENT_SIZE` | Largest datagram | Largest frame | Margin |
| --- | --- | --- | --- | --- | --- |
| Record-backed floor | 768 | **704** | 718 (c → s fragment) | 760 | 50 bytes |
| Derived reported maximum | 982 | **896** | 910 (c → s fragment) | 952 | 72 bytes |

A note against an easy misreading: the 768-byte floor is an *inner datagram*
size — the round trip that established it put an 810-byte frame on the wire and
got it back. Sizing the **frame** to 768 instead would be more conservative than
the evidence asks for, and is not what these candidates do.

### What each candidate costs

| | at 1,300 (stock) | at 896 | at 704 |
| --- | --- | --- | --- |
| 2,304-byte gamestate | 2 fragments, 2,412 wire bytes | 3 fragments, 2,466 | 4 fragments, 2,520 |
| 2,306-byte gamestate | 2 fragments, 2,414 | 3, 2,468 | 4, 2,522 |
| `MAX_MSGLEN` message (16,384) | 13 fragments | 19 fragments, 17,410 | 24 fragments, 17,680 |
| Ordinary traffic that begins to fragment | — | **none** | **none** |

The last row is the important one. The largest non-fragment message the census
observed was 384 bytes client to server and 303 server to client, both far below
either candidate, so neither value causes anything except the gamestate to
fragment. The whole cost of the conservative candidate is **two extra datagrams,
once, during connect** — 108 additional wire bytes on a 2.4 KB transfer.

### Profile bounds the strategy also needs

A fragment-size change does nothing for out-of-band traffic. **Seven classes are
over budget at both targets** — `statusResponse`, `infoResponse`, `connect`,
`getmotd`, `rcon`, `echo` and the rcon redirect's `print` — plus
`getKeyAuthorize`, which fits comfortably and is listed anyway because size is
not why it matters. They need five treatments, grouped below, and a sixth item
follows them: the legacy protocol path, which is not a sizing problem at all but
belongs here because it is the other thing the profile has to pin.

The grouping turns on *why* a class is or is not on the path, and the first
version of this analysis got that taxonomy wrong in a way worth stating. It
asked one question — can the client originate it? — and excluded everything that
answered no. There are three kinds:

- **Client-originated and unavoidable** (`connect`): must be capped.
- **Triggered by the destination** (`echo`): no statement about what the client
  chooses to send can exclude it, which is why the emitted-size check is a
  requirement rather than a precaution.
- **Addressed somewhere else** (`getmotd`, `getKeyAuthorize`): excluded by
  destination, not by size or origin — and `getmotd` is *also* client-originated
  on the connect path, so the first question would have said "yes" and the
  original analysis still missed it.

1. **`connect` — the one that must be solved.** It is the largest datagram the
   client can originate and the connection cannot happen without it. Its only
   variable term is the userinfo, and the fixed part is 16 bytes, so the cap is:

   | Target | Userinfo cap | Observed in the census |
   | --- | --- | --- |
   | 768 | **752 bytes** | the whole packet was 263 and 292 bytes |
   | 982 | 966 bytes | — |

   The cap is comfortable: the profile's actual userinfo produces a packet under
   300 bytes, so a 752-byte allowance is roughly 2.5× headroom. **Decided:** a
   cap of **512 bytes** of userinfo — well inside the selected target's 752-byte
   limit and still far above what the profile uses, and deliberately a number
   that would also have held at the 982-byte budget.

2. **`statusResponse` and `infoResponse` — excluded, and the exclusion has to be
   made real.** These are the server browser's answers, and the prototype
   profile has no server browser: the browser client is launched at one pinned
   virtual destination and has no reason to issue `getstatus` or `getinfo`.

   An earlier version of this document called that "an enforced constraint".
   It was not one, and the correction matters. Nothing in the engine, the
   profile or the acceptance evidence enforced it: there is no cvar, no removed
   command and no test, and the derivation's own
   `server_browser_queries_on_relay_path=False` is a switch in the analysis, not
   in the client. Worse, the record this decision sizes against *contains* these
   classes — the WP5 census deliberately issued both queries, so `getstatus`,
   `getinfo`, `statusResponse` and `infoResponse` all appear in it. And a size
   assertion cannot catch a violation: with this profile's short serverinfo and
   few players the realized `statusResponse` is 465 bytes, comfortably under
   768, so "no connectionless class over budget" would pass whether or not the
   browser is issuing queries.

   Until WP7 lands it is therefore an **assumption**, and the decision names the
   two mechanisms that convert it into a constraint: WP7 neutralizes the
   client's `ping`, `serverstatus`, `localservers` and `globalservers` command
   paths in the `web` branch, and the re-census asserts **presence** — that no
   `getstatus` or `getinfo` datagram appears at all — rather than asserting size.

   For completeness, if the operator ever wanted them on the path,
   `statusResponse` would need the serverinfo capped to 348 bytes at the 768
   target and 562 at 982.

3. **`echo` — the one that cannot be excluded.** The client answers an
   out-of-band `echo` from its own server address by sending the argument
   straight back (`cl_main.c:2784-2791`). The trigger belongs to the
   destination, so a compromised or merely chatty server can elicit a
   client-to-server out-of-band datagram of up to 1,022 bytes: the read line is
   truncated to `MAX_STRING_CHARS - 1` by `MSG_ReadStringLine`
   (`msg.c:508,526`), the command token and its space consume five of those, and
   the reply is the argument plus the 4-byte prefix.

   There is a mitigating property — on the relayed path the eliciting datagram
   is itself bounded by the transport, and the reply is strictly *smaller* than
   what elicited it, so a conforming relay makes this fit automatically. But
   that argument leans on the inbound and outbound budgets being the same
   number, and WP2's methodology explicitly could not attribute a budget per
   direction. It is therefore not a bound this decision may rest on.

   **Decided:** leave the handler in place and let WP7's fail-closed
   emitted-size check refuse an oversize reply, counted like any other refusal.
   Losing an `echo` answer costs the session nothing — it is a diagnostic
   courtesy, not part of the protocol — so a counted, harmless drop is a better
   outcome than a code change to the connectionless path. The alternative that
   was considered and not selected, disabling the handler outright in the `web`
   branch, remains sound: a slightly larger engine change for a slightly smaller
   attack surface.

4. **`rcon` and its answer — excluded.** The profile sets no rcon password and
   the browser client exposes no rcon command, so neither the 1,024-byte request
   nor the server's reply can be elicited. The reply is worth naming beside it:
   `SV_FlushRedirect` prints accumulated command output out of band bounded by
   `SV_OUTPUTBUF_LENGTH` (1,008 bytes, `sv_main.c:696-698,714`), giving a
   1,017-byte server-to-client datagram. `rcon` itself is also the one class that
   bypasses `NET_OutOfBandPrint` and calls `NET_SendPacket` directly
   (`cl_main.c:1890`), so a size check placed at the out-of-band send would not
   see it. A future profile enabling rcon must revisit both.

5. **`getmotd` and `getKeyAuthorize` — refused by destination.** Covered in full
   above. The structural bound is WP7's address rule: a send to anything but the
   pinned virtual destination is refused and counted, never re-addressed.
   **Decided:** `+set cl_motd 0` on the client as defence in depth. It carries
   an honest caveat rather than a claim: `cl_motd` is registered with flags `0`
   (`cl_main.c:3562`), so unlike `com_legacyprotocol` it can be changed after
   start. It cannot carry this bound on its own, and is not asked to — the
   address rule is the primary mechanism and this sits behind it.

6. **The legacy protocol path — refused.** Not a size problem: a compat
   connection only makes datagrams smaller. But the pinned server accepts
   protocol-68 by default, and doing so bypasses the gamename check and drops
   the challenge-checksum spoofing protection, while also making the header
   geometry this document derives no longer the only one on the wire.
   **Decided:** both server and client launch with `+set com_legacyprotocol 0`,
   so the legacy path is refused outright and the census's observed geometry is
   the only one the profile can produce.

   This is a stronger bound than a launch flag usually is, and worth saying so
   plainly, because the difference between a bound and a hope is exactly what
   this section is about. The cvar is registered `CVAR_INIT` (`common.c:2799`),
   so it can only be set on the command line and cannot be changed afterwards by
   console, config file or game module. With it at zero, `SV_DirectConnect`
   takes the non-compat branch for any declared version (`sv_client.c:354-367`),
   and the gamename bypass at `sv_client.c:86-92` is itself gated on
   `com_legacyprotocol` being non-zero, so zero closes that too. It is also
   observable after the fact — in the `protocol` ROM cvar
   (`common.c:2802-2806`) and in the header widths the re-census measures.

### The check runs on both endpoints, at one named seam

Two corrections to how the fail-closed machinery was originally specified.

**It is not browser-only.** The first version put the emitted-size check and its
counters on "the browser's send path". But every class that remains over budget
in the *other* direction — `statusResponse` at 1,443, `infoResponse` at 1,040,
the rcon redirect's `print` at 1,017 — is emitted by the **native server**, and
those are the largest classes in the whole table. Under a browser-only check,
a wrong assumption there produces a datagram the server emits, the relay or the
transport drops, and nothing counts. WP8's "zero oversize refusals, one refusal
means a class was missed" could not detect a missed class in the direction where
the biggest classes live.

There is a second, sharper reason. The exclusion for `statusResponse` and
`infoResponse` is that *this client* never asks — but the server answers whoever
asks (`sv_main.c:802-805`), and on the relayed path it sees one IPv4 endpoint
with a source port per session. Anything that reaches the server socket bearing
a session's source port elicits a server-to-client out-of-band datagram into
that browser's session. That is precisely the reasoning this decision applied to
`echo`, and it was not applied symmetrically. **The same emitted-size check and
the same per-class counters therefore run in the native server build**, which is
being rebuilt from the final pin anyway. A server-side over-budget emission is a
counted, surfaced event, not a silent drop.

**The seam is `Sys_SendPacket`.** "The send path" was underspecified, and the
two obvious call sites are each wrong in one direction: `rcon` bypasses
`NET_OutOfBandPrint` entirely, and the `connect` packet is compressed *inside*
`NET_OutOfBandData`, so a check at either would miss traffic or measure the
pre-compression size. `Sys_SendPacket` is the only point below both, and below
the `cl_packetdelay`/`sv_packetdelay` queue (`net_chan.c:528,550-566`). Putting
the check there makes "after compression" automatic rather than a rule someone
has to remember, and it makes the check total for everything that leaves the
process. Not quite *every* datagram the engine emits: `NET_SendPacket` returns
early for `NA_LOOPBACK`, `NA_BOT` and `NA_BAD` before reaching this seam
(`net_chan.c:544-553`), so loopback traffic goes to `NET_SendLoopPacket` and is
never sized — consistent with that function's existing entry in the inherited
gaps below, and harmless because nothing on a relayed path is loopback.

### Where the change lands

| Component | Change |
| --- | --- |
| `ioq3` (`web` branch) | `FRAGMENT_SIZE` becomes an explicit constant at the selected value, documented in place with the reason and the symmetry requirement. `MAX_PACKETLEN` unchanged. |
| `ioq3` (`web` branch) | The fail-closed emitted-size check at `Sys_SendPacket`, in **both** builds, with per-class and per-direction counters split into client-originated and elicited. |
| `ioq3` (`web` branch) | The cap on the formatted `connect` data string, refuse-and-surface rather than truncate. |
| `ioq3` (`web` branch) | Refuse and count any send to an address other than the pinned virtual destination; never re-address one. |
| `ioq3` (`web` branch) | Neutralize the `ping`, `serverstatus`, `localservers` and `globalservers` command paths, so the server-browser exclusion becomes structural. |
| `ioq3` (`web` branch) | Browser `Sys_RandomBytes` backed by `crypto.getRandomValues`, because `qport` quality is what separates two players on the relay's shared address. |
| Profile (launch arguments) | `+set com_legacyprotocol 0` on both server and client; `+set cl_motd 0` on the client. |
| This repository | The engine pin, and the WP7 evidence recording the rebuild and re-census. |
| Native server | Rebuilt from the same final `web` pin — mandatory, because `FRAGMENT_SIZE` is server-side packet logic. |

### What the shared relay address does to the server's per-address logic

The relay collapses every player onto one server-visible IPv4 endpoint, with a
source port per session. That is the privacy property the whole project exists
for, and the server was not written for it. Three consequences, none of which
changes the sizing, all of which touched the original frozen WP8 thresholds.

**Rate limits are shared.** `SVC_BucketForAddress` keys its leaky buckets on the
address bytes and never the port (`sv_main.c:405-427`), so both players share
**one** bucket. `getchallenge` is limited to burst 10 per 1,000 ms per address
(`sv_client.c:71-75`), as are `getstatus`, `getinfo` and `rcon`. One session
reconnecting in a loop can therefore deny `getchallenge` to the other — directly
against the original frozen "100% of connect attempts within 3 attempts" threshold, and
by a mechanism that has nothing to do with either client misbehaving.

**qport becomes the only discriminator.** `SV_PacketEvent` demultiplexes on
(base address, qport) alone (`sv_main.c:851-862`). On a shared address a qport
collision makes one client's datagram match the other's slot — and the server
then rewrites the stored remote port to the incoming one (`:867-870`)
**before** the netchan checksum is validated (`net_chan.c:270-278`, via
`SV_Netchan_Process` at `sv_main.c:873`). The checksum stops the *content* being
accepted; it does not stop the port rewrite, which has already happened. The
victim's netchan is re-homed to the wrong relay port and subsequent server
traffic is delivered into the wrong browser session — which would violate WP7's
own "no packet is delivered across browser sessions".

**So qport quality is a security property, not a detail.** It comes from
`Com_RandomBytes` (`common.c:2816-2818`), which falls back to time-seeded
`rand()` when `Sys_RandomBytes` fails. A browser port must reimplement
`Sys_RandomBytes`; stubbed or weakly seeded, two clients started in the same
second get the same qport and correlated `clc.challenge` values — degrading the
very challenge-checksum protection this document leans on when it pins the
non-legacy path. **Decided:** WP7's browser `Sys_RandomBytes` is a real CSPRNG
(`crypto.getRandomValues`), and WP8's evidence shows the two clients' qports
differ.

**The shared bucket is fixed, not accepted.** An earlier draft recorded the
rate-limit sharing as an accepted inherited gap on the grounds that scoping the
buckets was engine work this decision had not authorized. **Decided by the
operator on 2026-08-30:** extend WP7's scope with the engine change instead.

The reasoning is about scale rather than about the acceptance run. The two-player
WP8 topology would rarely trip a burst of 10 per second, so as an acceptance
risk this is marginal — but the product's real shape is 5 to 10 players behind
one relay, and at that size a single client reconnecting in a loop stalls
*every* other player's connect and reconnect. That is a product defect wearing
an acceptance-threshold costume, and fixing the mechanism is worth more than
accepting it and hoping WP8 does not see it.

The change is deliberately small and off by default:

- `SVC_HashForAddress` (`sv_main.c:376-396`) mixes the source port into the hash,
  and `SVC_BucketForAddress` (`:405-427`) compares it as well as the address
  bytes, which means `leakyBucket_s` (`server.h:313-328`) gains a port field and
  stores it when a bucket is created.
- All of it sits behind **one new cvar, defaulting to off — that is, to exact
  upstream behaviour** — and the managed launch profile turns it on with `+set`.
  A sensible name is `sv_rateLimitPerPort`; WP7 finalizes the exact spelling.
- Only the per-address limiter is affected: `SVC_RateLimitAddress`
  (`sv_main.c:518-521`), reached by `getchallenge` (`sv_client.c:71`),
  `getstatus` (`sv_main.c:549`), `getinfo` (`sv_main.c:612`) and `rcon`
  (`sv_main.c:719`). The global `outboundLeakyBucket` is not per-address and
  does not change.

**Why the default stays upstream, and why turning it on here is safe.** On the
open internet, keying rate limits by source port is a mistake: an attacker
simply varies the port and evades the limit entirely. That objection is exactly
why upstream keys on the address, and why this must not become the default for
anyone building on this engine. It does not apply to this deployment, because
the game server is reachable **only** from the relay — every packet it sees has
already been authorized and forwarded, so the source port is assigned by the
relay rather than chosen by an attacker. A cvar defaulting to upstream behaviour
keeps that reasoning local to the environment where it holds.

**Two further things the cvar's warning must carry.**

*It is a trade, not pure gain.* Keying on the port also means a client whose
reconnect arrives on a **fresh** relay source port is no longer limited where
upstream would have limited it — the reconnect throttle that per-IP bucketing
gave for free is weakened in exactly the case the change is designed to help.
That is acceptable here, because the relay authorizes every session before it
forwards anything and the threat model is one player stalling another rather
than an anonymous flood. But it is a trade and the documentation should say so
rather than presenting port-keying as strictly better.

*Bucket exhaustion fails closed, into a denial.* The table holds `MAX_BUCKETS`
= 16,384 live buckets (`sv_main.c:364`), reclaimed only after `burst * period`
of inactivity. Port-keying creates a bucket per **session** rather than per
address, so it moves the table meaningfully closer to full. When it is full,
`SVC_BucketForAddress` returns `NULL` (`sv_main.c:478`) and `SVC_RateLimit`
skips its whole body and returns `qtrue` (`:486-507`) — and every caller reads
`qtrue` as "drop this request". So exhaustion does not disable the limiter; it
**denies connectionless requests for everyone**, including `getchallenge`, which
is the connect path. Harmless in this deployment only because relay-only
reachability bounds the number of live source ports by the number of authorized
sessions, which is orders of magnitude below 16,384 — and that reasoning is
precisely what stops being true if anything else is ever allowed to reach the
server, so it belongs in the warning rather than in this document alone.

**The long-term direction, unscheduled and deliberately not coupled to WP7.**
The cleaner fix is topological rather than engine-side: give the relay's
server-facing leg a **per-player virtual IPv6 source address** — the same
address model the wider stack already uses — so each player reaches the game
server from a distinct address. That would restore per-address bucketing exactly
as upstream designed it, making the cvar unnecessary, and it would dissolve the
qport-only demultiplexing below as well, since `SV_PacketEvent` would once again
have distinct base addresses to separate clients by. It is a relay and topology
change outside this repository and outside WP7, recorded here as the direction
of travel and not scheduled.

**The qport collision hazard is not fixed by any of this.** The bucket change
touches rate limiting only; the demultiplexing and the port rewrite are
untouched, so the hazard above remains a documented residual gap mitigated by
requiring a real CSPRNG for `qport`. Only the IPv6 leg would remove it. WP8's
distinct-qports evidence requirement therefore stands.

**And there is a constraint on the relay that this decision must state, because
nothing in the engine can enforce it.** `SV_DirectConnect` matches an existing
client slot on the base address **and (qport OR source port)** — the disjunction
is the point, at `sv_client.c:373-379` for the reconnect throttle and `:457-465`
for slot reuse, and WP5's census document already records the behaviour. So if
the relay ever assigns a **live** session's server-facing source port to a
*different* session, the server matches the new session onto the first player's
slot on the port alone, however good the new session's qport is. The CSPRNG
requirement does not help here: it makes qport collisions unlikely, and this
path needs no qport collision at all.

**The relay must therefore never reuse a live session's server-facing source
port for another session.** It is a relay-side property, outside this repository
and outside the engine, stated here and in the WP7 contract because it is the
kind of assumption that stays invisible until it fails. It also reframes WP8's
distinct-source-ports evidence: that has been listed as *privacy* evidence, and
it is equally **correctness** evidence — two live sessions sharing a
server-facing port is a deterministic slot collision, not a privacy nicety.

## Strategy 3 — bounded engine-pair tunnel fragmentation

**Not required, and therefore not selected.** The derivation says so explicitly
rather than by omission: once `FRAGMENT_SIZE` fits the budget, every netchan
case fits at both targets, and netchan traffic already has fragmentation and
reassembly built into the engine. Adding a second, independent fragmentation
layer underneath it would duplicate machinery that works.

The only traffic a tunnel could help is the out-of-band classes the profile
cannot exclude — `connect` and `echo` — which the engine never fragments. For
those, bounding the profile is a strictly smaller change than adding a
reassembly path, a reassembly buffer, a timeout and a fragment-loss policy to
both endpoints, each of which is new attack surface on a path that carries
unauthenticated pre-connection traffic. It would also not help `echo` in the way
one might hope: reassembling a diagnostic reply is a strange thing to build when
dropping it costs nothing.

**The condition under which it comes back** is worth stating precisely, because
it is the one thing that would reopen this: a tunnel becomes necessary if the
operator rejects the profile bounds — that is, if the relayed path must carry
arbitrary userinfo, a server browser, an `echo` reply that must not be dropped,
or any other out-of-band class that cannot be bounded below the budget. If that happens, WP6 reopens; WP7 must not
improvise it.

**Stream-assisted game traffic** remains out of scope: the WP6 contract makes it
a separately owned shared-relay change requiring a new cross-repository plan and
review, and this document neither selects nor designs it.

## The decision

**Decided by the operator on 2026-08-30:** **strategy 2, the symmetric
fragment-size reduction**, sized to the **record-backed floor of 768 inner
bytes**, giving **`FRAGMENT_SIZE = 704`**, together with the profile bounds
above. The reserve stays at 64 bytes with 64-byte alignment, the userinfo cap at
512 bytes. The current, operator-amended WP8-Mini thresholds are tabulated
below; they do not alter this transport decision.

The reasoning that was put to the operator, for the conservative target over the
permissive one:

- 768 is the only number the record demonstrates actually carried traffic
  end-to-end. 982 is a subtraction from a self-reported value, inside a range
  the round never sent a case in. The WP6 contract's own non-goal — "treating a
  single browser session's reported maximum as a universal constant" — points
  the same way.
- **768 is demonstrated in both directions; 982 is a send-side property only.**
  Every measured case was a round trip through an echoing destination, so a
  768-byte inner datagram is known to have survived browser-to-server *and*
  server-to-browser. The derived 982 comes from `maxDatagramSize`, which the
  browser reports about its own transmit path and nothing else. This is the
  single strongest argument for the conservative target and the analysis had
  missed it: the round-trip cases are the **only** evidence in the entire record
  that speaks to the server-to-browser direction at all. It also bears on the
  mid-session rule below, which re-reads that same send-side property and uses
  it to bound both directions — conservative here precisely because the floor it
  is compared against was established both ways.
- The cost of being wrong is asymmetric. Choosing 704 and being too conservative
  costs two extra datagrams per connect, once. Choosing 896 and being wrong
  means the gamestate does not arrive and no session can start at all.
- The measured cost of the conservative choice is negligible: 108 extra wire
  bytes on one transfer per connection, and nothing else in the profile changes
  its fragmentation behaviour at all.
- 704 also survives a path that turns out to be tighter than this one. The
  replay confirms the 704 worst case is still carried at a transport maximum of
  810 bytes, where the 896 worst case is not.

### The road not taken

**Considered and not selected: `FRAGMENT_SIZE = 896` at the derived 982-byte
budget.** The counter-argument was real and is kept here rather than deleted,
because a later path that behaves differently is the circumstance under which it
would be revisited. If the 1,024-byte reported maximum is a stable property of
this browser and this relay — and the evidence that it was byte-identical on
loopback and constant across six seconds of a held session is genuine — then 896
would leave a smaller gamestate burst and a little more headroom per datagram
for later engine changes. It was rejected because 982 is a subtraction from a
self-reported number inside a range no case was ever sent in, and because the
failure mode is asymmetric: too conservative costs two datagrams per connect,
too optimistic costs every session.

The derivation deliberately still computes both targets side by side, so this
alternative stays recomputable rather than becoming a historical claim.

### What the operator settled

| Point | Decision |
| --- | --- |
| Strategy | Strategy 2, symmetric fragment-size reduction, with the profile bounds |
| Sizing target | Record-backed 768-byte inner floor → `FRAGMENT_SIZE = 704` |
| Reserve / alignment | 64 bytes / 64 bytes |
| Userinfo cap | 512 bytes |
| WP8 thresholds | Original ten selected on 2026-08-30; explicitly replaced by the acceptance-only Mini table on 2026-09-01, retaining the 768-byte budget, zero failure/refusal bounds and 256-datagram receive queue |

Every sizing value was selected as proposed by this analysis. The original WP8
table was likewise selected unchanged, then explicitly replaced by the later
operator-owned Mini acceptance amendment above; no sizing value moved with it.

## Behaviour when the live budget is not what was measured

The record shows a constant 1,024 on the measured path, but the contract
requires the specification regardless, and a browser is entitled to report
something else. These rules are **decided** as part of the selected strategy and
belong in WP7's implementation:

- **Read the transport's reported maximum at session open, and again on every
  send.** WP2's probe sampled it once, which its own open-items list records as
  a caveat; the game client holds sessions open for a quarter of an hour or
  more, so it re-reads. This costs a property read per datagram and removes the
  entire class of stale-budget failures.
- **Compute the accepted inner budget as `reported maximum − 42`** and compare
  every outbound frame against the reported maximum before writing it.
- **If the accepted budget is at or above the selected floor**, operate
  normally. `FRAGMENT_SIZE` is fixed at build time and cannot adapt, so a larger
  budget buys headroom, not larger datagrams.
- **If the accepted budget falls below the selected floor**, the session cannot
  carry the engine's traffic. Note what this threshold is: it is the sizing
  target (768), not the requirement (718, the largest datagram the build can
  actually emit). A session reporting an 800-byte maximum — budget 758 — is
  refused even though every datagram this build can produce would have fitted.
  That is deliberate and it is the conservative direction, but it means **the
  64-byte reserve is doing double duty**: it is the margin below the budget when
  choosing `FRAGMENT_SIZE`, and it is also a hard session-refusal margin at
  runtime. An operator who later wants sessions accepted down to the true
  requirement is changing the second use, not the first. **Fail closed**: refuse to send, surface a
  distinct, non-retrying error to the loader, and close the session. Do not
  truncate, do not silently drop, and do not fall back to another transport —
  a fallback would be a transport mechanism this decision did not authorize.
- **If it changes mid-session**, the same test applies to the next send. A
  session that started viable and became non-viable ends the same way as one
  that was never viable: fail closed with a distinct error, so that WP8 can
  count the event rather than see an unexplained disconnect.
- **Every oversize refusal is counted and surfaced**, per direction and per
  class, because WP8's packet-failure threshold is meaningless if refusals are
  invisible. **Decided by the operator on 2026-08-30**, as an explicit
  refinement of the frozen packet-send-failure threshold: the counters are split
  in two, **client-originated** refusals and **elicited** refusals (`echo`). The
  reason it is needed: the
  decided `echo` treatment is to refuse an oversize reply and count it, but WP8
  freezes "**0** oversize refusals — one refusal means a class was missed". As
  written, the one mechanism installed deliberately for `echo` would fail WP8
  with entirely the wrong diagnosis the first time it worked as intended. The
  frozen zero threshold therefore applies to the **client-originated** counter;
  an elicited refusal is the system behaving correctly, and is counted and
  reported as information rather than as a failure.
- **The emitted `connect` datagram is size-checked after compression**, not
  before. This is the one place where the pre-compression bound is not a bound.

## What was replayed, and what was not

The WP6 contract asks for worst-case recorded shapes to be replayed through the
shared relay "where possible". The honest accounting:

**Replayed deterministically, in the committed suite.** The integration
environment WP2 used was supplied by the operator for that round and dismantled
afterwards, so no routed replay was possible in this work package. What was
replayed is the contract itself: the same frame encoder, the same in-memory
relay and echo destination from
[`scripts/relay_loopback.py`](../scripts/relay_loopback.py), and the same
size pre-check, driven at the datagram maximum the routed record reports.
Through that path, against a 1,024-byte maximum:

- the 704-candidate worst cases (716 and 718 bytes) are carried;
- the 896-candidate worst case (910 bytes) is carried;
- the record-backed floor case (768 bytes) is carried;
- every fragment of the recorded 2,304-byte gamestate re-split at
  `FRAGMENT_SIZE = 704` is carried, as four actual datagrams rather than as
  arithmetic;
- the stock engine's worst cases (1,312 and 1,314 bytes) are **refused**, with
  the same outcome the routed record shows at the same two sizes;
- the uncapped `connect` packet (1,039 bytes) is **refused**;
- at a tighter 810-byte maximum, the 704 worst case is still carried while the
  896 worst case is refused.

The loopback model and the pinned browser refuse the same two sizes for the same
reason — both compare the frame against the reported maximum before writing it.
That is agreement between two implementations of the *same rule*, not
confirmation that the path enforces it; no 1,354- or 1,356-byte frame has been
put on a wire by anything in this repository. It is worth exactly what it is: a
check that the model's arithmetic matches the browser's.

**Deferred to the live path.** WP7 answered the implementation-risk questions
the loopback cannot speak to: real per-direction behaviour, keep-alive need,
live-budget enforcement and real relay fragment bursts. WP8-Mini incorporates
that accepted evidence and adds start/end budget observations plus the reduced
two-client network gate below; it deliberately no longer claims a 15-minute
reported-maximum stability measurement.

## WP8 acceptance thresholds

**Current contract: WP8-Mini, explicitly authorized by the operator on
2026-09-01.** This table replaces the original ten thresholds frozen on
2026-08-30. The replacement is an operator-owned plan change made after WP7 had
already passed the exact builds, final-pin census, two-session relay round,
bounded-queue observations, responsive-browser check and one-sided reconnect.
It does not move the selected 768-byte budget or any WP7 implementation bound.
The current topology is one accepted Fedora/KDE workstation, one normal and one
incognito context in a fresh profile, five minutes of concurrent active FFA,
and a targeted reconnect of each client.

| Metric | Threshold | Rationale |
| --- | --- | --- |
| Connection success | Both initial connections complete within at most 3 attempts | The Mini round is too small for a meaningful first-attempt percentage, but the gamestate must still arrive deterministically. |
| Unexpected disconnects | **0** per client during the measured round | Planned targeted drops are classified separately. |
| Planned reconnects | 100% success, each within 10 s to in-game | The Mini operation replaces only the browser's relay transport beneath the still-running engine/netchan: it requires fresh authorization, an assignment and operator-confirmed continued play, not a second game join or gamestate. |
| Network failures | **0** client-originated oversize refusals, write failures, invalid return frames, receive failures, queue overflows, failed reassemblies or truncated messages; elicited refusals remain separately informational | These are the network defects WP8-Mini still exists to detect. Browser counters plus server-side packet observation cover both endpoints. |
| Datagram budget | No observed game UDP payload above **768 bytes** in either direction; at the start and end each browser is open under `acceptedInnerFloor=768` with no `path_budget` refusal or terminal reason | WP7's accepted per-write and periodic enforcement closes a session below the floor, so the open states retain the direct live-path test without pretending the snapshot exposes a separate raw maximum or manufacturing an endurance claim. |
| Receive queue | Fixed depth **256 datagrams**, with the observed high-water mark reported and zero overflow | The implementation is already structurally bounded; the short round checks that real reconnect bursts stay inside it. |
| Reassembly | 0 failed reassemblies; 0 truncated messages | The direct test that both endpoints agree on `FRAGMENT_SIZE`. |
| Session separation | Two distinct virtual assignments, distinct client qports and distinct concurrently live server-facing source ports; no translated-port fixup; either targeted drop leaves the other client playable | These are the correctness boundaries on a shared relay-visible server address. Values remain runtime-only. |
| Server profile | `sv_rateLimitPerPort=1`; both initial engine connections complete on their first attempt, with no connection outcome attributable to a silent rate-limit drop | Two local contexts deliberately exercise the shared-household/source-address case. Because the server's limiter drops excess requests silently, successful connection outcomes — not absence of a response packet — are the Mini evidence. |
| Privacy | The server sees only the relay's base address; no workstation address or environment-specific value enters committed evidence | Different public client networks are no longer required because ioquake3 clients communicate with the server, not with each other. |
| Browser behavior | Both contexts remain responsive with zero unexpected or gameplay-affecting browser errors; both move and fire, and at least one player-vs-player frag is witnessed. Immediate pointer-lock reacquisition denials during manual top-level KDE/Wayland context switching are counted and reported separately. | Numeric frame, long-task, memory and latency gates were explicitly removed; WP7's responsive-browser observation is incorporated by reference. The operator explicitly accepted the two reported transient denials in the 2026-09-01 Mini round as a KDE/Wayland variation after both clients remained playable. |

## Paths that are sizing-neutral

Recorded so a later reviewer does not re-derive them. Downloads, VoIP and demo
traffic are **not** additional sizing risks, and not because the profile
excludes them: everything on those paths is written into a message that the
netchan then fragments, so `FRAGMENT_SIZE` bounds them exactly as it bounds
gameplay traffic, whether or not they are enabled. (`cl_allowDownload` defaults
to 0 at `cl_main.c:3606` in any case.) They would change bandwidth and fragment
counts, never the maximum datagram size, so no bound in this document moves if a
later profile turns them on.

## Inherited gaps

Recorded so they are not rediscovered as surprises:

- **No per-direction budget.** WP2's methodology cannot separate the directions;
  both are held to one budget. Separating them needs a destination that can
  reply at a size other than the one it received, which the contract's echo
  destination is not.
- **The 769–1,023 range is unmeasured.** The selected target does not rely on
  it; it would have been the risk accepted by the 896 alternative.
- **Huffman expansion is derived, not measured.** Hence the requirement to check
  the emitted `connect` size on the wire.
- **The game module's reject string is engine-unbounded.** The committed QVM's
  longest is 32 bytes, so `print` fits; a different QVM could emit up to 16,383
  bytes out of band. The bound belongs to the content pin, not to the engine.
- **`NET_SendLoopPacket` copies without a length check** (`net_chan.c:474`) into
  a `MAX_PACKETLEN` buffer. Not reachable in this profile — it needs a listen
  server answering its own out-of-band query — but it is one more place where
  out-of-band traffic is unchecked.
- **The SOCKS send path copies without a length check** too
  (`net_ip.c:679` into the 4,096-byte `socksBuf` at `:643`). Unreachable without
  `usingSocks`, which the browser build has no way to set, and recorded beside
  the loopback path for the same reason: both are places where a size check on
  the out-of-band send would not be the last word.
- **The legacy protocol path is refused by launch argument.** This was
  previously listed here as a weakness; that undersold it and the entry is
  corrected rather than removed. `com_legacyprotocol` is `CVAR_INIT`, so once
  the process starts at zero nothing — console, config or game module — can
  raise it, and the result is observable in the `protocol` ROM cvar and in the
  header widths the re-census measures. The residual risk is only that a
  deployment omits the argument at launch, which the re-census detects; it is
  not a bound that can decay at runtime. See the profile bounds above.
- ~~**The shared per-address rate-limit bucket.**~~ **Closed by decision, not
  accepted.** The operator extended WP7's scope on 2026-08-30 with a cvar-gated
  port-aware bucket rather than accepting the gap; see the shared-address
  section. The entry is kept struck through rather than deleted because the
  reasoning for the default remaining upstream — port-keyed buckets are evadable
  on the open internet and are only safe here because the server is reachable
  solely from the relay — is a constraint anyone reusing this engine change
  needs to inherit with it.
- **The qport collision hazard.** The server rewrites a client's stored remote
  port before validating the netchan checksum, so a collision on a shared
  address re-homes the victim's netchan even though the spoofed content is then
  rejected. Mitigated by requiring a real CSPRNG for qport, not eliminated.
