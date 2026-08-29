<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# arena-web

`arena-web` is the public integration repository for a small, independently
named Quake III-style arena game that runs directly in the browser. The project
uses the maintained
[ioquake3](https://github.com/ioquake/ioq3) Emscripten target, GPL game code,
content selected only after license verification, and a WebTransport-to-UDP
relay for multiplayer networking.

The immediate goal is a deliberately small vertical slice: one reproducible
browser build, one native dedicated server, a minimal free content set and two
browser clients completing a real multiplayer session. It is not yet a game
release.

## Repository layout

- `ioq3/` pins the [Kaesual ioquake3 fork](https://github.com/Kaesual/ioq3).
  Engine and platform changes belong in that fork rather than as a permanent
  patch stack here.
- `docs/initial-plan.md` records the independently reviewed feasibility
  findings, decisions, risks and prototype sequence.
- `docs/prototype-work-packages.md` contains the approved, dependency-ordered
  prototype increments and evidence gates.
- `docs/immutable-baseline.md` records WP0's exact toolchain, browser,
  acceptance-platform, metadata and relay-trust contracts.
- `docs/wp1-build-evidence.md` records the accepted browser build: its inputs,
  determinism controls, artifact identities, license closure and findings.
- `manifests/browser-client.json` is the artifact manifest of that build.
- `LICENSE` applies GPL-2.0-or-later to original arena-web code and
  documentation. Pinned components and content retain their own licenses.

The browser shell, content lockfiles, the public relay contract and browser
conformance probe, and the dedicated-server image will live in this repository
as later work packages add them. The shared relay server implementation and
environment-specific deployment remain outside this public repository. Large
upstream asset archives and generated build products should not be committed
here merely for convenience.

## Checkout

```bash
git clone --recurse-submodules https://github.com/Kaesual/arena-web.git
```

For an existing checkout:

```bash
git submodule update --init --recursive
```

The ioq3 pin is intentionally exact. Do not use `git submodule update --remote`:
the branch metadata will move from `main` to the product `web` branch when that
branch is created for the first engine change.

Committed metadata can be checked without building the engine or downloading
game content:

```bash
scripts/check.sh
```

The matching containerized check is `scripts/check-container.sh`.

## Building the browser client

The build runs entirely inside the Emscripten builder image pinned by
`locks/baseline.json`, which must already be present locally. Obtain its exact
reference with `scripts/build-browser.sh --print-image` and pull that digest
with your container runtime.

```bash
CONTAINER_RUNTIME=podman scripts/fetch-emscripten-ports.sh --fetch  # once, online
CONTAINER_RUNTIME=podman scripts/build-browser.sh                   # one clean build
CONTAINER_RUNTIME=podman scripts/verify-browser-build.sh            # two clean builds
```

`CONTAINER_RUNTIME` defaults to `docker`. The first command is the only one
that uses the network: ioquake3's Emscripten target links SDL2 through a port
whose source the SDK downloads, so that source is fetched once against the
identity the SDK itself pins and afterwards mounted read-only into offline
builds. Everything is written to the gitignored `build/` directory; the engine
artifacts are not committed, their identities are.

The build produces the engine runtime files and the `baseq3` and `missionpack`
QVMs. It is not yet playable: no game content has been selected. See
`docs/wp1-build-evidence.md`.

## Licensing

Original arena-web code and documentation are licensed under
GPL-2.0-or-later. The pinned ioquake3 core and game code retain their upstream
GPL terms, while its bundled third-party components retain their individual
licenses recorded in the immutable baseline. Maps, models, textures, audio and
other content likewise retain their respective licenses. Every shipped content
file will need machine-readable provenance. Proprietary Quake III game data is
not part of this project.
