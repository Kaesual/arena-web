<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP5 evidence: matching native server and packet census

**Status:** complete — the witnessed round of 2026-08-30 closed the one
pending acceptance word (see
[`wp5-witnessed-round-2026-08-30.md`](wp5-witnessed-round-2026-08-30.md))

The census below was taken from a live session and every required check passed.
The one thing an automated run could not produce — a *player* scoring against
the server — is recorded as an explicit non-gating observation and has its own
checklist at the end.

This document records the native dedicated server that matches the WP1 browser
client, the container image that carries it, the native test client that drives
it, and the packet census WP6 needs: what ioquake3 actually puts on the wire, in
both directions, at the engine/UDP boundary.

`ioq3/` is untouched at its pinned commit. No engine source is patched, no
engine option changes what is sent, and the census is taken from a packet
capture rather than from the game — the instrumentation is entirely outside the
game protocol.

## What was built

| Path | Role |
| --- | --- |
| [`locks/native-toolchain-packages.conf`](../locks/native-toolchain-packages.conf) | the exact package set the native toolchain installs, pinned to one immutable Ubuntu snapshot by version, size and SHA-256 |
| [`locks/native-toolchain-indexes.conf`](../locks/native-toolchain-indexes.conf) | the sidecar: the snapshot's own signed index files by digest, so the package lock's trust root is re-checkable |
| [`scripts/resolve-native-packages.sh`](../scripts/resolve-native-packages.sh) | maintenance: regenerate that lock (the only step that resolves anything) |
| [`scripts/fetch-native-packages.sh`](../scripts/fetch-native-packages.sh) | fetch and digest-verify those packages; `--check` re-verifies |
| [`scripts/native_toolchain.py`](../scripts/native_toolchain.py) | the lock's and the sidecar's fail-closed contract |
| [`scripts/container-runtime.sh`](../scripts/container-runtime.sh) | resolve the container runtime and refuse one that lacks the Podman constructs these steps use |
| [`native/toolchain.Containerfile`](../native/toolchain.Containerfile) | the build-and-test toolchain image, from the WP0 native builder base |
| [`scripts/build-native-toolchain.sh`](../scripts/build-native-toolchain.sh) | assemble that image offline; `--print-tag` |
| [`scripts/build-native.sh`](../scripts/build-native.sh) + [`build-native-in-container.sh`](../scripts/build-native-in-container.sh) | one accepted native build, `--target server` or `--target client` |
| [`scripts/verify-native-build.sh`](../scripts/verify-native-build.sh) | two clean builds, compared |
| [`native/server-profile.json`](../native/server-profile.json) | the declarative FFA profile both sides derive their command line from |
| [`native/server-default.cfg`](../native/server-default.cfg) | the server's own engine configuration |
| [`native/server.Containerfile`](../native/server.Containerfile) | the distributed server image, from the pinned Debian runtime base |
| [`scripts/arena_server.py`](../scripts/arena_server.py) | the packaging discipline: profile validation, command-line derivation, digest-verified staging |
| [`scripts/stage-server-tree.py`](../scripts/stage-server-tree.py) | stage the server's or the client's game tree |
| [`scripts/build-server-image.sh`](../scripts/build-server-image.sh) | stage, build and verify the server image |
| [`scripts/verify-server-image.py`](../scripts/verify-server-image.py) | inspect the built image and emit its manifest |
| [`native/census-client.sh`](../native/census-client.sh) | start the native client on a virtual X server inside the toolchain image |
| [`scripts/packet_census.py`](../scripts/packet_census.py) | the census itself: pcap parsing, classification and statistics |
| [`scripts/census_run.py`](../scripts/census_run.py) + [`run-packet-census.sh`](../scripts/run-packet-census.sh) | the driven session and its acceptance checks |
| [`provenance/arena-web-server.json`](../provenance/arena-web-server.json) | the artifact manifest of the server image's content |
| [`records/wp5-packet-census.json`](../records/wp5-packet-census.json) | the machine-readable census |

## The builder-pinning problem, and exactly how it was solved

WP0 pins the native builder base by platform digest and says in as many words
that this "is not permission to use an unversioned package repository during an
accepted build". That pin is the whole problem: `ubuntu@sha256:1e0a86e5…`
carries 92 packages and **no compiler, no CMake and no make**, and the ioquake3
tree is a CMake project. Something has to be installed.

The resolution has three parts, and none of them happens during an accepted
build:

1. **One immutable archive.** `snapshot.ubuntu.com` publishes the Ubuntu archive
   as it stood at an exact timestamp. The lock names
   `https://snapshot.ubuntu.com/ubuntu/20260824T000000Z`, and
   `scripts/native_toolchain.py` accepts only that URL shape — a bare
   `archive.ubuntu.com` or a suite name is rejected, because it would be exactly
   the moving reference WP0 forbids.
2. **One resolution, recorded.** `scripts/resolve-native-packages.sh` runs
   `apt-get install --print-uris` inside the pinned builder base against that
   snapshot and writes every resolved package — name, version, size, SHA-256 and
   pool path — into the lock. The digests are the ones the snapshot's own
   GPG-signed `Packages` index states, so the recorded values are rooted in
   Ubuntu's archive key at the moment the lock was made. This is the only step
   that needs the network *and* a dependency resolver, and it is a deliberate,
   reviewable toolchain change rather than something a build does.
3. **Verified, offline installation.** `scripts/fetch-native-packages.sh`
   downloads exactly those pool paths and rejects any byte whose length or
   SHA-256 differs — and rejects an unpinned file lying beside them, because the
   whole directory is the install set. `scripts/build-native-toolchain.sh` then
   builds the toolchain image with `--network none` and `--pull=never`, and the
   image's own `RUN` step empties the APT source list afterwards so a stray
   `apt-get update` inside the toolchain fails instead of reaching a moving
   archive.

4. **The trust root is recorded, not just used.** The package digests came out
   of the snapshot's signed `Packages` indexes during one networked resolution,
   and nothing about that run was itself recorded, so a reviewer would have had
   to take it on faith. `locks/native-toolchain-indexes.conf` is the sidecar
   that closes it: per suite the clearsigned `InRelease` (plus `Release` and
   `Release.gpg`) and per suite and component the `Packages.gz`/`Packages.xz`,
   each by SHA-256 and byte length, at the same immutable snapshot.
   `scripts/native_toolchain.py` validates its shape and requires it to describe
   the same archive, suites and components the package lock resolved against,
   and `fetch-native-packages.sh` loads it, so a malformed or disagreeing
   sidecar fails the step rather than sitting unread beside the lock.

   **What it makes auditable:** a reviewer can fetch those exact paths, check
   them against these digests, verify `InRelease` with the Ubuntu archive
   keyring, and confirm that the `Packages` files it names produce the package
   digests in the lock — offline afterwards, and without re-running the
   resolver. **What it does not do:** it does not remove the need to trust that
   keyring, and it is deliberately not a second copy of the package digests; a
   test asserts the two files share none.

The install itself is two passes — `dpkg --unpack --force-depends` over the
whole closure, then an unforced `dpkg --configure -a`. A single
`dpkg --install` cannot work offline here: it unpacks in the order it is given
and `python3-minimal` `Pre-Depends` on a package that sorts after it. The
forcing applies to the unpack pass only; the configure pass runs unforced and
the image build then asserts, with an **anchored** match, that no package is
left in a state other than `install ok installed` — unanchored, that pattern
also matches `deinstall ok installed`, so a removed package would read as a
healthy one — and that at least as many packages are installed as `.deb` files
were staged, because a package that never unpacked leaves no status line to be
unhealthy. Neither check tolerates a `dpkg-query` failure, so an incomplete
closure fails rather than producing a half-installed toolchain.

**The toolchain is build-and-test only.** It compiles the dedicated server and
the native test client, runs that client for the census and carries `tcpdump`.
No *file* from it is copied into the distributed server image, which starts from
the separately pinned Debian runtime base and inherits no layer from Ubuntu.

That is the honest form of the claim, and the stronger one would be wrong:
`ioq3ded` is statically linked against the toolchain's C runtime startup objects
and `libgcc`, so fragments of Ubuntu-built `crt*.o` and `libgcc.a` are inside
the distributed binary. Both carry permissive terms for exactly this — the GCC
Runtime Library Exception for `libgcc`, and glibc's own permissive terms for the
startup files — so the licensing conclusion is unchanged: the binary is
distributable under the engine's GPL without the toolchain becoming a
distributed component. What changes is only the wording, from "no bytes" to "no
files".

`locks/native-toolchain-packages.conf`'s own header still carries the older,
broader phrasing ("none of its bytes"). It is left exactly as it is on purpose:
that file's SHA-256 is the toolchain image's tag, the
`native-toolchain-packages` input identity in
`provenance/arena-web-server.json` and a field of the census record, so editing
a comment in it would move three committed identities for a wording fix. The
correction lives here; the next change that legitimately regenerates the lock
carries it into the header.

The server binary is compiled against the builder's glibc 2.39 and runs on the
runtime base's 2.41; that direction works and the reverse would not, which is
what the WP0 amendment already recorded.

## The dedicated server

### What is compiled

The dedicated target compiles with `DEDICATED`/`BOTLIB` and the null client
stubs and links only `${CMAKE_DL_LIBS}` and `m`
(`cmake/server.cmake:17-18`, `cmake/platforms/unix.cmake:19-21`) — no SDL, no
GL. The accepted configuration is:

```text
-DBUILD_SERVER=ON  -DBUILD_CLIENT=OFF
-DBUILD_RENDERER_GL1=OFF  -DBUILD_RENDERER_GL2=OFF
-DBUILD_GAME_LIBRARIES=OFF  -DBUILD_GAME_QVMS=OFF
```

The game modules are deliberately **not** built. The QVMs this server loads are
the accepted WP1 artifacts, so building a second set here would create a second
identity for the same bytecode and would run `code/tools/lcc`, whose 1998 terms
restrict commercial use, for no reason at all. The in-container script proves
the absence rather than assuming it: it fails if any `q3lcc`, `q3rcc`, `q3cpp`,
`lburg` or `q3asm` executable exists in the build tree.

Determinism uses the same controls as the browser build: a source export of the
pinned commit without Git metadata, fixed container paths, `LC_ALL=C`/`TZ=UTC`
and a `SOURCE_DATE_EPOCH` that is the pinned engine commit's own committer
timestamp — asserted, not taken from the moment of the build.

### What the image contains

`native/server.Containerfile` starts `FROM` the pinned runtime base by digest
and adds four files and nothing else:

```text
/opt/arena-web/ioq3ded                     the dedicated server
/opt/arena-web/arena/arena-web-ffa.pk3     the audited WP3 content pack
/opt/arena-web/arena/default.cfg           the product's own server configuration
/opt/arena-web/arena/vm/qagame.qvm         the accepted WP1 game module
```

The game directory sits beside the binary because ioquake3 derives `fs_basepath`
from the executable's own directory, not from the working directory:
`main` calls `Sys_SetDefaultInstallPath(DEFAULT_BASEDIR)`
(`code/sys/sys_main.c:838-839`), and on Linux `DEFAULT_BASEDIR` is
`Sys_BinaryPath()` (`:739-747`). That is one path in the profile rather than two
that could drift.

`scripts/verify-server-image.py` then inspects the **built image**, not the
build context:

- it lists the whole filesystem of the runtime base and of the built image the
  same way, as the same user, and requires the difference to be exactly those
  four files plus the directories that contain them. Nothing else added, nothing
  removed, nothing changed;
- it digests those four paths inside the image and requires each to equal the
  committed manifest entry — or, for the binary, the build output this
  repository produced;
- it lists every per-package `/usr/share/doc/*/copyright` file in both images and
  requires the two sets to be identical, content included.

The image runs as `65534:65534`, declares no writable state, and the census
mounts an empty `tmpfs` over `$HOME`. It therefore starts from an empty writable
state and needs no undeclared host content.

### `preserve-copyright-files`, and the two the obvious check misses

The baseline's runtime-base record carries `preserve-copyright-files` as a
redistribution obligation. Discharging it means counting the files in the built
image, and the obvious `find /usr/share/doc -maxdepth 2 -name copyright -type f`
finds **76**, not the 78 the baseline documents. The difference is not a missing
file: `/usr/share/doc/libgcc-s1` and `/usr/share/doc/libstdc++6` are symlinks to
`gcc-14-base`'s directory, and a plain `find` does not descend into them. The
check uses `find -L` and verifies all 78 by content.

This is worth stating because a check that silently verified 76 of 78 would have
looked like it passed — and comparing the image against the base cannot catch
it, since a mis-reading applies to both sides equally. The expected count is
therefore pinned in `native/server-profile.json` as `runtimeBaseCopyrightFiles`
and required of the **base** before the two sets are compared, so reverting
`find -L` to `find` fails the verification instead of passing quietly with 76.

## The native test client

The client is the census instrument. It is **not distributed**: it is built in
the same pinned toolchain, from the same pinned engine commit, and it runs
inside that toolchain image for exactly as long as the census takes.

It needs a real GL context. SDL's dummy video driver provides none, so the
client would fail renderer initialisation; `native/census-client.sh` starts
`Xvfb` and selects Mesa's software rasteriser explicitly
(`LIBGL_ALWAYS_SOFTWARE=1`, `GALLIUM_DRIVER=llvmpipe`) rather than depending on
whatever a host driver might expose inside a container.

Its game tree is the same audited pack and the same accepted QVMs the server
carries — `cgame` and `ui` rather than `qagame`, because those are the modules a
client loads. Both trees are staged by the same digest-verified code path, so
the identity agreement is a property of the staging rather than a claim.

## The census

### Where it is measured

The capture container shares the **server container's network namespace**
(`--network container:…`) and runs `tcpdump` on the server's own interface with
the filter `udp port 27960`. Two consequences matter:

- the capture cannot contain host traffic. The only interface it can see belongs
  to a container on a private network created for this session, and the filter
  additionally restricts it to the server's own port;
- the capture cannot contain a credential. The profile sets no password, the
  server registers `rconPassword` empty, and no authorization exists in this
  protocol at all.

Sizes are recorded at the engine/UDP boundary: `udpPayloadBytes` is exactly the
buffer the engine handed to `sendto` or read from `recvfrom`
(`code/qcommon/net_ip.c` `NET_SendPacket` / `NET_GetPacket`). The IP and
link-layer totals are recorded beside it so a reader can see the difference
rather than assume one.

### How a datagram is classified

| Property | Rule | Source |
| --- | --- | --- |
| direction | by UDP port against the server endpoint | — |
| connectionless vs netchan | first four bytes equal to −1 | `code/qcommon/net_chan.c:35` |
| fragmented | high bit of the sequence (`FRAGMENT_BIT`) | `net_chan.c:55` |
| header bytes | sequence 4, plus qport 2 **only** client-to-server, plus challenge checksum 4, plus 2 + 2 when fragmented | `Netchan_Transmit`, `net_chan.c:196-210`; `Netchan_TransmitNextFragment`, `:117-137` |
| connection | a client-to-server sequence that goes **backwards** opens the next one | `Netchan_Setup` resets the netchan, `net_chan.c:84-90` |

A strict decrease, not a failure to advance: `Netchan_TransmitNextFragment`
writes the same `outgoingSequence` on every fragment of one message and
increments only after the last (`net_chan.c:118`, `:162-165`), so a repeated
client sequence is the rest of a fragmented message. Treating it as a reconnect
would invent a connection and split the message into one-fragment pieces. This
profile produced no fragmented client message, so the accepted record reads the
same either way — but WP6 has to generate exactly that case with this tool, so
it is fixed and tested rather than left to be discovered there.

The netchan payload itself is Huffman-coded, so the census reads sizes and
headers rather than message contents. What the traffic cannot say — that the
client joined, scored and came back — is checked against the game's own logs
instead, and every check records which of the two it rests on.

### What was driven

The client is driven through its own console over stdin, which ioquake3 reads
when stdin is not a terminal (`code/sys/con_tty.c:290-296`, queued by
`Com_GetEvent` in `code/qcommon/common.c:2029-2040`). Only commands a person at
the keyboard would type are used: the movement and button commands of
`code/client/cl_input.c` and the `connect`, `disconnect`, `ping` and
`serverstatus` commands of `code/client/cl_main.c`.

The session is: start and connect, the two connectionless queries a server
browser makes, driven play against the profile's three bots, `disconnect`, a
pause long enough for `sv_reconnectlimit`, `connect` again, driven play again,
`disconnect`, `quit`.

## Results

The accepted session is
[`records/wp5-packet-census.json`](../records/wp5-packet-census.json). It is the
run's own summary, unedited: 41,833 engine datagrams over two connections,
against `arena-web-server:latest`
(`sha256:27a307166f2fad40c73a8a4df2c59e5a1f9db13584383296a84ec5306f42dfc2`) and
the toolchain image `arena-web-native-toolchain:d36179bb9342033f`
(`sha256:e472ec1f90ee255dec1d532479fba43cfd893922be29327eb46c98df2d06f4c3`).

### Per direction, at the engine/UDP boundary

| | client → server | server → client |
| --- | --- | --- |
| datagrams | 34,179 | 7,654 |
| **maximum** | **394 B** | **1,312 B** |
| minimum | 13 B | 30 B |
| median | 36 B | 81 B |
| 95th percentile | 38 B | 124 B |
| 99th percentile | 40 B | 191 B |
| mean | 34.9 B | 86.7 B |
| total | 1,194,275 B | 663,505 B |

The asymmetry is the point. The server's datagrams are on average 2.5 times
larger and its maximum is 3.3 times larger, because a snapshot carries the world
and a usercmd carries one player's intent.

**The client's packet *rate*, however, is not representative, and the census says
so rather than hiding it.** The client sent 89.5 and 89.3 datagrams a second
across the two connections, not the 30 `cl_maxpackets` defaults to
(`code/client/cl_main.c:3588`): the census server
is on `10.201.27.0/24`, `Sys_IsLANAddress` treats RFC1918 space as LAN
(`code/qcommon/net_ip.c:715-736`), and `CL_ReadyToSendPacket` then returns true
every frame while `cl_lanForcePackets` is 1 (`code/client/cl_input.c:701-703`,
default 1 at `cl_main.c:3655`). The server has the mirror rule for outgoing rate
(`sv_lanForceRate`, `code/server/sv_snapshot.c:669`,
`code/server/sv_client.c:1416`), although it was not the binding constraint
here: the server sent 20.0 datagrams a second in both connections, which is the
`sv_fps` of 20 that `native/server-default.cfg` sets.

**Sizes are unaffected by that**, and sizes are what this census measures. A
routed deployment would see fewer client datagrams of the same shapes. Counts,
totals and per-second rates from this session are therefore not transferable;
the size distributions are.

### Netchan versus connectionless

| | client → server | server → client |
| --- | --- | --- |
| netchan | 34,173 (max 394 B) | 7,648 (max 1,312 B) |
| connectionless | 6 (max 292 B) | 6 (max 465 B) |
| fragmented | 0 | 4 |

Connectionless traffic is rare but it is **not** small, and it is exactly the
traffic netchan fragmentation would never protect: an out-of-band datagram
carries no fragment fields at all.

| Command | Direction | Count | Sizes |
| --- | --- | --- | --- |
| `getstatus` | client → server | 1 | 13 |
| `getinfo` | client → server | 1 | 15 |
| `getchallenge` | client → server | 2 | 39, 40 |
| `connect` | client → server | 2 | 263, 292 |
| `connectResponse` | server → client | 2 | 30 |
| `challengeResponse` | server → client | 2 | 46, 47 |
| `infoResponse` | server → client | 1 | 182 |
| `statusResponse` | server → client | 1 | 465 |

No unknown out-of-band command appeared. The largest connectionless datagram in
either direction is the `statusResponse` at 465 bytes, and its size grows with
the number of connected players — with three bots and one client it is already
465, which is worth carrying into WP6 rather than treating out-of-band traffic
as negligible.

### Distribution

The record carries the complete per-size counts; the shape is:

- **client → server** has 31 distinct netchan sizes, concentrated in a narrow
  band: 37 B (7,752), 36 B (4,976), 34 B (4,291), 38 B (3,944), 35 B (3,285),
  33 B (2,519). Everything above ~60 bytes is a reliable command riding along,
  and the handful of datagrams at the maximum are such bursts.
- **server → client** has 186 distinct netchan sizes with a long tail: the mode
  is around 73–79 B (296 at 75 B, 284 at 76 B, 275 at 77 B), the 99th percentile
  is 191 B, and the tail runs to the 1,312-byte gamestate fragment.

### Fragmentation, and the largest thing the protocol sends

Exactly two messages were fragmented in the whole session — one per connection,
both the gamestate, both server-to-client:

| Connection | Fragments | Message bytes | Total UDP payload | Largest datagram |
| --- | --- | --- | --- | --- |
| 0 | 2 | 2,304 | 2,328 | 1,312 |
| 1 | 2 | 2,306 | 2,330 | 1,312 |

**1,312 bytes is the observed maximum in the entire census, and it is exactly
the value WP0 predicted from the source**: a 1,300-byte fragment
(`FRAGMENT_SIZE`) plus the 12-byte fragmented server header. Nothing reached
`MAX_PACKETLEN` (1,400): the netchan fragments at 1,300 first, so no *netchan*
datagram gets that large. That bound is a netchan bound only — a connectionless
datagram never goes through `Netchan_Transmit` at all and has no fragment
fields, which is why `statusResponse` is reported separately below and in
finding 3. Two datagrams in the session were at or above `FRAGMENT_SIZE`; both
are the first fragment of a gamestate.

The client fragmented nothing. Its largest message in this profile is 394 bytes,
so the client-to-server fragmented header of 14 bytes was never exercised — a
gap the census records rather than papers over, and one WP6 has to cover by
generating the case rather than waiting to observe it.

### The header asymmetry, measured

| | Observed header | Count |
| --- | --- | --- |
| client → server, whole | **10 B** | 34,173 |
| server → client, whole | **8 B** | 7,644 |
| server → client, fragmented | **12 B** | 4 |
| client → server, fragmented | — | 0 |

The difference is exactly 2 bytes, the qport only a client writes, and the
fragment surcharge is exactly 4.

**Read this precisely: it is a consistency check, not an independent
measurement of the header layout.** `classify` computes the header length from
the pinned engine's own constants, selected by direction and by the fragment
bit, and the wire evidence is that all 41,833 datagrams are consistent with that
computation — none was shorter than its computed header, every client-to-server
netchan datagram carried a parseable qport in the two bytes the layout puts it
in, and the fragment bit and the size classes line up. A census that re-derived
the layout from the bytes alone would have to decode the message body, which the
netchan Huffman-codes. So WP0's 10/8/+4 numbers are confirmed to be the numbers
this profile is consistent with on every datagram; they were read from
`net_chan.c:199-210` and `:118-138`, and this does not replace that reading.

### Connection, disconnect and reconnect

The capture segments into two netchan connections without reading a single
payload byte, because a netchan's outgoing sequence restarts at 1:

| Connection | client → server | server → client | qport | client source port |
| --- | --- | --- | --- | --- |
| 0 | 21,944 | 4,906 | 44023 | 27960 |
| 1 | 12,229 | 2,742 | 44023 | 27960 |

Both the disconnect and the reconnect are therefore visible in the traffic, and
the server's own log confirms them: two `entered the game` lines and two
`disconnected` lines for the census client.

Two observations in that table matter for later work, and neither is a setting
this repository chose:

- **the qport is the same across the reconnect.** `net_qport` is `CVAR_INIT` and
  drawn once per process from `Com_RandomBytes` (`net_chan.c:76`,
  `code/qcommon/common.c:2816-2818`), so a reconnecting client is identifiable
  across connections by qport alone;
- **the client's own UDP source port is 27960**, the same number as the server's.
  `net_port` defaults to `PORT_SERVER` for a client too, and `NET_OpenIP` scans
  upward from it only on a bind conflict (`code/qcommon/net_ip.c:1363-1405`). A
  direct client's source port is therefore predictable, which a relay design
  should know before it reasons about port-based separation.

### The protocol steps the census covers

Every step is located in the capture rather than in the driver's timeline:

| Step | Direction | First observed size |
| --- | --- | --- |
| `getstatus` | client → server | 13 B |
| `getinfo` | client → server | 15 B |
| `infoResponse` | server → client | 182 B |
| `statusResponse` | server → client | 465 B |
| `getchallenge` | client → server | 39 B |
| `challengeResponse` | server → client | 46 B |
| `connect` | client → server | 263 B |
| `connectResponse` | server → client | 30 B |
| first netchan client → server | client → server | 15 B |
| first netchan server → client (gamestate fragment) | server → client | 1,312 B |

### Acceptance checks

All eleven required checks passed. The twelfth is reported rather than required
and did not occur; the findings below say why.

| Check | Evidence | Result |
| --- | --- | --- |
| `client-joined-twice` | server log | pass — 2 `entered the game` lines |
| `client-disconnected` | server log | pass — 2 `disconnected` lines |
| `client-took-damage-and-died` | server log | pass — 40 obituaries naming the client |
| `client-scored-and-respawned` | server log | pass — 14 score-changing deaths |
| `client-fragged-a-bot` | server log | **not observed** — see the findings below |
| `two-netchan-connections-observed` | capture | pass |
| `challenge-and-connect-observed` | capture | pass |
| `initial-queries-observed` | capture | pass |
| `gamestate-fragments-observed` | capture | pass — 2 |
| `no-media-download-attempted` | client log | pass |
| `no-engine-error` | both logs | pass |
| `no-unknown-connectionless-command` | capture | pass |

### Determinism of the artifacts

- Two clean builds of the dedicated server in the pinned toolchain produced a
  byte-identical binary,
  `sha256:dbb194f26ec8870e004da56acc11d5caa449dd2a2afd829be957f534cef499d2`
  (798,456 bytes).
- Two builds of the server image from those binaries produced the **same image
  id**, `sha256:27a307166f2fad40c73a8a4df2c59e5a1f9db13584383296a84ec5306f42dfc2`,
  and identical content manifests, which also still equal the committed
  `provenance/arena-web-server.json` apart from `producer`.
  `scripts/verify-native-build.sh --target server` is what checks all three, and
  `podman build --timestamp 0` is what makes the image id reproducible at all;
  without it the two images would differ only in when they were made.
- Two clean builds of the native test client were byte-identical too:
  `ioquake3` `sha256:0a16b40a…`, `renderer_opengl1.so` `sha256:5e13c0b8…`,
  `renderer_opengl2.so` `sha256:7a49bf20…`. It is not distributed, so this is
  hygiene rather than an obligation — but a census instrument that is not
  reproducible is a poor instrument.

> **Note appended 2026-08-31.** The second bullet's "still equal the
> committed `provenance/arena-web-server.json`" is scoped to the
> pre-amendment content pack this image was built and censused with
> (`sha256:55a1d51f…`, 24,181,175 bytes). WP3's content amendment of
> 2026-08-31 reissued the pack under a new identity while this record
> deliberately keeps the image as censused, so a rebuild from the current
> `build/content-pack` — including `scripts/verify-native-build.sh
> --target server` — now reports a pack mismatch by design until WP7's
> mandated server rebuild reissues the record. Everything above stands as
> the record of the accepted WP5 builds; see the amendment section of
> [`wp3-content-closure.md`](wp3-content-closure.md) for the disclosure.

## Standalone operation, observed

WP4 recorded two consequences of running a standalone game directory that WP5
inherits. The first follows from the source; the second was checked on this
image directly.

- **The v4 authorize-server challenge and its CD-key paths are skipped.**
  `SV_GetChallenge` only contacts `AUTHORIZE_SERVER_NAME` when `com_standalone`
  is 0 (`code/server/sv_client.c:150`), and `FS_CheckPak0` sets it to 1 for any
  base game that is not ioquake3's own (`code/qcommon/files.c:3652-3657`). The
  census server therefore performs no authorization of any kind — which is also
  why the capture contains no credential to leak.
- **`banUser` and `banClient` are not registered at all.**
  `code/server/sv_ccmds.c:1526-1529` registers them only when `com_standalone`
  is 0. Feeding the running server three commands on its console shows it:

  ```text
  banUser someone   ->  broadcast: print "server: banUser someone\n"
  banClient 0       ->  broadcast: print "server: banClient 0\n"
  kick someone      ->  Player someone is not on the server
  ```

  The first two are not engine commands, so `Cmd_ExecuteString` forwarded them
  to the game module, which on a dedicated server echoes any command it does not
  recognise as a server say (`code/game/g_svcmds.c:501-509`). `kick` **is** an
  engine command and the engine answered it itself (`SV_Kick_f`,
  `sv_ccmds.c:378`). A server built on this engine therefore has no built-in ban
  command, which any later hosting design has to supply itself — and which
  matters more, not less, once every relayed player shares one address.

## What the census means for a relayed deployment

WP5's scope asks for the effects of all relayed players sharing the relay's base
IPv4 address. Those were established against the pinned engine while the WP0
amendment was open; the census adds one measurement to them.

- **The query and challenge rate limiters key on the base address only.**
  `SVC_BucketForAddress` compares `address.ip` (or `.ip6`) and never the UDP
  source port (`code/server/sv_main.c:405-433`), so every relayed player shares
  one bucket. `SV_GetChallenge` allows 10 challenges per 1,000 ms per address
  (`code/server/sv_client.c:71`) and `getstatus`, `getinfo` and `rcon` have the
  same shape (`sv_main.c:549`, `:612`, `:719`), plus one global outbound bucket.
- **Bans are portless.** `SV_IsBanned` (`sv_client.c:288`) compares with
  `NET_CompareBaseAdrMask` (`code/qcommon/net_ip.c:397-440`), which masks the
  address and never looks at the port. Banning one relayed player bans every
  player behind that relay.
- **Netchan separation is by qport, and it is probabilistic.** `SV_PacketEvent`
  matches a packet to a client by base address plus the client-chosen 16-bit
  qport (`sv_main.c:845-870`; rationale at `net_chan.c:40-46`), drawn from
  `Com_RandomBytes` (`code/qcommon/common.c:2816-2818`). Two relayed players
  therefore collide with probability about 1/65536 per pair.
- **`SV_DirectConnect` matches a slot on base address AND (qport OR source
  port).** Both the reconnect-throttle scan (`sv_client.c:373-379`) and the slot
  reuse (`:461-463`) accept either, so a relay that reassigns a UDP source port
  to a second player can make the server treat that player as the first one
  reconnecting. This is a hazard for the relay's port-assignment policy, not a
  bug in the engine.

What the census measures on top of that: **the qport is chosen once per process,
not per connection.** `net_qport` is `CVAR_INIT` and set from
`Com_RandomBytes` during `Com_Init` (`net_chan.c:76`,
`common.c:2816-2818`), so the disconnect and reconnect in this session kept the
same qport, and both netchan runs the capture segments carry it. A client that
reconnects is therefore identifiable across connections by qport alone, which is
a privacy property a relay design has to be aware of rather than an engine
setting to change.

## The `_baseline_input_identities` extension

The WP0 amendment pinned the runtime base and deliberately left one thing for
WP5: `_baseline_input_identities` in `scripts/metadata.py` mapped only the
engine and the `tools[]` entries, so an artifact manifest that declared
`server-runtime-base` as a baseline input failed closed with "contains unknown
baseline input". That is exactly what the server-image manifest has to declare —
for an image the base is not a tool that vanishes when the build ends, it is
most of the distributed bytes.

The extension adds the third collection to that mapping and nothing else. Every
rule an input is checked by is unchanged: the id must exist in the baseline, the
manifest must carry a matching `inputs[]` record, its kind and identity must
agree exactly, and a baseline input present in the manifest but undeclared is
still rejected. The function additionally refuses a baseline that records one id
in two collections. That cannot happen in a valid lock — the engine id is fixed
to `ioq3` and both id sets are closed to their own reviewed sets — but this
function is the one place where a collision would silently resolve to one record
rather than fail, so it fails.

`tests/test_metadata.py` gains a dedicated class: the positive path, the
identity of all three collections including the archive branch, a wrong runtime
digest, a wrong kind, a renamed id, a present-but-undeclared runtime base, the
two-collection refusal, and a check that the committed
`provenance/arena-web-server.json` agrees with the baseline it names.

## What is tested

| Suite | Covers |
| --- | --- |
| `tests/test_native_toolchain.py` | the package lock: a moving archive, a second snapshot, an unknown directive, unsorted or duplicated rows, a shared digest, a malformed digest or size, a pool path outside `pool/` or traversing out of it, a pool file naming another package or version, both epoch spellings, a foreign architecture, an unresolved request; the fetched directory: a missing, extra, modified, truncated or symlinked package; and the index sidecar: a moving archive, a missing or duplicated snapshot, an unknown directive or index kind, a suite index carrying a component and a component index without one, a non-canonical path, a malformed digest or size, duplicated or unsorted rows, a suite with no `InRelease`, a component with no `Packages`, an extra suite, and the committed sidecar's agreement with the committed package lock |
| `tests/test_arena_server.py` | the profile: the retail base game, a public master registration, a pure server, a second address family, a port that disagrees with its cvar, a privileged port, a non-FFA game type, a client download, the retail player model, a map or bot the pack does not carry, a frag limit that differs from the recipe, an unexplained or shallow cvar note, a config source outside the repository, a config that is not `default.cfg`, a missing role, an unknown key, an out-of-range bot skill, a relative game directory, and a committed argument list that is not the derivation; plus staging: a wrong artifact, a missing build directory, an extra or modified staged file and a wrong mode |
| `tests/test_packet_census.py` | a fragmented client message staying one connection and one three-fragment message, its 14-byte header, and a strictly decreasing sequence still opening a connection; the committed record's required checks, evidence labels, session identity, both directions and classes, header asymmetry, engine bound, connection count and command set; the capture reader: Ethernet, cooked and big-endian captures, a non-pcap file, a short header, an unsupported link type, a truncated snapshot, an IP fragment, non-IPv4 frames, a capture ending mid-record; the classifier: connectionless detection and command extraction, the 10- and 8-byte headers, the fragment surcharge in both directions, datagrams shorter than their own header or sequence, an unknown direction; and the session: direction derivation, foreign traffic, phases, class separation, command naming, an unexpected connectionless command, reconnect segmentation, one gamestate per connection, milestones, per-direction maxima and distributions, the engine bounds and an empty census |
| `tests/test_metadata.py` | the `_baseline_input_identities` extension, as above |

Everything above is deterministic and runs in `scripts/check.sh` without a
container, a build or a network.

**The census run itself has no unit test, on purpose.** It starts containers,
drives a real client through a real X server and reads a real capture; a test
against stubs would mostly assert that the stubs behave like the stubs. What
covers it instead is that its own run scores itself: twelve acceptance checks —
eleven required and one reported — each naming whether it rests on the capture
or on the game's own logs, and the run fails if any required one fails.

## Findings recorded rather than fixed

### 1. The driven client did not frag a bot

WP5's acceptance evidence asks for a client that "connects, joins, plays,
scores, disconnects and reconnects". Everything except one word is demonstrated
and checked: the client connects twice, joins by name twice, plays for the whole
session — moving, turning, firing, switching weapons, chatting — takes damage
from the bots 40 times, changes its own score by dying 14 times, disconnects
twice and reconnects once.

What did not happen is a **frag against a bot**. The client is driven blind from
a script over its own console: it fires wherever it happens to be facing, with
no view of the screen and no aim. Several sessions were driven with progressively
better tactics — stepped sweeps instead of a continuous spin, so a dozen shots
land in each facing rather than being smeared over the arena; explicit ascending
`weapon` selection instead of `weapnext`, which with only the spawn weapons
alternates onto the gauntlet and fires at nothing; respawning to restock the
spawn weapon's 100 bullets; and the bots at the engine's lowest skill, where
`G_AddBot` also gives them a handicap of 50 — and none of them produced one.

This is recorded rather than engineered around, for two reasons. Forcing it
would mean changing the game — a damage multiplier, a stripped-down map, a
weapon the profile does not start with — and the census would then no longer be
of the profile the browser slice runs. And the packet census does not depend on
it: a frag is one obituary, a handful of bytes inside one snapshot, in a session
of 41,833 datagrams.

The check therefore stays in the record as an explicit `required: false`
observation rather than being deleted or quietly passed, and **scoring by a
player is left to a witnessed round**, exactly as WP4 left its player-input
outcomes to one. The checklist is at the end of this document.

### 2. The observed maximum is a floor for WP6, not a bound

1,312 bytes is the largest datagram this profile produced, and it is the
structural maximum of a *fragment*. It is not the largest datagram the protocol
can produce: the client-side fragmented header is 2 bytes larger, and neither a
larger gamestate nor a busier snapshot was exercised by one map, one game type,
three bots and one human slot. WP6 is required to generate boundary cases rather
than inherit this session's maximum, and this document does not offer 1,312 as a
bound.

### 3. `statusResponse` grows with the player count

The out-of-band `statusResponse` was 465 bytes with four players on the server,
and it is built from the server info plus one line per client
(`code/server/sv_main.c` `SVC_Status`). With `sv_maxclients` at 8 it would be
roughly twice that. Connectionless traffic cannot be fragmented, so this is the
one observed shape that grows without a netchan bound behind it.

### 4. The census client's source port is not a distinguishing feature

The client bound UDP 27960 — the same number as the server, because `net_port`
defaults to `PORT_SERVER` on both sides. Nothing here depends on that, but a
later design that reasons about source ports should not assume they are random.

## Reproducing the result

From a clean checkout, with the WP1 browser build and the WP3 content pack
already produced:

```bash
scripts/fetch-native-packages.sh                 # once, online
scripts/build-native-toolchain.sh                # offline from here on
scripts/build-native.sh --target server
scripts/build-native.sh --target client
scripts/build-server-image.sh
scripts/run-packet-census.sh --rotation oa_pvomit \
  --settings '{"gametype":0,"fraglimit":15,"bots":{"named":[{"name":"Skelebot","skill":1},{"name":"Liz","skill":1},{"name":"Major","skill":1}]}}' \
  --play-seconds 240 --max-play-seconds 420 \
  --record records/wp5-packet-census.json
```

That last line is the exact command the accepted census was taken with, options
included: the defaults are shorter (120 seconds of driven play, no record
written), and `--record` is what turns a run into the committed evidence — it
refuses to write when a required check failed. Neither `--rotation` nor
`--settings` has a default and both are required: the map a server plays, its
gametype, its frag limit and its bots are all launch arguments, and the census
is a caller like any other. The accepted census was taken on `oa_pvomit` at the
values above, which is why they are written here rather than as placeholders.
The bots run at the engine's lowest skill on purpose: the census client is
driven blind from a script and would otherwise spend the session respawning
rather than playing. Bot skill changes how the game plays, not what the protocol
sends.

These scripts default to `CONTAINER_RUNTIME=podman` and check up front that the
runtime provides the constructs they use; see the note on that below.

Only the first command uses the network, and it accepts a package only if its
length and SHA-256 match the lock. `scripts/verify-native-build.sh --target
server` runs two clean builds, builds the image from each, compares the two
image ids and requires the regenerated manifest to still equal the committed
`provenance/arena-web-server.json` apart from `producer`. The census writes its
evidence to the gitignored `build/packet-census/`: the raw capture, both game
logs, the per-datagram records and the summary.
`records/wp5-packet-census.json` is that summary from the accepted run.

The pinned runtime base must be present locally; obtain the exact reference with
`scripts/baseline-inputs.py server-runtime-image` and pull that digest.

### Why these steps are Podman-specific

`podman build --timestamp` is what makes two assemblies of the same content
produce the same image id, and `podman image exists` is the existence test the
scripts use; Docker spells neither the same way. The scripts therefore default
to `podman` and `scripts/container-runtime.sh` fails early with a clear message
if the selected runtime lacks those constructs, instead of failing obscurely
part way through a build. The image-id reproducibility claim in this document is
a Podman claim.

## Witnessed round — completed 2026-08-30

One acceptance word was not covered by the automated session: a player
scoring. The operator played the round on 2026-08-30 and every item passed —
join, move/look/fire, **a witnessed kill against a bot** (client obituary plus
the server's `Kill: … ArenaWebCensus killed Sly by MOD_MACHINEGUN`),
disconnect and reconnect. The full report, including a documented
local-network variation and two usability observations, is
[`wp5-witnessed-round-2026-08-30.md`](wp5-witnessed-round-2026-08-30.md);
with it, WP5's client acceptance list is complete.

One reproduction note from that round: on a rootless Podman host there is no
route from the host into the container subnet, so the host-native client
cannot reach `10.201.27.10` directly. The witnessed round kept every
containment option and additionally published the port on loopback only —
`--publish 127.0.0.1:27960:27960/udp` — with the client connecting to
`127.0.0.1:27960`. The recipe below is kept in its original in-network form;
add that publish option when running the client on the host.

The original recipe, for reproduction:

Start the server exactly as the census does, containment included — the
acceptance sits next to that posture, so it should not be witnessed against a
laxer one:

```bash
podman network create --subnet 10.201.27.0/24 arena-witness
podman run --rm --name arena-witness-server \
  --network arena-witness --ip 10.201.27.10 \
  --cap-drop all \
  --security-opt no-new-privileges \
  --security-opt label=disable \
  --read-only \
  --tmpfs /var/lib/arena:rw,noexec,nosuid,nodev,mode=1777 \
  arena-web-server:latest $(python3 -c \
    'import sys;sys.path.insert(0,"scripts")
from pathlib import Path
from arena_server import load_profile, server_launch_arguments
settings = {"gametype": 0, "fraglimit": 15, "bots": {"minPlayers": 3, "skill": 1}}
print(" ".join(server_launch_arguments(Path("."), load_profile(Path(".")), ["oa_pvomit"], settings)))')
```

Then stage the client's own tree — it is not the server's, because a client
loads `cgame` and `ui` rather than `qagame` — and put the built binaries beside
it, which is what the census driver does too:

```bash
scripts/stage-server-tree.py --role client --target build/witness-client
cp build/native-client/tree/Release/ioquake3 \
   build/native-client/tree/Release/renderer_opengl*.so build/witness-client/
```

Then run `build/witness-client/ioquake3` against `10.201.27.10:27960` on a real
display, with the profile's client arguments, and:

- [ ] **Join.** The client connects and the arena appears with the three bots.
- [ ] **Move, look and fire.** Movement, mouse look and the weapon all respond.
- [ ] **Score.** Kill a bot; the obituary appears and the scoreboard (Tab) shows
      the frag.
- [ ] **Disconnect and reconnect.** `disconnect`, then `connect
      10.201.27.10:27960` again, and the session resumes.

Record the result. Only then is the "scores" half of WP5's client acceptance
complete; everything else in the list above is already checked automatically and
recorded in `records/wp5-packet-census.json`.

## What this does not prove

- **Nothing about the browser.** This is the native client against the native
  server on a container network. WebTransport, the relay and the browser network
  backend are WP7.
- **Nothing about a relayed address.** The census observes one client at one
  address; the shared-base-address consequences above are read from the engine
  source, not measured, because measuring them needs a relay and two players.
- **Nothing about packet shapes this profile did not produce.** A short session
  of one map, one game type and three bots is a sample. Deriving bounds from it
  is WP6's job, and WP6 is explicitly required to generate boundary cases the
  observed session may not contain.
- **Nothing about latency, loss or reordering.** A container bridge has
  essentially none of any of them, which is why the census reports sizes and
  counts and not timing quality.
- **Nothing about packet *rates* over a real path.** Both endpoints treat the
  other as a LAN peer on this private network and skip their own rate limits, so
  the client's ~89 datagrams a second is a property of the test topology. The
  size distributions are what transfers.
- **Nothing about a second server, a second map or a longer session.**
