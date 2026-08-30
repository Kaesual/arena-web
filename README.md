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
- `docs/wp3-content-closure.md` records the accepted content pack: how its
  members were selected, the per-file license verification, the static closure
  result, the determinism proof and the license report.
- `content/pack-recipe.json` is the committed content selection: the pinned
  upstream archives, the game profile and every accepted exception.
- `provenance/` holds the member-level provenance of the assembled pack and the
  pack's own identity.
- `arena/` is the product browser loader, its engine configuration and the
  content configuration of the offline one-map slice;
  `docs/wp4-vertical-slice.md` records how the engine is booted, what the
  served set is allowed to contain and what the automated run observed.
- `native/` is the dedicated server and its native test client: the container
  definitions, the server's own engine configuration and the declarative profile
  both sides derive their command line from.
  `provenance/arena-web-server.json` is the artifact manifest of the server
  image's content, `locks/native-toolchain-packages.conf` pins the packages the
  native toolchain installs, and `docs/wp5-packet-census.md` records the build,
  the image and the packet census.
- `records/` holds the machine-readable measurement records the work packages
  produce, starting with the WP5 packet census.
- `docs/relay-datagram-contract.md` is the public routed datagram contract: the
  protocol subset and byte-exact framing a browser needs to reach one game
  destination through a compatible relay.
- `probe/` is the standalone browser conformance probe for that contract, and
  `docs/wp2-relay-probe.md` records what it implements and what its routed
  acceptance still needs.
- `LICENSE` applies GPL-2.0-or-later to original arena-web code and
  documentation. Pinned components and content retain their own licenses.

The browser shell and the dedicated-server image will live in this repository as
later work packages add them. The shared relay server implementation and
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
QVMs. On its own it is not playable; the game content is assembled separately.
See `docs/wp1-build-evidence.md`.

## Building the content pack

The pack is the dependency closure of what the pinned `baseq3` QVM sources
reference, for one map, one player presentation and offline bots. It is
assembled from license-verified upstream archives that are pinned by exact
size and SHA-256 in `content/pack-recipe.json`.

```bash
scripts/fetch-content-sources.sh          # once, online; about 900 MB
scripts/build-content-pack.sh             # one clean assembly
scripts/verify-content-pack.sh            # two clean assemblies, compared
```

Only the first command uses the network, and it accepts an archive only if its
size and digest match the recipe. Everything is written to the gitignored
`build/` directory; the PK3 is not committed, its member-level provenance and
identity are. See `docs/wp3-content-closure.md`.

## Running the offline arena slice

The slice is one map, free-for-all, with three bots and no networking. It needs
the browser build and the content pack from the two steps above.

```bash
scripts/serve-arena.sh          # then open http://127.0.0.1:8174/
```

The script does not serve this repository. It assembles a separate tree
containing the loader, the committed content configuration, the two committed
manifests and exactly the artifacts those manifests declare — each copied only
after its SHA-256 and size match the committed identity — and then re-reads the
tree and refuses to serve if a file is extra, missing or modified. The loader
verifies the same identities again in the browser and executes the engine from
the verified bytes. Press Start to enter the map; the click is also what gives
the page audio activation.

The client needs no cross-origin isolation, so the server sets no COOP or COEP
header.

The same slice can be driven automatically in the pinned acceptance browser.
Obtain the exact Chrome for Testing archive `docs/immutable-baseline.md` pins,
unpack it, and run:

```bash
ARENA_CHROME=/path/to/chrome-linux64/chrome scripts/run-arena-acceptance.sh
```

That stages, serves and launches twice, and writes logs, screenshots and a
machine-readable summary to the gitignored `build/arena-acceptance/`. It is
pre-acceptance evidence only: a person at the acceptance desktop still has to
play the slice, and `docs/wp4-vertical-slice.md` carries that checklist.

## Building the native server and taking the packet census

The dedicated server matches the browser client: same engine commit, same
audited content pack, same accepted QVMs. It is built in a toolchain image that
is the WP0 native builder base plus exactly the packages
`locks/native-toolchain-packages.conf` pins, and the distributed image starts
from the separately pinned Debian runtime base.

```bash
CONTAINER_RUNTIME=podman scripts/fetch-native-packages.sh    # once, online
CONTAINER_RUNTIME=podman scripts/build-native-toolchain.sh   # offline
CONTAINER_RUNTIME=podman scripts/build-native.sh --target server
CONTAINER_RUNTIME=podman scripts/build-native.sh --target client
CONTAINER_RUNTIME=podman scripts/build-server-image.sh
CONTAINER_RUNTIME=podman scripts/run-packet-census.sh
```

Only the first command uses the network, and it accepts a package only if its
byte length and SHA-256 match the lock. `scripts/verify-native-build.sh` runs two
clean builds of either target and compares them.

The census starts the server and the native client on a private container
network, drives a full session through the client's own console, captures the
traffic on the server's interface filtered to its own UDP port, and writes a
machine-readable census. See `docs/wp5-packet-census.md`.

## Running the relay conformance probe

The probe measures the usable routed datagram path between a browser and one
game destination behind a compatible WebTransport-to-UDP relay. It is
game-neutral and this repository contains no relay server.

```bash
scripts/serve-probe.sh          # then open http://127.0.0.1:8173/probe/
```

The page first runs the committed conformance vectors and a complete in-memory
session through its own implementation of
`docs/relay-datagram-contract.md`, and refuses to open a network session if
anything disagrees. The endpoint, trust input, short-lived authorization and
routing prefix are typed in at runtime; none of them is committed here, and the
measurement report has no field for them. Without those values the probe runs
its self-test and stops, which is the intended state until an integration
environment supplies them.

The deterministic half of the contract needs no browser and runs in
`scripts/check.sh`.

## Licensing

Original arena-web code and documentation are licensed under
GPL-2.0-or-later. The pinned ioquake3 core and game code retain their upstream
GPL terms, while its bundled third-party components retain their individual
licenses recorded in the immutable baseline. Maps, models, textures, audio and
other content likewise retain their respective licenses; every member of the
assembled content pack has machine-readable provenance under `provenance/`, and
every member of the current pack is GPL-2.0-or-later. Proprietary Quake III
game data is not part of this project.
