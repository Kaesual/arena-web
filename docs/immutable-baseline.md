<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Immutable prototype baseline

**Status:** WP0 complete — independently reviewed; all findings addressed

This document is the human-readable companion to
[`locks/baseline.json`](../locks/baseline.json). The lock is authoritative for
machine consumption; this document explains why each input exists and how a
reviewer obtains and verifies it.

## Pinned inputs

| Role | Version or revision | Immutable identity | Platform |
| --- | --- | --- | --- |
| Engine and bundled `baseq3` gamecode | ioq3 `588393618dbc82e7207c21c6ddecca229944a03a` | Git commit and submodule pin | source |
| WebAssembly builder | Emscripten `6.0.8` | `sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475` | `linux/amd64` |
| Native builder base | Ubuntu `24.04` | `sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316` | `linux/amd64` |
| Acceptance browser | Chrome for Testing `152.0.7977.64` (`r1669021`) | `sha256:8b592f066af71f054aab2cc80fc26f73c775c6d44ebb99d16ade924b24756c2e` | `linux64` |
| Acceptance desktop | Fedora Linux Workstation 44, GNOME | Fedora Workstation 44 release media recorded in the lock | `x86_64` |

The engine lock also binds the staged submodule branch metadata to `main`.
Moving product engine work to `web` therefore requires an explicit baseline
and published-schema reissue together with the new public commit and gitlink.

OCI references used by builds end in the platform-manifest digest, not a tag.
The corresponding multi-platform index digests are retained in the lock so a
reviewer can verify how the platform manifest was selected. A tag such as
`6.0.8` or `24.04` is descriptive only.

The index-to-platform mapping is independently inspectable with:

```bash
docker buildx imagetools inspect \
  docker.io/emscripten/emsdk@sha256:f174124ff798a3ead1abef247d9a849c270b642d552fea500a42565ff210f765
docker buildx imagetools inspect \
  docker.io/library/ubuntu@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
```

## Emscripten upgrade decision

The pinned ioq3 workflow uses `mymindstorm/setup-emsdk@v13` with Emscripten
`3.1.58`. The action's moving major reference cannot be an arena-web input. Its
version is retained as upstream evidence, but it is not the selected builder.

The prototype deliberately targets Emscripten `6.0.8`. This keeps the new port
on a current official SDK generation instead of beginning on a compiler more
than two years older, and it aligns with the independently maintained browser
engine build that motivated this project. This is a compatibility claim to be
proved, not assumed: WP1 must produce two clean, byte-identical builds with
`6.0.8`. A failure stops for a reviewed compatibility decision. It does not
silently select `3.1.58` or patch ioq3 as an incidental workaround.

The first compatibility audit covers ioq3's current `-sUSE_SDL=2`, exported
`FS`/filesystem and ES-module/modularization settings explicitly. Those are
known version-sensitive surfaces, not authorization to change them before a
clean `6.0.8` build demonstrates a problem.

The official `emscripten/emsdk:6.0.8` index is
`sha256:f174124ff798a3ead1abef247d9a849c270b642d552fea500a42565ff210f765`.
Its `linux/amd64` member, and the actual build reference, is
`sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475`.
The preferred source tag resolves to emsdk commit
`e5bd3d0874e302a18f13c5b41f5bacf9a40c8e59`.

## Engine license inventory

The ioq3 Git input is intentionally not assigned one blanket license. Its core
and bundled `baseq3` game code are GPL-2.0-or-later, while the reviewed source
tree also contains separately licensed third-party directories, prebuilt native
SDL libraries, build tooling and individual source-file exceptions.
`engine.licenseComponents` records the closed WP0 inventory, each role, exact
covered paths, expression and pinned evidence. The `ioq3-core` record covers
`code/` only after excluding the sorted union of every other component path;
the validator requires that equality and rejects duplicate or overlapping
exception paths. This prevents a broad GPL path from silently swallowing a
more specific license. The offline validator additionally requires every
recorded path to exist and binds the lock's exact top-level `code/thirdparty`
and `code/tools` entry sets to this reviewed checkout, so a later pin cannot
add a bundled library or tool while retaining the old inventory unchanged.

The explicit exceptions include IJG, Xiph and Opus codec sources, zlib and
Info-ZIP/minizip code, SDL and curl inputs disabled by Emscripten, OpenAL
headers, native-only SDL binaries, browser-compiled Mumble Link, Puff, the HPND
ADPCM coder, the BSD/custom QVM libc file and two public-domain declarations.
The updater translation unit is compiled, but its implementation is disabled
by its unsatisfied feature guard. The official IJG 9f archive is pinned as
`sha256:04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b`;
its `jpeg-9f/README` member is the license evidence, rather than the bundled
`jpeglib.h` that merely refers to a missing README.

`code/tools/lcc` is different: its custom 1998 terms include commercial-use
restrictions, so `LicenseRef-LCC-1998` is registered only as a QVM build tool.
Its source remains visible through the pinned public ioq3 submodule, but it is
not an allowed browser/native product input and no lcc source or executable may
enter a product artifact. WP1 must record whether the accepted QVM build uses
it and keep that legal boundary explicit before any release decision.

The product-input allowlist admits only the expressions and reviewed custom
public-domain/notice references actually recorded by the lock. It remains a
gate, not a compatibility conclusion. WP1 must map every component to the real
compile/link closure, confirm or correct the provisional SDL/OpenAL roles,
inventory runtime code supplied by the pinned Emscripten SDK, and package every
applicable notice and corresponding-source obligation. A component absent from
the final link may be reported as absent; it may not be erased from the source
inventory.

## Native builder decision

The native builder begins from Ubuntu 24.04 rather than ioq3 CI's older Ubuntu
22.04 runner. The selected official-image index is
`sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517`;
its `linux/amd64` manifest is
`sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316`.
The manifest identifies its preferred-source revision as
`461fbe29535e51d03451ae146a90f730671d950d`.

This image is a build-only input and is never inherited by a distributed
client or server image. WP5 must select and pin a separate runtime base, record
its redistribution and preferred-source obligations, and pin every additional
package or builder layer that it introduces. This builder pin alone is not
permission to use an unversioned package repository during an accepted build.

## Browser and desktop acquisition

Chrome for Testing is a test tool, not a shipped product component. The exact
archive is:

```text
https://storage.googleapis.com/chrome-for-testing-public/152.0.7977.64/linux64/chrome-linux64.zip
size:   194030544 bytes
sha256: 8b592f066af71f054aab2cc80fc26f73c775c6d44ebb99d16ade924b24756c2e
```

A reviewer can verify and run it with:

```bash
curl --fail --location --output chrome-linux64.zip \
  https://storage.googleapis.com/chrome-for-testing-public/152.0.7977.64/linux64/chrome-linux64.zip
printf '%s  %s\n' \
  8b592f066af71f054aab2cc80fc26f73c775c6d44ebb99d16ade924b24756c2e \
  chrome-linux64.zip | sha256sum --check
unzip chrome-linux64.zip
./chrome-linux64/chrome --version
```

The required version output is `Google Chrome for Testing 152.0.7977.64`.
Chrome for Testing is used only with trusted prototype content and is never
copied into an arena-web distribution.

Its Chrome-specific terms are recorded as test-tool evidence, including the
SHA-256 and retrieval date of the document fetched on 2026-08-29. The URL is a
moving terms page, so the date and digest are evidence of that exact retrieval,
not a claim that a future fetch must have the same bytes. The Ubuntu evidence
is a commit-pinned Docker Official Images record that contains the selected
index and `linux/amd64` manifest. These aggregate `LicenseRef-*` records
describe non-redistributed build/test inputs only; they are not product-input
licenses. The separately recorded lcc build-tool exception is documented in
the engine inventory above.

Manual prototype acceptance uses Fedora Linux Workstation 44, GNOME, on
`x86_64`. The exact release medium is:

```text
https://download.fedoraproject.org/pub/fedora/linux/releases/44/Workstation/x86_64/iso/Fedora-Workstation-Live-44-1.7.x86_64.iso
size:   2851612672 bytes
sha256: 1620295f6a00c27c3208f0c00b8ece4eab1ec69b9002152d97488bf26a426ddf
```

The lock also pins Fedora's OpenPGP-clearsigned release CHECKSUM document as
`sha256:023909e4d2e4521390f61fcfb863588bed6122362a546265ae19374a08a705ad`.
Fedora is an acceptance-only aggregate and is never redistributed by this
project. Its aggregate license record and unavailable single preferred-source
form make that boundary explicit rather than silently treating an operating
system ISO as one product input.

Reviewers do not need bit-identical hardware, but results must record graphics
device, driver and display refresh rate because those can materially affect
browser rendering and frame pacing.

## Metadata contracts

All committed JSON under `locks/`, and later under `manifests/` or
`provenance/`, is strict UTF-8, duplicate-key-free, sorted and two-space
indented. Unknown top-level or record fields fail validation. The supported
formats are:

- [`baseline-lock.schema.json`](../schemas/baseline-lock.schema.json): exact
  code, builder, browser, OS, license-policy and trust inputs;
- [`relay-measurement-vector.schema.json`](../schemas/relay-measurement-vector.schema.json):
  game-neutral WP2 sizes and framing constants;
- [`artifact-manifest.schema.json`](../schemas/artifact-manifest.schema.json):
  sorted paths, sizes and SHA-256 identities for generated build artifacts;
- [`content-provenance.schema.json`](../schemas/content-provenance.schema.json):
  preferred source, transformation, member identity, license and notice data
  for every generated content member.

The JSON Schemas are the interchange description. The dependency-free Python
validator executes the schema subset used here and rejects an unsupported
schema keyword, so the published schemas cannot silently become decorative.
Automated test fixtures execute all four published schemas, including artifact
and content formats that do not yet have production instances. The validator
then adds semantic checks that JSON Schema alone does not express well: OCI
`image@platformDigest` agreement, immutable source identities, license policy,
preferred-source completeness, normalized paths, sorted unique records, the
ioq3 lock-to-public-submodule/index-gitlink/clean-checkout binding and
cross-record references. Artifact manifests and content provenance name the
SHA-256 identity of the baseline they use; declared baseline inputs must match
its exact kind and identity. Any byte change to `locks/baseline.json` creates a
new whole-file
identity: every dependent manifest or provenance record must be reissued
rather than relabeling an old artifact with the new identity. Content members
carry a role, per-member license expression and explicit packaging
obligations; every non-notice member must bind those obligations to at least one
packaged notice member, and every notice path must resolve to a declared member
whose role is `notice`.

Product inputs must use an expression in `productInputAllowedExpressions`; any
custom reference in that expression must also be registered in
`productInputLicenseRefs`. The allowlist is a gate, not a claim that any two
listed inputs are automatically compatible; WP3 must review the actual
combination and distribution obligations. Non-product build and test tools may
use only registered tool references and their exact distribution boundary.
SHA-256 evidence must carry a real, non-future retrieval date. An unknown
license, missing digest or unexplained unavailable preferred source fails
closed.

## Relay trust and measurement vector

Routed rehearsal uses WebTransport's `serverCertificateHashes` mechanism with
SHA-256 and an ECDSA P-256 certificate whose total validity is at most 14 days.
The current certificate hash is runtime configuration: no endpoint, certificate
or fingerprint is committed. This avoids an undocumented machine-wide trust
store change while preserving an explicit browser-visible trust decision.

The WP2 vector is committed in
[`relay-measurement-vector.json`](../locks/relay-measurement-vector.json). It
tests both directions with identical sizes. It has useful resolution from 512
through 1,298 bytes, including ten-byte steps from 1,200 through 1,290,
adjacent values around 1,300, 1,307, 1,309, 1,312 and
1,314 bytes, and explicit cases at and above 1,400 through ioq3's 16,384-byte
message bound. The latter record failure behavior for the prototype's expected
packet domain and prevent the later connectionless-packet decision from silently
inheriting a netchan assumption; ioq3's larger internal out-of-band scratch
buffer is not itself a promise that such datagrams are valid protocol traffic.

The ioq3 boundaries come from the pinned engine. An unfragmented payload tops
out at 1,299 bytes: its server header is 8 bytes and its client header is 10,
giving 1,307 and 1,309 bytes. A 1,300-byte fragment adds the two 2-byte fragment
fields, giving 1,312 and 1,314 bytes. Relay framing adds a 40-byte header and a
2-byte length prefix, so a 1,314-byte inner datagram occupies 1,356 bytes before
the browser transport's own overhead.

Packed cases are explicitly browser-to-server only because the reverse frame
contains exactly one datagram. Every packed inner datagram is at least 16 bytes
and therefore carries its complete payload-prefix nonce. The intentionally tiny
single-datagram 0- and 1-byte framing cases run sequentially and are not used
as concurrent-session isolation evidence. WP2 measures what the routed path
accepts; the vector does not pre-decide WP6's sizing strategy.

## Validation

The fast local check needs Python 3 and no third-party package:

```bash
scripts/check.sh
```

The whole-document identity currently used by later manifests is independently
inspectable with:

```bash
sha256sum locks/baseline.json
# sha256:d565905280ac8575ad2798d4e1cd5cabb18c694a4b6b192957c48a41c416f039
```

The container check first verifies the lock against the host checkout's ioq3
gitlink, then runs the same metadata/schema validator and tests inside the
pinned Emscripten platform image with `--network none` and `--pull never`:

```bash
scripts/check-container.sh
# or: CONTAINER_RUNTIME=podman scripts/check-container.sh
```

The container image must therefore be obtained explicitly before the command is
run. Its immutable reference is printed without executing a container by
`scripts/check-container.sh --print-image`; pull that exact output with the
chosen container runtime. Neither validation command builds ioq3 or downloads
game content.
