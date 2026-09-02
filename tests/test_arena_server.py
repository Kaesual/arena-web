# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from arena_runtime import ArenaRuntimeError, published_maps  # noqa: E402
from arena_server import (  # noqa: E402
    STAGED_DIRECTORY_MODE,
    STAGED_FILE_MODE,
    ArenaServerError,
    _fraglimit_minimum,
    max_server_rotation,
    server_launch_arguments,
    setting_arguments,
    validate_launch_settings,
    client_tree_files,
    expected_client_arguments,
    expected_server_arguments,
    image_content_paths,
    load_profile,
    server_binary_path,
    server_tree_files,
    stage_tree,
    verify_staged_tree,
)


def read(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


# One valid launch configuration in each bot shape. They are written out rather
# than derived because a test that builds its input from the same bounds it is
# checking proves only that the code agrees with itself.
MIN_PLAYERS_SETTINGS = {
    "bots": {"minPlayers": 4, "skill": 3},
    "fraglimit": 15,
    "gametype": 0,
}
NAMED_SETTINGS = {
    "bots": {"named": [{"name": "Liz", "skill": 2}, {"name": "Major", "skill": 4}]},
    "fraglimit": 30,
    "gametype": 3,
}


class ProfileFixture(unittest.TestCase):
    """A writable copy of the checkout, small enough to mutate per test."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        for relative in (
            "native/server-profile.json",
            "native/server-default.cfg",
            "arena/game-profile.json",
            "arena/default.cfg",
            "content/pack-recipe.json",
            "manifests/browser-client.json",
            "provenance/arena-web-ffa-content-manifest.json",
        ):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        # Every published fragment, because the profile is bound to the whole
        # published set now rather than to one committed map.
        shutil.copytree(ROOT / "content/maps", self.root / "content/maps")
        # The pinned engine tree, read-only: the profile publishes the two
        # command-line bounds and they are checked against it.
        (self.root / "ioq3").symlink_to(ROOT / "ioq3")
        self.profile_path = self.root / "native/server-profile.json"

    def profile(self) -> dict:
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def write(self, profile: dict) -> None:
        self.profile_path.write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def reject(self, mutate, message: str) -> None:
        """One mutation, refused — and the file restored afterwards.

        The restore is what lets a test state several spellings of one rule.
        Without it the second mutation starts from the first one's tree and the
        test passes on the first failure's message, which is the shape that
        turns a two-case test into a one-case test in silence.
        """
        original = self.profile_path.read_text(encoding="utf-8")
        self.addCleanup(
            lambda: self.profile_path.write_text(original, encoding="utf-8")
        )
        profile = self.profile()
        mutate(profile)
        # The argument lists are derived, so a mutated declarative field has to
        # be re-derived or the test would only prove the list check fires.
        try:
            profile["serverArguments"] = expected_server_arguments(profile)
            profile["clientArguments"] = expected_client_arguments(profile)
        except (ArenaServerError, KeyError, TypeError):
            pass
        self.write(profile)
        try:
            with self.assertRaisesRegex(ArenaServerError, message):
                load_profile(self.root)
        finally:
            self.profile_path.write_text(original, encoding="utf-8")


class ProfileTests(ProfileFixture):
    def test_the_committed_profile_is_valid(self) -> None:
        profile = load_profile(ROOT)
        self.assertEqual(profile["basegame"], "arena")
        self.assertNotIn("map", profile)

    def test_server_arguments_are_derived_and_carry_no_map(self) -> None:
        profile = load_profile(ROOT)
        arguments = expected_server_arguments(profile)
        self.assertEqual(arguments, profile["serverArguments"])
        self.assertNotIn("+map", arguments)

    def test_the_rotation_precedes_every_bot_once_prepended(self) -> None:
        """`addbot` is forwarded to a running game module, so a map has to be up
        first. The rotation is prepended, and every `+set` line is applied by
        Com_StartupVariable before the command buffer runs at all, so `vstr d1`
        is the first *command* either way."""
        profile = load_profile(ROOT)
        arguments = server_launch_arguments(
            ROOT, profile, ["oa_pvomit"], NAMED_SETTINGS
        )
        self.assertLess(arguments.index("+vstr"), arguments.index("+addbot"))
        committed = len(profile["serverArguments"])
        addbots = len(NAMED_SETTINGS["bots"]["named"]) * 5
        self.assertEqual(
            arguments[-committed - addbots : -addbots], profile["serverArguments"]
        )

    def test_a_launch_without_a_rotation_is_refused(self) -> None:
        """The server-side half of the rule the loader enforces for the browser:
        this repository cannot check that a caller's two derivations came from
        one list, but it can refuse to produce a command line for a rotation
        nobody chose."""
        profile = load_profile(ROOT)
        with self.assertRaisesRegex(ArenaRuntimeError, "is empty"):
            server_launch_arguments(ROOT, profile, [], MIN_PLAYERS_SETTINGS)
        with self.assertRaisesRegex(ArenaRuntimeError, "publishes no archive"):
            server_launch_arguments(ROOT, profile, ["q3dm17"], MIN_PLAYERS_SETTINGS)

    def test_the_engine_ceiling_is_a_boundary_and_not_a_slogan(self) -> None:
        """One entry either side of the ceiling, and deliberately not written so
        that it can quietly stop testing anything.

        Phrasing this against the published set would make the refusal half
        conditional on the set being larger than the ceiling — true today at 16
        maps against 15, and silently skipped the first time a release published
        fewer. A rotation may repeat a map, so the boundary is found by growing
        one instead, which works whatever the release publishes.
        """
        profile = load_profile(ROOT)
        names = published_maps(ROOT, "provenance/arena-web-ffa-content-manifest.json")
        cheapest = min(names, key=len)
        fits = 0
        for count in range(1, 200):
            try:
                server_launch_arguments(
                    ROOT, profile, [cheapest] * count, MIN_PLAYERS_SETTINGS
                )
            except ArenaRuntimeError as error:
                self.assertIn("pinned engine's", str(error))
                fits = count - 1
                break
        else:  # pragma: no cover - a bound that never bites is the failure
            self.fail("no rotation length was refused")
        self.assertGreater(fits, 1)
        server_launch_arguments(ROOT, profile, [cheapest] * fits, MIN_PLAYERS_SETTINGS)

    def test_the_reported_ceiling_is_the_one_the_derivation_enforces(self) -> None:
        """`max_server_rotation` is what a caller sizes a rotation with, so it
        has to be the largest rotation that is actually accepted and not merely
        a plausible number."""
        profile = load_profile(ROOT)
        names = published_maps(ROOT, "provenance/arena-web-ffa-content-manifest.json")
        ceiling = max_server_rotation(ROOT, profile, MIN_PLAYERS_SETTINGS)
        self.assertGreater(ceiling, 1)
        self.assertLessEqual(ceiling, len(names))
        server_launch_arguments(ROOT, profile, names[:ceiling], MIN_PLAYERS_SETTINGS)
        # One more entry, from the published set when there is one left over and
        # otherwise a repeat, so this half never silently stops running.
        one_more = names[: ceiling + 1] if ceiling < len(names) else names + [names[0]]
        with self.assertRaisesRegex(ArenaRuntimeError, "pinned engine's"):
            server_launch_arguments(ROOT, profile, one_more, MIN_PLAYERS_SETTINGS)

    def test_named_bots_follow_the_engine_delay_cadence(self) -> None:
        _sets, addbot = setting_arguments(
            {**NAMED_SETTINGS, "bots": {"named": [
                {"name": "Liz", "skill": 1},
                {"name": "Major", "skill": 1},
                {"name": "Penguin", "skill": 1},
            ]}}
        )
        delays = [
            int(addbot[index + 4])
            for index, item in enumerate(addbot)
            if item == "+addbot"
        ]
        self.assertEqual(delays, [2000, 3500, 5000])

    def test_the_committed_array_carries_no_bot_and_no_setting(self) -> None:
        """What moved out, asserted from the other side. The committed list is
        what a caller passes verbatim, so a setting left in it would be a
        default nobody can see."""
        arguments = expected_server_arguments(load_profile(ROOT))
        self.assertNotIn("+addbot", arguments)
        for cvar in ("fraglimit", "g_gametype", "bot_minplayers", "g_spSkill"):
            self.assertNotIn(cvar, arguments)

    def test_client_arguments_carry_no_endpoint(self) -> None:
        profile = load_profile(ROOT)
        arguments = expected_client_arguments(profile)
        self.assertEqual(arguments, profile["clientArguments"])
        self.assertNotIn("+connect", arguments)

    def test_committed_argument_lists_must_be_the_derivation(self) -> None:
        profile = self.profile()
        profile["serverArguments"] = profile["serverArguments"] + ["+set", "x", "1"]
        self.write(profile)
        with self.assertRaisesRegex(ArenaServerError, "exactly the derivation"):
            load_profile(self.root)

    def test_the_retail_base_game_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["basegame"] = "baseq3"
            profile["cvars"]["com_basegame"] = "baseq3"

        self.reject(mutate, "ioquake3's own base game")

    def test_a_public_master_registration_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["cvars"].__setitem__("dedicated", "2"),
            "public master servers",
        )

    def test_a_pure_server_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["cvars"].__setitem__("sv_pure", "1"),
            "FS_FindVM",
        )

    def test_a_second_address_family_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["cvars"].__setitem__("net_enabled", "3"),
            "only IPv4",
        )

    def test_a_port_that_does_not_match_the_cvar_is_refused(self) -> None:
        self.reject(
            lambda profile: profile.__setitem__("port", 27961),
            "must equal the profile port",
        )

    def test_a_privileged_port_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["port"] = 80
            profile["cvars"]["net_port"] = "80"

        self.reject(mutate, "unprivileged UDP port")

    def test_a_committed_game_type_is_refused_because_it_is_a_setting(self) -> None:
        """This replaced a check that the committed value had to be GT_FFA. The
        property that check really executed — the profile may not decide the
        gametype behind a caller's back — is what survives, and it is now
        stronger: no value at all is permitted here, not merely the wrong one."""
        def mutate(profile: dict) -> None:
            profile["cvars"]["g_gametype"] = "3"
            profile["cvarNotes"]["g_gametype"] = "a note long enough to pass"

        self.reject(mutate, "launch setting and must not be committed")

    def test_a_client_download_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["client"]["cvars"].__setitem__(
                "cl_allowDownload", "1"
            ),
            "no media download",
        )

    def test_the_retail_player_model_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["client"]["cvars"].__setitem__("model", "sarge"),
            "player presentation",
        )

    def test_a_committed_map_is_refused(self) -> None:
        """Neither committed list may choose a map. They are what a caller
        passes verbatim, so a default inside one is a rotation nobody can see
        and nothing downstream reports."""
        profile = self.profile()
        profile["serverArguments"] = ["+map", "q3dm17"] + profile["serverArguments"]
        self.write(profile)
        with self.assertRaisesRegex(ArenaServerError, "exactly the derivation"):
            load_profile(self.root)

    def test_a_roster_that_is_not_the_packaged_set_is_refused(self) -> None:
        """Equality, because `bot_minplayers` fills its slots with `addbot
        random`, which draws from the packaged bots.txt and never looks at this
        roster: a packaged bot missing here would appear on a server nobody
        could have asked for it on."""

        def extra(profile: dict) -> None:
            profile["botRoster"] = sorted(profile["botRoster"] + ["Grunt"])

        self.reject(extra, "must be exactly the bots the pack packages")

        def fewer(profile: dict) -> None:
            # Not the last: the browser slice names Skelebot, so dropping that
            # one would be refused by the subset rule first and this half would
            # stop testing the equality it is about.
            profile["botRoster"] = profile["botRoster"][1:]

        self.reject(fewer, "must be exactly the bots the pack packages")

    def test_a_committed_frag_limit_is_refused_because_it_is_a_setting(self) -> None:
        def mutate(profile: dict) -> None:
            profile["cvars"]["fraglimit"] = "20"
            profile["cvarNotes"]["fraglimit"] = "a note long enough to pass"

        self.reject(mutate, "launch setting and must not be committed")

    def test_an_unexplained_cvar_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["cvars"]["sv_fps"] = "40"

        self.reject(mutate, "exactly the cvars it sets")

    def test_a_shallow_cvar_note_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["cvarNotes"]["sv_pure"] = "because"

        self.reject(mutate, "must state why the cvar is set")

    def test_a_config_source_outside_the_repository_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["configFiles"][0]["source"] = "../../etc/passwd"

        self.reject(mutate, "leaves the repository|does not exist")

    def test_a_config_that_is_not_default_cfg_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["configFiles"][0]["served"] = "server.cfg"

        self.reject(mutate, "the engine requires exactly default.cfg")

    def test_both_roles_must_supply_a_config(self) -> None:
        def mutate(profile: dict) -> None:
            profile["configFiles"] = [profile["configFiles"][0]]

        self.reject(mutate, "one config per role")

    def test_an_unknown_profile_key_is_refused(self) -> None:
        profile = self.profile()
        profile["extra"] = 1
        self.write(profile)
        with self.assertRaisesRegex(ArenaServerError, "unexpected key set"):
            load_profile(self.root)

    def test_a_published_bound_that_is_not_its_derivation_is_refused(self) -> None:
        """The bounds are published so a consumer can read them instead of
        reimplementing this file, which makes them an assertion rather than a
        note — so each is checked against the thing that derives it."""

        def gametypes(profile: dict) -> None:
            profile["launchSettings"]["gametypes"] = [0, 3, 4]

        self.reject(gametypes, "the gametypes this release supports")

        def skill(profile: dict) -> None:
            profile["launchSettings"]["bots"]["skill"] = [1, 9]

        self.reject(skill, "clamps a bot's skill")

        def count(profile: dict) -> None:
            profile["launchSettings"]["bots"]["maxCount"] = 12

        self.reject(count, "leaves room for 7")

        def floor(profile: dict) -> None:
            profile["launchSettings"]["fraglimit"]["minimum"] = 0

        self.reject(floor, "the match-end rule makes it 1")

    def test_the_frag_limit_floor_follows_the_committed_time_limit(self) -> None:
        """It is derived, not decided: a committed time limit already ends a
        level, so the frag limit would not have to. Tested on the derivation
        itself, because moving `timelimit` in the profile is refused earlier by
        the binding to the browser slice."""
        profile = load_profile(ROOT)
        self.assertEqual(_fraglimit_minimum(profile), 1)
        self.assertEqual(
            _fraglimit_minimum({**profile, "cvars": {**profile["cvars"], "timelimit": "10"}}),
            0,
        )

    def test_a_relative_game_directory_is_refused(self) -> None:
        self.reject(
            lambda profile: profile.__setitem__("gameDirectory", "opt/arena-web"),
            "absolute image path",
        )

class LaunchSettingsTests(unittest.TestCase):
    """The four values a caller supplies, and the bounds they are held to."""

    def setUp(self) -> None:
        self.profile = load_profile(ROOT)

    def refuses(self, settings: dict, message: str) -> None:
        with self.assertRaisesRegex(ArenaServerError, message):
            validate_launch_settings(self.profile, settings)

    def test_both_shapes_are_accepted(self) -> None:
        for settings in (MIN_PLAYERS_SETTINGS, NAMED_SETTINGS):
            validate_launch_settings(self.profile, settings)

    def test_the_two_bot_shapes_are_exclusive(self) -> None:
        self.refuses(
            {**MIN_PLAYERS_SETTINGS, "bots": {"minPlayers": 2, "skill": 3, "named": []}},
            "exactly one of",
        )
        self.refuses({**MIN_PLAYERS_SETTINGS, "bots": {}}, "exactly one of")

    def test_an_unsupported_gametype_is_refused_by_value(self) -> None:
        self.refuses({**MIN_PLAYERS_SETTINGS, "gametype": 4}, "this release supports")
        validate_launch_settings(self.profile, {**MIN_PLAYERS_SETTINGS, "gametype": 3})

    def test_a_frag_limit_of_zero_is_refused_at_the_bound(self) -> None:
        """The bound *is* the match-end rule here, which is why there is no
        second copy of it at launch: the floor is derived from the rule at
        profile load, so a value inside the bound cannot violate it."""
        self.refuses({**MIN_PLAYERS_SETTINGS, "fraglimit": 0}, "must be an integer in")
        self.assertEqual(
            self.profile["launchSettings"]["fraglimit"]["minimum"],
            _fraglimit_minimum(self.profile),
        )

    def test_a_bot_the_release_does_not_publish_is_refused(self) -> None:
        self.refuses(
            {**NAMED_SETTINGS, "bots": {"named": [{"name": "Grunt", "skill": 3}]}},
            "is not a bot this release publishes",
        )

    def test_more_bots_than_slots_is_refused(self) -> None:
        cast = [
            {"name": name, "skill": 3} for name in self.profile["botRoster"]
        ]
        validate_launch_settings(
            self.profile, {**NAMED_SETTINGS, "bots": {"named": cast}}
        )
        self.refuses(
            {**NAMED_SETTINGS, "bots": {"named": [cast[0], dict(cast[0])]}},
            "names 'Assassin' twice",
        )
        self.refuses(
            {**NAMED_SETTINGS, "bots": {"named": cast + [dict(cast[0])]}},
            "must name 1..7 bots",
        )
        self.refuses(
            {**MIN_PLAYERS_SETTINGS, "bots": {"minPlayers": 8, "skill": 3}},
            "must be an integer in 0..7",
        )

    def test_no_bots_at_all_is_a_configuration_and_not_an_error(self) -> None:
        """`G_CheckMinimumPlayers` returns immediately at 0, so a human-only
        server is a legitimate setting rather than a missing one."""
        settings = {**MIN_PLAYERS_SETTINGS, "bots": {"minPlayers": 0, "skill": 3}}
        arguments = server_launch_arguments(ROOT, self.profile, ["oa_pvomit"], settings)
        self.assertIn("bot_minplayers", arguments)
        self.assertNotIn("+addbot", arguments)

    def test_the_min_players_shape_carries_the_skill_cvar(self) -> None:
        """The one non-obvious emission, and the reason the budget is a line
        wider than a count of the settings suggests: `G_AddRandomBot` reads
        `g_spSkill`, so difficulty in this shape is a `+set` line of its own."""
        sets, addbot = setting_arguments(MIN_PLAYERS_SETTINGS)
        self.assertEqual(addbot, [])
        self.assertEqual(
            sets,
            [
                "+set", "bot_minplayers", "4",
                "+set", "fraglimit", "15",
                "+set", "g_gametype", "0",
                "+set", "g_spSkill", "3",
            ],
        )

    def test_the_named_shape_carries_no_skill_cvar(self) -> None:
        sets, addbot = setting_arguments(NAMED_SETTINGS)
        self.assertNotIn("g_spSkill", sets)
        self.assertEqual(addbot[:5], ["+addbot", "Liz", "2", "free", "2000"])

    def test_the_ceiling_depends_on_the_settings_and_not_only_the_names(self) -> None:
        """A rotation ceiling stated without its configuration is the kind of
        number this topic has already passed on wrongly three times."""
        cast = [{"name": name, "skill": 3} for name in self.profile["botRoster"]]
        with_min_players = max_server_rotation(ROOT, self.profile, MIN_PLAYERS_SETTINGS)
        with_full_cast = max_server_rotation(
            ROOT, self.profile, {**NAMED_SETTINGS, "bots": {"named": cast}}
        )
        self.assertGreater(with_min_players, with_full_cast)


class TreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile(ROOT)

    def _archives(self) -> list[str]:
        """The content set, from the manifest rather than from a list here.

        Every published archive is in both trees, so a test that spelled them
        out would have to be edited for each added map — and would then be
        asserting what its own author last typed rather than what the release
        contains.
        """
        content = read("provenance/arena-web-ffa-content-manifest.json")
        return sorted(
            f"arena/{item['path'].rsplit('/', 1)[-1]}"
            for item in content["artifacts"]
        )

    def test_the_server_tree_carries_only_the_module_it_runs(self) -> None:
        files = server_tree_files(ROOT, self.profile)
        self.assertEqual(
            sorted(files),
            sorted(self._archives() + ["arena/default.cfg", "arena/vm/qagame.qvm"]),
        )

    def test_the_client_tree_carries_the_modules_it_runs(self) -> None:
        files = client_tree_files(ROOT, self.profile)
        self.assertEqual(
            sorted(files),
            sorted(
                self._archives()
                + ["arena/default.cfg", "arena/vm/cgame.qvm", "arena/vm/ui.qvm"]
            ),
        )

    def test_both_sides_use_the_same_committed_identities(self) -> None:
        server = server_tree_files(ROOT, self.profile)
        client = client_tree_files(ROOT, self.profile)
        content = read("provenance/arena-web-ffa-content-manifest.json")
        digests = {
            item["path"].rsplit("/", 1)[-1]: item["sha256"]
            for item in content["artifacts"]
        }
        packs = [name for name in server if name.endswith(".pk3")]
        self.assertEqual(sorted(packs), self._archives())
        self.assertGreater(len(packs), 1)
        for pack in packs:
            self.assertEqual(server[pack]["sha256"], client[pack]["sha256"])
            self.assertEqual(server[pack]["sha256"], digests[pack.rsplit("/", 1)[-1]])
        engine = read("manifests/browser-client.json")
        for module, relative in (
            ("qagame", "arena/vm/qagame.qvm"),
            ("cgame", "arena/vm/cgame.qvm"),
            ("ui", "arena/vm/ui.qvm"),
        ):
            entry = next(
                item
                for item in engine["artifacts"]
                if item["path"] == f"baseq3/vm/{module}.qvm"
            )
            source = server if module == "qagame" else client
            self.assertEqual(source[relative]["sha256"], entry["sha256"])

    def test_the_binary_sits_in_the_game_directory(self) -> None:
        self.assertEqual(server_binary_path(self.profile), "/opt/arena-web/ioq3ded")

    def test_image_content_paths_are_relative_and_complete(self) -> None:
        paths = image_content_paths(self.profile, server_tree_files(ROOT, self.profile))
        self.assertEqual(
            paths,
            sorted(
                [f"opt/arena-web/{name}" for name in self._archives()]
                + [
                    "opt/arena-web/arena/default.cfg",
                    "opt/arena-web/arena/vm/qagame.qvm",
                    "opt/arena-web/ioq3ded",
                ]
            ),
        )
        self.assertFalse(any(path.startswith("/") for path in paths))


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile(ROOT)
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work)
        self.engine_dir = self.work / "engine"
        self.content_dir = self.work / "content"
        self.target = self.work / "staged"
        self.files = server_tree_files(ROOT, self.profile)
        self._write_sources()

    def _write_sources(self) -> None:
        for entry in self.files.values():
            if entry["kind"] != "artifact":
                continue
            root = self.engine_dir if entry["manifest"] == "engine" else self.content_dir
            path = root / entry["artifactPath"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._bytes_with(entry))

    @staticmethod
    def _bytes_with(entry: dict) -> bytes:
        # The staging check is a digest check, so the fixture only has to be a
        # file of the right length; the digest is asserted to mismatch below.
        return b"\x00" * entry["size"]

    def test_a_wrong_artifact_is_refused(self) -> None:
        with self.assertRaisesRegex(ArenaServerError, "is not the committed"):
            stage_tree(
                ROOT,
                self.target,
                self.files,
                engine_dir=self.engine_dir,
                content_dir=self.content_dir,
            )

    def test_a_missing_build_directory_is_refused(self) -> None:
        with self.assertRaisesRegex(ArenaServerError, "does not exist; build it first"):
            stage_tree(
                ROOT,
                self.target,
                self.files,
                engine_dir=self.work / "absent",
                content_dir=self.content_dir,
            )

    def test_a_real_staging_is_verified(self) -> None:
        engine_dir = ROOT / "build/browser/tree/Release"
        content_dir = ROOT / "build/content-pack"
        if not engine_dir.is_dir() or not (
            content_dir / "baseq3/arena-web-ffa-base.pk3"
        ).is_file():
            self.skipTest("the accepted build outputs are not present")
        verified = stage_tree(
            ROOT,
            self.target,
            self.files,
            engine_dir=engine_dir,
            content_dir=content_dir,
        )
        # Every published archive plus the server's QVM: the manifest-bound
        # artifacts, which is what stage_tree verifies. default.cfg is repository
        # source and is compared, not digest-verified.
        content = read("provenance/arena-web-ffa-content-manifest.json")
        self.assertEqual(len(verified), len(content["artifacts"]) + 1)
        for path in self.target.rglob("*"):
            expected = (
                STAGED_DIRECTORY_MODE if path.is_dir() else STAGED_FILE_MODE
            )
            self.assertEqual(path.stat().st_mode & 0o7777, expected, path)
        verify_staged_tree(self.target, self.files)

        extra = self.target / "arena" / "extra.cfg"
        extra.write_bytes(b"")
        os.chmod(extra, STAGED_FILE_MODE)
        with self.assertRaisesRegex(ArenaServerError, "does not declare"):
            verify_staged_tree(self.target, self.files)
        extra.unlink()

        (self.target / "arena/default.cfg").write_text("tampered", encoding="utf-8")
        os.chmod(self.target / "arena/default.cfg", STAGED_FILE_MODE)
        with self.assertRaisesRegex(ArenaServerError, "differs from"):
            verify_staged_tree(self.target, self.files)

    def test_a_wrong_mode_is_refused(self) -> None:
        engine_dir = ROOT / "build/browser/tree/Release"
        content_dir = ROOT / "build/content-pack"
        if not engine_dir.is_dir() or not (
            content_dir / "baseq3/arena-web-ffa-base.pk3"
        ).is_file():
            self.skipTest("the accepted build outputs are not present")
        stage_tree(
            ROOT,
            self.target,
            self.files,
            engine_dir=engine_dir,
            content_dir=content_dir,
        )
        os.chmod(self.target / "arena/default.cfg", 0o600)
        with self.assertRaisesRegex(ArenaServerError, "is not mode 0644"):
            verify_staged_tree(self.target, self.files)

    def test_a_missing_staged_file_is_refused(self) -> None:
        with self.assertRaisesRegex(ArenaServerError, "does not exist"):
            verify_staged_tree(self.work / "never-staged", self.files)


class DerivationEdgeTests(unittest.TestCase):
    def test_argument_derivation_is_sorted_by_cvar_name(self) -> None:
        profile = copy.deepcopy(load_profile(ROOT))
        profile["cvars"] = {"zz_last": "1", "aa_first": "2"}
        arguments = expected_server_arguments(profile)
        self.assertEqual(arguments[:6], ["+set", "aa_first", "2", "+set", "zz_last", "1"])




class MultiMapRecipeTests(ProfileFixture):
    """A pack may carry several maps; a server still starts exactly one of them."""

    def _fragments(self, maps: list[dict]) -> None:
        """Rewrite the fragment set, and the manifest inputs that bind it."""
        directory = self.root / "content/maps"
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        base = read("content/maps/oa_pvomit.json")
        inputs = []
        for entry in maps:
            fragment = json.loads(json.dumps(base))
            fragment["map"] = entry["map"]
            fragment["arena"] = dict(base["arena"], **entry)
            fragment["generatedMembers"] = [
                "NOTICE-arena-web.txt",
                f"scripts/{entry['map']}.arena",
            ]
            path = directory / f"{entry['map']}.json"
            path.write_text(json.dumps(fragment, indent=2, sort_keys=True) + "\n")
            inputs.append(
                {
                    "id": f"arena-web-map-{entry['map']}",
                    "identity": "sha256:"
                    + hashlib.sha256(path.read_bytes()).hexdigest(),
                    "kind": "archive",
                }
            )
        manifest_path = self.root / "provenance/arena-web-ffa-content-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["inputs"] = [
            item
            for item in manifest["inputs"]
            if not item["id"].startswith("arena-web-map-")
        ] + inputs
        manifest["artifacts"] = [
            item
            for item in manifest["artifacts"]
            if not item["path"].startswith("baseq3/arena-web-ffa-map-")
        ] + [
            {
                "path": f"baseq3/arena-web-ffa-map-{entry['map']}.pk3",
                "sha256": "a" * 64,
                "size": 1,
            }
            for entry in maps
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def test_a_pack_with_several_maps_is_accepted_and_all_are_launchable(self) -> None:
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa", "fraglimit": "15"},
            ]
        )
        profile = load_profile(self.root)
        arguments = server_launch_arguments(
            self.root, profile, ["oa_shine", "oa_pvomit"], MIN_PLAYERS_SETTINGS
        )
        self.assertIn("map oa_shine;set nextmap vstr d2", arguments)
        self.assertIn("map oa_pvomit;set nextmap vstr d1", arguments)

    def test_every_published_arena_must_be_ffa_not_just_a_started_one(self) -> None:
        """The map is a launch argument, so there is no started map at build
        time: the property has to hold for every archive the release
        publishes, whichever one it is that fails."""
        for offender in ("oa_shine", "oa_pvomit"):
            self._fragments(
                [
                    {
                        "map": name,
                        "type": "tourney" if name == offender else "ffa",
                        "fraglimit": "15",
                    }
                    for name in ("oa_shine", "oa_pvomit")
                ]
            )
            with self.assertRaisesRegex(
                ArenaServerError, "must be exactly 'ffa'"
            ) as caught:
                load_profile(self.root)
            self.assertIn(f"content/maps/{offender}.json", str(caught.exception))

    def test_a_supported_extra_tag_is_refused_too(self) -> None:
        """`ffa tourney` passed the membership check that stood in for the
        reduction rule until WP-F batch 3 gave the rule a gate."""
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa tourney", "fraglimit": "15"},
            ]
        )
        with self.assertRaisesRegex(
            ArenaServerError, "must be exactly 'ffa'"
        ) as caught:
            load_profile(self.root)
        self.assertIn("content/maps/oa_pvomit.json", str(caught.exception))

    def test_an_archive_the_content_manifest_lacks_is_refused(self) -> None:
        """The recipe derives the archive set from the fragments; if the content
        manifest does not carry one of them, the server tree cannot be built and
        must say so rather than raise a KeyError."""
        manifest_path = self.root / "provenance/arena-web-ffa-content-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["artifacts"] = [
            item
            for item in manifest["artifacts"]
            if not item["path"].startswith("baseq3/arena-web-ffa-map-")
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        profile = load_profile(self.root)
        with self.assertRaisesRegex(ArenaServerError, "declares no artifact"):
            server_tree_files(self.root, profile)

    def test_a_fragment_that_is_not_the_one_the_manifest_records_is_refused(
        self,
    ) -> None:
        """A fragment is read only after its digest matches the identity the
        content manifest records, so content cannot join the build without
        joining the release identity."""
        path = self.root / "content/maps/oa_pvomit.json"
        fragment = json.loads(path.read_text())
        fragment["arena"]["fraglimit"] = "99"
        path.write_text(json.dumps(fragment))
        with self.assertRaisesRegex(ArenaServerError, "content manifest records"):
            load_profile(self.root)


if __name__ == "__main__":
    unittest.main()
