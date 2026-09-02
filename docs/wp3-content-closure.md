<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# WP3 evidence: audited minimal-content closure

**Status:** WP3 complete — two clean assemblies accepted, byte-identical.
Amended 2026-08-31: the pack additionally carries the weapon models whose
names the gamecode constructs at runtime; see
[the amendment section](#amendment-of-2026-08-31-the-names-the-gamecode-constructs-at-runtime).
**Amended 2026-09-01 (WP-C): the pack is no longer one archive.** It is a base
archive plus one archive per map, and the base carries seven player
presentations rather than one; see
[the archive split](#amendment-of-2026-09-01-the-archive-split). Everything
below the selected-profile table is WP3's reasoning about *how* content is
selected and licensed, which the split does not change; the numbers in it are
WP3's, and the current ones are in the split section.

This document records what the arena-web content pack is: how its members were
selected, which upstream inputs they come from, how their licences were
verified per file rather than per repository, what the static closure checks
prove, and what a distributor of this pack owes.

The pack itself is not committed. Its identities are, in
[`provenance/arena-web-ffa-content.json`](../provenance/arena-web-ffa-content.json)
(every member, with source, licence, obligations, notice binding and digest)
and
[`provenance/arena-web-ffa-content-manifest.json`](../provenance/arena-web-ffa-content-manifest.json)
(each archive's own identity and the inputs that produced it).

`ioq3/` is untouched at its pinned commit. No baseline, schema, WP1 build
script or committed WP1 manifest was changed.

## The selected profile

*As WP3 accepted it. The current archive set is in
[the archive split](#amendment-of-2026-09-01-the-archive-split).*

| Item | Selection |
| --- | --- |
| Package | `arena-web-ffa-oa_pvomit` |
| Archive | `baseq3/arena-web-ffa.pk3`, `sha256:ae244d1eb8948b17b4348bcf8617b86e2db68516bdb0d0616b29a9958b140664`, 24,484,503 bytes |
| Members | 698 (690 assets, 6 notices, 2 generated metadata), 54.26 MB uncompressed |
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
pack as WP3 originally assembled it (the amendment of 2026-08-31 later raised
the assembled pack to 698; the candidate measurements are historical and were
not re-run).

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

The pack is a closure, not a list, with one declared exception — root 8. Roots:

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
2. **The declared derived references.** Two compiled sources construct
   per-weapon model names the sources never spell out — `cg_weapons.c:658-668`
   and the menu's `q3_ui/ui_players.c:91-98` both strip the extension of
   `item->world_model[0]` and append `_flash.md3`, `_barrel.md3` or
   `_hand.md3` — so the only literal either reading can extract is the suffix.
   The recipe declares both construction sites
   (`derivedConstructionSites`, reconciled two-way against a suffix-literal
   scan of the compiled translation units) and each derived name
   (`derivedReferences`, included with its members or excluded with a
   reason), and the assembly refuses what the pinned tree does not back: the
   cited lines must contain the construction, the derivation is recomputed
   rather than trusted, the base must be a weapon world model of the pinned
   `bg_itemlist`, the derived name must *not* be statically extracted, and
   each included entry's declared members must end up packaged while each
   excluded reference must not. The reverse direction is checked too: every
   world model crossed with every site suffix must be declared, statically
   owned, or absent from the pinned source set — the same two-way discipline
   the template reconciliation applies. Added by the amendment of 2026-08-31,
   below.
3. **The declared expansion of every template.** A template such as
   `sound/player/footsteps/step%i.wav` cannot be resolved statically, so
   `content/pack-recipe.json` states either its expansion (footsteps 1–4 from
   `cg_main.c`, crosshairs `a`–`j` from `NUM_CROSSHAIRS`, the profile map) or
   the reason it expands to nothing. The assembly fails if the QVMs use a
   template the recipe does not mention, and fails again if the recipe mentions
   one the QVMs do not use, so the list cannot rot in either direction.
4. **The map**: BSP shader lump, plus entity `model`, `noise` and `music` keys,
   plus its `.aas` and level shot.
5. **The player presentation**: MD3s, `.skin` files, `animation.cfg`, icon, and
   the model's own and its `sex`-derived voice directories.
6. **The bots**: `botfiles/` defaults plus each selected character, expanded
   through `#include` and `CHARACTERISTIC_*` file references — resolved from
   `botfiles/` itself, because `botlib` calls `PC_SetBaseFolder("botfiles")`.
7. **The packaged notices.**
8. **Every `scripts/*.shader` in the sources**, into the base archive and into
   no other. This is the one member category that is in the pack by **rule**
   rather than because a closure reached it, so the rule and its reason are
   recipe data — `shaderAuthority` in `content/pack-recipe.json` — rather than
   a special case in the builder, and `check_shader_authority` asserts both
   halves of it on every assembly. Its images are deliberately not followed: a
   shader name a map actually uses is still resolved through the shared
   `ShaderIndex` by the archive that uses it, and that archive packages the
   winning definition's images exactly as before. See
   [the shader authority](#the-base-is-the-packs-shader-authority) for why the
   split needs it.

Resolution uses the engine's own rules, read out of the pinned checkout:

- images try the reference's own extension first and then `tga`, `jpg`,
  `jpeg`, `png`, `pcx`, `bmp`, `pvr` against the stripped name
  (`R_LoadImage`);
- sounds likewise try `wav`, `ogg`, `opus` (`S_CodecGetSound`, whose codec list
  is built by prepending);
- a drawn reference is looked up as a shader **with its extension stripped**
  and only falls back to an image of the *original* name (`R_FindShader`);
- shader definitions are indexed across every `scripts/*.shader` in the source
  set with reverse-alphabetical file precedence. WP3 packaged only the shader
  files that won a needed definition; the base now packages all of them under
  root 8, which is what makes the index's model and the engine's agree once
  there is more than one archive;
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
subtracted, and none among the recipe's declared derived references. That is
the exact claim: every reference the check can see either resolves to a
packaged member or is named in the recipe with a reason. It is
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

Two clean assemblies were last run by `CONTAINER_RUNTIME=podman
scripts/verify-content-pack.sh` for the amendment of 2026-08-31, from the
commit that carries the reissued records (the original WP3 acceptance ran
identically from commit `c773fdd3783811ce6c78940e9e182de1ff16e930`):

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

Every one of the 698 packaged members is **`GPL-2.0-or-later`**, an expression
on WP0's product-input allowlist. The combination is therefore internally
consistent and distributable under GPL-2.0-or-later, and it is compatible with
the GPL-2.0-or-later browser client WP1 assembled, though the two remain
separately licensed works that are aggregated rather than merged: the pack
contains no code and is loaded as data.

| Member group | Members | Source | Licence | Obligations |
| --- | --- | --- | --- | --- |
| Core art, sound, item/weapon models, menu art, shaders, and the `CREDITS`, `CREDITS-0.8.5` and `README` notices | 495 | `openarena-data` | GPL-2.0-or-later | attribution notice, corresponding source |
| Bot files, `.aas`, feedback and team sounds | 78 | `openarena-misc` | GPL-2.0-or-later | same |
| 0.8.8 patch replacements and the `COPYING` and `CREDITS-0.8.8` notices | 71 | `openarena-088-data` | GPL-2.0-or-later | same |
| World textures, sky and flares | 26 | `openarena-textures` | GPL-2.0-or-later | same |
| Player model and voices | 23 | `openarena-players` | GPL-2.0-or-later | same |
| Map and level shot | 2 | `openarena-maps` | GPL-2.0-or-later | same |
| `scripts/arenas.txt`, `scripts/bots.txt`, `NOTICE-arena-web.txt` | 3 | arena-web recipe | GPL-2.0-or-later | same |

The seven rows sum to the 698 packaged members. Five of the six notice members
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
   redistribution of the pack. They are inside **every** archive, so shipping
   the archives satisfies this as long as none is repacked without them. Each
   archive is published under its own URL and redistributed on its own, which
   is why each carries the complete set rather than relying on a sibling.
2. **Offer the corresponding source.** For each GPL member that is a compiled
   or exported form, the preferred source form is the same digest-pinned Debian
   `orig` tarball it came from — Debian added `source/assets/` for exactly this
   purpose, so `maps/oa_pvomit.bsp`'s Radiant `.map`, the player model's
   modelling sources and the texture sources all travel with the packaged file.
   The six tarballs and their digests are listed above and in the generated
   `NOTICE-arena-web.txt`. Where the packaged file already *is* the preferred
   form — shader scripts, most sounds, the bot `.c` files — no separate source
   exists and none is claimed.
3. **Offer the assembly source.** `content/pack-recipe.json`, the per-map
   fragments `content/maps/<map>.json` that the content manifest records by
   digest, and the scripts under `scripts/` at the commit named by the
   manifest's `producer` are part of the corresponding source for the assembled
   archives. Each archive's own notice names the one of those two recipe files
   that produced it, and the repository at that commit carries the rest.
4. **Preserve the author credits.** `source/assets/maps/credits` names the map
   author and converter, and the `CREDITS*` files name the OpenArena
   contributors; neither may be stripped.

No attribution obligation beyond GPL-2's notice preservation applies, because
no packaged member is under a CC-BY or CC-BY-SA expression. No member carries a
non-commercial, no-derivatives or unknown-licence term; the two GPL-2-only
items in the upstream set are excluded by an enforced rule rather than by
inspection.

## Placement of the two committed records

The content-provenance record is the format WP0 defined for exactly this. Each
archive's own identity needs the artifact-manifest format, but that format's
validator requires `emscripten-builder` **and** `ioq3` as declared baseline
inputs for anything under `manifests/`, and its own comment scopes that rule to
"a generated engine build". A content pack is not built by Emscripten, and
declaring it as an input would be false. Both records therefore live under
`provenance/`, where the validator applies the same schema and semantic checks
without the engine-build input requirement. This is a placement decision, not a
change: no schema, validator or WP0/WP1 artifact was modified.

## Findings recorded rather than fixed

- **The closure does not model `SP_target_speaker`'s `.wav` append.** An entity
  `noise` value is resolved as written (`content_pack.py` `_add_bsp`), but the
  game module appends `.wav` unless the value already contains it (ioq3
  `code/game/g_target.c`), so `sound/ambient/sparks.ogg` is opened as
  `sound/ambient/sparks.ogg.wav`. Across the audited map set that is the only
  value where the two differ, and neither name exists in any pinned archive, so
  the reference dangles either way and the fragment accepts it. The gap is
  nevertheless **fail-open**: where a source provides the un-suffixed name, the
  closure would package a member the engine never opens and report the
  reference resolved — no unresolved entry, no stale acceptance, no failing
  gate, and a silent sound. Fixing it means modelling which spawn functions
  append and which do not, which is a closure-model change rather than a
  content one. Found by the WP-F review.
- **The `skyParms` finding recorded here was wrong, and is withdrawn.** It said
  that `_stage_images` emits outerbox names without an extension while
  `ParseSkyParms` only ever tries `"%s_%s.tga"` (`renderergl2/tr_shader.c`), so
  a source shipping `full_rt.jpg` would resolve in the closure and be missing in
  the engine. `ParseSkyParms` does build a `.tga` name, but it hands it to
  `R_FindImageFile`, and `R_LoadImage` (`renderergl2/tr_image.c`) strips the
  given extension and probes **every** registered loader against the stem.
  `renderergl2`'s loader table and `game_assets.IMAGE_EXTENSIONS` hold the same
  seven formats, so the closure's extension-less emission matches the engine
  rather than being wider than it,
  and the largest acceptance class rests on a correct model. What the closure
  genuinely does not model is `R_LoadImage`'s separate `.dds` path, tried first
  under `r_ext_compressed_textures` and as a last fallback without it. That gap
  points the *safe* way: the closure would call a reference unresolved that the
  engine can open, which is a loud failure needing an explicit acceptance entry,
  not a silent one. No pinned source ships a `.dds`, so it is not live either.
  What the two tables do **not** share is their order. `IMAGE_EXTENSIONS` is
  `renderergl1`'s order, which its own comment cites, while the browser builds
  `renderergl2` only, whose table leads with `png`. That difference is real
  rather than hypothetical — 43 image stems in the pinned sources ship in more
  than one format, seven of them as `.png` **and** `.tga`, and
  `levelshots/oa_shouse` is one of the 43 — but it cannot change a member or a
  verdict here, for a reason worth writing down rather than trusting: the
  closure packages the **first** existing candidate and only that one, and
  `R_LoadImage` stops at the first loader that actually returns an image, so a
  renderer that would have preferred the other format finds it absent and falls
  through to the member that is packaged. No stem is packaged in two formats
  (checked over the built archives: 856 image stems, none doubled). The order
  would decide something only if two candidates for one stem were both
  packaged — which needs two references naming the same stem with different
  extensions, and no reference set here produces that.
  Recorded by WP-F batch 2, which re-derived the class from the engine while
  publishing two more maps that depend on it.
- **The closure treats a two-track `music` value as one game path.**
  `CG_StartMusic` (`code/cgame/cg_main.c`) runs `COM_Parse` twice over the
  worldspawn `music` string and passes the two tokens to
  `trap_S_StartBackgroundTrack` as an intro and a loop track, while `_add_bsp`
  resolves the whole value as a single reference. `oa_dm2` is the one audited
  map that writes both, so its acceptance entry is the combined string
  `"music/fla22k_04_intro.ogg music/fla22k_04_loop.ogg"` — a name no filesystem
  could hold. Neither of the two real names exists in any pinned archive, so
  nothing is missing today; a source that shipped them would leave the closure
  packaging neither while still reporting a single unresolved reference. Whoever
  publishes `oa_dm2` inherits that acceptance entry and should read it as one
  string standing for two references rather than as a path.
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
- 24.5 MB compressed is a large first page load. WP3 owns the audited closure,
  not delivery; measuring and reducing load time belongs to the work package
  that ships a loader.
- The pack is not byte-compatible with a public OpenArena pure server, and is
  not intended to be. The plan already excludes arbitrary-server compatibility.
- `menu/art` is 11.11 MB of uncompressed TGA, the single largest group. It is
  required by the `q3_ui` menu the `baseq3` QVMs contain. Re-encoding it would
  be a transformation with its own provenance consequences and was deliberately
  not done here.

## Amendment of 2026-08-31: the names the gamecode constructs at runtime

**Operator confirmation, 2026-08-31:** after this amendment was built and
reviewed, the operator ran a visual round on the real display and confirmed
that both reported defects are resolved — the machine gun's barrel renders
and spins, and the lightning gun's beam is visible.

WP3 closed on the bounded claim above: every reference the two static readings
extract resolves or is a recipe acceptance. The claim held; the bound bit.
While confirming the WP4 renderer fix on 2026-08-30, the operator reported two
visual gaps — the machine gun's spinning barrel missing, the lightning gun's
beam invisible — and the investigation found one shared cause outside anything
the static readings can see: **`cg_weapons.c` constructs a whole family of
per-weapon model names at runtime by string surgery**. `CG_RegisterWeapon`
strips the extension of `item->world_model[0]` and appends `_flash.md3`
(`code/cgame/cg_weapons.c:658-660`), `_barrel.md3` (`:662-664`) and
`_hand.md3` (`:666-668`). The only string literal in that code is the suffix,
which is not path-shaped, and the first argument of the registration trap is
the composed `path` variable rather than a literal, so *both* readings pass it
by. Verified empirically, not argued: the extractor reports zero `_flash` or
`_barrel` references and exactly one `_hand` — the literal fallback
`models/weapons2/shotgun/shotgun_hand.md3` (`:670-672`), which was packed all
along.

The consequences in the shipped pack were wider than the two reports:

- **No weapon had a muzzle-flash model**, so no weapon rendered muzzle-flash
  art or its dynamic light — the flash entity is the `tag_flash` anchor and
  `CG_AddPlayerWeapon` returns when `flash.hModel` is 0
  (`cg_weapons.c:1315-1318`).
- **The lightning beam was gated off by that same early return**, which sits
  *before* `CG_LightningBolt` (`:1340`). The beam art was never missing:
  `lightningBoltNew` is defined in the packed `scripts/oanew.shader` and all
  six `textures/oafx/lbeam*.tga` were packed from the start. WP4's defect
  ledger recorded "beam shader/texture absent" as the cause; that was wrong,
  and [`wp4-vertical-slice.md`](wp4-vertical-slice.md) now carries the dated
  correction.
- **The machine gun's barrel** (and the gauntlet's and BFG's) registered as 0
  and was silently not drawn — the spin logic was always present
  (`cg_weapons.c:1137`).

The operator decided the scope on 2026-08-30: **full restoration except the
grapple** — the two defect fixes, the remaining barrels, and muzzle-flash
models for every weapon the stock FFA profile can reach.

This amendment was independently reviewed before acceptance, and the review
returned fix-first; its findings are folded in below rather than listed
separately. The substantive ones: the first round missed `rocketl_hand.md3`
and `bfg_hand.md3`, which the pinned archives *do* ship, and asserted the
opposite — the same class of gap the amendment exists to close, caught by a
reviewer instead of a check — so the category gained the reverse
reconciliation over its whole derivation space described below, and the
recipe now also declares the second construction site the review found in
the menu code. The identity the first round issued
(`sha256:f3d19e9b…`, 696 members) was superseded inside the same unpushed fix
round and was never accepted. One transparency note on conventions: the fix
round also corrected the member count inside WP4's dated note of 2026-08-31
from 696 to 698 — legitimate precisely because nothing had been pushed and
the 696 identity was never accepted, but stated plainly here: the append-only
convention for dated evidence is held at the push boundary, not per local
commit.

### The new recipe category: `derivedReferences`

`templateExpansions` could not carry this: its reconciliation deliberately
fails on a declared template the QVM readings do not report, and these names
are not format templates — the readings report nothing at all. The recipe
therefore gains a third reference category, required rather than optional
(`load_recipe` refuses a recipe without it, so deleting the key cannot
silently shrink the pack): `derivedConstructionSites` declares *where* the
gamecode performs the surgery, and `derivedReferences` declares each name it
can produce, consumed by `scripts/build-content-pack.py` as closure root 1b.

There are **two** construction sites in the compiled `baseq3` sources, and
both are declared: `code/cgame/cg_weapons.c:658-668` (flash, barrel and hand)
and the menu's `code/q3_ui/ui_players.c:91-98` (barrel and flash — the same
surgery, whose name set is a strict subset of `cg_weapons.c`'s, so it demands
no member of its own). The missionpack `code/ui/ui_players.c` carries the
surgery too but is not compiled into these QVMs and is deliberately outside
the record. The sites are reconciled **two-way** against the pinned tree: a
declared site must really construct and register a derived name — the cited
lines are a three-line adjacent range that must contain `COM_StripExtension`,
the quoted suffix and `trap_R_RegisterModel`, which together make each
declared citation the unique passing range for its suffix — and a scan of
the compiled translation units and their reachable headers must find no site
the recipe does not declare. The scan's reach is stated exactly rather than
implied: it recognises string literals of the shape `"_<alphanumeric>.md3"`,
the adjacent `COM_StripExtension`/`Q_strcat` spelling both pinned sites use,
and nothing else — a future engine pin that composed such a name through a
format string (`Com_sprintf("%s_hand.md3", …)`) or appended a non-`.md3`
suffix would be outside its reach and would require the scan to be extended;
the pinned tree contains no such spelling, so this is a bound on the guard's
reach against future drift, not a live gap.

Each `derivedReferences` entry names the derived `reference`, its `kind`, the
`constructedFrom` base name, and the construction it cites — which must equal
one of the declared sites exactly; it then either lists the `members` it must
resolve to or an `excludedReason`. The validation is fail-closed in the same
spirit as the rest of the recipe, before any archive byte is read:

- the derivation is recomputed: `reference` must equal `constructedFrom` with
  its extension replaced by the site's suffix;
- `constructedFrom` must be a weapon world model parsed out of the pinned
  `bg_itemlist` — the only bases either site applies to — and a reference the
  static readings extract;
- the derived reference must **not** itself be statically extracted — the
  shotgun-hand fallback literal is refused if declared here, so the category
  cannot absorb references the ordinary closure already owns;
- every included entry's declared members must be packaged by the finished
  closure, and every excluded reference must be absent from it — both are
  build properties, not merely committed-record tests.

Per-entry validation alone would still be fail-open over the space of names
the gamecode can construct — exactly the shape of gap the review caught — so
the builder also runs a **reverse reconciliation over the whole derivation
space**, mirroring the two-way discipline the template reconciliation
established: every weapon world model of the pinned `bg_itemlist`, crossed
with every suffix the declared sites append, must be a declared derived
reference (included or excluded), a statically extracted name the ordinary
closure already owns, or demonstrably absent from the digest-verified source
set. A constructible name a pinned archive provides that the recipe neither
includes nor excludes fails the build, and so does an exclusion whose file no
pinned source provides — a stale exclusion is dead text, like a stale
acceptance.

An entry is therefore checkable against the pinned tree alone, and
`tests/test_content_pack.py` exercises the refusals (invalid, undeclared or
overlapping-range construction sites, underivable name, base outside the
weapon items, statically visible name, malformed entry, duplicate under any
kind, unpackaged included member, packaged excluded reference, and the
reverse reconciliation's four verdicts) as well as the positive cases against
the real pinned tree.

### What was added

Fifteen entries. Thirteen resolve, and their dependency closures — computed
by the same `SourceSet`/`ShaderIndex`/`ClosureBuilder` machinery as every
other root — resolve **completely** inside the six pinned archives: all six
closures the investigation had left open (`shotgun`, `rocketl`, `grenadel`,
`plasma`, `railgun`, `bfg` flashes) came back with zero unresolved and zero
malformed references, so none had to be excluded, and the two first-person
hand rigs the review restored (`rocketl_hand`, `bfg_hand`) have empty
closures of their own — their MD3 surfaces name no materials at all, which
fits their role: they are position and animation rigs, not drawn geometry.
Every added member is
`GPL-2.0-or-later` under its source's default expression (the
`nonDefaultLicensePaths` exclusions match none of them), carries the same
obligations and notice bindings as its siblings, and is copied unmodified
from the verified upstream archive; the amendment introduces **no new licence
expression, no new obligation and no new notice document**, which the
regenerated provenance record proves member by member.

The 30 added members, 1,142,720 bytes uncompressed — of which
`gauntlet1.TGA` alone is 786,450 bytes (69%), the price of the gauntlet
barrel's own skin texture:

| Member | Source | Bytes | SHA-256 |
| --- | --- | --- | --- |
| `models/weapons2/bfg/bfg_barrel.md3` | `openarena-088-data` | 3,796 | `86afeb04a1ad49594fdfc03c4be130b0d785445628489ce638ac37d4a15e6cce` |
| `models/weapons2/bfg/bfg_flash.md3` | `openarena-data` | 1,324 | `bf3e28d1d423c79a0a0b020b914a35b5d29df4e861ac4a9b71372df05d41f7db` |
| `models/weapons2/bfg/bfg_hand.md3` | `openarena-data` | 2,460 | `af578cefe5d12a896c79e90ddab27b4dabe6e907c52a48bcfb6ef1957e081d6b` |
| `models/weapons2/bfg/f_bfg2.tga` | `openarena-data` | 16,600 | `ca83017ee94d01ec4ad805cb1914a25cdee9128d021d0214a5a798d5a7c48060` |
| `models/weapons2/bfg/f_bfg3.tga` | `openarena-data` | 49,691 | `ff86518cfb554dd1455bd2acbb8f768b27520a6c2e572cc2cfba1573e24685f7` |
| `models/weapons2/gauntlet/gauntlet1.TGA` | `openarena-data` | 786,450 | `475e371d098e76727b086691c9d74bfd53117a555052129d38861e374a0bf8fd` |
| `models/weapons2/gauntlet/gauntlet_barrel.md3` | `openarena-data` | 2,692 | `56bb358ada9a6e6f9e3b0ea730fa3b333f001dd624accb4a2f15a8e4def97315` |
| `models/weapons2/grenadel/grenadel_flash.md3` | `openarena-data` | 3,116 | `20a7b6a596704194b50adb122f801517b03180c0644d5f0ccafbe14fca203df3` |
| `models/weapons2/lightning/lightning_flash.md3` | `openarena-data` | 604 | `fcebd7d33964396a0c3486dc049f309d8ee17e7558d091ebed0d806b05f1aa7f` |
| `models/weapons2/machinegun/f_machinegun2.tga` | `openarena-data` | 12,625 | `918f6f6e9d8c1813468a8d5c0199eb86bdc218fb55f82ac23cb89681b2a96e55` |
| `models/weapons2/machinegun/f_machinegun3.jpg` | `openarena-088-data` | 3,054 | `a8c16dec5fccf5d368d4da78c233cebbf4c0377ab5baa7493386837c2bfed1cb` |
| `models/weapons2/machinegun/machinegun_barrel.md3` | `openarena-data` | 1,988 | `5c76250c8834e1a0da729841ee006b8e6a6fc7c931aa9a9ae7a2a93e2de3c894` |
| `models/weapons2/machinegun/machinegun_flash.md3` | `openarena-data` | 1,068 | `63580ff7fe37467d0765921f0e125cf2f2f0f92828724a727082f2627c2fa054` |
| `models/weapons2/plasma/plasma_flash.md3` | `openarena-data` | 1,068 | `72e11aca42694f5d0130249942220e8e541a0ad6ea59229872d13a0731307f7a` |
| `models/weapons2/railgun/f_railgun2.jpg` | `openarena-data` | 2,684 | `ce4185651927020f314553fbd7a1fae8cd4a21d387c7318a565a0292800423a7` |
| `models/weapons2/railgun/railgun_flash.md3` | `openarena-data` | 956 | `2de713fd9ad1e7929117286ade6322d9bc83785cef16c81c9b0aa741311e5767` |
| `models/weapons2/rocketl/rocketl_flash.md3` | `openarena-data` | 3,116 | `620ecf7d6fa8f2da35a6624c8cf832cfe5d6c7778fff5c07fb9888a867d0c2e4` |
| `models/weapons2/rocketl/rocketl_hand.md3` | `openarena-data` | 2,964 | `903b60569563a0c0b9d60fe85a2d0f1dd45d929b7eb1dd6411a8dda452c2a83c` |
| `models/weapons2/shotgun/shotgun_flash.md3` | `openarena-data` | 3,116 | `3bf0a94071fd6ff7d7f0d21af870fc6a6d8f7daae967c5f298f0e632480656b3` |
| `scripts/weapon_newmuzzle.shader` | `openarena-088-data` | 8,582 | `41e157b608ef96466167d7bcff08ef1ddac2b903de33b6d244dde282dd793b72` |
| `textures/flares/flarey.tga` | `openarena-textures` | 66,075 | `60b8b1c095c44a97b8059004e60abf550a46abce76980b769a78005e797802d9` |
| `textures/flares/lava.tga` | `openarena-textures` | 66,075 | `47bca573e9661d53f184b236bea81c8ed8ad1b6c9973906b942217cee59e55f8` |
| `textures/oa/muzzle/muz1.tga` | `openarena-data` | 12,827 | `57711f65f4c7c078e804ac763f1bfc551c576fa42f33b04b3cb6ca48b8b0d45b` |
| `textures/oa/muzzle/muz2.tga` | `openarena-data` | 12,827 | `2bc63754ca3f4977ea5afb715cd0955349319d41d51e449668a6bbd4891b34e4` |
| `textures/oa/muzzle/muz3.tga` | `openarena-data` | 12,827 | `981b379b466a86e9ffa59fc034b4b281ca7f3178d2257fc12171801cd8a86efb` |
| `textures/oa/muzzle/muz4.tga` | `openarena-data` | 12,827 | `cc1955bba3a3d94b3f4dc386e05bf5f69c7c77636799a621f389b9078ed2a3d6` |
| `textures/oa/muzzle/muz5.tga` | `openarena-data` | 12,827 | `c3dad2d6b2c38813701aaa02513d9e7cfe168876e25feaa45cde65a40e43c359` |
| `textures/oa/muzzle/muz6.tga` | `openarena-data` | 12,827 | `633a763283e59f3458c3bc35e190141e6556fc5e0b115f25889f445a5ef4509b` |
| `textures/oa/muzzle/muz7.tga` | `openarena-data` | 12,827 | `541118e0816fe53f61825f80ebee8424e29d0049fb6bf7aa4b26470e52b93813` |
| `textures/oa/muzzle/muz8.tga` | `openarena-data` | 12,827 | `958e7e18874c08a987c1012a2f4ab2b8512c7c7b2af35eebb2aa105e695d547a` |

Three closure results deserve a sentence each. The shotgun, rocket and
grenade flashes share one new shader file, `scripts/weapon_newmuzzle.shader`,
whose 28 `cmuz_*` definitions collide with no definition in any previously
packaged shader file, so packaging it shadows nothing (checked against every
packaged file, not assumed). The plasma flash's `f_plasmagun2` shader in the
already-packaged `weapon_plasma.shader` stages `textures/flares/lava.tga`,
`textures/flares/flarey.tga` and the already-packed `twilightflare.tga` —
and `flarey.tga` is the very image WP4's finding 2
records as the missing stage of the engine-registered `sun` shader, so that
cosmetic gap closes incidentally and the corresponding acceptance in the WP4
driver's list becomes inert (it stays listed; an acceptance that never fires
is harmless). And the machine-gun barrel needs no shader at all: its `mgun`
material has no definition anywhere, so the engine builds the implicit
single-stage shader from the already-packed `mgun.tga`.

### What was excluded, and what has no entry at all

Two entries are exclusions, recorded in the recipe with their reasons:

- **`models/weapons2/grapple/grapple_barrel.md3`** — the operator's decision.
  The stock FFA profile cannot reach the grapple: no packaged map entity
  places `weapon_grapplinghook` and the profile offers no other way to obtain
  it. (The grapple's *world* model was always packed — it is a static
  `bg_misc.c` literal — but its barrel stays out.)
- **`models/weapons2/machinegun/machinegun_hand.md3`** — the upstream file is
  byte-identical
  (`sha256:681d01439210d9669156e25065a45c9c4a42f368ed606f86bcf1b6f8571e59c2`,
  5,148 bytes) to the packed `shotgun_hand.md3` that `cg_weapons.c:670-672`
  registers as the fallback whenever the constructed hand model is absent, so
  packaging it would duplicate the same bytes without changing what renders.

The `_hand` family deserves its own account, because the first round of this
amendment got it wrong — it claimed the archives ship no hand model beyond
the shotgun's and the machine gun's, and the review disproved that against
the archives themselves. The pinned `openarena-data` archive ships **four**:
`shotgun_hand.md3` (a static literal, packed by the ordinary closure all
along), `machinegun_hand.md3` (excluded above, byte-identical to it),
and `rocketl_hand.md3` and `bfg_hand.md3`, which are **distinct rigs** (17
and 14 frames against the shared 30-frame shotgun rig) for two weapons the
FFA profile reaches — so the first round's pack silently substituted the
shotgun's first-person rig where the native reference client shows the right
one. Both are now included, under the operator's standing full-restoration
scope.

The remaining fourteen names of the 30-name derivation matrix (ten weapons ×
three suffixes) have deliberately **no** recipe entry, because the pinned
archives provide no such file: no OpenArena release in this input set ships
`gauntlet_flash`, `grapple_flash`, any hand model beyond the four above, or
barrels beyond the machinegun/gauntlet/BFG/grapple four. For those names the
constructed registration returns 0 in the native reference client exactly as
it does here — absence upstream is the reference behaviour, not a gap, and an
entry would have nothing to include *or* exclude. That claim is no longer a
sentence a reviewer has to disprove by hand: the reverse reconciliation
described above recomputes it on every build, and a name that turns up in a
pinned archive without an entry stops the assembly.

### The reissued identity

The pack identity moved from
`sha256:55a1d51fa99b131c76e5813ee5449fa671c3a584ee251607528868e5a0a05ad7`
(24,181,175 bytes, 668 members) to
`sha256:ae244d1eb8948b17b4348bcf8617b86e2db68516bdb0d0616b29a9958b140664`
(**24,484,503 bytes, 698 members**, 27 packaged shader files) — the
single-archive identity as the 2026-08-31 amendment left it. The current
archive set is in
[the archive split](#amendment-of-2026-09-01-the-archive-split). Both committed
records — the member-level provenance and the pack manifest — were
regenerated by the same deterministic assembly in the pinned builder image,
and the two-assembly reproducibility check was re-run and passed:
byte-identical PK3s, provenance, manifest, toolchain record and closure
report, agreeing with the committed records. The only changed pre-existing
member is the generated `NOTICE-arena-web.txt`, whose text embeds the
recipe's own digest by design. The staged `build/arena-serve` tree was
re-staged and re-verified against the reissued manifest.

Not rewritten, deliberately: the WP5 packet census, the WP2/WP4/WP5 witnessed
and measurement evidence, and `provenance/arena-web-server.json`. The server
record identifies the WP5 image **as built and censused**, with the
pre-amendment pack inside it; that is a historical fact about accepted
evidence, not a live manifest. WP7 must rebuild and re-census the server from
the final engine pin anyway (the WP6 decision requires it), and that rebuild
picks up the amended pack and reissues the server record. The disclosed cost:
`scripts/verify-native-build.sh --target server` rebuilds the image from
`build/content-pack` and compares it against that committed record, so it is
**knowingly red** from this amendment until WP7's mandated rebuild reissues
the record — a deliberate, visible mismatch, not an oversight; a dated note
in [`wp5-packet-census.md`](wp5-packet-census.md) scopes its present-tense
equality claim accordingly.

### This amendment is sizing-neutral for WP6

The added members are client-side models and textures resolved from names the
cgame constructs locally in `CG_RegisterWeapon`. They add no configstring
*index* and no gameplay state; the one server-visible change is the pack's
own checksum, because `sv_paks`/`sv_pakNames` and
`sv_referencedPaks`/`sv_referencedPakNames` are `CVAR_SYSTEMINFO`
(`code/server/sv_init.c`) and therefore travel in `CS_SYSTEMINFO` inside the
gamestate — the message WP5 measured and WP6 sized. Reissuing the pack
changes those checksum strings by at most a few bytes (a signed checksum's
decimal width can shift), which cannot change the gamestate's fragment count
at `FRAGMENT_SIZE` 704 and approaches no bound the decision set. The census
inputs, the routed-path budget and every number in
[`wp6-network-sizing.md`](wp6-network-sizing.md) are untouched, and the WP6
decision's inputs therefore remain exactly the two committed records it names.

## Amendment of 2026-09-01: the archive split

The pack is no longer one archive. It is a **base archive** plus **one archive
per map**, and the base carries seven player presentations instead of one.
Nothing about how members are selected, licensed or audited changed; what
changed is how the selected members are divided into distributables.

### Why

A single archive makes every player who returns after a map is added
re-download the whole pack, because one added map moves the one artifact's
bytes. The split is what makes the growth additive for the player: an archive's
bytes, and therefore its URL, must not move when the set grows.

That property does not follow from splitting alone. Six paths by which one
map's presence reached another archive's bytes were closed for it, and the
whole thing is held to one mechanical test — build the set, add a map, rebuild,
and every previously existing archive and the base must be byte-identical.
`scripts/verify-content-pack.sh` performs it, so the property is checked rather
than argued. It holds for a map drawn from the sources the recipe already pins;
a map needing a *new* upstream source edits the root recipe, which is the base
archive's own selection input, so the base's bytes would move with it.

### The archive set

| Archive | Identity | Members | Uncompressed |
| --- | --- | --- | --- |
| `baseq3/arena-web-ffa-base.pk3` | `sha256:6c3341ef87d16c75b7d3fb5f368d9f935dac304c1dd7667f96b64dd73912bb03`, 40,913,889 bytes | 832 (824 assets, 6 notices, 2 generated) | 82.86 MB |
| `baseq3/arena-web-ffa-map-oa_pvomit.pk3` | `sha256:304a2266a08ebe2f3b63117214dd9cf2489b974c2036d6cf05309555e3ce95d3`, 1,923,375 bytes | 28 (21 assets, 6 notices, 1 generated) | 5.44 MB |

Every member of both archives is `GPL-2.0-or-later`, as before.

**The map archive is a set difference, not a description.** It is
`closure(M) \ closure(base)`: everything map `M`'s closure reaches that the
base's does not. "The assets referenced only by this map" would have been
set-dependent — whether an asset is referenced only by `M` depends on which
*other* maps are in the build — and would therefore have moved an existing
archive's bytes as the set grew.

**Both archives carry the complete notice set.** Each has its own immutable URL
and is redistributed on its own, so each is a GPL distribution in its own
right. That is why `provenance/arena-web-ffa-content.json` now records sources
and members *per archive* rather than once for the whole pack.

### What the split required

1. **A closure per archive, over one shared source set and one shared shader
   index.** The source set collapses every game path to exactly one member
   before any closure runs, so a member landing in two archives is
   byte-identical by construction. The *builders* must be separate, because
   `ClosureBuilder`'s memos are per run: under one shared builder a member two
   maps reference would belong to whichever map was walked first, and adding a
   map that sorted earlier would migrate it out of an archive that already
   exists.
2. **A per-archive upstream-source list in the notice.** It was computed from
   all packaged members, so a new map that introduced a new source rewrote
   every archive's notice. It shows: `openarena-maps` appears in the map
   archive's notice and in no other.
3. **A per-map recipe fragment** (`content/maps/<name>.json`) carrying that
   map's arena definition, its own accepted unresolved references and its own
   generated members. The notice records the digest of the archive's *own*
   selection input, so a single whole-set recipe would have put a whole-set
   digest inside every archive's bytes.
4. **Fragment-local acceptances.** `acceptedUnresolved` must be hit exactly —
   an unhit entry is a stale-acceptance failure — so a globally declared
   acceptance would be stale in every archive that does not reference it.
   `music/sonic5.wav` is `oa_pvomit`'s and lives in `oa_pvomit`'s fragment.
5. **A map-free, count-free package name.** `_notice_text` puts the package
   name on the notice's first line and the id further down, and they read
   `arena-web-ffa-oa_pvomit` and "one-map". Both are inside every archive's
   bytes. They are now `arena-web-ffa` and "arena-web FFA content pack".
6. **Per-map `scripts/<map>.arena` files.** A whole-set `scripts/arenas.txt`
   is a base member that names every packaged map. The base still generates
   that file, because the QVMs open it by name, but it carries **no arena
   block at all** — that the base names no map is a build gate, not a comment.
   `G_LoadArenas` reads the per-map `.arena` files straight afterwards.

### The map set is the fragment directory

The root recipe carries no list of maps. It is the base archive's own selection
input, and its digest is what the base's notice records, so a map set inside it
would move the base's bytes — and every existing map archive's — whenever a map
was added. `content/maps/` is the map set.

The fragments do not thereby escape the release identity. The content manifest
records one input per fragment with its digest
(`arena-web-map-<name>`), the manifest is an authority whose own digest is a
`compatibility` member, and `release_index.py` checks that set against the
directory in **both** directions: an enumerated fragment that is missing, a
fragment on disk that is not enumerated, and a digest that does not match are
all failures. `arena_runtime.py` and `arena_server.py` read a fragment only
after its digest matches the identity the manifest records.

### Served names carry their own digest

A content archive is served under an immutable cache policy, so its served name
carries the first 16 hex characters of its own SHA-256 while its manifest path
stays a stable literal. The digest half is **derived, not trusted**: a name
published with a stale hash over current bytes would be cached for a year, the
loader would throw on the mismatch, and the client would have no recovery path.

### Seven player presentations, not eleven

The base was to carry the eleven models that ship a preferred source form.
Running the closure against the pinned archives cut that to seven —
`assassin`, `gargoyle`, `kyonshi`, `liz`, `major`, `penguin`, `skelebot`:

| Model | Why it is not packaged |
| --- | --- |
| `angelyss` | the pinned archives carry `animation.cfg` and three `lower*.md3` and nothing else — no upper, no head, no skin, no icon |
| `grism` | seven textures and no model at all |
| `beret` | its default skins map every surface to `models/players/beret/skin1.tga`, whose shader has a second stage on `spec_skin1.tga`, which no pinned archive provides. A stage with a missing image makes `ParseStage` fail, which makes the *whole* shader the default shader, so every beret surface would render untextured |
| `neko` | `upper_default.skin` maps both its surfaces into `models/players/hnt/`, which holds one file, and `lower_default.skin` names a `claw.tga` that is not shipped |

All four have a preferred source form; what they lack is a working presentation
in the archives this recipe pins. Fixing either pair means adding a content
source, which is a recipe decision with its own licence audit.

The eight references the seven kept models leave dangling are authoring residue
inside the MD3s: three absolute paths from a modeller's machine, one bare
material name, and four upstream names missing a path separator. They are accepted
with the reason that they are never looked up: `cg_players.c` registers a
custom skin for every player model, and `R_AddMD3Surfaces` then resolves each
surface through that skin alone, falling to `tr.defaultShader` rather than to
the MD3's own shader name for a surface the skin does not cover.

## Amendment of 2026-09-01: the audited map set

The pack carried one map. This amendment records the audit behind the set it
carries now: which maps, who made them, what the licence position is for each,
what each map archive accepts as an unresolved upstream reference, and what a
real engine did with them.

The archives are published in batches, because each publication re-identifies
the release. The audit below covers the whole audited set; the authority for
which archives exist at any commit is
`provenance/arena-web-ffa-content-manifest.json`, which records one
`arena-web-map-<name>` input per published fragment, and
`release/browser-release.json`, which names every served file.

### The set, and how it was cut

Twenty-nine maps, every one already inside the six digest-pinned archives this
recipe selects from. **No new content source was added**, which was checked
rather than assumed: the member provenance of all twenty-nine closures resolves
to `openarena-088-data`, `openarena-data`, `openarena-maps`, `openarena-misc`
and `openarena-textures` and to nothing else, so `content/pack-recipe.json`
stays untouched as maps are added and the base archive stays byte-stable.

Sixty-eight maps ship a BSP. The criteria, in the order they cut:

1. **A licence this project can stand behind.** `maps/oa_thor.*` is GPL-2-only
   in Debian's per-file review and is excluded by an enforced rule, below.
2. **An arena definition upstream.** Seven maps ship a BSP with no entry in
   either `scripts/arenas.txt`, so they have no `longname` and no declared game
   type. Inventing one is a product decision, not an audit result.
3. **A determinable author.** Five maps carry no credit anywhere in the
   archives — no `credits` line, no role line, no per-map notes, no author key
   in the `.map`. They are redistributable and not creditable, which is not the
   same thing.
4. **A game type this profile can start.** Of the remainder, the maps upstream
   tags `ffa` or `tourney`.
5. **A working presentation in these archives**, decided by rendering each
   candidate rather than by reading its closure report. Three maps failed it
   and are recorded below.

### The licence position

**The authority is Debian's machine-readable `debian/copyright` for each source
package**, cited by URL and digest above, and it declares `Files: *` as
`GPL-2+` with exactly two narrower stanzas across the whole set: the `merman`
player model and the `oa_thor` map, both GPL-2-only. That is a per-file review
by a distribution that has to defend it, cross-checked here against the
material itself — the `COPYING` text, the `README` release terms, the
`source/assets/maps/credits` author lines and the per-file bot headers.

**No map in the set falls under either narrower stanza**, so every packaged
member is `GPL-2.0-or-later`. This is enforced and was exercised rather than
argued: each recipe source declares its `nonDefaultLicensePaths`, the exclusion
is applied globally rather than per source — so a path one source declares as
differently licensed cannot slip in because a higher-precedence source happens
to provide the same game path — and an assembly naming `oa_thor` aborts with

    profile map oa_thor: maps/oa_thor.bsp is covered by pattern 'maps/oa_thor.*',
    which openarena-088-data declares as differently licensed from its source
    expression; it must be selected out or declared separately

even though `openarena-maps` ships that same game path with no restriction
declared.

**All twenty-nine ship a Radiant `.map` preferred source form** in the same
digest-pinned tarball as the BSP, so the corresponding-source offer is complete
for every map. Checked against the extracted trees by name, including the two
mismatches upstream carries — `oa_thor`'s source is `oa_Thor.map` and
`oa_spirit3`'s is `oa_spirit3ctfduel1.map`. Neither map is in this set.

### Per-map authorship

GPL-2 grants the right to redistribute; it does not write the credits. This
table is what the credits have to be right about. Every citation is a file
inside the six pinned archives.

| Map | Longname | Credited author(s) | Evidence in the pinned archives | Note |
| --- | --- | --- | --- | :-: |
| `aggressor` | Aggressive Tendencies | Tyrann | source/assets/maps/credits; CREDITS 'Tyrann - Map (Aggressor)' | G |
| `am_galmevish` | GalMevish | Luciano 'Armageddon_Man' / 'Neon_Knight' Balducchi | worldspawn message 'GalMevish by Armageddon_Man'; CREDITS 'Armageddon_Man - Maps (am_gamelvish)' | — |
| `am_galmevish2` | Galmevish Yards | Luciano 'Neon_Knight' Balducchi | worldspawn message 'GalMevish by Armageddon_Man'; CREDITS-0.8.8 'Neon_Knight - Maps (am_ series)' | — |
| `am_spacecont` | Space Contact | Luciano 'Neon_Knight' Balducchi | CREDITS-0.8.8 'Neon_Knight - Maps (am_ series)'; readme_088 new-map list | — |
| `am_underworks2` | Under Working 2.0 | Luciano 'Neon_Knight' Balducchi | CREDITS-0.8.8 'Neon_Knight - Maps (am_ series)'; am_underworks/ source subdirectory | — |
| `ce1m7` | Lava House Of Thon | Ed (OpenArena conversion); American McGee (original Quake 1 e1m7) | source/assets/maps/credits 'ce1m7 - Ed, American McGee'; CREDITS 'Ed - Map (Conversion of e1m7)' | A C |
| `czest1dm` | Monastery At Night | Cestmir 'Czestmyr' Houska | czest1dm.txt copyright + licence header; CREDITS | — |
| `czest1tourney` | The Space Spire | Cestmir 'Czestmyr' Houska | CREDITS 'Czestmyr - Maps (czestdm1, czest1tourney, czest2ctf, czest3ctf)' | — |
| `dm6ish` | Darkest Hour | SavageX (Maik Merten) | worldspawn message 'dm6ish by maik merten'; CHANGES 'DM6ISH added, map by SavageX (adapted from Nexuiz map sources)'; CREDITS 'SavageX - Maps' | E |
| `kaos2` | Khaos Everywhere! | Vondur | worldspawn message 'Khaooohs -- by Vondur'; CREDITS 'Vondur - Map (kaos)' | G |
| `mlca1` | Meisterlampe's Temple | '[uM]Meisterlampe' (mapper); Luciano 'Neon_Knight' Balducchi (locations, bot support) | mlca1_readme.txt; CREDITS-0.8.8 'meisterlampe - Maps (mlca1, mlctf1)' | — |
| `oa_dm1` | Think Twice Or Die | id Software / John Romero (original Quake 1 dm1); OpenArena converter not named | worldspawn 'Place of Two Deaths (converted from q1 sources)'; CHANGES "Quake's DM maps converted, from dm1 to dm7" | A B |
| `oa_dm2` | Trappie Land | id Software / John Romero (original Quake 1 dm2); OpenArena converter not named | worldspawn 'Claustrophobopolis (Converted from q1 sources)' | A B |
| `oa_dm3` | Abandoned Base | id Software / John Romero (original Quake 1 dm3); Alias Conrad Coldwood (OpenArena conversion) | worldspawn 'The Abandoned Base'; oadm3readme.txt signed 'Alias Conrad Coldwood, August 2nd 2007' | A |
| `oa_dm4` | A Bad Place | id Software / John Romero (original Quake 1 dm4); OpenArena converter not named | worldspawn 'The Bad Place (converted from q1)' | A B |
| `oa_dm5` | Inner Cistern | id Software / John Romero (original Quake 1 dm5); OpenArena converter not named | worldspawn 'The Cistern' | A B |
| `oa_dm6` | The Dark Zone | id Software / John Romero (original Quake 1 dm6); OpenArena converter not named | worldspawn 'The Dark Zone (converted from q1)' | A B |
| `oa_dm7` | Outer Cistern | id Software / John Romero (original Quake 1 dm7); sago007 (OpenArena conversion) | source/assets/maps/credits 'oa_dm7 - converted from quake 1 by sago007' | A |
| `oa_koth1` | Repulsive Castle | cosmo | maps_cosmo.txt entry 'oa_koth1 / Repulsion / new map from scratch' | — |
| `oa_minia` | Kit's Mini Arena | kit89 | arena longname "Kit's Mini Arena"; CREDITS 'kit89 - Models, maps' | — |
| `oa_pvomit` | Projectile Vomit | Henke 'Stjartmunnen' Bjork (original); dmn_clown (OpenArena conversion) | source/assets/maps/credits; oa_pvomit.txt signed by Henke Bjork | D |
| `oa_rpg3dm2` | Trial By Error | Robert P. Gove Jr ('RPG') | rpg3dm2source.txt full readme with copyright | F |
| `oa_shine` | Shine In The Atmosphere | Henke 'Stjartmunnen' Bjork (original); dmn_clown (OpenArena conversion) | source/assets/maps/credits; oa_shine.txt signed by Henke Bjork | D |
| `oa_shouse` | Strange House | Jonathan 'Amphetamine' Garrod | source/assets/maps/credits 'oa_shouse - Jonathan "Amphetamine" Garrod'; CREDITS | — |
| `pul1duel-oa` | Five Steps Ahead | 'pulchr' (Hans Litgard) | pul1duel-oa-readme.txt full readme with author and contact | — |
| `sleekgrinder` | Sleek Grinder | cosmo | maps_cosmo.txt entry 'sleekgrinder / Sleek Grinder / new map from scratch' | — |
| `slimefac` | Slime Facility | cosmo | maps_cosmo.txt entry and pak6-patch088/slimefac.txt 'Author: cosmo' | — |
| `suspended` | Suspended Satellite | BaronOfHell | source/assets/maps/credits 'Suspended - BaronOfHell'; CREDITS 'BaronOfHell - Map (Suspended), texture, shaders' | — |
| `wrackdm17` | Never Ending Yard | cosmo | maps_cosmo.txt entry 'wrackdm17 / Never Ending Yard' | E |

Nine of the twenty-nine are named in `source/assets/maps/credits`; the rest
rest on a role line in `CREDITS`, `CREDITS-0.8.5` or `CREDITS-0.8.8` — all
three of which are packaged notice members in every archive — on a per-map
readme in the source tree, or on the `.map` worldspawn `message`. Every author
named above has at least one of those.

### The notes, and why none of them is a licence blocker

Fifteen of the twenty-nine carry a note. **They are attribution-quality
observations, not licence defects**, and the distinction is the one the licence
position already rests on: the authority is a distribution's per-file review,
not the completeness of an upstream author's readme. Holding the set to a
higher bar than that review would also disqualify the map this pack has shipped
since WP3.

| Note | What it is | Maps |
| --- | --- | --- |
| **A** | Conversion of an id Software Quake 1 map. OpenArena's licence argument is in the packaged `README`: John Romero released the Quake map sources under GPLv2, with an external URL as the citation. The argument travels with the pack, since `README` is one of the six notice members every archive carries, but its evidence is a link rather than a shipped licence file. Each of these maps also ships a `dmN.diff.bz2` patch against the original Quake sources, which makes the derivation explicit rather than hidden | `ce1m7` `oa_dm1` `oa_dm2` `oa_dm3` `oa_dm4` `oa_dm5` `oa_dm6` `oa_dm7` |
| **B** | The person who did the OpenArena conversion is not named anywhere in the archives, so the conversion cannot be credited even though the original can | `oa_dm1` `oa_dm2` `oa_dm4` `oa_dm5` `oa_dm6` |
| **C** | `source/assets/maps/credits` reads `ce1m7 - Ed, American McGee`. Romero's release covered *his own* maps; e1m7 is credited here to McGee, so the chain from McGee's map to that release is not established in the archives | `ce1m7` |
| **D** | The original author's shipped `.txt` is a release note — build time, player count, beta-tester thanks — with no permission or licence statement of any kind. `oa_shine`'s also names a second texture author ("by me and Evillair") with no licence record here | `oa_pvomit` `oa_shine` |
| **E** | Adapted from a third-party project's map. `CHANGES` and `maps_cosmo.txt` name Nexuiz, whose map sources are GPL, but neither record names the original map or its author, so the chain cannot be walked from the archives | `dm6ish` `wrackdm17` |
| **F** | The author's own grant carves the textures out: `rpg3dm2source.txt` gives a full GPLv2-or-later grant with a copyright line and then says "Textures copyright (C) their respective owners", crediting id Software and Lunaran. **Checked against the shipped closure, where it does not reach**: every texture `oa_rpg3dm2` packages comes from `openarena-textures`, OpenArena's own set — `base_ceiling`, `base_floor`, `base_light`, `base_support`, `base_trim`, `base_wall`, `liquids`, `sfx`, `skies` — plus one `textures/effects` member from `openarena-data`. `CHANGES` records that OpenArena retextured the map, and the member provenance is what confirms it | `oa_rpg3dm2` |
| **G** | The GPLv2 claim for a third party's map lives only in OpenArena's own `CHANGES` — "Tyrann's Aggressor map converted over. (GPL v2)" — which is one project stating another author's licence on their behalf. For `kaos2` the assertion names `kaos`, its sibling, and not `kaos2` at all | `aggressor` `kaos2` |

Four maps are stronger than the baseline rather than weaker, carrying the
author's own written grant inside the pinned archives: `czest1dm`
(`Copyright (C) 2007 Cestmir "Czestmyr" Houska` and the full GPL-2-or-later
paragraphs), `mlca1` (`Map released under GPLv2.` and the full text),
`pul1duel-oa` (a Copyright / Permissions section) and `oa_rpg3dm2` (note F).

### What each map archive accepts as unresolved

An archive's accepted list is per fragment and must be hit **exactly**: an
entry nothing references fails the build as stale, an unlisted dangling
reference fails it as unresolved. The lists were produced by running each map's
real closure with an empty acceptance list and recording what came back, not by
copying a template. Five classes, over the twenty-nine kept maps:

| Class | Count | What it is |
| --- | --- | --- |
| sky outerbox | 90 across 15 maps | OpenArena's sky shaders write `skyParms full <height> -` and `ParseSkyParms` expands that outerbox name into six images the release does not ship. Harmless by the renderer's own code: a missing outerbox image becomes `tr.defaultImage`, and the box is drawn only `if (outerbox[0] && outerbox[0] != tr.defaultImage)`, so it is skipped and the cloud layers still draw |
| worldspawn music | 14 across 13 maps | A `music` key naming a track no pinned release ships. `kaos2` contributes two, because the lookup tries the name and the name plus `.wav`; `oa_dm2`'s single key names an intro and a loop track in one value. A missing track is silence |
| entity sounds | 2 across 2 maps | A `noise` key naming a sound the release does not ship — `am_underworks2` and `oa_shouse` |
| `textures/NULL` | 4 across 4 maps | q3map2's placeholder for a face the compiler could not texture, written into the BSP's shader lump — `oa_dm1`, `oa_dm2`, `oa_dm4`, `oa_dm6`. No OpenArena release ships an image for it |
| images named but absent | 3 across 3 maps | An image a shipped shader or the BSP's shader lump names that no pinned archive provides. This is the only class a player can see, so it decided the cut below |

### What the engine did with them

Every candidate was loaded in the pinned native client on a virtual display,
with three bots, sound enabled and `developer 1`, and a frame was captured from
each. Two things came out of it that a closure report cannot give.

The face shares below are recomputable from the pinned archives without that
run: an IBSP header is followed by 17 (offset, length) lump pairs, lump 1 holds
72-byte texture records whose first 64 bytes are the shader name, and lump 13
holds 104-byte face records whose first four bytes are the index into lump 1.
Counting faces per shader name is what produced every percentage here. The
frames themselves are preparation evidence and are not committed; the counts
are what the decision rests on.

**The console is silent about the worst case.** A name in a BSP's shader lump
that has neither a shader script nor an image file makes `R_FindShader` set
`shader.defaultShader` and report it at `PRINT_DEVELOPER` only, so an ordinary
run says nothing at all while the surface renders as the default grid. Three
maps were therefore cut on the *rendered frame* and on the share of their
geometry the affected shader covers, not on their console output:

| Map | Affected faces | Why it is not in the set |
| --- | --- | --- |
| `pxlfan` | **682 of 1,725 (39.5 %)** | `textures/desertfactory_metal/metal05` is the map's floor. Nearly two fifths of the geometry renders as the default grid, and the captured frame shows it |
| `am_lavaarena` | **114 of 928 (12.3 %)** | three names — the barrels (`desertfactory_metal/barrel01`, `barrel01_top`) and the ceiling lights (`base_light/ceil1_4_10k`), all in the playable space |
| `oa_koth2` | 26 references | the whole `cosmo_block` / `cosmo_sfx` / `cosmo_trim` / `cosmoflash` set. Not an audit artefact: it names `textures/cosmo_block/beton3`, which is in no pinned archive, while the release ships `textures/cosmo_trim/beton3.tga` |

The three kept maps in that class are one to two orders of magnitude below
them, which is where the evidence separates rather than where a threshold was
chosen: `oa_koth1` 31 of 2,200 faces (1.4 %), `am_underworks2` 26 of 4,197
(0.6 %), and `kaos2` **0** — its shader lump names
`textures/gothic_floor/goopq1metal7_98d` but no face uses it.

Recovering any of the three cut maps means adding a content source, which is a
recipe decision with its own licence audit, so they are held out rather than
patched.

**One accepted reference had no accepted engine note.** `SP_target_push`
registers `sound/misc/windfly.wav` unless the entity carries the `bouncepad`
spawnflag (ioq3 `code/game/g_trigger.c`), and no OpenArena release ships that
file; the recipe has always accepted it as a dangling reference of the
gamecode's own closure. `oa_pvomit` has no non-bouncepad `target_push`, so it
never fired and `scripts/arena_acceptance.py` never listed it. Six of the
audited maps do have one — `czest1tourney`, `oa_shine`, `sleekgrinder`,
`slimefac`, `wrackdm17` and the cut `pxlfan` — and in the native run it fired
on exactly those six and no others. It is an accepted note now.

**And one accepted note stops firing.** `flareShader` is registered by the
renderer itself and its image `gfx/fx/flares/blur.tga` is not in the base
closure, which is why the acceptance has always reported it. Ten of the audited
maps name `flareShader` directly in their BSP shader lump, so their closures
resolve it and package that image; with any of them in the set the renderer
finds it and the note goes quiet. The note is kept rather than deleted, because
it is correct again for any set without those maps.

### The base is the pack's shader authority

Splitting one archive into many put two orders of precedence against each
other, and at one map they could not disagree.

`ShaderIndex` resolves a shader name over the whole source set and lets the
alphabetically highest file win, which is right for a single archive because
`write_pk3` stores members sorted and `ScanAndLoadShaderFiles` concatenates the
listing in reverse. At run time, though, the *archive* order decides first:
`FS_AddGameDirectory` walks the PK3s descending, so the base's definitions beat
every map archive's — but between two map archives the archive name decides and
the file name has no say. Two upstream files that define the same name, landing
in two different map archives, therefore resolve to the definition the index
did not choose, and the closure has packaged the other definition's images.

Assembling all thirty-one candidates is what found it: 37 shader names across
five upstream file pairs, `scripts/cosmoflash.shader` against
`scripts/am_cosmoflash.shader` the largest at 30. Dropping maps does not fix
it — over that set `scripts/detailtest.shader` was reached by 22 maps and
`scripts/evil8_base.shader` by seven, so that pair survives almost any subset.

The fix is root 8: **every `scripts/*.shader` in the sources goes into the base
and into no other archive**. Then the base always wins, and inside one archive
the stored order is exactly what `ShaderIndex` assumes, so the two orders
coincide by construction. It costs 70 files and 578,756 uncompressed bytes
(63,394 compressed), and it takes every shader file *out* of the map archives.
The selection is over the sources, which the recipe pins by digest, so it does
not depend on the map set and a later map still leaves the base byte-identical.

The two orders coincide only if the *same* comparator decides both, which was
not true when this was first written: `shader_file_precedence` folded case
while `write_pk3` and the engine sort the raw path, and `scripts/QTex.shader`
is the one capitalised shader file in these sources — `'Q' < 'a'` puts it first
in the stored order and therefore last in precedence, while a case-folded key
ranks it among the `q`s. Nothing collides across that pair, so nothing was
mis-resolved, but the key is the raw path now and the claim above is true as
written.

Two consequences, both stated rather than left to be found:

- **The rule is asserted rather than trusted.** `check_shader_resolution`
  catches a resolved name whose run-time winner moved — the loud half.
  `check_shader_authority` catches the quiet half: a shader file the base
  leaves out, or one left in a map archive whose names happen not to collide,
  breaks the rule without moving any name that resolves today and restores the
  hazard for the next map set. It also refuses a `selects` pattern matching no
  source path, because that one satisfies every other check by packaging
  nothing.
- **The pack gains 1,338 shader names it did not resolve before.** They change
  nothing that was audited, because the closure has always resolved names
  through an index built over *all* the source files: for every reference the
  closure sees, the winner and its images are what they were. Outside the
  closure the engine and client register seven names of their own —
  `projectionShadow`, `flareShader`, `sun` and `gfx/2d/sunflare`
  (`renderergl2/tr_shader.c` `CreateExternalShaders`), and `gfx/2d/bigchars`,
  `white` and `console` (`client/cl_main.c` `CL_InitRenderer`). Four of the
  seven are defined by a packaged shader file: `flareShader` and `sun` by
  `scripts/oaflares.shader`, which the pack already carried, `console` by
  `scripts/oanew.shader` — its missing image is a standing accepted note — and
  `projectionShadow` by `scripts/decals.shader`, whose only stage image is
  `$whiteimage`. A control run of `oa_pvomit` against the previously published
  pair of archives and against the new set produced the same engine output.

## Amendment of 2026-09-02: what the manifest records, and two bounds

The archive set did not change and no archive's bytes moved. What changed is
what the **content artifact manifest** says about each archive, and two bounds
that are now read out of the pinned engine instead of being assumed.

### Three records per archive

`provenance/arena-web-ffa-content-manifest.json` carries, beside every
artifact's identity:

| Field | On | What it is |
| --- | --- | --- |
| `uncompressedSize` | every archive | the sum of its members' uncompressed bytes, computed from the members this build wrote |
| `map` | map archives only | the map it carries — the selection key a rotation is expressed in |
| `peakHunkBytes` | map archives only | the measured peak engine hunk of that map |

They are here rather than in `arena/game-profile.json` because there should be
one home per fact: the manifest is generated from the archives it describes, it
is an authority whose digest is a `compatibility` member, and the browser
fetches it before any archive. `map` exists so a consumer selects by a declared
field rather than by parsing an archive file name.

### Why the measurements are not in a fragment

Peak hunk cannot be computed from the sources; it is measured by loading the
map. The obvious home for a per-map number is that map's fragment — and it is
the wrong one. Each archive's generated notice carries the SHA-256 of its own
selection input (`content/pack-recipe.json` for the base,
`content/maps/<name>.json` for a map), so a number added to a fragment moves
that map archive's bytes and therefore its immutable URL. A measurement must
not be able to move content.

The figures therefore live in `records/map-resource-measurements.json`, which is
not a selection input, and the build copies them into the manifest.
`scripts/release_index.py` binds the record into the release identity as the
content-manifest input `arena-web-resource-measurements` and checks the
manifest's copy against it in both directions.

**The scope of that check, stated because the name promises more.** It is a
copy-consistency check: editing either side alone is a failure, but a *wrong
measurement* is not detectable, because nothing can recompute it. What stands
behind the numbers is the run that produced them and one gate — the record
names the engine commit it was measured against, and an engine that has moved
is refused, because peak hunk is an engine-behaviour measurement that does not
survive a move unremeasured.

### Two bounds read from the pinned engine

- **Peak hunk against `DEF_COMHUNKMEGS`.** `Com_InitHunkMemory` allocates
  `com_hunkMegs` megabytes, defaulting to `DEF_COMHUNKMEGS`, and refuses to go
  below `MIN_COMHUNKMEGS`, which is defined as the same constant — so it is the
  default and the floor for a client, not a ceiling: `com_hunkMegs` is
  `CVAR_LATCH|CVAR_ARCHIVE` and a larger value raises the allocation, and a
  dedicated server floors at `MIN_DEDICATED_COMHUNKMEGS` instead. The bound this
  gate enforces is the default, which is what an unconfigured client gets. Every
  declared figure must fit it, read out of `ioq3/code/qcommon/common.c` rather
  than restated. The published set peaks at 35,641,800 bytes (`suspended`)
  against 134,217,728.
- **The published archive set against `BIG_INFO_STRING`.**
  `SV_SpawnServer` assembles all `CVAR_SYSTEMINFO` cvars with
  `Cvar_InfoString_Big` into one 8192-byte buffer, and `sv_referencedPakNames`
  and `sv_referencedPaks` grow with the referenced archives.
  `Info_SetValueForKey_Big` neither truncates nor fails on overflow: it prints
  one line and returns, leaving out whichever key first did not fit — and
  `Cvar_InfoString_Big` walks `cvar_vars` in list order, so it is not even
  predictably a pak key. `arena_runtime.check_systeminfo_budget` projects the
  string over the *whole published set*, as if every archive were referenced,
  and refuses a set that would not fit.

  Measured rather than assumed: a dedicated server started with all nine
  published archives reports two referenced archives, not nine —
  `FS_ClearPakReferences(0)` runs on every `SV_SpawnServer` and only the base
  and the loaded map are opened afterwards — and its `CS_SYSTEMINFO` is 275
  bytes of 8192 across thirteen cvars. The projection is pessimistic on
  purpose, because a bound may not depend on which files a session happens to
  open. Each archive costs `37 + len(<map name>)` bytes, so the ceiling is
  `(8192 − 590) / (37 + longest map name)` — 149 map archives at today's
  longest name, `am_underworks2`. Any rotation is a subset of the published set
  and the string grows monotonically with it, so a published set that fits
  leaves every rotation of it fitting.

  **This is not the constraint on rotation size.** It was expected to be; it is
  not, by two orders of magnitude against the 29-map v1 set, which projects to
  1,908 bytes. What bounds a rotation is what a player downloads and holds in
  the tab, which is why the manifest now records both sizes.
