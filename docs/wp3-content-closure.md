<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP3 evidence: audited minimal-content closure

**Status:** WP3 complete — two clean assemblies accepted, byte-identical

This document records what the arena-web content pack is: how its members were
selected, which upstream inputs they come from, how their licences were
verified per file rather than per repository, what the static closure checks
prove, and what a distributor of this pack owes.

The pack itself is not committed. Its identities are, in
[`provenance/arena-web-ffa-content.json`](../provenance/arena-web-ffa-content.json)
(every member, with source, licence, obligations, notice binding and digest)
and
[`provenance/arena-web-ffa-content-manifest.json`](../provenance/arena-web-ffa-content-manifest.json)
(the PK3's own identity and the inputs that produced it).

`ioq3/` is untouched at its pinned commit. No baseline, schema, WP1 build
script or committed WP1 manifest was changed.

## The selected profile

| Item | Selection |
| --- | --- |
| Package | `arena-web-ffa-oa_pvomit` |
| Archive | `baseq3/arena-web-ffa.pk3`, `sha256:00bdfea142756e934049743d04e12e09eac0fde4b48ffc2fb00012520e75d9be`, 24,181,170 bytes |
| Members | 668 (660 assets, 6 notices, 2 generated metadata), 53.17 MB uncompressed |
| Map | `oa_pvomit`, "Projectile Vomit", 16 deathmatch spawns, with its `.aas` bot navigation |
| Player presentation | `skelebot/default`: `lower`/`upper`/`head` MD3s, skins, `animation.cfg`, icon and its complete `sound/player/skelebot` voice set |
| Bots | Skelebot, Rai and Sly, all using the one packaged presentation |
| Gamecode | none — the pack targets the pinned ioquake3 `baseq3` QVMs from WP1 |

The pack contains no `.qvm`, no `vm/` directory, no OpenArena `glsl/` engine
programs and no native code; `scripts/content_pack.py` refuses to package any
of them and the committed provenance is tested against the same rule.

## Why these inputs

### OpenArena, obtained through Debian's cleaned source packages

`initial-plan.md` names the Debian-cleaned OpenArena 0.8.8 packages and the
OpenArena Community Mappack Volume 1 as starting candidates and requires their
per-file licensing to be verified rather than assumed. Debian splits that
material into several source packages, each built from an `orig` tarball that
Debian derived from the upstream ZIP releases by removing the engine and the
non-free files, unpacking the PK3s, and **adding the upstream asset sources**
where the packaged file is not the preferred form for modification. Their
`debian/README.source` documents that derivation step by step.

That makes the Debian tarballs a better input than the upstream ZIPs for two
independent reasons: the non-free material is already excised under a public
review, and the preferred source form travels in the same digest-pinned
artifact as the packaged asset.

Six of those source packages are used:

| Recipe source | Debian source package | Upstream PK3 it carries | Precedence |
| --- | --- | --- | --- |
| `openarena-data` | `openarena-data` 0.8.5split-17 | `pak0` — core art, sounds, item and weapon models, menu art, shaders | 10 |
| `openarena-maps` | `openarena-maps` 0.8.5split-17 | `pak1-maps` — maps and level shots | 20 |
| `openarena-players` | `openarena-players` 0.8.5split-17 | `pak2-players` — player models and player voices | 30 |
| `openarena-textures` | `openarena-textures` 0.8.5split-17 | `pak4-textures` — world textures and sky boxes | 40 |
| `openarena-misc` | `openarena-misc` 0.8.5split-17 | `pak5-TA`, `pak6-misc` — bot files, `.aas` navigation, feedback sounds | 50 |
| `openarena-088-data` | `openarena-088-data` 0.8.8-17 | `pak6-patch088` — the 0.8.8 patch content | 60 |

Precedence reproduces OpenArena's own PK3 load order, so a file that the 0.8.8
patch replaces is taken from the patch and not from `pak0`.

`openarena-085-data` (`pak6-patch085`) is deliberately **not** an input. The
closure resolves completely without it, and every additional archive is another
110 MB download and another provenance surface. The consequence is recorded
honestly: this pack is not byte-compatible with an OpenArena 0.8.8 pure server,
which the prototype plan already excludes from scope.

The OpenArena Community Mappack Volume 1 (`openarena-oacmp1`) was not needed:
the selected map and its dependency closure come from the base release, and
adding a second map source would enlarge the audit without changing the
profile.

### Per-file licence verification

Debian's machine-readable `debian/copyright` for each of these source packages
declares `Files: *` as `GPL-2+`, with exactly two narrower stanzas across the
whole set:

- `*/models/players/merman/*` and `source/assets/models/merman/*` — GPL-2 only;
- `source/assets/maps/oa_thor.*` and `pak6-patch088/maps/oa_thor.*` — GPL-2 only.

That is a per-file review by a distribution that has to defend it, not a
blanket repository claim, and it was cross-checked against the material itself:
the tarballs carry the GPL-2 text as `COPYING`, the OpenArena `README` states
the release terms, the `source/assets/maps/credits` file names the author of
every map, and the bot character files under `botfiles/bots/` each carry an
explicit per-file "GNU General Public License … either version 2 … or (at your
option) any later version" header.

The two GPL-2-only items are excluded from the pack by selection, so **every
packaged member is `GPL-2.0-or-later`**, which is on WP0's
`productInputAllowedExpressions` list. This is enforced, not assumed:
each recipe source declares its `nonDefaultLicensePaths`, and the assembly
aborts if the closure ever selects a member matching one of them. The
`merman` player model and the `oa_thor` map would each stop the build today.

Licence evidence, retrieved 2026-08-29:

| Debian source package | Version | `.dsc` (signed record of the tarball digests) | Packaging tarball containing `debian/copyright` |
| --- | --- | --- | --- |
| `openarena-data` | 0.8.5split-17 | `sha256:8fc811cd16c30a24c6d47b282ac342035bc45b25ae0beef9b5c1fd66c9f9e6d3` | `sha256:02de547e909a74b9f0e91d2e02734f89bb26d8626ea11ec5d47cc738c0540ba3` |
| `openarena-maps` | 0.8.5split-17 | `sha256:ed1698a04d06f49aa0681a04a533e8e8eac00822251bf2ce1794814803fc8d74` | `sha256:efb941c25f4c0a7e2b632ab81a434ebc6c3a6bb3350d9be6e8eb4d755112c06b` |
| `openarena-players` | 0.8.5split-17 | `sha256:fdde2fc2737c296780f0c8507c6c984ddda468298b6db8d18cef577cd8d30fa3` | `sha256:98bf73a8fe009a9e81baa34ed861214d506c430e11e017b5e185545f972f4c49` |
| `openarena-textures` | 0.8.5split-17 | `sha256:1b0119a9a34e8bdcf670e05c059e3ced77a2e1a17fb9469309fb01bfb7e31c04` | `sha256:fb2be735f38f3bad53dae3fd7495fa8f2c67712166dd55c044c9bd1cbe55b8fb` |
| `openarena-misc` | 0.8.5split-17 | `sha256:7c893e83979efe9d355d72748bf543f33693a6366e43e29b35c3b26ea250977d` | `sha256:75d7d82acc788a75b3408706f37ecd77366efb4de5d3c643ff5985376be928ae` |
| `openarena-088-data` | 0.8.8-17 | `sha256:2f95e0aa0c6618e2b7c264261f8cecab429ffaf9ae0d17b57ae815b912abb7b7` | `sha256:45b4c55d379f114925f6db793ff75b9ec7a547e469728d3b9caf157b2bad997f` |

The `debian/copyright` documents themselves are identical across
`openarena-data`, `openarena-maps`, `openarena-misc` and `openarena-textures`
(`sha256:1dce40781117bdd291ef6234ac2173567b9f6c3aea0d3bd86f145d89f5a653a0`);
`openarena-players` adds the `merman` stanza
(`sha256:e22cfa50084d44a44e7aa4d63afb656df79e58215325b336eaafe498e225e327`) and
`openarena-088-data` the `oa_thor` stanza
(`sha256:a9d9c75e0651d776c43d4c7586422c8f98334cc732c4eb3e09f4c3ab659f8f4b`).

### Immutable identities of the content archives

Every input is pinned by exact archive plus digest and fetched from
`snapshot.debian.org`, whose `/file/<sha1>` endpoint is content-addressed. Each
digest below was additionally confirmed against the `Checksums-Sha256` field of
the corresponding signed Debian `.dsc`, so the pin is not merely "what was
downloaded once".

| Archive | Size | SHA-256 |
| --- | --- | --- |
| `openarena-data_0.8.5split.orig.tar.bz2` | 91,071,346 | `66c7bcf44022ce96331c15718e7145d5367aa23bff95e2fce7abfc844ae6e588` |
| `openarena-maps_0.8.5split.orig.tar.bz2` | 41,711,739 | `f9922a8e080e5f1de3e190deb355f50f8ca1700dcbe28aab88be07e6a45dea60` |
| `openarena-players_0.8.5split.orig.tar.bz2` | 104,856,904 | `7e443d7171414ae97514209e2512388d616dafbaadfdcf63204821491ffcb5c6` |
| `openarena-textures_0.8.5split.orig.tar.bz2` | 449,360,566 | `40229bb6852675e29684817af80b66a839c89b7062043ff3df5f3f420846bb0a` |
| `openarena-misc_0.8.5split.orig.tar.bz2` | 37,021,328 | `ba65afb212c6909e974b32d9f0b2696e33c579bf63e44e1d9320c7b183f00c6c` |
| `openarena-088-data_0.8.8.orig.tar.bz2` | 189,161,133 | `66db4a12ed575f4fee8eb2171acdd925c105e3b49570a12baf99968d13d302f0` |

The archives live under the gitignored `build/content-sources/` and are never
committed. `content/pack-recipe.json` carries the exact URL, size and digest of
each, and both the fetch script and the assembly re-verify size and digest
before any byte is read.

### Why this map

Every candidate below is `GPL-2.0-or-later` and ships a Radiant `.map` source
in the same digest-pinned tarball, so criterion 1 (verified licence and
obtainable preferred source form) does not separate them. The remaining
criteria did.

| Candidate | Closure | Why not selected |
| --- | --- | --- |
| **`oa_pvomit`** | **652 members, 53.09 MB** | **selected** |
| `q3dm6ish` | 651 members, 50.03 MB | Smallest closure, but a conversion of a third-party Quake 1 map by another author, so its authorship chain runs through material OpenArena's own release terms cover only indirectly. Provenance ranks above closure size. |
| `dm4ish` | 650 members, 50.90 MB | Same second authorship chain as `q3dm6ish`, and a larger closure. |
| `fan` | 656 members, 50.56 MB | Comparable size, but its textures are scattered over nine shared texture directories rather than one map-specific set, and it has 8 spawn points to `oa_pvomit`'s 16. |
| `islanddm` | 658 members, 60.60 MB | Terrain-blend texture set makes it the largest of the small maps, and 6 spawn points. |
| `oa_dm7` | 667 members, 52.70 MB | 4 deathmatch spawns: a duel map, not an FFA-with-bots map. Also a Quake 1 conversion. |
| `oa_dm1` | 685 members, 54.53 MB | Larger closure across `gothic_*` and `base_*` texture sets. |
| `oa_shouse` | 683 members, 55.34 MB | By far the largest per-map shader set of the small maps. |

These closures are measured with the accepted extractor and the accepted
resolution rules, one bot character each and the generated members excluded, so
they are comparable to each other but sit slightly below the 668 members of the
assembled pack.

`oa_pvomit` is level design by Henke "Stjartmunnen" Björk, converted for
OpenArena by dmn_clown, credited in `source/assets/maps/credits`, with author
notes in `source/assets/maps/oa_pvomit.txt` that record it as an FFA map with
bot support. Its world textures live almost entirely in the map-specific
`textures/mc-oa-dm02` and `textures/mc-oa-dm04` directories the same author
made, which is why its texture contribution to the pack is 0.4 MB. It costs
about 3 MB (6.5%) more than the smallest candidate and buys a single, credited
authorship chain.

### Why this player presentation

Only models that ship a `source/assets/models/<name>/` preferred source form
**and** a `botfiles/bots/<name>_c.c` character were considered, which excludes
`sarge` and `tony` (no source tree) and `assassin` (no bot character).
`merman` is excluded by licence. Of the rest:

| Candidate | Closure | Dangling references inside the model |
| --- | --- | --- |
| **`skelebot`** | **53.09 MB** | **none** |
| `penguin` | 51.61 MB | `/home/niko/Desktop/penguinblend/lambert2SG` |
| `liz` | 52.06 MB | three absolute `/home/ross/Desktop/...` material paths |
| `major` | 52.79 MB | `models/players/majorlegs`, `models/players/majortorso` |
| `beret` | 52.79 MB | `models/players/beret/spec_skin1.tga` and a stray material name |
| `gargoyle` | 54.51 MB | none, but a larger closure |
| `kyonshi` | 55.52 MB | `models/players/kyonshiMaterial.001` |

`skelebot` is the only candidate whose MD3 surfaces and skins resolve
completely, and it has the fewest unresolved references of the whole set. Its
two remaining artefacts (`models\players\skelebot\Material`,
`...\Material.005`) are backslash material names inside the MD3s, which the
engine answers with its default shader and which cannot name a packaged file at
all; they are reported separately from missing assets rather than hidden.

## How the member set is derived

The pack is a closure, not a list. Roots:

1. **What the pinned `baseq3` QVM sources name, by two readings.**
   `scripts/qvm_references.py` reads `cmake/basegame.cmake` and takes the exact
   translation units it compiles into `cgame`, `qagame` and `ui` — note that
   `baseq3`'s menu is `code/q3_ui`, not the missionpack `code/ui` — follows
   their local `#include`s, and drops the `MISSIONPACK` branches, which those
   QVMs are not built with. A preprocessor condition the evaluator cannot
   decide keeps **both** branches, and an unterminated conditional is an error,
   so the extracted set is a superset of the compiled text rather than a
   silently truncated one.

   Two readings run over that text. The first collects **path-shaped string
   literals** under a known asset directory: 379 literals and 40 format
   templates. The second collects the **first argument of every
   content-registration trap** — `trap_R_RegisterShader`,
   `trap_R_RegisterShaderNoMip`, `trap_R_RegisterModel`, `trap_R_RegisterSkin`,
   `trap_S_RegisterSound` — regardless of shape, because a shader script may
   define a name that is not a path at all: 207 names and 6 templates. The trap
   says which kind of reference it is.

   The second reading exists because the first is not sufficient, which an
   independent review found: `white`, `menuback`, `powerups/quad`,
   `viewBloodBlend`, `smokePuff`, `lagometer`, the `medal_*` family and 34
   other registered names have no directory prefix and were being discarded
   before they reached the closure.

   **What this does not cover.** Only these five traps are read, and only their
   first argument. A reference the gamecode composes at runtime from data the
   sources do not contain — a cvar, a server-set model name, a downloaded
   configuration — is outside any static reading, and so is any content a
   *map* asks for that its BSP does not name.
2. **The declared expansion of every template.** A template such as
   `sound/player/footsteps/step%i.wav` cannot be resolved statically, so
   `content/pack-recipe.json` states either its expansion (footsteps 1–4 from
   `cg_main.c`, crosshairs `a`–`j` from `NUM_CROSSHAIRS`, the profile map) or
   the reason it expands to nothing. The assembly fails if the QVMs use a
   template the recipe does not mention, and fails again if the recipe mentions
   one the QVMs do not use, so the list cannot rot in either direction.
3. **The map**: BSP shader lump, plus entity `model`, `noise` and `music` keys,
   plus its `.aas` and level shot.
4. **The player presentation**: MD3s, `.skin` files, `animation.cfg`, icon, and
   the model's own and its `sex`-derived voice directories.
5. **The bots**: `botfiles/` defaults plus each selected character, expanded
   through `#include` and `CHARACTERISTIC_*` file references — resolved from
   `botfiles/` itself, because `botlib` calls `PC_SetBaseFolder("botfiles")`.
6. **The packaged notices.**

Resolution uses the engine's own rules, read out of the pinned checkout:

- images try the reference's own extension first and then `tga`, `jpg`,
  `jpeg`, `png`, `pcx`, `bmp`, `pvr` against the stripped name
  (`R_LoadImage`);
- sounds likewise try `wav`, `ogg`, `opus` (`S_CodecGetSound`, whose codec list
  is built by prepending);
- a drawn reference is looked up as a shader **with its extension stripped**
  and only falls back to an image of the *original* name (`R_FindShader`);
- shader definitions are indexed across every `scripts/*.shader` in the source
  set with reverse-alphabetical file precedence, and
  only the 26 shader files that actually win a needed definition are packaged;
- MD3 surface shaders, `.skin` surface assignments and BSP shader lump entries
  are followed transitively.

The shader precedence deserves a footnote, because the obvious reading of
`ScanAndLoadShaderFiles` is wrong. ioquake3 does **not** sort that list:
`FS_ListFiles` returns the PK3's own order, the engine concatenates the files
in the reverse of it, and `FindShaderInShaderText` takes the first definition
it meets. Reverse-alphabetical is the correct model here only because
`write_pk3` stores members sorted by path, which makes this pack's listing
alphabetical. A pack produced by a different writer would need a different
model, and `shader_file_precedence` says so.

Two files are product-owned rather than upstream, because the upstream ones
describe a whole OpenArena release this pack cannot honour: `scripts/arenas.txt`
and `scripts/bots.txt` are generated from the recipe profile and declared as
`metadata` members whose source is the recipe's own SHA-256. A generated member
satisfies the QVM reference without the upstream file being packaged at all.

## Static closure result

`scripts/build-content-pack.py` reports **no unresolved reference among those
the two readings above extract**, once 28 recipe-declared acceptances are
subtracted. That is the exact claim: every reference the check can see either
resolves to a packaged member or is named in the recipe with a reason. It is
not a claim that the pack satisfies every reference the running game could ever
make, which no static reading can establish.

The check fails if a declared acceptance ever becomes resolvable, so the list
cannot silently outlive its justification. The acceptances fall into four
groups:

- **Team Arena / missionpack data** the `baseq3` QVMs still name outside a
  `MISSIONPACK` guard: the six `models/players/characters/james/*` skins and
  `models/weapons/v_launch/tris.md3`. No audited free source provides the
  `characters/` tree, and the FFA profile never selects one.
- **Single-player and Capture the Flag cues** outside this profile:
  `models/mapobjects/podium/podium4.md3`, `menu/medals/medal_victory`,
  `sound/player/announce/youwin.wav`, `sound/teamplay/flagret_{blu,red}.wav`,
  and `powerups/blueflag`, the flag-carrier effect `cg_main.c` registers under
  a runtime team-gametype guard the static reader cannot evaluate.
- **Assets OpenArena replaced differently**: the optional halo sub-models
  (`armor/shard_sphere`, `holdable/medkit_sphere`, `instant/invis_ring`),
  `sound/misc/windfly.wav` (OpenArena uses `sound/items/flight.wav`),
  `music/win`/`music/loss`, `menu/art/pblogo` (PunkBuster branding, which this
  project does not want an equivalent of), and `menubackRagePro`, the alternate
  menu background `ui_qmenu.c` registers only for `GLHW_RAGEPRO` hardware.
- **Dangling upstream references**: `music/sonic5.wav`, named by
  `oa_pvomit`'s worldspawn but shipped by no OpenArena release, and three
  placeholder material names left in `models/weaphits/bullet.md3`,
  `models/weaphits/boom01.md3` and `models/misc/telep.md3` by their authors.

Plus one non-reference: `models/players`, which is the directory argument of
`trap_FS_GetFileList` in the player-model menu.

Four further references are reported separately as *malformed*: absolute
Windows paths and bare material names inside upstream MD3s. They cannot name a
file in a PK3 under any resolution rule, so classing them with missing assets
would be misleading; the renderer answers them with its default shader.

**These are static checks only.** They prove that the packaged members satisfy
every reference the pinned sources make that this profile can reach. They do
not prove the map loads, renders or plays — that is WP4's real-browser
acceptance, and the plan's failure boundary still applies there.

## Determinism

The assembly runs **inside the WP0 builder image**, for the same reason the
browser build does: a PK3's bytes depend on the CPython `zipfile` writer and
the zlib the interpreter links, so "byte-identical" is a property of a fixed
toolchain and not of the format. Pinning it is the difference between a
reproducibility claim and a claim about whichever host happened to run the
script — and the observed difference is real: the same recipe assembled on the
host interpreter and in the image produced different digests.

Each assembly writes `toolchain.txt` beside the pack:

```text
python: 3.12.3 (cpython)
zlib-module-version: 1.3
zlib-runtime-version: 1.3
```

Two clean assemblies were run by `CONTAINER_RUNTIME=podman
scripts/verify-content-pack.sh` from arena-web commit
`621da595a85e171ba5beb8d82be5acac59addb3f`:

```text
=== comparing the two assemblies ===
two clean assemblies are byte-identical
```

The comparison is a recursive `diff` over the whole assembly directory: the
PK3, the provenance record, the manifest, the toolchain record and the closure
report all matched byte for byte, and the rebuilt records were then compared
against the committed ones (a differing `producer` commit is reported, not
treated as a mismatch, as in WP1).

| Control | Value | Why |
| --- | --- | --- |
| Interpreter and zlib | the pinned builder image | The archive's compressed bytes come from these, so they are inputs, not environment. |
| Member order | sorted by path | The ZIP central directory cannot depend on traversal order. |
| Member timestamp | `1980-01-01 00:00:00` | The earliest a ZIP can express, so no ambient clock value reaches the archive. |
| Member mode and system | `0100644`, Unix | A reader sees the same mode regardless of the building host's umask. |
| Compression | deflate, level 9, passed per member | A level given to the `ZipFile` constructor is ignored for a caller-supplied `ZipInfo`, so it must be passed to `writestr` or zlib's default silently applies. A regression test pins this. |
| Input bytes | read from the digest-verified `.tar.bz2`, from the same open file the digest was taken over | There is no unpacked intermediate tree, and nothing can replace the archive between the check and the read. |
| Build tree | deleted before every assembly | No earlier member can survive into an accepted pack. |
| Network | `--network none` | The offline property is enforced by the runtime rather than asserted. |
| Worktree | must be clean, untracked files included | The manifest records the commit that produced it. |

Nothing in the assembly reads the clock, the environment or the current
directory; the output directory path is not an input to any member.

## Reproducing the result

From a clean checkout:

```bash
git submodule update --init --recursive
scripts/check.sh
scripts/fetch-content-sources.sh                            # once, online
CONTAINER_RUNTIME=podman scripts/verify-content-pack.sh     # two clean assemblies
```

`CONTAINER_RUNTIME` defaults to `docker`. The builder image must already be
present locally; `scripts/build-content-pack.sh --print-image` prints its exact
reference without running anything. Only the fetch step uses the network, and
it accepts an archive only if its size and SHA-256 match
`content/pack-recipe.json` exactly. `scripts/build-content-pack.sh` performs a
single assembly. Everything is written to the gitignored `build/` directory.

Resource note: the fetch downloads about 913 MB of `.tar.bz2`, and the assembly
holds every selected member of every source archive in memory while it resolves
the closure — the archives are read once, sequentially, because bzip2 has no
usable random access. Expect roughly 2 GB of RAM for the assembly container and
about 1 GB of disk for the archives. A machine that cannot afford that would
need a spill-to-disk staging step, which would reintroduce the unpacked
intermediate tree this design deliberately avoids.

## Licence report

Every one of the 668 packaged members is **`GPL-2.0-or-later`**, an expression
on WP0's product-input allowlist. The combination is therefore internally
consistent and distributable under GPL-2.0-or-later, and it is compatible with
the GPL-2.0-or-later browser client WP1 assembled, though the two remain
separately licensed works that are aggregated rather than merged: the pack
contains no code and is loaded as data.

| Member group | Members | Source | Licence | Obligations |
| --- | --- | --- | --- | --- |
| Core art, sound, item/weapon models, menu art, shaders, and the `CREDITS`, `CREDITS-0.8.5` and `README` notices | 470 | `openarena-data` | GPL-2.0-or-later | attribution notice, corresponding source |
| Bot files, `.aas`, feedback and team sounds | 78 | `openarena-misc` | GPL-2.0-or-later | same |
| 0.8.8 patch replacements and the `COPYING` and `CREDITS-0.8.8` notices | 68 | `openarena-088-data` | GPL-2.0-or-later | same |
| World textures and sky | 24 | `openarena-textures` | GPL-2.0-or-later | same |
| Player model and voices | 23 | `openarena-players` | GPL-2.0-or-later | same |
| Map and level shot | 2 | `openarena-maps` | GPL-2.0-or-later | same |
| `scripts/arenas.txt`, `scripts/bots.txt`, `NOTICE-arena-web.txt` | 3 | arena-web recipe | GPL-2.0-or-later | same |

The seven rows sum to the 668 packaged members. Five of the six notice members
travel with the OpenArena source that carries them; the sixth,
`NOTICE-arena-web.txt`, is generated, which is why the `arena-web` row is three
members and not two.

Every member records `license-notice` and `copyleft-source`, and every
non-notice member binds those obligations to all six packaged notice members.
No provisional or unresolved notice claim remains.

### Notices shipped inside the pack

| Notice member | What it is |
| --- | --- |
| `COPYING` | The GNU General Public License version 2 text, as OpenArena ships it |
| `CREDITS`, `CREDITS-0.8.5`, `CREDITS-0.8.8` | OpenArena's own author credits for 0.8.1, 0.8.5 and 0.8.8, reproduced unmodified |
| `README` | OpenArena's release document, which states the terms under which the content was released |
| `NOTICE-arena-web.txt` | Generated: names every upstream source with its licence, licence evidence, retrieval URL, digest and preferred source, and carries the written offer for corresponding source |

### Attribution and source-offer obligations a distributor must meet

1. **Ship the notices.** All six notice members above must accompany any
   redistribution of the pack. They are inside the PK3, so shipping the PK3
   satisfies this as long as the PK3 is not repacked without them.
2. **Offer the corresponding source.** For each GPL member that is a compiled
   or exported form, the preferred source form is the same digest-pinned Debian
   `orig` tarball it came from — Debian added `source/assets/` for exactly this
   purpose, so `maps/oa_pvomit.bsp`'s Radiant `.map`, the player model's
   modelling sources and the texture sources all travel with the packaged file.
   The six tarballs and their digests are listed above and in the generated
   `NOTICE-arena-web.txt`. Where the packaged file already *is* the preferred
   form — shader scripts, most sounds, the bot `.c` files — no separate source
   exists and none is claimed.
3. **Offer the assembly source.** `content/pack-recipe.json` and the scripts
   under `scripts/` at the commit named by the manifest's `producer` are part
   of the corresponding source for the assembled archive.
4. **Preserve the author credits.** `source/assets/maps/credits` names the map
   author and converter, and the `CREDITS*` files name the OpenArena
   contributors; neither may be stripped.

No attribution obligation beyond GPL-2's notice preservation applies, because
no packaged member is under a CC-BY or CC-BY-SA expression. No member carries a
non-commercial, no-derivatives or unknown-licence term; the two GPL-2-only
items in the upstream set are excluded by an enforced rule rather than by
inspection.

## Placement of the two committed records

The content-provenance record is the format WP0 defined for exactly this. The
PK3's own identity needs the artifact-manifest format, but that format's
validator requires `emscripten-builder` **and** `ioq3` as declared baseline
inputs for anything under `manifests/`, and its own comment scopes that rule to
"a generated engine build". A content pack is not built by Emscripten, and
declaring it as an input would be false. Both records therefore live under
`provenance/`, where the validator applies the same schema and semantic checks
without the engine-build input requirement. This is a placement decision, not a
change: no schema, validator or WP0/WP1 artifact was modified.

## Findings recorded rather than fixed

- **The shader stage reader models the stage image directives common to both
  renderers.** `map`, `clampmap`, `videomap`, `animMap` and `skyParms` are what
  name images in either renderer; `renderergl2`'s stage-type keywords
  (`diffuseMap` and friends) select a stage type and name no image of their
  own. What the reader does **not** model are `renderergl2`'s implicit,
  optional companion-image probes — for a lightmapped diffuse stage it also
  tries `<image>_n` (normal map, `tr_image.c`) and `<image>_s` (specular,
  `tr_shader.c`) and silently proceeds without them. This matters because the
  WP1 browser client builds `renderergl2` only (`BUILD_RENDERER_GL1 OFF` in
  `cmake/platforms/emscripten.cmake`). No OpenArena shader in this pack ships
  such companions, so nothing is missing; a later content source that did
  provide them would have those optional images invisible to the closure.
- **The generated members' licence evidence is pinned to a published commit.**
  `content/pack-recipe.json` points `arena-web`'s `licenseEvidenceUrl` at
  `LICENSE` at commit `1cc0a1a`, the newest published arena-web commit, whose
  `LICENSE` is byte-identical to the current one. It is deliberately not the
  moving `main` branch. It should be re-pinned to this work package's own
  commit once that is published.
- **Only five registration traps are read.** The list is
  `trap_R_RegisterShader`, `trap_R_RegisterShaderNoMip`,
  `trap_R_RegisterModel`, `trap_R_RegisterSkin` and `trap_S_RegisterSound`, and
  only their first argument. A future gamecode change that registers content
  through a sixth trap would need this list extended; nothing detects that
  automatically.

## What this does not prove

- Nothing has been run. The pack has not been loaded by the engine, in a
  browser or anywhere else. Runtime compatibility with the pinned `baseq3`
  QVMs is WP4's acceptance, and WP4's failure boundary — stop and return to
  plan review rather than switching gamecode — is unchanged.
- 24.2 MB compressed is a large first page load. WP3 owns the audited closure,
  not delivery; measuring and reducing load time belongs to the work package
  that ships a loader.
- The pack is not byte-compatible with a public OpenArena pure server, and is
  not intended to be. The plan already excludes arbitrary-server compatibility.
- `menu/art` is 11.11 MB of uncompressed TGA, the single largest group. It is
  required by the `q3_ui` menu the `baseq3` QVMs contain. Re-encoding it would
  be a transformation with its own provenance consequences and was deliberately
  not done here.
