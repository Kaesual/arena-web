# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed checks for the immutable browser release index."""

from __future__ import annotations

import json
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
        for relative in sorted(paths):
            destination = checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        return checkout

    def test_committed_index_matches_exact_served_tree(self) -> None:
        expected = served_files(ROOT, load_profile(ROOT))
        validate_release_index(ROOT, expected)
        index = json.loads((ROOT / "release/browser-release.json").read_text())
        self.assertEqual(len(index["servedFiles"]), 17)
        self.assertEqual(
            [entry["path"] for entry in index["servedFiles"]], sorted(expected)
        )

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
