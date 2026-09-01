<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Browser-client and game-server integration contract

**Status:** Normative for the accepted `arena-web` vertical slice. This document
describes the public boundary an integration needs in order to publish the
browser client, run its matching dedicated server and connect the two through a
compatible relay. It does not prescribe a hosting product, deployment system or
user interface.

The complete WP11 consumer handoff is
[`wp11-integration-handoff.md`](wp11-integration-handoff.md). The machine entry
point is [`release/browser-release.json`](../release/browser-release.json): it
binds the exact browser tree and the smaller authorities below without placing
a self-hashing file inside that tree.

| Question | Source of truth |
| --- | --- |
| Which browser and QVM bytes are accepted? | [`manifests/browser-client.json`](../manifests/browser-client.json) |
| Which content pack is accepted? | [`provenance/arena-web-ffa-content-manifest.json`](../provenance/arena-web-ffa-content-manifest.json) |
| Which files and engine arguments does the browser load? | [`arena/game-profile.json`](../arena/game-profile.json) |
| Which fixed relay profile does the browser enforce? | [`arena/relay-profile.json`](../arena/relay-profile.json) and the [relay datagram contract](relay-datagram-contract.md) |
| Which server files are accepted? | [`provenance/arena-web-server.json`](../provenance/arena-web-server.json) |
| How is the server started? | [`native/server-profile.json`](../native/server-profile.json) |
| Which numeric limits and health states were measured? | [`records/wp11-server-resources.json`](../records/wp11-server-resources.json) |
| Which public sources, toolchains and licences apply? | [`locks/baseline.json`](../locks/baseline.json), the manifests above and the member-level records under [`provenance/`](../provenance/) |

An integration must consume those files together from the one immutable WP11
release checkout and run `scripts/check.sh` before packaging them. Values below
explain that checked contract; they are not competing defaults.

## Supported profile and accepted release

The supported profile is intentionally narrow:

- one Linux `amd64` dedicated-server image;
- one browser client built from the same ioquake3 commit and QVMs;
- the `arena` standalone game directory, map `oa_pvomit`, FFA, frag limit 15,
  no time limit, three fixed bots and eight total server slots;
- one compatible WebTransport datagram relay between the browser and the
  server's IPv4 UDP endpoint; and
- Google Chrome for Testing `152.0.7977.64` on Fedora Linux 44 `x86_64` as the
  accepted browser platform. The accepted real multiplayer rounds used
  KDE/Wayland as an explicit variation; this is not a general browser or
  platform support claim.

The current accepted release is the indivisible tuple below. A publisher may
assign it a convenient release name, but `latest`, a branch name or a mutable
container tag is not an identity.

| Input | Accepted identity |
| --- | --- |
| Baseline lock | `sha256:227c9434ba306b5b95bb36f392b1d9faa08fdef5b325dd4d557d8c4b8ee55287` |
| ioq3 engine | `git:d594b1cc9bfc5b58ccebffd4d840a13782cb6592` |
| Browser loader producer | `git:95f45b537dd0bb8b4a542b97d0f4281eefa7604a` |
| Browser artifact manifest | `sha256:1fca91ba4198398198f90d52222de4e9e2a5d910e275061b2f605f13e45c8047` |
| Content artifact manifest | `sha256:7785b2a65104257d1f0cd67d9b59771dc259726155acc54bdae0451cef92dfc5` |
| Content base archive | `sha256:6c3341ef87d16c75b7d3fb5f368d9f935dac304c1dd7667f96b64dd73912bb03` |
| Content map archives | covered transitively through the content artifact manifest, and each bound byte-for-byte to its counterpart in the server manifest |
| Server artifact manifest | `sha256:580654c261a364ad71a0ae5e92b6ad291032ae8f97e5f4276e557ea3a6081281` |
| Server image producer/build checkout | `git:95f45b537dd0bb8b4a542b97d0f4281eefa7604a` |
| Accepted native server image ID | `sha256:73ba426831ffee51811d24d6ead5a241723a1aa1bc446ecf3d6405dbb806bd2f` |

The image value is the reproducible container configuration/image ID observed
after loading the accepted single-platform image, not a promise that every
registry will use that value as its manifest digest. A registry publication
must be pinned by its own immutable manifest digest and must additionally load
to the accepted image ID. An archive transfer must likewise verify the loaded
image ID rather than trusting its filename.

The image records its producer in an OCI label. Reproduce it from a clean
checkout at `95f45b537dd0bb8b4a542b97d0f4281eefa7604a`, after reproducing the
accepted browser, content and native builds, and run
`scripts/build-server-image.sh`. Compare the generated
`build/server-image/artifact-manifest.json` byte-for-byte with this release's
`provenance/arena-web-server.json`; require manifest identity
`sha256:580654c261a364ad71a0ae5e92b6ad291032ae8f97e5f4276e557ea3a6081281`
and loaded image ID
`sha256:73ba426831ffee51811d24d6ead5a241723a1aa1bc446ecf3d6405dbb806bd2f`.

A rebuild from the current documentation commit or any other later commit has
a new image ID even when all four runtime files are byte-identical, because its
producer label is different. Such a rebuild must be published under a new
identity. Its generated manifest must match every content and input field in
`provenance/arena-web-server.json`, allowing only the documented
`producer.commit` difference; any other difference is a new content tuple that
requires its own reviewed manifest. Neither case may be silently called the
already accepted image.

The [WP7 acceptance report](wp7-routed-acceptance-2026-09-01.md) binds the
compatible protocol/content ancestor to the final packet census and routed
two-browser result. The
[WP8-Mini report](wp8-mini-acceptance-2026-09-01.md) adds five minutes of
concurrent play and reconnect of both clients without changing an owning input.
The [WP10 report](wp10-canvas-resize-acceptance-2026-09-01.md) accepts runtime
canvas resize and HTML fullscreen. WP11 reissues browser/server identities for
the Emscripten-only host-stop export and binds their focused lifecycle/resource
evidence in the handoff above; its server payload bytes and content PK3 remain
unchanged.

## Browser artifact publication

### Build and stage

The browser engine and content pack are separate reproducible builds. The
commands and immutable input acquisition are documented in the repository
README. After both accepted build directories exist, assemble the public tree
with:

```bash
scripts/serve-arena.sh --stage-only
python3 scripts/stage-arena.py --target build/arena-serve --check
```

The target must remain under the checkout's gitignored `build/` directory while
the repository scripts create or verify it. A publisher then transfers that
verified directory as one unit. The current tree contains exactly these
files. The count is not fixed: the content archives below are one base plus one
per supported map, so a release that publishes more maps stages more files.
Derive the set from the release index rather than asserting a number:

```text
index.html
loader.js
default.cfg
game-profile.json
arena/canvas-resize.js
arena/host-lifecycle.js
arena/network-backend.js
arena/relay-profile.json
probe/relay-framing.js
manifests/browser-client.json
provenance/arena-web-ffa-content-manifest.json
engine/ioquake3.js
engine/ioquake3.wasm
engine/baseq3/vm/cgame.qvm
engine/baseq3/vm/qagame.qvm
engine/baseq3/vm/ui.qvm
content/baseq3/arena-web-ffa-base-6c3341ef87d16c75.pk3
content/baseq3/arena-web-ffa-map-oa_pvomit-304a2266a08ebe2f.pk3
```

The staging check refuses an extra, missing, symlinked or changed file. It
checks every generated artifact by SHA-256 and byte length against its
manifest. The browser repeats the artifact checks before it executes the
module or WebAssembly or writes any file into the in-memory filesystem.

### HTTP contract

The complete tree must be served below one same-origin release root. All
relative paths and both manifests are load-bearing; renaming a file or serving
one from another origin is unsupported. The page must be a secure context in
production, normally HTTPS, because it requires Web Crypto for artifact
verification and WebTransport for multiplayer. Loopback HTTP is suitable only
for local development.

Serve `.wasm` as `application/wasm`, JavaScript as a JavaScript media type, JSON
as `application/json`, HTML as `text/html`, and each PK3 as an opaque
binary. The content archives carry their own SHA-256 in their file names and
never change under a published name, so serve them
`Cache-Control: public, max-age=31536000, immutable`.
The loader imports the verified module from a `blob:` URL and instantiates the
verified WebAssembly bytes itself. A restrictive Content Security Policy must
therefore be tested with those two mechanisms and must allow the configured
WebTransport endpoint; no CSP profile has yet been accepted by this project.

The client is single-threaded and does **not** require COOP, COEP or
`SharedArrayBuffer`. An embedding product may use cross-origin isolation for
its own reasons, but it is not part of this client contract.

Publish a release atomically below a new immutable URL whenever any staged file
changes, including `index.html`, the loader or either profile. Artifact
verification covers the engine, QVMs and content bytes; HTTPS plus an immutable
release root protects the product-owned loader and profiles. Never replace
files below a URL already advertised as immutable. A stable launch route may
select or redirect to an immutable release root, but caches for the stable
selector must be revalidated.

The client has no service worker, OPFS integration or persistent settings. A
page load fetches and verifies the current release; ordinary HTTP caching may
reuse immutable responses, but reload does not promise to preserve engine
settings. Content downloads from a game server are disabled.

## Browser launch and relay handoff

The staged page is a complete launcher. Its module exposes
`globalThis.arenaWeb` as soon as `loader.js` evaluates. An integration that
wants multiplayer must call `arenaWeb.configureRelay(configuration)` in the
client document before the player presses **Start**. Calling it while the
loader status is `starting` or `ready` is supported; calling it after Start is
not.

The integration may invoke the API directly from a same-origin parent frame or
from a script included in the client document. There is no cross-origin
`postMessage` API. A cross-origin embedding therefore needs its own reviewed
adapter rather than reaching into the frame.

The runtime object has these fields:

| Field | Contract |
| --- | --- |
| `endpointUrl` | Non-empty HTTPS URL with no username, password, query or fragment. It never carries authorization. |
| `certificateHashes` | Array of zero or more lowercase or uppercase 64-hex-character SHA-256 certificate hashes. An empty array uses ordinary public Web PKI; a non-empty array becomes WebTransport `serverCertificateHashes` and must describe the ECDSA-P256, at-most-14-day certificate profile fixed in `locks/baseline.json`. |
| `destinationAddressHex` | The non-zero 16-byte virtual destination as exactly 32 hexadecimal characters without punctuation. This is a relay address, not the server container's IPv4 address. |
| `destinationPort` | Virtual destination UDP port, integer `1..65535`. The relay route behind it must reach the native server's actual UDP port 27960. |
| `clientSourcePort` | Client-chosen correlation port, integer `1..65535`, unique among simultaneously live sessions in the same JavaScript realm. It is not a host UDP socket. |
| `tokenProvider` | Function returning, or resolving to, a non-empty opaque one-time authorization string. It is invoked once for every initial or reconnect attempt. |
| `keepAliveIntervalMilliseconds` | `0` disables application keep-alive; an enabled value is `1000..86400000`. The integration environment decides whether it is needed. The accepted WP8-Mini round exercised 5000 ms, but did not establish that it is universally required. |
| `assignmentTimeoutMilliseconds` | Optional integer `1..60000`; default 10000. It bounds the wait for the relay's address assignment. |

Illustrative wiring, with deliberately non-usable placeholders, is:

```js
globalThis.arenaWeb.configureRelay({
  endpointUrl: runtimeRelayEndpoint,
  certificateHashes: runtimeCertificateHashes,
  destinationAddressHex: runtimeVirtualDestination,
  destinationPort: runtimeVirtualPort,
  clientSourcePort: allocateLiveCorrelationPort(),
  tokenProvider: async () => issueFreshOneTimeAuthorization(),
  keepAliveIntervalMilliseconds: runtimeKeepAliveInterval,
});
```

The provider, not the loader, owns authorization issuance. It must return a
fresh value on **every** invocation, including after a failed open; a failed
attempt has spent its value. The loader sends the value once in the first
WebTransport datagram, then drops its reference. It never puts authorization in
a URL, persists it, or includes it in `report`, `snapshot()` or an engine log.
The provider must apply the same rules and must not log a returned value or an
exception containing it.

Only the one-time authorization is a credential. The endpoint, certificate
hash, virtual destination and ports are not passwords, but they are
environment-specific topology and trust inputs. This repository keeps all of
them runtime-only so public source and evidence do not accidentally describe a
particular deployment.

### User activation and observable state

The built-in **Start** button and a same-origin host both use
`arenaWeb.start()`. The call must occur directly in a transient user-activation
event; it is accepted exactly once from `ready`. It rejects a duplicate without
starting second work. The gesture grants audio activation and allows later
pointer lock. The canvas fills its page and derives an engine resolution of at
least 320 by 240 CSS pixels from its live box.

After startup, a `ResizeObserver` forwards changes of that CSS box into SDL's
existing Emscripten resize path. ioq3 then adopts the new custom resolution
after its existing delayed renderer resize. The browser profiles deliberately
set `r_fullscreen=0`: the HTML stage owns fullscreen, while the engine continues
to treat the canvas as a resizable window. Ordinary resize and entering or
leaving HTML fullscreen therefore reinitialize the renderer at the new size;
they do not restart the game or relay session. `snapshot().render`
retains the startup CSS dimensions and separately reports the current CSS
dimensions, observer availability and observed-event count for diagnostics.

The supported host-facing API is:

```js
globalThis.arenaWeb.snapshot()
globalThis.arenaWeb.subscribe(listener) // synchronous immediate snapshot; returns unsubscribe
globalThis.arenaWeb.start()
globalThis.arenaWeb.stop()
globalThis.arenaWeb.whenSettled()
globalThis.arenaWeb.focusSurface()
globalThis.arenaWeb.setFullscreen(engaged)
```

`snapshot()` is a defensive copy and reports exact loading byte progress.
`stop()` is idempotent before Start, during static/relay/engine loading and
while running: it aborts attempt work, closes the relay without Reconnect,
requests the real engine quit and, once engine boot began, waits for the actual
Emscripten exit. Every stop call returns the same Promise. `whenSettled()` also
returns one stable Promise and resolves exactly once with
`{status, exitCode, reason}` for `failed` or `exited`; reconnect never settles
it. The accepted running-stop smoke returns exit code 0; a pre-engine stop has
no process code and returns `null`. The full call/return/error semantics are normative in the
[WP11 handoff](wp11-integration-handoff.md#2-exact-browser-lifecycle-boundary).

The important `snapshot().status` transitions are engine/UI milestones rather
than the transport state machine:

```text
starting -> ready -> booting -> running
    |          |        |         |
    +----------+--------+---------+-> stopping -> exited
                       \-> reconnect-ready
running -> reconnect-ready -> reconnecting -> running
any nonterminal state -> failed
```

`ready` means every static artifact was fetched and verified. In relay mode,
the engine boots only after the relay assigned the session, and `running` means
the engine subsequently emitted its `clientGameLoaded` marker. It does **not**
mean the assigned transport is still open: a relay that closes during engine
boot can expose `reconnect-ready` before the later engine marker writes
`running`. An integration must therefore require both
`snapshot().status === "running"` and `snapshot().relay.state === "open"` for a
currently playable relay client. `failed` and `exited` are terminal.

When an established relay session ends, the engine stays alive, the overlay
offers **Reconnect**, and the next attempt invokes `tokenProvider` again. A
path-budget failure is terminal rather than retryable. The transport must
report `maxDatagramSize >= 810`: the enforced 768-byte inner floor plus the
42-byte single-datagram relay overhead. After an established session, other
transport failures expose the reconnect path and spend a fresh authorization.
A failure during the initial open leaves the page in `failed`; retrying that
case requires a reload and another fresh authorization.

The stable runtime surface is the canvas selected by
`[data-runtime-surface="arena-web"]`. It is the focus/pointer-lock target; its
surrounding stage is the fullscreen element. The focus and fullscreen methods
return explicit `{ok, ... , reason}` results and do not bypass browser
activation policy.

`report` and `engineLog()` are exposed for acceptance diagnostics, but an
integration must treat them as read-only and must not depend on unlisted
counters as a product API. The
surrounding product owns localization and any catalogue or account UX; the
client page retains its own exact loading, failure and reconnect messages.
`engineLog()` is diagnostic, not presentation data: it may contain
user-controlled player text and runtime topology such as the virtual game
destination, so it must not be published or rendered as trusted markup.

## Dedicated-server integration

### Image and command

`scripts/build-server-image.sh` produces the local tag
`arena-web-server:latest`. That tag is a build convenience only. Distribution
must use an immutable registry manifest digest or a verified archive and record
the loaded image ID as described above.

The image has these fixed properties:

- platform `linux/amd64`;
- entrypoint `/opt/arena-web/ioq3ded` and no default argument list;
- unprivileged user and group `65534:65534`;
- working directory `/opt/arena-web`;
- `HOME=/var/lib/arena`;
- one declared network endpoint, UDP 27960; and
- no required host file, secret, environment variable or persistent volume.

The image deliberately has no aggregate `org.opencontainers.image.licenses`
annotation: the GPL arena/engine and the pinned mixed-license Debian base cannot
honestly be flattened into one blanket expression. The image build inspects and
requires the complete OCI configuration above, the exact four provenance/title
labels and the absence of any extra label; the baseline and all preserved
per-package copyright files remain the licence authority.

Start the entrypoint with `native/server-profile.json.serverArguments` in its
committed order. Those arguments are validated as the exact derivation of the
profile's cvars, map and bots. Do not append an override or replace the bundled
configuration: that creates a different, unsupported server profile.

The runtime should apply the same confinement used by acceptance:

```text
drop all Linux capabilities
set no-new-privileges
use a read-only root filesystem
mount an empty 64-MiB rw,noexec,nosuid,nodev tmpfs with mode 1777 at /var/lib/arena
```

The home tmpfs contains disposable engine configuration and the game VM's
`games.log`. It must start empty and need not survive a restart. The image
itself already selects the unprivileged user; granting root or additional
capabilities is not part of the contract. Dedicated game events are also
written to stdout/stderr, which is the hosting system's observable log stream.
Player names are user-controlled log data, so neither stream nor the ephemeral
file may be treated as trusted markup or published without an operator policy.

The server binds IPv4 only and must be reachable from the relay at UDP 27960.
It must not be exposed as an open Internet game server. The profile enables
`sv_rateLimitPerPort=1`, which is safe only when a managed relay controls live
server-facing source ports and gives concurrent clients distinct ports. On an
untrusted direct-UDP path arbitrary source ports could evade address buckets
and exhaust their finite pool; that topology must use the upstream default
instead and is not this accepted profile.

The accepted prototype guard is one CPU core, 256 MiB memory, 128 PIDs and the
64-MiB home tmpfs. Under a representative two-client/three-bot busy phase the
observed maxima were 0.045801 CPU cores, 29,810,688 bytes cgroup memory,
30,998,528 bytes process HWM and 1,272 bytes in the home. That leaves 21.834x
CPU, 9.005x cgroup-memory and 52,758.541x home-space headroom. The full record
is [`records/wp11-server-resources.json`](../records/wp11-server-resources.json).
These are conservative capacity guards for the accepted two-human prototype,
not an SLO, a production capacity promise or evidence for five humans.

### Readiness, liveness and stop

Process existence is liveness. Readiness must use the native ioquake3 binary
query rather than a server-log substring:

1. From the server network, send one UDP payload consisting of four `0xff`
   bytes followed by ASCII `getstatus `, a short hexadecimal challenge and a
   newline to port 27960. The protocol permits at most 128 challenge characters;
   using one whitespace-free token keeps the echoed comparison unambiguous.
2. Require a UDP response from that endpoint beginning with four `0xff` bytes
   and `statusResponse\n`.
3. Parse the following ioquake3 info string, require the echoed challenge, and
   require at least `mapname=oa_pvomit`, `g_gametype=0`, `fraglimit=15`,
   `timelimit=0` and `sv_maxclients=8`. The active game directory is not a
   `getstatus` server-info field; it is already fixed by the verified image and
   exact command profile and must not be inferred from this probe.

Use a stable health-check source address and UDP source port, do not send more
than one query per second, and apply a bounded timeout. The server rate-limits
connectionless queries; probes that continuously rotate ephemeral source ports
would defeat the point of the per-port bucket and consume its finite table.
`getstatus` also returns the current player list; a readiness implementation
must not retain or publish that unneeded, user-controlled tail.
Startup has a 20-second deadline. After the first good reply, three consecutive
failed one-second checks produce `failed`; one or two misses remain `ready`.
The complete observation vocabulary is `missing` (no owned runtime),
`preparing` (running inside the startup window without a valid reply), `ready`
(the exact check passed and the post-ready threshold has not been crossed) and
`failed` (unexpected process exit, missed startup deadline or threshold
crossing).

The image entrypoint receives container signals directly. On `SIGTERM` or
`SIGINT`, the pinned engine sends its final server message, shuts down the game
VM and network state, and then exits with status 1. A process manager must treat
that non-zero status as an expected operator stop when it sent the signal; the
same status without a requested stop remains a failure. The accepted harness
allows ten seconds before forcing termination; the measured graceful path took
0.119 seconds. There is no persistent world to flush, restore or migrate.

Remote administration is deliberately disabled: the profile is LAN-dedicated,
publishes no master-server heartbeat, and sets the RCON, private-server and game
passwords empty. Lifecycle belongs to the container/process manager, not to a
network admin command.

## First public access policy

The accepted Arena profile is guest-playable. A consumer may authorize a guest
or an authenticated user only for a server it deliberately published and
currently observes as `ready`; Arena has no account, character, saved-login or
persistent-state prerequisite. Ordinary platform-wide ban, rate-limit and abuse
policy may run before authorization.

Each one-time authorization names exactly one virtual destination. No new value
is issued for an unpublished, stopped, missing, preparing or failed server, and
every reconnect obtains a new value. A policy change governs new attempts;
WP11 adds no live-session revocation. The browser never receives the actual UDP
server endpoint.

## Relay route and catalogue boundary

The integration owns a mapping from the browser-visible virtual destination to
the actual IPv4 UDP server endpoint. The browser receives only the virtual
address and port. The game server receives packets only from the relay-facing
network and sees the relay's base address plus a relay-controlled source port,
not the player's real address.

A catalogue or launch record needs only enough arena-specific metadata to
select one immutable release tuple and one runtime route:

- a stable product/profile identifier chosen by the integrating product;
- the immutable browser release-root URL;
- the immutable server image reference and its verified image ID;
- the virtual destination address and port supplied at launch; and
- whatever presentation fields the product owns, such as localized title or
  artwork.

Presentation fields, account rules, capacity allocation and authorization
issuance are not `arena-web` manifest fields. They must not be written back into
the public profiles. Conversely, an integration must not derive protocol or
server arguments from presentation metadata; it consumes the checked profiles
above.

All relayed players currently share one server-visible base address. The
accepted server patch adds the source port to connectionless rate-limit buckets
under its explicit cvar, while the ordinary game channel still distinguishes
clients by qport. Address-based bans or abuse attribution at the native server
therefore describe the relay, not an individual player. Per-player policy must
be enforced before or at the relay boundary. A future server-facing virtual
IPv6 source per player would be a separate relay and topology change, not an
assumption an integration may make today.

## The rotation invariant

**The set of maps a server rotates through must be a subset of the set of
archives the client has loaded.** Deriving those two from separate sources is the
defect this section exists to prevent.

The failure is quiet and late. With `sv_pure 0` and `cl_allowDownload 0` the
engine performs no content-agreement check when a client connects, so a client
missing one map connects normally and plays. The *map change* is what breaks it:
the engine restarts its filesystem synchronously, does not find the map, and
drops that client with `ERR_DROP` in the middle of a match — possibly hours into
a session, and only for the clients lacking that one archive.

**Neither half can catch this alone, and this repository cannot catch it at
all.** The rotation is a runtime input to the dedicated server, and the client's
fetch set is likewise supplied by the caller. A check written here would compare
the integration's own input against itself and always pass. So the rule binds
where the two inputs are actually chosen:

> The integration holds **exactly one** rotation list, and derives both the
> server's launch arguments and the loader's fetch set from it. Deriving the two
> separately is the defect; no downstream check catches it.

Two consequences worth stating:

- A rotation list may name only maps this release publishes. The published set is
  the entries of `arena/game-profile.json` `artifacts[]` whose `manifest` is
  `content`; anything else has no archive to fetch and no BSP in the image.
- A client set that is a strict superset of the server's rotation is safe, and is
  the conservative choice when the two are decided at different times. Only the
  subset direction fails.

**Today the map is still committed**, so this obligation is not yet load-bearing:
`native/server-profile.json` carries `+map` inside its committed
`serverArguments`, and the browser profile commits the matching
`engineArguments`. The rotation becomes an uncommitted launch argument in a later
work package, and both halves move together when it does. An integration built
before then should already keep its rotation in one place, so that change is a
substitution rather than a redesign.

Recorded as later hardening rather than a present requirement: the loader could
read the server's advertised rotation out of `serverinfo` before Start and verify
the subset relation itself, which would move the check back inside a component
that can fail closed.

## Updates, rollback and compatibility

Browser, content and server artifacts are one compatibility unit:

1. the browser and server manifests must name the same ioq3 commit;
2. the server manifest must name the exact browser and content manifest
   identities;
3. the staged browser tree must pass `stage-arena.py --check`;
4. the loaded image must pass the server-image verification and be addressed
   by an immutable published identity; and
5. the relay must implement the public datagram contract and report at least
   the 810-byte WebTransport datagram size the client requires.

Do not update the browser, QVM, content pack, server binary or fixed relay
profile independently. Publish a new tuple and a new browser release root,
make the server ready, update the relay route and only then select the new tuple
for new launches. Keep the previous immutable tree and image addressable for
rollback. Existing sessions need not be migrated between tuples.

Changing only product presentation or a stable launch selector does not change
the tuple. Changing any served byte, engine/content manifest, server runtime
file, server argument or relay protocol profile does.

### Which change creates which new identity

The `compatibility` block is **not** the full change surface. It carries seven
members, and a change can move authorities and served files that are none of
them — most importantly `arena/game-profile.json`, which is how a consumer
discovers the archives in the first place. Re-read the profile on every new
tuple; do not diff `compatibility` alone and conclude nothing else moved.

| Change | What moves |
| --- | --- |
| engine source on `web` | everything |
| engine build outputs (`ioquake3.js`/`.wasm`, QVMs) | browser manifest, server manifest, server image |
| browser loader/shell bytes (`loader.js`, `index.html`, shell JS) | one `servedFiles` entry and nothing else — they are in no manifest and no authority, so `compatibility` stays bit-identical |
| base pack content (QVM closure, player models, bots, notices) | content manifest, content payload, server manifest, server image |
| **a map added to or removed from the supported set** | three `compatibility` members — `contentManifestIdentity`, `serverManifestIdentity`, `serverImageId` — plus the browser profile, the content member provenance, the resource measurement and `servedFiles`. `contentPayloadIdentity` does **not** move, subject to the one condition below |
| the rotation a server plays | nothing |
| which archives a client fetches | nothing — a runtime selection from the already published set |
| relay profile | the relay-profile authority and the release index |
| product presentation, launch selector | nothing |

**The fifth row is why the content pack is split at all.** The base archive and
every archive that already existed stay byte-identical when a map is added, which
is asserted mechanically on every build: assemble the set, add a map, reassemble,
compare bytes. So a published archive's bytes and its URL never move, four of the
seven members stand still, and a consumer's update reduces to re-reading the
profile, fetching only the archives that are new to it, and updating three
digests. Served content names carry the first 16 hex of their own SHA-256, so
they may be cached immutably and a name collision cannot silently change content.

**The one condition on that row.** Base stability holds for a map assembled
from upstream sources this release already pins. A map that requires a *new*
source edits `content/pack-recipe.json`, which is both an authority and the base
archive's own selection input; the base's notice carries that file's digest, so
the base's bytes change and `contentPayloadIdentity` moves with them. That is a
full content reissue rather than an additive one. Every previously published
archive URL still resolves and its bytes are still what they were, but the base
is a new object and must be fetched again. A producer must say which of the two
a release is; a consumer must not infer additivity from the fact that maps were
added.

Adding a map remains a tuple event that needs a new release, and it is
deliberately batched: prepare many maps, publish few times.

## Distribution and licence obligations

The staged browser tree is executable distribution, not merely a link to this
repository. A distributor must provide an adjacent, durable **Source and
licences** link and meet all of these obligations:

- ship the browser notice set identified in
  [`wp1-build-evidence.md`](wp1-build-evidence.md#the-notice-set-a-browser-distribution-must-ship),
  including the ioquake3/component notices, Emscripten, musl, LLVM
  compiler-rt, IJG and SDL terms. That WP1 section records where the inventory
  originated; repository-local notices for this release resolve at the current
  engine pin `d594b1cc9bfc5b58ccebffd4d840a13782cb6592`, as the authoritative
  baseline lock requires;
- offer the corresponding public source identified in
  [`wp1-build-evidence.md`](wp1-build-evidence.md#corresponding-source): the
  current pinned ioq3 fork, this repository's build orchestration, the pinned
  Emscripten source and the pinned SDL port archive;
- preserve the six notices already packaged inside **every** archive — each
  is published under its own URL and redistributed on its own — and offer the
  digest-pinned preferred content sources and assembly source described in
  [`wp3-content-closure.md`](wp3-content-closure.md#attribution-and-source-offer-obligations-a-distributor-must-meet);
- distribute the server image with all 78 per-package Debian copyright files
  it inherits and preserves; meet the runtime base's corresponding-source and
  written-offer contract recorded in `locks/baseline.json`; and offer the
  corresponding engine, QVM, content and image-assembly sources; and
- describe the aggregate accurately: original arena-web and GPL engine/game
  code are GPL-2.0-or-later, while linked runtime components retain their own
  compatible licences. Do not apply one blanket label to every component.

The PK3 already carries its notices; the browser engine notice set is **not**
part of the staged tree and must be published alongside it. The server
image already carries the runtime-base copyright files, but a public image
listing still needs a source/licence link. A release is not publication-ready
until those links and files exist.

## Minimum integration acceptance

This contract was derived from already accepted WP7/WP8 artifacts; writing it
does not require another gameplay or network endurance round. A consuming
integration should nevertheless make these cheap checks before selecting a
release:

1. `scripts/check.sh` passes in the public checkout.
2. The staged browser tree and loaded server image pass their existing exact
   identity checks.
3. The server reaches the binary `getstatus` readiness state above.
4. The browser reaches `ready` before Start and, after one real user gesture,
   reaches `running` with relay state `open`.
5. Resize the running client and enter and leave HTML fullscreen; the engine
   adopts each new canvas size while the game and relay session remain alive.
6. Ending that relay session exposes `reconnect-ready`; reconnect obtains a
   fresh authorization and returns to `running` without restarting the server.
7. The public Source and licences link exposes the required notices and
   corresponding-source locations.

Steps 3 to 6 are the consuming environment's smoke test, not a request to
repeat the prototype's full acceptance. Failures at this boundary must stop
publication or selection of the tuple; they must not be papered over by
changing a checked profile locally.
