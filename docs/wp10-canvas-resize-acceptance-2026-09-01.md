<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP10 canvas-resize acceptance — 2026-09-01

**Status:** Complete and accepted.

WP10 closes the known WP4 limitation that a running client retained its
startup resolution after an ordinary browser resize or after entering HTML
fullscreen. It is a deliberately small browser-runtime follow-up. The relay,
native server, packet sizing, content and gameplay contracts are unchanged.

## Accepted implementation

The implementation identity is
`git:1d0f032ad294804275553c1e33ca306ce2baf7b7`. The ioq3 pin remains
`git:968eeb44294aa0003c430430cf32a6540f9a81e4`; no engine rebuild or artifact
manifest reissue was necessary.

The browser loader now observes changes to the canvas CSS box and forwards a
browser resize event into SDL's existing Emscripten window-event path. ioq3's
existing delayed resize handling then adopts the new custom resolution. Both
checked browser profiles set `r_fullscreen=0`: the HTML stage, rather than the
engine, owns fullscreen, so the engine must continue to treat its canvas as a
resizable window. The stage has a 320 by 240 CSS-pixel floor, matching the
loader's existing startup-resolution floor.

The diagnostic report preserves the startup dimensions and separately exposes
the current dimensions, observer availability and resize-event count. These
fields are acceptance diagnostics, not a new product API. The staged release
tree now contains 16 files because `arena/canvas-resize.js` is a served runtime
module.

## Automated evidence

- `scripts/check.sh` passed all 793 tests, including 16 deterministic
  JavaScript resize-bridge checks and the Python staging, profile and browser
  acceptance contracts.
- The exact pinned Google Chrome for Testing `152.0.7977.64` acceptance run
  passed. It observed and adopted `1280x577 -> 1120x487`, then restored and
  adopted `1280x577`; the engine log contained the matching `MODE` markers.
- The existing artifact, engine-argument, map, bot, input, focus, rendering,
  console and runtime-error gates remained green. The runtime tree passed the
  strict 16-file staging check.

The automated resize is part of the existing cheap offline browser acceptance;
it does not repeat WP7/WP8 relay, reconnect, packet-census or endurance work.

## Witnessed KDE/Wayland check

The operator used the exact pinned Chrome for Testing `152.0.7977.64` on the
accepted Fedora 44 KDE/Wayland platform variation. After the arena started,
the window was resized smaller and larger and HTML fullscreen was entered and
left. The view followed the available size and gameplay and input remained
healthy throughout. The operator reported: **“alles okay”.**

## Review and closure

A focused review covered the complete runtime, profile, staging, acceptance
and documentation diff. It found no open defect. WP10 changes no ioq3 source,
network protocol, native-server file or generated engine/content artifact.
Consumers must nevertheless publish the changed 16-file browser tree under a
new immutable release root, as required by the integration contract.
