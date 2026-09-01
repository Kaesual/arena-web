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
        self.assertEqual(len(index["servedFiles"]), 18)
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
