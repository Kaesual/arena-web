#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble the audited arena-web content pack and emit its committed identities.

The closure roots are the pinned `baseq3` QVM references plus the one map, one
player presentation and bot data the recipe names. Everything else follows from
the content itself. The PK3 goes to a gitignored build directory; the provenance
record, artifact manifest and closure report are the reviewable outputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from content_pack import (
    ClosureBuilder,
    ContentError,
    SourceSet,
    _game_path,
    build_provenance,
    file_sha256,
    iter_forbidden,
    load_recipe,
    recipe_sources,
    provenance_sources,
    validate_provenance,
    write_pk3,
)
from game_assets import parse_animation_cfg, parse_key_value_blocks
from metadata import (
    ARTIFACT_SCHEMA,
    MetadataError,
    _canonical_json_identity,
    _load_json,
    validate_artifact_manifest,
    validate_baseline,
)
from qvm_references import (
    _reachable_headers,
    baseq3_references,
    baseq3_translation_units,
    select_compiled_lines,
)

PRODUCER_NAME = "arena-web scripts/build-content-pack.sh"

# The reference kinds ClosureBuilder can expand; a derived reference must name
# one of these so `builder.add` dispatches to a real handler.
DERIVED_REFERENCE_KINDS = (
    "bsp",
    "botfile",
    "file",
    "image",
    "model",
    "shader",
    "skin",
    "sound",
)

_DERIVED_ENTRY_BASE_FIELDS = frozenset(
    {"constructedFrom", "construction", "kind", "reference"}
)
_DERIVED_CONSTRUCTION_FIELDS = frozenset({"appends", "file", "lines"})

# A construction site is exactly three adjacent lines: strip, append,
# register. Bounding the citation to that span, together with requiring all
# three markers inside it, makes each declared citation the *unique* passing
# range for its suffix — a range shifted by even one line drops a marker, and
# a wider one could contain a neighbouring site's markers.
_CONSTRUCTION_SITE_MAX_SPAN = 2

# The registration trap through which every derived weapon-model name reaches
# the renderer. A cited construction site must contain it, or the cited lines
# are not the place the constructed name is used.
_DERIVED_REGISTRATION_TRAP = "trap_R_RegisterModel"

# A derived-name suffix exactly as the pinned sources spell it: a string
# literal of the shape "_<alphanumeric>.md3", which is the adjacent
# COM_StripExtension/Q_strcat spelling both pinned sites use. The scan sees
# only this shape — a construction spelled through a format string such as
# Com_sprintf("%s_hand.md3", ...), or a non-.md3 suffix, would be outside its
# reach and would need this pattern extended. The pinned tree contains no
# such spelling; a future engine pin must be re-checked when it moves.
_SUFFIX_LITERAL_RE = re.compile(r'"(_[A-Za-z0-9]+\.md3)"')

# One bg_itemlist weapon entry: classname, pickup sound, then world_model[0].
_WEAPON_ITEM_RE = re.compile(r'"(weapon_\w+)"\s*,\s*"[^"]*"\s*,\s*\{\s*"([^"]+)"')

# The pinned bg_itemlist defines exactly this many baseq3 weapons (the ten
# from gauntlet through grapple; the MISSIONPACK ones are compiled out). The
# parser must find them all, or an entry it silently failed to match would
# shrink the derivation space unnoticed. This number moves only with the
# engine pin.
_BASEQ3_WEAPON_COUNT = 10


def _encode(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _check_profile(recipe: dict[str, Any]) -> None:
    """Reject a profile whose parts disagree before anything is assembled."""
    profile = recipe["profile"]
    arena = profile["arena"]
    if arena["map"] != profile["map"]:
        raise ContentError(
            f"profile.arena.map {arena['map']!r} is not profile.map {profile['map']!r}"
        )
    packaged_models = set(profile["playerModels"])
    for bot in profile["bots"]:
        if bot["model"] not in packaged_models:
            raise ContentError(
                f"bot {bot['name']!r} uses model {bot['model']!r}, which the profile "
                f"does not package: {sorted(packaged_models)}"
            )
    declared = arena["bots"].split()
    actual = [bot["name"] for bot in profile["bots"]]
    if declared != actual:
        raise ContentError(
            f"profile.arena.bots {declared} does not list the profile's bots {actual}"
        )


def _reconcile_templates(
    references: dict[str, Any], recipe: dict[str, Any]
) -> dict[str, Any]:
    """Check the recipe's template expansions against the pinned QVM sources.

    This needs no archive, so it runs before any upstream byte is read: a
    recipe that has drifted from the gamecode should fail in a second, not
    after a gigabyte of verification.
    """
    declared = {entry["template"]: entry for entry in recipe["templateExpansions"]}
    used: set[str] = set()
    for module_references in references.values():
        used |= set(module_references.templates)
        used |= {name for _kind, name in module_references.registration_templates}
    missing = sorted(used - set(declared))
    if missing:
        raise ContentError(
            f"reference templates have no recipe expansion: {missing}; the profile "
            "must state what each expands to or why it cannot"
        )
    unknown = sorted(set(declared) - used)
    if unknown:
        raise ContentError(
            f"recipe declares expansions for templates the QVMs do not use: {unknown}"
        )
    for template, entry in sorted(declared.items()):
        if entry["expansions"] and "kind" not in entry:
            raise ContentError(
                f"recipe template {template!r} expands without declaring which "
                "kind of reference its expansions are"
            )
    return declared


def _static_reference_paths(references: dict[str, Any]) -> set[str]:
    """The normalised set of references the two static readings extract."""
    static_references: set[str] = set()
    for module_references in references.values():
        static_references |= {
            _game_path(literal) for literal in module_references.literals
        }
        static_references |= {
            _game_path(name) for _kind, name in module_references.registrations
        }
    return static_references


def _weapon_world_models(engine_root: Path) -> list[str]:
    """Parse the weapon items' `world_model[0]` out of the pinned bg_itemlist.

    These are the bases every derived-name construction site applies to:
    both `cg_weapons.c` and `q3_ui/ui_players.c` walk `bg_itemlist` for an
    `IT_WEAPON` item and perform their string surgery on its first world
    model. `MISSIONPACK` entries are dropped the same way the reference
    extraction drops them, because the `baseq3` QVMs are not built with them.
    """
    path = engine_root / "code" / "game" / "bg_misc.c"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ContentError(f"cannot read the pinned item list {path}: {error}") from error
    compiled = "\n".join(select_compiled_lines(text, frozenset(), origin=str(path)))
    models: set[str] = set()
    for classname, model in _WEAPON_ITEM_RE.findall(compiled):
        if not model.lower().endswith(".md3"):
            raise ContentError(
                f"weapon item {classname!r} has a world model {model!r} this "
                "parser does not understand"
            )
        models.add(model)
    if len(models) != _BASEQ3_WEAPON_COUNT:
        raise ContentError(
            f"parsed {len(models)} weapon world models out of the pinned "
            f"bg_itemlist, expected {_BASEQ3_WEAPON_COUNT}; an entry the "
            "parser does not match would silently shrink the derivation "
            "space, so this fails instead"
        )
    return sorted(models)


def _scan_derived_suffix_sites(engine_root: Path) -> set[tuple[str, str]]:
    """Every `(file, suffix)` at which a compiled `baseq3` source holds a
    derived-name suffix literal of the exact shape `_SUFFIX_LITERAL_RE`
    recognises (see its comment for what that shape excludes).

    The scan runs over the exact translation units `cmake/basegame.cmake`
    compiles plus their reachable headers — the same file population the
    reference extraction reads — so a construction site in a source the QVMs
    do not contain (the missionpack `code/ui/ui_players.c`, for example) does
    not demand a declaration, while a new site added to a compiled source
    fails the build until the recipe declares it.
    """
    units = baseq3_translation_units(engine_root)
    files: set[Path] = set()
    for module_sources in units.values():
        files |= set(module_sources)
    files |= set(_reachable_headers(sorted(files), engine_root))
    root = engine_root.resolve()
    found: set[tuple[str, str]] = set()
    for path in sorted(files):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _SUFFIX_LITERAL_RE.finditer(text):
            found.add((path.resolve().relative_to(root).as_posix(), match.group(1)))
    return found


def _reconcile_construction_sites(
    recipe: dict[str, Any], engine_root: Path
) -> list[dict[str, Any]]:
    """Two-way check of the declared construction sites against the pinned tree.

    Forward: every declared site must exist — the cited lines must be a small
    adjacent range containing `COM_StripExtension`, the quoted suffix and the
    registration trap. Reverse: every suffix literal the compiled sources hold
    must belong to a declared site, so the recipe's account of *where* derived
    names come from cannot silently omit a site — the discipline
    `_reconcile_templates` applies to templates, applied to construction sites.
    """
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for site in recipe.get("derivedConstructionSites", []):
        if not isinstance(site, dict) or set(site) != _DERIVED_CONSTRUCTION_FIELDS:
            raise ContentError(
                "a derived construction site must have exactly the fields "
                f"{sorted(_DERIVED_CONSTRUCTION_FIELDS)}: {site!r}"
            )
        file_name = site["file"]
        lines = site["lines"]
        appends = site["appends"]
        if (
            not isinstance(lines, list)
            or len(lines) != 2
            or not all(isinstance(line, int) for line in lines)
            or not 1 <= lines[0] <= lines[1]
        ):
            raise ContentError(
                f"construction site {file_name}:{lines!r} lines must be "
                "[first, last] with 1 <= first <= last"
            )
        if lines[1] - lines[0] > _CONSTRUCTION_SITE_MAX_SPAN:
            raise ContentError(
                f"construction site {file_name}:{lines[0]}-{lines[1]} cites "
                f"{lines[1] - lines[0] + 1} lines; a site is a few adjacent "
                "lines, and a wider range could contain several sites at once"
            )
        if Path(file_name).is_absolute() or ".." in Path(file_name).parts:
            raise ContentError(
                f"construction site names a file outside the engine tree: "
                f"{file_name!r}"
            )
        source_path = engine_root / file_name
        try:
            source_lines = source_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError as error:
            raise ContentError(
                f"cannot read constructing file {file_name}: {error}"
            ) from error
        if lines[1] > len(source_lines):
            raise ContentError(
                f"construction site {file_name} has {len(source_lines)} lines, "
                f"the recipe names {lines}"
            )
        snippet = "\n".join(source_lines[lines[0] - 1 : lines[1]])
        if (
            "COM_StripExtension" not in snippet
            or f'"{appends}"' not in snippet
            or _DERIVED_REGISTRATION_TRAP not in snippet
        ):
            raise ContentError(
                f"construction site {file_name}:{lines[0]}-{lines[1]} does not "
                f"construct and register a name by appending {appends!r} to a "
                "stripped base name"
            )
        key = (file_name, appends)
        if key in seen:
            raise ContentError(
                f"construction site {file_name} / {appends!r} is declared twice"
            )
        seen.add(key)
        validated.append(site)

    scanned = _scan_derived_suffix_sites(engine_root)
    undeclared = sorted(scanned - seen)
    if undeclared:
        raise ContentError(
            "the pinned sources construct derived names at sites the recipe "
            f"does not declare: {undeclared}"
        )
    unknown = sorted(seen - scanned)
    if unknown:
        raise ContentError(
            "the recipe declares construction sites the pinned sources do not "
            f"contain: {unknown}"
        )
    return validated


def _check_derived_references(
    recipe: dict[str, Any],
    sites: list[dict[str, Any]],
    world_models: list[str],
    static_references: set[str],
) -> list[dict[str, Any]]:
    """Check the recipe's derived references against the pinned gamecode.

    A derived reference is a name the gamecode *constructs* at runtime by
    string surgery on a weapon world model it holds statically, so neither of
    the two static readings can extract it: the only literal in the
    constructing code is the suffix. This check refuses an entry the pinned
    tree does not back:

    - the entry must cite one of the declared construction sites, which
      `_reconcile_construction_sites` has already verified against the tree
      two-way;
    - the reference must equal the declared base name with its extension
      stripped and the site's suffix appended — the derivation is recomputed,
      not trusted;
    - the base name must be a weapon world model out of the pinned
      `bg_itemlist` — the only bases the construction sites apply to — and a
      reference the static readings extract;
    - the derived reference must *not* be statically extracted, or it does not
      belong in this category at all (the fallback literal
      `models/weapons2/shotgun/shotgun_hand.md3` is the live example);
    - an entry either resolves to declared members or states why it is
      excluded, never both and never neither.

    Runs before any archive byte is read, like the template reconciliation.
    Returns the included entries; `_reconcile_derivation_space` covers the
    reverse direction over the whole derivation space once the sources are
    open, and `_check_derived_members` verifies the outcome after the closure
    is built.
    """
    world_model_paths = {_game_path(model) for model in world_models}
    included: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in recipe["derivedReferences"]:
        fields = set(entry)
        if fields == _DERIVED_ENTRY_BASE_FIELDS | {"members"}:
            excluded = False
        elif fields == _DERIVED_ENTRY_BASE_FIELDS | {"excludedReason"}:
            excluded = True
        else:
            raise ContentError(
                f"derived reference entry has unexpected fields {sorted(fields)}; "
                "an entry carries exactly the base fields plus either 'members' "
                "or 'excludedReason'"
            )
        reference = entry["reference"]
        kind = entry["kind"]
        if kind not in DERIVED_REFERENCE_KINDS:
            raise ContentError(
                f"derived reference {reference!r} has unknown kind {kind!r}"
            )
        key = _game_path(reference)
        if key in seen:
            raise ContentError(f"derived reference {reference!r} is declared twice")
        seen.add(key)

        construction = entry["construction"]
        if construction not in sites:
            raise ContentError(
                f"derived reference {reference!r} does not cite a declared "
                "construction site; its construction must equal one of the "
                "recipe's derivedConstructionSites records exactly"
            )
        appends = construction["appends"]

        constructed_from = entry["constructedFrom"]
        stem = re.sub(r"\.[^./]*$", "", constructed_from)
        if _game_path(reference) != _game_path(stem + appends):
            raise ContentError(
                f"derived reference {reference!r} is not {constructed_from!r} "
                f"with its extension replaced by {appends!r}"
            )
        if _game_path(constructed_from) not in world_model_paths:
            raise ContentError(
                f"derived reference {reference!r} is constructed from "
                f"{constructed_from!r}, which is not a weapon world model of "
                "the pinned bg_itemlist, the only bases the construction "
                "sites apply to"
            )
        if _game_path(constructed_from) not in static_references:
            raise ContentError(
                f"derived reference {reference!r} is constructed from "
                f"{constructed_from!r}, which is not a reference the static "
                "readings of the pinned QVM sources extract"
            )
        if _game_path(reference) in static_references:
            raise ContentError(
                f"derived reference {reference!r} is itself statically "
                "extracted from the pinned QVM sources; it does not belong in "
                "the derived category"
            )

        if excluded:
            if not str(entry["excludedReason"]).strip():
                raise ContentError(
                    f"derived reference {reference!r} is excluded without a reason"
                )
            continue
        if (
            not isinstance(entry["members"], list)
            or not entry["members"]
            or not all(
                isinstance(member, str) and member for member in entry["members"]
            )
        ):
            raise ContentError(
                f"derived reference {reference!r} must declare the non-empty "
                "member list it resolves to"
            )
        included.append(entry)
    return included


def _reconcile_derivation_space(
    entries: list[dict[str, Any]],
    sites: list[dict[str, Any]],
    world_models: list[str],
    static_references: set[str],
    sources: SourceSet,
) -> None:
    """The reverse direction over the whole derivation space.

    `_check_derived_references` is fail-closed per declared entry; on its own
    that is fail-open over the space of names the gamecode can construct,
    which is how a shipped-but-undeclared model would slip through. This check
    mirrors `_reconcile_templates`' two-way discipline: every weapon world
    model of the pinned `bg_itemlist` crossed with every suffix the declared
    construction sites append must be a declared derived reference (included
    or excluded), a statically extracted reference the ordinary closure
    already owns, or demonstrably absent from the pinned source set. A name a
    pinned archive provides that the recipe neither includes nor excludes
    fails the build, and so does an exclusion whose file no pinned source
    provides — a stale exclusion is dead text, like a stale acceptance.
    """
    declared = {_game_path(entry["reference"]): entry for entry in entries}
    suffixes = sorted({site["appends"] for site in sites})
    for base in world_models:
        stem = re.sub(r"\.[^./]*$", "", base)
        for suffix in suffixes:
            name = stem + suffix
            entry = declared.get(_game_path(name))
            if entry is not None:
                if "excludedReason" in entry and name not in sources:
                    raise ContentError(
                        f"derived reference {name!r} is excluded, but no pinned "
                        "source provides it; the exclusion is stale"
                    )
                continue
            if _game_path(name) in static_references:
                # The ordinary closure owns it — the shotgun_hand fallback
                # literal is the live example.
                continue
            if name in sources:
                raise ContentError(
                    f"the pinned gamecode constructs {name!r} and a pinned "
                    "archive provides it, but the recipe neither includes nor "
                    "excludes it"
                )


def _check_derived_members(
    entries: list[dict[str, Any]], members: dict[str, Any]
) -> None:
    """Fail if the finished closure disagrees with the derived declarations.

    Every included entry's declared members must be packaged, and every
    excluded reference must be absent — the exclusion is a build property, not
    only a committed-record test.
    """
    for entry in entries:
        if "excludedReason" in entry:
            if _game_path(entry["reference"]) in members:
                raise ContentError(
                    f"derived reference {entry['reference']!r} is excluded but "
                    "was packaged anyway"
                )
            continue
        for member in entry["members"]:
            if _game_path(member) not in members:
                raise ContentError(
                    f"derived reference {entry['reference']!r} declares member "
                    f"{member!r}, which the assembled closure did not package"
                )


def _check_generated_metadata(
    metadata: dict[str, str], recipe: dict[str, Any], members: dict[str, bytes]
) -> None:
    """Read the generated arena and bot files back and check what they name."""
    arenas = parse_key_value_blocks(metadata["scripts/arenas.txt"])
    if len(arenas) != 1 or arenas[0]["map"] != recipe["profile"]["map"]:
        raise ContentError(
            f"generated scripts/arenas.txt does not name exactly the profile map: {arenas}"
        )
    if f"maps/{arenas[0]['map']}.bsp" not in members:
        raise ContentError(
            f"generated scripts/arenas.txt names map {arenas[0]['map']!r}, "
            "which is not a packaged member"
        )
    bots = parse_key_value_blocks(metadata["scripts/bots.txt"])
    if len(bots) != len(recipe["profile"]["bots"]):
        raise ContentError("generated scripts/bots.txt lost or gained a bot")
    for bot in bots:
        model = bot["model"].partition("/")[0]
        if f"models/players/{model}/lower.md3" not in members:
            raise ContentError(
                f"generated scripts/bots.txt names model {bot['model']!r}, "
                "which is not a packaged member"
            )
        if bot["aifile"].partition("/")[0] != "bots":
            raise ContentError(f"bot {bot['name']!r} has an unexpected aifile path")
        if f"botfiles/{bot['aifile']}" not in members:
            raise ContentError(
                f"generated scripts/bots.txt names {bot['aifile']!r}, "
                "which is not a packaged member"
            )


def _generated_metadata(recipe: dict[str, Any]) -> dict[str, str]:
    """Return the product-owned arena and bot definitions of the FFA profile.

    The upstream `scripts/arenas.txt` and `scripts/bots.txt` describe the whole
    OpenArena release. This pack ships one map and the bots that play it, so it
    carries its own two files instead of a list whose entries it cannot honour.
    """
    profile = recipe["profile"]
    arena = profile["arena"]
    lines = ["{"]
    for key in ("map", "longname", "bots", "fraglimit", "type"):
        lines.append(f'{key}\t\t"{arena[key]}"')
    lines.append("}")
    arenas = "\n".join(lines) + "\n"

    blocks = []
    for bot in profile["bots"]:
        blocks.append(
            "{\n"
            f'name\t\t"{bot["name"]}"\n'
            f'model\t\t"{bot["model"]}"\n'
            f'aifile\t\t"{bot["aifile"]}"\n'
            "}"
        )
    bots = "\n\n".join(blocks) + "\n"
    return {"scripts/arenas.txt": arenas, "scripts/bots.txt": bots}


def _notice_text(
    recipe: dict[str, Any], provenance_sources: list[dict[str, Any]]
) -> str:
    lines = [
        f"{recipe['package']['name']}",
        "",
        "This archive is an assembled subset of freely licensed game content. It",
        "contains no gamecode: the arena-web prototype runs the GPL baseq3 game,",
        "client-game and user-interface modules built from the pinned ioquake3",
        "source, and this archive supplies only the data those modules load.",
        "",
        "Every member of this archive, and the exact upstream file it came from,",
        "is recorded with its licence and cryptographic digest in the arena-web",
        f"provenance record for package {recipe['package']['id']!r}.",
        "",
        "Upstream sources",
        "----------------",
    ]
    for record in provenance_sources:
        lines += [
            f"* {record['id']}",
            f"    licence:          {record['licenseExpression']}",
            f"    licence evidence: {record['licenseEvidenceUrl']}",
            f"    obtained from:    {record['sourceUrl']}",
            f"    identity:         {record['sourceIdentity']}",
            f"    preferred source: {record['preferredSourceUrl']}",
            f"                      {record['preferredSourceRevision']}",
        ]
    lines += [
        "",
        "Written offer for corresponding source",
        "--------------------------------------",
        "The GNU General Public License version 2 text is packaged as COPYING.",
        "The complete corresponding source of every GPL member of this archive is",
        "the upstream material identified above, obtainable at the URLs listed",
        "with the recorded digests, together with the arena-web assembly scripts",
        "and recipe, which are the arena-web repository at the commit whose",
        "content/pack-recipe.json has the SHA-256 shown for 'arena-web' above.",
        "",
        "Attribution",
        "-----------",
        "Author credits for the OpenArena material are packaged as CREDITS,",
        "CREDITS-0.8.5 and CREDITS-0.8.8, and are reproduced unmodified.",
        "",
    ]
    return "\n".join(lines)


def build(root: Path, arguments: argparse.Namespace) -> int:
    baseline = validate_baseline(
        _load_json(root / "locks" / "baseline.json"), "baseline"
    )
    recipe_path = root / "content" / "pack-recipe.json"
    recipe = load_recipe(recipe_path)
    recipe_digest = f"sha256:{file_sha256(recipe_path)}"
    generated = dict(recipe["generatedSource"])
    if (
        generated["sourceIdentity"] != "recipe"
        or generated["preferredSourceRevision"] != "recipe"
    ):
        raise ContentError(
            "generatedSource must bind its identity to the recipe with the literal 'recipe'"
        )
    generated["sourceIdentity"] = recipe_digest
    generated["preferredSourceRevision"] = recipe_digest
    recipe = dict(recipe, generatedSource=generated)

    _check_profile(recipe)
    engine_root = root / baseline["engine"]["submodulePath"]
    references = baseq3_references(engine_root)
    declared_templates = _reconcile_templates(references, recipe)
    static_references = _static_reference_paths(references)
    construction_sites = _reconcile_construction_sites(recipe, engine_root)
    weapon_world_models = _weapon_world_models(engine_root)
    derived_references = _check_derived_references(
        recipe, construction_sites, weapon_world_models, static_references
    )

    sources = SourceSet(recipe_sources(recipe), arguments.archive_dir)
    _reconcile_derivation_space(
        recipe["derivedReferences"],
        construction_sites,
        weapon_world_models,
        static_references,
        sources,
    )
    builder = ClosureBuilder(sources, recipe)
    profile = recipe["profile"]

    # 1. Everything the pinned baseq3 QVMs can name, by two independent
    #    readings of the same MISSIONPACK-filtered text: path-shaped string
    #    literals, and the first argument of every content-registration trap.
    #    Only the second reading sees names that are not paths at all, such as
    #    `white`, `menuback` or `powerups/quad`, which a shader script defines.
    for module in sorted(references):
        module_references = references[module]
        for literal in module_references.literals:
            builder.add_engine_reference(literal, f"{module} literal")
        for kind, name in module_references.registrations:
            builder.add(name, kind, f"{module} registration")
        templates = set(module_references.templates)
        templates |= {name for _kind, name in module_references.registration_templates}
        for template in sorted(templates):
            entry = declared_templates[template]
            for expansion in entry["expansions"]:
                builder.add(expansion, entry["kind"], f"{module} template {template}")

    # 1b. The references the gamecode constructs at runtime by string surgery
    #     on world-model names it holds statically: cg_weapons.c strips the
    #     extension of item->world_model[0] and appends "_flash.md3",
    #     "_barrel.md3" or "_hand.md3". The only literal there is the suffix,
    #     so neither static reading can see these; the recipe declares each one
    #     with its constructing code, verified above against the pinned tree.
    for entry in derived_references:
        construction = entry["construction"]
        builder.add(
            entry["reference"],
            entry["kind"],
            f"derived reference ({construction['file']}:{construction['lines'][0]})",
        )

    # 2. The one map.
    map_name = profile["map"]
    builder.add(f"maps/{map_name}.bsp", "bsp", "profile map")
    builder.add(f"maps/{map_name}.aas", "file", "profile map bot navigation")
    builder.add(f"levelshots/{map_name}", "image", "profile map levelshot")

    # 3. The one player presentation, and each bot's.
    for selection in profile["playerModels"]:
        model, _, skin = selection.partition("/")
        skin = skin or "default"
        origin = f"player model {selection}"
        for part in ("lower", "upper", "head"):
            builder.add(f"models/players/{model}/{part}.md3", "model", origin)
            builder.add(f"models/players/{model}/{part}_{skin}.skin", "skin", origin)
        builder.add(f"models/players/{model}/animation.cfg", "file", origin)
        builder.add(f"models/players/{model}/icon_{skin}", "image", origin)
        animation = sources.get(f"models/players/{model}/animation.cfg")
        sex = "male"
        if animation is not None:
            sex = parse_animation_cfg(animation.data.decode("latin-1")).get(
                "sex", "male"
            )
            sex = {"m": "male", "f": "female", "n": "male"}.get(
                sex.lower(), sex.lower()
            )
        for directory in (model, sex):
            for sound in sources.list_directory(f"sound/player/{directory}"):
                builder.add(sound, "file", f"{origin} voice")

    # 4. Bot behaviour data.
    for entry in recipe["botfileRoots"]:
        builder.add(entry, "botfile", "botlib default")
    for bot in profile["bots"]:
        builder.add(f"botfiles/{bot['aifile']}", "botfile", f"bot {bot['name']}")

    # 5. Packaged notices.
    for notice in recipe["notices"]:
        builder.add(notice, "file", "packaged notice")

    report = builder.finish()
    _check_derived_members(recipe["derivedReferences"], report.members)

    members: dict[str, bytes] = {}
    origins: dict[str, tuple[str, str, str]] = {}
    for member in report.members.values():
        members[member.game_path] = member.data
        origins[member.game_path] = (
            member.source_id,
            member.source_path,
            "copied unmodified from the verified upstream archive",
        )

    used_sources = {origin[0] for origin in origins.values()} | {generated["id"]}
    metadata = _generated_metadata(recipe)
    metadata[recipe["noticeFile"]] = _notice_text(
        recipe, provenance_sources(recipe, used_sources)
    )
    for path, text in metadata.items():
        members[path] = text.encode("utf-8")
        origins[path] = (
            generated["id"],
            "content/pack-recipe.json",
            "generated from the committed recipe by scripts/build-content-pack.py",
        )

    _check_generated_metadata(metadata, recipe, members)
    forbidden = iter_forbidden(members)
    if forbidden:
        raise ContentError(f"assembled pack contains forbidden members: {forbidden}")

    provenance = build_provenance(recipe, baseline, members, origins)
    validate_provenance(provenance, baseline)

    pack_path = arguments.output_dir / recipe["packPath"]
    write_pk3(members, pack_path)

    manifest = {
        "$schema": ARTIFACT_SCHEMA,
        "artifacts": [
            {
                "path": recipe["packPath"],
                "sha256": file_sha256(pack_path),
                "size": pack_path.stat().st_size,
            }
        ],
        "baselineIdentity": _canonical_json_identity(baseline),
        "baselineInputIds": ["ioq3"],
        "digestAlgorithm": "sha256",
        "formatVersion": 1,
        "inputs": sorted(
            [
                {
                    "id": "ioq3",
                    "identity": f"git:{baseline['engine']['commit']}",
                    "kind": "git",
                },
                {
                    "id": generated["id"],
                    "identity": recipe_digest,
                    "kind": "archive",
                },
            ]
            + [
                {
                    "id": source["id"],
                    "identity": f"sha256:{source['sha256']}",
                    "kind": "archive",
                }
                for source in recipe["sources"]
                if source["id"] in {origin[0] for origin in origins.values()}
            ],
            key=lambda item: item["id"],
        ),
        "producer": {"commit": arguments.producer_commit, "name": PRODUCER_NAME},
    }
    validate_artifact_manifest(
        manifest, "generated content manifest", baseline=baseline
    )

    report_lines = [
        f"package: {recipe['package']['id']}",
        f"map: {map_name}",
        f"members: {len(members)}",
        f"pack: sha256:{manifest['artifacts'][0]['sha256']} ({manifest['artifacts'][0]['size']} bytes)",
        f"shader files: {len(report.shader_files)}",
        "",
        "accepted unresolved references (recipe-declared, outside this profile):",
    ]
    for reference in report.accepted_unresolved:
        report_lines.append(f"  {reference}")
    report_lines.append("")
    if report.malformed:
        report_lines.append(
            "malformed upstream references (editor artefacts; the renderer "
            "answers them with its default shader):"
        )
        report_lines += [
            f"  {reference!r}  <- {origin}"
            for reference, origin in sorted(report.malformed.items())
        ]
        report_lines.append("")
    if report.stale_acceptances:
        report_lines.append("STALE acceptances that now resolve:")
        report_lines += [f"  {item}" for item in report.stale_acceptances]
    if report.unresolved:
        report_lines.append("UNRESOLVED required references:")
        report_lines += [
            f"  {reference}  <- {origin}"
            for reference, origin in sorted(report.unresolved.items())
        ]
    report_text = "\n".join(report_lines) + "\n"

    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "closure-report.txt").write_text(
        report_text, encoding="utf-8"
    )
    arguments.provenance_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.provenance_output.write_text(_encode(provenance), encoding="utf-8")
    arguments.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.manifest_output.write_text(_encode(manifest), encoding="utf-8")

    print(report_text, end="")
    if not report.ok:
        print("content closure failed", file=sys.stderr)
        return 1
    print(f"content pack {pack_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--provenance-output", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    parser.add_argument("--producer-commit", required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        return build(root, arguments)
    except (ContentError, MetadataError, OSError) as error:
        print(f"content pack failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
