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
import sys
from pathlib import Path
from typing import Any

from content_pack import (
    ClosureBuilder,
    ContentError,
    SourceSet,
    build_provenance,
    file_sha256,
    iter_forbidden,
    load_recipe,
    recipe_sources,
    provenance_sources,
    validate_provenance,
    write_pk3,
)
from game_assets import parse_animation_cfg
from metadata import (
    ARTIFACT_SCHEMA,
    MetadataError,
    _canonical_json_identity,
    _load_json,
    validate_artifact_manifest,
    validate_baseline,
)
from qvm_references import baseq3_references

PRODUCER_NAME = "arena-web scripts/build-content-pack.sh"


def _encode(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


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
        "and recipe at the commit named by the accompanying artifact manifest.",
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

    sources = SourceSet(recipe_sources(recipe), arguments.archive_dir)
    builder = ClosureBuilder(sources, recipe)
    profile = recipe["profile"]

    # 1. Everything the pinned baseq3 QVMs can name directly.
    references = baseq3_references(root / baseline["engine"]["submodulePath"])
    declared_templates = {
        entry["template"]: entry for entry in recipe["templateExpansions"]
    }
    seen_templates: set[str] = set()
    for module in sorted(references):
        module_references = references[module]
        for literal in module_references.literals:
            builder.add_engine_reference(literal, f"{module} literal")
        for template in module_references.templates:
            seen_templates.add(template)
            entry = declared_templates.get(template)
            if entry is None:
                raise ContentError(
                    f"{module}: reference template {template!r} has no recipe expansion; "
                    "the profile must state what it expands to or why it cannot"
                )
            for expansion in entry["expansions"]:
                builder.add_engine_reference(expansion, f"{module} template {template}")
    unknown_templates = sorted(set(declared_templates) - seen_templates)
    if unknown_templates:
        raise ContentError(
            f"recipe declares expansions for templates the QVMs do not use: {unknown_templates}"
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
