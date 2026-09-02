# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import random
import sys
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_pack import (  # noqa: E402
    ZIP_COMPRESS_LEVEL,
    ZIP_TIMESTAMP,
    ClosureBuilder,
    ContentError,
    SourceSet,
    build_provenance,
    file_sha256,
    iter_forbidden,
    load_recipe,
    AssembledArchive,
    ClosureReport,
    ShaderIndex,
    check_duplicate_members,
    check_shader_authority,
    check_shader_resolution,
    load_map_fragments,
    map_pack_path,
    subtract_closure,
    provenance_sources,
    recipe_sources,
    validate_provenance,
    write_pk3,
)
from metadata import (  # noqa: E402
    MetadataError,
    _canonical_json_identity,
    _load_json,
    validate_baseline,
)
from game_assets import parse_key_value_blocks  # noqa: E402
from qvm_references import (  # noqa: E402
    ALWAYS_UNDEFINED,
    ReferenceError,
    baseq3_references,
    baseq3_translation_units,
    registration_references,
    select_compiled_lines,
)
from test_game_assets import build_bsp, build_md3  # noqa: E402

BASELINE = validate_baseline(_load_json(ROOT / "locks" / "baseline.json"), "baseline")
RECIPE_PATH = ROOT / "content" / "pack-recipe.json"


def write_archive(
    directory: Path, name: str, root: str, files: dict[str, bytes]
) -> Path:
    """Write a tar.bz2 shaped like a Debian orig tarball."""
    path = directory / name
    with tarfile.open(path, "w:bz2") as handle:
        for member_name, payload in sorted(files.items()):
            info = tarfile.TarInfo(f"{root}/{member_name}")
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
    return path


def source_record(path: Path, **overrides) -> dict:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    record = {
        "archiveRoot": "pack.orig",
        "documents": [],
        "fileName": path.name,
        "id": "test-source",
        "licenseEvidenceUrl": "https://example.invalid/copyright",
        "licenseExpression": "GPL-2.0-or-later",
        "nonDefaultLicensePaths": [],
        "precedence": 10,
        "preferredSourceRevision": f"sha256:{digest}",
        "preferredSourceUrl": "https://example.invalid/source",
        "sha256": digest,
        "size": len(payload),
        "sourceIdentity": f"sha256:{digest}",
        "sourceUrl": "https://example.invalid/source",
        "trees": ["pak0"],
        "url": "https://example.invalid/source",
    }
    record.update(overrides)
    return record


class SourceSetTests(unittest.TestCase):
    def test_rejects_a_digest_that_does_not_match_the_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            archive = write_archive(
                directory, "pack.tar.bz2", "pack.orig", {"pak0/a.txt": b"a"}
            )
            record = source_record(archive, sha256="0" * 64)
            with self.assertRaisesRegex(ContentError, "recipe pins sha256"):
                SourceSet(recipe_sources({"sources": [record]}), directory)

    def test_rejects_a_size_that_does_not_match_the_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            archive = write_archive(
                directory, "pack.tar.bz2", "pack.orig", {"pak0/a.txt": b"a"}
            )
            record = source_record(archive, size=1)
            with self.assertRaisesRegex(ContentError, "recipe pins 1"):
                SourceSet(recipe_sources({"sources": [record]}), directory)

    def test_rejects_a_missing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            archive = write_archive(
                directory, "pack.tar.bz2", "pack.orig", {"pak0/a.txt": b"a"}
            )
            record = source_record(archive)
            archive.unlink()
            with self.assertRaisesRegex(ContentError, "is missing"):
                SourceSet(recipe_sources({"sources": [record]}), directory)

    def test_higher_precedence_source_wins_and_keeps_member_case(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            low = write_archive(
                directory, "low.tar.bz2", "pack.orig", {"pak0/Gfx/A.tga": b"low"}
            )
            high = write_archive(
                directory, "high.tar.bz2", "pack.orig", {"pak0/Gfx/A.tga": b"high"}
            )
            sources = recipe_sources(
                {
                    "sources": [
                        source_record(low, id="low", precedence=10),
                        source_record(high, id="high", precedence=20),
                    ]
                }
            )
            found = SourceSet(sources, directory).get("gfx/a.tga")
            self.assertIsNotNone(found)
            self.assertEqual(found.data, b"high")
            self.assertEqual(found.source_id, "high")
            self.assertEqual(found.game_path, "Gfx/A.tga")

    def test_rejects_duplicate_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            archive = write_archive(
                directory, "p.tar.bz2", "pack.orig", {"pak0/a.txt": b"a"}
            )
            with self.assertRaisesRegex(ContentError, "distinct precedence"):
                recipe_sources(
                    {
                        "sources": [
                            source_record(archive, id="one"),
                            source_record(archive, id="two"),
                        ]
                    }
                )


class ClosureTests(unittest.TestCase):
    def _sources(
        self, directory: Path, files: dict[str, bytes], **overrides
    ) -> SourceSet:
        archive = write_archive(directory, "pack.tar.bz2", "pack.orig", files)
        record = source_record(archive, **overrides)
        return SourceSet(recipe_sources({"sources": [record]}), directory)

    def _builder(self, sources: SourceSet, **recipe) -> ClosureBuilder:
        base = {"acceptedUnresolved": [], "generatedMembers": [], "sources": []}
        base.update(recipe)
        return ClosureBuilder(sources, base)

    def test_image_reference_falls_back_to_another_extension(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(directory, {"pak0/gfx/2d/logo.jpg": b"jpeg"})
            builder = self._builder(sources)
            builder.add("gfx/2d/logo.tga", "image", "test")
            report = builder.finish()
            self.assertIn("gfx/2d/logo.jpg", report.members)
            self.assertFalse(report.unresolved)

    def test_shader_lookup_strips_the_extension_like_the_engine(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(
                directory,
                {
                    "pak0/scripts/x.shader": b"models/thing/skin\n{\n{\nmap textures/real.tga\n}\n}\n",
                    "pak0/textures/real.tga": b"tga",
                },
            )
            builder = self._builder(sources)
            builder.add("models/thing/skin.png", "shader", "test")
            report = builder.finish()
            self.assertIn("scripts/x.shader", report.members)
            self.assertIn("textures/real.tga", report.members)
            self.assertFalse(report.unresolved)

    def test_model_and_skin_pull_their_shaders(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(
                directory,
                {
                    "pak0/models/a.md3": build_md3([["textures/from_model"]]),
                    "pak0/models/a.skin": b"u_torso,textures/from_skin\ntag_x,\n",
                    "pak0/textures/from_model.tga": b"1",
                    "pak0/textures/from_skin.tga": b"2",
                },
            )
            builder = self._builder(sources)
            builder.add("models/a.md3", "model", "test")
            builder.add("models/a.skin", "skin", "test")
            report = builder.finish()
            self.assertIn("textures/from_model.tga", report.members)
            self.assertIn("textures/from_skin.tga", report.members)

    def test_map_pulls_shaders_entity_models_and_sounds(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            entities = (
                '{\n"classname" "worldspawn"\n"music" "music/track.wav"\n}\n'
                '{\n"model" "models/prop.md3"\n"noise" "sound/world/hum.wav"\n}\n'
                '{\n"model" "*3"\n}\n'
            )
            sources = self._sources(
                directory,
                {
                    "pak0/maps/m.bsp": build_bsp(
                        ["textures/wall", "noshader"], entities
                    ),
                    "pak0/textures/wall.tga": b"1",
                    "pak0/models/prop.md3": build_md3([[]]),
                    "pak0/sound/world/hum.wav": b"2",
                    "pak0/music/track.wav": b"3",
                },
            )
            builder = self._builder(sources)
            builder.add("maps/m.bsp", "bsp", "test")
            report = builder.finish()
            for expected in (
                "maps/m.bsp",
                "textures/wall.tga",
                "models/prop.md3",
                "sound/world/hum.wav",
                "music/track.wav",
            ):
                self.assertIn(expected, report.members)
            self.assertFalse(report.unresolved)

    def test_botfile_includes_resolve_from_the_botfiles_base_folder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(
                directory,
                {
                    "pak0/botfiles/bots/x_c.c": (
                        b'#include "chars.h"\nCHARACTERISTIC_WEAPONWEIGHTS "bots/x_w.c"\n'
                    ),
                    "pak0/botfiles/chars.h": b"h",
                    "pak0/botfiles/bots/x_w.c": b'#include "inv.h"\n',
                    "pak0/botfiles/inv.h": b"h",
                },
            )
            builder = self._builder(sources)
            builder.add("botfiles/bots/x_c.c", "botfile", "test")
            report = builder.finish()
            self.assertIn("botfiles/chars.h", report.members)
            self.assertIn("botfiles/bots/x_w.c", report.members)
            self.assertIn("botfiles/inv.h", report.members)
            self.assertFalse(report.unresolved)

    def test_gamecode_is_never_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(directory, {"pak0/vm/cgame.qvm": b"qvm"})
            builder = self._builder(sources)
            with self.assertRaisesRegex(ContentError, "refuses to package"):
                builder.add("vm/cgame.qvm", "file", "test")

    def test_a_differently_licensed_path_stops_the_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(
                directory,
                {"pak0/models/players/merman/lower.md3": build_md3([[]])},
                nonDefaultLicensePaths=["models/players/merman/*"],
            )
            builder = ClosureBuilder(
                sources,
                {
                    "acceptedUnresolved": [],
                    "generatedMembers": [],
                    "sources": [
                        {
                            "id": "test-source",
                            "nonDefaultLicensePaths": ["models/players/merman/*"],
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(ContentError, "differently licensed"):
                builder.add("models/players/merman/lower.md3", "model", "test")

    def test_the_licence_exclusion_holds_across_sources(self) -> None:
        # The declaring source loses the path to a higher-precedence one. The
        # exclusion must still fire, or a differently licensed file would enter
        # the pack through whichever source happened to win.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            declaring = write_archive(
                directory,
                "low.tar.bz2",
                "pack.orig",
                {"pak0/models/players/merman/lower.md3": build_md3([[]])},
            )
            winning = write_archive(
                directory,
                "high.tar.bz2",
                "pack.orig",
                {"pak0/models/players/merman/lower.md3": build_md3([[]])},
            )
            records = [
                source_record(
                    declaring,
                    id="declaring",
                    precedence=10,
                    nonDefaultLicensePaths=["models/players/merman/*"],
                ),
                source_record(winning, id="winning", precedence=20),
            ]
            sources = SourceSet(recipe_sources({"sources": records}), directory)
            builder = ClosureBuilder(
                sources,
                {
                    "acceptedUnresolved": [],
                    "generatedMembers": [],
                    "sources": records,
                },
            )
            with self.assertRaisesRegex(ContentError, "differently licensed"):
                builder.add("models/players/merman/lower.md3", "model", "test")

    def test_an_archive_member_may_not_escape_its_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            archive = write_archive(
                directory, "p.tar.bz2", "pack.orig", {"pak0/../../escape.txt": b"x"}
            )
            with self.assertRaisesRegex(ContentError, "escapes the archive root"):
                SourceSet(
                    recipe_sources({"sources": [source_record(archive)]}), directory
                )

    def test_a_generated_member_satisfies_the_reference_without_upstream_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(directory, {"pak0/scripts/bots.txt": b"upstream"})
            builder = self._builder(sources, generatedMembers=["scripts/bots.txt"])
            builder.add("scripts/bots.txt", "file", "test")
            report = builder.finish()
            self.assertNotIn("scripts/bots.txt", report.members)
            self.assertFalse(report.unresolved)

    def test_accepted_and_stale_unresolved_references(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(directory, {"pak0/gfx/present.tga": b"1"})
            builder = self._builder(
                sources,
                acceptedUnresolved=[
                    {"reference": "gfx/absent", "reason": "outside the profile"},
                    {"reference": "gfx/present", "reason": "no longer true"},
                ],
            )
            builder.add("gfx/absent", "image", "test")
            builder.add("gfx/present", "image", "test")
            report = builder.finish()
            self.assertEqual(report.accepted_unresolved, ["gfx/absent"])
            self.assertEqual(report.stale_acceptances, ["gfx/present"])
            self.assertFalse(report.unresolved)
            self.assertFalse(report.ok)

    def test_editor_artefacts_are_reported_apart_from_missing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            sources = self._sources(directory, {"pak0/a.txt": b"1"})
            builder = self._builder(sources)
            builder.add("E:\\projects\\oa\\Sphere", "shader", "test")
            builder.add("textures/really_missing", "shader", "test")
            report = builder.finish()
            self.assertEqual(list(report.malformed), ["E:\\projects\\oa\\Sphere"])
            self.assertEqual(list(report.unresolved), ["textures/really_missing"])


class PackWriterTests(unittest.TestCase):
    def test_pack_is_sorted_and_carries_no_ambient_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "out" / "pack.pk3"
            write_pk3({"b.txt": b"second", "a.txt": b"first"}, output)
            with zipfile.ZipFile(output) as archive:
                infos = archive.infolist()
            self.assertEqual([info.filename for info in infos], ["a.txt", "b.txt"])
            for info in infos:
                self.assertEqual(info.date_time, ZIP_TIMESTAMP)
                self.assertEqual(info.create_system, 3)
                self.assertEqual(info.external_attr >> 16, 0o100644)
                self.assertEqual(info.extra, b"")
                self.assertEqual(info.comment, b"")

    def test_the_declared_compression_level_is_actually_in_force(self) -> None:
        # A level handed to the ZipFile constructor is ignored for a
        # caller-supplied ZipInfo, so without passing it to writestr the pack is
        # silently written at zlib's default. The payload is built from a
        # seeded generator, so it is identical on every interpreter, and it is
        # shaped so that the default and declared levels compress it to
        # different sizes — verified as a precondition below on both zlib and
        # zlib-ng, so the test cannot silently turn vacuous.
        rng = random.Random(20260829)
        words = [bytes([65 + i % 26]) * (3 + i % 17) for i in range(200)]
        chunks = []
        for index in range(3000):
            chunks.append(words[rng.randrange(200)])
            if index % 7 == 0:
                chunks.append(bytes(rng.randrange(256) for _ in range(11)))
        payload = b"".join(chunks)

        def deflate(level: int) -> bytes:
            stream = zlib.compressobj(level, zlib.DEFLATED, -15)
            return stream.compress(payload) + stream.flush()

        declared = deflate(ZIP_COMPRESS_LEVEL)
        default = deflate(-1)
        self.assertNotEqual(
            len(default),
            len(declared),
            "payload no longer discriminates the compression levels; "
            "the test below would be vacuous",
        )
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "pack.pk3"
            write_pk3({"a.bin": payload}, output)
            with zipfile.ZipFile(output) as archive:
                info = archive.infolist()[0]
                with archive.open(info) as member:
                    self.assertEqual(member.read(), payload)
                stored_size = info.compress_size
        self.assertEqual(stored_size, len(declared))
        self.assertNotEqual(stored_size, len(default))

    def test_two_writes_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            first = Path(raw) / "a.pk3"
            second = Path(raw) / "b.pk3"
            members = {"x/y.txt": b"payload" * 100, "a.txt": b"a"}
            write_pk3(members, first)
            write_pk3(members, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_forbidden_members_are_listed(self) -> None:
        self.assertEqual(
            iter_forbidden(["vm/cgame.qvm", "gfx/a.tga", "glsl/x.glsl", "lib/x.so"]),
            [
                "glsl/x.glsl (OpenArena engine shader programs)",
                "lib/x.so (native code)",
                "vm/cgame.qvm (compiled gamecode)",
                "vm/cgame.qvm (gamecode directory)",
            ],
        )


class ProvenanceTests(unittest.TestCase):
    RECIPE = {
        "generatedSource": {
            "id": "arena-web",
            "licenseEvidenceUrl": "https://example.invalid/LICENSE",
            "licenseExpression": "GPL-2.0-or-later",
            "preferredSourceRevision": "sha256:" + "1" * 64,
            "preferredSourceUrl": "https://example.invalid/repo",
            "sourceIdentity": "sha256:" + "1" * 64,
            "sourceUrl": "https://example.invalid/repo",
        },
        "notices": ["COPYING"],
        "package": {"id": "test-pack", "name": "Test pack"},
        "sources": [
            {
                "id": "upstream",
                "licenseEvidenceUrl": "https://example.invalid/copyright",
                "licenseExpression": "GPL-2.0-or-later",
                "preferredSourceRevision": "sha256:" + "2" * 64,
                "preferredSourceUrl": "https://example.invalid/orig",
                "sourceIdentity": "sha256:" + "2" * 64,
                "sourceUrl": "https://example.invalid/orig",
            }
        ],
    }
    MEMBERS = {"COPYING": b"gpl", "gfx/a.tga": b"art", "scripts/bots.txt": b"bots"}
    ORIGINS = {
        "COPYING": ("upstream", "pack.orig/COPYING", "copied unmodified"),
        "gfx/a.tga": ("upstream", "pack.orig/pak0/gfx/a.tga", "copied unmodified"),
        "scripts/bots.txt": ("arena-web", "content/pack-recipe.json", "generated"),
    }

    def archive(self, members=None, origins=None, path="baseq3/base.pk3"):
        return AssembledArchive(
            path=path,
            members=self.MEMBERS if members is None else members,
            origins=self.ORIGINS if origins is None else origins,
            recipe_input="content/pack-recipe.json",
            recipe_identity="sha256:" + "1" * 64,
        )

    def test_roles_obligations_and_notice_binding(self) -> None:
        provenance = build_provenance(self.RECIPE, BASELINE, [self.archive()])
        self.assertEqual(len(provenance["archives"]), 1)
        by_path = {
            member["path"]: member for member in provenance["archives"][0]["members"]
        }
        self.assertEqual(by_path["COPYING"]["role"], "notice")
        self.assertEqual(by_path["COPYING"]["noticePaths"], [])
        self.assertEqual(by_path["gfx/a.tga"]["role"], "asset")
        self.assertEqual(by_path["gfx/a.tga"]["noticePaths"], ["COPYING"])
        self.assertEqual(by_path["scripts/bots.txt"]["role"], "metadata")
        self.assertEqual(
            by_path["gfx/a.tga"]["obligations"], ["copyleft-source", "license-notice"]
        )
        self.assertEqual(
            provenance["baselineIdentity"], _canonical_json_identity(BASELINE)
        )
        validate_provenance(provenance, BASELINE)

    def test_a_missing_notice_member_fails_closed(self) -> None:
        members = {
            path: data for path, data in self.MEMBERS.items() if path != "COPYING"
        }
        origins = {path: self.ORIGINS[path] for path in members}
        with self.assertRaisesRegex(ContentError, "notice members are not packaged"):
            build_provenance(self.RECIPE, BASELINE, [self.archive(members, origins)])

    def test_only_used_sources_are_declared(self) -> None:
        records = provenance_sources(self.RECIPE, {"upstream"})
        self.assertEqual([record["id"] for record in records], ["upstream"])

    def test_a_disallowed_licence_is_rejected(self) -> None:
        recipe = json.loads(json.dumps(self.RECIPE))
        recipe["sources"][0]["licenseExpression"] = "CC-BY-NC-4.0"
        provenance = build_provenance(recipe, BASELINE, [self.archive()])
        with self.assertRaises(ContentError):
            validate_provenance(provenance, BASELINE)


class QvmReferenceTests(unittest.TestCase):
    def test_missionpack_branches_are_dropped(self) -> None:
        text = "\n".join(
            [
                'keep "a"',
                "#ifdef MISSIONPACK",
                'drop "b"',
                "#else",
                'keep "c"',
                "#endif",
                "#ifndef MISSIONPACK",
                'keep "d"',
                "#endif",
                "#if defined(MISSIONPACK)",
                'drop "e"',
                "#endif",
            ]
        )
        kept = " ".join(select_compiled_lines(text, frozenset({"CGAME"})))
        self.assertIn('"a"', kept)
        self.assertIn('"c"', kept)
        self.assertIn('"d"', kept)
        self.assertNotIn('"b"', kept)
        self.assertNotIn('"e"', kept)

    def test_an_undecidable_condition_keeps_both_branches(self) -> None:
        text = "#if SOMETHING > 3\nkeep 1\n#else\nkeep 2\n#endif\n"
        kept = " ".join(select_compiled_lines(text, frozenset()))
        self.assertIn("keep 1", kept)
        self.assertIn("keep 2", kept)

    def test_module_macro_selects_its_own_branch(self) -> None:
        text = "#ifdef CGAME\ncgame\n#endif\n#ifdef UI\nui\n#endif\n"
        kept = " ".join(select_compiled_lines(text, frozenset({"CGAME"})))
        self.assertIn("cgame", kept)
        # UI is not in the defined set but is not known-undefined either, so the
        # conservative reader keeps it.
        self.assertIn("ui", kept)
        self.assertIn("MISSIONPACK", ALWAYS_UNDEFINED)

    def test_translation_units_are_the_pinned_baseq3_lists(self) -> None:
        units = baseq3_translation_units(ROOT / "ioq3")
        self.assertEqual(sorted(units), ["cgame", "qagame", "ui"])
        ui_names = {path.parent.name for path in units["ui"]}
        self.assertIn("q3_ui", ui_names)
        self.assertNotIn("ui", ui_names)
        for module in units.values():
            self.assertIn("q_shared.c", {path.name for path in module})

    def test_a_tree_without_the_cmake_lists_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ReferenceError):
                baseq3_translation_units(Path(raw))

    def test_references_exclude_missionpack_only_content(self) -> None:
        references = baseq3_references(ROOT / "ioq3")
        literals = set()
        for module in references.values():
            literals |= set(module.literals)
        self.assertIn("sound/world/jumppad.wav", literals)
        self.assertIn("models/weapons2/railgun/railgun.md3", literals)
        # These live only inside MISSIONPACK branches of the pinned sources.
        self.assertNotIn("models/players/james/lower.md3", literals)
        self.assertNotIn("sound/player/james/death1.wav", literals)

    def test_an_unterminated_conditional_is_an_error(self) -> None:
        # The one path by which the deliberately superset-producing reader
        # could silently lose the tail of a translation unit.
        with self.assertRaisesRegex(ReferenceError, "unterminated"):
            select_compiled_lines('#ifdef MISSIONPACK\ndrop "x"\n', frozenset())

    def test_registration_arguments_are_read_through_their_shapes(self) -> None:
        # Each line exercises one property of the argument scanner: plain
        # literals, a va() wrapper whose later arguments must be ignored, a
        # depth-0 comma ending the first argument, adjacent-literal
        # concatenation, a char literal that must not open a string, an escaped
        # quote, and a trap that is not a registration.
        text = "\n".join(
            [
                'cgs.media.a = trap_R_RegisterShader( "smokePuff" );',
                'cgs.media.b = trap_R_RegisterShaderNoMip("menuback");',
                "cgs.media.c = trap_R_RegisterModel("
                ' va( "models/players/%s/head.md3", name ) );',
                'cgs.media.d = trap_S_RegisterSound( "sound/n_health.wav",'
                " qfalse );",
                'trap_R_RegisterSkin(va("models/players/%s/%s.skin", m, s));',
                'trap_R_RegisterShader( "gfx/" "2d/" "crosshaira" );',
                "trap_R_RegisterShader( va(\"%s%i\", names[i], 'a' + j) );",
                'trap_R_RegisterShader( "say \\"hi\\"" );',
                'trap_Cvar_Set( "model", "sarge" );',
            ]
        )
        found = set(registration_references(text))
        self.assertIn(("shader", "smokePuff"), found)
        self.assertIn(("shader", "menuback"), found)
        self.assertIn(("model", "models/players/%s/head.md3"), found)
        self.assertIn(("sound", "sound/n_health.wav"), found)
        self.assertNotIn(("sound", "qfalse"), found)
        self.assertIn(("skin", "models/players/%s/%s.skin"), found)
        self.assertIn(("shader", "gfx/2d/crosshaira"), found)
        self.assertIn(("shader", "%s%i"), found)
        self.assertIn(("shader", 'say "hi"'), found)
        for kind, name in found:
            self.assertNotIn(name, ("model", "sarge"))


def _committed_provenance() -> dict:
    return _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")


def _all_members(provenance: dict) -> list[dict]:
    """Every member of every archive, which is what the whole-set checks read."""
    return [
        member
        for archive in provenance["archives"]
        for member in archive["members"]
    ]


class BuildGateTests(unittest.TestCase):
    """The gates in build-content-pack.py, exercised through its own module.

    The file is a CLI with a hyphenated name, so it is loaded by path; without
    this the gates that decide whether a recipe may produce a pack would have
    no test at all.
    """

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "build_content_pack", ROOT / "scripts" / "build-content-pack.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.module = module
        cls.recipe = load_recipe(RECIPE_PATH)
        cls.fragments = load_map_fragments(ROOT, cls.recipe)

    def _build(
        self,
        recipe: dict,
        archive_dir: Path,
        output: Path,
        fragments: dict | None = None,
    ) -> int:
        arguments = argparse.Namespace(
            archive_dir=archive_dir,
            output_dir=output,
            provenance_output=output / "p.json",
            manifest_output=output / "m.json",
            producer_commit="0" * 40,
        )
        if fragments is None:
            fragments = self.fragments
        with tempfile.TemporaryDirectory() as raw:
            fake_root = Path(raw)
            (fake_root / "content").mkdir()
            (fake_root / "content" / "maps").mkdir()
            (fake_root / "locks").mkdir()
            (fake_root / "content" / "pack-recipe.json").write_text(
                json.dumps(recipe, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            for name, fragment in fragments.items():
                (fake_root / "content" / "maps" / f"{name}.json").write_text(
                    json.dumps(fragment, indent=2, sort_keys=True, ensure_ascii=False)
                    + "\n",
                    encoding="utf-8",
                )
            (fake_root / "locks" / "baseline.json").write_bytes(
                (ROOT / "locks" / "baseline.json").read_bytes()
            )
            # The measured per-map figures, for exactly the fragments this
            # build carries. Synthetic values: what the build asserts is that a
            # measurement exists for every published map and fits the pinned
            # engine's hunk, not what any particular map peaks at.
            (fake_root / "records").mkdir()
            (fake_root / "records" / "map-resource-measurements.json").write_text(
                json.dumps(
                    {
                        "$comment": ["synthetic"],
                        "formatVersion": 1,
                        "maps": {
                            name: {"peakHunkBytes": 31441576} for name in fragments
                        },
                        "method": {
                            "engineCommit": json.loads(
                                (ROOT / "locks" / "baseline.json").read_text(
                                    encoding="utf-8"
                                )
                            )["engine"]["commit"],
                        },
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (fake_root / "ioq3").symlink_to(ROOT / "ioq3")
            return self.module.build(fake_root, arguments)

    def test_an_unexpanded_template_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        recipe["templateExpansions"] = [
            entry
            for entry in recipe["templateExpansions"]
            if entry["template"] != "maps/%s.bsp"
        ]
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "have no recipe expansion"):
                self._build(recipe, output, output)

    def test_a_template_the_qvms_do_not_use_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        recipe["templateExpansions"].append(
            {"expansions": [], "reason": "x" * 40, "template": "invented/%s.tga"}
        )
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "templates the QVMs do not use"):
                self._build(recipe, output, output)

    def test_an_expansion_without_a_declared_kind_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        for entry in recipe["templateExpansions"]:
            if entry.get("expansions") and "kind" in entry:
                entry.pop("kind")
                break
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "declaring which"):
                self._build(recipe, output, output)

    def test_a_generated_source_not_bound_to_the_recipe_stops_the_build(self) -> None:
        for field in ("sourceIdentity", "preferredSourceRevision"):
            recipe = json.loads(json.dumps(self.recipe))
            recipe["generatedSource"][field] = "sha256:" + "3" * 64
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, "literal 'recipe'"):
                    self._build(recipe, output, output)

    def test_a_map_template_carrying_a_whole_set_list_stops_the_build(self) -> None:
        """The three `%s` map templates are derived per map, never listed.

        WP-A kept them as three flat lists every packaged map had to be added
        to, so a map could join one and be forgotten in the others. Under
        per-map archives they are expanded from the archive's own map, and a
        recipe that still carries a list is refused rather than half-honoured.
        """
        for template in ("maps/%s.bsp", "levelshots/%s", "levelshots/%s.tga"):
            recipe = json.loads(json.dumps(self.recipe))
            for entry in recipe["templateExpansions"]:
                if entry["template"] == template:
                    entry["expansions"] = ["maps/oa_pvomit.bsp"]
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, "must not"):
                    self._build(recipe, output, output)

    def test_a_map_template_without_its_per_map_shape_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        for entry in recipe["templateExpansions"]:
            if entry["template"] == "levelshots/%s":
                entry["expandsPerMap"] = "levelshots/{map}.jpg"
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "must declare expandsPerMap"):
                self._build(recipe, output, output)

    def test_a_non_map_template_declaring_a_per_map_shape_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        for entry in recipe["templateExpansions"]:
            if entry["template"] not in self.module.MAP_TEMPLATES:
                entry["expandsPerMap"] = "x/{map}"
                break
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "must not\\n?.*expandsPerMap|not a map template"):
                self._build(recipe, output, output)

    def test_an_arena_missing_a_generated_field_stops_the_build(self) -> None:
        fragments = json.loads(json.dumps(self.fragments))
        fragments["oa_pvomit"]["arena"].pop("longname")
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, r"is missing \['longname'\]"):
                self._build(self.recipe, output, output, fragments)

    def test_an_arena_value_that_breaks_the_generated_grammar_stops_the_build(
        self,
    ) -> None:
        for value in ('a "quoted" name', "a {braced} name", "a // comment", "two\nlines"):
            fragments = json.loads(json.dumps(self.fragments))
            fragments["oa_pvomit"]["arena"]["longname"] = value
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, "cannot carry unambiguously"):
                    self._build(self.recipe, output, output, fragments)

    def test_an_empty_arena_value_stops_the_build(self) -> None:
        fragments = json.loads(json.dumps(self.fragments))
        fragments["oa_pvomit"]["arena"]["longname"] = ""
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "must be a non-empty string"):
                self._build(self.recipe, output, output, fragments)

    def test_a_fragment_whose_arena_names_another_map_stops_the_build(self) -> None:
        fragments = json.loads(json.dumps(self.fragments))
        fragments["oa_pvomit"]["arena"]["map"] = "elsewhere"
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "must define map"):
                self._build(self.recipe, output, output, fragments)

    def test_a_bot_model_the_profile_does_not_package_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        recipe["profile"]["bots"][0]["model"] = "nobody/default"
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "does not package"):
                self._build(recipe, output, output)

    def test_an_engine_constant_is_read_from_the_pinned_tree(self) -> None:
        # The gamecode's own value, not a number restated here.
        self.assertEqual(
            self.module.engine_constant(
                ROOT / "ioq3", "MAX_BOTS_TEXT", self.module._GAMECODE_HEADER
            ),
            8192,
        )
        with tempfile.TemporaryDirectory() as raw:
            fake = Path(raw) / "code" / "game"
            fake.mkdir(parents=True)
            (fake / "bg_public.h").write_text("#define MAX_BOTS 1024\n")
            with self.assertRaisesRegex(ContentError, "no longer defines"):
                self.module.engine_constant(
                    Path(raw), "MAX_BOTS_TEXT", self.module._GAMECODE_HEADER
                )

    def _resource_record(self, root: Path, **override) -> Path:
        baseline = json.loads(
            (ROOT / "locks" / "baseline.json").read_text(encoding="utf-8")
        )
        record = {
            "$comment": ["synthetic"],
            "formatVersion": 1,
            "maps": {"oa_pvomit": {"peakHunkBytes": 31441576}},
            "method": {"engineCommit": baseline["engine"]["commit"]},
        }
        record.update(override)
        (root / "records").mkdir(parents=True, exist_ok=True)
        path = root / "records" / "map-resource-measurements.json"
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def _load_resources(self, root: Path, maps=("oa_pvomit",)):
        from content_pack import load_map_resources

        baseline = json.loads(
            (ROOT / "locks" / "baseline.json").read_text(encoding="utf-8")
        )
        return load_map_resources(root, ROOT / "ioq3", baseline, maps)

    def test_the_measured_figures_are_read_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._resource_record(root)
            self.assertEqual(
                self._load_resources(root),
                {"oa_pvomit": {"peakHunkBytes": 31441576}},
            )

    def test_a_map_with_no_measurement_stops_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._resource_record(root)
            with self.assertRaisesRegex(ContentError, "does not match the committed"):
                self._load_resources(root, maps=("oa_pvomit", "am_galmevish"))

    def test_a_measurement_for_no_published_map_stops_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._resource_record(
                root,
                maps={
                    "oa_pvomit": {"peakHunkBytes": 31441576},
                    "retired": {"peakHunkBytes": 1},
                },
            )
            with self.assertRaisesRegex(ContentError, "does not match the committed"):
                self._load_resources(root)

    def test_a_peak_hunk_the_engine_could_not_allocate_stops_the_build(self) -> None:
        """The bound is DEF_COMHUNKMEGS out of the pinned engine, not a number
        restated here, so an engine that shrank the hunk cannot leave this
        permissive."""
        ceiling = (
            self.module.engine_constant(
                ROOT / "ioq3", "DEF_COMHUNKMEGS", "code/qcommon/common.c"
            )
            * 1024
            * 1024
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._resource_record(root, maps={"oa_pvomit": {"peakHunkBytes": ceiling}})
            with self.assertRaisesRegex(ContentError, "Hunk_Alloc failed"):
                self._load_resources(root)
            self._resource_record(
                root, maps={"oa_pvomit": {"peakHunkBytes": ceiling - 1}}
            )
            self._load_resources(root)

    def test_a_measurement_from_another_engine_stops_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._resource_record(root, method={"engineCommit": "0" * 40})
            with self.assertRaisesRegex(ContentError, "does not survive an engine move"):
                self._load_resources(root)

    def test_a_bot_file_the_engine_would_drop_stops_the_build(self) -> None:
        """G_LoadBotsFromFile drops the whole file at MAX_BOTS_TEXT, and then
        no bot connects at all, which a dedicated-server run measured."""
        recipe = json.loads(json.dumps(self.recipe))
        bot = recipe["profile"]["bots"][0]
        recipe["profile"]["bots"] = [
            dict(bot, name=f"Bot{index:03d}") for index in range(200)
        ]
        metadata = self.module._base_metadata(recipe)
        self.assertGreaterEqual(len(metadata["scripts/bots.txt"].encode()), 8192)
        with self.assertRaisesRegex(ContentError, "no bot connects"):
            self.module._check_base_metadata(metadata, recipe, {}, ROOT / "ioq3")

    def test_the_base_arena_file_names_no_map(self) -> None:
        """The byte-stability property of the whole split, as a gate."""
        metadata = self.module._base_metadata(self.recipe)
        self.assertEqual(
            parse_key_value_blocks(metadata["scripts/arenas.txt"]), []
        )
        named = dict(metadata)
        named["scripts/arenas.txt"] += '{\nmap\t\t"oa_pvomit"\n}\n'
        with self.assertRaisesRegex(ContentError, "must not depend on which maps"):
            self.module._check_base_metadata(named, self.recipe, {}, ROOT / "ioq3")

    def test_the_arena_file_listing_budget_comes_from_the_engine(self) -> None:
        """G_LoadArenas truncates the .arena listing silently, so it is a gate."""
        self.module._check_arena_file_listing(ROOT / "ioq3", ["oa_pvomit.arena"])
        many = [f"a_very_long_map_name_{index:04d}.arena" for index in range(40)]
        with self.assertRaisesRegex(ContentError, "without a word"):
            self.module._check_arena_file_listing(ROOT / "ioq3", many)

    def test_the_map_metadata_is_one_arena_for_its_own_map(self) -> None:
        metadata = self.module._map_metadata(self.fragments["oa_pvomit"])
        self.assertEqual(list(metadata), ["scripts/oa_pvomit.arena"])
        blocks = parse_key_value_blocks(metadata["scripts/oa_pvomit.arena"])
        self.assertEqual([block["map"] for block in blocks], ["oa_pvomit"])
        engine = ROOT / "ioq3"
        self.module._check_map_metadata(
            "oa_pvomit", metadata, {"maps/oa_pvomit.bsp": b"1"}, engine
        )
        with self.assertRaisesRegex(ContentError, "does not carry"):
            self.module._check_map_metadata("oa_pvomit", metadata, {}, engine)
        oversized = {
            list(metadata)[0]: '{\nmap\t\t"oa_pvomit"\nlongname\t\t"'
            + "x" * 9000
            + '"\n}\n'
        }
        with self.assertRaisesRegex(ContentError, "MAX_ARENAS_TEXT"):
            self.module._check_map_metadata("oa_pvomit", oversized, {}, engine)


class ArchiveSetGateTests(unittest.TestCase):
    """`_check_archive_set` — the gates that only the finished set can decide.

    They live in one function so that a test can reach all five without
    assembling gigabytes of upstream content: the reviewable risk is not that
    the individual checks are wrong, it is that a call site quietly disappears.
    """

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "build_content_pack_gate", ROOT / "scripts" / "build-content-pack.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.module = module

    def _recipe(self, **overrides) -> dict:
        recipe = {
            "noticeFile": "NOTICE-arena-web.txt",
            "derivedReferences": [],
            "basePackPath": "baseq3/base.pk3",
            "shaderAuthority": {"reason": "test", "selects": "scripts/*.shader"},
        }
        recipe.update(overrides)
        return recipe

    class _Sources:
        """Just enough SourceSet for the shader-authority rule to select from."""

        def __init__(self, paths):
            # The rule refuses a selection that matches nothing, so the default
            # stub carries the one shader file the default archives below hold.
            self._paths = sorted(paths or ["scripts/authority.shader"])

        def paths(self):
            return list(self._paths)

    def _archive(self, path, members):
        return AssembledArchive(
            path=path,
            members=members,
            origins={},
            recipe_input="content/pack-recipe.json",
            recipe_identity="sha256:" + "1" * 64,
        )

    def _report(self, shader_names=None):
        found = ClosureReport()
        found.shader_names.update(shader_names or {})
        return (found, {})

    def _run(self, archives, reports=None, fragments=(), recipe=None, sources=None):
        self.module._check_archive_set(
            recipe or self._recipe(),
            sources if sources is not None else self._Sources([]),
            archives,
            reports or {"base": self._report()},
            {name: {} for name in fragments},
            ROOT / "ioq3",
        )

    def test_the_committed_shape_passes(self) -> None:
        self._run(
            [
                self._archive(
                    "baseq3/base.pk3",
                    {"gfx/a.tga": b"one", "scripts/authority.shader": b""},
                )
            ],
            fragments=("oa_pvomit",),
        )

    def test_a_member_two_archives_disagree_on_is_refused(self) -> None:
        with self.assertRaisesRegex(ContentError, "different bytes in two archives"):
            self._run(
                [
                    self._archive(
                "baseq3/base.pk3",
                {"gfx/a.tga": b"one", "scripts/authority.shader": b""},
            ),
                    self._archive("baseq3/map-m.pk3", {"gfx/a.tga": b"two"}),
                ]
            )

    def test_the_notice_each_archive_generates_is_exempt(self) -> None:
        self._run(
            [
                self._archive(
                "baseq3/base.pk3",
                {"NOTICE-arena-web.txt": b"one", "scripts/authority.shader": b""},
            ),
                self._archive("baseq3/map-m.pk3", {"NOTICE-arena-web.txt": b"two"}),
            ]
        )

    def test_a_shader_the_runtime_resolves_elsewhere_is_refused(self) -> None:
        """Directly, because the authority rule makes this set unbuildable.

        Spreading two definitions of one name over two archives is what
        `check_shader_resolution` exists for, and it is also exactly what
        `check_shader_authority` now forbids, so `_check_archive_set` can no
        longer reach it. The behaviour is still tested here; that the call site
        exists is tested below with a set the authority rule permits.
        """
        base = b"models/x/skin\n{\n{\nmap models/x/skin.tga\n}\n}\n"
        other = b"models/x/skin\n{\n{\nmap models/x/other.tga\n}\n}\n"
        archives = {
            "a-base.pk3": {"scripts/a.shader": base},
            "a-map-m.pk3": {"scripts/z.shader": other},
        }
        check_shader_resolution(archives, {"models/x/skin": "scripts/a.shader"})
        with self.assertRaisesRegex(ContentError, "wins at run time"):
            check_shader_resolution(archives, {"models/x/skin": "scripts/z.shader"})

    def test_a_resolved_shader_no_archive_packages_is_refused(self) -> None:
        """The `check_shader_resolution` call site, through the set gate."""
        reports = {"base": self._report({"models/x/skin": "scripts/a.shader"})}
        with self.assertRaisesRegex(ContentError, "which no archive packages"):
            self._run(
                [self._archive("baseq3/base.pk3", {"scripts/authority.shader": b""})],
                reports,
            )

    def test_the_shader_authority_rule_holds(self) -> None:
        sources = self._Sources(["scripts/a.shader", "scripts/z.shader", "gfx/a.tga"])
        self._run(
            [
                self._archive(
                    "baseq3/base.pk3",
                    {"scripts/a.shader": b"", "scripts/z.shader": b""},
                ),
                self._archive("baseq3/map-m.pk3", {"gfx/a.tga": b"one"}),
            ],
            sources=sources,
        )

    def test_a_shader_file_the_base_leaves_out_is_refused(self) -> None:
        sources = self._Sources(["scripts/a.shader", "scripts/z.shader"])
        with self.assertRaisesRegex(ContentError, "missing \\['scripts/z.shader'\\]"):
            self._run(
                [self._archive("baseq3/base.pk3", {"scripts/a.shader": b""})],
                sources=sources,
            )

    def test_a_selection_that_matches_nothing_is_refused(self) -> None:
        """The one way to disarm the rule without failing anything else.

        Every check below derives from the same pattern, so a pattern matching
        no source path makes the base carry no shader file, gives no archive a
        stray one, and passes — with the rule effectively deleted. One
        character in the recipe is enough to get there.
        """
        recipe = self._recipe(
            shaderAuthority={"reason": "test", "selects": "scripts/*.shaders"}
        )
        with self.assertRaisesRegex(ContentError, "selects\\s+no source path"):
            self._run(
                [self._archive("baseq3/base.pk3", {"scripts/authority.shader": b""})],
                recipe=recipe,
                sources=self._Sources(["scripts/a.shader"]),
            )

    def test_a_map_archive_that_carries_a_shader_file_is_refused(self) -> None:
        """Removing the rule must fail here, not silently disarm the check.

        Without this, dropping the shader-authority root would put shader files
        back into map archives, and `check_shader_resolution` would go from
        "cannot fire" to "does not fire" with nothing to distinguish the two.
        """
        sources = self._Sources(["scripts/a.shader"])
        with self.assertRaisesRegex(ContentError, "only base.pk3 may"):
            self._run(
                [
                    self._archive("baseq3/base.pk3", {"scripts/a.shader": b""}),
                    self._archive("baseq3/map-m.pk3", {"scripts/a.shader": b""}),
                ],
                sources=sources,
            )

    def test_too_many_arena_files_to_list_are_refused(self) -> None:
        with self.assertRaisesRegex(ContentError, "without a word"):
            self._run(
                [self._archive("baseq3/base.pk3", {"scripts/authority.shader": b""})],
                fragments=[f"a_very_long_map_name_{index:04d}" for index in range(40)],
            )

    def test_an_excluded_derived_reference_in_any_archive_is_refused(self) -> None:
        recipe = self._recipe(
            derivedReferences=[
                {
                    "reference": "models/weapons2/x/x_hand.md3",
                    "excludedReason": "outside the profile",
                }
            ]
        )
        self._run(
            [self._archive("baseq3/base.pk3", {"scripts/authority.shader": b""})],
            recipe=recipe,
        )
        with self.assertRaisesRegex(ContentError, "packages it anyway"):
            self._run(
                [
                    self._archive(
                        "baseq3/base.pk3", {"scripts/authority.shader": b""}
                    ),
                    self._archive(
                        "baseq3/map.pk3", {"models/weapons2/x/x_hand.md3": b"1"}
                    ),
                ],
                recipe=recipe,
            )


class MapFragmentTests(unittest.TestCase):
    """The per-map recipe fragments: the directory *is* the map set."""

    def setUp(self) -> None:
        self.recipe = {"noticeFile": "NOTICE-arena-web.txt"}

    def _fragment(self, **overrides) -> dict:
        fragment = {
            "map": "a_map",
            "arena": {
                "map": "a_map",
                "longname": "A Map",
                "bots": "Skelebot",
                "fraglimit": "15",
                "type": "ffa",
            },
            "acceptedUnresolved": [],
            "generatedMembers": ["NOTICE-arena-web.txt", "scripts/a_map.arena"],
        }
        fragment.update(overrides)
        return fragment

    def _write(self, root: Path, name: str, fragment: dict) -> None:
        directory = root / "content" / "maps"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(
            json.dumps(fragment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def test_the_committed_shader_authority_selects_real_files(self) -> None:
        """The rule as committed, against the archives the recipe pins.

        The synthetic sources above prove the gate; this proves the recipe's
        own pattern is not the one that matches nothing.
        """
        recipe = load_recipe(RECIPE_PATH)
        self.assertEqual(recipe["shaderAuthority"]["selects"], "scripts/*.shader")
        manifest = json.loads(
            (ROOT / "provenance/arena-web-ffa-content.json").read_text()
        )
        base = recipe["basePackPath"].rsplit("/", 1)[-1]
        packaged = {
            member["path"]
            for archive in manifest["archives"]
            if archive["path"].rsplit("/", 1)[-1] == base
            for member in archive["members"]
            if member["path"].startswith("scripts/")
            and member["path"].endswith(".shader")
        }
        # 99 in these sources; the assertion is that it is a real set, not a
        # count someone has to maintain.
        self.assertGreater(len(packaged), 50)

    def test_the_committed_fragment_set_loads(self) -> None:
        """Every fragment on disk, against the set the manifest records.

        The two directions are what keep content from joining the build without
        joining the release identity, so the assertion is that comparison and
        not a list of the map names someone last added.
        """
        fragments = load_map_fragments(ROOT, load_recipe(RECIPE_PATH))
        manifest = json.loads(
            (ROOT / "provenance/arena-web-ffa-content-manifest.json").read_text()
        )
        declared = sorted(
            item["id"].removeprefix("arena-web-map-")
            for item in manifest["inputs"]
            if item["id"].startswith("arena-web-map-")
        )
        self.assertEqual(sorted(fragments), declared)
        self.assertIn("oa_pvomit", fragments)
        for name, fragment in fragments.items():
            self.assertEqual(fragment["arena"]["map"], name)

    def test_no_fragment_directory_is_an_empty_map_set(self) -> None:
        """A base with no map at all is a legal product, not an error."""
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(load_map_fragments(Path(raw), self.recipe), {})

    def test_a_fragment_named_after_another_map_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "a_map", self._fragment(map="other"))
            with self.assertRaisesRegex(ContentError, "its file name"):
                load_map_fragments(root, self.recipe)

    def test_a_fragment_with_the_wrong_field_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fragment = self._fragment()
            fragment["extra"] = 1
            self._write(root, "a_map", fragment)
            with self.assertRaisesRegex(ContentError, "exactly the fields"):
                load_map_fragments(root, self.recipe)

    def test_a_non_json_file_in_the_fragment_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "a_map", self._fragment())
            (root / "content" / "maps" / "notes.txt").write_text("x")
            with self.assertRaisesRegex(ContentError, "not a .json fragment"):
                load_map_fragments(root, self.recipe)

    def test_an_arena_for_another_map_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fragment = self._fragment()
            fragment["arena"]["map"] = "other"
            self._write(root, "a_map", fragment)
            with self.assertRaisesRegex(ContentError, "must define map"):
                load_map_fragments(root, self.recipe)

    def test_the_map_pack_path_is_derived_from_the_template(self) -> None:
        recipe = {"mapPackTemplate": "baseq3/p-{map}.pk3"}
        self.assertEqual(map_pack_path(recipe, "a_map"), "baseq3/p-a_map.pk3")


class ArchiveSetTests(unittest.TestCase):
    """The cross-archive invariants, over a finished archive set."""

    def test_a_member_two_archives_disagree_on_is_refused(self) -> None:
        archives = {
            "base.pk3": {"gfx/a.tga": b"one"},
            "map.pk3": {"gfx/a.tga": b"two"},
        }
        with self.assertRaisesRegex(ContentError, "different bytes in two archives"):
            check_duplicate_members(archives)
        check_duplicate_members(archives, exempt=["gfx/a.tga"])
        check_duplicate_members({"base.pk3": {"gfx/a.tga": b"one"},
                                 "map.pk3": {"gfx/a.tga": b"one"}})

    def test_a_shader_name_the_runtime_resolves_elsewhere_is_refused(self) -> None:
        """The lowest-named PK3 wins a shader name; the closure must agree."""
        base = b"models/x/skin\n{\n{\nmap models/x/skin.tga\n}\n}\n"
        other = b"models/x/skin\n{\n{\nmap models/x/other.tga\n}\n}\n"
        archives = {
            "arena-web-ffa-base.pk3": {"scripts/a.shader": base},
            "arena-web-ffa-map-m.pk3": {"scripts/z.shader": other},
        }
        # Closed against the base's file, which is also the run-time winner.
        check_shader_resolution(archives, {"models/x/skin": "scripts/a.shader"})
        # Closed against the map's file, which the base's would beat.
        with self.assertRaisesRegex(ContentError, "wins at run time"):
            check_shader_resolution(archives, {"models/x/skin": "scripts/z.shader"})

    def test_a_file_two_map_archives_share_is_ranked_where_the_engine_ranks_it(
        self,
    ) -> None:
        """`FS_AddFileToList` keeps only the first occurrence of a name.

        So a shader file two map archives both carry is listed once, at the
        position of the *highest*-named archive — which is earlier in the
        listing, and therefore loses to everything after it. A model that
        credited it to its lowest archive would call this safe.
        """
        shared = b"n\n{\n{\nmap x/shared.tga\n}\n}\n"
        other = b"n\n{\n{\nmap x/other.tga\n}\n}\n"
        archives = {
            "a-base.pk3": {},
            "a-map-alpha.pk3": {"scripts/aaa.shader": other,
                                "scripts/common.shader": shared},
            "a-map-zulu.pk3": {"scripts/common.shader": shared},
        }
        # The listing is [zulu/common, alpha/aaa] — alpha's common.shader is
        # deduped away — and the last entry wins, so `aaa.shader` does.
        check_shader_resolution(archives, {"n": "scripts/aaa.shader"})
        with self.assertRaisesRegex(ContentError, "wins at run time"):
            check_shader_resolution(archives, {"n": "scripts/common.shader"})

    def test_the_archive_order_is_the_engine_comparator(self) -> None:
        """`FS_PathCmp` uppercases before comparing, so `_` sorts after every
        letter; Python's `sorted()` puts it before every lower-case one. Map
        names may contain `_` — `oa_pvomit` does."""
        definition = b"n\n{\n{\nmap x/a.tga\n}\n}\n"
        archives = {
            "a-map-oa_pvomit.pk3": {"scripts/p.shader": definition},
            "a-map-oab.pk3": {"scripts/z.shader": definition},
        }
        # Engine order: oab < oa_pvomit, so oa_pvomit is listed first and
        # `z.shader` in oab wins. Python order would say the opposite.
        check_shader_resolution(archives, {"n": "scripts/z.shader"})
        with self.assertRaisesRegex(ContentError, "wins at run time"):
            check_shader_resolution(archives, {"n": "scripts/p.shader"})

    def test_a_shader_no_archive_packages_is_refused(self) -> None:
        with self.assertRaisesRegex(ContentError, "no archive packages"):
            check_shader_resolution({"base.pk3": {}}, {"x": "scripts/a.shader"})

    def test_the_subtraction_keeps_the_notice_set(self) -> None:
        def report(paths):
            found = ClosureReport()
            for path in paths:
                found.members[path] = object()
            return found

        base = report(["copying", "gfx/a.tga"])
        archive = report(["copying", "gfx/a.tga", "maps/m.bsp"])
        self.assertEqual(
            sorted(subtract_closure(archive, base)), ["maps/m.bsp"]
        )
        self.assertEqual(
            sorted(subtract_closure(archive, base, keep=["COPYING"])),
            ["copying", "maps/m.bsp"],
        )


class CommittedRecipeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recipe = load_recipe(RECIPE_PATH)

    def test_recipe_loads_and_pins_every_source_by_digest(self) -> None:
        for source in self.recipe["sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(source["sourceIdentity"], f"sha256:{source['sha256']}")
            self.assertTrue(
                source["url"].startswith("https://snapshot.debian.org/file/")
            )
            self.assertGreater(source["size"], 0)

    def test_every_accepted_reference_states_a_reason(self) -> None:
        for entry in self.recipe["acceptedUnresolved"]:
            self.assertGreater(len(entry["reason"]), 30, entry["reference"])

    def test_every_template_expansion_is_declared_or_explained(self) -> None:
        for entry in self.recipe["templateExpansions"]:
            if "expandsPerMap" in entry:
                # A map template is derived from the archive's own map, so it
                # carries neither a whole-set list nor a reason for having none.
                continue
            if not entry.get("expansions"):
                self.assertIn("reason", entry, entry["template"])
                self.assertGreater(len(entry["reason"]), 30, entry["template"])

    def test_the_recipe_covers_every_template_the_pinned_qvms_use(self) -> None:
        declared = {entry["template"] for entry in self.recipe["templateExpansions"]}
        used = set()
        for module in baseq3_references(ROOT / "ioq3").values():
            used |= set(module.templates)
            used |= {name for _kind, name in module.registration_templates}
        self.assertEqual(declared, used)

    def test_every_expanding_template_declares_the_kind_it_expands_to(self) -> None:
        for entry in self.recipe["templateExpansions"]:
            if entry.get("expansions"):
                self.assertIn("kind", entry, entry["template"])
                self.assertIn(
                    entry["kind"],
                    {
                        "bsp",
                        "botfile",
                        "file",
                        "image",
                        "model",
                        "shader",
                        "skin",
                        "sound",
                    },
                )

    def test_the_bare_registration_names_reach_the_packaged_members(self) -> None:
        # The regression the review found: a prefix filter over string literals
        # cannot see a shader the gamecode registers as `white` or `menuback`.
        registered = set()
        for module in baseq3_references(ROOT / "ioq3").values():
            registered |= {name for _kind, name in module.registrations}
        for name in ("white", "menuback", "powerups/quad", "viewBloodBlend"):
            self.assertIn(name, registered)
        provenance = _committed_provenance()
        paths = {member["path"].lower() for member in _all_members(provenance)}
        self.assertIn("scripts/newmenu.shader", paths)

    def test_every_derived_reference_is_included_or_excluded_with_a_reason(
        self,
    ) -> None:
        entries = self.recipe["derivedReferences"]
        self.assertEqual(len(entries), 15)
        for entry in entries:
            if "excludedReason" in entry:
                self.assertGreater(len(entry["excludedReason"]), 30, entry["reference"])
            else:
                self.assertTrue(entry["members"], entry["reference"])

    def test_the_committed_provenance_packages_every_derived_member(self) -> None:
        provenance = _committed_provenance()
        paths = {member["path"].lower() for member in _all_members(provenance)}
        for entry in self.recipe["derivedReferences"]:
            if "excludedReason" in entry:
                self.assertNotIn(entry["reference"].lower(), paths)
                continue
            for member in entry["members"]:
                self.assertIn(member.lower(), paths)

    def test_the_committed_provenance_covers_every_published_archive(self) -> None:
        provenance = _committed_provenance()
        manifest = _load_json(
            ROOT / "provenance" / "arena-web-ffa-content-manifest.json"
        )
        self.assertEqual(
            sorted(archive["path"] for archive in provenance["archives"]),
            sorted(item["path"] for item in manifest["artifacts"]),
        )
        # Each archive is published under its own URL and redistributed on its
        # own, so each carries the complete notice set.
        for archive in provenance["archives"]:
            paths = {member["path"] for member in archive["members"]}
            for notice in self.recipe["notices"]:
                self.assertIn(notice, paths, archive["path"])

    def test_the_committed_manifest_binds_the_recipe_by_digest(self) -> None:
        # The committed manifest's arena-web input identity is the recipe's
        # own SHA-256, so the committed records provably belong to the
        # committed recipe without running the containerized verification.
        manifest = _load_json(
            ROOT / "provenance" / "arena-web-ffa-content-manifest.json"
        )
        identities = {item["id"]: item["identity"] for item in manifest["inputs"]}
        generated_id = self.recipe["generatedSource"]["id"]
        self.assertEqual(
            identities[generated_id], f"sha256:{file_sha256(RECIPE_PATH)}"
        )

    def test_generated_members_are_declared_as_such(self) -> None:
        self.assertIn(self.recipe["noticeFile"], self.recipe["generatedMembers"])
        self.assertIn(self.recipe["noticeFile"], self.recipe["notices"])

    def test_committed_manifest_agrees_with_the_recipe_digests(self) -> None:
        manifest = _load_json(
            ROOT / "provenance" / "arena-web-ffa-content-manifest.json"
        )
        identities = {item["id"]: item["identity"] for item in manifest["inputs"]}
        for source in self.recipe["sources"]:
            self.assertEqual(identities[source["id"]], f"sha256:{source['sha256']}")
        self.assertEqual(identities["ioq3"], f"git:{BASELINE['engine']['commit']}")
        self.assertEqual(manifest["baselineInputIds"], ["ioq3"])

    def test_committed_provenance_matches_the_pack_profile(self) -> None:
        provenance = _committed_provenance()
        self.assertEqual(provenance["package"]["id"], self.recipe["package"]["id"])
        paths = {member["path"] for member in _all_members(provenance)}
        for name in load_map_fragments(ROOT, self.recipe):
            self.assertIn(f"maps/{name}.bsp", paths)
            self.assertIn(f"maps/{name}.aas", paths)
        self.assertFalse(iter_forbidden(paths))
        for notice in self.recipe["notices"]:
            self.assertIn(notice, paths)
        roles = {member["path"]: member["role"] for member in _all_members(provenance)}
        for notice in self.recipe["notices"]:
            self.assertEqual(roles[notice], "notice")

    def test_committed_provenance_declares_one_allowed_licence(self) -> None:
        provenance = _committed_provenance()
        allowed = set(BASELINE["licensePolicy"]["productInputAllowedExpressions"])
        expressions = {member["licenseExpression"] for member in _all_members(provenance)}
        self.assertEqual(expressions, {"GPL-2.0-or-later"})
        self.assertTrue(expressions <= allowed)

    def test_a_recipe_without_its_required_fields_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            broken = Path(raw) / "recipe.json"
            broken.write_text(
                json.dumps({"formatVersion": 1}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MetadataError):
                load_recipe(broken)


if __name__ == "__main__":
    unittest.main()
