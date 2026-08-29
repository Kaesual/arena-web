# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from game_assets import (  # noqa: E402
    BSP_LUMP_COUNT,
    IMAGE_EXTENSIONS,
    SOUND_EXTENSIONS,
    AssetFormatError,
    candidate_paths,
    is_engine_path,
    parse_animation_cfg,
    parse_bsp,
    parse_entities,
    parse_key_value_blocks,
    parse_md3,
    parse_shader_file,
    parse_skin,
    shader_file_precedence,
    strip_comments,
)


def build_bsp(shaders: list[str], entities: str, version: int = 46) -> bytes:
    entity_blob = entities.encode("latin-1")
    shader_blob = b"".join(
        name.encode("latin-1").ljust(64, b"\0") + struct.pack("<ii", 0, 0)
        for name in shaders
    )
    header_size = 8 + BSP_LUMP_COUNT * 8
    lumps = [
        (header_size, len(entity_blob)),
        (header_size + len(entity_blob), len(shader_blob)),
    ]
    lumps += [(header_size + len(entity_blob) + len(shader_blob), 0)] * (
        BSP_LUMP_COUNT - 2
    )
    header = b"IBSP" + struct.pack("<i", version)
    for offset, length in lumps:
        header += struct.pack("<ii", offset, length)
    return header + entity_blob + shader_blob


def build_md3(surface_shaders: list[list[str]]) -> bytes:
    surfaces = b""
    for shaders in surface_shaders:
        shader_blob = b"".join(
            name.encode("latin-1").ljust(64, b"\0") + struct.pack("<i", index)
            for index, name in enumerate(shaders)
        )
        surface_header = bytearray(108)
        surface_header[0:4] = b"IDP3"
        struct.pack_into("<i", surface_header, 76, len(shaders))
        struct.pack_into("<i", surface_header, 92, 108)
        struct.pack_into("<i", surface_header, 104, 108 + len(shader_blob))
        surfaces += bytes(surface_header) + shader_blob
    header = bytearray(108)
    header[0:4] = b"IDP3"
    struct.pack_into("<i", header, 4, 15)
    struct.pack_into("<ii", header, 84, len(surface_shaders), 0)
    struct.pack_into("<i", header, 100, 108)
    return bytes(header) + surfaces


class BspTests(unittest.TestCase):
    def test_reads_shaders_and_entities(self) -> None:
        data = build_bsp(
            ["textures/base/wall", "noshader"],
            '{\n"classname" "worldspawn"\n"music" "music/track.wav"\n}\n',
        )
        info = parse_bsp(data)
        self.assertEqual(info.shaders, ("textures/base/wall", "noshader"))
        self.assertEqual(info.entities[0]["classname"], "worldspawn")
        self.assertEqual(info.entities[0]["music"], "music/track.wav")

    def test_rejects_foreign_format(self) -> None:
        with self.assertRaises(AssetFormatError):
            parse_bsp(b"IBSPnot-a-header")

    def test_rejects_other_bsp_version(self) -> None:
        with self.assertRaises(AssetFormatError):
            parse_bsp(build_bsp(["a"], "{}", version=47))

    def test_rejects_lump_outside_file(self) -> None:
        data = bytearray(build_bsp(["a"], "{}"))
        struct.pack_into("<ii", data, 8 + 1 * 8, 1 << 20, 72)
        with self.assertRaises(AssetFormatError):
            parse_bsp(bytes(data))


class Md3Tests(unittest.TestCase):
    def test_reads_every_surface(self) -> None:
        data = build_md3([["models/a"], ["models/b", "models/c"]])
        self.assertEqual(parse_md3(data), ["models/a", "models/b", "models/c"])

    def test_rejects_non_advancing_surface(self) -> None:
        data = bytearray(build_md3([["models/a"]]))
        struct.pack_into("<i", data, 108 + 104, 0)
        with self.assertRaises(AssetFormatError):
            parse_md3(bytes(data))


class ShaderTests(unittest.TestCase):
    def test_collects_every_stage_image_kind(self) -> None:
        text = """
        textures/sky/one
        {
            skyParms env/blue - -
            {
                map textures/a.tga
                animMap 4 textures/b.tga textures/c.tga
            }
            {
                clampmap textures/d.tga
                map $lightmap
            }
        }
        """
        definitions = parse_shader_file(text)
        self.assertEqual(len(definitions), 1)
        images = set(definitions[0].images)
        self.assertIn("textures/a.tga", images)
        self.assertIn("textures/b.tga", images)
        self.assertIn("textures/c.tga", images)
        self.assertIn("textures/d.tga", images)
        self.assertIn("env/blue_up", images)
        self.assertEqual(
            len([name for name in images if name.startswith("env/blue")]), 6
        )
        self.assertNotIn("$lightmap", images)

    def test_ignores_comments(self) -> None:
        text = "// leading\ntextures/x\n{\n/* map textures/hidden.tga */\nmap textures/real.tga\n}\n"
        definitions = parse_shader_file(text)
        self.assertEqual(definitions[0].images, ("textures/real.tga",))

    def test_rejects_unterminated_body(self) -> None:
        with self.assertRaises(AssetFormatError):
            parse_shader_file("textures/x\n{\nmap a.tga\n")

    def test_rejects_name_without_body(self) -> None:
        with self.assertRaises(AssetFormatError):
            parse_shader_file("textures/x\n{\n}\ntextures/y\n")

    def test_precedence_is_reverse_sorted(self) -> None:
        self.assertEqual(
            shader_file_precedence(
                ["scripts/a.shader", "scripts/z.shader", "scripts/m.shader"]
            ),
            ["scripts/z.shader", "scripts/m.shader", "scripts/a.shader"],
        )

    def test_strip_comments_keeps_content(self) -> None:
        self.assertEqual(strip_comments("a // b\nc").split(), ["a", "c"])


class SkinAndConfigTests(unittest.TestCase):
    def test_skin_skips_tags_and_blanks(self) -> None:
        text = "u_torso,models/players/x/torso\ntag_weapon,\nu_head,\n"
        self.assertEqual(parse_skin(text), ["models/players/x/torso"])

    def test_animation_cfg_reads_sex_and_footsteps(self) -> None:
        settings = parse_animation_cfg("sex f\nfootsteps mech\n0 10 0 20\n")
        self.assertEqual(settings, {"sex": "f", "footsteps": "mech"})

    def test_key_value_blocks(self) -> None:
        blocks = parse_key_value_blocks('{\nmap\t\t"oa_dm1"\nlongname\t"One"\n}\n')
        self.assertEqual(blocks, [{"map": "oa_dm1", "longname": "One"}])

    def test_entities_ignore_trailing_nul(self) -> None:
        self.assertEqual(parse_entities('{\n"a" "b"\n}\n\0garbage'), [{"a": "b"}])


class ResolutionTests(unittest.TestCase):
    def test_extension_is_tried_first_then_all_others(self) -> None:
        self.assertEqual(
            candidate_paths("gfx/x.jpg", IMAGE_EXTENSIONS),
            [
                "gfx/x.jpg",
                "gfx/x.tga",
                "gfx/x.jpeg",
                "gfx/x.png",
                "gfx/x.pcx",
                "gfx/x.bmp",
                "gfx/x.pvr",
            ],
        )

    def test_extensionless_reference_tries_every_loader(self) -> None:
        self.assertEqual(
            candidate_paths("gfx/x", IMAGE_EXTENSIONS),
            [f"gfx/x.{extension}" for extension in IMAGE_EXTENSIONS],
        )

    def test_sound_order_matches_codec_registration(self) -> None:
        self.assertEqual(SOUND_EXTENSIONS, ("wav", "ogg", "opus"))
        self.assertEqual(
            candidate_paths("sound/a.wav", SOUND_EXTENSIONS),
            ["sound/a.wav", "sound/a.ogg", "sound/a.opus"],
        )

    def test_engine_path_rejects_editor_artefacts(self) -> None:
        self.assertTrue(is_engine_path("models/players/x/lower.md3"))
        self.assertFalse(is_engine_path("E:\\projects\\oa\\Sphere"))
        self.assertFalse(is_engine_path("models\\players\\x\\Material"))
        self.assertFalse(is_engine_path(""))


if __name__ == "__main__":
    unittest.main()
