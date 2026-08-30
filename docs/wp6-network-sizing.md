<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP6: the measured network-sizing decision

**Status:** analysis complete and a decision **proposed**. The strategy
selection and every numeric WP8 threshold below are the operator's to settle;
nothing here is decided. Each such value is marked **[proposed]**. WP7 stays
blocked until the operator selects a strategy and the independent
protocol/security review of this document has happened.

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

It decides — subject to the operator's selection — the transport strategy, the
exact byte, packet and fragment limits that strategy needs, what happens when a
live session reports a budget the decision did not anticipate, and the
implementation and acceptance contracts that replace WP7's and WP8's scope-gate
text.

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
| `rcon` | c → s | 1,024 | 1,066 | no | no | **no** |
| **`echo`** | **c → s** | **1,022** | **1,064** | **no** | **no** | **no** |
| `print` (rcon redirect) | s → c | 1,017 | 1,059 | no | no | **no** |
| `print` (rejection) | s → c | 86 | 128 | yes | yes | **no** |
| `challengeResponse` | s → c | 57 | 99 | yes | yes | **no** |
| `getchallenge` | c → s | 40 (ceiling 1,037) | 82 | yes | yes | **no** |
| `connectResponse` | s → c | 31 | 73 | yes | yes | **no** |
| `getinfo` | c → s | 15 | 57 | yes | yes | **no** |
| `getstatus` | c → s | 13 | 55 | yes | yes | **no** |
| netchan fragment | c → s | 1,314 | 1,356 | no | no | n/a — it *is* the fragment |
| netchan fragment | s → c | 1,312 | 1,354 | no | no | n/a |

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

**Refuted, by measurement rather than by arithmetic.**

An unchanged engine can emit a 1,314-byte datagram, which needs a 1,356-byte
frame. The transport reported a 1,024-byte maximum. That alone settles it, but
the record is stronger than that: the routed round **sent cases at exactly these
sizes and they were refused**.

| Inner bytes | Frame bytes | Outcome in the committed record |
| --- | --- | --- |
| 1,312 | 1,354 | `notSentFrameExceedsTransportLimit` |
| 1,314 | 1,356 | `notSentFrameExceedsTransportLimit` |

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

A fragment-size change does nothing for out-of-band traffic. **Six classes are
over budget at both targets** — `statusResponse`, `infoResponse`, `connect`,
`echo`, `rcon` and the rcon redirect's `print` — and they need four treatments,
grouped below. A fifth item follows them: the legacy protocol path, which is not
a sizing problem at all but belongs here because it is the other thing the
profile has to pin.

The grouping turns on one distinction. Most of these are excluded because the
*client* never originates them, which is a statement about the profile. But one
— `echo` — is triggered by the **destination**, so no statement about what the
client chooses to send can exclude it, and it is the reason the emitted-size
check is a requirement rather than a precaution.

1. **`connect` — the one that must be solved.** It is the largest datagram the
   client can originate and the connection cannot happen without it. Its only
   variable term is the userinfo, and the fixed part is 16 bytes, so the cap is:

   | Target | Userinfo cap | Observed in the census |
   | --- | --- | --- |
   | 768 | **752 bytes** | the whole packet was 263 and 292 bytes |
   | 982 | 966 bytes | — |

   The cap is comfortable: the profile's actual userinfo produces a packet under
   300 bytes, so a 752-byte allowance is roughly 2.5× headroom. **[proposed]** a
   cap of **512 bytes** of userinfo, well inside either target and still far
   above what the profile uses, so the same number holds whichever budget the
   operator selects.

2. **`statusResponse` and `infoResponse` — excluded, not capped.** These are the
   server browser's answers, and the prototype profile has no server browser:
   the browser client is launched at one pinned virtual destination and issues
   no `getstatus` or `getinfo`. They therefore never traverse the relay. That is
   a property of the profile, not of the transport, so it is recorded as an
   enforced constraint rather than assumed away — and if a later product wants a
   server browser through a relay, that reopens this decision. For completeness,
   if the operator wanted them on the path, `statusResponse` would need the
   serverinfo capped to 348 bytes at the 768 target and 562 at 982.

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

   **[proposed]** treatment: leave the handler in place and let WP7's
   fail-closed emitted-size check refuse an oversize reply, counted like any
   other refusal. Losing an `echo` answer costs the session nothing — it is a
   diagnostic courtesy, not part of the protocol — so a counted, harmless drop
   is a better outcome than a code change to the connectionless path. The
   alternative, disabling the handler outright in the `web` branch, is also
   sound and the operator may prefer it; it is a slightly larger engine change
   for a slightly smaller attack surface.

4. **`rcon` and its answer — excluded.** The profile sets no rcon password and
   the browser client exposes no rcon command, so neither the 1,024-byte request
   nor the server's reply can be elicited. The reply is worth naming beside it:
   `SV_FlushRedirect` prints accumulated command output out of band bounded by
   `SV_OUTPUTBUF_LENGTH` (1,008 bytes, `sv_main.c:696-698,714`), giving a
   1,017-byte server-to-client datagram. `rcon` itself is also the one class that
   bypasses `NET_OutOfBandPrint` and calls `NET_SendPacket` directly
   (`cl_main.c:1890`), so a size check placed at the out-of-band send would not
   see it. A future profile enabling rcon must revisit both.

5. **The legacy protocol path — refused.** Not a size problem: a compat
   connection only makes datagrams smaller. But the pinned server accepts
   protocol-68 by default, and doing so bypasses the gamename check and drops
   the challenge-checksum spoofing protection, while also making the header
   geometry this document derives no longer the only one on the wire.
   **[proposed]** both server and client launch with `+set com_legacyprotocol 0`,
   so the legacy path is refused outright and the census's observed geometry is
   the only one the profile can produce.

### Where the change lands

| Component | Change |
| --- | --- |
| `ioq3` (`web` branch) | `FRAGMENT_SIZE` becomes an explicit constant at the selected value, documented in place with the reason and the symmetry requirement. `MAX_PACKETLEN` unchanged. |
| `ioq3` (`web` branch) | A fail-closed emitted-size check on the browser's send path (see below), and the userinfo cap. |
| Profile (launch arguments) | `+set com_legacyprotocol 0` on both server and client. |
| This repository | The engine pin, and the WP7 evidence recording the rebuild and re-census. |
| Native server | Rebuilt from the same final `web` pin — mandatory, because `FRAGMENT_SIZE` is server-side packet logic. |

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

## Proposed decision

**[proposed]** Select **strategy 2, the symmetric fragment-size reduction**,
sized to the **record-backed floor of 768 inner bytes**, giving
**`FRAGMENT_SIZE = 704`**, together with the profile bounds above.

The reasoning for the conservative target over the permissive one:

- 768 is the only number the record demonstrates actually carried traffic
  end-to-end. 982 is a subtraction from a self-reported value, inside a range
  the round never sent a case in. The WP6 contract's own non-goal — "treating a
  single browser session's reported maximum as a universal constant" — points
  the same way.
- The cost of being wrong is asymmetric. Choosing 704 and being too conservative
  costs two extra datagrams per connect, once. Choosing 896 and being wrong
  means the gamestate does not arrive and no session can start at all.
- The measured cost of the conservative choice is negligible: 108 extra wire
  bytes on one transfer per connection, and nothing else in the profile changes
  its fragmentation behaviour at all.
- 704 also survives a path that turns out to be tighter than this one. The
  replay confirms the 704 worst case is still carried at a transport maximum of
  810 bytes, where the 896 worst case is not.

The counter-argument the operator should weigh: if the 1,024-byte maximum is a
stable property of this browser and this relay — and the evidence that it was
identical on loopback and constant across six seconds of a held session is real
— then 896 leaves a smaller gamestate burst and a little more headroom per
datagram for later engine changes. The choice is genuinely the operator's.

### Decision points the operator must settle

1. **Strategy**: symmetric fragment-size reduction, as proposed, or another.
2. **Sizing target**: the record-backed floor (`FRAGMENT_SIZE = 704`) or the
   derived budget (`FRAGMENT_SIZE = 896`).
3. **The 64-byte reserve and 64-byte alignment**, or different values. Both are
   script arguments, so any choice recomputes.
4. **The userinfo cap**: 512 bytes as proposed, or another value below the
   target's limit.
5. **The WP8 thresholds** below, each individually.

## Behaviour when the live budget is not what was measured

The record shows a constant 1,024 on the measured path, but the contract
requires the specification regardless, and a browser is entitled to report
something else. The rules are **[proposed]** and belong in WP7's implementation:

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
  carry the engine's traffic. **Fail closed**: refuse to send, surface a
  distinct, non-retrying error to the loader, and close the session. Do not
  truncate, do not silently drop, and do not fall back to another transport —
  a fallback would be a transport mechanism this decision did not authorize.
- **If it changes mid-session**, the same test applies to the next send. A
  session that started viable and became non-viable ends the same way as one
  that was never viable: fail closed with a distinct error, so that WP8 can
  count the event rather than see an unexplained disconnect.
- **Every oversize refusal is counted and surfaced**, per direction and per
  class, because WP8's packet-failure threshold is meaningless if refusals are
  invisible.
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

That the model and the real path agree at 1,312 and 1,314 is not an assumption:
the routed record contains those exact refusals.

**Deferred to WP7 and WP8, on the live path.** Everything the loopback cannot
speak to: real per-direction behaviour, whether a keep-alive is needed and at
what interval, whether the browser ever rejects a write that fits the reported
maximum, whether the reported maximum moves over a fifteen-minute session, and
the behaviour of a real relay under real fragment bursts. These are named in
WP7's and WP8's replacement contracts below so they cannot be lost.

## Proposed WP8 acceptance thresholds

All **[proposed]**; the operator freezes them. Each applies to the WP8 topology
— two independently addressed browser clients, at least 15 minutes of active
two-player FFA after both join, planned disconnect/reconnect exercises for each.

| Metric | Threshold | Rationale |
| --- | --- | --- |
| Connection success | 100% of connect attempts within 3 attempts; ≥ 90% on the first attempt | The gamestate is the first large transfer; a sizing error shows up as a *deterministic* connect failure, so anything below this is a sizing bug rather than flakiness. |
| Unexpected disconnects | **0** per client per 15-minute session | With a fixed profile and a bounded queue there is no benign cause; one is a defect to explain, not a rate to tolerate. |
| Planned reconnects | 100% success, each within 10 s to in-game | The census's own reconnect completed; the budget here is the fresh authorization plus a second gamestate. |
| Packet send failures | **0** oversize refusals; ≤ 0.1% write failures | Zero oversize is the direct test of this decision — one refusal means a class was missed. Write failures are a transport property WP2 never observed, so a small non-zero allowance is honest. |
| Frame pacing | ≥ 95% of frames within 2× the median frame time; no frame > 250 ms after the first 10 s | WP4 established the offline baseline; this bounds what the network backend is allowed to add, and excludes startup. |
| Long tasks | No main-thread task > 100 ms after the first 10 s | A synchronous drain of a fragment burst would show up here; it is the specific failure mode a smaller `FRAGMENT_SIZE` makes more likely. |
| Relay-added latency | Median round trip ≤ 1.5× the direct native round trip on the same path; 99th percentile ≤ 3× | Relative rather than absolute, because the routed path's own latency is an environment property and the record has no absolute baseline to hold anyone to. |
| Receive queue | Bounded at a fixed depth **[proposed: 256 datagrams]**; no growth trend over the session; overflow is an explicit counted event | WP7's bounded queue needs a number, and an unbounded queue is the failure the envelope already forbids. |
| Reassembly | 0 failed reassemblies; 0 truncated messages | The direct test that both endpoints agree on `FRAGMENT_SIZE`. |
| Privacy | Both players appear only as the relay's IPv4 endpoint with distinct source ports; neither public address appears in server logs or committed evidence | Unchanged from the WP8 envelope; restated so it is frozen with the rest. |

## Inherited gaps

Recorded so they are not rediscovered as surprises:

- **No per-direction budget.** WP2's methodology cannot separate the directions;
  both are held to one budget. Separating them needs a destination that can
  reply at a size other than the one it received, which the contract's echo
  destination is not.
- **The 769–1,023 range is unmeasured.** If the operator selects the 982 target,
  that gap is the risk being accepted.
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
- **The legacy protocol path is refused by profile, not by code.** `+set
  com_legacyprotocol 0` is a launch argument; nothing in the engine prevents a
  build from accepting protocol-68. WP7 records it as a profile requirement, and
  a deployment that forgets it gets weaker spoofing protection rather than a
  sizing failure.
