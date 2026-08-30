<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP4 witnessed round — 2026-08-30

**Result:** the 16-point manual round was completed; 14 checks passed, one was
an operator-accepted browser/platform variation, and the fullscreen check was
only partial because runtime resizing does not update the game resolution

This report records the manual round requested by the **Operator acceptance
checklist — PENDING** section of
[`wp4-vertical-slice.md`](wp4-vertical-slice.md). It covers the offline browser
slice only. No multiplayer, WebTransport, relay or delivery behavior was
exercised.

The operator deliberately chose the locally installed Brave browser and KDE
desktop for this round. That is sufficient for the operator's local product
acceptance, but it is not the literal WP0 platform named by the pending
checklist. The owner closing WP4 must therefore record or explicitly accept the
variation rather than describe this as a run in the pinned Chrome for Testing
on GNOME.

## Preconditions and staging

- The repository was clean before the round.
- `scripts/check.sh` validated all ten metadata files and passed all 614 tests.
  An initial run inside a restricted network sandbox produced 16
  `PermissionError` results when tests tried to create local WebSocket sockets;
  the same command passed completely when rerun with local loopback sockets
  available. No test or project defect was involved in the first result.
- The accepted WP1 browser build and WP3 content assembly were already present
  under `build/browser/tree/Release/` and `build/content-pack/`.
- `scripts/serve-arena.sh` staged exactly the twelve declared files in
  `build/arena-serve/`, verified 27,925,724 bytes (26.6 MiB), and reported no
  staging refusal.
- The first serve attempt met a transient `EADDRINUSE` on port 8174. The
  listener had disappeared when inspected, and the second attempt started the
  verified tree successfully on `http://127.0.0.1:8174/`. No unrelated process
  was stopped.

The loader reported exactly:

```text
6 artifacts verified against the committed manifests (26.6 MiB).
Press Start to enter oa_pvomit with 3 bots.
```

The server access log showed every staged input successfully served. The first
profile's repeat page load used successful `304` responses; the fresh second
profile fetched the same set with `200` responses.

## Acceptance environment

| Property | Observed value | WP0 checklist value |
| --- | --- | --- |
| Operating system | Fedora Linux 44, `x86_64` | Fedora Linux Workstation 44, `x86_64` |
| Desktop session | KDE on Wayland | GNOME |
| Browser | Brave Flatpak `com.brave.Browser` 1.94.117, Flathub commit `1f89c41e334ba87c16bbe4f8579de607e5f68d4b66c45145fdd1f61e299a4348` | Chrome for Testing 152.0.7977.64 |
| Desktop OpenGL probe | AMD Radeon Graphics, radeonsi, Mesa 26.1.8, OpenGL 4.6 | must be recorded, not pinned |
| Other installed GPU | NVIDIA GeForce RTX 3090, driver 610.57.04 | must be recorded, not pinned |
| Display | DisplayPort, 2560×1440 at 239.97 Hz, scale 1, HDR disabled, VRR disabled | must be recorded, not pinned |

The OpenGL row is the active desktop `glxinfo` result. Brave's internal
`brave://gpu` page was not captured, so this report does not overstate that
probe as independent proof of the browser GPU process's selected adapter.

Both browser launches used new temporary profiles rather than the operator's
normal profile:

```text
/tmp/arena-wp4-brave-1.HVzidI
/tmp/arena-wp4-brave-2.Ui7DR8
```

No extension or pre-existing browser state was therefore part of the round.

## The 16 checklist results

| # | Checklist item | Result | Witnessed evidence |
| ---: | --- | --- | --- |
| 1 | `scripts/check.sh` green and worktree clean | Pass | Ten metadata files validated, 614 tests passed, and `git status --short` was empty. |
| 2 | Serve accepted exactly the staged tree | Pass | `serve-arena.sh` staged twelve files and verified 27,925,724 bytes before serving on loopback. |
| 3 | Declared browser version | **Accepted variation, not a literal pass** | Brave 1.94.117 was used at the operator's explicit request. Chrome for Testing 152.0.7977.64 was neither downloaded nor installed. KDE/Wayland also differs from the declared GNOME desktop. |
| 4 | Six artifacts verified on the start screen | Pass | The operator read and confirmed the six-artifact, 26.6 MiB loader message before starting. |
| 5 | Enters the map | Pass | `oa_pvomit` appeared with Skelebot, Rai and Sly. |
| 6 | Move | Pass | Forward, backward and both strafes moved the player. |
| 7 | Look and pointer lock | Pass | Clicking captured the pointer, mouse movement turned the view, Escape released it, and clicking recaptured it. |
| 8 | Fire | Pass | The weapon fired reliably; weapon audio and visible impacts were present. |
| 9 | Take damage | Pass | Bot damage reduced health, the pain cue played, and play/respawn continued normally. |
| 10 | Deal damage and score | Pass | The operator killed a bot, saw the obituary, and confirmed the frag under the Tab scoreboard. |
| 11 | Complete or restart a session | Pass | The session restarted cleanly through the game UI and returned to a playable map with the bots. |
| 12 | Focus loss and recovery | Pass | Switching to another window and back left the client running; audio and input recovered and no control remained stuck. |
| 13 | Fullscreen and resize | **Partial; checklist requirement not met** | The Fullscreen control entered and left fullscreen and input still worked, but a size change after engine start did not update the game to the new exact dimensions. See the dedicated finding below. |
| 14 | Audio | Pass | Weapon, impact, pain and announcer audio all worked after the Start gesture without a separate unmute action. |
| 15 | Browser and game consoles | Pass | Brave DevTools showed no error. The game console showed only accepted missing references/capability messages and no QVM rejection, renderer-fatal error or unexpected missing asset. |
| 16 | Second clean launch | Pass | A second new Brave profile verified the same six artifacts, entered `oa_pvomit` with the same three bots, and had working movement and mouse look. |

This is 14 literal passes, one explicitly accepted environment variation and
one partial result whose resize half does not satisfy the checklist wording.

## Known renderer findings, observed as expected

The round proceeded under the decision in `wp4-vertical-slice.md` that the
known browser-renderer defect class is recorded rather than treated as a new
blocker:

1. the committed `r_vertexLight 1` workaround produces flatter world lighting
   and omits the native client's coloured light strips;
2. distant items and player models can show black modulate stages and
   white/oversaturated additive stages while rendering correctly up close;
3. geometry/frame flicker can be visible during play.

The operator described the game as working apart from those known issues. The
map-entry check specifically found no return of the pre-mitigation solid-white
world surfaces or the sphere-shaped disappearance of world geometry near the
camera.

### The white in-game console backdrop

The operator noticed a large white shadow/area while the drop-down game console
was open and supplied screenshots during the guided session. The console itself
showed the matching accepted warnings:

```text
WARNING: R_FindImageFile could not find 'textures/sfx/logo256.tga' in shader 'console'
Shader console has a stage with no image
```

`textures/sfx/logo256.tga` is the already documented missing console backdrop.
The renderer falls back to its default shader, producing the white area. The
operator confirmed that it disappeared completely when the console was closed.
It is therefore evidence of the accepted missing console image, not a fourth
gameplay-renderer symptom.

The other visible game-console warnings were within the accepted list,
including `gfx/fx/flares/blur.tga`, `textures/flares/flarey.tga` and
`music/sonic5.wav`. QVM messages that the modules had no bytecode compiler and
would use the interpreter were normal load messages, not QVM rejection.

Brave DevTools itself requested
`/.well-known/appspecific/com.chrome.devtools.json` when opened. The minimal
static server correctly returned 404 because that browser-tooling path is not
one of the twelve staged files. It produced no DevTools console error and was
not a request made by the arena loader or engine.

## New finding: runtime resize does not update the exact resolution

This behavior was reproducible within the witnessed round:

1. Before pressing Start, the browser window could be resized to an arbitrary
   size. Starting the engine then selected that exact canvas size — for example
   a non-standard dimension around 1194×622 — and the game fit the window
   correctly.
2. Resizing the browser window after the engine had started did not make the
   game adopt the new exact size automatically.
3. The in-game video settings initially displayed the exact non-standard
   startup size. Once the operator tried to change it, the UI offered only its
   conventional resolution list. Those choices were either smaller or larger
   than the browser window and could not restore an exact fit.
4. The HTML Fullscreen control itself worked in both directions and input
   remained healthy. Entering fullscreen after engine start, however, retained
   the mismatched rendering dimensions. Entering fullscreen before Start let
   startup derive the correct fullscreen size.

The checklist requires the view to fill the screen **at the new size**, so the
working Fullscreen control is not enough to call this check a pass. This is a
new resize/fullscreen finding, distinct from the three accepted renderer
symptoms. This report records behavior only; it does not assign a root cause or
silently expand the ongoing renderer investigation.

## Cleanup and repository state

- The loopback server was stopped with `SIGINT` after the second launch.
- No listener remained on port 8174.
- `build/arena-serve/` remains only as a gitignored staged tree.
- The two fresh Brave profiles remain under `/tmp` and may be removed by normal
  temporary-directory cleanup; they contain no project source.
- No existing source, manifest, provenance record or evidence document was
  changed during the round. This report is the only new repository file made
  for the result.

## Closure decision left to the WP4 owner

The functional slice passed every manual gameplay, input, focus, audio,
identity, console and clean-relaunch check. Two facts must remain explicit when
the pending WP4 evidence is closed:

- the operator consciously accepted Brave/KDE for this local round, while the
  current checklist names pinned Chrome for Testing on GNOME; and
- runtime resizing/fullscreen does not satisfy the existing exact-new-size
  requirement.

The owner can now decide whether to amend/waive the declared environment and
record the resize behavior as an accepted finding, or keep the corresponding
acceptance item open. This report does not make either policy decision on the
owner's behalf.

