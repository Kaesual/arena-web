# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import base64
import copy
import gc
import hashlib
import json
import socket
import struct
import sys
import tempfile
import threading
import unittest
import warnings
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_acceptance import (  # noqa: E402
    ACCEPTED_ENGINE_NOTES,
    ENGINE_DEFECT_PATTERNS,
    AcceptanceError,
    Check,
    Expectations,
    RunResult,
    _score,
    bots_from_engine_log,
    classify_engine_log,
    compare_runs,
    pinned_browser_version,
    png_pixel_statistics,
    strip_color_codes,
)
from arena_runtime import (  # noqa: E402
    SYSTEMINFO_FIXED_ALLOWANCE,
    ArenaRuntimeError,
    check_systeminfo_budget,
    content_archive_names,
    expected_engine_arguments,
    load_profile,
    manifest_index,
    projected_systeminfo_size,
    served_files,
    systeminfo_cvars,
    systeminfo_fixed_floor,
    stage,
    verify_staged,
)
from browser_session import (  # noqa: E402
    WEBSOCKET_GUID,
    BrowserSessionError,
    DevToolsSession,
    WebSocketClient,
    wait_until,
)

# --------------------------------------------------------------------------
# A synthetic repository, so the fail-closed rules can be exercised without
# touching the committed profile, manifests or recipe.
# --------------------------------------------------------------------------

ENGINE_FILES = {
    "ioquake3.js": b"export default function ioquake3() {}\n",
    "ioquake3.wasm": b"\x00asm\x01\x00\x00\x00",
    "baseq3/vm/cgame.qvm": b"cgame-bytes",
    "baseq3/vm/qagame.qvm": b"qagame-bytes",
    "baseq3/vm/ui.qvm": b"ui-bytes",
    "ioquake3.html": b"<!-- upstream shell -->",
    "ioquake3-config.json": b"{}",
    "missionpack/vm/ui.qvm": b"missionpack-ui",
    "baseq3/stray.pk3": b"PK\x03\x04 game data in an engine build",
}
BASE_PACK = "baseq3/arena-web-ffa-base.pk3"
MAP_PACK = "baseq3/arena-web-ffa-map-oa_pvomit.pk3"
CONTENT_FILES = {
    BASE_PACK: b"PK\x03\x04 pretend base",
    MAP_PACK: b"PK\x03\x04 pretend map",
    "baseq3/other.pk3": b"PK\x03\x04 another pack",
    "baseq3/vm/stray.qvm": b"gamecode in a content pack",
}


# The content manifest additionally records, per archive, the selection key a
# rotation is expressed in and what one costs. Synthetic figures: the rules
# under test are presence and agreement, not the measurements themselves.
CONTENT_RECORDS = {
    BASE_PACK: {"uncompressedSize": 41},
    MAP_PACK: {
        "map": "oa_pvomit",
        "peakHunkBytes": 31441576,
        "uncompressedSize": 40,
    },
}


def _manifest(
    files: dict[str, bytes], inputs: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    manifest = {
        "artifacts": [
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                **CONTENT_RECORDS.get(path, {}),
            }
            for path, data in sorted(files.items())
        ],
        "digestAlgorithm": "sha256",
        "formatVersion": 1,
    }
    if inputs is not None:
        manifest["inputs"] = inputs
    return manifest


def _served(path: str) -> str:
    """The served name an artifact must carry: its own digest, as the gate."""
    digest = hashlib.sha256(CONTENT_FILES[path]).hexdigest()[:16]
    name = path.rsplit("/", 1)[-1]
    stem, dot, suffix = name.partition(".")
    return f"content/baseq3/{stem}-{digest}{dot}{suffix}"


def _profile() -> dict[str, Any]:
    return {
        "$comment": ["synthetic"],
        "formatVersion": 1,
        "package": "arena-web-ffa",
        "basegame": "arena",
        "map": "oa_pvomit",
        "playerModel": "skelebot/default",
        "bots": [{"name": "Skelebot", "skill": 3}, {"name": "Rai", "skill": 3}],
        "cvars": {
            "bot_enable": "1",
            "com_basegame": "arena",
            "fraglimit": "15",
            "g_gametype": "0",
            "headmodel": "skelebot/default",
            "model": "skelebot/default",
            "net_enabled": "0",
            "r_allowResize": "1",
            "r_fullscreen": "0",
            "sv_maxclients": "8",
            "sv_pure": "0",
        },
        "cvarNotes": {
            name: "note"
            for name in (
                "bot_enable",
                "com_basegame",
                "fraglimit",
                "g_gametype",
                "headmodel",
                "model",
                "net_enabled",
                "r_allowResize",
                "r_fullscreen",
                "sv_maxclients",
                "sv_pure",
            )
        },
        "readyMarkers": {
            "serverSpawned": "Server: oa_pvomit",
            "clientGameLoaded": "CL_InitCGame:",
            "clientEnteredGame": "entered the game",
        },
        "readyMarkerNotes": {
            "serverSpawned": "sv_init.c",
            "clientGameLoaded": "cl_cgame.c",
            "clientEnteredGame": "g_client.c",
        },
        "manifests": {
            "content": "provenance/arena-web-ffa-content-manifest.json",
            "engine": "manifests/browser-client.json",
        },
        "configFiles": [
            {
                "source": "default.cfg",
                "served": "default.cfg",
                "fsPath": "/arena/default.cfg",
            }
        ],
        "artifacts": [
            {
                "manifest": "engine",
                "path": "ioquake3.js",
                "served": "engine/ioquake3.js",
                "role": "module-script",
            },
            {
                "manifest": "engine",
                "path": "ioquake3.wasm",
                "served": "engine/ioquake3.wasm",
                "role": "module-wasm",
            },
            {
                "manifest": "engine",
                "path": "baseq3/vm/cgame.qvm",
                "served": "engine/baseq3/vm/cgame.qvm",
                "role": "filesystem",
                "fsPath": "/arena/vm/cgame.qvm",
            },
            {
                "manifest": "content",
                "path": BASE_PACK,
                "served": _served(BASE_PACK),
                "role": "filesystem",
                "fsPath": "/arena/arena-web-ffa-base.pk3",
            },
            {
                "manifest": "content",
                "path": MAP_PACK,
                "served": _served(MAP_PACK),
                "role": "filesystem",
                "fsPath": "/arena/arena-web-ffa-map-oa_pvomit.pk3",
            },
        ],
        "engineArguments": [],
    }


def _fragment(map_name: str = "oa_pvomit", **arena) -> dict[str, Any]:
    definition = {
        "bots": "Skelebot Rai Sly",
        "fraglimit": "15",
        "longname": "Projectile Vomit",
        "map": map_name,
        "type": "ffa",
    }
    definition.update(arena)
    return {
        "map": map_name,
        "arena": definition,
        "acceptedUnresolved": [],
        "generatedMembers": ["NOTICE-arena-web.txt"],
    }


def _recipe() -> dict[str, Any]:
    return {
        "formatVersion": 2,
        "basePackPath": BASE_PACK,
        "mapPackTemplate": "baseq3/arena-web-ffa-map-{map}.pk3",
        "package": {"id": "arena-web-ffa", "name": "synthetic"},
        "profile": {
            "bots": [
                {
                    "aifile": "bots/skelebot_c.c",
                    "model": "skelebot/default",
                    "name": "Skelebot",
                },
                {"aifile": "bots/rai_c.c", "model": "skelebot/default", "name": "Rai"},
                {"aifile": "bots/sly_c.c", "model": "skelebot/default", "name": "Sly"},
            ],
            "playerModels": ["skelebot/default"],
        },
    }


# The two things `arena_runtime` reads out of the pinned engine tree: the
# CS_SYSTEMINFO buffer size and the cvars that share it. Both are stubbed rather
# than mocked, so the gates derived from them can be moved in a test and
# observed to bite.
SYSTEMINFO_HEADER = "ioq3/code/qcommon/q_shared.h"
SYSTEMINFO_SOURCE = "ioq3/code/server/sv_init.c"

# The registrations of the pinned tree, in both shapes the enumeration reads,
# plus one line that names the flag without registering anything.
SYSTEMINFO_CVARS = (
    "sv_cheats",
    "sv_serverid",
    "sv_pure",
    "sv_voipProtocol",
    "sv_paks",
    "sv_pakNames",
    "sv_referencedPaks",
    "sv_referencedPakNames",
    "timescale",
    "fs_game",
)


def _engine_header(big_info_string: int = 8192) -> str:
    return (
        "#define\tMAX_INFO_STRING\t\t1024\n"
        f"#define\tBIG_INFO_STRING\t\t{big_info_string}"
        "  // used for system info key only\n"
    )


def _engine_source(extra: tuple[str, ...] = ()) -> str:
    lines = ["\tif ( cvar_modifiedFlags & CVAR_SYSTEMINFO ) {\n"]
    for name in SYSTEMINFO_CVARS:
        lines.append(f'\tCvar_Get ("{name}", "", CVAR_SYSTEMINFO | CVAR_ROM );\n')
    for name in extra:
        lines.append(f'\t{{ &{name}, "{name}", "0", CVAR_SYSTEMINFO, 0, qfalse }},\n')
    return "".join(lines)


class SyntheticRepository:
    """A throwaway tree with the exact files `arena_runtime` reads."""

    def __init__(self, directory: Path) -> None:
        self.root = directory
        self.profile = _profile()
        self.profile["engineArguments"] = expected_engine_arguments(self.profile)
        self.recipe = _recipe()
        self.fragments = {"oa_pvomit": _fragment()}
        self.engine_dir = directory / "engine-build"
        self.content_dir = directory / "content-build"
        self.target = directory / "build" / "arena-serve"
        self.write()

    def write(self) -> None:
        self.set_engine_header()
        self.set_engine_source()
        (self.root / "arena").mkdir(parents=True, exist_ok=True)
        (self.root / "probe").mkdir(parents=True, exist_ok=True)
        (self.root / "manifests").mkdir(parents=True, exist_ok=True)
        (self.root / "provenance").mkdir(parents=True, exist_ok=True)
        (self.root / "content").mkdir(parents=True, exist_ok=True)
        (self.root / "arena" / "index.html").write_text(
            "<!doctype html>", encoding="utf-8"
        )
        (self.root / "arena" / "loader.js").write_text("// loader", encoding="utf-8")
        (self.root / "arena" / "canvas-resize.js").write_text(
            "// resize", encoding="utf-8"
        )
        (self.root / "arena" / "host-lifecycle.js").write_text(
            "// lifecycle", encoding="utf-8"
        )
        (self.root / "arena" / "network-backend.js").write_text(
            "// backend", encoding="utf-8"
        )
        (self.root / "probe" / "relay-framing.js").write_text(
            "// framing", encoding="utf-8"
        )
        (self.root / "arena" / "relay-profile.json").write_text(
            json.dumps(
                {
                    "$comment": ["synthetic"],
                    "formatVersion": 1,
                    "mode": "relay-client",
                    "connectFamily": "-6",
                    "innerDatagramFloor": 768,
                    "fragmentSize": 704,
                    "receiveQueueDepth": 256,
                    "singleDatagramOverhead": 42,
                    "keepAliveIntervalSource": "runtime",
                    "cvars": {
                        "bot_enable": "0",
                        "cl_allowDownload": "0",
                        "cl_motd": "0",
                        "cl_voip": "0",
                        "com_basegame": "arena",
                        "com_legacyprotocol": "0",
                        "headmodel": "skelebot/default",
                        "model": "skelebot/default",
                        "net_enabled": "2",
                        "r_allowResize": "1",
                        "r_fullscreen": "0",
                        "sv_pure": "0",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "arena" / "default.cfg").write_text("// cfg\n", encoding="utf-8")
        (self.root / "arena" / "other.cfg").write_text("// other\n", encoding="utf-8")
        (self.root / "arena" / "game-profile.json").write_text(
            json.dumps(self.profile), encoding="utf-8"
        )
        (self.root / "manifests" / "browser-client.json").write_text(
            json.dumps(_manifest(ENGINE_FILES)), encoding="utf-8"
        )
        # The fragments are written first: the content manifest records each
        # one's digest, and reading a fragment is gated on that identity.
        (self.root / "content" / "maps").mkdir(parents=True, exist_ok=True)
        inputs = []
        for name, fragment in sorted(self.fragments.items()):
            path = self.root / "content" / "maps" / f"{name}.json"
            path.write_text(json.dumps(fragment), encoding="utf-8")
            inputs.append(
                {
                    "id": f"arena-web-map-{name}",
                    "identity": "sha256:"
                    + hashlib.sha256(path.read_bytes()).hexdigest(),
                    "kind": "archive",
                }
            )
        (self.root / "provenance" / "arena-web-ffa-content-manifest.json").write_text(
            json.dumps(_manifest(CONTENT_FILES, inputs)), encoding="utf-8"
        )
        (self.root / "content" / "pack-recipe.json").write_text(
            json.dumps(self.recipe), encoding="utf-8"
        )
        for name, data in ENGINE_FILES.items():
            path = self.engine_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name, data in CONTENT_FILES.items():
            path = self.content_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def set_engine_header(self, big_info_string: int = 8192) -> None:
        path = self.root / SYSTEMINFO_HEADER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_engine_header(big_info_string), encoding="utf-8")

    def set_engine_source(self, extra: tuple[str, ...] = ()) -> None:
        path = self.root / SYSTEMINFO_SOURCE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_engine_source(extra), encoding="utf-8")

    def set_profile(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        (self.root / "arena" / "game-profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )

    def mutate(self, change) -> None:
        profile = copy.deepcopy(self.profile)
        change(profile)
        if "engineArguments" in profile and profile[
            "engineArguments"
        ] == expected_engine_arguments(self.profile):
            try:
                profile["engineArguments"] = expected_engine_arguments(profile)
            except (KeyError, TypeError):
                pass
        self.set_profile(profile)

    def stage(self) -> dict[str, Any]:
        return stage(
            self.root,
            self.target,
            engine_dir=self.engine_dir,
            content_dir=self.content_dir,
        )


class SyntheticRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.repository = SyntheticRepository(Path(self._directory.name))

    def refuses(self, change) -> str:
        self.repository.mutate(change)
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.repository.set_profile(_profile() | {"engineArguments": []})
        return str(caught.exception)


class ProfileValidationTest(SyntheticRepositoryTest):
    def test_the_synthetic_profile_is_accepted(self) -> None:
        profile = load_profile(self.repository.root)
        self.assertEqual(profile["map"], "oa_pvomit")
        self.assertIn("_manifests", profile)

    def test_an_unknown_key_is_refused(self) -> None:
        self.assertIn(
            "unexpected key set", self.refuses(lambda p: p.update({"extra": 1}))
        )

    def test_a_missing_key_is_refused(self) -> None:
        self.assertIn("unexpected key set", self.refuses(lambda p: p.pop("basegame")))

    def test_a_wrong_format_version_is_refused(self) -> None:
        self.assertIn(
            "formatVersion", self.refuses(lambda p: p.update({"formatVersion": 2}))
        )

    def test_a_map_name_with_a_path_separator_is_refused(self) -> None:
        self.assertIn(
            "map", self.refuses(lambda p: p.update({"map": "maps/oa_pvomit"}))
        )

    def test_a_profile_without_bots_is_refused(self) -> None:
        self.assertIn(
            "at least one bot", self.refuses(lambda p: p.update({"bots": []}))
        )

    def test_a_duplicate_bot_is_refused(self) -> None:
        self.assertIn(
            "twice",
            self.refuses(
                lambda p: p.update({"bots": [p["bots"][0], dict(p["bots"][0])]})
            ),
        )

    def test_an_out_of_range_bot_skill_is_refused(self) -> None:
        self.assertIn(
            "1..5",
            self.refuses(lambda p: p["bots"][0].update({"skill": 9})),
        )

    def test_a_boolean_bot_skill_is_refused(self) -> None:
        self.assertIn(
            "1..5", self.refuses(lambda p: p["bots"][0].update({"skill": True}))
        )

    def test_a_bot_the_content_pack_does_not_carry_is_refused(self) -> None:
        self.assertIn(
            "content pack packages",
            self.refuses(lambda p: p["bots"].append({"name": "Sarge", "skill": 3})),
        )

    def test_a_runtime_derived_cvar_may_not_be_committed(self) -> None:
        self.assertIn(
            "derived from the live canvas",
            self.refuses(lambda p: p["cvars"].update({"r_mode": "-2"})),
        )

    def test_a_cvar_value_with_whitespace_is_refused(self) -> None:
        self.assertIn(
            "without whitespace",
            self.refuses(lambda p: p["cvars"].update({"sv_pure": "0 1"})),
        )

    def test_an_undocumented_cvar_is_refused(self) -> None:
        self.assertIn(
            "cvarNotes",
            self.refuses(lambda p: p["cvars"].update({"r_gamma": "1"})),
        )

    def test_a_team_gametype_is_refused(self) -> None:
        self.assertIn(
            "GT_FFA",
            self.refuses(lambda p: p["cvars"].update({"g_gametype": "3"})),
        )

    def test_enabling_networking_is_refused(self) -> None:
        self.assertIn(
            "offline",
            self.refuses(lambda p: p["cvars"].update({"net_enabled": "1"})),
        )

    def test_disabling_runtime_resize_is_refused(self) -> None:
        self.assertIn(
            "runtime resize",
            self.refuses(lambda p: p["cvars"].update({"r_allowResize": "0"})),
        )

    def test_sdl_fullscreen_is_refused_because_html_owns_it(self) -> None:
        self.assertIn(
            "HTML stage",
            self.refuses(lambda p: p["cvars"].update({"r_fullscreen": "1"})),
        )

    def test_a_player_model_that_disagrees_with_the_model_cvar_is_refused(self) -> None:
        self.assertIn(
            "profile.playerModel",
            self.refuses(lambda p: p["cvars"].update({"model": "sarge/default"})),
        )

    def test_a_player_model_the_pack_does_not_carry_is_refused(self) -> None:
        def change(profile: dict[str, Any]) -> None:
            profile["playerModel"] = "sarge/default"
            profile["cvars"]["model"] = "sarge/default"
            profile["cvars"]["headmodel"] = "sarge/default"

        self.assertIn("player presentation", self.refuses(change))

    def test_a_frag_limit_that_disagrees_with_the_recipe_is_refused(self) -> None:
        self.assertIn(
            "frag limit",
            self.refuses(lambda p: p["cvars"].update({"fraglimit": "30"})),
        )

    def test_a_marker_that_is_not_the_engine_string_is_refused(self) -> None:
        self.assertIn(
            "sv_init.c",
            self.refuses(
                lambda p: p["readyMarkers"].update({"serverSpawned": "Map: oa_pvomit"})
            ),
        )

    def test_an_unknown_ready_marker_is_refused(self) -> None:
        self.assertIn(
            "readyMarkers",
            self.refuses(lambda p: p["readyMarkers"].update({"other": "x"})),
        )

    def test_a_map_the_content_recipe_does_not_assemble_is_refused(self) -> None:
        def change(profile: dict[str, Any]) -> None:
            profile["map"] = "q3dm6ish"
            profile["readyMarkers"]["serverSpawned"] = "Server: q3dm6ish"

        self.assertIn("records no fragment for map", self.refuses(change))

    def test_engine_arguments_that_are_not_the_derivation_are_refused(self) -> None:
        message = self.refuses(
            lambda p: p.update({"engineArguments": ["+map", "oa_pvomit"]})
        )
        self.assertIn("not the derivation", message)

    def test_engine_arguments_place_the_map_before_every_bot(self) -> None:
        arguments = expected_engine_arguments(self.repository.profile)
        self.assertLess(arguments.index("+map"), arguments.index("+addbot"))

    def test_engine_arguments_use_the_upstream_bot_delay_cadence(self) -> None:
        arguments = expected_engine_arguments(self.repository.profile)
        delays = [
            arguments[index + 4]
            for index, token in enumerate(arguments)
            if token == "+addbot"
        ]
        self.assertEqual(delays, ["2000", "3500"])

    def test_engine_arguments_are_sorted_by_cvar_name(self) -> None:
        arguments = expected_engine_arguments(self.repository.profile)
        names = [
            arguments[index + 1]
            for index, token in enumerate(arguments)
            if token == "+set"
        ]
        self.assertEqual(names, sorted(names))


class MultiMapRecipeTest(SyntheticRepositoryTest):
    """A pack may carry several maps; a loader profile starts exactly one."""

    def setUp(self) -> None:
        # `_artifacts` publishes further archives by adding to the two module
        # level tables, and a leak out of this class would leave later tests
        # with a content manifest that declares maps their profile does not
        # serve — which is now a refusal rather than something tolerated.
        files, records = dict(CONTENT_FILES), copy.deepcopy(CONTENT_RECORDS)
        self.addCleanup(lambda: CONTENT_FILES.update(files) or None)
        self.addCleanup(
            lambda: [
                CONTENT_FILES.pop(key)
                for key in list(CONTENT_FILES)
                if key not in files
            ]
        )
        self.addCleanup(
            lambda: [
                CONTENT_RECORDS.pop(key)
                for key in list(CONTENT_RECORDS)
                if key not in records
            ]
        )
        super().setUp()

    def _fragments(self, arenas: list[dict[str, Any]]) -> None:
        self.repository.fragments = {
            entry["map"]: _fragment(entry["map"], **entry) for entry in arenas
        }
        self.repository.write()

    def _artifacts(self, maps: list[str]) -> None:
        """The profile declares every published archive, so it grows with the set."""
        profile = self.repository.profile
        profile["artifacts"] = [
            artifact
            for artifact in profile["artifacts"]
            if artifact["manifest"] != "content" or artifact["path"] == BASE_PACK
        ]
        for name in maps:
            path = f"baseq3/arena-web-ffa-map-{name}.pk3"
            CONTENT_FILES.setdefault(path, f"PK\x03\x04 {name}".encode())
            CONTENT_RECORDS.setdefault(
                path,
                {"map": name, "peakHunkBytes": 31441576, "uncompressedSize": 40},
            )
            profile["artifacts"].append(
                {
                    "manifest": "content",
                    "path": path,
                    "served": _served(path),
                    "role": "filesystem",
                    "fsPath": f"/arena/arena-web-ffa-map-{name}.pk3",
                }
            )
        self.repository.write()

    def test_a_profile_starting_one_of_several_packaged_maps_is_accepted(self) -> None:
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa", "fraglimit": "15"},
            ]
        )
        self._artifacts(["oa_pvomit", "oa_shine"])
        self.assertEqual(load_profile(self.repository.root)["map"], "oa_pvomit")

    def test_a_map_outside_the_packaged_set_is_refused(self) -> None:
        self._fragments([{"map": "oa_shine", "type": "ffa", "fraglimit": "15"}])
        self._artifacts(["oa_shine"])
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.assertIn("records no fragment for map", str(caught.exception))

    def test_the_started_arena_is_the_one_that_must_be_ffa(self) -> None:
        self._fragments(
            [
                {"map": "oa_shine", "type": "tourney", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa", "fraglimit": "15"},
            ]
        )
        self._artifacts(["oa_pvomit", "oa_shine"])
        load_profile(self.repository.root)
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "tourney", "fraglimit": "15"},
            ]
        )
        self._artifacts(["oa_pvomit", "oa_shine"])
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.assertIn("only starts an FFA arena", str(caught.exception))

    def test_a_fragment_that_is_not_the_one_the_manifest_records_is_refused(self) -> None:
        """Reading a fragment is gated on the identity the content manifest
        records for it, so content cannot join the build without joining the
        release identity."""
        path = self.repository.root / "content" / "maps" / "oa_pvomit.json"
        fragment = json.loads(path.read_text())
        fragment["arena"]["fraglimit"] = "99"
        path.write_text(json.dumps(fragment), encoding="utf-8")
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.assertIn("content manifest records", str(caught.exception))


class ArtifactAllowlistTest(SyntheticRepositoryTest):
    def _artifact(self, **overrides: Any) -> dict[str, Any]:
        artifact = {
            "manifest": "engine",
            "path": "ioquake3.html",
            "served": "engine/ioquake3.html",
            "role": "filesystem",
            "fsPath": "/arena/ioquake3.html",
        }
        artifact.update(overrides)
        return artifact

    def test_the_upstream_shell_may_not_be_served(self) -> None:
        self.assertIn(
            "build evidence",
            self.refuses(lambda p: p["artifacts"].append(self._artifact())),
        )

    def test_the_retail_data_configuration_may_not_be_served(self) -> None:
        self.assertIn(
            "build evidence",
            self.refuses(
                lambda p: p["artifacts"].append(
                    self._artifact(
                        path="ioquake3-config.json",
                        served="engine/ioquake3-config.json",
                        fsPath="/arena/ioquake3-config.json",
                    )
                )
            ),
        )

    def test_missionpack_output_may_not_be_served(self) -> None:
        self.assertIn(
            "off-profile",
            self.refuses(
                lambda p: p["artifacts"].append(
                    self._artifact(
                        path="missionpack/vm/ui.qvm",
                        served="engine/missionpack/vm/ui.qvm",
                        fsPath="/arena/vm/mp-ui.qvm",
                    )
                )
            ),
        )

    def test_an_artifact_the_manifest_does_not_declare_is_refused(self) -> None:
        self.assertIn(
            "does not declare it",
            self.refuses(
                lambda p: p["artifacts"].append(
                    self._artifact(
                        path="baseq3/vm/other.qvm",
                        served="engine/baseq3/vm/other.qvm",
                        fsPath="/arena/vm/other.qvm",
                    )
                )
            ),
        )

    def test_a_served_path_outside_its_manifest_prefix_is_refused(self) -> None:
        self.assertIn(
            "served",
            self.refuses(lambda p: p["artifacts"][0].update({"served": "ioquake3.js"})),
        )

    def test_two_module_scripts_are_refused(self) -> None:
        self.assertIn(
            "exactly one 'module-script'",
            self.refuses(lambda p: p["artifacts"][1].update({"role": "module-script"})),
        )

    def test_a_missing_module_wasm_is_refused(self) -> None:
        self.assertIn(
            "exactly one 'module-wasm'",
            self.refuses(lambda p: p["artifacts"].pop(1)),
        )

    def test_the_retail_game_directory_is_refused(self) -> None:
        def change(profile):
            profile["basegame"] = "baseq3"
            profile["cvars"]["com_basegame"] = "baseq3"
            for artifact in profile["artifacts"]:
                if "fsPath" in artifact:
                    artifact["fsPath"] = artifact["fsPath"].replace(
                        "/arena/", "/baseq3/"
                    )
            profile["configFiles"][0]["fsPath"] = "/baseq3/default.cfg"

        self.assertIn("FS_CheckPak0", self.refuses(change))

    def test_a_profile_without_a_default_cfg_is_refused(self) -> None:
        self.assertIn(
            "FS_InitFilesystem",
            self.refuses(
                lambda p: p["configFiles"].__setitem__(
                    0,
                    {
                        "source": "other.cfg",
                        "served": "other.cfg",
                        "fsPath": "/arena/other.cfg",
                    },
                )
            ),
        )

    def test_a_config_file_that_is_not_in_the_repository_is_refused(self) -> None:
        self.assertIn(
            "does not exist",
            self.refuses(
                lambda p: p["configFiles"].append(
                    {
                        "source": "absent.cfg",
                        "served": "absent.cfg",
                        "fsPath": "/arena/absent.cfg",
                    }
                )
            ),
        )

    def test_a_config_file_with_a_path_in_its_source_is_refused(self) -> None:
        self.assertIn(
            "plain file name",
            self.refuses(
                lambda p: p["configFiles"][0].update({"source": "../loader.js"})
            ),
        )

    def test_a_config_file_written_outside_the_game_directory_is_refused(self) -> None:
        self.assertIn(
            "must be",
            self.refuses(
                lambda p: p["configFiles"][0].update({"fsPath": "/etc/default.cfg"})
            ),
        )

    def test_an_empty_config_file_list_is_refused(self) -> None:
        self.assertIn("is empty", self.refuses(lambda p: p.update({"configFiles": []})))

    def test_a_filesystem_path_outside_the_game_directory_is_refused(self) -> None:
        self.assertIn(
            "must start with",
            self.refuses(lambda p: p["artifacts"][2].update({"fsPath": "/etc/passwd"})),
        )

    def test_a_content_pack_outside_the_recipe_is_refused(self) -> None:
        self.assertIn(
            "recipe's archives",
            self.refuses(
                lambda p: p["artifacts"][3].update(
                    {
                        "path": "baseq3/other.pk3",
                        "served": _served("baseq3/other.pk3"),
                        "fsPath": "/arena/other.pk3",
                    }
                )
            ),
        )

    def test_a_content_fs_name_that_is_not_the_manifest_name_is_refused(self) -> None:
        """PK3 load order is by the name the engine sees, and the content build
        checks cross-archive shader precedence against the manifest names, so
        the two must be one name."""
        self.assertIn(
            "the engine's PK3 load order is by this name",
            self.refuses(
                lambda p: p["artifacts"][3].update({"fsPath": "/arena/zz-base.pk3"})
            ),
        )

    def test_a_served_name_that_is_not_the_artifact_digest_is_refused(self) -> None:
        """The gate that keeps an immutable URL honest: a published name with a
        stale hash over current bytes throws in the loader, and the client has
        no recovery path."""
        self.assertIn(
            "must be 'content/baseq3/",
            self.refuses(
                lambda p: p["artifacts"][3].update(
                    {"served": "content/baseq3/arena-web-ffa-base-0000000000000000.pk3"}
                )
            ),
        )

    def test_game_data_in_the_engine_manifest_is_refused(self) -> None:
        self.assertIn(
            "may not end in '.pk3'",
            self.refuses(
                lambda p: p["artifacts"].append(
                    self._artifact(
                        path="baseq3/stray.pk3",
                        served="engine/baseq3/stray.pk3",
                        fsPath="/arena/stray.pk3",
                    )
                )
            ),
        )

    def test_gamecode_in_the_content_manifest_is_refused(self) -> None:
        self.assertIn(
            "may not end in '.qvm'",
            self.refuses(
                lambda p: p["artifacts"].append(
                    self._artifact(
                        manifest="content",
                        path="baseq3/vm/stray.qvm",
                        served="content/baseq3/vm/stray.qvm",
                        fsPath="/arena/vm/stray.qvm",
                    )
                )
            ),
        )

    def test_a_served_path_used_twice_is_refused(self) -> None:
        def change(profile):
            duplicate = dict(profile["artifacts"][2])
            duplicate["fsPath"] = "/arena/vm/copy.qvm"
            profile["artifacts"].append(duplicate)

        self.assertIn("serves 'engine/baseq3/vm/cgame.qvm' twice", self.refuses(change))

    def test_a_filesystem_destination_used_twice_is_refused(self) -> None:
        def change(profile):
            profile["artifacts"][3]["fsPath"] = profile["artifacts"][2]["fsPath"]

        self.assertIn("writes '/arena/vm/cgame.qvm' twice", self.refuses(change))

    def test_a_config_file_colliding_with_the_loader_is_refused(self) -> None:
        self.assertIn(
            "serves 'loader.js' twice",
            self.refuses(
                lambda p: p["configFiles"].append(
                    {
                        "source": "loader.js",
                        "served": "loader.js",
                        "fsPath": "/arena/loader.js",
                    }
                )
            ),
        )

    def test_a_config_file_declared_twice_is_refused(self) -> None:
        self.assertIn(
            "serves 'default.cfg' twice",
            self.refuses(lambda p: p["configFiles"].append(dict(p["configFiles"][0]))),
        )

    def test_a_filesystem_artifact_without_a_destination_is_refused(self) -> None:
        self.assertIn(
            "unexpected key set",
            self.refuses(lambda p: p["artifacts"][2].pop("fsPath")),
        )


class StagingTest(SyntheticRepositoryTest):
    def test_staging_writes_exactly_the_declared_files(self) -> None:
        report = self.repository.stage()
        present = sorted(
            path.relative_to(self.repository.target).as_posix()
            for path in self.repository.target.rglob("*")
            if path.is_file()
        )
        self.assertEqual(present, report["servedFiles"])
        self.assertEqual(
            present,
            [
                "arena/canvas-resize.js",
                "arena/host-lifecycle.js",
                "arena/network-backend.js",
                "arena/relay-profile.json",
                _served(BASE_PACK),
                _served(MAP_PACK),
                "default.cfg",
                "engine/baseq3/vm/cgame.qvm",
                "engine/ioquake3.js",
                "engine/ioquake3.wasm",
                "game-profile.json",
                "index.html",
                "loader.js",
                "manifests/browser-client.json",
                "probe/relay-framing.js",
                "provenance/arena-web-ffa-content-manifest.json",
            ],
        )

    def test_staging_reports_the_verified_artifact_bytes(self) -> None:
        report = self.repository.stage()
        self.assertEqual(
            report["totalArtifactBytes"],
            sum(
                len(ENGINE_FILES[name])
                for name in ("ioquake3.js", "ioquake3.wasm", "baseq3/vm/cgame.qvm")
            )
            + len(CONTENT_FILES[BASE_PACK])
            + len(CONTENT_FILES[MAP_PACK]),
        )

    def test_staging_is_idempotent(self) -> None:
        first = self.repository.stage()
        second = self.repository.stage()
        self.assertEqual(first, second)

    def test_an_engine_artifact_that_is_not_the_committed_one_is_refused(self) -> None:
        (self.repository.engine_dir / "ioquake3.wasm").write_bytes(b"\x00asm tampered")
        with self.assertRaises(ArenaRuntimeError) as caught:
            self.repository.stage()
        self.assertIn("is not the committed engine artifact", str(caught.exception))

    def test_a_content_artifact_that_is_not_the_committed_one_is_refused(self) -> None:
        (self.repository.content_dir / BASE_PACK).write_bytes(b"other")
        with self.assertRaises(ArenaRuntimeError) as caught:
            self.repository.stage()
        self.assertIn("is not the committed content artifact", str(caught.exception))

    def test_a_missing_build_output_is_refused(self) -> None:
        (self.repository.engine_dir / "ioquake3.js").unlink()
        with self.assertRaises(ArenaRuntimeError) as caught:
            self.repository.stage()
        self.assertIn("build it first", str(caught.exception))

    def test_a_missing_build_directory_is_refused(self) -> None:
        with self.assertRaises(ArenaRuntimeError) as caught:
            stage(
                self.repository.root,
                self.repository.target,
                engine_dir=self.repository.root / "absent",
                content_dir=self.repository.content_dir,
            )
        self.assertIn("does not exist", str(caught.exception))

    def test_an_extra_file_in_the_staged_tree_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "extra.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("does not declare", str(caught.exception))

    def test_a_missing_file_in_the_staged_tree_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "loader.js").unlink()
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("missing declared files", str(caught.exception))

    def test_a_modified_staged_artifact_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "engine/ioquake3.wasm").write_bytes(b"\x00asm x")
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("committed manifest identity", str(caught.exception))

    def test_a_modified_staged_loader_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "loader.js").write_text(
            "// tampered", encoding="utf-8"
        )
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("differs from", str(caught.exception))

    def test_a_symlinked_directory_in_the_staged_tree_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "mirror").symlink_to(
            self.repository.target / "engine", target_is_directory=True
        )
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("symlinked directory", str(caught.exception))

    def test_a_symlink_in_the_staged_tree_is_refused(self) -> None:
        self.repository.stage()
        (self.repository.target / "link.js").symlink_to(
            self.repository.target / "loader.js"
        )
        with self.assertRaises(ArenaRuntimeError) as caught:
            verify_staged(self.repository.root, self.repository.target)
        self.assertIn("not a regular file", str(caught.exception))


class ManifestIndexTest(unittest.TestCase):
    def test_a_non_sha256_manifest_is_refused(self) -> None:
        manifest = _manifest(CONTENT_FILES) | {"digestAlgorithm": "sha1"}
        with self.assertRaises(ArenaRuntimeError):
            manifest_index(manifest, "manifest")

    def test_a_malformed_digest_is_refused(self) -> None:
        manifest = _manifest(CONTENT_FILES)
        manifest["artifacts"][0]["sha256"] = "not-a-digest"
        with self.assertRaises(ArenaRuntimeError):
            manifest_index(manifest, "manifest")

    def test_a_negative_size_is_refused(self) -> None:
        manifest = _manifest(CONTENT_FILES)
        manifest["artifacts"][0]["size"] = -1
        with self.assertRaises(ArenaRuntimeError):
            manifest_index(manifest, "manifest")

    def test_a_duplicate_path_is_refused(self) -> None:
        manifest = _manifest(CONTENT_FILES)
        manifest["artifacts"].append(dict(manifest["artifacts"][0]))
        with self.assertRaises(ArenaRuntimeError):
            manifest_index(manifest, "manifest")

    def test_an_empty_manifest_is_refused(self) -> None:
        with self.assertRaises(ArenaRuntimeError):
            manifest_index({"digestAlgorithm": "sha256", "artifacts": []}, "manifest")


class SysteminfoBudgetTest(SyntheticRepositoryTest):
    """The published archive set against the engine's own CS_SYSTEMINFO bound.

    `Info_SetValueForKey_Big` does not truncate and does not fail on overflow:
    it prints one line and returns, leaving whichever key first did not fit out
    of systeminfo — and `Cvar_InfoString_Big` walks `cvar_vars` in list order,
    so it is not even predictably a pak key. Nothing downstream reports it.
    """

    def test_the_projection_is_the_string_the_engine_would_assemble(self) -> None:
        names = ["arena-web-ffa-base", "arena-web-ffa-map-oa_pvomit"]
        expected = (
            SYSTEMINFO_FIXED_ALLOWANCE
            + 1
            + len("sv_referencedPakNames")
            + 1
            + len("arena/arena-web-ffa-base arena/arena-web-ffa-map-oa_pvomit")
            + 1
            + len("sv_referencedPaks")
            + 1
            + 2 * (len("-2147483648") + 1)
        )
        self.assertEqual(projected_systeminfo_size("arena", names), expected)

    def test_it_grows_with_the_set_and_a_rotation_is_a_subset(self) -> None:
        base = ["arena-web-ffa-base"]
        one = projected_systeminfo_size("arena", base + ["arena-web-ffa-map-a"])
        two = projected_systeminfo_size(
            "arena", base + ["arena-web-ffa-map-a", "arena-web-ffa-map-b"]
        )
        self.assertLess(one, two)

    def test_the_committed_synthetic_set_fits(self) -> None:
        profile = load_profile(self.repository.root)
        self.assertEqual(
            content_archive_names(profile),
            ["arena-web-ffa-base", "arena-web-ffa-map-oa_pvomit"],
        )
        self.assertLess(check_systeminfo_budget(self.repository.root, profile), 8192)

    def test_the_fixed_allowance_covers_the_pinned_cvars(self) -> None:
        """The one term of the bound that is a measurement rather than a
        derivation is checked against the sources it was measured from."""
        self.assertEqual(
            systeminfo_cvars(self.repository.root / "ioq3"), sorted(SYSTEMINFO_CVARS)
        )
        floor = systeminfo_fixed_floor(self.repository.root / "ioq3")
        self.assertLessEqual(floor, SYSTEMINFO_FIXED_ALLOWANCE)

    def test_more_systeminfo_cvars_than_the_allowance_covers_is_refused(self) -> None:
        """A gamecode or engine that registers more of them has to fit the
        allowance or say so — it may not quietly spend the pak headroom."""
        extra = tuple(f"g_padding_cvar_{index:02d}" for index in range(20))
        self.repository.set_engine_source(extra)
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.assertIn("and the projection allows", str(caught.exception))
        self.repository.set_engine_source()
        load_profile(self.repository.root)

    def test_sources_that_register_none_are_refused(self) -> None:
        (self.repository.root / SYSTEMINFO_SOURCE).write_text(
            "int nothing_here = 0;\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ArenaRuntimeError, "register no non-pak"):
            load_profile(self.repository.root)
        self.repository.set_engine_source()

    def test_a_set_that_would_overflow_is_refused(self) -> None:
        """The gate's own gate: shrink the bound the engine declares and the
        set that fits today has to stop being accepted."""
        self.repository.set_engine_header(600)
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.assertIn("BIG_INFO_STRING of 600", str(caught.exception))
        self.repository.set_engine_header()
        load_profile(self.repository.root)

    def test_an_unreadable_engine_bound_is_refused_rather_than_skipped(self) -> None:
        (self.repository.root / SYSTEMINFO_HEADER).write_text(
            "#define MAX_INFO_STRING 1024\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ArenaRuntimeError, "no longer defines"):
            load_profile(self.repository.root)
        (self.repository.root / SYSTEMINFO_HEADER).unlink()
        with self.assertRaisesRegex(ArenaRuntimeError, "cannot read"):
            load_profile(self.repository.root)


class ContentRecordTest(SyntheticRepositoryTest):
    """The selection key and the cost figures the content manifest carries."""

    def _manifest_path(self) -> Path:
        return (
            self.repository.root
            / "provenance"
            / "arena-web-ffa-content-manifest.json"
        )

    def _refuses(self, change) -> str:
        path = self._manifest_path()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        change(manifest)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ArenaRuntimeError) as caught:
            load_profile(self.repository.root)
        self.repository.write()
        return str(caught.exception)

    def _artifact(self, manifest: dict[str, Any], path: str) -> dict[str, Any]:
        return next(item for item in manifest["artifacts"] if item["path"] == path)

    def test_the_synthetic_records_are_accepted(self) -> None:
        profile = load_profile(self.repository.root)
        entry = profile["_manifests"]["content"][MAP_PACK]
        self.assertEqual(entry["map"], "oa_pvomit")
        self.assertEqual(entry["peakHunkBytes"], 31441576)
        self.assertEqual(entry["uncompressedSize"], 40)

    def test_a_map_archive_without_its_peak_hunk_is_refused(self) -> None:
        self.assertIn(
            "peakHunkBytes",
            self._refuses(
                lambda m: self._artifact(m, MAP_PACK).pop("peakHunkBytes")
            ),
        )

    def test_an_archive_without_its_extracted_size_is_refused(self) -> None:
        self.assertIn(
            "uncompressedSize",
            self._refuses(
                lambda m: self._artifact(m, BASE_PACK).pop("uncompressedSize")
            ),
        )

    def test_a_base_that_claims_a_map_is_refused(self) -> None:
        self.assertIn(
            "carries no map",
            self._refuses(
                lambda m: self._artifact(m, BASE_PACK).update({"map": "oa_pvomit"})
            ),
        )

    def test_a_map_no_committed_fragment_declares_is_refused(self) -> None:
        self.assertIn(
            "committed map fragments",
            self._refuses(
                lambda m: self._artifact(m, MAP_PACK).update({"map": "invented"})
            ),
        )

    def test_an_engine_artifact_borrowing_the_vocabulary_is_refused(self) -> None:
        path = self.repository.root / "manifests" / "browser-client.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["map"] = "oa_pvomit"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ArenaRuntimeError, "content-only records"):
            load_profile(self.repository.root)

    def test_an_unpublished_archive_claiming_a_map_is_refused(self) -> None:
        self.assertIn(
            "not one of the archives this release publishes",
            self._refuses(
                lambda m: self._artifact(m, "baseq3/other.pk3").update(
                    {"map": "oa_pvomit"}
                )
            ),
        )


class CommittedProfileTest(unittest.TestCase):
    """The real committed configuration, against the real committed manifests."""

    def setUp(self) -> None:
        self.profile = load_profile(ROOT)

    def test_it_is_valid_and_agrees_with_the_content_recipe(self) -> None:
        self.assertEqual(self.profile["map"], "oa_pvomit")
        self.assertEqual(self.profile["package"], "arena-web-ffa")

    def test_it_serves_the_engine_runtime_and_the_audited_pack_and_nothing_else(
        self,
    ) -> None:
        self.assertEqual(
            sorted(served_files(ROOT, self.profile)),
            [
                "arena/canvas-resize.js",
                "arena/host-lifecycle.js",
                "arena/network-backend.js",
                "arena/relay-profile.json",
                *sorted(
                    artifact["served"]
                    for artifact in self.profile["artifacts"]
                    if artifact["manifest"] == "content"
                ),
                "default.cfg",
                "engine/baseq3/vm/cgame.qvm",
                "engine/baseq3/vm/qagame.qvm",
                "engine/baseq3/vm/ui.qvm",
                "engine/ioquake3.js",
                "engine/ioquake3.wasm",
                "game-profile.json",
                "index.html",
                "loader.js",
                "manifests/browser-client.json",
                "probe/relay-framing.js",
                "provenance/arena-web-ffa-content-manifest.json",
            ],
        )

    def test_every_served_artifact_carries_a_committed_identity(self) -> None:
        files = served_files(ROOT, self.profile)
        artifacts = {
            name: entry for name, entry in files.items() if entry["kind"] == "artifact"
        }
        self.assertEqual(
            len(artifacts),
            len([a for a in self.profile["artifacts"] if "manifest" in a]),
        )
        for entry in artifacts.values():
            self.assertRegex(entry["sha256"], r"\A[0-9a-f]{64}\Z")
            self.assertGreater(entry["size"], 0)

    def test_every_content_archive_is_served_under_its_own_digest(self) -> None:
        """The served name carries the artifact's own hash, and is derived
        rather than trusted: a name published with a stale hash over current
        bytes is cached `immutable` for a year and throws in the loader."""
        files = served_files(ROOT, self.profile)
        content = {
            name: entry
            for name, entry in files.items()
            if entry["kind"] == "artifact" and entry["manifest"] == "content"
        }
        published = json.loads(
            (ROOT / "provenance/arena-web-ffa-content-manifest.json").read_text()
        )["artifacts"]
        self.assertEqual(len(content), len(published))
        self.assertGreater(len(content), 1)
        for name, entry in content.items():
            self.assertIn(f"-{entry['sha256'][:16]}.pk3", name)

    def test_only_the_declared_product_runtime_sources_are_served(self) -> None:
        files = served_files(ROOT, self.profile)
        loader = sorted(
            name for name, entry in files.items() if entry["kind"] == "loader"
        )
        self.assertEqual(
            loader,
            [
                "arena/canvas-resize.js",
                "arena/host-lifecycle.js",
                "arena/network-backend.js",
                "arena/relay-profile.json",
                "index.html",
                "loader.js",
                "probe/relay-framing.js",
            ],
        )

    def test_the_loader_does_not_reuse_the_upstream_emscripten_shell(self) -> None:
        page = (ROOT / "arena/index.html").read_text(encoding="utf-8")
        upstream = (ROOT / "ioq3/code/web/client.html.in").read_text(encoding="utf-8")
        for marker in (
            "EMSCRIPTEN_PRELOAD_FILE",
            "setup-ioq3-filesystem",
            "configFilename",
        ):
            self.assertIn(marker, upstream)
            self.assertNotIn(marker, page)

    def test_the_canvas_uses_the_element_id_sdl_addresses(self) -> None:
        # SDL2's Emscripten video driver hard-codes the selector "#canvas".
        self.assertIn(
            'id="canvas"', (ROOT / "arena/index.html").read_text(encoding="utf-8")
        )


class EngineLogClassificationTest(unittest.TestCase):
    def test_a_missing_image_is_a_missing_asset(self) -> None:
        found = classify_engine_log(
            ["WARNING: R_FindImageFile could not find 'textures/x' in shader 'y'"]
        )
        self.assertEqual(len(found["missing-asset"]), 1)

    def test_a_missing_sound_is_a_missing_asset(self) -> None:
        found = classify_engine_log(
            ["WARNING: could not find sound/x.wav - using default"]
        )
        self.assertEqual(len(found["missing-asset"]), 1)

    def test_a_bad_qvm_header_is_a_qvm_rejection(self) -> None:
        found = classify_engine_log(["Warning: vm/cgame.qvm has bad header"])
        self.assertEqual(len(found["qvm-rejection"]), 1)

    def test_an_unopenable_qvm_is_a_qvm_rejection(self) -> None:
        found = classify_engine_log(["Warning: Couldn't open VM file vm/ui.qvm"])
        self.assertEqual(len(found["qvm-rejection"]), 1)

    def test_a_gl_error_is_a_renderer_fatal(self) -> None:
        found = classify_engine_log(
            ["GL_CheckErrors: GL_INVALID_ENUM in tr_main.c at line 1"]
        )
        self.assertEqual(len(found["renderer-fatal"]), 1)

    def test_a_com_error_is_an_engine_error(self) -> None:
        found = classify_engine_log(["ERROR: Couldn't load maps/oa_pvomit.bsp"])
        self.assertEqual(len(found["engine-error"]), 1)

    def test_ordinary_output_is_not_a_defect(self) -> None:
        found = classify_engine_log(
            [
                "Server: oa_pvomit",
                "CL_InitCGame: 1.20 seconds",
                "Skelebot entered the game",
                "Architecture doesn't have a bytecode compiler, using interpreter",
            ]
        )
        self.assertEqual(sum(len(lines) for lines in found.values()), 0)

    def test_the_accepted_upstream_note_is_recorded_and_not_a_defect(self) -> None:
        found = classify_engine_log(
            ["^3WARNING: Failed to open sound music/sonic5.wav!"]
        )
        self.assertEqual(len(found["accepted-note"]), 1)
        self.assertIn("dangling upstream reference", found["accepted-note"][0])
        self.assertEqual(len(found["missing-asset"]), 0)

    def test_every_engine_registered_image_gap_is_accepted_with_a_reason(self) -> None:
        found = classify_engine_log(
            [
                "^3WARNING: R_FindImageFile could not find "
                "'gfx/fx/flares/blur.tga' in shader 'flareShader'",
                "^3WARNING: R_FindImageFile could not find "
                "'textures/flares/flarey.tga' in shader 'sun'",
                "^3WARNING: R_FindImageFile could not find "
                "'textures/sfx/logo256.tga' in shader 'console'",
            ]
        )
        self.assertEqual(len(found["accepted-note"]), 3)
        self.assertEqual(len(found["missing-asset"]), 0)

    def test_the_missing_taunt_of_the_packaged_model_is_accepted(self) -> None:
        found = classify_engine_log(
            [
                "^3WARNING: Failed to load sound sound/player/skelebot/taunt.wav!",
                "^3WARNING: Using default sound for sound/player/skelebot/taunt.wav",
                "^3WARNING: Failed to load sound sound/player/sarge/taunt.wav!",
            ]
        )
        self.assertEqual(len(found["accepted-note"]), 3)
        self.assertEqual(len(found["missing-asset"]), 0)

    def test_another_missing_image_is_still_a_defect(self) -> None:
        found = classify_engine_log(
            [
                "^3WARNING: R_FindImageFile could not find "
                "'textures/mc-oa-dm02/wall.tga' in shader 'somewall'"
            ]
        )
        self.assertEqual(len(found["missing-asset"]), 1)
        self.assertEqual(len(found["accepted-note"]), 0)

    def test_another_missing_sound_is_still_a_defect(self) -> None:
        found = classify_engine_log(
            ["^3WARNING: Failed to load sound sound/weapons/rocket/rocklf1a.wav!"]
        )
        self.assertEqual(len(found["missing-asset"]), 1)

    def test_every_defect_class_names_its_engine_source(self) -> None:
        for name, source, pattern in ENGINE_DEFECT_PATTERNS:
            self.assertTrue(name and source and pattern.pattern)

    def test_every_acceptance_carries_a_reason(self) -> None:
        self.assertTrue(ACCEPTED_ENGINE_NOTES)
        for pattern, reason in ACCEPTED_ENGINE_NOTES:
            self.assertTrue(pattern.pattern)
            self.assertGreater(len(reason), 20)


class PinnedBrowserTest(unittest.TestCase):
    def test_the_acceptance_browser_version_comes_from_the_baseline_lock(self) -> None:
        self.assertEqual(pinned_browser_version(ROOT), "152.0.7977.64")

    def test_a_baseline_without_the_browser_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "locks").mkdir()
            (root / "locks/baseline.json").write_text(
                json.dumps({"tools": []}), encoding="utf-8"
            )
            with self.assertRaises(AcceptanceError):
                pinned_browser_version(root)


# --------------------------------------------------------------------------
# Screenshot decoding.
# --------------------------------------------------------------------------


def _png(
    width: int, height: int, rows: list[list[tuple[int, int, int]]], filter_type: int
) -> bytes:
    raw = bytearray()
    previous = bytearray(width * 3)
    for row in rows:
        line = bytearray()
        for pixel in row:
            line += bytes(pixel)
        encoded = bytearray([filter_type])
        for index, value in enumerate(line):
            left = line[index - 3] if index >= 3 else 0
            up = previous[index]
            up_left = previous[index - 3] if index >= 3 else 0
            if filter_type == 0:
                encoded.append(value)
            elif filter_type == 1:
                encoded.append((value - left) & 0xFF)
            elif filter_type == 2:
                encoded.append((value - up) & 0xFF)
            elif filter_type == 3:
                encoded.append((value - ((left + up) >> 1)) & 0xFF)
            else:
                estimate = left + up - up_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - up_left),
                )
                if distances[0] <= distances[1] and distances[0] <= distances[2]:
                    predictor = left
                elif distances[1] <= distances[2]:
                    predictor = up
                else:
                    predictor = up_left
                encoded.append((value - predictor) & 0xFF)
        raw += encoded
        previous = line

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


class ScreenshotStatisticsTest(unittest.TestCase):
    def test_a_flat_black_image_has_one_colour(self) -> None:
        rows = [[(0, 0, 0)] * 8 for _ in range(8)]
        statistics = png_pixel_statistics(_png(8, 8, rows, 0))
        self.assertEqual(statistics["distinctColours"], 1)
        self.assertEqual(statistics["meanLuminance"], 0.0)
        self.assertEqual(statistics["sampledPixels"], 64)

    def test_every_png_filter_decodes_to_the_same_pixels(self) -> None:
        rows = [
            [
                ((column * 7 + row * 13) % 256, (column * 3) % 256, (row * 5) % 256)
                for column in range(16)
            ]
            for row in range(16)
        ]
        reference = png_pixel_statistics(_png(16, 16, rows, 0))
        for filter_type in (1, 2, 3, 4):
            with self.subTest(filter=filter_type):
                statistics = png_pixel_statistics(_png(16, 16, rows, filter_type))
                self.assertEqual(
                    statistics["distinctColours"], reference["distinctColours"]
                )
                self.assertEqual(
                    statistics["meanLuminance"], reference["meanLuminance"]
                )

    def test_sampling_every_fourth_column_reduces_the_sample_count(self) -> None:
        rows = [[(column * 16 % 256, 0, 0) for column in range(16)] for _ in range(4)]
        statistics = png_pixel_statistics(_png(16, 4, rows, 0), sample_stride=4)
        self.assertEqual(statistics["sampledPixels"], 16)

    def test_a_non_png_payload_is_refused(self) -> None:
        with self.assertRaises(AcceptanceError):
            png_pixel_statistics(b"not a png at all")

    def test_a_palette_png_is_refused(self) -> None:
        header = struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0)

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (
                struct.pack(">I", len(body))
                + kind
                + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
            )

        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")
        with self.assertRaises(AcceptanceError):
            png_pixel_statistics(data)


# --------------------------------------------------------------------------
# The DevTools transport.
# --------------------------------------------------------------------------


class FakeWebSocketServer:
    """The server half of RFC 6455, just enough to drive the client under test."""

    def __init__(self, *, wrong_accept: bool = False) -> None:
        self.wrong_accept = wrong_accept
        self.received: list[str] = []
        self.responder = None
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/test"

    def _serve(self) -> None:
        try:
            connection, _address = self._listener.accept()
        except OSError:  # pragma: no cover - closed before a client arrived
            return
        with connection:
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            key = ""
            for line in buffer.decode("latin-1").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
            accept = base64.b64encode(
                hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
            ).decode("ascii")
            if self.wrong_accept:
                accept = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
            connection.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode("ascii")
            )
            self._pump(connection, buffer)

    def _pump(self, connection: socket.socket, buffer: bytes) -> None:
        pending = bytearray(buffer.split(b"\r\n\r\n", 1)[1])

        def read(count: int) -> bytes:
            nonlocal pending
            while len(pending) < count:
                chunk = connection.recv(65536)
                if not chunk:
                    raise ConnectionError
                pending += chunk
            head = bytes(pending[:count])
            del pending[:count]
            return head

        try:
            while True:
                first, second = read(2)
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    (length,) = struct.unpack(">H", read(2))
                elif length == 127:
                    (length,) = struct.unpack(">Q", read(8))
                mask = read(4) if second & 0x80 else b""
                payload = read(length)
                if mask:
                    payload = bytes(
                        byte ^ mask[index % 4] for index, byte in enumerate(payload)
                    )
                if opcode == 0x8:
                    return
                if opcode != 0x1:
                    continue
                text = payload.decode("utf-8")
                self.received.append(text)
                if self.responder is not None:
                    for reply in self.responder(text):
                        self.send(connection, reply)
        except (ConnectionError, OSError):
            return

    @staticmethod
    def send(connection: socket.socket, payload: Any) -> None:
        final = True
        if isinstance(payload, tuple):
            if len(payload) == 3:
                opcode, body, final = payload
            else:
                opcode, body = payload
        else:
            opcode, body = 0x1, payload.encode("utf-8")
        header = bytearray([(0x80 if final else 0x00) | opcode])
        if len(body) < 126:
            header.append(len(body))
        elif len(body) < 65536:
            header.append(126)
            header += struct.pack(">H", len(body))
        else:
            header.append(127)
            header += struct.pack(">Q", len(body))
        connection.sendall(bytes(header) + body)

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:  # pragma: no cover - already closed
            pass


class WebSocketClientTest(unittest.TestCase):
    def _server(self, **arguments: Any) -> FakeWebSocketServer:
        server = FakeWebSocketServer(**arguments)
        self.addCleanup(server.close)
        return server

    def test_a_wrong_accept_key_is_refused(self) -> None:
        server = self._server(wrong_accept=True)
        with self.assertRaises(BrowserSessionError) as caught:
            WebSocketClient(server.url, timeout=5)
        self.assertIn("accept key", str(caught.exception))

    def test_a_refused_upgrade_leaves_no_open_socket(self) -> None:
        server = self._server(wrong_accept=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with self.assertRaises(BrowserSessionError):
                WebSocketClient(server.url, timeout=5)
            gc.collect()

    def test_a_non_ws_scheme_is_refused(self) -> None:
        with self.assertRaises(BrowserSessionError):
            WebSocketClient("http://127.0.0.1:1/devtools", timeout=1)

    def test_a_text_message_round_trips(self) -> None:
        server = self._server()
        server.responder = lambda text: [text.upper()]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("hello")
        self.assertEqual(client.receive_text(5), "HELLO")

    def test_a_large_message_uses_the_64_bit_length(self) -> None:
        server = self._server()
        payload = "x" * 100000
        server.responder = lambda text: [text]
        client = WebSocketClient(server.url, timeout=10)
        self.addCleanup(client.close)
        client.send_text(payload)
        self.assertEqual(client.receive_text(10), payload)
        self.assertEqual(server.received[0], payload)

    def test_a_fragmented_message_is_reassembled(self) -> None:
        server = self._server()

        def responder(_text: str) -> list[Any]:
            # A real fragmented message: the first frame clears FIN and the
            # continuation frame sets it.
            return [(0x1, b"part-one ", False), (0x0, b"part-two", True)]

        server.responder = responder
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        self.assertEqual(client.receive_text(5), "part-one part-two")

    def test_a_ping_between_two_fragments_is_answered_and_skipped(self) -> None:
        server = self._server()
        server.responder = lambda _text: [
            (0x1, b"left", False),
            (0x9, b"ping", True),
            (0x0, b"right", True),
        ]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        self.assertEqual(client.receive_text(5), "leftright")

    def test_a_continuation_without_a_start_is_refused(self) -> None:
        server = self._server()
        server.responder = lambda _text: [(0x0, b"orphan", True)]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        with self.assertRaises(BrowserSessionError) as caught:
            client.receive_text(5)
        self.assertIn("continuation frame without a start", str(caught.exception))

    def test_a_ping_is_answered_with_a_pong(self) -> None:
        server = self._server()
        server.responder = lambda _text: [(0x9, b"ping"), (0x1, b"after")]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        self.assertEqual(client.receive_text(5), "after")

    def test_a_binary_frame_is_refused(self) -> None:
        server = self._server()
        server.responder = lambda _text: [(0x2, b"\x00\x01")]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        with self.assertRaises(BrowserSessionError):
            client.receive_text(5)

    def test_a_server_close_is_reported(self) -> None:
        server = self._server()
        server.responder = lambda _text: [(0x8, b"\x03\xe8")]
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        with self.assertRaises(BrowserSessionError):
            client.receive_text(5)

    def test_no_message_at_all_times_out(self) -> None:
        server = self._server()
        server.responder = lambda _text: []
        client = WebSocketClient(server.url, timeout=5)
        self.addCleanup(client.close)
        client.send_text("go")
        with self.assertRaises(TimeoutError):
            client.receive_text(0.5)


class DevToolsSessionTest(unittest.TestCase):
    def _session(self, responder) -> DevToolsSession:
        server = FakeWebSocketServer()
        self.addCleanup(server.close)
        server.responder = responder
        session = DevToolsSession(server.url, timeout=5)
        self.addCleanup(session.close)
        self.server = server
        return session

    def test_a_call_is_correlated_with_its_answer(self) -> None:
        def responder(text: str) -> list[str]:
            message = json.loads(text)
            return [
                json.dumps(
                    {"id": message["id"], "result": {"value": message["method"]}}
                )
            ]

        session = self._session(responder)
        self.assertEqual(session.call("Page.enable")["value"], "Page.enable")

    def test_events_arriving_before_the_answer_are_kept(self) -> None:
        def responder(text: str) -> list[str]:
            message = json.loads(text)
            return [
                json.dumps({"method": "Network.requestWillBeSent", "params": {"n": 1}}),
                json.dumps({"method": "Network.requestWillBeSent", "params": {"n": 2}}),
                json.dumps({"id": message["id"], "result": {}}),
            ]

        session = self._session(responder)
        session.call("Network.enable")
        events = session.drain_events()
        self.assertEqual([event["params"]["n"] for event in events], [1, 2])
        self.assertEqual(session.drain_events(), [])

    def test_a_protocol_error_is_raised(self) -> None:
        def responder(text: str) -> list[str]:
            message = json.loads(text)
            return [
                json.dumps(
                    {"id": message["id"], "error": {"message": "no such method"}}
                )
            ]

        session = self._session(responder)
        with self.assertRaises(BrowserSessionError) as caught:
            session.call("Nope.nope")
        self.assertIn("no such method", str(caught.exception))

    def test_a_silent_endpoint_times_out(self) -> None:
        session = self._session(lambda _text: [])
        with self.assertRaises(TimeoutError):
            session.call("Page.enable", timeout=0.5)

    def test_pump_collects_events_without_a_call(self) -> None:
        def responder(text: str) -> list[str]:
            message = json.loads(text)
            return [
                json.dumps({"id": message["id"], "result": {}}),
                json.dumps({"method": "Log.entryAdded", "params": {}}),
            ]

        session = self._session(responder)
        session.call("Log.enable")
        session.pump(0.5)
        self.assertEqual(len(session.drain_events()), 1)


class WaitUntilTest(unittest.TestCase):
    def test_it_returns_the_first_truthy_value(self) -> None:
        values = iter([None, None, "ready"])
        self.assertEqual(
            wait_until(lambda: next(values), timeout=5, interval=0.01, description="x"),
            "ready",
        )

    def test_it_raises_when_the_condition_never_holds(self) -> None:
        with self.assertRaises(TimeoutError) as caught:
            wait_until(lambda: False, timeout=0.2, interval=0.01, description="the map")
        self.assertIn("the map", str(caught.exception))


class RunComparisonTest(unittest.TestCase):
    def _run(self, index: int, **snapshot: Any) -> RunResult:
        base = {
            "identities": [{"served": "engine/ioquake3.js", "actualSha256": "a" * 64}],
            "engineArguments": ["+map", "oa_pvomit"],
            "markers": {"clientGameLoaded": 1.0},
            "profile": {"package": "arena-web-ffa", "map": "oa_pvomit"},
        }
        base.update(snapshot)
        return RunResult(index=index, directory=Path("."), snapshot=base)

    def test_two_identical_runs_compare_equal(self) -> None:
        checks = compare_runs(self._run(1), self._run(2))
        self.assertTrue(all(check.passed for check in checks))

    def test_a_different_artifact_identity_fails_the_comparison(self) -> None:
        second = self._run(
            2, identities=[{"served": "engine/ioquake3.js", "actualSha256": "b" * 64}]
        )
        checks = {check.name: check for check in compare_runs(self._run(1), second)}
        self.assertFalse(checks["second-launch-same-artifact-identities"].passed)

    def test_different_engine_arguments_fail_the_comparison(self) -> None:
        second = self._run(2, engineArguments=["+map", "q3dm6ish"])
        checks = {check.name: check for check in compare_runs(self._run(1), second)}
        self.assertFalse(checks["second-launch-same-engine-arguments"].passed)

    def test_a_second_launch_that_never_entered_the_map_fails(self) -> None:
        second = self._run(2, markers={})
        checks = {check.name: check for check in compare_runs(self._run(1), second)}
        self.assertFalse(checks["second-launch-reached-the-same-profile"].passed)

    def test_a_run_with_a_failing_check_is_not_passed(self) -> None:
        result = self._run(1)
        result.checks.append(Check("something", False, "detail"))
        self.assertFalse(result.passed)


class BotDetectionTest(unittest.TestCase):
    BOTS = ("Skelebot", "Rai", "Sly")

    def test_the_local_player_is_not_counted_as_a_bot(self) -> None:
        lines = [
            "[stderr] UnnamedPlayer^7 entered the game",
            "[stderr] Skelebot^7 entered the game",
        ]
        self.assertEqual(bots_from_engine_log(lines, self.BOTS), {"Skelebot"})

    def test_every_configured_bot_is_found_through_its_colour_codes(self) -> None:
        lines = [f"[stderr] {name}^7 entered the game" for name in self.BOTS]
        self.assertEqual(bots_from_engine_log(lines, self.BOTS), set(self.BOTS))

    def test_a_chat_line_naming_a_bot_is_not_an_entry(self) -> None:
        lines = ["[stderr] Sly^7: ^2Skelebot entered the game, and so did I"]
        self.assertEqual(bots_from_engine_log(lines, self.BOTS), set())

    def test_a_name_that_merely_contains_a_bot_name_is_not_an_entry(self) -> None:
        lines = ["[stderr] SkelebotFan^7 entered the game"]
        self.assertEqual(bots_from_engine_log(lines, self.BOTS), set())

    def test_colour_codes_are_stripped_but_a_doubled_caret_is_kept(self) -> None:
        self.assertEqual(strip_color_codes("^1red^7 plain"), "red plain")
        self.assertEqual(strip_color_codes("^^"), "^^")


class ScoreTest(unittest.TestCase):
    """The three checks the review asked for, driven against synthetic runs."""

    ORIGIN = "http://127.0.0.1:8174"
    ARGUMENTS = ("+set", "sv_pure", "0", "+map", "oa_pvomit")

    def _expectations(self) -> Expectations:
        return Expectations(
            files=frozenset(
                {
                    "index.html",
                    "loader.js",
                    "engine/ioquake3.js",
                    # The archives the fixture's access log answers for, so the
                    # served-set check is consistent with the rotation checks
                    # rather than quietly failing beside them.
                    "content/base-aa.pk3",
                    "content/pvomit-bb.pk3",
                    "content/other-cc.pk3",
                }
            ),
            origin=self.ORIGIN,
            config_digests={"default.cfg": "c" * 64},
            artifact_digests={"engine/ioquake3.js": "a" * 64},
            engine_arguments=self.ARGUMENTS,
            bot_names=("Skelebot", "Rai"),
            rotation=("oa_pvomit",),
            rotation_parameter="oa_pvomit",
            rotation_served=frozenset({"content/base-aa.pk3", "content/pvomit-bb.pk3"}),
            rotation_excluded=frozenset({"content/other-cc.pk3"}),
            rotation_excluded_maps=("oa_shine",),
        )

    def _snapshot(self, **overrides: Any) -> dict[str, Any]:
        snapshot = {
            "status": "running",
            "error": None,
            "identities": [
                {
                    "served": "engine/ioquake3.js",
                    "actualSha256": "a" * 64,
                    "matches": True,
                }
            ],
            "configFiles": [{"served": "default.cfg", "sha256": "c" * 64}],
            "botEntries": [{"name": "Skelebot", "at": 1.0}, {"name": "Rai", "at": 2.0}],
            "markers": {"serverSpawned": 1.0, "clientGameLoaded": 2.0},
            "timings": {"runtimeInitializedMs": 1.0},
            "events": [],
            "render": {"cssWidth": 1280, "cssHeight": 577},
            "engineArguments": [
                *self.ARGUMENTS,
                "+set",
                "r_mode",
                "-1",
                "+set",
                "r_customwidth",
                "1280",
                "+set",
                "r_customheight",
                "577",
            ],
            "browserErrors": [],
            "unexpectedFileRequests": [],
            "frames": {"samples": 500, "meanFps": 60.0},
            "audioActivation": {"state": "running", "userActivation": True},
            "rotation": {
                "parameter": "oa_pvomit",
                "requested": ["oa_pvomit"],
                "resolved": ["oa_pvomit"],
                "published": ["oa_pvomit", "oa_shine"],
                "archives": ["baseq3/base.pk3", "baseq3/pvomit.pk3"],
            },
        }
        snapshot.update(overrides)
        return snapshot

    def _run(
        self,
        *,
        requests: list[str] | None = None,
        access_log: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> RunResult:
        result = RunResult(index=1, directory=Path("."))
        result.snapshot = self._snapshot(**overrides)
        result.engine_log = [
            "[stderr] UnnamedPlayer^7 entered the game",
            "[stderr] Skelebot^7 entered the game",
            "[stderr] Rai^7 entered the game",
        ]
        result.engine_defects = classify_engine_log(result.engine_log)
        result.requests = requests if requests is not None else [f"{self.ORIGIN}/"]
        result.access_log = (
            access_log
            if access_log is not None
            else [
                {"path": "/", "status": 200},
                {"path": "/content/base-aa.pk3", "status": 200},
                {"path": "/content/pvomit-bb.pk3", "status": 200},
            ]
        )
        result.screenshots = [
            {"file": "01-map-entered.png", "distinctColours": 900, "nearWhiteFraction": 0.42},
            {"file": "02-after-input.png", "distinctColours": 900, "nearWhiteFraction": 0.004},
            {"file": "03-after-focus.png", "distinctColours": 900, "nearWhiteFraction": 0.003},
        ]
        return result

    def _checks(self, result: RunResult) -> dict[str, Check]:
        _score(result, self._expectations())
        return {check.name: check for check in result.checks}

    def test_a_healthy_run_passes_every_new_check(self) -> None:
        checks = self._checks(self._run())
        for name in (
            "bots-entered-game",
            "engine-kept-running",
            "engine-arguments-are-the-committed-profile",
            "only-declared-local-artifacts",
            "rotation-canonicalised",
            "rotation-fetched-exactly-its-archives",
        ):
            self.assertTrue(checks[name].passed, f"{name}: {checks[name].detail}")

    def test_a_rotation_the_page_did_not_canonicalise_fails(self) -> None:
        checks = self._checks(
            self._run(
                rotation={
                    "parameter": "oa_shine,oa_pvomit",
                    "requested": ["oa_shine", "oa_pvomit"],
                    "resolved": ["oa_shine", "oa_pvomit"],
                    "published": ["oa_pvomit", "oa_shine"],
                    "archives": [],
                }
            )
        )
        self.assertFalse(checks["rotation-canonicalised"].passed)

    def test_a_run_that_fetched_an_excluded_archive_fails(self) -> None:
        """The check that a passing run could not have made: before the fetch
        selection existed, a page that downloaded everything satisfied every
        other check in the scoring function."""
        checks = self._checks(
            self._run(
                access_log=[
                    {"path": "/", "status": 200},
                    {"path": "/content/base-aa.pk3", "status": 200},
                    {"path": "/content/pvomit-bb.pk3", "status": 200},
                    {"path": "/content/other-cc.pk3", "status": 200},
                ]
            )
        )
        self.assertFalse(checks["rotation-fetched-exactly-its-archives"].passed)
        self.assertIn("other-cc", checks["rotation-fetched-exactly-its-archives"].detail)

    def test_a_run_that_missed_a_selected_archive_fails(self) -> None:
        checks = self._checks(
            self._run(
                access_log=[
                    {"path": "/", "status": 200},
                    {"path": "/content/base-aa.pk3", "status": 200},
                ]
            )
        )
        self.assertFalse(checks["rotation-fetched-exactly-its-archives"].passed)

    def test_a_missing_bot_fails_the_bot_check(self) -> None:
        checks = self._checks(self._run(botEntries=[{"name": "Skelebot", "at": 1.0}]))
        self.assertFalse(checks["bots-entered-game"].passed)

    def test_a_healthy_run_passes_the_white_surface_check(self) -> None:
        checks = self._checks(self._run())
        check = checks["canvas-no-white-surface-regression"]
        self.assertTrue(check.passed, check.detail)

    def test_a_white_in_game_screenshot_fails_the_white_surface_check(self) -> None:
        run = self._run()
        run.screenshots[1]["nearWhiteFraction"] = 0.16
        check = self._checks(run)["canvas-no-white-surface-regression"]
        self.assertFalse(check.passed)
        self.assertIn("16.00%", check.detail)

    def test_a_white_loading_screen_does_not_fail_the_white_surface_check(self) -> None:
        # 01-map-entered may legitimately capture the bright loading screen;
        # only the in-game screenshots are evidence of the defect.
        run = self._run()
        run.screenshots[0]["nearWhiteFraction"] = 0.51
        check = self._checks(run)["canvas-no-white-surface-regression"]
        self.assertTrue(check.passed, check.detail)

    def test_a_run_with_no_in_game_screenshot_fails_the_white_surface_check(self) -> None:
        run = self._run()
        run.screenshots = run.screenshots[:1]
        self.assertFalse(self._checks(run)["canvas-no-white-surface-regression"].passed)

    def test_a_page_report_disagreeing_with_the_log_fails_the_bot_check(self) -> None:
        run = self._run()
        run.engine_log = ["[stderr] Skelebot^7 entered the game"]
        self.assertFalse(self._checks(run)["bots-entered-game"].passed)

    def test_a_fatal_engine_exit_fails_the_kept_running_check(self) -> None:
        checks = self._checks(
            self._run(status="exited", events=[{"kind": "engine-exit", "detail": 3}])
        )
        self.assertFalse(checks["engine-kept-running"].passed)

    def test_a_loader_error_fails_the_kept_running_check(self) -> None:
        checks = self._checks(
            self._run(status="failed", error={"name": "LoaderError", "message": "x"})
        )
        self.assertFalse(checks["engine-kept-running"].passed)

    def test_arguments_that_are_not_the_committed_profile_fail(self) -> None:
        run = self._run()
        run.snapshot["engineArguments"] = ["+map", "q3dm6ish"]
        self.assertFalse(
            self._checks(run)["engine-arguments-are-the-committed-profile"].passed
        )

    def test_a_render_size_the_arguments_disagree_with_fails(self) -> None:
        run = self._run()
        run.snapshot["render"] = {"cssWidth": 640, "cssHeight": 480}
        self.assertFalse(
            self._checks(run)["engine-arguments-are-the-committed-profile"].passed
        )

    def test_a_later_render_size_does_not_rewrite_the_startup_arguments(self) -> None:
        run = self._run()
        run.snapshot["render"] = {
            "startupCssWidth": 1280,
            "startupCssHeight": 577,
            "cssWidth": 960,
            "cssHeight": 540,
            "resizeEvents": 1,
        }
        self.assertTrue(
            self._checks(run)["engine-arguments-are-the-committed-profile"].passed
        )

    def test_a_foreign_origin_with_a_staged_file_name_is_refused(self) -> None:
        checks = self._checks(
            self._run(requests=[f"{self.ORIGIN}/", "http://evil.example/loader.js"])
        )
        self.assertFalse(checks["only-declared-local-artifacts"].passed)

    def test_a_staged_path_on_the_serve_origin_is_accepted(self) -> None:
        checks = self._checks(
            self._run(requests=[f"{self.ORIGIN}/", f"{self.ORIGIN}/engine/ioquake3.js"])
        )
        self.assertTrue(checks["only-declared-local-artifacts"].passed)

    def test_an_undeclared_path_on_the_serve_origin_is_refused(self) -> None:
        checks = self._checks(
            self._run(requests=[f"{self.ORIGIN}/engine/ioquake3-config.json"])
        )
        self.assertFalse(checks["only-declared-local-artifacts"].passed)

    def test_a_query_string_does_not_hide_an_undeclared_path(self) -> None:
        checks = self._checks(self._run(requests=[f"{self.ORIGIN}/secrets.json?v=1"]))
        self.assertFalse(checks["only-declared-local-artifacts"].passed)

    def test_blob_and_data_urls_are_allowed(self) -> None:
        checks = self._checks(
            self._run(
                requests=[
                    f"{self.ORIGIN}/",
                    f"blob:{self.ORIGIN}/8f7a-c3",
                    "data:,",
                ]
            )
        )
        self.assertTrue(checks["only-declared-local-artifacts"].passed)


if __name__ == "__main__":
    unittest.main()
