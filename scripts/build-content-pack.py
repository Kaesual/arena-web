#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble the audited arena-web content pack and emit its committed identities.

The closure roots are the pinned `baseq3` QVM references plus the maps, player
presentations and bot data the recipe names. Everything else follows from the
content itself. The PK3 goes to a gitignored build directory; the provenance
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
    AssembledArchive,
    ClosureBuilder,
    ContentError,
    ShaderIndex,
    SourceSet,
    _game_path,
    build_provenance,
    check_duplicate_members,
    check_map_pack_template,
    check_shader_resolution,
    file_sha256,
    iter_forbidden,
    load_map_fragments,
    load_recipe,
    map_fragment_input_id,
    map_fragment_path,
    map_pack_path,
    recipe_sources,
    provenance_sources,
    subtract_closure,
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

# The pinned gamecode's header that carries the buffer sizes the generated
# metadata has to fit into.
_GAMECODE_HEADER = "code/game/bg_public.h"

# `G_LoadArenas` lists `scripts/*.arena` into a local `char dirlist[1024]`. The
# bound is a call argument rather than a `#define`, so it is read off the call
# itself; matching the whole call also proves the listing still asks for the
# extension this pack generates.
_ARENA_DIRLIST_RE = re.compile(
    r'trap_FS_GetFileList\(\s*"scripts"\s*,\s*"\.arena"\s*,\s*\w+\s*,\s*(\d+)\s*\)'
)
_GAMECODE_BOTS = "code/game/g_bot.c"


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


# The keys `_generated_metadata` writes into every `scripts/arenas.txt` block,
# in the order it writes them.
ARENA_FIELDS = ("map", "longname", "bots", "fraglimit", "type")

# What may not appear in an arena value. The generated file is read back by
# `parse_key_value_blocks`, which is a Python regex, and at run time by the
# gamecode's `COM_Parse`, which is not: braces would open or close a block for
# one reader and not the other, a quote would end a token, and `//` starts a
# comment the two strip differently. Upstream longnames are ordinary text, so
# this rejects rather than escapes — an arena value that needs any of these is a
# decision, not an encoding problem.
_ARENA_VALUE_FORBIDDEN = ('"', "{", "}", "//", "/*", "\n", "\r", "\t")


def _check_arena_fields(arena: dict[str, Any]) -> None:
    """Require an arena to carry exactly the fields the generator writes."""
    if not isinstance(arena, dict):
        raise ContentError(f"profile arena {arena!r} is not an object")
    missing = [key for key in ARENA_FIELDS if key not in arena]
    if missing:
        raise ContentError(f"profile arena {arena!r} is missing {missing}")
    for key in ARENA_FIELDS:
        value = arena[key]
        if not isinstance(value, str) or not value:
            raise ContentError(
                f"profile arena field {key!r} must be a non-empty string, "
                f"not {value!r}"
            )
        for token in _ARENA_VALUE_FORBIDDEN:
            if token in value:
                raise ContentError(
                    f"profile arena field {key!r} contains {token!r}, which the "
                    "generated scripts/arenas.txt cannot carry unambiguously"
                )


def _check_profile(recipe: dict[str, Any], fragments: dict[str, Any]) -> None:
    """Reject a recipe whose parts disagree before anything is assembled.

    The bot roster and the packaged player models live in the root recipe,
    because they belong to the base archive. Each map's arena definition lives
    in that map's fragment, and `load_map_fragments` has already required the
    two to name the same map.

    An arena's `bots` value is deliberately *not* required to equal the
    profile's roster any more. Requiring it made every map archive depend on the
    base's bot list, so adding a bot would have moved every archive's bytes.
    What it protected is narrow: the game module reads arena data only inside
    `GT_SINGLE_PLAYER` (ioq3 code/game/g_bot.c `G_InitBots`), and this profile
    is `GT_FFA` with bots supplied by `+addbot`. The packaged `q3_ui` *does*
    read `arena.bots` — `ui_startserver.c` and `ui_splevel.c` seed skirmish
    slots from it — but this product launches straight into a game and never
    enters those menus. What survives is the value gate below, which is about
    the generated file staying parseable at all.
    """
    profile = recipe["profile"]
    models = profile["playerModels"]
    if not isinstance(models, list) or not models:
        raise ContentError("profile.playerModels must be a non-empty array")
    if len(set(models)) != len(models):
        raise ContentError(f"profile.playerModels names a model twice: {models}")
    for fragment in fragments.values():
        _check_arena_fields(fragment["arena"])
    packaged_models = set(models)
    for bot in profile["bots"]:
        if bot["model"] not in packaged_models:
            raise ContentError(
                f"bot {bot['name']!r} uses model {bot['model']!r}, which the profile "
                f"does not package: {sorted(packaged_models)}"
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
        if entry.get("expansions") and "kind" not in entry:
            raise ContentError(
                f"recipe template {template!r} expands without declaring which "
                "kind of reference its expansions are"
            )
    _check_map_templates(declared)
    return declared


# The `%s` the gamecode fills with a map name, and the path each one produces.
# WP-A carried these as three flat lists in the recipe that every packaged map
# contributed to, which made "added to one list and forgotten in the other two"
# a class of defect that had to be checked for. Under per-map archives they are
# *derived* from the archive's own map instead, so the class does not exist: a
# map archive expands them for its own map and the base expands them for none.
MAP_TEMPLATES = {
    "maps/%s.bsp": "maps/{map}.bsp",
    "levelshots/%s": "levelshots/{map}",
    "levelshots/%s.tga": "levelshots/{map}.tga",
}


def _check_map_templates(declared: dict[str, dict[str, Any]]) -> None:
    """Require each map template to declare its per-map shape and no list."""
    for template, shape in sorted(MAP_TEMPLATES.items()):
        entry = declared.get(template)
        if entry is None:
            raise ContentError(
                f"the pinned QVMs no longer use the map template {template!r}; "
                "the closure roots and this check disagree about how a map is named"
            )
        if entry.get("expandsPerMap") != shape:
            raise ContentError(
                f"recipe template {template!r} must declare expandsPerMap "
                f"{shape!r}, not {entry.get('expandsPerMap')!r}"
            )
        if "expansions" in entry:
            raise ContentError(
                f"recipe template {template!r} is expanded per map and must not "
                "also carry a whole-set expansions list"
            )
        kind = entry.get("kind")
        if kind not in DERIVED_REFERENCE_KINDS:
            # This value *is* used, at the map closure root; `expandsPerMap` is
            # only checked. A wrong kind for `maps/%s.bsp` would be masked by
            # the explicit bsp root beside it and package the map without ever
            # following its shader lump.
            raise ContentError(
                f"recipe template {template!r} expands per map as kind {kind!r}, "
                f"which is not one of {list(DERIVED_REFERENCE_KINDS)}"
            )
    for template, entry in sorted(declared.items()):
        if template in MAP_TEMPLATES:
            continue
        if "expandsPerMap" in entry:
            raise ContentError(
                f"recipe template {template!r} is not a map template and must not "
                "declare expandsPerMap"
            )
        if "expansions" not in entry:
            raise ContentError(
                f"recipe template {template!r} must declare its expansions"
            )


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


def _engine_constant(engine_root: Path, name: str, source: str) -> int:
    """One `#define <name> <number>` out of the pinned engine tree.

    Read rather than restated, so an engine pin that changes a buffer size
    cannot leave a gate derived from it silently permissive.
    """
    header = engine_root / source
    pattern = re.compile(rf"^#define\s+{name}\s+(\d+)\s*$", re.M)
    match = pattern.search(header.read_text(encoding="latin-1"))
    if match is None:
        raise ContentError(
            f"{header} no longer defines {name}; the gate derived from it cannot "
            "be read out of the pinned engine"
        )
    return int(match.group(1))


def _check_arena_file_listing(engine_root: Path, arena_files: list[str]) -> None:
    """The per-map `.arena` names must fit `G_LoadArenas`' listing buffer.

    `G_LoadArenas` passes a `char dirlist[1024]` to `trap_FS_GetFileList` (ioq3
    code/game/g_bot.c), and `FS_GetFileList` packs `strlen(name) + 1` per entry
    and simply *stops* at `nTotal + nLen + 1 >= bufsize`
    (code/qcommon/files.c). Nothing reports the truncation, so a map set that
    outgrew the buffer would ship with the last arenas silently absent.

    It *joins* WP-A's `MAX_ARENAS_TEXT` gate rather than replacing it — an
    earlier draft of this comment claimed the pack no longer generates
    `scripts/arenas.txt`, which is wrong: the base still generates it, empty of
    arena blocks, and every map archive generates one `.arena` file. Those are
    still read into `char buf[MAX_ARENAS_TEXT]` and dropped whole on overflow,
    so `_check_arena_text_size` keeps that bound; this function adds the one the
    listing imposes, which is the silent failure of the two.
    """
    source = engine_root / _GAMECODE_BOTS
    match = _ARENA_DIRLIST_RE.search(source.read_text(encoding="latin-1"))
    if match is None:
        raise ContentError(
            f"{source} no longer lists scripts/*.arena into a bounded buffer; the "
            "listing gate cannot be derived from the pinned engine"
        )
    limit = int(match.group(1))
    total = sum(len(name.encode("utf-8")) + 1 for name in arena_files)
    if arena_files and total + 1 >= limit:
        raise ContentError(
            f"the {len(arena_files)} packaged .arena file names pack to {total} "
            f"bytes, and G_LoadArenas lists them into {limit} bytes and truncates "
            "the remainder without a word"
        )


def _check_arena_text_size(
    engine_root: Path, metadata: dict[str, str], what: str
) -> None:
    """Every generated arena file must fit the buffer its readers give it.

    `G_LoadArenasFromFile` and both `UI_LoadArenasFromFile` copies read a file
    into a fixed `char buf[MAX_ARENAS_TEXT]` and, when it does not fit, drop the
    *whole* file rather than truncate it (ioq3 code/game/g_bot.c,
    code/q3_ui/ui_gameinfo.c). One red console line, zero parsed arenas, and no
    property of the assembled archive would show it.
    """
    limit = _engine_constant(engine_root, "MAX_ARENAS_TEXT", _GAMECODE_HEADER)
    for path, text in sorted(metadata.items()):
        if not path.endswith((".arena", "arenas.txt")):
            continue
        size = len(text.encode("utf-8"))
        if size >= limit:
            raise ContentError(
                f"{what}: generated {path} is {size} bytes, and every reader "
                f"drops a file of MAX_ARENAS_TEXT ({limit}) bytes or more"
            )


def _check_base_metadata(
    metadata: dict[str, str],
    recipe: dict[str, Any],
    members: dict[str, bytes],
    engine_root: Path,
) -> None:
    """Read the base archive's generated metadata back and check what it names."""
    # The base names no map. That is the byte-stability property of the whole
    # split, so it is a gate rather than a comment: an arena block here would
    # mean the base archive depended on the map set.
    named = [arena["map"] for arena in parse_key_value_blocks(metadata["scripts/arenas.txt"])]
    if named:
        raise ContentError(
            f"the base archive's scripts/arenas.txt names {named}; the base must "
            "not depend on which maps are in the build"
        )
    # `G_LoadBotsFromFile` loads scripts/bots.txt into a fixed
    # `char buf[MAX_BOTS_TEXT]` and *drops the whole file* when it does not fit
    # (ioq3 code/game/g_bot.c). Unlike the arena data this one is load-bearing:
    # without it no bot connects at all, which §4.5 measured. The file grows
    # with the bot roster, so the size is a build gate.
    _check_arena_text_size(engine_root, metadata, "the base archive")
    limit = _engine_constant(engine_root, "MAX_BOTS_TEXT", _GAMECODE_HEADER)
    bots_bytes = len(metadata["scripts/bots.txt"].encode("utf-8"))
    if bots_bytes >= limit:
        raise ContentError(
            f"generated scripts/bots.txt is {bots_bytes} bytes, and every reader "
            f"drops a file of MAX_BOTS_TEXT ({limit}) bytes or more; the pack "
            "would ship a bot list the engine ignores, and then no bot connects"
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


def _check_map_metadata(
    map_name: str,
    metadata: dict[str, str],
    members: dict[str, bytes],
    engine_root: Path,
) -> None:
    """Read one map archive's generated `.arena` file back."""
    _check_arena_text_size(engine_root, metadata, f"the {map_name} archive")
    path = arena_file_path(map_name)
    arenas = parse_key_value_blocks(metadata[path])
    named = [arena["map"] for arena in arenas]
    if named != [map_name]:
        raise ContentError(
            f"generated {path} names {named}, not exactly its own map {map_name!r}"
        )
    if f"maps/{map_name}.bsp" not in members:
        raise ContentError(
            f"generated {path} names map {map_name!r}, which this archive does "
            "not carry"
        )


def arena_file_path(map_name: str) -> str:
    return f"scripts/{map_name}.arena"


def _arena_block(arena: dict[str, Any]) -> str:
    lines = ["{"]
    for key in ARENA_FIELDS:
        lines.append(f'{key}\t\t"{arena[key]}"')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _base_metadata(recipe: dict[str, Any]) -> dict[str, str]:
    """The product-owned bot definitions the base archive carries.

    The upstream `scripts/bots.txt` describes the whole OpenArena release; this
    archive ships the bots the profile names, so it carries its own file rather
    than a list whose entries it cannot honour.

    `scripts/arenas.txt` is generated too, and deliberately carries **no arena
    block at all**. Three things have to be true at once: the QVMs open that
    name (`G_LoadArenas`, and the two `UI_LoadArenasFromFile` copies), so it
    cannot simply be absent without a red console line on every start; the
    upstream file describes 58 arenas this pack does not carry, so it cannot be
    packaged; and a *generated* whole-set file would name every packaged map,
    which would move the base's bytes whenever a map was added. A file that
    names nothing satisfies all three, and each map archive carries its own
    `scripts/<map>.arena`, which `G_LoadArenas` reads straight after this one.
    """
    arenas = (
        "// arena-web: this pack's arena definitions are per map.\n"
        "// Each map archive carries its own scripts/<map>.arena, which\n"
        "// G_LoadArenas reads after this file (ioq3 code/game/g_bot.c).\n"
        "// A whole-set list here would name every packaged map, and the base\n"
        "// archive must not depend on which maps are in the build.\n"
    )
    blocks = []
    for bot in recipe["profile"]["bots"]:
        blocks.append(
            "{\n"
            f'name\t\t"{bot["name"]}"\n'
            f'model\t\t"{bot["model"]}"\n'
            f'aifile\t\t"{bot["aifile"]}"\n'
            "}"
        )
    return {
        "scripts/arenas.txt": arenas,
        "scripts/bots.txt": "\n\n".join(blocks) + "\n",
    }


def _map_metadata(fragment: dict[str, Any]) -> dict[str, str]:
    """The product-owned arena definition one map archive carries."""
    return {arena_file_path(fragment["map"]): _arena_block(fragment["arena"])}


def _notice_text(
    recipe: dict[str, Any],
    provenance_sources: list[dict[str, Any]],
    recipe_input: str,
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
        "and the recipe they read, which are the arena-web repository at the",
        f"commit whose {recipe_input} has the SHA-256 shown for 'arena-web'",
        "above. That file is this archive's own selection input; the repository",
        "at that commit carries the rest of the assembly, and the archives are",
        "published together.",
        "",
        "Attribution",
        "-----------",
        "Author credits for the OpenArena material are packaged as CREDITS,",
        "CREDITS-0.8.5 and CREDITS-0.8.8, and are reproduced unmodified.",
        "",
    ]
    return "\n".join(lines)


def _add_base_roots(
    builder: ClosureBuilder,
    recipe: dict[str, Any],
    references: dict[str, Any],
    declared_templates: dict[str, dict[str, Any]],
    derived_references: list[dict[str, Any]],
    sources: SourceSet,
) -> None:
    """Everything the base archive carries: the closure with no map in it.

    Roots 1, 1b, 3, 4 and 5. Root 2 — the maps — belongs to the per-map
    archives, which is what makes the base independent of the map set. A base
    built from a recipe with no fragment at all is a legal product, not an
    error; it is simply the closure of the gamecode, the player presentations
    and the bots.
    """
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
            if template in MAP_TEMPLATES:
                # Expanded per map, in the archive that carries that map.
                continue
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

    # 3. Every offered player presentation, and each bot's. These cannot be
    #    loaded on demand: another player's model arrives at run time and a
    #    failed registration falls back to DEFAULT_MODEL, which is not
    #    packaged and is fatal (ioq3 code/cgame/cg_players.c).
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


def _add_map_roots(
    builder: ClosureBuilder,
    recipe: dict[str, Any],
    declared_templates: dict[str, dict[str, Any]],
    map_name: str,
) -> None:
    """Root 2 for one map, plus the notice set every archive carries.

    The BSP pulls its own shaders, entity models and entity sounds; the AAS is
    the bot navigation botlib loads whenever bot_enable is set, and no packaged
    member references it, so it is named here directly. The three `%s` map
    templates the QVMs use are expanded for this map alone.
    """
    origin = f"profile map {map_name}"
    builder.add(f"maps/{map_name}.bsp", "bsp", origin)
    builder.add(f"maps/{map_name}.aas", "file", f"{origin} bot navigation")
    for template, shape in sorted(MAP_TEMPLATES.items()):
        entry = declared_templates[template]
        builder.add(
            shape.format(map=map_name), entry["kind"], f"map template {template}"
        )
    for notice in recipe["notices"]:
        builder.add(notice, "file", "packaged notice")


def _closure_report_lines(name: str, report: Any, members: dict[str, bytes]) -> list[str]:
    lines = [
        f"archive: {name}",
        f"  members: {len(members)}",
        f"  shader files: {len(report.shader_files)}",
        "  accepted unresolved references:",
    ]
    lines += [f"    {reference}" for reference in report.accepted_unresolved]
    if report.malformed:
        lines.append(
            "  malformed upstream references (editor artefacts; the renderer "
            "answers them with its default shader):"
        )
        lines += [
            f"    {reference!r}  <- {origin}"
            for reference, origin in sorted(report.malformed.items())
        ]
    if report.stale_acceptances:
        lines.append("  STALE acceptances that now resolve:")
        lines += [f"    {item}" for item in report.stale_acceptances]
    if report.unresolved:
        lines.append("  UNRESOLVED required references:")
        lines += [
            f"    {reference}  <- {origin}"
            for reference, origin in sorted(report.unresolved.items())
        ]
    return lines


def _check_archive_set(
    recipe: dict[str, Any],
    archives: list[AssembledArchive],
    reports: dict[str, Any],
    fragments: dict[str, Any],
    engine_root: Path,
) -> None:
    """Everything that is only decidable once the whole archive set exists.

    These four are deliberately one function rather than four calls in `build`:
    each is about the *set*, none of them can be exercised by looking at one
    archive, and keeping them together gives them a single place a test can
    reach without assembling gigabytes of upstream content.
    """
    # The keys are the names the engine sees, not the manifest paths, because
    # PK3 load order is by that name. `arena_runtime` binds the two together.
    by_engine_name = {
        Path(archive.path).name: archive.members for archive in archives
    }
    # §6.1 invariant 3: a member two archives share must be byte-identical,
    # exempting the notice each archive generates for itself.
    check_duplicate_members(by_engine_name, exempt=(recipe["noticeFile"],))
    # §6.1 invariant 2, in its honest form: the run-time winner of every shader
    # name the closure resolved must be the file the closure resolved it to.
    resolved: dict[str, str] = {}
    for report, _members in reports.values():
        resolved.update(report.shader_names)
    check_shader_resolution(by_engine_name, resolved)
    # The `.arena` names must all fit the listing buffer G_LoadArenas gives them.
    _check_arena_file_listing(
        engine_root,
        [arena_file_path(name).rpartition("/")[2] for name in fragments],
    )
    # An excluded derived reference must be absent from *every* archive, not
    # only from the base whose closure declared it.
    for archive in archives:
        for entry in recipe["derivedReferences"]:
            if (
                "excludedReason" in entry
                and _game_path(entry["reference"]) in archive.members
            ):
                raise ContentError(
                    f"derived reference {entry['reference']!r} is excluded but "
                    f"{archive.path} packages it anyway"
                )


def build(root: Path, arguments: argparse.Namespace) -> int:
    baseline = validate_baseline(
        _load_json(root / "locks" / "baseline.json"), "baseline"
    )
    recipe_path = root / "content" / "pack-recipe.json"
    recipe = load_recipe(recipe_path)
    check_map_pack_template(recipe)
    # The root recipe is the base archive's own selection input, so its digest
    # is what the base's notice carries. Each map fragment is its map archive's,
    # so a map's notice carries only its own. Neither reaches the other's bytes.
    recipe_digest = f"sha256:{file_sha256(recipe_path)}"
    fragments = load_map_fragments(root, recipe)
    fragment_digests = {
        name: f"sha256:{file_sha256(root / map_fragment_path(name))}"
        for name in fragments
    }
    generated = recipe["generatedSource"]
    if (
        generated["sourceIdentity"] != "recipe"
        or generated["preferredSourceRevision"] != "recipe"
    ):
        raise ContentError(
            "generatedSource must bind its identity to the recipe with the literal 'recipe'"
        )

    _check_profile(recipe, fragments)
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
    # One source set and one shader index for every archive. That is what makes
    # a game path resolve to the same bytes wherever it lands, so the archives
    # can be closed independently without diverging.
    shaders = ShaderIndex(sources)
    notice_paths = tuple(sorted(recipe["notices"]))

    base_builder = ClosureBuilder(
        sources,
        recipe,
        shaders=shaders,
        accepted_unresolved=recipe["acceptedUnresolved"],
        generated_members=recipe["generatedMembers"],
    )
    _add_base_roots(
        base_builder, recipe, references, declared_templates, derived_references, sources
    )
    base_report = base_builder.finish()
    _check_derived_members(recipe["derivedReferences"], base_report.members)

    plans: list[tuple[str, str, Any, dict[str, Any], str, str]] = [
        (
            "base",
            recipe["basePackPath"],
            base_report,
            _base_metadata(recipe),
            "content/pack-recipe.json",
            recipe_digest,
        )
    ]
    map_members: dict[str, dict[str, Any]] = {}
    for map_name in sorted(fragments):
        fragment = fragments[map_name]
        builder = ClosureBuilder(
            sources,
            recipe,
            shaders=shaders,
            accepted_unresolved=fragment["acceptedUnresolved"],
            generated_members=fragment["generatedMembers"],
        )
        _add_map_roots(builder, recipe, declared_templates, map_name)
        report = builder.finish()
        map_members[map_name] = subtract_closure(
            report, base_report, keep=notice_paths
        )
        plans.append(
            (
                map_name,
                map_pack_path(recipe, map_name),
                report,
                _map_metadata(fragment),
                map_fragment_path(map_name),
                fragment_digests[map_name],
            )
        )

    archives: list[AssembledArchive] = []
    reports: dict[str, Any] = {}
    used_source_ids: set[str] = set()
    for name, pack_path, report, metadata, recipe_input, recipe_identity in plans:
        selected = (
            base_report.members if name == "base" else map_members[name]
        )
        members: dict[str, bytes] = {}
        origins: dict[str, tuple[str, str, str]] = {}
        for member in selected.values():
            members[member.game_path] = member.data
            origins[member.game_path] = (
                member.source_id,
                member.source_path,
                "copied unmodified from the verified upstream archive",
            )
        archive_sources = {origin[0] for origin in origins.values()} | {generated["id"]}
        used_source_ids |= archive_sources
        # The notice's upstream-source list is this archive's own, not the
        # build's: a new map that introduced a new source would otherwise
        # rewrite the notice, and therefore the bytes, of every archive.
        metadata = dict(metadata)
        metadata[recipe["noticeFile"]] = _notice_text(
            recipe,
            provenance_sources(
                recipe, archive_sources, generated_identity=recipe_identity
            ),
            recipe_input,
        )
        for path, text in metadata.items():
            members[path] = text.encode("utf-8")
            origins[path] = (
                generated["id"],
                recipe_input,
                "generated from the committed recipe by scripts/build-content-pack.py",
            )
        if name == "base":
            _check_base_metadata(metadata, recipe, members, engine_root)
        else:
            _check_map_metadata(name, metadata, members, engine_root)
        forbidden = iter_forbidden(members)
        if forbidden:
            raise ContentError(
                f"{pack_path} contains forbidden members: {forbidden}"
            )
        archives.append(
            AssembledArchive(
                path=pack_path,
                members=members,
                origins=origins,
                recipe_input=recipe_input,
                recipe_identity=recipe_identity,
            )
        )
        reports[name] = (report, members)

    _check_archive_set(recipe, archives, reports, fragments, engine_root)

    provenance = build_provenance(recipe, baseline, archives)
    validate_provenance(provenance, baseline)

    artifacts = []
    for archive in archives:
        pack_path = arguments.output_dir / archive.path
        write_pk3(archive.members, pack_path)
        artifacts.append(
            {
                "path": archive.path,
                "sha256": file_sha256(pack_path),
                "size": pack_path.stat().st_size,
            }
        )

    manifest = {
        "$schema": ARTIFACT_SCHEMA,
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
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
                # One input per map fragment. The fragments decide what a map
                # archive holds, and the root recipe deliberately does not list
                # them, so this is where they enter the release identity.
                {
                    "id": map_fragment_input_id(name),
                    "identity": fragment_digests[name],
                    "kind": "archive",
                }
                for name in fragments
            ]
            + [
                {
                    "id": source["id"],
                    "identity": f"sha256:{source['sha256']}",
                    "kind": "archive",
                }
                for source in recipe["sources"]
                if source["id"] in used_source_ids
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
        f"archives: {len(archives)}",
        f"maps: {' '.join(sorted(fragments)) or '(none)'}",
        "",
    ]
    for artifact in manifest["artifacts"]:
        report_lines.append(
            f"pack {artifact['path']}: sha256:{artifact['sha256']} "
            f"({artifact['size']} bytes)"
        )
    report_lines.append("")
    ok = True
    for name, pack_path, *_ in plans:
        report, members = reports[name]
        report_lines += _closure_report_lines(pack_path, report, members)
        report_lines.append("")
        ok = ok and report.ok
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
    if not ok:
        print("content closure failed", file=sys.stderr)
        return 1
    print(f"content archives in {arguments.output_dir}")
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
