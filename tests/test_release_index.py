# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed checks for the immutable browser release index."""

from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from arena_runtime import load_profile, served_files  # noqa: E402
from release_index import ReleaseIndexError, validate_release_index  # noqa: E402


class ReleaseIndexTests(unittest.TestCase):
    def _minimal_checkout(self, directory: str) -> Path:
        checkout = Path(directory) / "arena-web"
        index_path = ROOT / "release/browser-release.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        paths = {"release/browser-release.json"}
        paths.update(entry["path"] for entry in index["authorities"].values())
        # The per-map fragments are content-determining data the content
        # manifest records by digest, and the index checks that set against the
        # directory both ways, so a minimal checkout has to carry them.
        paths.update(
            item.relative_to(ROOT).as_posix()
            for item in (ROOT / "content/maps").iterdir()
            if item.is_file()
        )
        # The measured per-map figures are content-determining data too: the
        # content manifest records this file's digest and copies its values.
        paths.add("records/map-resource-measurements.json")
        for relative in sorted(paths):
            destination = checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        return checkout

    def _index(self, checkout: Path) -> tuple[Path, dict]:
        path = checkout / "release/browser-release.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _write_index(self, path: Path, index: dict) -> None:
        path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _refresh_authority(self, checkout: Path, index: dict, role: str) -> None:
        source = checkout / index["authorities"][role]["path"]
        payload = source.read_bytes()
        index["authorities"][role]["sha256"] = hashlib.sha256(payload).hexdigest()
        index["authorities"][role]["size"] = len(payload)

    def test_committed_index_matches_exact_served_tree(self) -> None:
        expected = served_files(ROOT, load_profile(ROOT))
        validate_release_index(ROOT, expected)
        index = json.loads((ROOT / "release/browser-release.json").read_text())
        # Derived from the profile rather than restated, so an added map needs
        # no edit here: the comparison below is the check, and a count spelled
        # out beside it would only ever assert what its author last typed.
        self.assertEqual(len(index["servedFiles"]), len(expected))
        self.assertEqual(
            [entry["path"] for entry in index["servedFiles"]], sorted(expected)
        )

    def _fragment_path(self, checkout: Path) -> Path:
        return checkout / "content/maps/oa_pvomit.json"

    def test_a_fragment_the_content_manifest_does_not_record_is_refused(self) -> None:
        """The map set may not grow outside the release identity."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            (checkout / "content/maps/invented.json").write_text("{}")
            with self.assertRaisesRegex(ReleaseIndexError, "map fragments do not match"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_recorded_fragment_that_is_missing_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            self._fragment_path(checkout).unlink()
            with self.assertRaisesRegex(ReleaseIndexError, "map fragments do not match"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_fragment_whose_bytes_moved_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path = self._fragment_path(checkout)
            path.write_text(path.read_text() + "\n")
            with self.assertRaisesRegex(
                ReleaseIndexError, "is not the fragment the manifest records"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_stray_file_in_the_fragment_directory_is_refused(self) -> None:
        """The validator and the build must agree on what the directory means."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            (checkout / "content/maps/notes.txt").write_text("x")
            with self.assertRaisesRegex(ReleaseIndexError, "is not a map fragment"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_symlinked_fragment_is_refused(self) -> None:
        """A symlink's target may be outside the repository, so its digest
        would be of content the release does not carry."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            (checkout / "content/maps/linked.json").symlink_to(
                self._fragment_path(checkout)
            )
            with self.assertRaisesRegex(ReleaseIndexError, "is a symlink"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_changed_authority_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            authority = checkout / "native/server-profile.json"
            authority.write_bytes(authority.read_bytes() + b"\n")
            with self.assertRaisesRegex(ReleaseIndexError, "identity does not match"):
                validate_release_index(
                    checkout,
                    served_files(ROOT, load_profile(ROOT)),
                )

    def test_changed_served_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            changed = checkout / "arena/loader.js"
            changed.parent.mkdir(parents=True, exist_ok=True)
            changed.write_bytes(b"changed\n")
            expected = served_files(ROOT, load_profile(ROOT))
            expected["loader.js"] = {**expected["loader.js"], "source": changed}
            with self.assertRaisesRegex(ReleaseIndexError, "has another identity"):
                validate_release_index(checkout, expected)

    def test_missing_authority_role_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            del index["authorities"]["projectLicense"]
            self._write_index(path, index)
            with self.assertRaisesRegex(ReleaseIndexError, "exact authority role set"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_repointed_authority_role_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            index["authorities"]["projectLicense"] = dict(
                index["authorities"]["browserLicenseClosure"]
            )
            self._write_index(path, index)
            with self.assertRaisesRegex(ReleaseIndexError, "must name LICENSE"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_literal_compatibility_drift_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            index["compatibility"]["engineCommit"] = "0" * 40
            self._write_index(path, index)
            with self.assertRaisesRegex(ReleaseIndexError, "does not match its authorities"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_cross_authority_server_image_drift_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            resource_path = checkout / "records/wp11-server-resources.json"
            resource = json.loads(resource_path.read_text(encoding="utf-8"))
            resource["release"]["serverImageId"] = "sha256:" + "0" * 64
            resource_path.write_text(
                json.dumps(resource, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._refresh_authority(checkout, index, "resourceMeasurement")
            self._write_index(path, index)
            with self.assertRaisesRegex(ReleaseIndexError, "does not match its authorities"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    # ------------------------------------------------------------------
    # The member-level half of the client-to-server payload binding.
    # ------------------------------------------------------------------

    def _provenance(self, checkout: Path) -> tuple[Path, dict]:
        path = checkout / "provenance/arena-web-ffa-content.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _write_provenance(self, checkout: Path, index: dict, record: dict) -> None:
        path, _ = self._provenance(checkout)
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._refresh_authority(checkout, index, "contentMemberProvenance")

    def _member(self, record: dict, archive: str, path: str) -> dict:
        for entry in record["archives"]:
            if entry["path"].endswith(archive):
                for member in entry["members"]:
                    if member["path"] == path:
                        return member
        raise AssertionError(f"{path} is not in {archive}")

    def test_the_committed_set_shares_members_the_rule_is_about(self) -> None:
        """The rule has subject matter: without shared members it proves nothing."""
        _path, record = self._provenance(ROOT)
        seen: dict[str, set[str]] = {}
        for archive in record["archives"]:
            for member in archive["members"]:
                seen.setdefault(member["path"], set()).add(member["sha256"])
        shared = {path for path, digests in seen.items() if len(digests) == 1}
        duplicated = sum(
            1
            for path in seen
            if sum(
                1
                for archive in record["archives"]
                if any(member["path"] == path for member in archive["members"])
            )
            > 1
        )
        self.assertGreater(duplicated, 1)
        self.assertIn("COPYING", shared)

    def test_a_member_with_two_digests_across_archives_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            _, record = self._provenance(checkout)
            member = self._member(record, "-map-oa_pvomit.pk3", "COPYING")
            member["sha256"] = "0" * 64
            self._write_provenance(checkout, index, record)
            self._write_index(path, index)
            with self.assertRaisesRegex(
                ReleaseIndexError, "would resolve different bytes"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_an_exemption_that_exempts_nothing_is_refused(self) -> None:
        """A rule whose one exception has gone inert has stopped describing
        the pack, and the next member to diverge would inherit the silence."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            _, record = self._provenance(checkout)
            notices = [
                member
                for archive in record["archives"]
                for member in archive["members"]
                if member["path"] == "NOTICE-arena-web.txt"
            ]
            self.assertGreater(len(notices), 1)
            for member in notices:
                member["sha256"] = notices[0]["sha256"]
            self._write_provenance(checkout, index, record)
            self._write_index(path, index)
            with self.assertRaisesRegex(
                ReleaseIndexError, "no longer describes the pack"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_member_record_for_an_unpublished_archive_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            _, record = self._provenance(checkout)
            record["archives"].append(
                {
                    "path": "baseq3/arena-web-ffa-map-invented.pk3",
                    "members": [],
                    "sources": [],
                }
            )
            self._write_provenance(checkout, index, record)
            self._write_index(path, index)
            with self.assertRaisesRegex(
                ReleaseIndexError, "the content manifest publishes"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_server_archive_the_client_never_verifies_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            server_path = checkout / "provenance/arena-web-server.json"
            server = json.loads(server_path.read_text(encoding="utf-8"))
            server["artifacts"].append(
                {
                    "path": "opt/arena-web/arena/zz-extra.pk3",
                    "sha256": "0" * 64,
                    "size": 1,
                }
            )
            server["artifacts"].sort(key=lambda item: item["path"])
            server_path.write_text(
                json.dumps(server, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self._refresh_authority(checkout, index, "serverManifest")
            self._write_index(path, index)
            with self.assertRaisesRegex(
                ReleaseIndexError, "the release publishes"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    # ------------------------------------------------------------------
    # The measured per-map figures.
    # ------------------------------------------------------------------

    def _resources(self, checkout: Path) -> tuple[Path, dict]:
        path = checkout / "records/map-resource-measurements.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_an_edited_measurement_record_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            record_path, record = self._resources(checkout)
            record["maps"]["oa_pvomit"]["peakHunkBytes"] += 1
            record_path.write_text(
                json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self._write_index(path, index)
            with self.assertRaisesRegex(
                ReleaseIndexError, "is not records/map-resource-measurements.json"
            ):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def _rechain_content_manifest(self, checkout: Path, index: dict) -> None:
        """Re-derive every identity that follows the content manifest.

        A hand-edited manifest cannot be tested in isolation: its digest is the
        server manifest's declared input, which is the resource record's
        declared manifest, which is a `compatibility` member. Updating only the
        manifest fails on the first link and would leave the check under test
        unreached — so the fixture makes the release *consistent* and wrong,
        which is the state a bypassed build actually produces.
        """
        self._refresh_authority(checkout, index, "contentManifest")
        content_identity = "sha256:" + index["authorities"]["contentManifest"]["sha256"]
        index["compatibility"]["contentManifestIdentity"] = content_identity

        server_path = checkout / index["authorities"]["serverManifest"]["path"]
        server = json.loads(server_path.read_text(encoding="utf-8"))
        for item in server["inputs"]:
            if item["id"] == "arena-web-ffa-content":
                item["identity"] = content_identity
        server_path.write_text(
            json.dumps(server, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._refresh_authority(checkout, index, "serverManifest")
        server_identity = "sha256:" + index["authorities"]["serverManifest"]["sha256"]
        index["compatibility"]["serverManifestIdentity"] = server_identity

        resource_path = checkout / index["authorities"]["resourceMeasurement"]["path"]
        resource = json.loads(resource_path.read_text(encoding="utf-8"))
        resource["release"]["serverArtifactManifest"] = server_identity
        resource_path.write_text(
            json.dumps(resource, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._refresh_authority(checkout, index, "resourceMeasurement")

    def test_the_rechained_fixture_is_otherwise_accepted(self) -> None:
        """So the refusal below is the figure and not the plumbing."""
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            self._rechain_content_manifest(checkout, index)
            self._write_index(path, index)
            validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))

    def test_a_manifest_figure_the_record_did_not_measure_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = self._minimal_checkout(directory)
            path, index = self._index(checkout)
            manifest_path = checkout / index["authorities"]["contentManifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for artifact in manifest["artifacts"]:
                if artifact.get("map") == "oa_pvomit":
                    artifact["peakHunkBytes"] += 1
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            self._rechain_content_manifest(checkout, index)
            self._write_index(path, index)
            with self.assertRaisesRegex(ReleaseIndexError, "measured"):
                validate_release_index(checkout, served_files(ROOT, load_profile(ROOT)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
