<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Browser-client and game-server integration contract

**Status:** Normative for the accepted `arena-web` vertical slice. This document
describes the public boundary an integration needs in order to publish the
browser client, run its matching dedicated server and connect the two through a
compatible relay. It does not prescribe a hosting product, deployment system or
user interface.

`arena-web` deliberately has several small machine-readable sources of truth
rather than one release file that repeats all of them:

| Question | Source of truth |
| --- | --- |
| Which browser and QVM bytes are accepted? | [`manifests/browser-client.json`](../manifests/browser-client.json) |
| Which content pack is accepted? | [`provenance/arena-web-ffa-content-manifest.json`](../provenance/arena-web-ffa-content-manifest.json) |
| Which files and engine arguments does the browser load? | [`arena/game-profile.json`](../arena/game-profile.json) |
| Which fixed relay profile does the browser enforce? | [`arena/relay-profile.json`](../arena/relay-profile.json) and the [relay datagram contract](relay-datagram-contract.md) |
| Which server files are accepted? | [`provenance/arena-web-server.json`](../provenance/arena-web-server.json) |
| How is the server started? | [`native/server-profile.json`](../native/server-profile.json) |
| Which public sources, toolchains and licences apply? | [`locks/baseline.json`](../locks/baseline.json), the manifests above and the member-level records under [`provenance/`](../provenance/) |

An integration must consume those files together from one reviewed release
checkout and must run `scripts/check.sh` before packaging them. Values below
are explanations of that checked contract, not competing defaults. Reproducing
the already accepted historical server image uses its separately recorded
producer checkout only for the build, then compares the generated manifest
back to this release checkout as described below; it is not permission to mix
release metadata from two revisions.

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
| ioq3 engine | `git:968eeb44294aa0003c430430cf32a6540f9a81e4` |
| Browser loader, network backend and checked runtime profiles | `git:1d0f032ad294804275553c1e33ca306ce2baf7b7` |
| Browser artifact manifest | `sha256:8abd6b7a6f7d278ad95c753a5db9f1eff6be8ff08645c2f8ac4d91d7665e3f09` |
| Content artifact manifest | `sha256:f1e5453e6ecab0b251512cadee8f1a16de446bcc11c9038c93961f045765c7e1` |
| Server artifact manifest | `sha256:640933d6beecd79b88c02d73301de0ab60b7b3037937a690fc4a33f10aeefa1f` |
| Server image producer/build checkout | `git:fb58dd54bfe8eee196efd4d7a41950021ddcd141` |
| Accepted native server image ID | `sha256:ab6cd95dfed886778be5e5063a9f3669313fed3787d6a71b696e3a170d4f07bf` |

The image value is the reproducible container configuration/image ID observed
after loading the accepted single-platform image, not a promise that every
registry will use that value as its manifest digest. A registry publication
must be pinned by its own immutable manifest digest and must additionally load
to the accepted image ID. An archive transfer must likewise verify the loaded
image ID rather than trusting its filename.

The accepted image records its producing repository commit in an OCI label. To
reproduce that historical image, build all accepted inputs and run
`scripts/build-server-image.sh` from a separate clean checkout at
`fb58dd54bfe8eee196efd4d7a41950021ddcd141`. That checkout predates the commit
which copied the resulting server manifest into `provenance/`, so its committed
copy is not the comparison target. Instead, compare its newly generated
`build/server-image/artifact-manifest.json` byte-for-byte with
`provenance/arena-web-server.json` from this release checkout, require the
manifest identity `sha256:640933d6beecd79b88c02d73301de0ab60b7b3037937a690fc4a33f10aeefa1f`,
and require the loaded image ID
`sha256:ab6cd95dfed886778be5e5063a9f3669313fed3787d6a71b696e3a170d4f07bf`.

A rebuild from the current documentation commit or any other later commit has
a new image ID even when all four runtime files are byte-identical, because its
producer label is different. Such a rebuild must be published under a new
identity. Its generated manifest must match every content and input field in
`provenance/arena-web-server.json`, allowing only the documented
`producer.commit` difference; any other difference is a new content tuple that
requires its own reviewed manifest. Neither case may be silently called the
already accepted image.

The [WP7 acceptance report](wp7-routed-acceptance-2026-09-01.md) binds this
tuple to the final packet census and routed two-browser result. The
[WP8-Mini report](wp8-mini-acceptance-2026-09-01.md) adds five minutes of
concurrent play and reconnect of both clients without changing an owning input.
The [WP10 report](wp10-canvas-resize-acceptance-2026-09-01.md) accepts runtime
canvas resize and HTML fullscreen under the updated loader/profile identity;
the engine, content and server artifacts remain unchanged.

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
verified directory as one unit. The current tree contains exactly these 16
files:

```text
index.html
loader.js
default.cfg
game-profile.json
arena/canvas-resize.js
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
content/baseq3/arena-web-ffa.pk3
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
as `application/json`, HTML as `text/html`, and the PK3 as an opaque binary.
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

The player must activate the page's real **Start** button. The gesture grants
audio activation and allows later pointer lock; replacing it with a synthetic
click is unsupported. The canvas fills its page and derives an engine
resolution of at least 320 by 240 CSS pixels from its live box.

After startup, a `ResizeObserver` forwards changes of that CSS box into SDL's
existing Emscripten resize path. ioq3 then adopts the new custom resolution
after its existing delayed renderer resize. The browser profiles deliberately
set `r_fullscreen=0`: the HTML stage owns fullscreen, while the engine continues
to treat the canvas as a resizable window. Ordinary resize and entering or
leaving HTML fullscreen therefore reinitialize the renderer at the new size;
they do not restart the game or relay session. `snapshot().render`
retains the startup CSS dimensions and separately reports the current CSS
dimensions, observer availability and observed-event count for diagnostics.

The supported host-facing observation API is read-only:

```js
globalThis.arenaWeb.snapshot()
globalThis.arenaWeb.engineLog()
```

The important `snapshot().status` transitions are engine/UI milestones, not a
complete transport state machine:

```text
starting -> ready -> booting -> running
                         \-> failed
                 \-> reconnect-ready
running -> reconnect-ready -> reconnecting -> running
                                      \-> reconnect-ready or failed
running -> exited
```

`ready` means every static artifact was fetched and verified. In relay mode,
the engine boots only after the relay assigned the session, and `running` means
the engine subsequently emitted its `clientGameLoaded` marker. It does **not**
mean the assigned transport is still open: a relay that closes during engine
boot can expose `reconnect-ready` before the later engine marker writes
`running`. An integration must therefore require both
`snapshot().status === "running"` and `snapshot().relay.state === "open"` for a
currently playable relay client. `failed` is terminal for this page load.

When an established relay session ends, the engine stays alive, the overlay
offers **Reconnect**, and the next attempt invokes `tokenProvider` again. A
path-budget failure is terminal rather than retryable. The transport must
report `maxDatagramSize >= 810`: the enforced 768-byte inner floor plus the
42-byte single-datagram relay overhead. After an established session, other
transport failures expose the reconnect path and spend a fresh authorization.
A failure during the initial open leaves the page in `failed`; retrying that
case requires a reload and another fresh authorization.

`report` is exposed for acceptance diagnostics, but an integration must treat
it as read-only and must not depend on unlisted counters as a product API. The
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

Start the entrypoint with `native/server-profile.json.serverArguments` in its
committed order. Those arguments are validated as the exact derivation of the
profile's cvars, map and bots. Do not append an override or replace the bundled
configuration: that creates a different, unsupported server profile.

The runtime should apply the same confinement used by acceptance:

```text
drop all Linux capabilities
set no-new-privileges
use a read-only root filesystem
mount an empty rw,noexec,nosuid,nodev tmpfs with mode 1777 at /var/lib/arena
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

The profile provides eight total slots and starts three bots. Ordinary server
behavior may remove bots as human slots are needed, so “three bots” is not a
five-human capacity guarantee. Only two concurrent humans were accepted. No
numeric production CPU, memory, tmpfs-size, player-capacity or SLO guarantee
has been measured. A hosting system may impose resource controls, but it must
validate its chosen limits separately and must not present the prototype
observations as production guarantees.

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
Readiness becomes false after a timeout, a malformed response or a profile
mismatch. A transient failed query need not by itself kill a still-live
process; restart policy belongs to the hosting environment.

The image entrypoint receives container signals directly. On `SIGTERM` or
`SIGINT`, the pinned engine sends its final server message, shuts down the game
VM and network state, and then exits with status 1. A process manager must treat
that non-zero status as an expected operator stop when it sent the signal; the
same status without a requested stop remains a failure. The accepted harness
allows ten seconds before forcing termination. There is no persistent world to
flush, restore or migrate.

Remote administration is deliberately disabled: the profile is LAN-dedicated,
publishes no master-server heartbeat, and sets the RCON, private-server and game
passwords empty. Lifecycle belongs to the container/process manager, not to a
network admin command.

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

## Distribution and licence obligations

The staged browser tree is executable distribution, not merely a link to this
repository. A distributor must provide an adjacent, durable **Source and
licences** link and meet all of these obligations:

- ship the browser notice set identified in
  [`wp1-build-evidence.md`](wp1-build-evidence.md#the-notice-set-a-browser-distribution-must-ship),
  including the ioquake3/component notices, Emscripten, musl, LLVM
  compiler-rt, IJG and SDL terms. That WP1 section records where the inventory
  originated; repository-local notices for this release resolve at the current
  engine pin `968eeb44294aa0003c430430cf32a6540f9a81e4`, as the authoritative
  baseline lock requires;
- offer the corresponding public source identified in
  [`wp1-build-evidence.md`](wp1-build-evidence.md#corresponding-source): the
  current pinned ioq3 fork, this repository's build orchestration, the pinned
  Emscripten source and the pinned SDL port archive;
- preserve the six notices already packaged inside the PK3 and offer the
  digest-pinned preferred content sources and assembly source described in
  [`wp3-content-closure.md`](wp3-content-closure.md#attribution-and-source-offer-obligations-a-distributor-must-meet);
- distribute the server image with all 78 per-package Debian copyright files
  it inherits and preserves; meet the runtime base's corresponding-source and
  written-offer contract recorded in `locks/baseline.json`; and offer the
  corresponding engine, QVM, content and image-assembly sources; and
- describe the aggregate accurately: original arena-web and GPL engine/game
  code are GPL-2.0-or-later, while linked runtime components retain their own
  compatible licences. Do not apply one blanket label to every component.

The PK3 already carries its notices; the browser engine notice bundle is **not**
part of the 16-file staged tree and must be published alongside it. The server
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
