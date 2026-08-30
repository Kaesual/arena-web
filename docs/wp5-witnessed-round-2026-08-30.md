<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP5 witnessed round — 2026-08-30

**Result:** completed, with one documented local-network variation

This report records the manual round requested by the **Witnessed round —
PENDING** section of [`wp5-packet-census.md`](wp5-packet-census.md). It covers
the matching native client and dedicated server only. The packet census was not
rerun and no browser, WebTransport or relay behavior was exercised.

## Preconditions

- The repository was clean before the round.
- The already-built native client and both renderer libraries were present in
  `build/native-client/tree/Release/`.
- `arena-web-server:latest` was present with image id
  `sha256:27a307166f2fad40c73a8a4df2c59e5a1f9db13584383296a84ec5306f42dfc2`,
  matching the image recorded by the WP5 packet census.
- The existing `arena-witness` network used the documented
  `10.201.27.0/24` subnet.
- `scripts/stage-server-tree.py --role client` successfully staged the four
  digest-verified client game-tree files. The native client and the two renderer
  libraries were then copied beside that tree in `build/witness-client/`.

The staging script's `--check` mode is intentionally a check of the four-file
game tree, not of the final runnable directory. Running it after copying the
executables therefore rejects `ioquake3` as an extra executable-mode file; the
initial staging itself had already completed its verification successfully.

## Local networking variation

The first start followed the documented command literally: the server used the
static container address `10.201.27.10`, and the host-native client tried to
connect to `10.201.27.10:27960`. On this rootless Podman installation, the host
had no route into that private container subnet. The address resolved through
the host's ordinary default route instead, and the server saw no client
connection.

The server and client were stopped and restarted. The second server start kept
all documented containment options:

- all capabilities dropped;
- `no-new-privileges`;
- SELinux label separation disabled for this container;
- read-only root filesystem;
- the documented restricted temporary filesystem;
- the private `arena-witness` network and static server address.

It additionally published `27960/udp` only on host loopback:

```text
--publish 127.0.0.1:27960:27960/udp
```

The client then connected to `127.0.0.1:27960`. This did not expose the server
to the LAN, but it is a variation from the literal address in the pending-round
recipe. A future reproduction recipe should either document this rootless
Podman loopback mapping or run the graphical client in a network namespace that
can reach `10.201.27.10` directly.

## Witnessed results

| Checklist item | Result | Evidence |
| --- | --- | --- |
| Join | Pass | The client rendered the arena with the three bots. The server logged `ArenaWebCensus connected`, `ArenaWebCensus entered the game` and `ClientBegin: 3`. |
| Move, look and fire | Pass, with the input observation below | The player moved, aimed and fired during the live round. The server first logged `Skelebot killed ArenaWebCensus by MOD_ROCKET`, followed by the player's respawn and continued play. |
| Score against a bot | Pass | The operator witnessed the kill. The client displayed the obituary, and the server logged `Kill: 3 2 3: ArenaWebCensus killed Sly by MOD_MACHINEGUN`. |
| Disconnect and reconnect | Pass | The server logged `ArenaWebCensus disconnected`, then a second `connected`, `entered the game` and `ClientBegin: 3` for the same running client. The reconnected client rendered the arena again. |

The server-side frag and client obituary prove that the player scored. Whether
the increment was separately inspected under the Tab scoreboard was not
explicitly recorded during the guided session, so this report does not claim a
separate visual observation of that UI detail.

## Input and console observations

### Mouse capture

Mouse aiming worked well enough to play and score, but it was unnecessarily
difficult because the native client did not lock the pointer to its window.
This is consistent with the committed profile setting:

```text
+set in_nograb 1
```

The round therefore establishes functional mouse look, not comfortable pointer
capture. Anyone repeating the witnessed round should expect an extra focus
click and an unlocked pointer while that accepted profile argument remains in
place.

### Console command syntax

On this client, commands entered in the in-game console required a leading
slash. The working commands were:

```text
/disconnect
/connect 127.0.0.1:27960
```

Entering `disconnect` or `connect ...` while the chat field was active merely
sent those strings as chat messages. The leading-slash form in the actual
console performed the required operations. The stdin-assisted disconnect was
likewise normalized to the slash-prefixed console form; the reconnect was
entered in the game console and produced the server-log sequence above.

## Evidence images

The operator captured four native-client screenshots during the round; they
are committed under
[`evidence/wp5-witnessed-round-2026-08-30/`](evidence/wp5-witnessed-round-2026-08-30/)
as `native-client-1.png` … `native-client-4.png`. Beyond witnessing the round,
they are the native rendering reference for the browser-renderer defect class
recorded in [`wp4-vertical-slice.md`](wp4-vertical-slice.md): the native
client runs **without** the browser's `r_vertexLight` workaround, and the
images show intact lightmap lighting, no white surfaces, and items and player
models correctly shaded at distance (a bot at range in `native-client-3.png`,
items at range in `native-client-1.png`) — the exact cases that misrender in
the browser.

## Cleanup and repository state

- The client exited normally through its `quit` command.
- The server observed the final client disconnect and then shut down on
  `SIGINT`; its `--rm` container was removed.
- The `arena-witness` network existed before the round and was deliberately
  left in place.
- The runnable staged tree remains only under the gitignored
  `build/witness-client/` directory.
- No packet-census record or existing project document was overwritten during
  the round.

---

**Editorial note, added 2026-08-30 (after the record above was written).** The
"Evidence images" section describes distant items and player models as "the
exact cases that misrender in the browser". The root-cause investigation later
the same day reclassified that symptom: the native client renders those items
identically at the same camera positions, so their distant look is
renderergl2's normal shading for these OpenArena item shaders, not a browser
defect. The genuine browser defect the images helped diagnose — white
lightmapped world surfaces from a GLSL ES `mediump` precision default — was
root-caused and fixed in the engine. See the resolution section of
[`wp4-vertical-slice.md`](wp4-vertical-slice.md). The witnessed record above is
unchanged.
