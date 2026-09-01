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

from arena_server import (  # noqa: E402
    STAGED_DIRECTORY_MODE,
    STAGED_FILE_MODE,
    ArenaServerError,
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
            "content/maps/oa_pvomit.json",
        ):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        self.profile_path = self.root / "native/server-profile.json"

    def profile(self) -> dict:
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def write(self, profile: dict) -> None:
        self.profile_path.write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def reject(self, mutate, message: str) -> None:
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
        with self.assertRaisesRegex(ArenaServerError, message):
            load_profile(self.root)


class ProfileTests(ProfileFixture):
    def test_the_committed_profile_is_valid(self) -> None:
        profile = load_profile(ROOT)
        self.assertEqual(profile["basegame"], "arena")
        self.assertEqual(profile["map"], "oa_pvomit")

    def test_server_arguments_are_derived(self) -> None:
        profile = load_profile(ROOT)
        arguments = expected_server_arguments(profile)
        self.assertEqual(arguments, profile["serverArguments"])
        self.assertIn("+map", arguments)
        self.assertLess(arguments.index("+map"), arguments.index("+addbot"))

    def test_bots_follow_the_engine_delay_cadence(self) -> None:
        profile = load_profile(ROOT)
        arguments = expected_server_arguments(profile)
        delays = [
            int(arguments[index + 4])
            for index, item in enumerate(arguments)
            if item == "+addbot"
        ]
        self.assertEqual(delays, [2000, 3500, 5000])

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

    def test_a_non_ffa_game_type_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["cvars"].__setitem__("g_gametype", "3"),
            "GT_FFA",
        )

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

    def test_a_map_the_pack_does_not_contain_is_refused(self) -> None:
        # The browser cross-check fires first; the recipe cross-check is what
        # catches a map both product profiles agree on but the pack lacks.
        self.reject(
            lambda profile: profile.__setitem__("map", "q3dm17"),
            "must equal the browser slice's map",
        )

    def test_a_map_both_profiles_agree_on_but_the_pack_lacks_is_refused(self) -> None:
        browser_path = self.root / "arena/game-profile.json"
        browser = json.loads(browser_path.read_text(encoding="utf-8"))
        browser["map"] = "q3dm17"
        browser_path.write_text(
            json.dumps(browser, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.reject(
            lambda profile: profile.__setitem__("map", "q3dm17"),
            "records no fragment for map",
        )

    def test_a_bot_the_pack_does_not_package_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["bots"][0]["name"] = "Grunt"

        self.reject(mutate, "the browser slice's bots")

    def test_a_frag_limit_that_differs_from_the_recipe_is_refused(self) -> None:
        self.reject(
            lambda profile: profile["cvars"].__setitem__("fraglimit", "20"),
            "browser slice's value",
        )

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

    def test_a_bot_skill_outside_the_engine_range_is_refused(self) -> None:
        def mutate(profile: dict) -> None:
            profile["bots"][0]["skill"] = 9

        self.reject(mutate, "must be within")

    def test_a_relative_game_directory_is_refused(self) -> None:
        self.reject(
            lambda profile: profile.__setitem__("gameDirectory", "opt/arena-web"),
            "absolute image path",
        )


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

    def test_a_profile_starting_one_of_several_packaged_maps_is_accepted(self) -> None:
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa", "fraglimit": "15"},
            ]
        )
        self.assertEqual(load_profile(self.root)["map"], "oa_pvomit")

    def test_a_map_outside_the_packaged_set_is_refused(self) -> None:
        self._fragments([{"map": "oa_shine", "type": "ffa", "fraglimit": "15"}])
        with self.assertRaisesRegex(ArenaServerError, "records no fragment for map"):
            load_profile(self.root)

    def test_the_started_arena_is_the_one_that_must_be_ffa(self) -> None:
        self._fragments(
            [
                {"map": "oa_shine", "type": "tourney", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "ffa", "fraglimit": "15"},
            ]
        )
        load_profile(self.root)
        self._fragments(
            [
                {"map": "oa_shine", "type": "ffa", "fraglimit": "15"},
                {"map": "oa_pvomit", "type": "tourney", "fraglimit": "15"},
            ]
        )
        with self.assertRaisesRegex(ArenaServerError, "only starts an FFA arena"):
            load_profile(self.root)

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
