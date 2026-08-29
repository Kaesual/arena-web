<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Browser arena: initial findings and prototype plan

**Status:** Prototype constraints and work-package breakdown independently
reviewed; WP0 and WP1 complete

## Goal

Create a fast, independently named Quake III Arena-like browser experience with
freely redistributable code and content. The browser client should join a
matching native dedicated server through a WebTransport-to-UDP relay without
exposing the player's real IP address to the game server.

The first milestone is a technical vertical slice. It is neither an official
nor a complete OpenArena port, and it is not a general-purpose id Tech 3
hosting platform.

## Decided prototype constraints

- **Product identity:** `arena-web` is the working repository name for a
  separately named arena experience derived from attributed free components.
  It does not claim to be OpenArena or promise general OpenArena compatibility.
- **License:** original arena-web code and documentation are
  GPL-2.0-or-later. ioquake3, game code and each content component retain their
  own licenses and notices.
- **Compatibility:** the prototype supports only its pinned browser client,
  pinned native server and exact content set.
- **Playable slice:** one freely redistributable map, FFA, offline bots and two
  browser players for multiplayer acceptance. Additional maps and game modes
  follow the vertical slice.
- **Server topology:** multiplayer uses a matching native dedicated server
  behind the shared relay. Browser-hosted or peer-to-peer games are outside the
  product direction, not a deferred prototype feature.
- **Browser gate:** one exact Chromium version string on one exact desktop OS
  version. The owning work package records both before acceptance; other
  browsers, mobile and touch are later scope.
- **Original Quake III data:** proprietary game data is neither downloaded nor
  distributed. User-supplied retail PK3 support is a possible later feature,
  not a prototype requirement.
- **Engine branches:** the Kaesual ioquake3 fork keeps `main` available for
  tracking upstream. Product engine changes live on `web`, which will be
  created and pinned before the first engine modification.
- **Game code:** the prototype starts with ioquake3's bundled GPL `baseq3`
  game, client-game and UI source compiled as QVMs. The content work must prove
  that its audited free data closure works with those QVMs; changing to
  OpenArena game code requires an explicit plan revision and immutable pin.
- **Relay boundary:** this repository owns the public WebTransport-to-UDP
  contract and browser conformance probe, not a second relay server. One
  game-neutral relay implementation is shared by the operational stack; its
  environment-specific integration remains outside this public repository.

## Starting point

### Engine

[ioquake3](https://github.com/ioquake/ioq3) is the primary engine base. Its
current tree has an official Emscripten/CMake target covering the browser main
loop, SDL input and audio, WebGL rendering and packaged runtime files. The
upstream web template starts the client with the runtime cvar
`+set net_enabled 0`; the network sources are still compiled. Multiplayer
therefore needs an explicit browser backend and a decision about its seam in
the existing send/receive path.

The ioquake3 repository includes the GPL-released Quake III game, client-game
and UI source and can build QVMs. It does not include the proprietary Quake III
maps, textures, models, sounds and other game data. A complete Quake III build
therefore requires data supplied by someone who owns the game and cannot be
the freely hosted default for this project.

### OpenArena as the first content source

[OpenArena](https://github.com/OpenArena) is based on ioquake3 and provides
standalone game code and replacement content for a Quake III-style game. Its
per-file licensing and preferred source still need to be verified before
selection. The maintained ioquake3 Emscripten path is the decided engine
starting point. OpenArena remains the strongest initial content candidate for
maps, models, textures, sounds and UI data.

The OpenArena engine fork is a compatibility reference: the prototype must
identify which of its standalone, protocol and rendering changes are actually
needed rather than assuming either that all are required or that all can be
discarded.

The experimental `OpenArena/UI3` tree is not an initial input. It targets OA3,
is publicly stale, has no repository-wide license statement and contains many
binary assets without adequate per-file provenance.

### Other id Tech 3 descendants

- Tremulous is a promising later project: GPL code and CC-BY-SA media, but a
  significantly diverged engine and game protocol mean it would be a separate
  port that reuses lessons and relay components rather than a content switch.
- Turtle Arena and Unvanquished are free games with id Tech 3 ancestry, but
  their Spearmint and Dæmon engines have diverged too far to be targets of the
  first ioquake3 build.
- World of Padman and Q3Rally are not suitable content sources for this project:
  their important data is non-commercial, non-free or lacks a known free
  license.
- `ioquake/three` is a useful historical attempt at a free ioquake3 content
  base, but it is not a complete playable experience.

The network layer should be reusable without making these later ports part of
the first milestone.

## Repository and artifact boundaries

This product repository owns:

- the exact ioquake3 fork pin;
- the browser loader and product-facing web files;
- reproducible content selection, transformation and manifests;
- the native dedicated-server build and container assembly;
- public relay protocol documentation and a browser conformance probe;
- verification and license/provenance reports.

The ioquake3 fork owns only engine-level changes. It should remain close enough
to upstream that generally useful Emscripten fixes can be reviewed and offered
upstream without carrying product assets or deployment details.

The shell and retail-data configuration emitted by the upstream Emscripten
target are step-1 build evidence, not product artifacts. Product packaging
allowlists the generated engine runtime files it consumes and uses the loader
and content configuration owned here; it does not ship the upstream shell by
accident.

Large content is an input or generated artifact, not a reason to hide provenance
inside a binary checked into Git. A content lock must identify its public
source, immutable revision or digest, applicable license, preferred source form
and deterministic output members.

## Intended runtime shape

```text
browser
  ioquake3 WebAssembly + cgame/ui QVMs + curated PK3 content
        |
  small C/JavaScript WebTransport network backend
        |
  authenticated WebTransport-to-UDP relay
        |
  native dedicated server on UDP 27960
```

The browser-facing relay contract provides authorization and a virtual client
address. The dedicated server receives ordinary game UDP datagrams and does
not learn the player's public address: it sees the relay's IPv4 address and a
per-player UDP source port, not the browser-side virtual IPv6 address. The first
prototype targets only its matching, pinned server and content set.

## Principal technical risks

### Datagram size and fragmentation

ioquake3's netchan buffer is 1,400 bytes, but messages at or above its 1,300-byte
fragment size are split. Including netchan headers, expected wire datagrams can
reach 1,312 bytes from server to client and 1,314 bytes from client to server.
The maximum unfragmented body is 1,299 bytes, which becomes 1,307 bytes from
server to client and 1,309 bytes from client to server because only the client
header includes the 2-byte qport. Fragment records add two further 2-byte
fields, producing the 1,312/1,314-byte pair.
Connectionless handshake packets bypass netchan fragmentation and can follow a
different size bound. The existing relay adds a fixed 40-byte header and a
two-byte length prefix for each enclosed UDP datagram, so the largest expected
client fragment becomes a 1,356-byte WebTransport payload. The usable
WebTransport datagram budget remains session- and path-dependent and may
change.

Before the browser backend is implemented, the prototype must probe a
conservative WebTransport payload budget and record the observed range in each
direction. The matching native session must separately census both netchan and
connectionless handshake packets in each direction. Step 4 cannot start until
both evidence sets exist.

If all complete datagrams fit the conservative budget, the tunnel keeps them
intact. The working expectation is instead a documented symmetric fragment-size
reduction in the pinned browser and native server engines, accepting the loss
of stock-server compatibility; measurements must confirm it. Bounded
fragmentation implemented and reassembled by the matching engine pair is the
next in-scope alternative and needs strict byte, fragment-count and time limits.
Stream-assisted handling would change the separately owned shared relay and
therefore requires a new cross-repository plan and review. An asymmetric or
undocumented engine-size change is never acceptable. A WebSocket fallback also
requires a new plan review rather than becoming implicit prototype scope.

### Browser timing and interaction

Quake III movement is sensitive to frame timing. The Emscripten main loop,
VSync and `com_maxfps` must be tested at real display refresh rates. Pointer
lock, keyboard layout, focus loss, fullscreen and audio activation require
real-browser acceptance rather than only unit or headless tests.

### Content size and identity

The complete OpenArena 0.8.8 download is roughly 425 MB, which is a poor first
page load. The prototype should use a reproducible dependency closure for one
map, the required UI and game data, at least one player model and bots. Client
and server must use the exact same QVM/PK3 identities; missing-media download is
not part of the initial milestone.

Promising candidate content sources are the Debian-cleaned OpenArena 0.8.8
source/data package and OpenArena Community Mappack Volume 1. `quake3-mini` is
a useful size reference, but its manual reduction process is not yet the
provenance/build contract for this project.

### Compatibility scope and explicit non-goals

Preserving the native protocol where practical is valuable, but arbitrary
OpenArena servers, retail Quake III clients, public master servers, automatic
PK3 downloads and third-party mods are not prototype requirements. Expanding
that scope before the vertical slice works would multiply content, security and
protocol variables at once.

The prototype also excludes additional game modes, a public server catalogue,
persistent player progression, automatic content downloads, mobile/touch,
user-supplied retail Quake III data and ports of other id Tech 3 descendants.

## Agreed prototype sequence

The following sequence captures the agreed direction and is reflected by the
approved packages in
[`prototype-work-packages.md`](prototype-work-packages.md).

1. **Reproduce the upstream browser build.** Pin the ioquake3 commit and exact
   Emscripten toolchain, build the official Emscripten target without product
   networking changes and document the artifact identities. Acceptance is
   reproducible build evidence, not a playable runtime: no free content has
   been selected yet. Verify upstream's existing QVM build path and retain a
   separate pinned host-tools step only as a documented fallback. Use the
   Emscripten version pinned by upstream CI as the baseline or document and
   test a deliberate deviation.
   In parallel, run a standalone WebTransport datagram-budget probe against the
   compatible shared relay in the routed integration environment.
2. **Run a minimal free arena locally.** Select and license-audit one map, one
   player presentation and the required FFA/UI/bot data, then assemble their
   reproducible OpenArena-based dependency closure. Verify loading, UI,
   rendering, input, audio and offline play in the named Chromium build.
3. **Build the matching native server.** Produce a native dedicated-server
   artifact and container from pinned engine/gamecode/content inputs, and prove
   that a native test client can complete a session against it before adding
   the browser relay. Capture bidirectional netchan and connectionless packet
   sizes from connection through representative FFA and bot play.
4. **Add the browser network vertical slice.** Implement the smallest
   ioquake3-to-WebTransport backend, authenticate to the relay, address the
   pinned virtual server and preserve the measured datagram contract. Decide
   explicitly whether the backend replaces the platform socket implementation
   or hooks behind the existing engine send/receive boundary. This step starts
   only after the browser-path probe and native packet census select a sizing
   strategy; it does not invent fragmentation while being implemented.
5. **Exercise real multiplayer.** Connect two instances of the pinned Chromium
   client from separate client networks through the relay to the native server
   and play FFA for at least 10–15 minutes while observing loss, ordering,
   latency, reconnect behavior, frame timing and server-side address behavior.
6. **Prepare product integration.** Only after the vertical slice passes,
   specify persistent settings, content caching, launch UX, catalogue and
   account integration, health/probe behavior, server resource limits and
   production packaging.

## Inputs the work-package breakdown must resolve

These are bounded planning inputs, not open product scope:

1. Record the exact Emscripten image/digest and the commands that reproduce the
   pinned upstream build, including any separate host-QVM tools step and the
   reason for any deviation from upstream CI's toolchain version. WP0 selected
   Emscripten 6.0.8 and makes compatibility with that newer toolchain an
   explicit WP1 gate; 3.1.58 remains reference evidence only.
2. Choose the first map and player presentation by verified license,
   availability of preferred source and smallest reproducible dependency
   closure; aesthetics break ties rather than override provenance. Prove the
   closure works with the pinned ioq3 `baseq3` QVMs or stop for plan review.
3. Record the exact Chromium version string and desktop OS version used for
   prototype acceptance, plus how a reviewer obtains them.
4. Specify the public relay contract and browser probe, plus the observable
   authorization, virtual-address and packet-size evidence required from a
   compatible routed integration endpoint.
5. Define the conservative WebTransport payload budget and browser probe, then
   combine it with the native netchan and connectionless packet census to
   select intact datagrams, a symmetric engine-size reduction or bounded
   engine-pair fragmentation. A stream-assisted strategy is a separately owned
   relay change and needs a new cross-repository plan before step 4 starts.
6. Put numeric pass/fail thresholds and the two-network acceptance topology on
   multiplayer loss, latency, reconnect and frame-time evidence.

## Next implementation action

Begin WP0 from the approved packages in
[`prototype-work-packages.md`](prototype-work-packages.md). Each package states
its pinned inputs, concrete output, automated checks, manual acceptance,
license evidence, security implications and explicit non-goals.

The independent read-only plan review completed before the breakdown phase;
its source-backed corrections are incorporated above. The next review should
focus on work-package boundaries and acceptance evidence rather than reopening
the decided product scope without new evidence.

Product review subsequently rejected a second public relay implementation: a
duplicate would create an avoidable divergence risk from the one shared proxy.
The public repository remains reproducible through its client, protocol and
probe artifacts; routed end-to-end acceptance deliberately requires the shared
integration endpoint.

The work-package review then verified the relay framing and corrected the
privacy evidence, post-sizing server rebuild, strategy ownership and runtime
integration prerequisites. Those findings are incorporated in the approved
breakdown.
