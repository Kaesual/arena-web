<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Browser arena: prototype work packages

**Status:** Independently reviewed and approved; WP0 through WP5 are complete
— including WP2's routed acceptance of 2026-08-30 and the renderer fix carried
as a WP1 contract amendment. WP6's decision was made by the operator on
2026-08-30 — strategy 2, `FRAGMENT_SIZE = 704` at the record-backed 768-byte
floor, with the profile bounds and all ten WP8 thresholds frozen — every point
as the analysis proposed, and recomputable from the committed records. The
mandatory independent protocol/security review returned **fix-first**; its four
MAJOR findings were resolved, the three extensions it produced were settled by
the operator the same day, and a re-verification pass then **passed**, finding
no decided value had drifted. **WP6 is complete.** WP7 and WP8 carry
implementation-ready contracts. The operator explicitly authorized WP7 on
2026-08-31; implementation is in progress. WP8 remains blocked on WP7.

This document turns the reviewed direction in
[`initial-plan.md`](initial-plan.md) into coherent, testable increments. It
covers the first browser-arena vertical slice only. Later product integration
is represented by a final design package, not silently included in the
prototype.

## How to use this plan

A work package is complete only when all of the following are true:

1. Its build and source inputs are immutable and publicly obtainable. A routed
   acceptance environment may be supplied at runtime only where the WP names
   it explicitly; its public contract and non-secret evidence remain complete.
2. Its automated checks and manual acceptance have passed.
3. The complete diff has received a review proportional to its risk.
4. Review findings are fixed or recorded as an explicit blocker.
5. New system facts and decisions are reflected in the public documentation.
6. Changed submodules are committed and pushed before their pins move here.

Generated build and content artifacts are evidence, not Git inputs, unless a
later distribution contract explicitly says otherwise. Evidence records must
identify the input commits and digests, commands, target environment and output
digests well enough for a reviewer to repeat the result.

The current default acceptance platform is Linux x86_64 with an exact
Chrome-for-Testing version. WP0 freezes the full version string, archive digest
and desktop OS contract before implementation begins. Replacing this platform
is a plan change; adding another platform is later scope.

## Delivery map

| WP | Outcome | Depends on | State |
| --- | --- | --- | --- |
| WP0 | Immutable toolchain and acceptance baseline | — | ✅ Complete — exact public pins, closed license/tree inventory, schemas, relay vector and fail-closed validation |
| WP1 | Reproducible ioq3 browser build | WP0 | ✅ Complete — two clean offline builds in the pinned Emscripten 6.0.8 image produce byte-identical artifacts and one validated manifest, with the observed component, QVM/lcc, license-closure and isolation findings recorded. **Amended 2026-08-30:** "unmodified ioq3" is deliberately replaced by "the pinned upstream base plus the patches the lock enumerates", to carry the WP4 renderer fix; see the amendment section of the evidence document |
| WP2 | Relay conformance probe and routed-path measurement | WP0 | ✅ Complete — public contract (amended 2026-08-30 to the in-band session profile), browser probe, in-memory adapter and the deterministic suite, plus the routed acceptance of 2026-08-30: five pinned-browser sessions (three sequential, and two concurrent whose completion implies distinct assigned addresses via the relay's ownership rule) over a routed path, the in-band terminal refusal observed, zero-length datagrams carried both ways, and the validated merged record committed with a 1,024-byte datagram maximum and a 768-byte conservative inner floor for WP6 |
| WP3 | Audited deterministic minimal-content closure | WP0 | ✅ Complete — two clean assemblies in the pinned builder image produce a byte-identical 668-member `oa_pvomit` FFA pack from six digest-pinned Debian-cleaned OpenArena archives, every member `GPL-2.0-or-later` with resolved notices, and every reference the two static readings of the pinned `baseq3` QVM sources extract either resolves or is a recipe acceptance with a stated reason. **Amended 2026-08-31:** the recipe gained a fail-closed `derivedReferences` category for the weapon-model names the cgame constructs at runtime by string surgery — invisible to any static reading — with a reverse reconciliation over the whole derivation space and both pinned construction sites declared, restoring the missing barrels, every reachable muzzle flash and the distinct first-person hand rigs (grapple excluded by operator decision); the pack is now 698 members under a reissued identity, sizing-neutral for WP6, and the fix-first findings of the amendment's independent review are resolved in the amendment itself; see the amendment section of the evidence document |
| WP4 | One-map offline browser arena with bots | WP1, WP3 | ✅ Complete — the witnessed round of 2026-08-30 passed every gameplay, input, focus, audio, console and clean-relaunch check (report in the evidence documents); the known browser-renderer defect class (white lightmapped surfaces mitigated by the reviewed `r_vertexLight` workaround and guarded by a near-white regression check; distance-graded entity shading; frame flicker) is recorded as decided while the timeboxed root-cause hunt continues on scratch builds. Closure accepted the operator-chosen Brave/KDE variation (the pinned Chrome is exercised by the automated harness on every gate run) and recorded one new accepted limitation: runtime resize and fullscreen-after-start do not update the engine resolution. **Post-closure, 2026-08-30:** the defect class is resolved — white lightmapped surfaces root-caused to a GLSL ES `mediump` precision default and fixed in the engine (workaround removed), entity shading reclassified as renderergl2's normal native look rather than a browser defect, and frame flicker confirmed resolved by the operator on the real display, who also confirmed light-strip parity with the native reference images. Two pre-existing content gaps the operator reported at the same time — the machine gun's missing barrel model and the lightning gun's missing beam art — are recorded in the evidence document as an open content follow-up. **Post-closure, 2026-08-31:** that follow-up is closed by WP3's content amendment, which also corrects the recorded lightning-beam cause — the beam art was always packed; the missing `lightning_flash.md3` gated the beam path — as a dated note in the evidence document |
| WP5 | Matching native server and packet census | WP0, WP3 | ✅ Complete — the pinned native toolchain, the reproducible dedicated server, the runtime-base server image and a 41,833-datagram census of a driven session are built, reviewed and green, and the witnessed round of 2026-08-30 closed the one outstanding acceptance word with a player kill against a bot (report and native reference images in the evidence documents) |
| WP6 | Measured network-sizing decision | WP2, WP5 | ✅ Complete — a reviewed, recomputable sizing decision: the routed budget refutes intact datagrams, and the selected strategy is a symmetric `FRAGMENT_SIZE` reduction to **704** at the record-backed 768-byte floor, with profile bounds for the out-of-band classes the engine never fragments, a fail-closed emitted-size check on both endpoints at `Sys_SendPacket`, and ten frozen WP8 thresholds. Built: [`wp6-network-sizing.md`](wp6-network-sizing.md), the `scripts/derive-network-sizing.py` derivation that recomputes every number from the two committed records (with a suite that requires doctored records to flip its verdicts), and implementation-ready WP7/WP8 contracts. The operator decided all five open points on 2026-08-30 and the three review extensions the same day; the mandatory independent protocol/security review returned fix-first, and its four MAJOR findings — the unlisted 1,038-byte `getmotd`, the unenforced server-browser exclusion, the one-directional fail-closed machinery and the shared relay address — were resolved and confirmed by a re-verification pass that found no decided value had drifted |
| WP7 | Browser backend and matching server rebuild | WP4, WP5, WP6 | 🚧 In progress — explicitly authorized 2026-08-31; the implementation uses the existing engine send/receive boundary, with ioq3 fragmentation remaining the only fragmentation layer |
| WP8 | Two-browser multiplayer acceptance | WP5, WP7 | Contract complete, thresholds frozen 2026-08-30; blocked on WP7 |
| WP9 | Public product-integration blueprint | WP8 | Scope gate |

WP1, WP2 and WP3 are independent after WP0 and may be scheduled separately.
WP4 and WP5 may proceed independently after WP3. WP7 must not be approved or
implemented until WP6 has selected the transport and sizing strategy from
measured evidence.

```mermaid
flowchart LR
    WP0 --> WP1
    WP0 --> WP2
    WP0 --> WP3
    WP0 --> WP5
    WP1 --> WP4
    WP3 --> WP4
    WP3 --> WP5
    WP2 --> WP6
    WP5 --> WP6
    WP4 --> WP7
    WP5 --> WP7
    WP6 --> WP7
    WP5 --> WP8
    WP7 --> WP8
    WP8 --> WP9
```

## WP0 — Immutable baseline

### Outcome

The repository can identify every tool and acceptance environment used by the
prototype without a moving tag, an ambient host dependency or an undocumented
download.

### Scope

- Record the existing ioq3 submodule commit as the engine and bundled
  `baseq3` gamecode input, with a closed component-level license inventory
  rather than a blanket engine license. Express the GPL core as `code/` minus
  every separately licensed directory, prebuilt library, build tool and
  source-file exception; paths may not overlap silently.
- Pin the Emscripten builder by exact version and platform-specific OCI digest.
  The selected target is Emscripten 6.0.8; ioq3 CI's 3.1.58 remains recorded
  reference evidence. The baseline records the upgrade reason and maps the
  selected target to its OCI index and platform-manifest digests.
- Pin the native builder base by platform-specific digest. It is build-only,
  not a base for a distributed runtime image.
- Pin one Linux x86_64 Chrome-for-Testing archive by full version, public URL
  and cryptographic digest, plus the exact desktop OS version used for manual
  acceptance and the signed checksum identity for its installation medium.
- Define the machine-readable lock and provenance formats used by later WPs.
- Define the canonical artifact-manifest format and digest algorithm.
- Record the routed-browser trust mechanism: public Web PKI or a pinned
  `serverCertificateHashes` certificate satisfying browser constraints. Do not
  rely on an undocumented machine-wide trust-store change.
- Record the game-neutral WP2 measurement vector, including boundaries around
  1,300, 1,307, 1,309, 1,312 and 1,314-byte inner UDP datagrams, useful
  resolution below those boundaries and explicit oversized cases through
  ioq3's 16,384-byte message bound.
- Add validation that rejects moving references, missing digests, unknown
  licenses and incomplete preferred-source records. Exercise all published
  schemas with committed or test fixtures and prove load-bearing rejection
  paths with negative tests. Bind validation to the staged submodule URL and
  branch metadata and gitlink that form the commit candidate, reject unstaged
  `.gitmodules` changes, require a clean ioq3 checkout and check the reviewed
  third-party/tool inventory against that tree.

### Acceptance evidence

- A clean checkout validates every committed lock without performing a build.
- Every network input is an immutable commit, exact archive plus digest, or
  platform-specific image digest.
- The browser and builder can be obtained from only the committed public
  metadata.
- The provenance schema distinguishes code licenses from separately aggregated
  content licenses and records per-member role, obligations and resolved notice
  members.
- Generated artifact and content records bind themselves to the exact baseline
  identity; every declared baseline input agrees with its locked kind and
  immutable identity.
- `git diff --check` and the lock/provenance validators pass.
- The canonical baseline SHA-256 is documented, and later manifest/provenance
  identities are explicitly reissued after any baseline byte change.

### Explicit non-goals

- Compiling ioq3.
- Downloading or selecting game content.
- Modifying the ioq3 fork.
- Defining production deployment or secrets.

### Review

Focused reproducibility and licensing review of the lock formats, validators
and every initial pin.

## WP1 — Reproducible browser build

**State:** ✅ Complete. The evidence is
[`wp1-build-evidence.md`](wp1-build-evidence.md); the artifact identities are
[`manifests/browser-client.json`](../manifests/browser-client.json).

### Result

The scope below was met without an engine or build-system change, so no ioq3
enablement WP was needed and WP1 closed with no `web` branch. That last part was
amended on 2026-08-30, deliberately and after the fact: WP4's witnessed round
found a renderer defect that only the engine could fix, so the pin is now the
fork's `web` branch — the same upstream base, plus the patches the lock
enumerates and a validator checks. See
[the amendment section](wp1-build-evidence.md#amendment-of-2026-08-30-the-pin-carries-a-patch-series).
Everything below still holds; none of it depended on the tree being unpatched.
Decided facts:

- Emscripten 6.0.8 builds the pinned tree unchanged. The SDL port, filesystem
  export and ES-module settings all work as upstream wrote them, so the
  compatibility gate closes on 6.0.8 and 3.1.58 stays reference evidence.
- Accepted builds are offline. `-sUSE_SDL=2` makes the SDK fetch an SDL2 source
  snapshot, so that snapshot is pre-fetched once against the identity the
  pinned SDK itself pins and then mounted read-only.
- Two clean builds produced identical manifests and byte-identical artifacts.
  Determinism needs `SOURCE_DATE_EPOCH`, fixed container paths, a fixed
  locale/timezone and a source export without Git metadata — all product
  orchestration, and still no patch to any build-system file. The epoch is
  taken from the lock's upstream base commit, so a renderer-only engine patch
  moves the renderer artifacts and leaves the QVMs and the dedicated server
  byte-identical.
- QVM generation does execute lcc, as native host tools inside the same pinned
  builder, and no lcc source or executable reaches the distributable tree. The
  WP0 distribution boundary holds and no release-policy review is due.
- Two WP0 `sourceRole` values are corrected by observation: the bundled SDL2
  and OpenAL snapshots are not inputs to the browser artifact at all. The
  artifact's SDL2 and OpenAL come from the Emscripten SDK.
- The client requires no cross-origin isolation in this non-threaded link
  configuration.

### Outcome

A clean checkout reproducibly builds the pinned ioq3 Emscripten target without
product networking, and emits an exact artifact manifest. Engine source changes
are not forbidden but enumerated: the lock names the upstream base and every
patch on top of it, and the validator refuses a pin whose real diff is not
exactly what is declared.

### Scope

- Add product-owned build orchestration around the official CMake/Emscripten
  target.
- Start each accepted build from a deleted build tree.
- Keep build output outside Git and produce a deterministic manifest of the
  JavaScript, WebAssembly, data and generated shell/configuration artifacts.
- Bind the manifest to WP0 and require `ioq3` and `emscripten-builder` as exact
  baseline inputs; renamed or digest-divergent substitutes fail the build.
- Run two clean builds in the pinned builder and compare their artifacts.
- Verify the existing upstream path in which Emscripten disables native game
  libraries but leaves QVM builds enabled. Use a separate, equally pinned
  host-tools phase only if the clean build disproves that prior evidence.
- Map every WP0 engine component to the observed preprocessing, compilation,
  link or exclusion result. In particular, confirm or correct whether the
  OpenAL snapshot is interface-header-only, whether SDL/curl/Mumble/updater and
  prebuilt SDL libraries are excluded, and which minizip, codec and renderer
  exceptions enter the browser artifact.
- Record whether QVM generation executes the restrictive lcc build tool. Keep
  lcc source/executables out of distributable browser and native artifacts and
  stop for a release-policy review if the intended distribution would exceed
  WP0's public-source-submodule boundary.
- Audit the version-sensitive SDL port, filesystem export and ES-module settings
  explicitly during the 3.1.58-to-6.0.8 compatibility gate.
- Audit and record the actual linked license closure, including runtime code
  supplied by Emscripten rather than the ioq3 checkout, and assemble the notices
  and corresponding-source obligations needed for browser distribution.
- Record whether the resulting client actually requires cross-origin isolation;
  the current non-threaded link configuration suggests that it may not.
- Treat the generated upstream shell and retail-data configuration only as
  build evidence; do not package them as the product loader.

### Acceptance evidence

- Two clean builds from the same inputs produce identical artifact manifests
  and byte-identical distributable engine artifacts.
- Both accepted builds use Emscripten 6.0.8. A compatibility failure stops for
  review and cannot silently switch the accepted result back to 3.1.58.
- Compiler and tool versions in the log match WP0 exactly.
- No proprietary PK3 or other Quake III data is read or emitted.
- The output manifest identifies every artifact by size and cryptographic
  digest, names the exact WP0 baseline and agrees with its ioq3 and Emscripten
  identities.
- The license report distinguishes the GPL engine/QVM from every separately
  licensed linked component, has no unresolved provisional source-role claim
  and resolves all shipped notices.
- A documented command reproduces the result from a clean checkout.

This WP has no playable-runtime acceptance because free data has not yet been
selected. A successful compile is the complete intended outcome.

### Failure boundary

If the pinned upstream tree cannot build without an engine change, stop this WP
and propose a narrowly scoped ioq3 enablement WP. Do not create the `web` branch
or patch the engine as an unreviewed side effect.

### Explicit non-goals

- Product browser shell or UX.
- Content assembly or offline play.
- Browser networking.
- Native server container.

### Review

Build/reproducibility review of scripts, clean-build enforcement, toolchain
identity and artifact comparison.

## WP2 — Relay conformance probe and routed-path measurement

**State:** deterministic part implemented, routed acceptance pending. The
evidence is [`wp2-relay-probe.md`](wp2-relay-probe.md) and the published
contract is
[`relay-datagram-contract.md`](relay-datagram-contract.md). The specification,
the browser probe, the in-memory adapter and the deterministic tests exist; no
measurement against a real endpoint has been taken, and the payload budget WP6
needs has not been derived.

The guided readiness check recorded in
[`wp2-routed-readiness-2026-08-30.md`](wp2-routed-readiness-2026-08-30.md) found
that the integration profile did not match the session model of the relay the
routed round will use. The compatibility increment it names is **implemented**:
the contract and the probe moved to the in-band session profile, the 40-byte
routing header became a public self-contained profile the probe builds and
validates, the invalid-authorization case became an in-band refusal followed by
session termination, and the zero-length boundary is exercised in both
directions. The amendment is recorded in the evidence document; the committed
measurement vector is unchanged.

What is still missing is not code. The routed round waits on the operator-
supplied runtime values the evidence document lists and on the integration
provisioning of the readiness report's steps 5 to 7 — the byte-exact UDP echo
destination, the single-use authorizations and the endpoint trust input.

### Outcome

A reviewer can run a public standalone browser probe against a compatible
shared WebTransport-to-UDP relay and measure the usable routed datagram path
without ioq3. This repository does not create a second relay server.

### Scope

- Specify the game-destination subset of the public protocol needed for
  authorization, one virtual client address, one pinned virtual destination,
  keep-alive and bidirectional datagrams. Write it independently from observed
  wire behavior; copied implementation text or code retains its source notice.
- Specify the fixed framing: a 40-byte relay header, one or more big-endian
  `u16`-length-prefixed UDP datagrams from browser to server, and exactly one
  such UDP datagram from server to browser. A single datagram therefore adds
  42 bytes, and its destination port must match the projected endpoint exactly.
- Add a small browser probe that accepts an endpoint, certificate/trust input,
  short-lived authorization and virtual destination at runtime and commits none
  of those environment-specific values.
- Separate protocol framing and measurement logic from the browser transport so
  deterministic tests can exercise them with an in-memory adapter.
- Send the WP0 measurement vector, adjacent boundary sizes and session-specific
  payload-prefix nonces in both directions through a configured UDP echo
  destination behind the relay. Run payloads smaller than the nonce
  sequentially. Include single-datagram cases in both directions and bounded
  packed multi-datagram cases only from browser to server, matching the
  asymmetric framing contract, while keeping the probe implementation
  game-neutral.
- Run the public probe across at least one routed network path. Keep endpoint
  details out of the repository and record only non-secret topology
  characteristics.
- Record the browser-reported datagram size, successful payload range and
  failure behavior for each accepted session. Verify the known 42-byte
  single-datagram framing overhead rather than treating it as a measured
  transport constant.
- Derive a conservative payload budget from repeated sessions. This is an
  input to WP6, not permission to change ioq3 packet sizing.

### Acceptance evidence

- Automated probe tests reject missing runtime configuration, malformed relay
  frames, mismatched nonces and out-of-range measurement records.
- The routed endpoint rejects invalid authorization and accepts a fresh
  short-lived authorization for only the configured virtual destination.
- Two concurrent probe sessions with distinct virtual addresses and fresh
  single-use allowances receive only their own nonce-tagged echo traffic.
- Boundary tests cover empty, maximum accepted and oversized datagrams in both
  directions without unbounded allocation or an uncaught browser failure.
- The real pinned browser completes repeated probe sessions and emits a
  machine-readable measurement report for the routed path.
- The report preserves per-session/per-path results and does not present one
  observed browser maximum as a universal transport constant.
- A clean public checkout can run every deterministic probe test and can repeat
  the routed acceptance when supplied a compatible endpoint and one-time
  credentials.
- The published game-destination contract and in-memory adapter are complete
  enough for an independent implementation to satisfy the same conformance
  tests without access to the operational relay source.

### Explicit non-goals

- A relay server implementation or game-specific proxy container.
- Environment-specific relay deployment, credentials or operational details.
- ioq3 or game-protocol awareness.
- Tunnel fragmentation, WebSocket fallback or stream-assisted game traffic.
- Multi-server routing or public server discovery.

### Review

Protocol/client review covering runtime secret handling, framing, session
isolation and measurement correctness. Relay-side changes receive their own
review in the repository that owns the shared implementation.

## WP3 — Audited minimal-content closure

**State:** ✅ Complete. The evidence is
[`wp3-content-closure.md`](wp3-content-closure.md); the member-level provenance
is [`provenance/arena-web-ffa-content.json`](../provenance/arena-web-ffa-content.json)
and the pack identity
[`provenance/arena-web-ffa-content-manifest.json`](../provenance/arena-web-ffa-content-manifest.json).
**Amended 2026-08-31**, in band: the recipe gained the fail-closed
`derivedReferences` category for the runtime-constructed weapon-model names no
static reading can see, and the pack identity was reissued at 698 members; see
[the amendment section](wp3-content-closure.md#amendment-of-2026-08-31-the-names-the-gamecode-constructs-at-runtime).

### Result

The scope below was met from six digest-pinned Debian-cleaned OpenArena source
packages, without OpenArena gamecode and without a schema or baseline change.
Decided facts:

- The audited free closure **does** satisfy the pinned `baseq3` QVM profile.
  No required asset class is missing, so the WP4 failure boundary is not
  approached from the content side.
- Debian's per-file `debian/copyright` review is the licence evidence, checked
  against the material's own `COPYING`, `README`, map credits and per-file bot
  headers. Every packaged member is `GPL-2.0-or-later`; the two GPL-2-only
  items in the upstream set (`merman`, `oa_thor`) are excluded by an enforced
  rule, not by inspection.
- The Debian `orig` tarballs carry the upstream asset sources alongside the
  packaged files, so the preferred source form of the selected map, model and
  textures travels in the same pinned artifact.
- The member set is derived, not curated: it is the closure of what the pinned
  `baseq3` translation units reference, with `MISSIONPACK` branches removed and
  undecidable preprocessor conditions kept, expanded through the engine's own
  image, sound, shader, MD3, skin, BSP and botfile resolution rules.
- Reading path-shaped string literals is **not** sufficient on its own: the
  gamecode registers plenty of content under names a shader script defines,
  such as `white`, `menuback` or `powerups/quad`. The first argument of every
  content-registration trap is read as well, and the trap supplies the kind.
  The check's claim is bounded accordingly: every reference those two readings
  extract resolves or is an accepted exception, which is not the same as every
  reference the running game can make.
- Two clean assemblies produce a byte-identical PK3, provenance record and
  manifest. Determinism needs a sorted member order, a fixed ZIP timestamp and
  mode, a compression level passed per member, and reads straight out of the
  digest-verified archives — no unpacked intermediate tree exists. It also
  needs a pinned interpreter and zlib, so the accepted assembly runs in the WP0
  builder image with `--network none`.
- The content-pack artifact manifest lives under `provenance/` rather than
  `manifests/`, because the artifact-manifest validator requires
  `emscripten-builder` as a declared baseline input for anything under
  `manifests/` and no content assembly uses it. That is a placement decision;
  nothing WP0 or WP1 owns was changed.

### Outcome

The repository can deterministically assemble a candidate one-map FFA content
pack from verified free sources, with complete member-level provenance and no
proprietary Quake III data.

### Scope

- Select one map, one player presentation, required weapons/effects, bot data,
  fonts, menus, sounds and the smallest static dependency closure needed for
  the prototype.
- Prefer sources with a verified free license and obtainable preferred source
  form. Aesthetics break ties only after provenance and closure size.
- Record every upstream input by immutable revision or archive digest.
- Record each output member's source, license, notice requirements,
  transformation and preferred source form.
- Build deterministic PK3 output and a sorted member/digest manifest.
- Add static reference checks for missing shaders, textures, models, sounds,
  bot files and menu data where the formats permit it.
- Keep OpenArena gamecode out of the pack: the candidate targets the pinned
  ioq3 `baseq3` QVMs selected by the prototype plan.

### Acceptance evidence

- Every generated member has a provenance entry and an allowed free license.
- No non-commercial, no-derivatives, unknown-license or proprietary input is
  present.
- Two clean assemblies produce byte-identical PK3 files and manifests.
- The archive member list is deterministic and contains no ambient timestamps,
  local paths or untracked input.
- Static closure checks report no unresolved required member.
- The license report includes all attribution and source-offer obligations
  needed to distribute the candidate pack.

### Failure boundary

Runtime compatibility with `baseq3` QVMs is proved in WP4. If the selected
closure cannot support that profile, WP4 stops and the plan is reviewed; OA
gamecode is not added silently.

### Explicit non-goals

- A complete OpenArena distribution.
- Additional maps, modes, player models or cosmetic variants.
- Automatic server downloads or user-supplied retail data.
- Checking generated PK3 files into Git.

### Review

Full provenance/license review plus a focused deterministic-build and content-
closure review.

## WP4 — Offline browser vertical slice

**State:** implemented; witnessed real-browser acceptance **pending**. The
evidence is [`wp4-vertical-slice.md`](wp4-vertical-slice.md); the product
loader and content configuration are `arena/`, and the packaging discipline is
`scripts/arena_runtime.py`.

### Result

The scope below was met without touching the engine, the content pack or its
recipe. Decided facts:

- **The audited free content supports the pinned `baseq3` QVMs at runtime.**
  WP3's failure boundary is not reached: three bots load their characters,
  navigate the map, fire, kill each other, score and chat. No gamecode was
  switched and nothing was adopted from OpenArena's engine.
- The game directory is `arena`, not `baseq3`. `FS_CheckPak0` leaves
  `com_standalone` at 0 for ioquake3's own base game and the engine then
  refuses to start without the retail paks; any other directory name selects
  standalone operation.
- The engine requires a `default.cfg` in that directory and stops fatally
  without one. That is an engine requirement rather than a game-module
  reference, so the pack cannot be expected to carry it; WP4 supplies an
  original product-owned one.
- Three images the renderer and the client register **themselves** —
  `flareShader`, `sun` and `console` — are missing from the pack although the
  audited upstream sources contain them. This is a gap in WP3's closure roots,
  not in the free content, and it is cosmetic for this profile.
- The client still requires no cross-origin isolation. The re-check is on the
  shipped artifacts, and two complete runs passed under a header-free static
  serve.
- The six references the running game reports missing are on an explicit,
  literal acceptance list with a reason each; anything else fails the run.
- Standalone operation has consequences WP5 inherits, and they are consequences
  of the game directory rather than choices: the v4-only authorize-server
  challenge is skipped for a standalone game (`code/server/sv_client.c:150`)
  together with the CD-key paths it carries, and `banUser`/`banClient` are not
  even registered (`code/server/sv_ccmds.c:1526`). A native server built from
  the same content therefore has no id-asset authorization and no built-in ban
  commands, which the WP5 census and any later hosting design have to account
  for.

### Outcome

The pinned browser build, ioq3 `baseq3` QVMs and WP3 content form a playable
one-map FFA experience with offline bots in the real WP0 browser.

### Scope

- Add the product-owned browser loader and content configuration.
- Package only allowlisted WP1 runtime artifacts and the exact WP3 content.
- Start directly into the one-map FFA profile with at least one bot.
- Implement only the loader behavior needed for canvas sizing, pointer lock,
  keyboard/mouse input, fullscreen and user-activated audio.
- Record load timing, frame timing, console errors and exact runtime identities.
- Verify focus loss/recovery and a second clean launch.

### Acceptance evidence

- The exact WP0 browser loads from a clean local serve and enters the map
  without proprietary files or network-fetched game media.
- The user can move, look, fire, take/deal damage, score and complete or restart
  an FFA session with bots.
- Pointer lock, keyboard/mouse input, fullscreen, audio activation and focus
  recovery work in a real visible browser.
- Client console and browser console contain no missing required asset, QVM
  rejection, uncaught exception or WebGL fatal error.
- Runtime engine, QVM and PK3 identities match committed manifests.
- A repeat launch uses only declared local artifacts and reaches the same
  profile.

### Failure boundary

If free content cannot support the pinned ioq3 `baseq3` QVMs, stop and return
to plan review with concrete missing contracts. Switching gamecode or adopting
the OpenArena engine is outside this WP.

### Explicit non-goals

- Multiplayer or browser network backend.
- Persistent settings, OPFS, accounts or launcher integration.
- Additional maps/modes, touch input or non-Chromium browsers.
- Production hosting and caching policy.

### Review

Browser-shell/code review plus witnessed real-browser acceptance. Headless
rendering alone is insufficient.

## WP5 — Matching native server and packet census

**State:** complete. The approved WP0 amendment landed on 2026-08-30, was
independently reviewed at WP0 level and its findings were fixed in the same
step, which unblocked this work package; the implementation and its review
followed, and the witnessed round of 2026-08-30 closed the final acceptance
word (see the Result section below and
[`wp5-witnessed-round-2026-08-30.md`](wp5-witnessed-round-2026-08-30.md)).

### Why WP5 stopped, and the approved resolution

The scope requires the server's runtime base to be "separately pinned … with
its distribution and preferred-source obligations recorded". The WP0 baseline
cannot express that record, verified against the real validator, not assumed:

1. Every `tools[]` entry is validated with `product_input=False`, which demands
   a registered tool-only LicenseRef **and** a non-distribution `distribution`
   value (`scripts/metadata.py` `_validate_license`). A runtime base that ships
   inside a server image has redistribution obligations, so every honest
   attempt is rejected. The schema has no record type for a redistributed
   image at all — a WP0 gap, not a WP5 inconvenience.
2. The validator requires the tool id set to equal exactly
   `{chrome-for-testing, emscripten-builder, native-builder-base}`.
3. Any byte change to `locks/baseline.json` moves the canonical baseline
   identity that `manifests/browser-client.json` and
   `provenance/arena-web-ffa-content-manifest.json` bind, and that
   `docs/immutable-baseline.md` documents.
4. There is no side channel: `schemas/` is closed to exactly four files and
   every metadata JSON must declare one of the three known schemas.

**Approved resolution (option "WP0 amendment"):** one dedicated, fully
reviewed amendment step before any WP5 implementation:

- Add a redistributed-product-image record type to
  `schemas/baseline-lock.schema.json` and its validation to
  `scripts/metadata.py`: digest-pinned identity, license evidence, explicit
  redistribution and preferred-source obligations, an allowed-license gate of
  its own — without weakening any existing rule; the exact-set closures stay
  closed and are extended deliberately.
- Extend `tests/test_metadata.py` with the positive path and the negative
  paths (wrong license class, missing obligations, unexpected collection
  members).
- Add the WP5 runtime-base entry to `locks/baseline.json` under the new type.
- Reissue the two bound records by re-running the deterministic WP1 browser
  build and WP3 assembly: every artifact digest must remain byte-identical
  (the builds do not consume the new entry); only the recorded baseline
  identity moves. Update the documented identity in
  `docs/immutable-baseline.md` together with the amendment rationale.
- Full independent review at WP0 level (reproducibility and licensing), like
  every baseline decision.

Rejected alternatives, for the record: a WP5-owned side validator (a second
license gate beside WP0's single one — a weakening); a fully static server on
`scratch` (does not answer the native test client's runtime needs and leaves
the WP0 gap open); deferring the pin (violates the empty-writable-state
acceptance).

**Amendment outcome (2026-08-30).** Implemented as one amendment commit and
independently reviewed at WP0 level; the verdict was that the amendment
preserves the fail-closed guarantees, with nothing blocking. The review's one
MAJOR — the corresponding-source record over-claimed a third-party archive as
a distributor's discharge — and its eight MINORs were all fixed in the same
step: the committed record now also carries `written-offer-on-request` as
arena-web's own backstop obligation, the validator forbids a redistributed
image that reuses any digest or reference a `tools[]` entry pins as
build-only, the third license class's whole gate lives in one function that
the generic license gate refuses to serve, the pre-existing product/tool
registry disjointness and two previously untested branches are pinned by
tests, and `evidenceUrl` is a commit-pinned `docker-library/repo-info`
locator. `docs/immutable-baseline.md` carries the amendment rationale; the
baseline identity became
`sha256:036573866ac5d3da70fbe0b736d8196ebfa94f8b5002bca7fd31fd91943fc1eb` and
no artifact digest moved (both deterministic builds re-verified by the
implementer and independently reproduced by the coordinator). That identity was
superseded later the same day by the engine-pin amendment. The coordinator
reviewed the fix delta directly — gates, external evidence claims, a mutation
check on the new validator branch — and recorded the decision that a second
independent review round was not required for it.

Recorded for WP5 rather than fixed here: `_baseline_input_identities`
deliberately excludes the new record type, so a WP5 artifact manifest that
declares `server-runtime-base` as a baseline input fails closed until WP5
extends that mapping as part of its own reviewed change. **WP5 has since done
so**, adding the third collection to that mapping with positive and negative
tests and without changing any rule an input is checked by; see the Result
section below.

### Result

The scope below was met without an engine change, without a schema change and
without a second license gate. The evidence is
[`wp5-packet-census.md`](wp5-packet-census.md); the server image's content
identity is
[`provenance/arena-web-server.json`](../provenance/arena-web-server.json) and
the census is [`records/wp5-packet-census.json`](../records/wp5-packet-census.json).
Decided facts:

- **The builder pin needed a package lock of its own, and got one.** The pinned
  Ubuntu base carries no compiler, so WP5 pins the packages its toolchain adds
  in `locks/native-toolchain-packages.conf`: one immutable
  `snapshot.ubuntu.com` archive at an exact timestamp, every package by version,
  size and SHA-256 from that snapshot's signed index, fetched and verified
  before an offline image build. An accepted build resolves nothing.
  `locks/native-toolchain-indexes.conf` records the signed index files those
  digests came out of, so the trust root is re-checkable offline down to the
  Ubuntu archive keyring rather than resting on one unrecorded networked run.
- **The dedicated server is reproducible.** Two clean builds produce a
  byte-identical binary, and two image builds produce the same image id. The
  server's QVM is the accepted WP1 artifact rather than a second build of the
  same bytecode, so no `lcc` executable is produced at all.
- **The image adds four files to the pinned runtime base and changes nothing
  else**, verified by comparing the whole filesystem of the built image with the
  base's. All 78 per-package copyright files survive — and the obvious check
  finds only 76, because two of the base's documentation directories are
  symlinks.
- **The census is consistent with WP0's header numbers on every datagram**: 10
  bytes client-to-server, 8 server-to-client, +4 when fragmented. That is a
  consistency check rather than an independent measurement — the header length
  is computed from the pinned engine's constants, and what the capture adds is
  that all of the session's datagrams agree with it and none was shorter than
  its computed header. The observed maximum is 1,312 bytes, exactly
  `FRAGMENT_SIZE` plus the fragmented server header, and no netchan datagram
  reached `MAX_PACKETLEN`.
- **Connectionless traffic is rare but not small.** `statusResponse` was 464
  bytes with four players and grows with the player count, and out-of-band
  datagrams carry no fragment fields at all — so netchan fragmentation is not a
  bound for them.
- **`_baseline_input_identities` was extended**, deliberately and with positive
  and negative tests, so an artifact manifest may declare the runtime base it
  ships. Nothing else about how a baseline input is checked changed.
- **One acceptance word is not met by the automated session.** The client is
  driven blind and did not frag a bot; that check is recorded as an explicit
  non-gating observation and a witnessed round is listed in the evidence
  document.

### Verified groundwork, recorded ahead of implementation

Established against the pinned engine tree while the pin question was open;
the WP5 evidence document inherits and re-cites these:

- **Relay-address behaviour** (scope item): the query/challenge rate limiters
  key on the base address only — `SVC_BucketForAddress`
  (`code/server/sv_main.c:405-433`) never consults the UDP source port, so all
  relayed players share one bucket of 10 challenges per 1000 ms
  (`sv_client.c:71`; same shape for status/info/rcon, plus a global outbound
  bucket). Bans compare base address and subnet, portless
  (`SV_IsBanned` → `NET_CompareBaseAdrMask`, applied in `SV_DirectConnect`);
  banning one relayed player bans all. Netchan separates clients behind one
  address by the client-chosen 16-bit qport (`SV_PacketEvent`,
  `sv_main.c:845-870`; rationale `code/qcommon/net_chan.c:40-46`), drawn from
  `Com_RandomBytes` — isolation is therefore bounded (~1/65536 collision per
  pair), not absolute. Additional hazard the plan text does not name:
  `SV_DirectConnect` slot matching accepts base address AND (qport match OR
  source-port match) (`sv_client.c:377-379`, `461-463`), so relay source-port
  assignment policy matters.
- **Netchan header asymmetry** confirms WP0's recorded numbers: 10 bytes
  client-to-server (sequence + qport + challenge checksum), 8 bytes
  server-to-client, fragments add 2 + 2; `MAX_PACKETLEN` 1400,
  `FRAGMENT_SIZE` 1300 (`code/qcommon/net_chan.c`).
- **Build facts:** the dedicated target compiles with `DEDICATED`/`BOTLIB`
  and the null client stubs and links only `${CMAKE_DL_LIBS}` and `m`
  (`cmake/platforms/unix.cmake:19-22`, `cmake/server.cmake:43`) — no SDL, no
  GL; the native client links SDL2/zlib/ogg/vorbis/opus/openal and needs a
  real GL path (Xvfb + software GL; `SDL_VIDEODRIVER=dummy` yields no GL
  context). Server and client have very different runtime needs; the runtime
  base the amendment pins may be minimal.

**State of the implementation:** the evidence document
[`wp5-packet-census.md`](wp5-packet-census.md) records the builds, the image,
the census and the findings. Everything below is built and checked except the
witnessed "scores" round, which that document carries as a checklist.

### Outcome

A pinned native client can complete the same FFA profile against a matching
containerized dedicated server, and the session produces a trustworthy packet-
size census for WP6.

### Scope

- Build the native dedicated server from the WP0 engine commit, a separately
  pinned WP5 runtime base and the exact WP3 QVM/content identities. The runtime
  base must record its distribution and preferred-source obligations and must
  not inherit the build-only WP0 Ubuntu builder.
- Add a minimal server configuration for the single FFA map and bots.
- Build or pin a matching native test client without proprietary data.
- Run connection, representative FFA/bot play, disconnect and reconnect while
  capturing packet sizes in both directions.
- Distinguish netchan traffic from connectionless handshake/query traffic.
- Record observed maximums and distributions at the engine/UDP boundary,
  including the client-to-server and server-to-client header asymmetry.
- Record the effects of all relayed players sharing the relay's base IPv4
  address: query/challenge rate limits and address bans do not distinguish the
  per-player UDP source ports.
- Keep instrumentation outside the game protocol where possible.

### Acceptance evidence

- A native client connects, joins, plays, scores, disconnects and reconnects
  against the containerized server.
- Client and server QVM/PK3 identities agree exactly and no media download is
  attempted.
- The server container starts from an empty writable state and needs no
  undeclared host content.
- A machine-readable census covers initial queries, challenge/connect,
  gamestate, representative play and disconnect in both directions.
- The raw capture or equivalent preferred evidence can be regenerated without
  logging credentials or unrelated host traffic.

### Explicit non-goals

- Browser networking or WebTransport.
- Public master-server registration.
- General compatibility with OpenArena or retail Quake III clients.
- Production resource limits, orchestration or persistent worlds.

### Review

Container/supply-chain review plus independent validation that the census
covers connectionless and netchan traffic and measures the correct boundary.

## WP6 — Measured network-sizing decision

**State:** ✅ Complete. The operator decided every point on 2026-08-30, the
independent protocol/security review returned fix-first, its four MAJOR findings
were resolved, and the re-verification pass confirmed them with no decided value
drifted. WP7 is blocked only on explicit approval to start.

### Result

[`wp6-network-sizing.md`](wp6-network-sizing.md) carries the decision document:
the inputs by commit and digest, the full arithmetic, the code-level boundary
cases with `file:line` citations, the three strategies in the ownership order
below, the selected strategy and the frozen WP8 thresholds. Every number in
it is recomputed from the two committed records by
`scripts/derive-network-sizing.py`, which
[`tests/test_network_sizing.py`](../tests/test_network_sizing.py) exercises both
positively and against doctored records that must flip its verdicts.

What the evidence established: intact datagrams are refuted — an unchanged
engine needs a 1,356-byte frame, and the pinned browser refused to write frames
at exactly the 1,312- and 1,314-byte inner sizes it requires. That refusal is the
probe's own pre-send check against the reported maximum rather than an
observation of the path, but a datagram the client will not send is just as
fatal, and the failure lands on the gamestate during every connection rather
than rarely. A
symmetric `FRAGMENT_SIZE` reduction carries every netchan case at either sizing
target — 704 at the record-backed 768-byte floor, 896 at the derived 982-byte
budget — and costs two extra datagrams once per connect at the conservative
value, with nothing else in the profile beginning to fragment. Bounded tunnel
fragmentation is not needed for netchan traffic. What a fragment-size change
does *not* address is out-of-band traffic, which the engine never fragments and
to which it applies no packet-sized ceiling at all: `statusResponse` reaches
1,443 bytes, `connect` 1,039 and the server-elicited `echo` reply 1,022, so the
decision adds explicit profile bounds and a fail-closed emitted-size check
rather than relying on fragmentation. Those classes fall into three kinds, and
the distinction is load-bearing because an earlier two-way version of it — origin
only — is exactly what let the 1,038-byte `getmotd` go unlisted. Some are
excluded by **origin**, because the client never sends them. `echo` is excluded
by nothing: the **destination** elicits it, which is why the emitted-size check
is a requirement rather than a belt-and-braces addition. And `getmotd` and
`getKeyAuthorize` are bounded by **destination** — they are addressed to the
update and authorize servers rather than the pinned one, so the bound on them is
WP7's address rule, not a size cap.

The operator settled all five open points on 2026-08-30, each exactly as
proposed: strategy 2 with its profile bounds, the record-backed 768-byte target
giving `FRAGMENT_SIZE = 704`, the 64-byte reserve and alignment, the 512-byte
userinfo cap, and all ten WP8 thresholds frozen. The 896 alternative at the
982-byte derived budget is recorded there as considered and not selected, and
the derivation still computes it so the road not taken stays recomputable.

Still open: the mandatory independent protocol/security review. WP6 does not
close until it passes.

### Outcome

A reviewed decision combines WP2's browser-path budget with WP5's game packet
census and selects exactly one bounded transport strategy before engine network
work begins.

### Scope

- Compare conservative per-direction WebTransport payload budgets, relay
  overhead and the full native census, including connectionless packets.
- Derive code-level packet bounds for the fixed prototype profile and generate
  boundary cases that may not occur in a short observed session.
- Replay worst-case recorded packet shapes through the shared relay where
  possible.
- Select one strategy in this ownership order unless evidence justifies
  otherwise: intact datagrams with no engine change; a symmetric documented
  fragment-size reduction in the pinned browser and server engines; or bounded
  fragmentation implemented and reassembled by that matching engine pair.
  The working scheduling assumption is the symmetric reduction because one
  1,314-byte ioq3 datagram becomes a 1,356-byte relay payload.
- Treat stream-assisted game traffic as a separately owned shared-relay change.
  Selecting it requires a new cross-repository plan and review; WP6 cannot
  authorize it inside this repository.
- Specify all packet, fragment, byte, count and timeout limits required by the
  selected strategy.
- Specify behavior when the live browser budget is below the accepted floor or
  changes during a session.
- Freeze numeric WP8 acceptance thresholds for connection success, unexpected
  disconnects, reconnects, frame pacing, packet failures and latency overhead.
- Replace WP7 and WP8's scope-gate text with implementation-ready contracts and
  review them before WP7 starts.
- Require WP7 to rebuild and re-census the native server from the final `web`
  engine pin whenever the selected strategy changes server-side packet logic.

### Acceptance evidence

- Every input report identifies exact WP0/WP2/WP5 commits and artifact digests.
- Arithmetic includes relay overhead and separate direction/header cases.
- Connectionless traffic is covered explicitly; netchan fragmentation is not
  assumed to protect it.
- The selected strategy fits every accepted observed packet within a documented
  safety margin and covers the generated profile boundary cases, or defines an
  explicit bounded alternate path.
- A reviewer can recompute the decision from committed reports and scripts.
- WP7 contains no transport mechanism that this decision did not authorize.
- The decision identifies every changed owning component and the exact
  post-change browser/server rebuild and census evidence required from WP7.

### Explicit non-goals

- Implementing the ioq3 browser network backend.
- Treating a single browser session's reported maximum as a universal constant.
- Quietly lowering only one endpoint's packet sizing.
- Adding WebSocket fallback without a new plan review.

### Review

Mandatory independent protocol/security review. Any finding affecting the
strategy, bounds or census reopens WP6 and keeps WP7 blocked.

## WP7 — Browser backend and matching server rebuild

**State:** 🚧 In progress, explicitly authorized by the operator on
2026-08-31. WP6's mandatory independent protocol/security review and
re-verification passed before implementation began. The selected shape remains
strategy 2: the symmetric fragment-size reduction at `FRAGMENT_SIZE = 704`,
sized to the record-backed 768-byte inner budget, with the profile bounds. The
values below are decided, not proposed.

The implementation seam is the existing engine send/receive boundary:
`Sys_SendPacket` hands emitted browser datagrams to the product backend and
`NET_GetPacket` polls its bounded receive queue. ioq3 netchan remains above this
boundary and is the only fragmentation and reassembly layer. Native builds keep
their socket implementation at the same boundary. Product loader and
authorization plumbing remain in this repository.

### Decision values

Decided by the operator on 2026-08-30, each as WP6 proposed.

| Placeholder | Decided value | Source |
| --- | --- | --- |
| `[decision: inner budget]` | 768 bytes | WP6, record-backed contiguous floor |
| `[decision: FRAGMENT_SIZE]` | 704 | WP6, budget less the 64-byte reserve, aligned |
| `[decision: userinfo cap]` | 512 bytes | WP6; inside the selected target's 752-byte limit |
| `[decision: receive queue depth]` | 256 datagrams | WP6, frozen WP8 threshold |
| `[decision: echo treatment]` | refuse the oversize reply via the emitted-size check | WP6; disabling the handler was considered and not selected |
| `[decision: cl_motd]` | `+set cl_motd 0` | WP6, review finding M1; confirmed 2026-08-30 |
| `[decision: counter split]` | client-originated and elicited refusals counted separately | WP6, review finding m3; confirmed 2026-08-30 |
| `[decision: rate-limit buckets]` | port-aware connectionless buckets behind a new cvar, default off | WP6, review finding M4; decided 2026-08-30 as a WP7 scope extension |

The last three arrived from the independent review after the operator's original
selection and were settled by him on 2026-08-30. Two were confirmed as drafted;
the rate-limit item he decided **differently** — the draft had recorded the
shared bucket as an accepted gap, and he chose the engine change instead, which
is why WP7's scope below is larger than the first version of this contract.

### Outcome

One WP0 browser client obtains fresh authorization, addresses the pinned virtual
destination and completes the single-map FFA profile through the shared relay.
Its matching native server is rebuilt from the same final engine pin and
re-censused, because the selected strategy changes server-side packet logic.

### Fixed boundaries

- Create and publish the ioq3 fork's `web` branch before the first engine
  modification, then update this repository's branch metadata and exact pin.
- Keep the low-level engine/backend change in ioq3 and product loader/token
  plumbing in this repository.
- Choose and document one seam: platform socket replacement or the existing
  engine send/receive boundary.
- Bridge asynchronous WebTransport receipt to synchronous engine polling with an
  explicitly bounded queue of `[decision: receive queue depth]` datagrams and
  defined overflow and shutdown behaviour. Overflow is a counted, surfaced event,
  never a silent drop.
- Use a token-provider hook that obtains a fresh short-lived, single-use
  allowance for every connection or reconnect attempt; never reuse a static
  credential.
- Implement the protocol keep-alive required for an idle authenticated session,
  and record whether one turned out to be needed and at what interval — WP2
  could not answer this because its sessions were never idle.
- Preserve the engine's datagram semantics.
- Fail closed on invalid authorization, unknown destination, path-budget
  violation and relay closure.
- **Add no transport mechanism WP6 did not authorize.** In particular: no
  WebSocket fallback, no stream-assisted game traffic, and no second
  fragmentation or reassembly layer beneath the engine's own. If the sizing
  turns out to be insufficient, WP7 stops and WP6 reopens.

### Engine sizing work

- Set `FRAGMENT_SIZE` to `[decision: FRAGMENT_SIZE]` as an explicit constant in
  `code/qcommon/net_chan.c`, documented in place with the reason, the symmetry
  requirement and a pointer to the WP6 decision. Leave `MAX_PACKETLEN` at 1,400:
  reducing `FRAGMENT_SIZE` only widens the `MAX_PACKETLEN >= FRAGMENT_SIZE + 14`
  margin, and `MAX_PACKETLEN` also sizes the loopback buffer and feeds rate
  accounting, neither of which WP6 measured.
- The value is a **protocol constant**, not a send-side tuning knob: the
  receiver infers end-of-message from `fragmentLength == FRAGMENT_SIZE`. The
  browser client and the native server must therefore be built from the same
  engine pin. Asymmetry is a silent truncation, so WP7 must *demonstrate*
  agreement rather than rely on the build being careful — by the two
  artifact-level observations named in the acceptance envelope below, not by a
  source-level equality test, which cannot fail because the constant lives in
  one file.
- Enforce the `[decision: userinfo cap]` on the **formatted `connect` data
  string**, not on the userinfo copy. The two differ: `CL_CheckForResend` copies
  the userinfo and *then* adds `protocol`, `qport` and `challenge`
  (`cl_main.c:2413-2425`), so a cap applied before that copy does not bound what
  reaches the wire — and the formatted string is the whole variable term WP6's
  arithmetic bounds.
  The behaviour on violation is **refuse and surface**, never truncate.
  Truncating an info string can cut a key/value pair in half and drop the
  `challenge` key the server requires (`sv_client.c:369,402-419`), turning a cap
  violation into an unexplained connect failure instead of a clear one.
  Note what the number then means: capping the formatted string at 512 bytes
  leaves an effective userinfo allowance of **502 bytes**, once the 10 fixed
  bytes of `connect "` and its closing quote are accounted for, against the
  752-byte limit the arithmetic computes for the selected target. That is
  conservative on purpose — it is roughly two-thirds of what the budget would
  permit and still far above what the profile uses.
- Add a fail-closed emitted-size check **at `Sys_SendPacket`**, in **both** the
  browser client and the native server build. The seam is named deliberately: it
  is the only point below both `NET_OutOfBandPrint` and `NET_OutOfBandData` —
  `rcon` bypasses the former and the `connect` packet is compressed inside the
  latter — and below the `cl_packetdelay`/`sv_packetdelay` queue
  (`net_chan.c:528,550-566`). Placing it there makes "after compression"
  automatic and makes the check total.
  It compares the **emitted** datagram against the session's current accepted
  budget, refuses to send when it does not fit, and counts and surfaces the
  refusal per direction and per class. The pre-compression size is not a bound
  and must not be used as one.
- The server build carries the same check and the same counters. Its **budget
  source differs and must be stated**: a native server reads no WebTransport
  maximum, so there is nothing session-derived for it to compare against. Its
  budget is the decided **768-byte floor**, fixed at build or launch time rather
  than re-read per send. The browser's per-send re-read exists because a
  transport can change its reported maximum; the server has no such input.
  The largest over-budget classes — `statusResponse` 1,443, `infoResponse`
  1,040, the rcon redirect's `print` 1,017 — are all server-emitted, so a
  browser-only check leaves them unmeasured: the datagram would be emitted, dropped by the relay or
  the transport, and counted nowhere. A server-side over-budget emission is a
  counted, surfaced event.
- Split the refusal counters into **client-originated** and **elicited**
  (`echo`). `[decision: counter split]` — WP8's zero-oversize threshold applies
  to the client-originated counter; an elicited refusal is the mechanism working
  as designed and is reported, not failed.
- Refuse and count any send to an address **other than the pinned virtual
  destination**, and never silently re-address one. This is the structural bound
  on every second-destination class: `getmotd` (1,038 bytes, addressed to the
  update server, carrying the renderer string) and `getKeyAuthorize` (64 bytes,
  addressed to the authorize server, carrying the CD key). A backend that maps
  any `netadr_t` onto the pinned destination — the obvious shortcut when there
  is only one — would put both on the relayed path and disclose their contents
  to the game server. The rule binds regardless of size, and regardless of what
  a later engine change adds to that list.
- Re-read the transport's reported datagram maximum on every send, not only at
  session open, and derive the accepted inner budget as that value less the
  42-byte single-datagram overhead.
- Implement WP6's budget-change behaviour: a session whose accepted budget falls
  below `[decision: inner budget]` fails closed with a distinct, non-retrying
  error and closes, whether that is true at session open or becomes true later.
- Keep the server browser off the relayed path **structurally**: neutralize the
  client's `ping`, `serverstatus`, `localservers` and `globalservers` command
  paths in the `web` branch. WP6 bounds `statusResponse` and `infoResponse` by
  excluding them rather than capping them, and until this lands the exclusion is
  an assumption, not a constraint — there is no cvar and no removed command
  today, and the WP5 census that this decision sizes against actually contains
  both queries and both answers.
- `[decision: cl_motd]` launch the client with `+set cl_motd 0` as defence in
  depth behind the address rule. `cl_motd` carries no `CVAR_INIT`
  (`cl_main.c:3562` registers it with flags 0), so unlike `com_legacyprotocol`
  it can be changed after start and cannot carry this bound alone.
- `[decision: rate-limit buckets]` make the connectionless rate-limit buckets
  **port-aware**, behind a new cvar whose **default is off — exact upstream
  behaviour** — and which the managed launch profile enables with `+set`. A
  sensible name is `sv_rateLimitPerPort`; WP7 finalizes the exact spelling.
  `SVC_HashForAddress` (`sv_main.c:376-396`) mixes the source port into the
  hash, `SVC_BucketForAddress` (`:405-427`) compares it alongside the address
  bytes, and `leakyBucket_s` (`server.h:313-328`) gains a port field populated
  when a bucket is created. Only the per-address limiter is affected —
  `SVC_RateLimitAddress` (`sv_main.c:518-521`), reached by `getchallenge`
  (`sv_client.c:71`), `getstatus` (`sv_main.c:549`), `getinfo`
  (`sv_main.c:612`) and `rcon` (`sv_main.c:719`); the global
  `outboundLeakyBucket` is not per-address and does not change.
  Two things must be preserved in how this is written. The default stays
  upstream because port-keyed rate limits are **evadable on the open internet**,
  where an attacker varies the source port freely; this deployment is safe only
  because the game server is reachable solely from the relay, so the source port
  is assigned by the relay rather than chosen by an attacker. And the cvar's
  documentation must say so, because the constraint has to travel with the code.
- **Relay-side constraint the integration environment must meet:** the relay
  must never assign a **live** session's server-facing source port to another
  session. `SV_DirectConnect` matches an existing client slot on the base
  address **and (qport OR source port)** — a disjunction, at
  `sv_client.c:373-379` and `:457-465` — so a reused live port makes the server
  match the new session onto the first player's slot deterministically,
  regardless of qport. The CSPRNG requirement below does not cover this: it
  makes qport collisions unlikely, and this path needs none. Nothing in the
  engine can enforce it, so WP7 records it as a requirement on the environment
  and WP8 evidences it.
- Implement the browser's `Sys_RandomBytes` as a real CSPRNG
  (`crypto.getRandomValues`). This is a security requirement, not housekeeping:
  `qport` comes from `Com_RandomBytes` (`common.c:2816-2818`), which falls back
  to time-seeded `rand()` if `Sys_RandomBytes` fails, and on the relay's shared
  server-visible address `qport` is the **only** discriminator between the two
  players' netchans (`sv_main.c:851-862`). A collision re-homes the victim's
  netchan to the wrong relay port — the server rewrites the stored port at
  `:867-870` *before* the checksum is validated — and would deliver server
  traffic into the wrong browser session. Weak seeding also correlates
  `clc.challenge` across clients.
- Launch both the server and the browser client with `+set com_legacyprotocol 0`.
  The pinned server otherwise accepts protocol-68 connections by default, which
  bypasses the gamename check and drops the challenge-checksum spoofing
  protection, and which would put a second netchan header geometry on the wire
  besides the one WP6 derived and the census observed. This is a sizing-neutral
  hardening requirement — a compat connection only makes datagrams smaller — but
  it must be in the launch profile and asserted by the re-census.
- Handle the out-of-band `echo` class, which the destination triggers rather
  than the client. Per `[decision: echo treatment]`, the emitted-size check
  refuses an oversize reply and counts it; losing an `echo` answer costs the
  session nothing. WP7 must not assume the inbound budget bounds the reply — WP2
  could not attribute a budget per direction, so the check is the bound.

### Minimum acceptance envelope

- Automated native/unit tests cover address conversion, queue limits and
  overflow, send and receive failures, shutdown, the emitted-size check
  including the compressed `connect` case and an oversize `echo` reply, the
  userinfo cap, the budget-change path, and fragment reassembly at the selected
  `FRAGMENT_SIZE` including the zero-length terminator case.
- Symmetry evidence is the pair of artifact-level observations below, not a
  source-level equality test. Stated once here so the two bullets agree:
  the constant is
  file-static to one file, so a source-level "client and server agree" test
  compares the value with itself and cannot fail. The server artifact is pinned
  by the re-census's fragment geometry below; the browser artifact is pinned by
  WP8's zero-failed-reassembly evidence, since a browser built with a different
  value cannot reassemble the server's gamestate. WP7 states both observations
  as its symmetry evidence rather than shipping a self-comparing test.
- The exact browser connects through the shared relay to the exact server and
  can join, move, fire, score, disconnect and reconnect.
- **The server is rebuilt from the same final `web` pin and its WP5 profile is
  re-censused**, because the selected strategy changes server-side packet logic.
  The re-census must show: no datagram in either direction above
  `[decision: inner budget]`, the largest fragment datagram equal to
  `[decision: FRAGMENT_SIZE]` plus the fragmented header for its direction, the
  gamestate split into the fragment count WP6 predicts, no connectionless class
  over budget, and the netchan header widths still 8/10/12/14 — which is also
  the observable proof that `com_legacyprotocol 0` took effect.
  It must additionally assert **presence, not size**: no `getstatus` and no
  `getinfo` datagram appears in the capture at all. A size assertion cannot
  catch a server-browser violation, because this profile's realized
  `statusResponse` is 465 bytes and passes an over-budget check whether or not
  the browser is issuing queries. The capture filter must be widened beyond
  `udp port 27960` for this assertion to mean anything, and to make any
  second-destination traffic visible.
  `scripts/derive-network-sizing.py` re-run against the new census must report
  every bound still passing.
- No packet is delivered across browser sessions or outside the authorized
  virtual destination.
- Browser and server logs demonstrate exact engine/QVM/content identity.
- Real-browser acceptance passes without uncaught promise failures, unbounded
  queues or a main-thread busy loop.

### Explicit non-goals

- Two-player endurance acceptance, which belongs to WP8.
- Production deployment, public server lists or arbitrary server addresses.
- Other browsers, mobile/touch, content downloads or persistent settings.
- Any transport mechanism WP6 did not select.

### Review

Independent ioq3 diff review and protocol/security review are mandatory before
the ioq3 pin moves here. The diff review must specifically confirm that both
sides of the `FRAGMENT_SIZE` change are present and equal, and that no
fragmentation layer was added beneath the engine's own.

## WP8 — Two-browser multiplayer acceptance

**State:** Acceptance contract, drafted by WP6, with its numeric thresholds
**frozen by the operator on 2026-08-30**. WP8 may be approved once WP7 closes.

### Outcome

Two instances of the pinned browser client, originating from two independent
client networks, complete a sustained FFA session through the relay to the
matching native server while meeting the frozen thresholds.

### Fixed acceptance topology

- Two independently addressed client networks and one server-side observation
  point.
- One exact browser/OS contract and identical client artifacts.
- At least 15 minutes of active two-player FFA after both clients join.
- Planned disconnect/reconnect exercises for each client.
- Server-side evidence that both players appear only as the relay's IPv4
  endpoint with distinct per-player source ports, and that neither player's
  public address appears. The distinct source ports are **correctness** evidence
  as well as privacy evidence: `SV_DirectConnect` matches a client slot on base
  address and (qport **or** source port), so two live sessions sharing a
  server-facing port is a deterministic slot collision, not merely a privacy
  weakness.

### Numeric thresholds

The table in [`wp6-network-sizing.md`](wp6-network-sizing.md), "WP8 acceptance
thresholds", **is** the contract: connection success, unexpected disconnects,
planned reconnects, packet send failures, frame pacing, long tasks, relay-added
latency, receive-queue behaviour, reassembly and privacy. All ten were frozen by
the operator on 2026-08-30, each as WP6 proposed. They are deliberately not
restated here — one table, one place to change, and changing any of them now is
a plan change rather than a WP8 judgement call.

### Evidence envelope

- Connection/reconnect success, unexpected disconnect and packet-send failure
  counts, with oversize refusals counted separately from write failures — WP6
  requires zero of the former and tolerates a small rate of the latter, so a
  report that merges them cannot be assessed. Oversize refusals are themselves
  reported split into **client-originated** and **elicited**; the zero threshold
  applies to the client-originated count, because the `echo` mechanism WP6
  installed deliberately produces elicited refusals when it works.
- Oversize-refusal counts from **both** endpoints. The largest over-budget
  classes are server-emitted, so browser-side counters alone cannot show that no
  class was missed.
- **Distinct qports for the two clients**, read from the server side. On the
  relay's shared server-visible address `qport` is the only thing separating the
  two netchans, so this is the evidence that the browser's `Sys_RandomBytes` is
  a real CSPRNG rather than a stub. Also report any
  `SV_PacketEvent: fixing up a translated port` occurrence: on a shared address
  that log line is the observable signature of the collision hazard WP6 records.
- Evidence that the port-aware rate-limit cvar is **enabled** on the acceptance
  server, and any observed rate-limit rejection. With it on, one client's
  reconnect loop can no longer deny `getchallenge` to the other, which is what
  removes the shared-address caveat from the connection-success threshold. A
  rejection observed anyway means the cvar was not enabled or the change does
  not work, and either way is a WP7 defect rather than a threshold to relax.
- Browser- and server-side per-session evidence for packet sizes, drops,
  reassembly failures and bounded queue behaviour, including the observed
  maximum queue depth.
- The transport's reported datagram maximum sampled over the whole session, not
  only at open. WP2 could only sample it at open and recorded that as a known
  limitation; a fifteen-minute session is the first opportunity to close it.
- Whether a keep-alive was needed, and at what interval.
- Relay-side non-identifying aggregate counters for rate-limit and malformed or
  rejected traffic; do not require per-session relay metrics that do not exist.
- End-to-end latency and relay-added overhead against a direct native round trip
  on the same path, since WP6's latency thresholds are relative.
- Browser frame-time distribution, long tasks, console errors and memory/queue
  growth over the session.
- Server logs and packet observations sufficient to verify relay-mediated
  privacy without recording player public addresses in committed evidence.

### Explicit non-goals

- Load testing beyond two players.
- Browser-hosted or peer-to-peer multiplayer.
- Production SLOs, autoscaling or Internet-wide compatibility.
- Accounts, progression, matchmaking, public catalogue or moderation.
- Declaring support for untested browsers/platforms.

### Review

Acceptance-evidence review plus a final vertical-slice review across the public
loader, ioq3 pin, relay contract, content manifest and server artifact. Any
observed datagram over the selected budget, or any reassembly failure, reopens
WP6 rather than being waived here.


## WP9 — Product-integration blueprint

**State:** Scope gate. Begin only after WP8 closes the technical vertical slice.

### Outcome envelope

A public, implementation-ready design describes how the proven artifacts can
be hosted and operated without folding unrelated product features into the
prototype.

### Design topics

- Browser launch and error UX.
- Persistent settings and content caching.
- Immutable artifact publication and cache invalidation.
- Server catalogue and launch metadata boundaries.
- Authentication-token handoff through the public relay contract.
- Consequences of all relayed players sharing one server-visible base IPv4 for
  query rate limits, bans and abuse controls. **Unscheduled direction of
  travel, recorded by WP6 and deliberately not coupled to WP7:** giving the
  relay's server-facing leg a per-player **virtual IPv6 source address** — the
  address model the wider stack already uses — would restore per-address
  rate-limit bucketing exactly as upstream designed it, making WP7's cvar
  unnecessary, and would dissolve the qport-only client demultiplexing that WP6
  records as a residual hazard. It is a relay and topology change outside this
  repository, and it is the clean fix that WP7's cvar deliberately is not.
- Health/probe behavior and native-server lifecycle/resource contracts.
- Source, license, notice and artifact delivery obligations.
- Separation between public reusable components and environment-specific
  deployment configuration.

### Explicit non-goals

- Implementing the hosting application or deployment.
- Accounts, progression, moderation or a general id Tech 3 platform.
- Reopening the proven transport strategy without new measurements.

### Review

Architecture, security, licensing and operability review before any product
integration implementation is scheduled.

## Review checkpoints

1. **Plan approval:** completed. WP0–WP6 and the WP7–WP9 envelopes are approved;
   WP0 is the first authorized implementation increment.
2. **Early evidence:** after WP1–WP3, review build reproducibility, browser-path
   measurements and content provenance before assembling the playable slice.
3. **Network gate:** WP6 independently reviews the measurements and replaces
   WP7/WP8's scope gates with exact implementation and acceptance contracts.
   The analysis and the replacement contracts are complete, and the operator
   made every decision on 2026-08-30 — strategy, sizing target, reserve and
   alignment, userinfo cap and all ten WP8 thresholds — each as proposed. The
   mandatory independent protocol/security review of
   [`wp6-network-sizing.md`](wp6-network-sizing.md) then returned **fix-first**,
   endorsing the strategy and the arithmetic but requiring a complete packet-class
   census and enforceable bounds; those fixes were made, and the three
   extensions it produced — `cl_motd 0`, the refusal-counter split and a
   cvar-gated port-aware rate-limit bucket in WP7's scope — were settled on
   2026-08-30. The re-verification pass then **passed**. **This gate is
   closed.** WP7 is not blocked by any remaining review or decision; it starts
   when the coordinator and operator explicitly approve it.
4. **Vertical-slice closure:** WP8 reviews the complete two-client evidence
   before WP9 or any hosting integration begins.

## Scheduled follow-up outside the numbered packages

The operator decided on 2026-08-31 that the accepted WP4 limitation —
runtime resize and fullscreen-after-start do not update the engine
resolution — is deliberately **not** part of WP7's network scope and will be
addressed as its own small work package **after WP7**, covering the
loader/engine canvas-size propagation.

## Input still needed from the operator

No additional input is needed to review or begin WP0 if the Linux x86_64
Chrome-for-Testing default is acceptable. Before WP2's routed-path acceptance,
the operator must provide or approve a compatible integration relay endpoint,
a browser-compatible trust mechanism, a UDP echo destination, two distinct
virtual addresses and enough fresh single-use allowances for every session.
Before WP7/WP8 routed acceptance, the integration environment must provide a
fresh-token issuer or equivalent harness and host the matching rebuilt native
server behind an exact virtual-address/UDP-port route. Before WP8, the operator
must also provide or approve access to two independent client networks for the
privacy and multiplayer acceptance. Product naming, additional platforms and
production deployment choices are deliberately not prerequisites for this
prototype.
