<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP11 immutable integration handoff

**Status:** Accepted producer contract for the first `arena-web` browser/server
release. This document is self-contained: a consumer does not need a private
repository, deployment topology or product implementation to understand any
field below. The immutable public Git commit containing this document and
[`release/browser-release.json`](../release/browser-release.json) is the
release source. Mutable branch names and local container tags are not release
identities.

The only supported profile is `arena-web-ffa`: FFA on
`oa_pvomit`, frag limit 15, no time limit, eight slots and three server bots.
The accepted browser is Chrome for Testing 152.0.7977.64 on Fedora Linux 44
`x86_64`; the dedicated server is Linux `amd64`. These are deliberately narrow
prototype bounds, not a wider platform or capacity claim.

## 1. Browser manifest, digests and public layout

The release index is outside the browser root to avoid hashing itself. Its
`servedFiles` array is the complete, path-sorted browser root: exactly 25
relative files, each with byte length and SHA-256. The staging validator checks
that list against both repository source and the generated artifact manifests;
an extra, missing, symlinked or changed file fails the release.

The primary immutable identities are:

| Input | Identity |
| --- | --- |
| Baseline lock | `sha256:227c9434ba306b5b95bb36f392b1d9faa08fdef5b325dd4d557d8c4b8ee55287` |
| Browser artifact manifest | `sha256:1fca91ba4198398198f90d52222de4e9e2a5d910e275061b2f605f13e45c8047` |
| Content artifact manifest | `sha256:3ec982977ea0d23dae03b565f68d6f3296796b2a3f9f49b25451713bc50055fe` |
| Base content archive | `sha256:caa003fcd7a79d3431a73166ed531d40b8a3d3728bca487d4b55c07d681c4229`, 40,985,746 bytes |
| Map archives | eight, one per map, enumerated in the content artifact manifest |

**The content is a set of archives, not one PK3.** A base archive carries
everything not tied to a map — the gamecode's own closure, the seven player
presentations, the bots and the notices — and one archive per map carries that
map and what only it reaches. Each is served under a name containing the first
16 hex characters of its own SHA-256 under an immutable cache policy, so a
published URL is never rewritten and a returning player re-downloads only what
actually changed. The dedicated server carries every archive.

**The client currently fetches every declared archive**, not only the ones for
the maps it will play. The committed profile declares the published set and the
loader verifies each artifact against the committed manifest before it starts,
so nothing is unverified — but the fetch is the whole set, and it grows with
the set. Selecting a subset per rotation is the next work package; until it
lands, size the first load against the total of every `filesystem` artifact in
`arena/game-profile.json`.

**`contentPayloadIdentity` now names the base archive.** The field name did not
change and no consumer sees an error, so it is called out here: it used to mean
"the content", and it now means "the base payload". The map archives are
covered through `contentManifestIdentity`, and every archive is additionally
bound byte-for-byte to its counterpart in the server manifest.

### Reproducing this release

Run this from a clean checkout of the release commit — the commit that carries
this document and `release/browser-release.json`:

```text
CONTAINER_RUNTIME=podman scripts/reproduce-release.sh
```

It rebuilds the browser, the content archives and the server image in the
pinned images and compares each generated record with the committed one in
full, plus the loaded image ID against `records/wp11-server-resources.json`.
Every comparison fails closed.

**Why it needs a command rather than a list of build invocations, and why that
is not a workaround.** A reissue is written in two commits and cannot be written
in one: a record names the commit whose sources produced it, and no commit can
contain a record naming itself. So the *source* commit carries new inputs beside
the previous release's generated records — the exact state every build script's
metadata gate refuses, correctly. A clean checkout of the commit named in
`producer.commit` therefore **cannot** run these builds, and any instruction to
try is wrong.

`scripts/reproduce-release.sh` resolves that by building from the release
commit, which does validate, while stamping the producer commit that the
committed records name. It reads that commit out of `manifests/browser-client.json`,
`provenance/arena-web-ffa-content-manifest.json` and
`provenance/arena-web-server.json` rather than accepting it from whoever runs
the build, so there is no operator assertion about what was built from where,
and no step that bypasses a gate or works on a dirty tree. The three build
scripts expose the same stamp as `--producer-commit` for anyone reproducing one
artifact set on its own.

Then check the served tree itself:

```text
scripts/check.sh
scripts/serve-arena.sh --stage-only
python3 scripts/stage-arena.py --target build/arena-serve --check
```

Publish `build/arena-serve/` atomically below one immutable same-origin URL and
keep all relative paths unchanged. The exact list and every individual digest
are in `release/browser-release.json`; a consumer must not maintain another
copy. Serve production over HTTPS, `.wasm` as `application/wasm`, JavaScript as
a JavaScript media type and JSON as `application/json`. The client is
single-threaded and does not require COOP/COEP or `SharedArrayBuffer`.

## 2. Exact browser lifecycle boundary

The stable same-origin API is `globalThis.arenaWeb`. It exists while static
loading is in progress and exposes:

```js
snapshot()
subscribe(listener)
configureRelay(configuration)
start()
stop()
whenSettled()
focusSurface()
setFullscreen(engaged)
reconnectRelay()
```

`report` and `engineLog()` remain diagnostics, not product state. A consumer
must not parse UI text or engine logs. Every `snapshot()` is a defensive copy;
mutating it cannot change the loader. It contains the public `status`, bounded
`error`, final `exit`, `progress`, surface/fullscreen state and safe relay
counters. It contains no authorization value. Progress has exactly
`{phase, loadedBytes, totalBytes, fraction}`: `phase` is `loading` until all seven
declared runtime artifacts verify and `verified` afterward; `fraction` is
clamped to 0..1, and byte totals become exact once artifact manifests have been
read.

`subscribe(listener)` registers a function, calls it **synchronously and
immediately** with the current snapshot, then calls it for later public state
changes. It returns an idempotent unsubscribe function. A listener exception is
reported to the browser console but does not change lifecycle or prevent other
listeners from running.

Call `configureRelay()` at most once before Start; the exact fields are in
section 6. Then call `start()` directly inside the player's transient user-
activation event. The built-in Start button calls this same function. It is
accepted exactly once and only from `ready`; its Promise resolves after any
relay assignment is open and engine boot has been scheduled. It does not mean
the map is live. A second call, including one made while the first is pending,
returns a rejected Promise with `LoaderError: Start has already been accepted`
and starts no second engine or relay. A call outside `ready` rejects without
changing state, and a synthetic/no-activation call rejects with
`Start requires transient user activation`.

The main status path is:

```text
starting -> ready -> booting -> running
    |          |        |         |
    +----------+--------+---------+-> stopping -> exited
                          \-> reconnect-ready -> reconnecting
                                  ^                  |
                                  +------------------+
any nonterminal state -> failed
```

`ready` proves all static bytes. `running` proves the engine emitted its
`clientGameLoaded` marker. Relay playability independently requires
`snapshot().relay.state === "open"`; an early transport close may leave the
engine marker and relay state on different paths. Reconnect is nonterminal,
does not restart the engine and invokes the token provider again for a fresh
one-time authorization.

`stop()` is valid during `starting`, `ready`, artifact loading, relay opening,
engine boot and `running`. Its first call returns the one stop Promise; every
later call returns that identical Promise. It aborts outstanding static loads,
closes the relay as a client stop without offering Reconnect, disables further
Start work and prevents late callbacks from restoring a live state. Before
engine boot it settles as `{status: "exited", exitCode: null,
reason: "host_stop"}`. Once Emscripten boot has begun it waits for runtime
initialization if necessary, invokes the exported `Web_RequestQuit` handoff on
the engine thread, and resolves only after the real Emscripten `onExit`. It
never presents a loader-only exit while the engine is still alive. The accepted
running-stop browser smoke returned engine exit code 0; stopping before engine
boot settles with `null` as described above.

`whenSettled()` always returns the same Promise and resolves exactly once. Its
value is `{status, exitCode, reason}` with terminal status `failed` or `exited`,
an integer engine exit code where one exists, otherwise `null`, and one bounded
reason. The current reason vocabulary is `host_stop`, `engine_exit`,
`engine_abort`, `engine_contract_error`, `engine_error` and `loader_error`.
`snapshot().exit` then contains the equivalent `{code, reason}`. It never
rejects, never settles for reconnect, and a later callback cannot settle it a
second time.

## 3. Runtime surface, focus and fullscreen

The exact runtime surface selector is:

```css
[data-runtime-surface="arena-web"]
```

It identifies the one `canvas#canvas`. The canvas is the keyboard and
pointer-lock target. Its surrounding `div#stage` is the HTML fullscreen
element. SDL owns the canvas backing store and receives the loader's observed
CSS-box resize; the engine remains internally windowed with `r_fullscreen=0`.
The supported embedding is same-origin only.

`focusSurface()` synchronously returns
`{ok, focused, reason}`. Success is `{ok:true, focused:true, reason:null}`;
refusal uses `not_supported`, `not_focused` or a bounded browser exception
name. `setFullscreen(boolean)` returns a Promise for
`{ok, engaged, reason}`. A request already in the desired state succeeds;
otherwise refusal uses `not_supported`, `state_mismatch` or a bounded browser
exception name. Entering fullscreen and pointer lock still require a real user
activation. There is no synthetic bypass. The built-in Fullscreen control uses
the same API.

## 4. Dedicated-server runtime contract

The exact server identities are:

| Input | Identity |
| --- | --- |
| OCI configuration/image ID | `sha256:2509723cb663bdbe02a00ff8fc4f6565297f73faa67f656e237b5e004cf2fa30` |
| Server artifact manifest | `sha256:763e1797c1965c13b02caf36d6e984cd9a0a0ecabbac42b62b2a423f14631834` |
| Server profile | `sha256:6d48c19238b1874bf30d276a8419ec771007f13739f0e69c447b33d412a69472` |

The image is `linux/amd64`, user/group `65534:65534`, workdir
`/opt/arena-web`, environment `HOME=/var/lib/arena`, entrypoint
`/opt/arena-web/ioq3ded`, no default arguments and one UDP endpoint, port
27960. A registry publication needs its own immutable registry manifest digest
and must load to the image ID above; `arena-web-server:latest` is only a local
build tag.

The build verifier reads the finished image and requires that complete OCI
configuration plus the exact title, engine, baseline and producer labels. It
also requires that there is no blanket `org.opencontainers.image.licenses`
label: the GPL arena/engine and mixed-license Debian runtime retain the
component-level authorities in section 9.

Reproduce that exact image with `scripts/reproduce-release.sh` above, or on its
own from a clean checkout of the release commit with

```text
scripts/build-server-image.sh --producer-commit <provenance/arena-web-server.json producer.commit>
```

after reproducing its browser, content and native inputs. The generated
`artifact-manifest.json` must equal the committed `provenance/arena-web-server.json`
byte-for-byte and the loaded image must have the image ID above.

The producer commit has to be passed rather than inferred, and this is the
reason: the image stamps it into an immutable OCI label, so a build that took
it from `HEAD` would produce a different image ID from any checkout other than
the one the record names — and that checkout, being a reissue's source commit,
cannot run the build at all. Passing the commit the record itself carries is
what makes the accepted image ID reachable. Omit the flag and you get a
different image, which is a new release, not this one.

Pass the exact `native/server-profile.json.serverArguments` array in its
committed order. In command-line notation it is:

```text
+set bot_enable 1 +set com_basegame arena +set com_legacyprotocol 0
+set dedicated 1 +set fraglimit 15 +set g_gametype 0 +set net_enabled 1
+set net_port 27960 +set sv_maxclients 8 +set sv_pure 0
+set sv_rateLimitPerPort 1 +set timelimit 0 +map oa_pvomit
+addbot Skelebot 1 free 2000 +addbot Rai 1 free 3500
+addbot Sly 1 free 5000
```

Drop all capabilities, set no-new-privileges, make the root filesystem
read-only and mount an initially empty, `rw,noexec,nosuid,nodev`, mode-1777,
64-MiB tmpfs at `/var/lib/arena`. That home holds only ephemeral engine config
and `games.log`. There is **no persistent path or volume** in this release; no
world, save, secret or host file is required. The read-only image is
125,209,549 bytes; the measured container writable layer after stop was 12,554
bytes and is disposable.

Readiness is the native binary UDP query, no more than once per second from a
stable source address and port:

```text
ff ff ff ff + ASCII "getstatus " + short whitespace-free hex challenge + "\n"
```

Require a response from the target endpoint beginning with four `0xff` bytes
and `statusResponse\n`; parse the following info string and require the echoed
challenge plus `mapname=oa_pvomit`, `g_gametype=0`, `fraglimit=15`,
`timelimit=0` and `sv_maxclients=8`. Discard the unneeded player-list tail.
Startup has a 20-second deadline. After readiness, three consecutive failed
one-second checks make the observation failed.

Send `SIGTERM` or `SIGINT` to the entrypoint and allow 10 seconds before a
forced kill. The normal signal path sends the final server message, closes the
VM/network and exits with code 1; code 1 is therefore success only when the
manager requested this stop. The measured graceful exit took 0.137 seconds.
Any unsolicited exit, including code 1, is failure.

## 5. Indivisible compatibility identity

The following tuple is one unit and must not be mixed with an earlier or later
loader, profile, QVM, pack, binary or relay profile:

```text
baseline          sha256:227c9434ba306b5b95bb36f392b1d9faa08fdef5b325dd4d557d8c4b8ee55287
ioq3               git:d594b1cc9bfc5b58ccebffd4d840a13782cb6592
browser manifest   sha256:1fca91ba4198398198f90d52222de4e9e2a5d910e275061b2f605f13e45c8047
content manifest   sha256:7785b2a65104257d1f0cd67d9b59771dc259726155acc54bdae0451cef92dfc5
content base       sha256:6c3341ef87d16c75b7d3fb5f368d9f935dac304c1dd7667f96b64dd73912bb03
server manifest    sha256:580654c261a364ad71a0ae5e92b6ad291032ae8f97e5f4276e557ea3a6081281
server image ID    sha256:2509723cb663bdbe02a00ff8fc4f6565297f73faa67f656e237b5e004cf2fa30
```

`release/browser-release.json.compatibility` repeats these values and its
validator derives all manifest identities from the named authority files.

## 6. Proxy adapter: virtual destination only

Configure relay mode before Start with this exact shape:

```js
arenaWeb.configureRelay({
  endpointUrl,                    // credential-free HTTPS URL; no query/fragment
  certificateHashes,             // array of 64-hex SHA-256 values, or [] for native roots
  destinationAddressHex,         // exactly 32 hex digits: virtual IPv6 destination
  destinationPort,               // integer 1..65535; normally 27960
  clientSourcePort,               // integer 1..65535; unique among live page sessions
  tokenProvider,                  // async/sync function returning one non-empty string
  keepAliveIntervalMilliseconds, // 0 or 1000..86400000
  assignmentTimeoutMilliseconds, // optional integer 1..60000; default 10000
});
```

The browser receives the public relay URL and the **virtual** IPv6 destination
only. It must never receive, infer or log the server's real IPv4/UDP endpoint.
The backend sends only to the configured virtual address/port and rejects any
other engine destination. The loader derives `+connect -6
[virtual-address]:port`; its snapshot redacts that argument. A compatible
relay privately maps the one authorized virtual destination to the server's
actual UDP 27960 endpoint and gives simultaneous clients distinct live
server-facing source ports.

Only the one-time authorization returned by `tokenProvider` is a credential.
The provider is called once per initial attempt and once per reconnect; even a
failed attempt spends its value. The backend sends it in the first
WebTransport datagram, clears its reference, never places it in a URL and never
returns it in reports, snapshots or errors. Endpoint, certificate digest,
virtual destination and ports are runtime topology/trust inputs, not secrets,
but remain outside this public release because they select an environment.

## 7. Exact first guest policy

This Arena profile is guest-playable. A consuming service may authorize either
a guest or an authenticated user only for a server that the service has
deliberately published and currently observes as `ready`. Arena requires no
account, character, saved login, progression or persistent state. The service
may still apply its ordinary platform-wide ban, rate-limit and abuse policy
before issuing authorization.

Each authorization names exactly one virtual destination. An unpublished,
stopped, missing, preparing or failed server receives no fresh authorization.
Every reconnect receives a new one-time value. A policy change affects new
attempts; this release adds no live-session revocation. Neither a guest nor an
authenticated player receives the native server endpoint.

## 8. Measured resource guard and observation semantics

[`records/wp11-server-resources.json`](../records/wp11-server-resources.json)
is the complete machine-readable measurement. The accepted per-instance guard
for this exact eight-slot/two-human/three-bot prototype is:

| Resource | Limit | Busy observed maximum | Remaining safety margin |
| --- | ---: | ---: | ---: |
| CPU | 1 core | 0.041056-core peak sample | 0.958944 core; 24.357x |
| Memory | 268,435,456 bytes | 29,724,672-byte peak cgroup; 31,039,488-byte process HWM | 238,501,888 bytes; 8.968x against cgroup peak |
| Writable home | 67,108,864 bytes | 1,272 bytes | 67,107,592 bytes; 52,758.541x |
| Processes | 128 PIDs | constrained successfully by the probe | guard, not a measured demand claim |

Startup readiness was 1.751 seconds. The ten-second idle phase averaged
0.020647 cores. The 30-second two-native-client phase, with movement, weapon,
fire, chat and respawn traffic while all three bots remained active, averaged
0.032544 cores. These values preserve large practical headroom, but are
capacity guards only—not an SLO, autoscaling rule, production concurrency
claim or evidence for more than two humans.

The complete observation vocabulary is:

- `missing`: no owned runtime exists;
- `preparing`: the desired process is running inside its 20-second startup
  deadline but has no valid readiness response;
- `ready`: the exact binary readiness profile has passed and fewer than three
  consecutive one-second post-ready checks have failed; and
- `failed`: the owned process exited unexpectedly, missed the startup deadline
  or failed three consecutive post-ready checks.

The probe witnessed all four states, graceful stop and an unexpected forced
exit (code 137). Resource-limit breach handling remains the owning process
manager's responsibility and yields `failed`, never `ready` merely because a
container object exists.

## 9. Complete provenance and licences

The release index hash-binds every authority below. Together they are the
complete shipped-code/content provenance manifest:

| Shipped part | Exact authority and obligation |
| --- | --- |
| Original loader, scripts and documentation | repository `LICENSE`, GPL-2.0-or-later, at the immutable release commit |
| Engine, QVMs and Emscripten runtime | `locks/baseline.json`, `manifests/browser-client.json` and the complete linked closure/notice table in `docs/wp1-build-evidence.md` |
| Content archives | `content/pack-recipe.json`, the per-map fragments under `content/maps/` that its content manifest records by digest, member-level `provenance/arena-web-ffa-content.json`, `provenance/arena-web-ffa-content-manifest.json` and `docs/wp3-content-closure.md` |
| Native server and base | `provenance/arena-web-server.json`, `native/server-profile.json` and the redistributed-runtime-base record in `locks/baseline.json` |

The browser distribution must expose a durable adjacent **Source and
licences** link containing: ioquake3 `COPYING.txt`; all Xiph/Opus, zlib,
minizip/Info-ZIP, Mumble Link, ADPCM, MD5, puff and QVM-libc notices enumerated
in the baseline; IJG `jpeg-9f/README`; the Emscripten licence
(`sha256:620a78084fc7ca97c0b5dea9abf891f3ffcadfdbf305276f099c9c4e12fc1d86`);
musl COPYRIGHT
(`sha256:b870108ec5e7790e9f9919064f1b9421d62d5f9b0e6c230c6adf7ea2da62e97b`);
LLVM compiler-rt LICENSE.TXT
(`sha256:1a8f1058753f1ba890de984e48f0242a3a5c29a6a8f2ed9fd813f36985387e8d`);
and SDL 2.32.10 LICENSE.txt
(`sha256:97f35b302b361680ec1e891e95d2d52097bb95abff361434916d99dc1305f127`).
The exact source URLs, expressions and included/excluded roles are the validated
records in `locks/baseline.json`; none may be replaced by an aggregate blanket
licence.

Corresponding source is the public ioq3 commit
`d594b1cc9bfc5b58ccebffd4d840a13782cb6592`, this repository at the browser
manifest's producer/release commits, emsdk commit
`e5bd3d0874e302a18f13c5b41f5bacf9a40c8e59`, the digest-pinned SDL port
archive and IJG source archive. Each archive already contains all six required
notice members (`COPYING`, `CREDITS`, `CREDITS-0.8.5`, `CREDITS-0.8.8`,
`README`, `NOTICE-arena-web.txt`) — **every archive carries the complete set**,
because each is published under its own URL and redistributed on its own; do
not repack them out, and offer the six
digest-pinned preferred OpenArena source archives plus the recipe/assembly
scripts. The server image must retain all 78 Debian per-package copyright
files and the runtime base's complete-corresponding-source/public-archive and
written-offer obligations recorded by the baseline.

This index is complete for the accepted bytes. Adding, removing or changing a
served file, linked component, content member, server layer, profile or source
offer creates a new release and requires a newly checked index.

## Producer acceptance evidence

The final producer state passed all 841 deterministic tests and the strict
25-file stage/index check. Two clean browser builds at producer checkout
`95f45b537dd0bb8b4a542b97d0f4281eefa7604a` produced the same browser manifest
and bytes. Three clean content assemblies at that checkout produced the same
archives, and a fourth with one map added left every archive that already
existed byte-identical — the property the archive split exists for, checked
mechanically by `scripts/verify-content-pack.sh` rather than argued. Two clean native-server builds and two image builds at producer
checkout `95f45b537dd0bb8b4a542b97d0f4281eefa7604a` produced the same binary,
server manifest and OCI image ID. The image verifier checked the exact OCI
configuration, complete added filesystem, unchanged runtime-base remainder and
all 78 per-package copyright files.

The final Chrome for Testing 152.0.7977.64 lifecycle run passed 22/22 checks.
It stopped during static loading and after actual Emscripten runtime
initialization; proved identical duplicate Stop Promises; observed immediate
subscription and idempotent unsubscribe; rejected a duplicate Start; reached
the loaded map with one virtual relay assignment and simultaneous
`status=running`/`relay.state=open`; exercised focus and fullscreen enter/leave;
then received the stable, exactly-once `{status:"exited", exitCode:0,
reason:"host_stop"}` result only after the engine quit export and Emscripten
`onExit`. Its saved result contains no authorization. The relay endpoint,
certificate, virtual test destination and one-time authorization were
runtime-only and discarded with the temporary local containers.

The final resource probe used the exact image ID above, produced the bound
record in section 8 and witnessed `missing`, `preparing`, `ready`, `failed`,
graceful stop and unexpected exit. The initial independent GPT-5.6-Sol review's
licensing, terminal-reentrancy, progress-state and acceptance-coverage findings
were fixed before these final runs. Its final review's release-validator,
obsolete-pin and failed-witness wording findings were likewise fixed: the
validator now requires the exact 20 role-to-path authorities and derives every
compatibility identity plus all manifest links from them.
The focused final GPT-5.6-Sol re-review then returned PASS with no blocker,
major or minor finding.

## Minimal consumer acceptance

A consumer need only: validate the immutable checkout; stage and hash-check the
25-file tree; verify the loaded image ID; observe exact server readiness; use
one real gesture to reach browser `running` plus relay `open`; exercise focus
and enter/leave fullscreen; call `stop()` and receive the real final settlement;
and make the complete Source and licences link durable. This is a smoke test,
not a repetition of the earlier packet census, multiplayer endurance or broad
browser matrix.
