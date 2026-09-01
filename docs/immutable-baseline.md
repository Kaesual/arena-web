<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Immutable prototype baseline

**Status:** WP0 complete — independently reviewed; all findings addressed.
Amended on 2026-08-30 twice: first to add the redistributed server runtime base,
then to move the engine pin from the unmodified upstream commit to the fork's
`web` branch carrying an enumerated patch series on top of it.

This document is the human-readable companion to
[`locks/baseline.json`](../locks/baseline.json). The lock is authoritative for
machine consumption; this document explains why each input exists and how a
reviewer obtains and verifies it.

## Pinned inputs

| Role | Version or revision | Immutable identity | Platform |
| --- | --- | --- | --- |
| Engine and bundled `baseq3` gamecode | ioq3 fork `596e56a6bf58f41e1ad9cc1685c7c11a75dba87a` (`web`), on upstream base `588393618dbc82e7207c21c6ddecca229944a03a` | Git commit and submodule pin | source |
| WebAssembly builder | Emscripten `6.0.8` | `sha256:8714ed3a9fb585e662c931259a996bac36a57a8dd34b81e8277436fd77364475` | `linux/amd64` |
| Native builder base | Ubuntu `24.04` | `sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316` | `linux/amd64` |
| Server runtime base | Debian `13-slim` (trixie, `13.6`) | `sha256:abc9cb88a5587630d7f915f47b23b0668fe250fbfc6457aa4d52b534c1bbf73f` | `linux/amd64` |
| Acceptance browser | Chrome for Testing `152.0.7977.64` (`r1669021`) | `sha256:8b592f066af71f054aab2cc80fc26f73c775c6d44ebb99d16ade924b24756c2e` | `linux64` |
| Acceptance desktop | Fedora Linux Workstation 44, GNOME | Fedora Workstation 44 release media recorded in the lock | `x86_64` |

The engine lock also binds the staged submodule branch metadata, now to `web`.
That move happened on 2026-08-30, with the explicit baseline and
published-schema reissue it was always going to require; see
"[The engine pin is a fork commit](#the-engine-pin-is-a-fork-commit-and-what-that-obliges-the-lock-to-say)"
below.

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
docker buildx imagetools inspect \
  docker.io/library/debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
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

## The engine pin is a fork commit, and what that obliges the lock to say

**Amendment of 2026-08-30.** The pin was an unmodified upstream commit, and the
lock could say nothing else: `engine.branch` was fixed to `main` and the record
had no way to express "this commit, minus an upstream base, is exactly these
changes". WP4's witnessed round then found a renderer defect that is an engine
defect — under a GLSL ES driver the renderer's fragment shaders were compiled at
`mediump`, which cannot hold the world-unit arithmetic they do, and lightmapped
surfaces came out saturated white. The diagnosis and the fix are in
[`wp1-build-evidence.md`](wp1-build-evidence.md); this section is about what the
lock had to grow to hold it.

At the time of this amendment the pin became
`92351b8f0543448b9defaac25c552274eecbf15b` on the fork's `web` branch. WP7
subsequently extended the enumerated series for the browser relay boundary,
network sizing and managed-relay rate buckets and reissued the baseline at the
current pin `596e56a6bf58f41e1ad9cc1685c7c11a75dba87a`. WP11 adds only the host-requested graceful-quit handoff needed by the embedding lifecycle. The validation model
described below did not change. `main` in that fork continues to mirror
upstream and is where the upstream base lives.

Two fields carry the claim, and they are meaningless apart:

- `engine.upstreamBase` names the commit and the public repository the series
  sits on — here `588393618dbc82e7207c21c6ddecca229944a03a` in
  `https://github.com/ioquake/ioq3`, which is upstream `main`'s head.
- `engine.appliedPatches` enumerates what the pin adds: an `id`, the exact set
  of tree `paths` the patch touches, a one-line `rationale`, and an
  `upstreamStatus` from a closed vocabulary.

`appliedPatches` is **present exactly when the pin is not the upstream base**.
An absent list is the claim "this is the unmodified upstream tree", and the
validator refuses an empty list as a second way of spelling it, just as it
refuses an absent list on a pin that does differ. The two states therefore have
one representation each, and neither can be reached by accident.

`upstreamStatus` records a fact about the patch's upstream relationship and
deliberately not an intention. Its only current value is `not-submitted`: no
upstream submission exists. The patch is written to be upstream-mergeable — that
is a property of how it is written, not a promise about what will happen to it —
and submitting it is an optional later step that is deliberately not scheduled,
because arena-web builds on Emscripten 6.0.8 while upstream's reference
toolchain is 3.1.58 and validating against that older toolchain is not work this
prototype wants. Adding a further status is a reviewed schema change, exactly as
adding a patch is a reviewed lock change.

None of that would be worth much as a declaration nobody checks, so
`scripts/metadata.py` binds it to the submodule offline: the upstream base must
be an ancestor of the pin, and the union of every record's `paths` must equal
`git diff --name-only <upstreamBase> <commit>` in `ioq3/`. An undeclared changed
path and a declared path that does not actually differ both fail. This check
needs the submodule's Git directory, so it sits beside the existing lock-to-pin
binding and is skipped with it under `--without-git-metadata`, which is the mode
the container check uses.

**Repository-local license evidence moves with the pin.** Every
`licenseComponents[].license` whose `evidenceIdentity` is a `git:` identity must
equal the pinned engine commit; that gate is unchanged, so those identities and
their `evidenceUrl`s moved to `92351b8f…` in the first amendment and now name
`596e56a6bf58…`. The alternative — letting such evidence stay at the base,
since the patch changes
none of it — would have given the gate two acceptable answers, and a gate with
two answers is a weaker gate. Pointing the evidence at the exact tree that is
built keeps one answer and stays true: `COPYING.txt`, every bundled
third-party notice and every per-file exception are byte-identical at the two
original amendment commits, because that amendment touched one renderer source
file and nothing else. The later enumerated network patches likewise touch no
licence-evidence path.
`upstreamEvidence.ioq3Commit` follows the same rule for the same reason; the
workflow file it cites is likewise byte-identical at the upstream base and all
named fork pins.

What this amendment does not do: it does not turn the engine into a place for
product features. The fork carries an enumerated series of small,
upstream-mergeable fixes and the lock has to name every one of them; anything
that could live in this repository still belongs in this repository.

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

WP1 has recorded that mapping in
[`wp1-build-evidence.md`](wp1-build-evidence.md), including its correction of
the provisional SDL and OpenAL roles. That document reports the observed
browser closure; this lock keeps describing the pinned source tree, and neither
the inventory nor the baseline identity changed.

## Native builder decision

The native builder begins from Ubuntu 24.04 rather than ioq3 CI's older Ubuntu
22.04 runner. The selected official-image index is
`sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517`;
its `linux/amd64` manifest is
`sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316`.
The manifest identifies its preferred-source revision as
`461fbe29535e51d03451ae146a90f730671d950d`.

This image is a build-only input and is never inherited by a distributed
client or server image. The separate runtime base it demands is the amendment
below. That still leaves WP5 to pin every additional package or builder layer
it introduces: this builder pin alone is not permission to use an unversioned
package repository during an accepted build.

WP5 did exactly that. The base carries no compiler, so
[`locks/native-toolchain-packages.conf`](../locks/native-toolchain-packages.conf)
pins the packages its toolchain image installs — one immutable
`snapshot.ubuntu.com` archive at an exact timestamp, and every package by name,
version, size and SHA-256 taken from that snapshot's GPG-signed index. An
accepted build resolves nothing and downloads nothing: the packages are fetched
and digest-verified first, and the image is assembled offline. That lock is
WP5's own, validated by `scripts/native_toolchain.py`, and it is not part of the
baseline identity; see [`wp5-packet-census.md`](wp5-packet-census.md).

## Server runtime base, and why WP0 had to be amended for it

**Amendment of 2026-08-30.** WP5 stopped before implementation because its
scope requires the dedicated server's runtime base to be pinned "with its
distribution and preferred-source obligations recorded", and this baseline
could not express that record. Every `tools[]` entry is validated as a
non-distributed build or test tool: it must carry a registered tool-only
`LicenseRef` *and* a distribution value that keeps it out of any artifact. A
base image that ships inside a server image is the opposite of that, so every
honest attempt to record one was rejected — not by policy, but because the
lock had no record type for an image arena-web hands on. That is a WP0 gap,
and the amendment closes it rather than letting WP5 invent a second license
gate beside this one.

`redistributedProductImages` is that record type, and it is a third license
class, not a relaxation of the two that existed. A product input is compiled or
packaged into an arena-web artifact and must use an allowed product expression.
A tool never leaves the workstation and must use a registered tool-only
reference. A redistributed product image is neither: arena-web ships its
third-party binaries unchanged, so the record must additionally bind its
license evidence to the exact image bytes, name what redistributing them
obliges, and name the channel their complete corresponding source is obtainable
from. The three `LicenseRef` registries are required to be disjoint, so a
reference admitted for one class can never satisfy another, and the two
populations may not overlap by identity either: a record here that reused the
digest or reference of an image `tools[]` pins as build-only is rejected, since
that image was reviewed on the promise that it never reaches a distribution.

The reference this class registers is `LicenseRef-Debian-Image-Aggregate`. It
denotes exactly one thing: the aggregate of the per-package Debian copyright
files the pinned image carries. It is not a claim that the image is under one
license, and it is not usable as a product-input or tool-only reference.

The selected base is Debian `13-slim` (trixie, `13.6`). The dedicated server
target compiles with `DEDICATED`/`BOTLIB` and the null client stubs and links
only `${CMAKE_DL_LIBS}` and `m` — no SDL, no GL — so the runtime needs little
more than glibc, and a slim Debian is the smallest honest base that still
carries its own license evidence. It is deliberately not the Ubuntu 24.04
builder: that image is build-only and must not be inherited by anything
distributed. Its glibc `2.41-12+deb13u3` is newer than the builder's `2.39`, so
a binary built on the builder runs on it; the reverse would not hold and is not
what the pin claims.

The selected official-image index is
`sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132`; its
`linux/amd64` manifest, and the actual runtime reference, is
`sha256:abc9cb88a5587630d7f915f47b23b0668fe250fbfc6457aa4d52b534c1bbf73f`. The
index entry for that member identifies its preferred-source revision as
`bae6d64d90b4068b09ff9d8b564c2773ef5d8d83` in
`https://github.com/debuerreotype/docker-debian-artifacts.git`, which is the
preferred form of the image *build*. The preferred form of the *binaries* is a
different thing and is recorded separately, in `correspondingSource`: the image
is built from the Debian snapshot `20260824T000000Z`, whose sources are
published at
`https://snapshot.debian.org/archive/debian/20260824T000000Z/`. That timestamp
is not inferred — the image states it in
`/etc/apt/sources.list.d/debian.sources` and in its own build record
(`debian.sh --arch 'amd64' out/ 'trixie' '@1787529600'`, debuerreotype 0.17).
That same file names a second archive at the same timestamp,
`debian-security`. The record's one URL is the primary archive, and naming it
is deliberately not a claim that every one of the 78 packages resolves there —
which is a further reason the written offer below is recorded beside it rather
than instead of it.

The license evidence is the evidence the image itself carries, and the
validator requires exactly that: the record's `license.evidenceIdentity` must
equal its `platformDigest`, and it must name an `evidencePath`. Here that path
is `usr/share/doc`, where each of the 78 packages carries its Debian copyright
file; the license texts those files refer to are in the same image under
`/usr/share/common-licenses`. For this class `evidenceUrl` is therefore a
locator rather than the binding evidence, and it is required to be a pinned
one: it names the commit-fixed `docker-library/repo-info` record at
`cae117d8c88cf4f36d2e284cb10fc1a8851f18bc`, which documents `debian:13-slim`
as exactly this index and `linux/amd64` digest — the same convention the Ubuntu
builder record uses. A reviewer verifies the whole claim without trusting this
document:

```bash
podman pull docker.io/library/debian@sha256:abc9cb88a5587630d7f915f47b23b0668fe250fbfc6457aa4d52b534c1bbf73f
podman run --rm --network none \
  docker.io/library/debian@sha256:abc9cb88a5587630d7f915f47b23b0668fe250fbfc6457aa4d52b534c1bbf73f \
  bash -c 'cat /etc/os-release
           echo "packages:  $(dpkg-query -W | wc -l)"
           echo "copyright: $(ls /usr/share/doc/*/copyright | wc -l)"
           ls /usr/share/common-licenses
           grep snapshot /etc/apt/sources.list.d/debian.sources'
```

The expected output is Debian 13 (trixie), `13.6`, 78 packages, 78 surviving
`copyright` files — the slim variant strips documentation but keeps those — the
`/usr/share/common-licenses` texts they refer to, and the snapshot
`20260824T000000Z` the corresponding-source record names.

Two obligation sets are recorded rather than assumed. Redistributing the
binaries obliges `license-notice` and `preserve-copyright-files`: the server
image WP5 builds may not strip `/usr/share/doc/*/copyright` out of the base it
inherits. The copyleft packages among them oblige
`complete-corresponding-source` and `public-archive-availability`, and the
record adds `written-offer-on-request` on purpose. `snapshot.debian.org` is
Debian's public archive, not one arena-web operates, and for the GPL-2.0-only
packages in the base a distributor's §3 options are to accompany the source,
make its own written offer, or pass along an offer it received — pointing at
somebody else's archive is none of the three. The archive is therefore recorded
as the primary channel and the written offer as arena-web's own backstop; the
concrete discharge decision belongs to the WP5 server-image record, when an
image is actually distributed. Both vocabularies are closed and both required
sets are enforced, so an under-declared record fails rather than passing
quietly.

Those obligations are declared, not derived, and the difference matters. In
content provenance the required obligations follow from the license expression,
because the expressions there are real SPDX identifiers. Nothing can be derived
from an opaque aggregate reference, so for this class the closed vocabulary and
the required sets are a machine-enforced floor only: they stop a record from
claiming less than the minimum, and the honesty of what it does claim rests on
the review that admits the reference in the first place.

What this amendment does not do: it pins one runtime base and nothing else. It
does not build, contain or bless a server image, it adds no package on top of
the base, and it is not permission to install one from an unversioned
repository. Only the recorded baseline identity moved — the WP1 browser build
and the WP3 content assembly were re-run against the amended lock and produced
byte-identical artifacts, because neither consumes this entry. A reissue like
that keeps the original `producer.commit` in each record: the producing commit
and its build did not change, only the identity of the baseline they bind to.

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
  code, its upstream base and applied patch series, builder, browser, OS,
  redistributed-runtime-image, license-policy and trust inputs;
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
ioq3 lock-to-public-submodule/index-gitlink/clean-checkout binding, the
enumerated engine patch series against the real submodule diff, and
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
use only registered tool references and their exact distribution boundary. A
redistributed product image may use only a reference registered in
`redistributedImageLicenseRefs`, must declare the redistribution boundary
rather than either of the other two, must bind its license evidence to its own
platform digest and name the path carrying it, must name an obtainable
preferred source, and must declare its redistribution and corresponding-source
obligations from closed vocabularies. SHA-256 evidence must carry a real,
non-future retrieval date. An unknown license, missing digest or unexplained
unavailable preferred source fails closed.

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
# sha256:cc45026e109df38a3b019192ed8b6807bae8bc787119b2b89fe5c7a6f28c05f1
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
