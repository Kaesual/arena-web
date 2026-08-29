# Browser arena: initial findings and prototype plan

**Status:** Discussion draft; evidence gathered, work packages not yet reviewed

## Goal

Create a fast, Quake III Arena-like browser experience with freely
redistributable code and content. The browser client should join a matching
native dedicated server through a WebTransport-to-UDP relay without exposing
the player's real IP address to the game server.

The first milestone is a technical vertical slice, not a full OpenArena port
and not a general-purpose id Tech 3 hosting platform.

## Starting point

### Engine

[ioquake3](https://github.com/ioquake/ioq3) is the primary engine base. Its
current tree has an official Emscripten/CMake target covering the browser main
loop, SDL input and audio, WebGL rendering and packaged runtime files. The
upstream web template disables networking, so multiplayer still needs an
explicit browser network backend.

The ioquake3 repository includes the GPL-released Quake III game, client-game
and UI source and can build QVMs. It does not include the proprietary Quake III
maps, textures, models, sounds and other game data. A complete Quake III build
therefore requires data supplied by someone who owns the game and cannot be
the freely hosted default for this project.

### OpenArena as the first content source

[OpenArena](https://github.com/OpenArena) is based on ioquake3 and supplies
free game code and replacement content for a Quake III-style standalone game.
The maintained ioquake3 Emscripten path is a better engine starting point than
the OpenArena engine fork, but OpenArena remains the strongest initial source
for game logic, maps, models, textures, sounds and UI data.

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
- public protocol documentation for the relay integration;
- verification and license/provenance reports.

The ioquake3 fork owns only engine-level changes. It should remain close enough
to upstream that generally useful Emscripten fixes can be reviewed and offered
upstream without carrying product assets or deployment details.

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

The browser-facing relay provides authorization and a virtual client address.
The dedicated server receives ordinary game UDP datagrams and does not learn
the player's public address. The first prototype targets only its matching,
pinned server and content set.

## Principal technical risks

### Datagram size and fragmentation

ioquake3 allows network packets up to 1,400 bytes and normally fragments at
1,300 bytes. WebTransport datagrams commonly provide a smaller path budget, and
the relay adds its own header. Sending an oversized datagram may fail silently
in browser implementations.

The prototype must record real packet-size distributions and the browser's
reported maximum datagram size in both directions. If complete game datagrams
do not fit, the preferred direction is bounded tunnel fragmentation and
reassembly below the engine rather than a silent reduction that makes client
and server protocol behavior disagree. Reassembly needs strict byte, fragment,
count and time limits and must treat a missing fragment as loss of the original
UDP datagram.

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

Promising reviewed content sources are the Debian-cleaned OpenArena 0.8.8
source/data package and OpenArena Community Mappack Volume 1. `quake3-mini` is
a useful size reference, but its manual reduction process is not yet the
provenance/build contract for this project.

### Compatibility scope

Preserving the native protocol where practical is valuable, but arbitrary
OpenArena servers, retail Quake III clients, public master servers, automatic
PK3 downloads and third-party mods are not prototype requirements. Expanding
that scope before the vertical slice works would multiply content, security and
protocol variables at once.

## Agreed prototype sequence

The following sequence captures the agreed direction. It still needs to be
split into reviewed work packages with inputs, outputs, acceptance evidence and
explicit non-goals.

1. **Reproduce the upstream browser build.** Pin the ioquake3 commit and exact
   Emscripten toolchain, build the official Emscripten target without product
   networking changes and document the artifact identities.
2. **Run a minimal free arena locally.** Assemble a reproducible OpenArena-based
   content closure with one map and bots, then verify loading, UI, rendering,
   input, audio and offline play in a real browser.
3. **Build the matching native server.** Produce a native dedicated-server
   artifact and container from pinned engine/gamecode/content inputs, and prove
   that a native test client can complete a session against it before adding
   the browser relay.
4. **Add the browser network vertical slice.** Implement the smallest
   ioquake3-to-WebTransport backend, authenticate to the relay, address the
   pinned virtual server and measure packet sizes before deciding the final
   fragmentation contract.
5. **Exercise real multiplayer.** Connect two browser clients through the relay
   to the native server and play for at least 10–15 minutes while observing
   loss, ordering, latency, reconnect behavior, frame timing and server-side
   address behavior.
6. **Prepare product integration.** Only after the vertical slice passes,
   specify persistent settings, content caching, launch UX, catalogue and
   account integration, health/probe behavior, server resource limits and
   production packaging.

## Decisions required before implementation work packages

1. **Product identity:** ship an explicitly OpenArena-compatible profile or a
   smaller separately named arena experience derived from attributed free
   OpenArena components. The latter is the current recommendation.
2. **ioquake3 branch policy:** decide whether the Kaesual fork keeps `main` as a
   direct upstream-tracking branch and carries product changes on `web` before
   the first engine commit.
3. **Repository license:** choose a GPL-compatible license for original code
   and document how linked JavaScript, QVM source and separately aggregated
   assets retain their licenses.
4. **Initial content:** select the first map, player model, bots and game modes,
   then prove the complete source and license chain before generating a pack.
5. **Compatibility boundary:** confirm that only the matching pinned server is
   required for the prototype.
6. **Browser matrix:** choose the browsers and desktop platforms that define
   acceptance; mobile/touch is not assumed.

## Next planning action

Turn steps 1–6 into coherent, reviewable work packages. Each package should
state its pinned inputs, concrete output, automated checks, manual acceptance,
license evidence, security implications and explicit non-goals. Review that
breakdown before modifying the ioquake3 fork or introducing content downloads.
