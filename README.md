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
  prototype increments and evidence gates. WP0 is next.
- `LICENSE` applies GPL-2.0-or-later to original arena-web code and
  documentation. Pinned components and content retain their own licenses.

Future product-owned build scripts, the browser shell, content lockfiles,
license manifests, the public relay contract and browser conformance probe, and
the dedicated-server image will live in this repository. The shared relay
server implementation and environment-specific deployment remain outside this
public repository. Large upstream asset archives and generated build products
should not be committed here merely for convenience.

## Checkout

```bash
git clone --recurse-submodules git@github.com:Kaesual/arena-web.git
```

For an existing checkout:

```bash
git submodule update --init --recursive
```

The ioq3 pin is intentionally exact. Do not use `git submodule update --remote`:
the branch metadata will move from `main` to the product `web` branch when that
branch is created for the first engine change.

No supported build command exists yet. The first implementation work package
will pin and reproduce the upstream Emscripten build before this README grows a
build procedure.

## Licensing

Original arena-web code and documentation are licensed under
GPL-2.0-or-later. The pinned ioquake3 source retains its upstream GPL terms;
game code, maps, models, textures, audio and other content retain their
respective licenses. Every shipped content file will need machine-readable
provenance. Proprietary Quake III game data is not part of this project.
