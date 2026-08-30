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
    iter_forbidden,
    load_recipe,
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

    def test_roles_obligations_and_notice_binding(self) -> None:
        provenance = build_provenance(self.RECIPE, BASELINE, self.MEMBERS, self.ORIGINS)
        by_path = {member["path"]: member for member in provenance["members"]}
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
            build_provenance(self.RECIPE, BASELINE, members, origins)

    def test_only_used_sources_are_declared(self) -> None:
        records = provenance_sources(self.RECIPE, {"upstream"})
        self.assertEqual([record["id"] for record in records], ["upstream"])

    def test_a_disallowed_licence_is_rejected(self) -> None:
        recipe = json.loads(json.dumps(self.RECIPE))
        recipe["sources"][0]["licenseExpression"] = "CC-BY-NC-4.0"
        provenance = build_provenance(recipe, BASELINE, self.MEMBERS, self.ORIGINS)
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

    def _build(self, recipe: dict, archive_dir: Path, output: Path) -> int:
        arguments = argparse.Namespace(
            archive_dir=archive_dir,
            output_dir=output,
            provenance_output=output / "p.json",
            manifest_output=output / "m.json",
            producer_commit="0" * 40,
        )
        with tempfile.TemporaryDirectory() as raw:
            fake_root = Path(raw)
            (fake_root / "content").mkdir()
            (fake_root / "locks").mkdir()
            (fake_root / "content" / "pack-recipe.json").write_text(
                json.dumps(recipe, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (fake_root / "locks" / "baseline.json").write_bytes(
                (ROOT / "locks" / "baseline.json").read_bytes()
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
            if entry["template"] == "maps/%s.bsp":
                entry.pop("kind")
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

    def test_an_inconsistent_profile_stops_the_build(self) -> None:
        cases = (
            (["profile", "arena", "map"], "elsewhere", "is not profile.map"),
            (["profile", "bots", 0, "model"], "nobody/default", "does not package"),
            (["profile", "arena", "bots"], "Nobody", "does not list"),
        )
        for path, value, message in cases:
            recipe = json.loads(json.dumps(self.recipe))
            target = recipe
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, message):
                    self._build(recipe, output, output)

    @staticmethod
    def _derived_entry(recipe: dict, reference: str) -> dict:
        for entry in recipe["derivedReferences"]:
            if entry["reference"] == reference:
                return entry
        raise AssertionError(f"recipe has no derived reference {reference}")

    def test_the_committed_derived_references_verify_against_the_pinned_tree(
        self,
    ) -> None:
        references = baseq3_references(ROOT / "ioq3")
        included = self.module._check_derived_references(
            references, self.recipe, ROOT / "ioq3"
        )
        self.assertEqual(len(included), 11)
        included_references = {entry["reference"] for entry in included}
        self.assertNotIn(
            "models/weapons2/grapple/grapple_barrel.md3", included_references
        )
        self.assertNotIn(
            "models/weapons2/machinegun/machinegun_hand.md3", included_references
        )

    def test_a_derived_reference_with_an_unknown_constructing_site_stops_the_build(
        self,
    ) -> None:
        cases = (
            ({"lines": [1, 3]}, "does not construct"),
            ({"file": "code/cgame/cg_invented.c"}, "cannot read constructing"),
            ({"lines": [400000, 400002]}, "entry names"),
            ({"file": "../outside.c"}, "outside the engine tree"),
        )
        for override, message in cases:
            recipe = json.loads(json.dumps(self.recipe))
            entry = self._derived_entry(
                recipe, "models/weapons2/lightning/lightning_flash.md3"
            )
            entry["construction"].update(override)
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, message):
                    self._build(recipe, output, output)

    def test_a_derived_reference_must_be_derivable_from_its_base(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        entry = self._derived_entry(
            recipe, "models/weapons2/lightning/lightning_flash.md3"
        )
        entry["constructedFrom"] = "models/weapons2/plasma/plasma.md3"
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "extension replaced"):
                self._build(recipe, output, output)

    def test_a_derived_base_the_sources_do_not_hold_stops_the_build(self) -> None:
        recipe = json.loads(json.dumps(self.recipe))
        entry = self._derived_entry(
            recipe, "models/weapons2/lightning/lightning_flash.md3"
        )
        entry["constructedFrom"] = "models/weapons2/invented/invented.md3"
        entry["reference"] = "models/weapons2/invented/invented_flash.md3"
        entry["members"] = [entry["reference"]]
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "static readings"):
                self._build(recipe, output, output)

    def test_a_statically_visible_name_is_refused_in_the_derived_category(
        self,
    ) -> None:
        # The live example: cg_weapons.c holds the shotgun_hand fallback as a
        # path-shaped literal, so it is not a derived reference and an entry
        # claiming so misdescribes the closure.
        recipe = json.loads(json.dumps(self.recipe))
        recipe["derivedReferences"].append(
            {
                "constructedFrom": "models/weapons2/shotgun/shotgun.md3",
                "construction": {
                    "appends": "_hand.md3",
                    "file": "code/cgame/cg_weapons.c",
                    "lines": [666, 668],
                },
                "kind": "model",
                "members": ["models/weapons2/shotgun/shotgun_hand.md3"],
                "reference": "models/weapons2/shotgun/shotgun_hand.md3",
            }
        )
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "itself statically"):
                self._build(recipe, output, output)

    def test_a_malformed_derived_entry_stops_the_build(self) -> None:
        base = self._derived_entry(
            self.recipe, "models/weapons2/lightning/lightning_flash.md3"
        )
        cases = (
            ({"excludedReason": "both included and excluded at once"}, "unexpected fields"),
            ({"members": None}, "unexpected fields"),
            ({"kind": "weapon"}, "unknown kind"),
        )
        for override, message in cases:
            recipe = json.loads(json.dumps(self.recipe))
            entry = self._derived_entry(
                recipe, "models/weapons2/lightning/lightning_flash.md3"
            )
            entry.update(json.loads(json.dumps(override)))
            if entry.get("members") is None and "members" in entry:
                del entry["members"]
            with tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                with self.assertRaisesRegex(ContentError, message):
                    self._build(recipe, output, output)
        recipe = json.loads(json.dumps(self.recipe))
        recipe["derivedReferences"].append(json.loads(json.dumps(base)))
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "declared twice"):
                self._build(recipe, output, output)
        recipe = json.loads(json.dumps(self.recipe))
        entry = self._derived_entry(
            recipe, "models/weapons2/grapple/grapple_barrel.md3"
        )
        entry["excludedReason"] = "   "
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            with self.assertRaisesRegex(ContentError, "without a reason"):
                self._build(recipe, output, output)

    def test_a_derived_member_the_closure_did_not_package_stops_the_build(
        self,
    ) -> None:
        entries = [
            {
                "reference": "models/weapons2/x/x_flash.md3",
                "members": ["models/weapons2/x/x_flash.md3"],
            }
        ]
        with self.assertRaisesRegex(ContentError, "did not package"):
            self.module._check_derived_members(entries, {})
        self.module._check_derived_members(
            entries, {"models/weapons2/x/x_flash.md3": object()}
        )

    def test_generated_metadata_must_agree_with_the_members(self) -> None:
        recipe = {
            "profile": {
                "map": "m",
                "bots": [{"name": "B", "model": "x/default", "aifile": "bots/x_c.c"}],
            }
        }
        metadata = {
            "scripts/arenas.txt": '{\nmap\t\t"m"\n}\n',
            "scripts/bots.txt": (
                '{\nname\t\t"B"\nmodel\t\t"x/default"\naifile\t\t"bots/x_c.c"\n}\n'
            ),
        }
        members = {
            "maps/m.bsp": b"1",
            "models/players/x/lower.md3": b"2",
            "botfiles/bots/x_c.c": b"3",
        }
        self.module._check_generated_metadata(metadata, recipe, members)
        with self.assertRaisesRegex(ContentError, "not a packaged member"):
            self.module._check_generated_metadata(
                metadata, recipe, {"maps/m.bsp": b"1"}
            )
        wrong_map = dict(metadata, **{"scripts/arenas.txt": '{\nmap\t\t"o"\n}\n'})
        with self.assertRaisesRegex(ContentError, "profile map"):
            self.module._check_generated_metadata(wrong_map, recipe, members)


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
            if not entry["expansions"]:
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
            if entry["expansions"]:
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
        provenance = _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")
        paths = {member["path"].lower() for member in provenance["members"]}
        self.assertIn("scripts/newmenu.shader", paths)

    def test_every_derived_reference_is_included_or_excluded_with_a_reason(
        self,
    ) -> None:
        entries = self.recipe["derivedReferences"]
        self.assertEqual(len(entries), 13)
        for entry in entries:
            if "excludedReason" in entry:
                self.assertGreater(len(entry["excludedReason"]), 30, entry["reference"])
            else:
                self.assertTrue(entry["members"], entry["reference"])

    def test_the_committed_provenance_packages_every_derived_member(self) -> None:
        provenance = _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")
        paths = {member["path"].lower() for member in provenance["members"]}
        for entry in self.recipe["derivedReferences"]:
            if "excludedReason" in entry:
                self.assertNotIn(entry["reference"].lower(), paths)
                continue
            for member in entry["members"]:
                self.assertIn(member.lower(), paths)

    def test_the_committed_provenance_has_the_amended_member_count(self) -> None:
        provenance = _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")
        self.assertEqual(len(provenance["members"]), 696)

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
        provenance = _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")
        self.assertEqual(provenance["package"]["id"], self.recipe["package"]["id"])
        paths = {member["path"] for member in provenance["members"]}
        self.assertIn(f"maps/{self.recipe['profile']['map']}.bsp", paths)
        self.assertIn(f"maps/{self.recipe['profile']['map']}.aas", paths)
        self.assertFalse(iter_forbidden(paths))
        for notice in self.recipe["notices"]:
            self.assertIn(notice, paths)
        roles = {member["path"]: member["role"] for member in provenance["members"]}
        for notice in self.recipe["notices"]:
            self.assertEqual(roles[notice], "notice")

    def test_committed_provenance_declares_one_allowed_licence(self) -> None:
        provenance = _load_json(ROOT / "provenance" / "arena-web-ffa-content.json")
        allowed = set(BASELINE["licensePolicy"]["productInputAllowedExpressions"])
        expressions = {member["licenseExpression"] for member in provenance["members"]}
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
