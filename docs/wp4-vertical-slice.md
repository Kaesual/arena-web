<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP4 evidence: offline browser vertical slice

**Status:** complete — the witnessed round of 2026-08-30 passed and its closure
decisions are recorded below. The renderer defect that round found has since
been root-caused and fixed in the engine, the configuration workaround it
needed is removed, and the other two symptoms are reclassified; see "The defect
class, resolved" below. The sections between are kept as the record of how the
round actually went.

This document records what the offline one-map FFA slice is, how the engine is
booted, which bytes a local serve is allowed to hand to the browser, what the
automated pre-acceptance run observed in the exact WP0 browser, and — just as
precisely — which parts of WP4's acceptance a person had to witness.

The slice is built and green: the pinned browser loads it from a clean local
serve, the engine boots, the map is entered with three bots that fight each
other, every runtime artifact matches its committed manifest identity, and no
QVM rejection, uncaught exception or renderer-fatal error occurs.

WP4 itself changed no lock, schema, manifest, provenance record, content recipe
or WP0/WP1/WP2/WP3 script, and left `ioq3/` untouched at its pinned commit. The
post-closure renderer fix did move the engine pin, deliberately and under its
own amendment; everything WP4 built is unchanged by it apart from the removed
workaround.

## What was built

| Path | Role |
| --- | --- |
| [`arena/index.html`](../arena/index.html) | the product loader page: canvas, start gate, fullscreen control, pointer-lock hint |
| [`arena/loader.js`](../arena/loader.js) | the product loader: manifest-verified artifact loading, engine boot, instrumentation |
| [`arena/default.cfg`](../arena/default.cfg) | the product's own engine configuration and key bindings |
| [`arena/game-profile.json`](../arena/game-profile.json) | the content configuration: game directory, map, bots, cvars, served artifacts, engine arguments |
| [`scripts/arena_runtime.py`](../scripts/arena_runtime.py) | the packaging discipline: profile validation, the served allowlist, digest verification, staging |
| [`scripts/stage-arena.py`](../scripts/stage-arena.py) | the staging command (`--check` re-verifies an existing tree) |
| [`scripts/serve-arena.sh`](../scripts/serve-arena.sh) | stage, verify and serve on loopback |
| [`scripts/browser_session.py`](../scripts/browser_session.py) | a dependency-free WebSocket/DevTools client and launcher for the pinned browser |
| [`scripts/arena_acceptance.py`](../scripts/arena_acceptance.py) | the automated pre-acceptance run and its checks |
| [`scripts/run-arena-acceptance.sh`](../scripts/run-arena-acceptance.sh) | the pre-acceptance entry point |
| [`tests/test_arena_runtime.py`](../tests/test_arena_runtime.py) | 134 deterministic tests, raising the suite from 287 to 421 |

The loader is original arena-web code. ioquake3's generated Emscripten demo
shell was read for interface knowledge — which `Module` keys the emitted
JavaScript consumes — and is otherwise untouched: WP1 records it as build
evidence, the packaging allowlist below refuses to serve it, and a test asserts
that none of its distinguishing markers appears in `arena/index.html`.

## How the engine is booted

Everything the engine is told comes from `arena/game-profile.json`, and
`scripts/arena_runtime.py` recomputes the whole command line from that file's
declarative fields and refuses the profile if the committed list differs. The
loader therefore consumes `engineArguments` verbatim and cannot drift away from
the profile it claims to start.

### The command line

```text
+set bot_enable 1  +set com_basegame arena  +set fraglimit 15
+set g_gametype 0  +set headmodel skelebot/default  +set model skelebot/default
+set net_enabled 0  +set r_allowResize 1  +set sv_maxclients 8
+set sv_pure 0  +set timelimit 0
+map oa_pvomit
+addbot Skelebot 3 free 2000
+addbot Rai 3 free 3500
+addbot Sly 3 free 5000
+set r_mode -1  +set r_customwidth <canvas width>  +set r_customheight <canvas height>
```

Why each part, against the pinned tree:

| Argument | Derived from |
| --- | --- |
| `+set` lines are order-independent | `Com_ParseCommandLine` splits the line at `+`, `Com_StartupVariable` applies every `set` before anything else runs, and it does so **again** after `Com_ExecuteCfg`, so no configuration file can override them (`code/qcommon/common.c`). |
| `+map` then `+addbot` | The remaining lines go to the command buffer in order (`Com_AddStartupCommands`). `addbot` is not an engine command; `Cmd_ExecuteString` forwards an unknown command to the running server game module (`code/qcommon/cmd.c`), which is `Svcmd_AddBot_f` (`code/game/g_bot.c`). It therefore has to follow `+map`. |
| `addbot <name> <skill> free <delay>` | Exactly `Svcmd_AddBot_f`'s argument order. The delays 2000/3500/5000 ms reproduce `G_SpawnBots`' own cadence, `BOT_BEGIN_DELAY_BASE` plus `BOT_BEGIN_DELAY_INCREMENT` per bot. |
| `com_basegame arena` | **Not** `baseq3`. `FS_CheckPak0` leaves `com_standalone` at 0 whenever the base game directory is ioquake3's own, and the engine then refuses to start without the retail `pak0.pk3`–`pak8.pk3` (`code/qcommon/files.c`). Any other directory name selects standalone operation, which is what a game built on this engine is. `scripts/arena_runtime.py` refuses a profile that names `baseq3`. |
| `model` / `headmodel` | `code/client/cl_main.c` defaults both to `sarge`, which is retail Quake III data. `CG_NewClientInfo` falls back to `DEFAULT_MODEL` and `CG_Error`s if that also fails to register (`code/cgame/cg_players.c`), so leaving the default would drop the client on the first player. |
| `bot_enable 1` | `Svcmd_AddBot_f` returns immediately when it is 0. |
| `g_gametype 0` | `GT_FFA` (`code/game/bg_public.h`). |
| `fraglimit 15`, `timelimit 0` | The arena profile of `content/pack-recipe.json`; `arena_runtime.py` asserts the equality, so the two cannot drift. |
| `sv_maxclients 8` | Slots for the local player and the bots (`code/game/g_main.c`). |
| `sv_pure 0` | Load-bearing, not hygiene: `FS_FindVM` (`code/qcommon/files.c:1398`) only considers a loose `vm/*.qvm` in a directory search path while `fs_numServerPaks` is 0 (`:1419`). The three QVMs are written beside the pack rather than inside it, so a pure client would not find them at all. |
| `net_enabled 0` | WP4 is strictly offline. |
| `r_allowResize 1` | Adds `SDL_WINDOW_RESIZABLE`, without which a canvas resize never reaches the engine (`code/sdl/sdl_glimp.c`). |
| `r_mode -1` plus `r_customwidth`/`r_customheight` | Runtime-derived from the live canvas box and therefore deliberately **absent** from the committed profile; a committed value would be an environment-specific one. The validator rejects all three as committed cvars. |

### The Module configuration

`arena/loader.js` calls the ES-module factory the WP1 build emits with:

- `canvas` — the page's `<canvas id="canvas">`. The id is not decorative: SDL2's
  Emscripten video driver hard-codes the selector `"#canvas"`
  (`SDL_emscriptenvideo.c`, `Emscripten_CreateWindow`), and everything from
  window creation to pointer lock and fullscreen addresses that string.
- `arguments` — a **copy** of the list above. Emscripten's `callMain` unshifts
  the program name onto the array it is handed, so passing the recorded array
  would edit the evidence.
- `instantiateWasm` — the WebAssembly is instantiated from the bytes the loader
  already fetched and verified. The emitted `createWasm` checks
  `Module['instantiateWasm']` before it ever resolves a `.wasm` URL, so no
  second fetch happens and the verified identity is the identity of what runs.
- `preRun` — writes the three QVMs, the content pack and `default.cfg` into the
  Emscripten filesystem with `FS.mkdirTree` and `FS.writeFile`. Every byte is
  already in memory and verified, so the callback is synchronous and needs no
  run dependency.
- `print` / `printErr` — capture the engine's own console into the page instead
  of the browser console, which keeps the two consoles the acceptance evidence
  distinguishes actually distinct.
- `locateFile` — records any request and returns the path unchanged. Nothing
  should call it; a call is a defect the evidence has to be able to see.
- `onRuntimeInitialized`, `onAbort`, `onExit` — timing and failure records.

The engine module itself is imported from a `blob:` URL built from the verified
bytes, so the executed script is the verified script rather than a second
same-origin fetch of the same path. The emitted module tolerates this: its only
uses of `import.meta.url` are the Node branch and a `try`/`catch`-guarded
`scriptDirectory`, and `locateFile` is supplied.

### The filesystem the engine sees

```text
/arena/arena-web-ffa.pk3      the audited WP3 content pack
/arena/default.cfg            the product's own engine configuration
/arena/vm/cgame.qvm           the pinned baseq3 QVMs from the WP1 build
/arena/vm/qagame.qvm
/arena/vm/ui.qvm
```

The engine's base path under Emscripten is the process working directory, `/`,
so `com_basegame arena` resolves to `/arena`. The QVM paths in the WP1 manifest
still read `baseq3/vm/*.qvm` — that is the artifact's identity, not its
destination.

## Packaging discipline

A local serve may hand the browser exactly twelve files and nothing else:

```text
index.html                                       product loader page
loader.js                                        product loader
default.cfg                                      product engine configuration
game-profile.json                                the committed content configuration
manifests/browser-client.json                    the committed WP1 manifest, verbatim
provenance/arena-web-ffa-content-manifest.json   the committed WP3 manifest, verbatim
engine/ioquake3.js                               WP1 artifact
engine/ioquake3.wasm                             WP1 artifact
engine/baseq3/vm/cgame.qvm                       WP1 artifact
engine/baseq3/vm/qagame.qvm                      WP1 artifact
engine/baseq3/vm/ui.qvm                          WP1 artifact
content/baseq3/arena-web-ffa.pk3                 WP3 artifact
```

`scripts/stage-arena.py` assembles that tree from the gitignored build outputs
and nothing is committed. The verification is doubled:

1. **On the host, before serving.** Every artifact is copied out of a build
   directory only after its SHA-256 **and** byte length equal the committed
   manifest entry, and the staged tree is then re-read and compared to the
   expected file set. An extra file, a missing file, a symlink, a modified
   artifact or a modified loader file each fail. The profile itself is refused
   if it names `ioquake3.html`, `ioquake3-config.json` or anything under
   `missionpack/` — WP1's build evidence and off-profile output — or if an
   engine artifact ends in `.pk3` or a content artifact in `.qvm`.
2. **In the browser, before booting.** The loader fetches the two committed
   manifests and refuses to continue unless every artifact it fetched has the
   declared digest and size. Because the module script and the WebAssembly are
   then executed from exactly those bytes, the check is load-bearing rather
   than decorative.

The three product files carry no manifest identity of their own — they are
repository source, staged and byte-compared against the checkout. The loader
records the digest of `default.cfg` anyway, and the pre-acceptance run compares
it with the repository file, so the configuration the engine executed is
identified in the evidence too.

The profile is additionally bound to the audited pack: the map, the player
presentation, the frag limit, every bot name and the pack path must agree with
`content/pack-recipe.json`, and the game directory must not be `baseq3`. All of
these are enforced, with negative tests.

## Cross-origin isolation: re-checked, still not required

WP1 found the client links non-threaded and asked the WP that ships a loader to
re-check that against the artifact it actually serves. Done, on the exact
artifacts in `manifests/browser-client.json`:

- `ioquake3.js` (`sha256:43c37ad7…`) contains zero occurrences of
  `SharedArrayBuffer`, `Atomics`, `pthread`, `PThread` and
  `__emscripten_thread`. Its three `wasmMemory` references are to the module's
  own memory, which it does not create or share.
- `ioquake3.wasm` (`sha256:14d6e241…`) **defines** its memory rather than
  importing one, with limits flags `0x01` — the shared bit is clear — and
  min = max = 4,096 pages, the 256 MiB `-sTOTAL_MEMORY` the Emscripten platform
  file asks for. None of its 423 imports names a thread or atomic primitive.

This also corrects WP1, which reported "its single `wasmMemory` reference": the
shipped `ioquake3.js` has three, and none of them creates or shares a memory.
WP4's count is the one taken from the artifact the loader serves.

**The served page therefore sets no COOP or COEP header, and the slice runs
without cross-origin isolation.** Two complete runs of the whole profile —
engine boot, map load, bot play, audio, input — passed in the pinned browser
under a static serve that sets no headers at all. Precisely: those runs were
served by the acceptance driver's own in-process `StaticServe`, not by
`scripts/serve-arena.sh`; both are header-free `http.server`-based static
serves over the same staged tree, and neither sets COOP, COEP or anything else.
That is the strongest form this check can take: not an inspection claim, but a
client that demonstrably runs without the headers.

The property still belongs to this link configuration and not to the project:
enabling threads later would reinstate the requirement.

## Load size and load timing

The served set is 26.6 MiB of artifacts, and the content pack is most of it:

| Artifact | Bytes |
| --- | --- |
| `content/baseq3/arena-web-ffa.pk3` | 24,181,175 |
| `engine/ioquake3.wasm` | 2,339,266 |
| `engine/ioquake3.js` | 266,707 |
| `engine/baseq3/vm/qagame.qvm` | 488,108 |
| `engine/baseq3/vm/cgame.qvm` | 343,304 |
| `engine/baseq3/vm/ui.qvm` | 307,164 |
| **total** | **27,925,724 (26.6 MiB)** |

Plus the three product files and the two committed manifests, together under
25 KiB.

Measured in the pinned browser over loopback, from the page's own timeline
(milliseconds after the loader module started; both accepted runs):

| Milestone | Run 1 | Run 2 |
| --- | --- | --- |
| all 27.9 MB fetched **and** SHA-256-verified | 58.4 | 60.8 |
| user gesture (the driver's trusted click) | 264.1 | 255.8 |
| runtime initialized | 302.3 | 293.6 |
| server spawned the map (`Server: oa_pvomit`) | 1005.2 | 1002.1 |
| client game module up (`CL_InitCGame:`) | 1675.3 | 1656.0 |
| the **local player** entered the game | 1680.9 | 1661.2 |
| bot Skelebot entered the game | 3213.3 | 3205.0 |
| bot Rai entered the game | 4713.5 | 4705.0 |
| bot Sly entered the game | 6213.3 | 6204.8 |

The last four rows are the correction the review of this work package forced.
"Entered the game" is printed for every client, so the row that used to read
"first bot" was the local player; the three bot rows are now matched by name
and land 2.2, 3.7 and 5.2 seconds after the server spawned, which is exactly
the `+addbot` cadence the command line asks for.

Read this honestly: **this is a loopback measurement, not a delivery
measurement.** 26.6 MiB arrives in under 60 ms from a local static server
because there is no network in the path; the same bytes over a real connection
are the load time a player would see, and nothing here measures that. What the
numbers do establish is that hashing 26.6 MiB with `crypto.subtle` costs a
fraction of the boot, and that the engine reaches a playable map about 1.7 s
after the user gesture.

## Frame timing

The loader runs its own `requestAnimationFrame` loop and records every
inter-frame delta. That is the same animation-frame pipeline that drives the
engine: `code/sys/sys_main.c` hands `Com_Frame` to
`emscripten_set_main_loop(..., 0, 1)`, and fps 0 means the browser's animation
frame. It is a measurement of frame cadence, not of engine work per frame.

Both accepted runs, 60 s of driven play each:

| | Run 1 | Run 2 |
| --- | --- | --- |
| samples | 4,115 | 4,110 |
| mean | 16.99 ms (58.9 fps) | 16.99 ms (58.9 fps) |
| median | 16.7 ms | 16.7 ms |
| 95th percentile | 16.8 ms | 16.8 ms |
| longest frame | 733.3 ms | 750.0 ms |
| frames over 50 ms | 2 | 3 |

The two or three long frames are the synchronous map load and renderer
initialization, which happen inside one animation frame. `com_maxfps` defaults
to 0 under Emscripten and `r_swapInterval` to 1
(`code/qcommon/common.c`, `code/renderergl2/tr_init.c`), so the display's
refresh rate is the ceiling and 58.8 fps against a 60 Hz frame clock is the
pipeline keeping up.

## Automated pre-acceptance

`scripts/run-arena-acceptance.sh` drives the exact WP0 acceptance browser —
Chrome for Testing `152.0.7977.64`, refused if the binary reports anything else
— against a clean local serve of the staged tree, twice, each launch with its
own throwaway profile directory. It speaks the DevTools protocol over a
standard-library WebSocket client; this repository takes no third-party
dependency for it.

Both runs passed every check. Evidence lands in the gitignored
`build/arena-acceptance/`: `summary.json`, and per run `result.json`, the
engine's complete console output and three full-size screenshots.

| Check | What it asserts | Result |
| --- | --- | --- |
| `loader-ready` | the loader verified everything and offered to start | pass |
| `runtime-identities-match-committed-manifests` | all six artifacts matched their committed digest and size | pass |
| `engine-configuration-is-the-repository-file` | the `default.cfg` the engine executed is the repository file | pass |
| `engine-started` | the WebAssembly runtime initialized | pass |
| `map-entered` | `Server: oa_pvomit` and `CL_InitCGame:` both appeared | pass |
| `bots-entered-game` | every configured bot joined **by name**, and the page's live derivation and the driver's recomputation from the saved log agree | pass |
| `engine-kept-running` | the page's final status is `running`, its error is null and no engine exit was recorded | pass |
| `engine-arguments-are-the-committed-profile` | the arguments the engine received are the committed list plus exactly the render-size suffix derived from the reported canvas box | pass |
| `engine-console-no-missing-asset` | no unaccepted missing image or sound | pass |
| `engine-console-no-qvm-rejection` | no QVM header, magic or size rejection | pass |
| `engine-console-no-renderer-fatal` | no `GL_CheckErrors` or GL init failure | pass |
| `engine-console-no-engine-error` | no `Com_Error` | pass |
| `browser-console-no-error` | no error-level browser console entry, including network failures | pass |
| `no-uncaught-exception` | no `Runtime.exceptionThrown`, no page error, no unhandled rejection | pass |
| `only-declared-local-artifacts` | every request the page made is a `blob:`/`data:` URL or begins with the serve's own origin **and** resolves, by URL parsing, to a staged path | pass |
| `serve-answered-only-staged-files` | the server's own access log holds only staged paths, all 200 | pass |
| `no-unexpected-engine-file-request` | the engine never asked `locateFile` for anything | pass |
| `frames-advanced` | the animation frame loop kept running | pass |
| `audio-user-activated` | an `AudioContext` created at boot is `running` and `navigator.userActivation` is set | pass |
| `canvas-rendered-a-scene` | every screenshot decodes to thousands of distinct colours | pass |
| `second-launch-same-artifact-identities` | run 2 loaded byte-identical artifacts | pass |
| `second-launch-same-engine-arguments` | run 2 ran the same command line | pass |
| `second-launch-reached-the-same-profile` | run 2 entered the same map from the same package | pass |

The rendering check is deliberately not a claim about what is on the screen. It
decodes each PNG with a small standard-library reader and counts distinct
colours: 5,137 to 10,668 across the six screenshots of the two runs, against
the single colour a blank canvas would produce. A human still has to look at
them.

### Why the bot gate is name-anchored

`g_client.c:1026` prints `"<netname>^7 entered the game"` for **every** client
that begins, and the local player begins first. A check on that sentence alone
would pass on a session with no bots at all — an earlier draft of this work
package had exactly that hole, and its "first bot" timing row sat five
milliseconds after `CL_InitCGame:`, which is impossible against a 2,000 ms
`addbot` delay.

The gate is therefore per bot and derived twice. The loader watches the print
stream live, strips Quake III colour codes and records a timestamped entry only
for an exact `"<bot name> entered the game"`; the driver recomputes the same set
in Python from the saved console log; and the check requires every configured
bot to be present **and** the two derivations to agree. The timings below show
the effect: the bots now appear 2.2, 3.7 and 5.2 seconds after the server
spawned, which is `BOT_BEGIN_DELAY_BASE` plus `BOT_BEGIN_DELAY_INCREMENT` per
bot, exactly as the command line asks.

Input is dispatched as trusted DevTools events — the start click, `w`/`a`/`s`/`d`,
mouse movement and repeated fire — for 60 seconds per run. That establishes that
real key and mouse events reach a running client without breaking it. It does
**not** establish that the player moved, aimed or hit anything; that is the
witnessed round's job.

Focus loss and recovery are exercised by dispatching `blur` and `focus` on the
window, which is what SDL's Emscripten backend listens for. That is a dispatched
event, not a window manager moving focus between real windows, and it is
recorded as such.

### The game actually plays itself

The strongest thing the automated round produces is the engine's own console
during those 60 seconds. From run 1:

```text
UnnamedPlayer^7 entered the game
Skelebot^7 entered the game
Rai^7 entered the game
Sly^7 entered the game
Rai^7 was gunned down by Skelebot^7
UnnamedPlayer^7 almost dodged Rai^7's rocket
UnnamedPlayer^7 almost dodged Skelebot^7's rocket
Sly^7: ^2It is just easier for unnamedplayer this way.
Sly^7 ate Skelebot^7's rocket
Sly^7 blew himself up.
```

Three bots loaded their characters, navigated the map's `.aas`, fired weapons,
killed each other, scored and used the chat the bot files carry — and the two
`almost dodged` lines are the local player being killed by bot rocket splash,
so damage flows to the human client too, even though nobody was steering it.
**The audited free content supports the pinned `baseq3` QVMs at runtime.** WP3's
failure boundary — stop and return to plan review if it does not — is not
reached, no gamecode was switched and nothing was adopted from OpenArena's
engine.

That still is not the witnessed round: nothing here shows a *player* moving,
aiming or scoring, only that the simulation runs and reaches the human client.

### The host-dependent part of the run

The pre-acceptance run needs a working WebGL implementation, and on the machine
that produced this evidence the choice mattered:

| Headless ANGLE backend | Result |
| --- | --- |
| `gl` (the host's own driver) — the default | Both runs completed. |
| `swiftshader` | Chrome's GPU process died with SIGSEGV part-way through renderer initialization; the WebGL context was lost and the engine stopped with `Couldn't compile shader` or `shaders failed to link`, at a different point each time. |
| `vulkan` | The same GPU-process crash. |

That is an observation about this host's Chrome/ANGLE stack, not a finding
about the client: the same client, same serve and same profile complete under
the `gl` backend, and the failure mode is a browser-side process crash reported
in Chrome's own log, not an engine or content error. It is recorded because a
reviewer repeating the run elsewhere may need `--angle`.

A headed run was also tried. The engine initializes fine, but an unfocused
window on this desktop receives almost no animation frames — three frames in two
minutes — so the map never loads. The witnessed acceptance is a person in front
of a visible, focused window, which is a different situation; the automated
round stays headless on purpose.

## What is tested, and what deliberately is not

`tests/test_arena_runtime.py` covers the packaging discipline, the profile's
fail-closed rules, the engine-log classification, the browser transport and the
run scoring — including negative cases for every check the review of this work
package asked for. The committed profile itself is validated against the real
committed manifests and the real content recipe.

**`arena/loader.js` has no JavaScript unit test, on purpose.** Every one of its
paths is DOM- or Emscripten-bound — canvas geometry, pointer lock, fullscreen,
`crypto.subtle`, `WebAssembly.instantiate`, blob module import, Emscripten's
`FS` and `preRun` — so a Node harness would need a stub for each, and a test
against a stub would mostly assert that the stub behaves like the stub. WP2's
`tests/js_conformance_harness.mjs` pattern works there because the probe's
framing logic is pure; nothing in this loader is.

What covers it instead is stronger than a stub test would be:

- the pre-acceptance run exercises the whole loader end to end in the exact
  acceptance browser, twice, and fails on any uncaught exception or console
  error;
- the load-bearing derivations exist twice, in different languages, and the run
  requires them to agree — the engine arguments (loader against
  `arena_runtime.expected_engine_arguments`), the artifact identities (loader
  against the manifests read from this checkout) and the bots that joined
  (loader against the driver's recomputation from the console log).

A defect in one of those derivations shows up as a disagreement rather than as
two implementations being wrong in the same way.

## Findings recorded rather than fixed

These are the runtime contracts the WP3 static closure could not see. Each one
is real, none of them blocks the slice, and none of them was patched into the
audited content pack — `content/`, `provenance/` and the WP3 scripts are
untouched.

### 1. The engine requires its own `default.cfg`, and the pack has none

`FS_InitFilesystem` stops with a fatal `Couldn't load default.cfg`
(`code/qcommon/files.c`). That is an engine requirement, not a game-module
reference, so the pack — which is the closure of what the pinned QVM sources
name — does not contain one and could not have been expected to.

WP4 supplies `arena/default.cfg`: original arena-web configuration for an
arena-web-named game directory, using only ioquake3's own command and key names
(`code/client/cl_input.c`, `code/cgame/cg_consolecmds.c`,
`code/client/cl_keys.c`). It is not a copy of anyone else's `default.cfg` and it
is not an addition to the audited pack. `scripts/arena_runtime.py` requires the
profile to place a `default.cfg` in the active game directory, so this cannot
silently regress.

### 2. Three images the *engine and client* register are missing from the pack

The renderer registers `flareShader` and `sun` itself, in
`CreateExternalShaders` (`code/renderergl2/tr_shader.c:3972`, called from
`R_InitShaders` at `:4026`), and the client registers
`console` (`code/client/cl_main.c`, `CL_InitRenderer`). Those shader scripts are
in the pack; the images their stages name are not:

| Reference | Present in the audited upstream sources |
| --- | --- |
| `gfx/fx/flares/blur.tga` (shader `flareShader`) | yes — `openarena-088-data`, `pak6-patch088/gfx/fx/flares/blur.tga` |
| `textures/flares/flarey.tga` (shader `sun`) | yes — `openarena-textures`, `pak4-textures/textures/flares/flarey.tga` |
| `textures/sfx/logo256.tga` (shader `console`) | yes — `openarena-textures`, `pak4-textures/textures/sfx/logo256.tga` |

**This is a gap in WP3's closure, not in the free content.** The closure reads
what the `baseq3` QVM sources reference; these three are registered by the
engine and the client, which that reading does not cover. WP3's own bounded
claim — "every reference *those two readings* extract" — is intact, and this is
exactly the kind of reference it excludes.

The consequence for this profile is cosmetic: `r_flares` defaults to 0
(`code/renderergl2/tr_init.c`), so no flare is drawn at all, and the third is
the drop-down console's backdrop. The renderer answers all three with its
default shader.

The fix belongs to the content recipe — add the engine's and client's own
registrations as closure roots — and is left to the owner of `content/`.

### 3. The selected player model has no taunt, upstream

`sound/player/skelebot/taunt.wav` is one of the thirteen names in
`cg_customSoundNames` (`code/cgame/cg_players.c`). OpenArena ships twelve of the
thirteen for skelebot and no taunt at all; the packaged voice set is complete
with respect to what exists. The fallback then tries `sound/player/sarge/…`,
which is retail Quake III data this project does not have. The engine registers
both as optional, logs a warning and continues; the taunt gesture is silent.

### 4. `music/sonic5.wav`

Already recorded by WP3: `oa_pvomit`'s worldspawn names a music track no
OpenArena release ships. It appears once per run as a sound warning.

### The acceptance list, in full

Findings 2 to 4 are **six** missing references, and the driver's acceptance list
has one entry each:

| Reference | Finding |
| --- | --- |
| `gfx/fx/flares/blur.tga` | 2 |
| `textures/flares/flarey.tga` (and `Shader sun has a stage with no image`) | 2 |
| `textures/sfx/logo256.tga` | 2 |
| `sound/player/skelebot/taunt.wav` | 3 |
| `sound/player/sarge/taunt.wav` (and its `Using default sound for …` line) | 3 |
| `music/sonic5.wav` | 4 |

Finding 1 is **not** in that list and is not a missing reference: `default.cfg`
is an engine requirement that a product file satisfies, so the engine never
reports it missing at all.

Each of the six is reproduced with its reason in the run's `summary.json`.
Anything else the engine reports missing fails the run — the list contains no
wildcard, on purpose.

## Reproducing the result

From a clean checkout, with the WP1 build and the WP3 pack already produced
(see [`wp1-build-evidence.md`](wp1-build-evidence.md) and
[`wp3-content-closure.md`](wp3-content-closure.md)):

```bash
scripts/check.sh                                   # 634 tests, no network
scripts/serve-arena.sh                             # then open http://127.0.0.1:8174/
```

For the automated round, unpack the exact Chrome for Testing archive
[`immutable-baseline.md`](immutable-baseline.md) pins and point `ARENA_CHROME`
at its `chrome` binary:

```bash
ARENA_CHROME=/path/to/chrome-linux64/chrome scripts/run-arena-acceptance.sh
```

It stages, verifies, serves, runs twice and writes
`build/arena-acceptance/summary.json`. `--angle gl|vulkan|swiftshader` selects
the headless WebGL backend, `--headed` opens a real window, `--runs` and
`--play-seconds` size the round. The evidence above was produced with the
defaults and `--play-seconds 60`.

The accepted engine build used here reproduced WP1's result exactly: a third
clean build, run from this work package's baseline commit, emitted the same ten
artifact digests as `manifests/browser-client.json`, differing only in the
manifest's `producer.commit`.

## The witnessed round found a renderer defect

The first witnessed attempt (2026-08-30) was aborted on sight: parts of the
world rendered as solid white — whole wall sections, floor bands, a beam at a
jump pad — and surfaces near the player vanished in a sphere-shaped region
around the view. The defect was then found in the stored screenshots of every
earlier automated pre-acceptance run as well; the harness had checked console
output and colour diversity but never pixels, so nobody had looked.

What the diagnosis established, each point against the pinned tree or by
repeated measurement in the pinned browser:

- **Not the content.** The engine log contains zero missing-image warnings
  beyond the three known engine-registered cosmetics, and the same pack, QVMs
  and profile render flawlessly in the WP5 native client (renderergl2 over
  Mesa; screenshot taken via the engine's own `screenshot` command).
- **Intermittent per session, stable within one.** Across all instrumented
  browser sessions the defect appeared in roughly two of three runs; a healthy
  in-game frame measures well under 1% near-white pixels, a defective one 15
  to 52%.
- **Ruled out by repeated A/B runs** (defect persists in every case):
  `r_hdr 0`, `r_postProcess 0` + `r_toneMap 0`, `r_ext_framebuffer_object 0`,
  `r_mergeLightmaps 0` (looked clean once, then reproduced — the defect's
  intermittency produces false fixes at one run per configuration), a WebGL 1
  context (`--disable-webgl2`, 3/3 defective) — which under this engine also
  runs without vertex array objects, since the engine probes only for the ARB
  extension name and WebGL exposes the OES name.
- **`r_ignoreGLErrors 0` reports nothing.** The defective path completes
  without a single GL error; the atlas of symptoms is semantic, not an
  illegal call.
- **Only bypassing the lightmapped world-surface path avoids it:**
  `r_vertexLight 1` was clean in seven of seven sessions (probability of that
  under the observed base rate, were it ineffective: about 0.05%).
- **No upstream fix exists.** The pinned ioq3 commit is upstream `main`'s HEAD;
  the historical VAO-cache fixes (upstream #678, #723) are already in the pin
  with the cache defaulted off.

**The mitigation** — since removed; the root cause was found and fixed later the
same day, see "The defect class, resolved" below — was `r_vertexLight 1` in the
committed profile: the browser
slice renders with per-vertex lighting instead of lightmaps. That is a visual
downgrade confined to the browser — the native client keeps lightmaps and its
defaults — and it is marked in the profile as a workaround to remove, not a
preference. The operator's native-vs-browser comparison (reference images in
[`evidence/wp5-witnessed-round-2026-08-30/`](evidence/wp5-witnessed-round-2026-08-30/))
puts the cost concretely: beyond the flatter lighting, the map's coloured
light strips visible throughout the native render are absent in the browser.
Restoring parity with those reference images is the measurable target for the
root-cause fix. The automated pre-acceptance now measures near-white fractions on
the in-game screenshots and fails above 5% (`canvas-no-white-surface-regression`),
so the defect class cannot return silently and the workaround's effectiveness
is re-measured on every run.

**The witnessed round then found two further symptoms of the same defect
class** (2026-08-30, after the workaround landed):

- **Distance-graded entity shading.** Items and player models render with
  black modulate stages and white/oversaturated additive stages once they are
  a few hundred units away, and correctly up close; the world and the
  first-person weapon are correct at every distance. This is deterministically
  reproducible: a camera at `setviewpos 96 1736 -370 270` shows the four-box
  ammo cluster (~430 units) with black bodies and neon icons only, while
  `setviewpos 96 1500 -390 270` (~170 units) shows them fully textured, in
  the same session. Ruled out against the pinned data and by A/B runs:
  broken mip levels (`r_textureMode GL_LINEAR` changes nothing), LOD models
  with unresolved shaders (the pack contains none), fog (the BSP has zero fog
  volumes), a missing light grid (present, no warnings), and the
  `r_vertexLight` workaround itself (the defect is visible in the
  pre-workaround session's screenshots). No configuration lever was found.
- **Frame flicker.** The operator reports visible geometry flicker that
  screenshots do not capture — consistent with unstable frames rather than
  stable wrong pixels. Not yet instrumented.

**Decisions of 2026-08-30, superseding the morning's deferral:** the witnessed
acceptance proceeds *with these defects recorded* — gameplay, input, audio and
identity evidence are unaffected, and the round's result records the known
browser-renderer defect class instead of being blocked by it. The root-cause
hunt was pulled forward: a timeboxed instrumented-build investigation of
renderergl2 under Emscripten's WebGL layer, using the deterministic
reproductions above, on scratch copies only — the `ioq3/` submodule stays
byte-for-byte upstream while it runs. If it ends in a small engine patch, the
agreed model is a public fork carrying enumerated, upstream-mergeable patches
on top of the pinned upstream commit — the same model the operator's Luanti
fork uses — which also means amending WP1's "unmodified ioq3" contract
deliberately at that point.

## The defect class, resolved — 2026-08-30, after closure

The hunt above ran and ended. This section is the outcome for all three
symptoms; it is recorded after WP4's closure and changes none of the closure
evidence, which stands as written.

### Symptom 1, white lightmapped surfaces — **root-caused and fixed**

It was an engine defect, and a precision one. `GLSL_GetShaderHeader`
(`code/renderergl2/tr_glsl.c`) emitted `precision mediump float;` for every
GLSL ES shader stage. `mediump` only has to cover ±2^15 with 10 bits of
mantissa, and `lightall_fp.glsl` works in world units per fragment: on a map
whose coordinates run into the thousands, `dot(viewDir, viewDir)` overflows,
`normalize()` returns the zero vector, and `CalcSpecular()`'s `1e-8` epsilon
underflows to zero as well, so the specular term is unbounded and the surface
is drawn saturated white. Desktop GL ignores precision qualifiers, which is
exactly why the native client was clean and only the browser was not — the
"native is clean" observation above was evidence *for* this cause, not against
an engine cause.

A WebGL probe on the same driver confirms the mechanism without inference: at
`mediump`, `normalize(vec3(1200.0))` returns the zero vector and `1.0e-8 > 0.0`
is false; at `highp` both behave.

The fix is one enumerated patch on the fork's `web` branch,
`renderergl2-glsl-es-highp`, touching that one file: `highp` unconditionally in
the vertex stage and `GL_FRAGMENT_PRECISION_HIGH`-guarded `highp` in the
fragment stage. The engine pin, the amended WP1 contract, the rebuilt client and
the full validation numbers are in
[`wp1-build-evidence.md`](wp1-build-evidence.md#amendment-of-2026-08-30-the-pin-carries-a-patch-series).

Measured: the near-white fraction of an in-game frame fell from about 33% to
about 1.3% in the investigation, 0 of 14 instrumented runs were defective
against a base rate of roughly two in three, all 115 GLSL programs still
compile, and WebGL 1 and WebGL 2 are both fixed.

**The `r_vertexLight 1` workaround is removed** from `arena/game-profile.json`,
together with its `cvarNote`, and `engineArguments` is regenerated from the
declarative fields. The browser slice renders with lightmaps again. Three
post-fix acceptance rounds — six sessions, eighteen in-game screenshots — passed
every check with near-white fractions between 0.0022 and 0.0252. The
`canvas-no-white-surface-regression` gate keeps its 5% threshold unchanged and
now guards the fix instead of the workaround.

### Symptom 2, distance-graded entity shading — **reclassified, not a browser defect**

This is not a defect of the browser build. The native client renders these
items identically at the same distances: the black modulate stages and
white/oversaturated additive stages are renderergl2's normal look for these
OpenArena item shaders, and the comparison that made it look browser-specific
was against expectation rather than against the native client at the same
camera position. Nothing to fix, and nothing carried forward.

### Symptom 3, frame flicker — **resolved, confirmed by the operator**

The patched build is frame-bitstable in the headless harness, so the
instrumentation available here could not see the symptom either way, and it was
carried forward as a re-check for the operator on the real desktop. That
re-check happened on 2026-08-30: playing the patched client on the real display
and compositor, the operator reports the world renders stably and the flicker
is gone.

### The operator's confirmation of the fix — 2026-08-30

The operator played the patched build on the real desktop and confirmed all
three symptoms resolved: the map's coloured light strips — the parity target
taken from the native reference images — now render correctly, item, weapon
and character-model textures are correct at distance, and the world renders
stably with no flicker. In the operator's words, every reported bug is
correctly fixed, with no new regression observed.

### Two pre-existing content gaps, reported 2026-08-30 — **open**

While confirming the fix, the operator reported two visual gaps that predate
the renderer work (explicitly not regressions):

1. **The machine gun's spinning barrel is missing.** The content pack carries
   `models/weapons2/machinegun/machinegun.md3` but no
   `machinegun_barrel.md3`. The engine constructs that file name at runtime by
   string surgery (`code/cgame/cg_weapons.c:663` appends `_barrel.md3`), which
   is exactly the class of reference WP3's *static* reading of the QVM sources
   cannot extract, and a missing barrel model registers as `0` and is silently
   not drawn — the spin logic itself is present (`cg_weapons.c:1137`).
2. **The lightning gun's beam is invisible.** The pack carries the weapon
   model, skin and muzzle flashes but no beam shader or texture. The renderer's
   beam path exists and works (`code/renderergl2/tr_surface.c:742`,
   `RT_LIGHTNING`), and gameplay is unaffected — damage and aim feel correct —
   so the gap is the missing art, not the engine.

Both are content-closure gaps, not renderer defects. The follow-up is a small
deliberate content increment: check whether the pinned OpenArena archives
provide these assets at all, extend the recipe with explicit
reference bookkeeping if they do (or record a stated acceptance if they do
not), and rebuild the pack reproducibly with reissued records.

### An operational fact found on the way

**ioquake3 silently drops startup `+` commands beyond 32 lines.**
`Com_ParseCommandLine` (`code/qcommon/common.c:413`) stores at most
`MAX_CONSOLE_LINES` = 32 console lines and simply `return`s when it reaches
that number — no warning, no error. Line 0 is the segment before the first
`+`, so at most 31 `+` commands survive.

That is a hard constraint on generated `engineArguments`, which grow with every
cvar and every bot in the profile. The committed profile currently emits 15,
plus the three the loader derives from the live canvas, so there is room; but a
profile that quietly crossed the bound would lose its trailing `+addbot` lines
with nothing in any log to say so. Anyone adding cvars or bots should count.

## Operator acceptance checklist — **completed 2026-08-30**

The witnessed round happened on 2026-08-30 and is recorded in
[`wp4-witnessed-round-2026-08-30.md`](wp4-witnessed-round-2026-08-30.md):
fourteen of the sixteen checks passed literally, the browser/desktop check was
an operator-chosen variation, and the fullscreen/resize check was only
partial. The owner decisions that close the round follow the checklist below,
which is kept as written. It declared a person at the WP0 acceptance desktop —
Fedora Linux Workstation 44 on x86_64, GNOME — with the pinned Chrome for
Testing `152.0.7977.64` in a visible, focused window, serving the slice with
`scripts/serve-arena.sh`.

Before starting, confirm the run is the declared one:

- [ ] `scripts/check.sh` is green and the worktree is clean.
- [ ] `scripts/serve-arena.sh` printed no refusal; the staged tree is the twelve
      files listed above.
- [ ] `chrome --version` prints `Google Chrome for Testing 152.0.7977.64`.
- [ ] The loader's start screen reports six artifacts verified against the
      committed manifests.

Then, with the page open at `http://127.0.0.1:8174/`:

- [ ] **Enters the map.** Press Start; the arena appears with bots.
- [ ] **Move.** Forward, back and both strafes move the player.
- [ ] **Look.** Clicking the arena captures the mouse; moving it turns the view;
      Esc releases the capture and clicking recaptures it.
- [ ] **Fire.** The weapon fires, is audible, and its impacts appear.
- [ ] **Take damage.** A bot hits the player; health drops and the pain cue plays.
- [ ] **Deal damage and score.** The player kills a bot; the obituary appears and
      the scoreboard (Tab) shows the frag.
- [ ] **Complete or restart a session.** Play to the frag limit of 15, or restart
      through the menu, and the session ends and restarts cleanly.
- [ ] **Focus loss and recovery.** Switch to another window and back: the client
      keeps running, audio and input recover, and nothing is stuck.
- [ ] **Fullscreen.** The Fullscreen control enters and leaves fullscreen and the
      view fills the screen at the new size.
- [ ] **Audio.** Weapon, impact, pain and announcer sounds are audible after the
      start gesture, with no separate unmute step.
- [ ] **Consoles.** The browser DevTools console holds no error, and the in-game
      console (Shift+Escape; the console key is hard-coded, `cl_keys.c:1261`)
      holds no missing-asset, QVM or renderer error beyond the six accepted
      references recorded above.
- [ ] **Second clean launch.** Reload with a fresh browser profile and reach the
      same map with the same six artifact digests.

Record the result — the desktop, the browser version, the observations and any
defect — and only then is WP4's acceptance evidence complete.

### Closure decisions — 2026-08-30

The witnessed report deliberately left two policy decisions to the WP4 owner;
both are decided here.

**The Brave/KDE environment variation is accepted for this round.** The
operator deliberately chose the locally installed Brave 1.94.117 Flatpak on
KDE/Wayland instead of the declared pinned Chrome for Testing on GNOME. The
pinned Chrome for Testing `152.0.7977.64` is exercised on every gate run by
the automated acceptance harness — same serve path, same artifact-verifying
loader — so the pinned platform does not go unwitnessed; the human round adds
what the harness cannot: a real GPU and compositor path, real input, audible
audio and a second Chromium-based browser. A future witnessed round should
either use the pinned browser or record its variation the same way this one
did.

**The runtime-resize finding is recorded as an accepted known limitation, not
a blocker.** After engine start, resizing the browser window does not update
the engine resolution, and entering fullscreen after start retains the
startup dimensions (entering fullscreen *before* start derives the correct
size). The checklist's "fills the screen at the new size" wording is
therefore not met. This is a loader/engine canvas-size propagation gap,
distinct from the three renderer symptoms under root-cause investigation, and
it is carried forward as its own follow-up item rather than silently widening
that investigation.

With those two decisions, the recorded renderer defect class and the passing
gameplay, input, focus, audio, console and clean-relaunch evidence, WP4's
acceptance evidence is complete and WP4 is closed.

## What this does not prove

- Nothing about multiplayer. `net_enabled` is 0 and there is no browser network
  backend; that is WP7.
- Nothing about delivery. The load timing above is loopback timing, and
  production hosting, caching and compression are explicit non-goals.
- Nothing about other browsers, other operating systems, touch input, persistent
  settings, OPFS or accounts.
- Nothing about content the running game could ask for and did not during two
  60-second sessions. The static closure's bound and this dynamic observation
  are both partial, in different ways.
