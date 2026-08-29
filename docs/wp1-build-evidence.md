<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP1 evidence: reproducible upstream browser build

**Status:** WP1 complete — two clean builds accepted, byte-identical

This document records what an accepted browser build of the pinned, unmodified
ioquake3 Emscripten target actually is: its exact inputs, the commands that
repeat it, the environment controls that make it deterministic, the observed
license closure and the findings WP1 was required to confirm or correct.

The generated engine artifacts are not committed. Their identities are, in
[`manifests/browser-client.json`](../manifests/browser-client.json).

No engine or build-system change was needed. `ioq3/` is untouched at its pinned
commit, and no `web` branch was created.

## Inputs

| Role | Identity |
| --- | --- |
| Engine and bundled `baseq3` gamecode | ioq3 `588393618dbc82e7207c21c6ddecca229944a03a` |
| WebAssembly builder | `docker.io/emscripten/emsdk@sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475` (Emscripten 6.0.8, `linux/amd64`) |
| SDL2 source snapshot for `-sUSE_SDL=2` | `https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip` |
| Baseline the manifest binds to | `sha256:d565905280ac8575ad2798d4e1cd5cabb18c694a4b6b192957c48a41c416f039` |

The first two identities are read out of
[`locks/baseline.json`](../locks/baseline.json) at build time by
`scripts/baseline-inputs.py`; no build script restates a digest the lock
already owns. A renamed or digest-divergent substitute therefore fails before
a container starts, and again when the manifest is validated.

The SDL2 snapshot is not an arena-web choice. ioquake3's Emscripten target
compiles and links with `-sUSE_SDL=2`, and the pinned SDK implements that flag
as a port whose source it downloads on first use. Its identity is the identity
the pinned SDK itself pins:

```text
url:    https://github.com/libsdl-org/SDL/archive/release-2.32.10.zip
sha512: 001738b610b42a8f8badfd6af3402f0a1a8601034adef0b8c702dd2b1951dc1b71b733a6779d97499b6f7314d226ec0c8dcffeb753f35a5c51e995ca20bdd459
sha256: 7a3c207b8509edc487d658df357ad764cd852d68fe248d307b25c0741d52fdf0
```

`scripts/fetch-emscripten-ports.sh --fetch` reads `VERSION` and `HASH` out of
the pinned image's own `tools/ports/sdl2.py` and refuses to continue if they
disagree with the values above, so this pin cannot drift away from the
toolchain that consumes it. The SDK then verifies the download against that
SHA-512 itself.

## Reproducing the result

From a clean checkout, with the pinned builder image already present locally:

```bash
git submodule update --init --recursive
scripts/check.sh
CONTAINER_RUNTIME=podman scripts/fetch-emscripten-ports.sh --fetch   # once, online
CONTAINER_RUNTIME=podman scripts/verify-browser-build.sh             # two clean builds
```

`CONTAINER_RUNTIME` defaults to `docker`. `scripts/build-browser.sh` performs a
single build; `scripts/verify-browser-build.sh` performs the two clean builds
WP1 accepts and compares them. Build output lands under the gitignored
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
3. Deletes the build root and exports the pinned commit with
   `git archive` into `build/<name>/source`. The export carries no Git
   metadata, which pins the compiled content to the lock and keeps ioquake3's
   optional `git describe` product version out of the artifacts.
4. Runs `scripts/build-browser-in-container.sh` inside the pinned image with
   `--network none`, `--pull never`, `--platform linux/amd64`, `--cap-drop
   all`, `--security-opt no-new-privileges` and a non-root user whose ID
   matches the invoking user. Source is mounted read-only at `/src`, the
   pre-fetched port sources read-only at `/ports`, the output tree read-write
   at `/work`.
5. Inside the container, refuses any builder that does not report the
   baseline's Emscripten version, then runs the official upstream target
   unchanged:

   ```bash
   emcmake cmake -S /src -B /work/tree -DCMAKE_BUILD_TYPE=Release
   cmake --build /work/tree --parallel <jobs>
   ```

6. Refuses the result if a QVM build tool reached the distributable tree.
7. Emits the artifact manifest from `/work/tree/Release`, refusing any
   `.pk3` file, symlink or other non-regular file.

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

These are product orchestration only. No ioq3 source or build-system file was
patched.

| Control | Value | Why |
| --- | --- | --- |
| `SOURCE_DATE_EPOCH` | `1784478090` | CMake turns it into the compiled-in `PRODUCT_DATE`. Without it the engine embeds `__DATE__` and no two builds agree. The value is the pinned engine commit's own committer timestamp, so it is derived from the baseline, and the build fails if the checkout's commit timestamp is not exactly that. |
| Git metadata absent from `/src` | `git archive` export | Makes `PRODUCT_VERSION` exactly `1.36` instead of a `git describe` suffix whose abbreviation length is environment-dependent. |
| Fixed container paths | `/src`, `/ports`, `/work` | Build output cannot depend on where the checkout lives. |
| `LC_ALL=C`, `LANG=C`, `TZ=UTC` | fixed | Locale- and timezone-independent formatting. |
| Deleted build tree | every build | No stale object may survive into an accepted build. |

Observed effect: `-DPRODUCT_DATE="Jul 19 2026"` and `-DPRODUCT_VERSION="1.36"`
in the compile flags of both accepted builds, and `build/tree/version.txt`
containing `1.36`.

## Accepted builds

Two complete clean builds were run by `scripts/verify-browser-build.sh` from
arena-web commit `f40b3f6366653fc7bf61625ed19052000c7b4ce4`. They produced
identical artifact manifests and byte-identical artifacts.

Artifact manifest: `sha256:0f789d012697b59b04bbdfdb9dbc101e34afa68e85278d9fd28d8e29d40509cf`

```text
12d597a49bc351149d7459692a0311cc5e186cf4f376c703ddaa6cfa27a602e4  baseq3/vm/cgame.qvm
449fbd197d34ec3f51006438fc2bf961cdabdf38101b6a17d33f107ad4186805  baseq3/vm/qagame.qvm
23ba9181726e108be05a0096a9f49f3c7643d4ff8888267a6948c4a4e8389c33  baseq3/vm/ui.qvm
d75941dc65e1c0006ac8ac5925af3291a4c1e7b6975a295e0f5cf86a7ee2aa66  ioquake3-config.json
a43ca343372f7f8683d46d137f3ebbfbd8f5879d71a84fbbd6a3ff907082bcb2  ioquake3.html
43c37ad7b82e0d6dbd7f6913542fbf3c2e9bac55656953de064ecdc6d13d53e9  ioquake3.js
14d6e24174897e13ef4f35c9bee186da138d8d919cad6524e0d798d6c6b3ad8a  ioquake3.wasm
a9963c8a60dd3a4a4ec9e278ef6b00fa40b4ab663e980f90baa48bdccf469949  missionpack/vm/cgame.qvm
cd615ce97dd65b2879158b540f0d331eb27fb52be9af7c8350dcc403955e3f68  missionpack/vm/qagame.qvm
80783e0cfe98e5ea0009863c02c56b3c022a7c74468b5218c4870ce178accec5  missionpack/vm/ui.qvm
```

Two further builds run during development — one with container network access
before the offline port pre-fetch existed, one offline — produced the same ten
digests, so the result does not depend on network availability during the
build.

### Toolchain identity in the log

Each build writes `build/<name>/toolchain.txt`:

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
  executables in `build/<name>/tree/tools/Release/`.
- The accepted build ran 170 `q3lcc` translation steps and 6 `q3asm` links,
  producing the three `baseq3` and three `missionpack` QVMs.
- No lcc source or executable reaches a distributable artifact. The
  distributable directory `tree/Release` contains exactly the ten files listed
  above; the tools live in the sibling `tree/tools` directory, and
  `scripts/build-browser-in-container.sh` fails the build if a tool binary ever
  appears under `tree/Release`.

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
| Per-component notices inside the ioq3 tree (Xiph, Opus, zlib, minizip, Mumble Link, ADPCM, MD5, puff, `bg_lib.c`) | the pinned commit `588393618dbc82e7207c21c6ddecca229944a03a` |
| IJG terms | `jpeg-9f/README` in `sha256:04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b`, as pinned by WP0 |
| Emscripten LICENSE | `sha256:620a78084fc7ca97c0b5dea9abf891f3ffcadfdbf305276f099c9c4e12fc1d86` |
| musl COPYRIGHT | `sha256:b870108ec5e7790e9f9919064f1b9421d62d5f9b0e6c230c6adf7ea2da62e97b` |
| LLVM compiler-rt LICENSE.TXT | `sha256:1a8f1058753f1ba890de984e48f0242a3a5c29a6a8f2ed9fd813f36985387e8d` |
| SDL 2.32.10 LICENSE.txt | `sha256:97f35b302b361680ec1e891e95d2d52097bb95abff361434916d99dc1305f127` |

### Corresponding source

The GPL corresponding source for a distributed browser client is: the pinned
public ioq3 commit; this repository's build orchestration at the commit named
in the manifest's `producer`; the pinned Emscripten SDK, whose preferred source
the baseline records as emsdk commit
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

## What this does not prove

- Nothing is playable. No free content has been selected yet, so the client
  has no game data. WP1 has no runtime acceptance by design.
- No browser has run the artifact. Real-browser rendering, input and audio
  acceptance belong to WP4.
- The `missionpack` QVMs are built because upstream builds them. They are not
  a product decision; product packaging allowlists what it consumes.
