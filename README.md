# arena-web

`arena-web` is the public integration repository for a Quake III-style arena
game that runs directly in the browser. The project uses the maintained
[ioquake3](https://github.com/ioquake/ioq3) Emscripten target, freely
redistributable game code and content, and a WebTransport-to-UDP relay for
multiplayer networking.

The immediate goal is a deliberately small vertical slice: one reproducible
browser build, one native dedicated server, a minimal free content set and two
browser clients completing a real multiplayer session. It is not yet a game
release.

## Repository layout

- `ioq3/` pins the [Kaesual ioquake3 fork](https://github.com/Kaesual/ioq3).
  Engine and platform changes belong in that fork rather than as a permanent
  patch stack here.
- `docs/initial-plan.md` records the initial feasibility findings, decisions,
  risks and prototype sequence. It is a discussion draft; reviewed work
  packages still need to be derived from it.

Future product-owned build scripts, the browser shell, content lockfiles,
license manifests and the dedicated-server image will live in this repository.
Large upstream asset archives and generated build products should not be
committed here merely for convenience.

## Checkout

```bash
git clone --recurse-submodules git@github.com:Kaesual/arena-web.git
```

For an existing checkout:

```bash
git submodule update --init --recursive
```

No supported build command exists yet. The first implementation work package
will pin and reproduce the upstream Emscripten build before this README grows a
build procedure.

## Licensing status

The pinned ioquake3 source retains its upstream GPL terms. Game code, maps,
models, textures, audio and other content retain their respective licenses.
Before implementation begins, this repository needs an explicit license for
its original code and a machine-readable provenance policy for every shipped
content file. Proprietary Quake III game data is not part of this project.
