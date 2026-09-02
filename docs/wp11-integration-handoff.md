<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP11 immutable integration handoff

**Status:** Accepted producer contract for the first `arena-web` browser/server
release. This document is self-contained: a consumer does not need a private
repository, deployment topology or product implementation to understand any
field below. The immutable public Git commit containing this document and
[`release/browser-release.json`](../release/browser-release.json) is the
release source. Mutable branch names and local container tags are not release
identities.

The only supported profile is `arena-web-ffa`: eight slots, no time limit, and
free-for-all or team deathmatch. **Neither the map nor the four per-server
settings are part of the profile.** The rotation is a launch argument on both
halves — section 9 is the rule that binds the two derivations together — and the
gametype, the frag limit and the bots are launch arguments on the server half,
with no committed default and published bounds (section 8).
The accepted browser is Chrome for Testing 152.0.7977.64 on Fedora Linux 44
`x86_64`; the dedicated server is Linux `amd64`. These are deliberately narrow
prototype bounds, not a wider platform or capacity claim.

## 1. Browser manifest, digests and public layout

The release index is outside the browser root to avoid hashing itself. Its
`servedFiles` array is the complete, path-sorted browser root: every relative
file with its byte length and SHA-256, and nothing else. The count is not
restated here, because it grows with every published map and a copy that has to
be rewritten each release is a copy waiting to be stale — read it off the array.
The staging validator checks
that list against both repository source and the generated artifact manifests;
an extra, missing, symlinked or changed file fails the release.

The primary immutable identities are:

| Input | Identity |
| --- | --- |
| Baseline lock | `sha256:227c9434ba306b5b95bb36f392b1d9faa08fdef5b325dd4d557d8c4b8ee55287` |
| Browser artifact manifest | `sha256:1fca91ba4198398198f90d52222de4e9e2a5d910e275061b2f605f13e45c8047` |
| Content artifact manifest | `sha256:c7f366994a9dda1d39720b18ea2e7bf91fb8dcd6e1f3916f2680432594004906` |
| Base content archive | `sha256:7cfa98c9fac1274ed45ee653572252e3d3d47c47c6d80163b59afd1c6354277c`, 55,304,102 bytes |
| Map archives | twenty-nine, one per map, enumerated in the content artifact manifest |

**The content is a set of archives, not one PK3.** A base archive carries
everything not tied to a map — the gamecode's own closure, the seven player
presentations in both their own and their team colours, the seven bots and the
notices — and one archive per map carries that map and what only it reaches.
**The base grew by 14,318,356 bytes at this release**, from 40,985,746, which is
what the team skins and the five added bot characters cost; that is a player's
forced first download and it does not scale with the rotation. No map archive
moved a byte. Each is served under a name containing the first
16 hex characters of its own SHA-256 under an immutable cache policy, so a
published URL is never rewritten and a returning player re-downloads only what
actually changed. The dedicated server carries every archive.

**The client fetches the base plus the archives it is told to, and the page has
to be told.** Open it as

```text
<release root>/index.html?maps=<map>[,<map>...]
```

naming the maps the server will rotate through. The base archive is implicit
and cannot be named. Names are canonicalised — sorted and de-duplicated —
because a rotation list may legitimately play the same map twice in a cycle, so
`?maps=b,a,b` and `?maps=a,b` fetch the same set. Every other published archive
is not fetched at all.

**The parameter is required, and both plausible defaults are wrong.** Falling
back to the whole set is the cost this exists to remove; falling back to the
profile's own map gives a client whose archive set is a strict *subset* of the
server's rotation, which is the one direction that fails — invisibly, until
rotation reaches the missing map and drops that client mid-match. So a page
opened without the parameter refuses before it fetches anything, with a message
naming the published maps.

Two consequences worth stating rather than leaving to be found:

- **The parameter is in a URL and is therefore editable by the person viewing
  the page.** That is not a trust question: the committed profile declares every
  published archive and the loader verifies each artifact it fetches against the
  committed manifest, so a selection is a choice from an already trusted set and
  never a new one. What an edited URL can do is give that viewer a rotation
  their server does not match — an `ERR_DROP` they brought on themselves. Do not
  treat the parameter as a protected control surface.
- **The loader records what it fetched, and what the server later said was
  missing.** `window.arenaWeb.snapshot().rotation` carries the parameter as
  given, the canonical map list, the published set, the archives it *selected*
  and the archives actually *fetched* — separate, because a run that failed
  partway differs in the second. Nothing can catch a rotation that is too small
  *before* it breaks; the engine does say so when it breaks, and
  `missingOnServer` carries that line (`CL_InitDownloads` prints
  `WARNING: You are missing some files referenced by the server` with the names,
  because `cl_allowDownload` is 0 on both profiles). It arrives at the map
  change that drops the client, so it prevents nothing — it names the cause
  inside the component that fails.

**Sizing a first load.** The content artifact manifest records, per archive,
`size` (packed), `uncompressedSize` (extracted) and — for a map archive —
`map` and `peakHunkBytes`, the measured peak engine hunk for that map. Sum the
base and the rotation's archives rather than the whole set. `peakHunkBytes` is
a per-map figure and a rotation's cost is the maximum, not the sum:
`Hunk_Clear` resets the marks on every map load.

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
counters. It contains no authorization value, and it carries `rotation` — what the page
was opened for and what it fetched (section 1). Progress has exactly
`{phase, loadedBytes, totalBytes, fraction}`: `phase` is `loading` until every
*selected* runtime artifact verifies and `verified` afterward. The count is
rotation-dependent and is deliberately not stated here; derive it from
`snapshot().rotation` or `identities`. `fraction` is clamped to 0..1, and byte
totals become exact once artifact manifests have been read.

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
`Start requires transient user activation`. Without a relay configuration the
engine runs a local listen server, and the map it starts is **the first entry
of `?maps=` as written** — the loader prepends `+map <that map>` to the
profile's committed `engineArguments`, which carry no map of their own. Its
archive is therefore in the fetch set by construction, so there is no Start
refusal for a missing offline map any more; the release before this one had one
because the started map was committed independently of the rotation. A relay
client starts no map of its own and takes its rotation from the server it
connects to.

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
| OCI configuration/image ID | `sha256:2f31235dd98f865b57f69c393dfec5008953927212577a9b6f583187b235a4a9` |
| Server artifact manifest | `sha256:4e3598bb8a61333e64ed76a1fe270c6e3e5420824bcacbcd3b4fd9d796411faa` |
| Server profile | `sha256:8c3ca45ec0f52c896ab4e41223dfedaaa34fbd62d9138f8eb31672ab0b5a15dc` |

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

Pass the **rotation arguments, then the launch settings, then** the exact
`native/server-profile.json.serverArguments` array in its committed order, and
finally any named bots. The committed array carries **no map and no per-server
setting** — either inside it would be a choice the caller cannot see — and
`scripts/arena_server.py` `server_launch_arguments` is the one supported
derivation of the whole line. In command-line notation, for a rotation of
`oa_pvomit, am_galmevish` at `bot_minplayers`:

```text
+set d1 "map oa_pvomit;set nextmap vstr d2"
+set d2 "map am_galmevish;set nextmap vstr d1"
+vstr d1
+set bot_minplayers 4 +set fraglimit 15 +set g_gametype 0 +set g_spSkill 3
+set bot_enable 1 +set com_basegame arena +set com_legacyprotocol 0
+set dedicated 1 +set net_enabled 1 +set net_port 27960
+set sv_maxclients 8 +set sv_pure 0 +set sv_rateLimitPerPort 1
+set timelimit 0
```

or, for the same rotation with a named cast in Team Deathmatch:

```text
+set d1 "map oa_pvomit;set nextmap vstr d2"
+set d2 "map am_galmevish;set nextmap vstr d1"
+vstr d1
+set fraglimit 15 +set g_gametype 3
+set bot_enable 1 +set com_basegame arena +set com_legacyprotocol 0
+set dedicated 1 +set net_enabled 1 +set net_port 27960
+set sv_maxclients 8 +set sv_pure 0 +set sv_rateLimitPerPort 1
+set timelimit 0
+addbot Liz 3 free 2000 +addbot Major 3 free 3500
```

One `d<N>` cvar per rotation entry, each loading its map and pointing `nextmap`
at the next; `vstr d1` enters the cycle. This is stock ioquake3's own idiom —
`ExitLevel` runs `vstr nextmap` and baseq3 carries no map list of its own
(ioq3 `code/game/g_main.c`). Only one placement is forced: every `+set` line is
applied by `Com_StartupVariable` before the command buffer runs at all, so the
cvars may sit anywhere, while `addbot` is forwarded to a game module that has to
be **running** and therefore must follow `vstr d1`.

### The four per-server settings

Four values are supplied at launch beside the rotation, and **none of them has a
committed default**. That is the same decision the map forced: a committed value
a caller may or may not override is a default nobody can see and nothing
reports — a server would run a gametype or a frag limit its operator never
chose and would look from outside exactly like one that was configured.
`native/server-profile.json.launchSettings` publishes the bounds so this can be
validated without running our code, and every bound there is checked against
what derives it.

| Value | Bound | Emitted as |
| --- | --- | --- |
| `gametype` | one of `launchSettings.gametypes` — `0` (GT_FFA) or `3` (GT_TEAM) | `+set g_gametype <v>` |
| `fraglimit` | `launchSettings.fraglimit.minimum`…`maximum`, 1…999 | `+set fraglimit <v>` |
| `bots.minPlayers` + `bots.skill` | 0…`launchSettings.bots.maxCount`, and 1…5 | `+set bot_minplayers <v>` and `+set g_spSkill <v>` |
| `bots.named[]` | 1…`maxCount` entries `{name, skill}` from `botRoster`, skill 1…5 | `+addbot <name> <skill> free <delay>`, delay `2000 + 1500·i` |

The bot half is **one shape or the other, never both**. `bot_minplayers` is the
one to prefer: the engine tops the server up and **removes bots again as humans
arrive** (`G_CheckMinimumPlayers`), it costs the same two console lines whatever
the bot count, and it is the only shape that distributes across two teams —
where the same function clamps the figure to `sv_maxclients / 2 − 1` per team,
so 3 at this profile's 8 slots. Named bots are the fine-grained alternative: a
fixed cast, each at its own skill, at one console line per bot.

**The gametype values are the pinned gamecode's, not ours.** They are read out
of `gametype_t` in ioq3 `code/game/bg_public.h`; this release chooses the two
*names*. GT_CTF is excluded because none of the 29 published maps is a CTF map,
which makes it a content campaign rather than a mode switch.

**Two things Team Deathmatch changes that are not settings.** Every packaged
player model ships a complete red/blue skin set and the build refuses a model
without one, so registration cannot fall through to ioquake3's unpackaged
`DEFAULT_TEAM_MODEL`. And one cosmetic defect remains and is accepted by name:
`cg_main.c` registers `powerups/blueflag` as the red team's quad shell under
`cgs.gametype >= GT_TEAM`, no pinned archive provides that Quake III Arena
name, so a red-team player carrying Quad Damage renders that one shell with the
default shader. It is reported at `PRINT_DEVELOPER` only.

**A rotation has a hard ceiling, and both halves of it are silent.** ioquake3
concatenates argv into a fixed `char commandLine[MAX_STRING_CHARS]` (1024 bytes)
with `Q_strcat`, which truncates through `Q_strncpyz` rather than failing, and
`Com_ParseCommandLine` stops taking console lines at `MAX_CONSOLE_LINES` (32)
and returns, leaving the rest neither parsed nor reported. Both numbers are
published in `native/server-profile.json.engineCommandLine` and are checked
against the pinned engine on every validation, so they cannot go stale.

**The ceiling is a property of the settings as much as of the map names**, and
that is new at this release: the non-rotation half of the command line is now a
caller's choice. Each row is the worst case its shape can reach *inside the
published bounds* — widest frag limit, highest skill, longest roster names —
because a ceiling stated for one configuration is not a ceiling.

| Bot setting | Fixed `+` commands | Distinct maps a rotation may hold |
| --- | ---: | ---: |
| `bot_minplayers` | 14 | 15 |
| 5 named bots | 17 | 13 |
| 6 named bots | 18 | 12 |
| 7 named bots | 19 | 11 |

**At this release the recommended configuration's ceiling is 15 of the 29
published maps** — the same figure as before these settings moved, which is a
coincidence of arithmetic and not a constant: `bot_minplayers` costs two `+set`
lines where three committed `+addbot` lines and two committed setting cvars used
to sit. **It meets the old ceiling; it does not raise it.** The second of those
two lines is `g_spSkill`, and it is the one an outside derivation forgets:
`G_AddRandomBot` reads that cvar and passes it to `addbot`, so difficulty in
this shape is a console line of its own.

The **line** bound is a pure count — the initial line, the fixed `+` commands,
and `n + 1` for a rotation of *n* maps — so it permits `32 − 2 − fixed` maps
whatever they are called. Every named-bot row above is exactly line-bound. The
`bot_minplayers` row is not: 15 maps come to 31 console lines and the sixteenth
is refused on **bytes**.

The **byte** bound moves with the names. `max_server_rotation` answers for the
published list *in order*, so it reports 16 for the `bot_minplayers` row where
the worst *distinct* rotation is 15; the table above answers the question a
validator must ask. At the widest `bot_minplayers` settings the alphabetically
first fifteen assemble to 952 of 1024 bytes and the fifteen **longest** to 984.
The check refuses at 1024 rather than above it, so that is room for **39**
further characters — where before these settings moved it was two, because five
console lines of committed `+addbot` text have left the line. One extra
character in a rotated name costs exactly one byte, and a named cast spends that
headroom again.

**And a rotation may repeat a map** — that is deliberate, a cycle may
legitimately visit one map twice — in which case the bound can be exceeded below
fifteen entries: fifteen entries of `am_underworks2` assemble to 1036 bytes and
are refused.

So treat `max_server_rotation` as what it is: an upper bound for distinct maps
in the published order, at one configuration, not a promise about an arbitrary
fifteen-entry rotation. The binding check is `server_launch_arguments`, which
refuses a rotation and setting pair that would not fit before it produces a
command line — fail-closed, and the only figure that answers for the server you
actually launch. Do not interpolate the ceiling from a map count in either
direction, and do not carry it across a change of settings. A rotation is
bounded far below it in practice by what a player downloads, which section 1's
per-archive figures let you compute.

**A rotation only advances when a level ends, and this profile gives a level
exactly one way to end.** `CheckExitRules` (ioq3 `code/game/g_main.c`) returns
early for sudden death — `if ( ScoreIsTied() ) return;`, ahead of both limit
checks — and 0:0 is a tie, so a map on which two or more players are playing
and nobody scores never exits, on the time limit or on anything else.

Read the guard exactly, because it is narrower than it looks: `ScoreIsTied`
returns false when `level.numPlayingClients < 2`, so an **empty** server is not
in sudden death and a non-zero `timelimit` would end its level normally. This
release's profile commits `timelimit 0`, which leaves the frag limit as the
only exit — so here an idle or empty server does stay on its map indefinitely,
but for that reason rather than for the tie. A profile that set a time limit
would behave differently, and "the time limit bounds how long a map runs" is
false only under a tie.

There is **no automatic second path**: `vstr nextmap` is sent from `ExitLevel`
and nowhere else in the gamecode (`g_main.c:1072`). The manual paths all need
players, so none of them is available in the state that produces a stall on an
empty server:

- a passed `callvote nextmap`, which builds the same command string
  (`g_cmds.c:1388`);
- a passed `callvote timelimit <n>` or `callvote fraglimit <n>`, which change
  *when* a level ends and can therefore advance a stalled rotation — and can
  equally set both to zero at run time, undoing there what this release's
  build-time rule refuses — `arena-web` requires the committed `fraglimit` and
  `timelimit` not to be both zero, and a vote is outside that gate's reach.

`g_allowVote` defaults to `1` (`g_main.c:158`) and neither profile disables it,
so on an occupied server those ways out exist; on an empty one none does.

A server launched with **no** rotation is not a special case that needs its own
error path: it comes up mapless and never answers a `getstatus` the readiness
contract below accepts, so the omission fails the gate you already run instead
of surfacing at a map change.

Drop all capabilities, set no-new-privileges, make the root filesystem
read-only and mount an initially empty, `rw,noexec,nosuid,nodev`, mode-1777,
64-MiB tmpfs at `/var/lib/arena`. That home holds only ephemeral engine config
and `games.log`. There is **no persistent path or volume** in this release; no
world, save, secret or host file is required. The read-only image is
289,788,367 bytes; the measured container writable layer after stop
was 12,588 bytes and is disposable.

Readiness is the native binary UDP query, no more than once per second from a
stable source address and port:

```text
ff ff ff ff + ASCII "getstatus " + short whitespace-free hex challenge + "\n"
```

Require a response from the target endpoint beginning with four `0xff` bytes
and `statusResponse\n`; parse the following info string and require the echoed
challenge plus `g_gametype`, `fraglimit`, `timelimit` and `sv_maxclients` equal
to `native/server-profile.json`'s committed cvars — read from the profile
rather than copied, since a later release may set them differently. Discard the
unneeded player-list tail.

**`mapname` is checked differently at readiness and afterwards.** At readiness
require it to equal the **first entry of the rotation you launched with**: that
is the caller's own expectation, and it is what proves the launch argument took
effect and therefore that a rotation was supplied at all — a server that
ignored or never received one does not become ready. **After readiness require
only that `mapname` is some entry of that rotation.** `SV_SpawnServer` sets
`mapname` afresh on every map change (ioq3 `code/server/sv_init.c`), which is
the entire point of a rotation, so a repeated check pinned to the first entry
would declare a healthy server failed a few seconds into its second map, every
cycle. A map from outside the rotation is still a failure in both phases.

Startup has a 20-second deadline. After readiness, three consecutive failed
one-second checks make the observation failed.

Send `SIGTERM` or `SIGINT` to the entrypoint and allow 10 seconds before a
forced kill. The normal signal path sends the final server message, closes the
VM/network and exits with code 1; code 1 is therefore success only when the
manager requested this stop. The measured graceful exit took 0.113 seconds.
Any unsolicited exit, including code 1, is failure.

## 5. Indivisible compatibility identity

The following tuple is one unit and must not be mixed with an earlier or later
loader, profile, QVM, pack, binary or relay profile:

```text
baseline          sha256:227c9434ba306b5b95bb36f392b1d9faa08fdef5b325dd4d557d8c4b8ee55287
ioq3               git:d594b1cc9bfc5b58ccebffd4d840a13782cb6592
browser manifest   sha256:1fca91ba4198398198f90d52222de4e9e2a5d910e275061b2f605f13e45c8047
content manifest   sha256:c7f366994a9dda1d39720b18ea2e7bf91fb8dcd6e1f3916f2680432594004906
content base       sha256:7cfa98c9fac1274ed45ee653572252e3d3d47c47c6d80163b59afd1c6354277c
server manifest    sha256:4e3598bb8a61333e64ed76a1fe270c6e3e5420824bcacbcd3b4fd9d796411faa
server image ID    sha256:2f31235dd98f865b57f69c393dfec5008953927212577a9b6f583187b235a4a9```

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
  playerName,                     // the player's own name; see the rule below
  playerModel,                    // one of relay-profile.json playerSettings.models
});
```

### The player's name and model

Both are **runtime inputs of the same class as the endpoint and the
destination**: per-session, never committed, and in the name's case never
reported. They exist because without them a hosted server calls every human
`UnnamedPlayer` and gives them all the same face — ioq3 registers `name` with
that default (`code/client/cl_main.c`) and this release used to pin `model` and
`headmodel` shut in the committed relay profile.

`arena/relay-profile.json.playerSettings` publishes what they may be, and
`scripts/arena_runtime.py` checks each bound against the thing that derives it:

| Field | Bound |
| --- | --- |
| `playerModel` | exactly one of `playerSettings.models`, which is exactly the player models `content/pack-recipe.json` packages |
| `playerName` | 1 to `playerSettings.name.maxLength` characters; printable ASCII without `"`, `;`, `\` or `^`, and without a repeated space |

**Three failures, three answers, and the split is deliberate.** A name that is
too long is **truncated**, not refused — a session must not fail over a name
that is merely long. The bound is `MAX_NETNAME - 1` (ioq3
`code/game/g_local.h`), the number of characters `ClientCleanName` copies into
`client->pers.netname` before its `outpos < outSize - 1` loop stops; truncating
on the producer side rather than leaving it to the engine is what makes the
stored name predictable instead of merely bounded. Leading and trailing spaces
are **trimmed**, because they are invisible, because `ClientCleanName` discards
leading ones itself, and because a cut can otherwise expose a trailing one.
Forbidden **content is refused**, because it is the only one of the three a
player can see and report.

`^` is refused rather than passed through, and that is the decision worth
stating: `ClientCleanName` reads `^` plus a character as a colour code, drops it
outright when the colour is black, and substitutes `"UnnamedPlayer"` when
nothing colourless survives. Accepting it would mean a name that silently
becomes colour, or silently becomes the very default this setting exists to
replace — neither diagnosable by the player who typed it. `\`, `"` and `;` are
refused as the userinfo separator, the quoting character the engine's own
command-line assembly adds, and the console command separator.

**The command line beats an archived value.** `name`, `model` and `headmodel`
are `CVAR_USERINFO | CVAR_ARCHIVE`, so a value could survive in the browser
filesystem's `q3config.cfg` — but `Com_Init` runs `Com_ExecuteCfg()` and *then*
`Com_StartupVariable(NULL)` with the comment "override anything from the config
files with command line args" (ioq3 `code/qcommon/common.c`). What the
integration passes is what the session runs.

**The model choice survives Team Deathmatch.** `cg_forceModel` defaults to `0`
(`code/cgame/cg_main.c`), so `CG_NewClientInfo` keeps each client's own model
and only the *skin* becomes red or blue; the substitution of
`DEFAULT_TEAM_MODEL` happens only under `cg_forceModel`, which nothing in this
release sets.

**`headmodel` is set with `model` and cannot be omitted.** ioq3 registers it
separately with its own `sarge` default, and `sarge` is not in this pack, so a
model set without it would give the player a packaged body and a head that fails
to register — a `CG_Error`, not a mismatch. The loader emits both from the one
`playerModel` value.

The player's name is **redacted out of the evidence** alongside the relay
destination: `snapshot()` and `report.engineArguments` carry `[player name]` in
its place. The model is not redacted, because it is a choice from a committed
set and knowing which one a client registered is evidence rather than
disclosure.

The relay client's worst case is **427 of 1024 bytes and 18 of 32 console
lines**, measured with `engine_command_line` over a 35-character name, the
longest offered model, a full IPv6 destination and the loader's three
render-size arguments. The client half is nowhere near either ceiling and does
not bound a server's rotation.

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
| CPU | 1 core | 0.045365-core peak sample | 0.954635 core; 22.043x |
| Memory | 268,435,456 bytes | 30,609,408-byte peak cgroup; 31,719,424-byte process HWM | 237,826,048 bytes; 8.77x against cgroup peak |
| Writable home | 67,108,864 bytes | 1,272 bytes | 67,107,592 bytes; 52,758.541x |
| Processes | 128 PIDs | constrained successfully by the probe | guard, not a measured demand claim |

Startup readiness was 1.75 seconds, which is a poll-loop
figure and not a startup time: the readiness probe waits out a 0.75-second
socket timeout and then a one-second interval, so it says the server was ready
before the second poll and no more than that. The ten-second idle phase
averaged 0.020968 cores. The 30-second two-native-client phase, with
movement, weapon, fire, chat and respawn traffic while the bots remained
active, averaged 0.033733 cores. These values preserve large practical headroom, but are
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

The final producer state passed the deterministic test suite and the strict
stage/index check over every file the release index names. Reproducibility is not asserted here but performed:
`scripts/reproduce-release.sh`, run from a clean checkout of the commit that
carries this document, rebuilds the browser, the content archives, the native
server and the image and compares the browser manifest, the content manifest,
the member-level content provenance, the server manifest and the loaded image
ID with the committed ones, byte for byte and failing closed. Content
assemblies additionally reassemble the set with one map added and require every
archive that already existed to be byte-identical — the property the archive
split exists for, checked mechanically by `scripts/verify-content-pack.sh`
rather than argued. The image verifier checked the exact OCI configuration,
complete added filesystem, unchanged runtime-base remainder and all 78
per-package copyright files.

Counts and producer checkouts are deliberately not restated in this paragraph.
They moved with every reissue and were wrong twice; the release index and the
`producer` field of each manifest are where they are checked.

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

A consumer need only: validate the immutable checkout; stage and hash-check the tree the
release index names, whose size grows with the published map set; verify the loaded image ID; observe exact server readiness; use
one real gesture to reach browser `running` plus relay `open`; exercise focus
and enter/leave fullscreen; call `stop()` and receive the real final settlement;
and make the complete Source and licences link durable. This is a smoke test,
not a repetition of the earlier packet census, multiplayer endurance or broad
browser matrix.
