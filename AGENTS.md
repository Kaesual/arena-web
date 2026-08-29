<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# arena-web agent instructions

These instructions apply to this repository and supplement any more specific
`AGENTS.md` found below the path being changed.

## Purpose and visibility

This is a public, self-contained repository for the browser arena client,
reproducible content assembly and its matching dedicated-server artifact. Do
not refer to private repositories, private infrastructure, credentials,
internal hostnames or unpublished operational details in code or
documentation.

The project is in prototype state. `docs/initial-plan.md` records the current
evidence and decided prototype constraints; `docs/prototype-work-packages.md`
contains the approved delivery sequence. Do not silently expand those
constraints or turn later product ideas into prototype requirements.

## Submodule boundary

`ioq3/` is the Kaesual fork of upstream ioquake3. Keep engine, renderer,
Emscripten platform and low-level network-backend changes in that fork. Keep
the product build, browser shell, content inputs, content provenance and
server-image assembly in this repository.

Before changing `ioq3/`, check for instructions inside it and put the checkout
on the intended development branch rather than committing on a detached HEAD.
Commit and push an ioquake3 change in that repository before bumping its pin
here. A pin that exists only locally is not an acceptable project state.

Keep the fork's `main` branch suitable for tracking upstream. Product engine
changes belong on `web`; create and publish that branch before the first engine
change, then update this repository's submodule branch metadata and pin.
Until then, do not use `git submodule update --remote`; the recorded commit is
the checkout contract.

Avoid adding maps, asset packs or generated PK3 files to the engine fork. New
source repositories may be pinned here when a submodule is genuinely the best
contract; release archives should normally be identified by an explicit URL,
cryptographic digest and license/provenance record instead.

## Reproducibility and licensing

- Unless a file says otherwise, original arena-web code and documentation are
  GPL-2.0-or-later. Add SPDX identifiers to new source files where the file
  format permits them.
- Pin the Emscripten toolchain, source commits, content inputs and container
  bases exactly. A successful build must be reproducible from documented public
  inputs.
- Do not use proprietary Quake III data, assets with missing provenance, or
  non-commercial/no-derivatives material.
- Preserve the license of every component. Do not describe a mixed engine,
  QVM and asset distribution as if it had one blanket license.
- Generated browser packs must have a deterministic member manifest and a
  documented preferred source form. Keep source, transformation and output
  identities together.
- Do not bypass dependency, signature, checksum or license checks to make a
  build succeed.

## Architecture constraints

- The browser client uses WebTransport through a small game-neutral relay
  contract; browsers do not gain direct UDP access.
- Multiplayer targets a matching native dedicated server. Do not add
  browser-hosted or peer-to-peer game scope.
- This repository owns the public relay contract and browser conformance probe,
  not a second relay server implementation. Routed acceptance uses a compatible
  shared relay endpoint supplied by the integration environment.
- Keep ioquake3's game datagrams intact at the engine boundary. Do not implement
  tunnel fragmentation before packet and browser-path measurements require it.
  Any such fragmentation must be bounded, authenticated, loss-tolerant and
  transparent to the game protocol.
- The first supported target is the pinned browser client against its pinned
  native server. Compatibility with arbitrary public Quake III/OpenArena
  servers is not an implicit requirement.
- The first game profile is one freely redistributable map, FFA, offline bots
  and a two-player multiplayer acceptance. Additional modes are later scope.
- The first browser gate is one exact Chromium version string on one exact
  desktop OS version, recorded by the owning work package. Other browsers,
  mobile and touch are later scope.
- Treat input, audio activation, frame timing, packet sizes, content load time
  and real-browser rendering as acceptance behavior, not incidental polish.

## Changes and verification

Keep changes small enough to review. Before closing an implementation work
package, inspect the full diff, run the narrowest relevant automated checks and
exercise material browser behavior in a real browser. A headless renderer alone
is not sufficient evidence for gameplay rendering or input.

Do not commit build outputs unless a later documented distribution contract
explicitly requires a particular artifact to be versioned.
