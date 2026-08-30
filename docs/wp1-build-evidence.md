<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP1 evidence: reproducible browser build

**Status:** WP1 complete — two clean builds accepted, byte-identical. Amended
on 2026-08-30: the pin is no longer an unmodified upstream commit but the fork's
`web` branch, carrying one enumerated patch on top of it. See
"[Amendment: the pin carries a patch series](#amendment-of-2026-08-30-the-pin-carries-a-patch-series)".

This document records what an accepted browser build of the pinned ioquake3
Emscripten target actually is: its exact inputs, the commands that repeat it,
the environment controls that make it deterministic, the observed license
closure and the findings WP1 was required to confirm or correct.

The generated engine artifacts are not committed. Their identities are, in
[`manifests/browser-client.json`](../manifests/browser-client.json).

WP1 itself needed no engine or build-system change, and closed with `ioq3/`
untouched at its pinned upstream commit and no `web` branch. That contract was
amended after WP4's witnessed round found a renderer defect that only the engine
could fix; the amendment section below records what changed and what did not.

## Inputs

| Role | Identity |
| --- | --- |
| Engine and bundled `baseq3` gamecode | ioq3 fork `92351b8f0543448b9defaac25c552274eecbf15b` (branch `web`), on upstream base `588393618dbc82e7207c21c6ddecca229944a03a` |
| WebAssembly builder | `docker.io/emscripten/emsdk@sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475` (Emscripten 6.0.8, `linux/amd64`) |
| SDL2 source snapshot for `-sUSE_SDL=2` | upstream tag `release-2.32.10`, obtained as `https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip` |
| Baseline the manifest binds to | `sha256:a9126a609d3f041c60c7ca43b3db0e7be8754b9ef0862a6557e8c523038da5e5` |

That baseline identity has moved twice on 2026-08-30. First the WP0 amendment
added the redistributed server runtime base: the build does not read that entry,
so the manifest was reissued against the new whole-file identity and every
artifact digest stayed byte-identical. Then the engine pin moved to the fork
commit above, which the build very much does read; that reissue is the amendment
section below, and it moved two of the ten artifact digests.

The first two identities are read out of
[`locks/baseline.json`](../locks/baseline.json) at build time by
`scripts/baseline-inputs.py`; no build script restates a digest the lock
already owns. A renamed or digest-divergent substitute therefore fails before
a container starts, and again when the manifest is validated.

The SDL2 snapshot is not an arena-web choice. ioquake3's Emscripten target
compiles and links with `-sUSE_SDL=2`, and the pinned SDK implements that flag
as a port whose source it downloads on first use:

```text
tag:    release-2.32.10          (libsdl-org/SDL)
url:    https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip
sha512: 001738b610b42a8f8badfd6af3402f0a1a8601034adef0b8c702dd2b1951dc1b71b733a6779d97499b6f7314d226ec0c8dcffeb753f35a5c51e995ca20bdd459
sha256: 7a3c207b8509edc487d658df357ad764cd852d68fe248d307b25c0741d52fdf0
```

Which of these values belong to whom matters. `2.32.10` and the SHA-512 are the
SDK's: `scripts/fetch-emscripten-ports.sh --fetch` reads `VERSION` and `HASH`
out of the pinned image's own `tools/ports/sdl2.py`, refuses to continue if
either disagrees with this repository's record, and the SDK then verifies its
download against that same SHA-512. The URL is composed from the SDK's
`VERSION` exactly as the SDK composes it, so comparing it adds no independent
evidence. The remaining values — the SHA-256, the archive file name and the
`SDL-release-2.32.10` top-level directory — are arena-web's own records of what
that download and unpacking produce, and they are enforced by this repository
rather than by the SDK.

The archive is a GitHub-generated source endpoint, not a content-addressed
object, and GitHub has changed archive generation before. The recoverable
preferred source is therefore the upstream tag `release-2.32.10` in
`https://github.com/libsdl-org/SDL`, recorded alongside the two archive
digests. The exact upstream commit id could not be determined offline from the
material at hand: the archive carries no revision metadata — it contains no
`.gitattributes` at all, so no `export-subst` expansion applies, and
`include/SDL_revision.h` is the unexpanded placeholder that defines
`SDL_REVISION` to the empty string — and resolving the tag to a commit would
need an unpinned network lookup. A
later WP that publishes a client should resolve and record that commit id once,
under review.

### The tree the build actually reads

The digest-verified object is the archive; what the compiler reads is the
unpacked tree. The SDK does **not** re-verify a port it has already unpacked:
its `up_to_date()` check returns true on the presence of a `.emscripten_url`
marker whose content matches the port URL, and from that point the archive is
never read or re-hashed again.

`scripts/build-browser.sh` therefore calls
`scripts/fetch-emscripten-ports.sh --stage` before every build, which
re-verifies both archive digests, deletes the unpacked tree and re-creates it
from the archive with the same `shutil.unpack_archive` call and the same marker
file the SDK itself writes. The bytes the compiler sees are derived from bytes
this repository checked in the same script run, so a post-fetch modification of
the port tree cannot survive into a build, and the SDL2 identity the manifest
records is the identity of the source the build consumed.

## Reproducing the result

From a clean checkout, with the pinned builder image already present locally:

```bash
git submodule update --init --recursive
scripts/check.sh
CONTAINER_RUNTIME=podman scripts/fetch-emscripten-ports.sh --fetch   # once, online
CONTAINER_RUNTIME=podman scripts/verify-browser-build.sh             # two clean builds
```

`CONTAINER_RUNTIME` defaults to `docker`. Only the first command uses the
network. `scripts/build-browser.sh` performs a single build;
`scripts/verify-browser-build.sh` performs the two clean builds WP1 accepts and
compares them. Build output lands under the gitignored
`build/` directory and never inside either Git source tree.

The build image reference can be printed without running anything:

```bash
scripts/build-browser.sh --print-image
```

### What one accepted build does

1. Runs the metadata validator, which binds the lock to the staged submodule
   URL, branch, gitlink and a clean `ioq3` checkout.
2. Refuses to continue unless the arena-web worktree is clean, because the
   manifest records the commit that produced it.
3. Re-creates the Emscripten port tree from the digest-verified archive
   (`--stage`), then deletes the build root and exports the pinned commit
   with `git archive` into `build/<name>/source`. The export carries no Git
   metadata, which pins the compiled content to the lock and keeps ioquake3's
   optional `git describe` product version out of the artifacts.
4. Runs `scripts/build-browser-in-container.sh` inside the pinned image with
   `--network none`, `--pull never`, `--platform linux/amd64`, `--cap-drop
   all`, `--security-opt no-new-privileges` and a non-root user whose ID
   matches the invoking user. The source export is mounted read-only at `/src`
   and the staged port sources read-only at `/ports`. The only writable mount
   is `/work`, the CMake binary directory, so the build cannot modify its own
   inputs and cannot reach the build log or the manifest the host writes
   beside it.

   One flag is a deliberate relaxation: `--security-opt label=disable`, which
   `scripts/check-container.sh` already uses. On an SELinux host the
   bind-mounted checkout would otherwise be unreadable inside the container
   without relabelling the repository itself. That is a worse trade than
   dropping one confinement layer around an offline, capability-less container
   whose only writable mount is a gitignored build directory.
5. Inside the container, refuses any builder that does not report the
   baseline's Emscripten version, then runs the official upstream target
   unchanged:

   ```bash
   emcmake cmake -S /src -B /work -DCMAKE_BUILD_TYPE=Release
   cmake --build /work --parallel <jobs>
   ```

6. Refuses the result if a QVM build tool reached the distributable tree.
7. Emits the artifact manifest from the build's `Release` directory, refusing
   any `.pk3` file, symlink or other non-regular file.

The container filesystem is writable, unlike `scripts/check-container.sh`'s
read-only validation container: the Emscripten port build writes `libSDL2.a`
into the SDK's own cache inside the image. That cache is discarded with the
container, so the only persistent local build state is the digest-verified
port source, and the accepted build never mutates a toolchain that a later
build reuses.

Upstream CI uses `-G Ninja`. The pinned image has no Ninja, so the accepted
build uses CMake's default Unix Makefiles generator. This changes no compiler
or linker input.

## Determinism controls

These are product orchestration only. No ioq3 build-system file is patched, and
the one patched source file is enumerated in the lock rather than applied here.

| Control | Value | Why |
| --- | --- | --- |
| `SOURCE_DATE_EPOCH` | `1784478090` | CMake turns it into the compiled-in `PRODUCT_DATE`. Without it the engine embeds `__DATE__` and no two builds agree. The value is the committer timestamp of the lock's **upstream base** commit, so it is derived from the baseline, and the build fails if that commit's timestamp is not exactly this. The base rather than the pin, deliberately: see the amendment section. |
| Git metadata absent from `/src` | `git archive` export | Makes `PRODUCT_VERSION` exactly `1.36` instead of a `git describe` suffix whose abbreviation length is environment-dependent. |
| Fixed container paths | `/src`, `/ports`, `/work` | Build output cannot depend on where the checkout lives. |
| `LC_ALL=C`, `LANG=C`, `TZ=UTC` | fixed | Locale- and timezone-independent formatting. |
| Deleted build tree | every build | No stale object may survive into an accepted build. |

Observed effect: `-DPRODUCT_DATE="Jul 19 2026"` and `-DPRODUCT_VERSION="1.36"`
in the compile flags of both accepted builds, and a generated `version.txt`
containing `1.36`.

## Accepted builds

The current accepted result is the patched pin's. Two complete clean builds were
run by `scripts/verify-browser-build.sh` from arena-web commit
`c6a6b7f37a8d0e8046b35be8403c4f3bb4708904`; they produced identical artifact
manifests and byte-identical artifacts. A third clean build, from
`dd827ede405942766b741500bcd002f169c56bf1`, emitted the same ten digests and
differs from the committed manifest in `producer.commit` alone.

Committed artifact manifest:
`sha256:0ce5721e9ea41b6c40c542a7a0b21255fe4657d69e7ae7954618dfa62e2cfb76`.

```text
12d597a49bc351149d7459692a0311cc5e186cf4f376c703ddaa6cfa27a602e4  baseq3/vm/cgame.qvm
449fbd197d34ec3f51006438fc2bf961cdabdf38101b6a17d33f107ad4186805  baseq3/vm/qagame.qvm
23ba9181726e108be05a0096a9f49f3c7643d4ff8888267a6948c4a4e8389c33  baseq3/vm/ui.qvm
d75941dc65e1c0006ac8ac5925af3291a4c1e7b6975a295e0f5cf86a7ee2aa66  ioquake3-config.json
a43ca343372f7f8683d46d137f3ebbfbd8f5879d71a84fbbd6a3ff907082bcb2  ioquake3.html
b7d4f0f2c9e3871359bfcab097787f988ea366e2ab8bf8c37211b91975866fb7  ioquake3.js
55108e97a43ce8a6140b0e912ff0246cb8fefd84d03b18cdae152aa4d0bf4802  ioquake3.wasm
a9963c8a60dd3a4a4ec9e278ef6b00fa40b4ab663e980f90baa48bdccf469949  missionpack/vm/cgame.qvm
cd615ce97dd65b2879158b540f0d331eb27fb52be9af7c8350dcc403955e3f68  missionpack/vm/qagame.qvm
80783e0cfe98e5ea0009863c02c56b3c022a7c74468b5218c4870ce178accec5  missionpack/vm/ui.qvm
```

WP1's own accepted builds, before the pin carried a patch, were run from
`7c50b6e3d35af601bfb21e67f9da976b44fc5bf3` and wrote
`sha256:c6665366ec489a8ba470caffa2faf91c52183a9d920628746bff36780dafab56`;
the WP0 amendment reissued that manifest as
`sha256:fbfdac8b0eb8b982b7f01d5ece11eaefa1ffaf9583d7095f872f814ffdb2b12b`
with every artifact digest unchanged. Those builds differed from the ten digests
above in `ioquake3.js` (`43c37ad7…`) and `ioquake3.wasm` (`14d6e241…`, 2,339,266
bytes) and in nothing else.

Six further builds run during development produced the same ten digests: one
with container network access before the offline port pre-fetch existed, one
offline, and two earlier accepted pairs — one whose CMake binary directory sat
a level deeper inside the container, one taken before the port tree was
re-staged from the verified archive on every build. The result therefore
depends on neither network availability during the build nor the container path
of the build directory, and re-staging the port tree changes nothing that is
compiled.

`producer.commit` names the commit whose orchestration produced the manifest,
so a rebuild from a later commit records its own. `scripts/verify-browser-build.sh`
compares a rebuild against `manifests/browser-client.json` on that basis: every
field except `producer` must agree exactly, and a differing producing commit is
reported rather than treated as a mismatch.

### Toolchain identity in the log

Each build writes `build/<name>/tree/toolchain.txt`:

```text
emscripten-version: 6.0.8
emcc: emcc (Emscripten gcc/clang-like replacement + linker emulating GNU ld) 6.0.8 (aeb67926e7de656da38bc807d83050af93578758)
cmake: cmake version 3.28.3
host-c-compiler: cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
node: v24.19.0
source-date-epoch: 1784478090
em-ports: /ports
```

The Emscripten version matches the baseline exactly. The host C compiler is
the one inside the pinned builder image; it builds only the QVM tools, never a
distributable artifact.

## Emscripten 6.0.8 compatibility audit

WP0 selected 6.0.8 over ioquake3 CI's 3.1.58 and made compatibility an
explicit WP1 gate covering three version-sensitive surfaces. All three work
unchanged; no fallback to 3.1.58 was needed or taken.

| Surface | Upstream setting | Observed on 6.0.8 |
| --- | --- | --- |
| SDL port | `-sUSE_SDL=2` as both a compile and a link option | Resolved to the SDK's SDL2 port 2.32.10, built as `libSDL2.a` and linked. No source change and no additional flag. |
| Filesystem export | `-sEXPORTED_RUNTIME_METHODS=FS,addRunDependency,removeRunDependency` | `Module["FS"]`, `addRunDependency` and `removeRunDependency` are all present in the emitted JavaScript, which is what the upstream shell's preload hook uses. |
| ES module output | `-sEXPORT_ES6`, `-sEXPORT_NAME=ioquake3` | The emitted `ioquake3.js` is a real ES module: it uses `import.meta.url` and ends in `export default ioquake3;`. |

The SDL2 port also appends `createContext` to `EXPORTED_RUNTIME_METHODS` and
sets `GL_ENABLE_GET_PROC_ADDRESS`, which is why the WebGL library variant in
the link closure is `libGL-webgl2-getprocaddr`.

One diagnostic appears during configuration and is not a 6.0.8 regression:
`The ASM compiler identification is unknown` / `Did not find file
Compiler/-ASM`. ioquake3 enables the `ASM` language for GNU-style compilers and
compiles `code/asm/ftola.c` and `code/asm/snapvector.c`, which are C files.
Both compile and link.

## Component mapping

Every `engine.licenseComponents` record in the WP0 lock, against what the
accepted build actually did with it. Object counts are from the client link
line; include counts are from the client compile flags.

The client link contains 382 objects: 352 from `code/` and 30 generated
GLSL-stringify units. 130 of the `code/` objects are `ioq3-core`.

| Component | WP0 `sourceRole` | Observed result |
| --- | --- | --- |
| `ioq3-core` | `engine-core` | **Compiled and linked.** 130 objects, plus the 30 generated shader units. It contributes no `-I` of its own; all twelve client include directories belong to the bundled third-party components below. |
| `ijg` | `browser-build-source` | **Compiled and linked.** 46 objects, 1 include directory. |
| `opus` | `browser-build-source` | **Compiled and linked.** 134 objects, 5 include directories. |
| `libvorbis` | `browser-build-source` | **Compiled and linked.** 21 objects, 2 include directories. |
| `opusfile` | `browser-build-source` | **Compiled and linked.** 6 objects, 2 include directories. |
| `zlib` | `browser-build-source` | **Compiled and linked.** 6 objects, 1 include directory. |
| `libogg` | `browser-build-source` | **Compiled and linked.** 2 objects, 1 include directory. |
| `minizip` | `browser-build-source` | **Compiled and linked.** `unzip.c` and `ioapi.c`, both with warnings disabled. |
| `mumble-link` | `browser-build-source` | **Compiled and linked.** `USE_MUMBLE` is on by default and the Emscripten platform file does not disable it, so `libmumblelink.c` is in the browser artifact. |
| `snd-adpcm` | `browser-build-source` | **Compiled and linked.** |
| `puff` | `browser-build-source` | **Compiled and linked.** |
| `public-domain-md5` | `browser-build-source` | **Compiled and linked.** |
| `public-domain-updater` | `feature-disabled-source` | **Confirmed.** `sys_autoupdater.c` is compiled and linked, but `USE_AUTOUPDATER` is not defined, so its implementation is excluded by its own guard. |
| `qvm-libc` | `qvm-build-source` | **Confirmed.** `bg_lib.c` is absent from the client link and present in the QVM translation units. |
| `curl-headers` | `emscripten-disabled-source` | **Confirmed excluded.** The Emscripten platform file sets `USE_HTTP` off, so `libraries/curl.cmake` returns before adding anything. No object, no include directory, no `USE_HTTP` define. |
| `sdl-prebuilt-libraries` | `native-only-prebuilt` | **Confirmed excluded.** `code/thirdparty/libs` is reachable only on Windows and macOS. |
| `sdl-headers` | `emscripten-disabled-source` | **Corrected.** Not merely disabled: for Emscripten, `libraries/sdl.cmake` returns after adding `-sUSE_SDL=2` and never references `code/thirdparty/SDL2-2.32.8`. The SDL2 in the artifact is the SDK's port at 2.32.10, not this 2.32.8 header snapshot. |
| `openal-headers` | `browser-interface-header` | **Corrected.** The snapshot is not used at all — not even as headers. Emscripten ships its own `cmake/Modules/FindOpenAL.cmake`, which sets `OpenAL_FOUND`, points `OPENAL_INCLUDE_DIR` at the Emscripten sysroot and returns `-lopenal`. `USE_INTERNAL_OPENAL_HEADERS` is therefore not defined and no `-I` for `openal-soft-1.24.3` appears. The OpenAL interface headers and implementation both come from the SDK. |
| `lcc-build-tool` | `qvm-build-tool` | **Confirmed build-tool only.** See below. |

Both corrections are recorded here rather than in the lock: WP0's inventory
describes the pinned *source tree*, which still contains those snapshots, and a
component absent from the final link may be reported as absent but not erased
from the source inventory. A future baseline reissue may narrow the two
provisional `sourceRole` values to match this evidence.

## QVM generation and the lcc boundary

QVM generation does execute the restrictively licensed lcc build tool, and it
does so entirely on the build host side.

- The Emscripten platform file sets `BUILD_GAME_LIBRARIES` off and leaves
  `BUILD_GAME_QVMS` on. The prior evidence is confirmed: no separate,
  separately pinned host-tools phase was introduced.
- `cmake/utils/qvm_tools.cmake` adds an `ExternalProject` that configures
  `cmake/tools` as its own project. CMake does not forward the Emscripten
  toolchain file to it, so the sub-build detects the image's own native
  compiler — `The C compiler identification is GNU 13.3.0`, `/usr/bin/cc` —
  and produces native `q3lcc`, `q3rcc`, `q3cpp`, `lburg` and `q3asm`
  executables in the CMake binary tree's `tools/Release/` directory.
- The accepted build ran 170 `q3lcc` translation steps and 6 `q3asm` links,
  producing the three `baseq3` and three `missionpack` QVMs.
- No lcc source or executable reaches a distributable artifact. The
  distributable `Release` directory contains exactly the ten files listed
  above; the tools live in the sibling `tools` directory, and
  `scripts/build-browser-in-container.sh` fails the build if `q3lcc`, `q3rcc`,
  `q3cpp`, `lburg` or `q3asm` ever appears under `Release`. The first four are
  the legal boundary — they are built from `code/tools/lcc` and carry its
  restrictive 1998 terms. `q3asm` is ioquake3's own GPL code and carries no
  such restriction; it is in the guard as build-output hygiene, so that the
  check covers every tool the QVM phase produces rather than only the
  restricted ones.

The intended distribution therefore does not exceed WP0's boundary for lcc.
`LicenseRef-LCC-1998` remains a build-tool-only registration whose source stays
visible through the public ioq3 submodule, and no release-policy review is
needed at this point. The boundary must be re-checked if a later WP ever
proposes shipping a build container or an SDK-style artifact rather than the
engine outputs alone.

## Linked license closure and notices

The browser artifact is a combined work. It is not distributable under one
blanket license, and it contains runtime code that is not in the ioq3 checkout
at all.

### From the pinned ioq3 checkout

GPL-2.0-or-later engine and QVM code (`ioq3-core`, and `qvm-libc` in the QVMs),
linked together with these separately licensed components, all of which are on
WP0's product-input allowlist:

IJG (`jpeg-9f`), BSD-3-Clause (`libogg`, `libvorbis`, `opus`, `opusfile`),
Zlib (`zlib`, `puff`, `mumble-link`), `Zlib AND Info-ZIP` (`minizip`), HPND
(`snd-adpcm`), and the two reviewed public-domain declarations
(`public-domain-md5`, and `public-domain-updater` whose implementation is
guarded out). The QVM `bg_lib.c` adds
`BSD-3-Clause AND LicenseRef-Patrick-Powell-Snprintf`.

All of these are permissive and GPL-compatible, so the combined browser client
is distributable under GPL-2.0-or-later with every component's own notice
preserved. `curl-headers`, `sdl-headers`, `sdl-prebuilt-libraries` and
`openal-headers` are absent from the artifact and need no shipped notice for
it.

### Supplied by the Emscripten SDK, not by ioq3

The observed link closure adds, beyond the ioq3 objects:

```text
-lGL-webgl2-getprocaddr  -lal  -lc  -lclang_rt.builtins
-ldlmalloc  -lhtml5  -lsockets  -lstubs
.../sysroot/lib/wasm32-emscripten/libSDL2.a
```

plus the JavaScript runtime that emcc emits into `ioquake3.js`.

| Supplied component | License | Notice evidence in the pinned image |
| --- | --- | --- |
| Emscripten JS runtime, `libal`, `libGL`, `libhtml5`, `libsockets`, `libstubs`, vendored `dlmalloc` | MIT **or** University of Illinois/NCSA (dual, at the recipient's choice) | `/emsdk/upstream/emscripten/LICENSE`, `sha256:620a78084fc7ca97c0b5dea9abf891f3ffcadfdbf305276f099c9c4e12fc1d86` |
| `libc` (musl-derived) | MIT | `/emsdk/upstream/emscripten/system/lib/libc/musl/COPYRIGHT`, `sha256:b870108ec5e7790e9f9919064f1b9421d62d5f9b0e6c230c6adf7ea2da62e97b` |
| `libclang_rt.builtins` (LLVM compiler-rt) | Apache-2.0 WITH LLVM-exception | `/emsdk/upstream/emscripten/system/lib/compiler-rt/LICENSE.TXT`, `sha256:1a8f1058753f1ba890de984e48f0242a3a5c29a6a8f2ed9fd813f36985387e8d` |
| `libSDL2.a` (SDK port, SDL 2.32.10) | Zlib | `SDL-release-2.32.10/LICENSE.txt` in the pinned port archive, `sha256:97f35b302b361680ec1e891e95d2d52097bb95abff361434916d99dc1305f127` |

Apache-2.0 WITH LLVM-exception is GPLv2-incompatible in general, but the LLVM
exception exists precisely so compiler runtime libraries may be linked into
works under other licenses without imposing Apache-2.0 terms on them; the
combination is the ordinary, intended one for a compiler-provided builtins
library.

### The notice set a browser distribution must ship

Every entry below resolves to a file with a fixed identity. No provisional
source-role claim remains unresolved.

| Notice | Identity |
| --- | --- |
| ioquake3 `COPYING.txt` (GPL-2.0) | `sha256:fac9da110d1433f4df0cb9f5dda9449e9aff6ee236ed240fa29e3e92926c363a` at the pinned commit |
| Per-component notices inside the ioq3 tree (Xiph, Opus, zlib, minizip, Mumble Link, ADPCM, MD5, puff, `bg_lib.c`) | the pinned commit `92351b8f0543448b9defaac25c552274eecbf15b`, where each is byte-identical to the upstream base |
| IJG terms | `jpeg-9f/README` in `sha256:04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b`, as pinned by WP0 |
| Emscripten LICENSE | `sha256:620a78084fc7ca97c0b5dea9abf891f3ffcadfdbf305276f099c9c4e12fc1d86` |
| musl COPYRIGHT | `sha256:b870108ec5e7790e9f9919064f1b9421d62d5f9b0e6c230c6adf7ea2da62e97b` |
| LLVM compiler-rt LICENSE.TXT | `sha256:1a8f1058753f1ba890de984e48f0242a3a5c29a6a8f2ed9fd813f36985387e8d` |
| SDL 2.32.10 LICENSE.txt | `sha256:97f35b302b361680ec1e891e95d2d52097bb95abff361434916d99dc1305f127` |

### Corresponding source

The GPL corresponding source for a distributed browser client is: the pinned
public ioq3 commit, which is the fork commit carrying the enumerated series and
not the upstream base — the built source is the patched tree, and the fork is
public for exactly that reason; this repository's build orchestration at the
commit named in the manifest's `producer`; the pinned Emscripten SDK, whose
preferred source the baseline records as emsdk commit
`e5bd3d0874e302a18f13c5b41f5bacf9a40c8e59`; and the SDL2 port archive named
above. All four are public and immutably identified.

Assembling these notices into a shipped bundle belongs to the WP that first
distributes a client. WP1 fixes what the bundle must contain.

## Cross-origin isolation

**The client does not require cross-origin isolation.** WP0's expectation is
confirmed.

The link configuration is non-threaded: no `-pthread`, no `-sPTHREAD_POOL_SIZE`
and no `-sSHARED_MEMORY` anywhere in the compile or link options. The emitted
`ioquake3.js` contains zero occurrences of `SharedArrayBuffer`, `Atomics`,
`pthread`, `PThread` or `__emscripten_thread`, and its single `wasmMemory`
reference is an ordinary non-shared memory.

A page hosting this client therefore needs no COOP/COEP headers. That is a
property of this link configuration, not a promise: enabling threads later
would reinstate the requirement, and the WP that owns the product loader should
re-check it against the artifact it actually ships.

## The upstream shell and retail-data configuration

`ioquake3.html` and `ioquake3-config.json` are build evidence and are recorded
in the manifest as such. They are not the product loader.

`ioquake3-config.json` is the upstream retail-data configuration: it lists
`baseq3/pak0.pk3` through `pak8.pk3`, the missionpack and demo paks, and the
QVM paths. Those are filename references generated from ioquake3's committed
template. No proprietary Quake III data was read, downloaded or emitted by any
accepted build; the manifest generator fails closed on any `.pk3` file in a
build output, and none is produced.

The upstream shell also hardcodes `+set net_enabled 0`, which is why the
network sources compile but no multiplayer path is exercised here.

## Amendment of 2026-08-30: the pin carries a patch series

WP1 closed on a contract that is written into its own title: a **reproducible
unmodified ioq3 browser build**. That contract is deliberately amended here. It
did not survive contact with WP4's witnessed round, which found that the browser
client renders a third of the lightmapped world white — and the cause turned out
to be in the engine, where this repository could not reach it.

### What the defect was

`GLSL_GetShaderHeader` (`code/renderergl2/tr_glsl.c`) emitted
`precision mediump float;` for every GLSL ES shader stage. GLSL ES has no
default float precision in the fragment language, so a default must be emitted;
`mediump` is the wrong one for this renderer. It is only required to cover
±2^15 with 10 bits of mantissa, and `lightall_fp.glsl` does world-unit
arithmetic per fragment:

```text
viewDir = u_ViewOrigin - position     (interpolated, world units)
E       = normalize(viewDir)
```

On a map whose coordinates run into the thousands, `dot(viewDir, viewDir)`
overflows to infinity, `normalize()` returns the zero vector, `E` and
`EH = dot(E, H)` become 0, and `CalcSpecular()`'s
`v = (EH * EH) * (roughness + 0.5) + EPSILON` collapses onto `EPSILON` — which
is `1e-8` and itself underflows to zero at `mediump`. The division by it is
unbounded and the surface is drawn saturated white. Desktop GL parses precision
qualifiers and ignores them, which is why the identical shader source is correct
natively and only a GLES driver that honours them is affected.

A WebGL probe on the same driver (Chrome 152 / ANGLE over Mesa radeonsi)
confirms the mechanism directly rather than by inference: at `mediump`,
`normalize(vec3(1200.0))` returns the zero vector and `1.0e-8 > 0.0` is false,
while both behave at `highp`.

### The patch, and what it is not

One patch, `renderergl2-glsl-es-highp`, touching `code/renderergl2/tr_glsl.c`
and nothing else. It adds `GLSL_AddFloatPrecision()`: `highp` unconditionally
for the vertex stage — which is what GLSL ES already defaults to, so the old
code was a downgrade there — and a `GL_FRAGMENT_PRECISION_HIGH`-guarded `highp`
for the fragment stage, because `highp` is mandatory in GLSL ES 3.00 but
optional in 1.00 and the previous `mediump` behaviour is retained where it is
unavailable.

It is written to be upstream-mergeable: it changes no arena-web-specific
behaviour, carries its reasoning in its commit message as upstream rationale,
and would be correct for any GLES target. An upstream submission is an
**optional later step and is deliberately not scheduled** — arena-web builds on
Emscripten 6.0.8 while ioquake3's reference toolchain is 3.1.58, and validating
against that older toolchain is not work this prototype wants to take on. The
lock records the fact rather than the intention: `upstreamStatus` is
`not-submitted`.

### The contract, restated

The pin is **not** "unmodified ioq3" any more, and it is **not** "a fork we
change as we like" either. It is:

> the exact upstream base commit the lock names, plus the exact patches the lock
> enumerates, and nothing else.

The fork is `https://github.com/Kaesual/ioq3.git`; its `main` mirrors upstream
and holds the base, `web` holds the series. The pin is
`92351b8f0543448b9defaac25c552274eecbf15b`; the base is
`588393618dbc82e7207c21c6ddecca229944a03a`, which is upstream `main`'s head. The
enumeration lives in `engine.appliedPatches` and the record type, its presence
rule and the offline check that binds it to the real submodule diff are
described in
[`immutable-baseline.md`](immutable-baseline.md#the-engine-pin-is-a-fork-commit-and-what-that-obliges-the-lock-to-say).
The practical consequence for this document is that "unmodified" is now a claim
the lock makes and a validator checks, rather than a sentence in a report.

### The embedded product date follows the base, not the pin

`SOURCE_DATE_EPOCH` used to be the pinned engine commit's committer timestamp.
Moving the pin moved it, and the first rebuild showed what that costs:
`PRODUCT_DATE` is compiled into `code/game/g_main.c` and `code/qcommon/common.c`,
so both `qagame` QVMs and the dedicated server binary changed too — at identical
sizes, for a renderer patch that touches neither.

The epoch is therefore taken from `engine.upstreamBase.commit`, through a
`scripts/baseline-inputs.py` field rather than a literal repeated in a script.
`PRODUCT_DATE` is ioquake3's own product version string and this fork's patch
series does not make the engine a newer ioquake3 release; what identifies an
accepted build is the commits and digests the lock and the manifests carry. The
practical gain is that the difference between two accepted builds now equals the
difference between their sources.

### What the rebuild actually moved

Three clean builds, digests above. Against the pre-patch manifest, exactly two
artifacts differ:

| Artifact | Before | After |
| --- | --- | --- |
| `ioquake3.wasm` | `14d6e241…`, 2,339,266 bytes | `55108e97…`, 2,339,387 bytes (+121) |
| `ioquake3.js` | `43c37ad7…` | `b7d4f0f2…`, same 266,707 bytes |

Everything else — all six QVMs, `ioquake3.html`, `ioquake3-config.json` — is
byte-identical. `ioquake3.js` moves because the Emscripten glue embeds
memory-layout constants that follow the grown data section, not because the
patch reaches JavaScript.

### Scope proof: the dedicated server is untouched

The patch is renderer-only, and the dedicated server compiles no renderer, so
the WP5 artifact had to come out byte-identical. It did.
`scripts/verify-native-build.sh --target server` ran two clean server builds and
two image builds:

| Evidence | Result |
| --- | --- |
| `ioq3ded`, both builds | `sha256:dbb194f26ec8870e004da56acc11d5caa449dd2a2afd829be957f534cef499d2`, 798,456 bytes — the digest WP5 recorded |
| Server-image content set | all four entries unchanged, including `arena/vm/qagame.qvm` `449fbd19…` |
| Two image builds | the same image id |
| Regenerated manifest vs. `provenance/arena-web-server.json` | agrees, `producer` aside |

The image **id** does change, to
`27a307166f2fad40c73a8a4df2c59e5a1f9db13584383296a84ec5306f42dfc2`, and the
image was rebuilt for that reason: `native/server.Containerfile` stamps the
engine commit, the baseline identity and the producing commit into
`com.kaesual.arena-web.*` labels, so an unrebuilt image would keep asserting the
superseded baseline while the provenance record asserted the new one. The
distributed bytes under `/opt/arena-web` are the same bytes; only the labels
moved. The WP5 evidence documents are records of the rounds they describe and
are left exactly as written.

### Validation of the fix

The investigation that produced the patch measured, in the pinned browser:
the near-white fraction of an in-game frame dropping from about 33% to about
1.3%; 0 of 14 instrumented harness runs defective where the base rate had been
roughly two in three; all 115 GLSL programs still compiling; and both WebGL 1
and WebGL 2 fixed.

After the rebuild, with the `r_vertexLight` workaround removed from
`arena/game-profile.json`, `scripts/run-arena-acceptance.sh` was run three
times — six sessions, eighteen in-game screenshots — and every check passed in
every round. Near-white fractions per round, in capture order:

| Round | In-game near-white fractions |
| --- | --- |
| 1 | 0.0092, 0.0065, 0.0036, 0.0064, 0.0052, 0.0059 |
| 2 | 0.0064, 0.0252, 0.0024, 0.0050, 0.0048, 0.0054 |
| 3 | 0.0081, 0.0063, 0.0069, 0.0069, 0.0023, 0.0022 |

The `canvas-no-white-surface-regression` gate is unchanged at 5%, and it now
guards the real fix rather than a workaround. The one 2.5% frame is a view with
genuinely bright surfaces in it, an order of magnitude below the gate and two
below the 15–52% the defect produced.

## What this does not prove

- Nothing is playable. No free content has been selected yet, so the client
  has no game data. WP1 has no runtime acceptance by design.
- No browser has run the artifact. Real-browser rendering, input and audio
  acceptance belong to WP4.
- The `missionpack` QVMs are built because upstream builds them. They are not
  a product decision; product packaging allowlists what it consumes.
