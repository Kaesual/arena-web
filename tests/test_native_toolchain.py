# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from native_toolchain import (  # noqa: E402
    LOCK_PATH,
    NativeToolchainError,
    load_package_lock,
    package_file_name,
    package_url,
    parse_package_lock,
    verify_package_directory,
)

SNAPSHOT = "https://snapshot.ubuntu.com/ubuntu/20260824T000000Z"

MINIMAL = "\n".join(
    (
        "# a comment",
        f"snapshot {SNAPSHOT}",
        "suite noble",
        "component main",
        "request make",
        "package libc6 2.39-0ubuntu8.8 " + "a" * 64 + " 100 pool/main/g/glibc/libc6_2.39-0ubuntu8.8_amd64.deb",
        "package make 4.3-4.1build2 " + "b" * 64 + " 200 pool/main/m/make-dfsg/make_4.3-4.1build2_amd64.deb",
    )
)


def without(line_prefix: str) -> str:
    return "\n".join(
        line for line in MINIMAL.splitlines() if not line.startswith(line_prefix)
    )


class ParseTests(unittest.TestCase):
    def test_minimal_lock_parses(self) -> None:
        lock = parse_package_lock(MINIMAL)
        self.assertEqual(lock["snapshot"], SNAPSHOT)
        self.assertEqual(lock["suites"], ["noble"])
        self.assertEqual(lock["components"], ["main"])
        self.assertEqual(lock["requests"], ["make"])
        self.assertEqual([item["name"] for item in lock["packages"]], ["libc6", "make"])
        self.assertEqual(lock["packages"][0]["size"], 100)

    def test_package_url_and_file_name(self) -> None:
        lock = parse_package_lock(MINIMAL)
        package = lock["packages"][1]
        self.assertEqual(package_file_name(package), "make_4.3-4.1build2_amd64.deb")
        self.assertEqual(
            package_url(lock, package),
            f"{SNAPSHOT}/pool/main/m/make-dfsg/make_4.3-4.1build2_amd64.deb",
        )

    def test_a_moving_archive_is_rejected(self) -> None:
        text = MINIMAL.replace(SNAPSHOT, "https://archive.ubuntu.com/ubuntu")
        with self.assertRaisesRegex(NativeToolchainError, "immutable Ubuntu snapshot"):
            parse_package_lock(text)

    def test_a_second_snapshot_is_rejected(self) -> None:
        text = MINIMAL + f"\nsnapshot {SNAPSHOT}"
        with self.assertRaisesRegex(NativeToolchainError, "second snapshot"):
            parse_package_lock(text)

    def test_a_missing_snapshot_is_rejected(self) -> None:
        with self.assertRaisesRegex(NativeToolchainError, "no snapshot archive"):
            parse_package_lock(without("snapshot "))

    def test_an_unknown_directive_is_rejected(self) -> None:
        with self.assertRaisesRegex(NativeToolchainError, "unknown directive"):
            parse_package_lock(MINIMAL + "\nmirror https://example.invalid/")

    def test_unsorted_packages_are_rejected(self) -> None:
        lines = MINIMAL.splitlines()
        lines[-2], lines[-1] = lines[-1], lines[-2]
        with self.assertRaisesRegex(NativeToolchainError, "sorted by name"):
            parse_package_lock("\n".join(lines))

    def test_a_duplicate_package_is_rejected(self) -> None:
        text = MINIMAL + "\n" + MINIMAL.splitlines()[-1]
        with self.assertRaisesRegex(NativeToolchainError, "pinned only once"):
            parse_package_lock(text)

    def test_two_packages_may_not_share_a_digest(self) -> None:
        text = MINIMAL.replace("b" * 64, "a" * 64)
        with self.assertRaisesRegex(NativeToolchainError, "share one digest"):
            parse_package_lock(text)

    def test_a_short_digest_is_rejected(self) -> None:
        text = MINIMAL.replace("b" * 64, "b" * 63)
        with self.assertRaisesRegex(NativeToolchainError, "not a SHA-256 digest"):
            parse_package_lock(text)

    def test_a_zero_size_is_rejected(self) -> None:
        text = MINIMAL.replace(" 200 pool/", " 0 pool/")
        with self.assertRaisesRegex(NativeToolchainError, "positive byte count"):
            parse_package_lock(text)

    def test_a_pool_path_outside_pool_is_rejected(self) -> None:
        text = MINIMAL.replace("pool/main/m/make-dfsg/", "elsewhere/")
        with self.assertRaisesRegex(NativeToolchainError, "must be under 'pool/'"):
            parse_package_lock(text)

    def test_a_traversing_pool_path_is_rejected(self) -> None:
        text = MINIMAL.replace("pool/main/m/make-dfsg/", "pool/../../etc/")
        with self.assertRaisesRegex(
            NativeToolchainError, "relative and contained|under 'pool/'"
        ):
            parse_package_lock(text)

    def test_a_pool_file_naming_another_package_is_rejected(self) -> None:
        text = MINIMAL.replace("make_4.3-4.1build2_amd64.deb", "gcc_4.3-4.1build2_amd64.deb")
        with self.assertRaisesRegex(NativeToolchainError, "pool file names package"):
            parse_package_lock(text)

    def test_a_pool_file_naming_another_version_is_rejected(self) -> None:
        text = MINIMAL.replace("make_4.3-4.1build2_amd64.deb", "make_4.4_amd64.deb")
        with self.assertRaisesRegex(NativeToolchainError, "pool file names version"):
            parse_package_lock(text)

    def test_an_epoch_is_accepted_in_either_spelling(self) -> None:
        for file_version in ("13.2.0-7ubuntu1", "4%3a13.2.0-7ubuntu1"):
            with self.subTest(file_version=file_version):
                text = "\n".join(
                    (
                        f"snapshot {SNAPSHOT}",
                        "suite noble",
                        "component main",
                        "request cpp",
                        "package cpp 4:13.2.0-7ubuntu1 "
                        + "c" * 64
                        + f" 10 pool/main/g/gcc-defaults/cpp_{file_version}_amd64.deb",
                    )
                )
                parse_package_lock(text)

    def test_a_foreign_architecture_is_rejected(self) -> None:
        text = MINIMAL.replace("make_4.3-4.1build2_amd64.deb", "make_4.3-4.1build2_arm64.deb")
        with self.assertRaisesRegex(NativeToolchainError, "not installable here"):
            parse_package_lock(text)

    def test_an_unresolved_request_is_rejected(self) -> None:
        text = MINIMAL.replace("request make", "request cmake")
        with self.assertRaisesRegex(NativeToolchainError, "not in the resolved set"):
            parse_package_lock(text)

    def test_unsorted_requests_are_rejected(self) -> None:
        text = MINIMAL.replace("request make", "request make\nrequest libc6")
        with self.assertRaisesRegex(NativeToolchainError, "request rows must be sorted"):
            parse_package_lock(text)


class VerifyDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory))
        self.lock = parse_package_lock(self._lock_for(b"one", b"two"))

    def _lock_for(self, first: bytes, second: bytes) -> str:
        return "\n".join(
            (
                f"snapshot {SNAPSHOT}",
                "suite noble",
                "component main",
                "request make",
                "package libc6 2.39-0ubuntu8.8 "
                + hashlib.sha256(first).hexdigest()
                + f" {len(first)} pool/main/g/glibc/libc6_2.39-0ubuntu8.8_amd64.deb",
                "package make 4.3-4.1build2 "
                + hashlib.sha256(second).hexdigest()
                + f" {len(second)} pool/main/m/make-dfsg/make_4.3-4.1build2_amd64.deb",
            )
        )

    def _write(self, first: bytes = b"one", second: bytes = b"two") -> None:
        (self.directory / "libc6_2.39-0ubuntu8.8_amd64.deb").write_bytes(first)
        (self.directory / "make_4.3-4.1build2_amd64.deb").write_bytes(second)

    def test_a_matching_directory_is_accepted(self) -> None:
        self._write()
        self.assertEqual(
            verify_package_directory(self.lock, self.directory),
            [
                "libc6_2.39-0ubuntu8.8_amd64.deb",
                "make_4.3-4.1build2_amd64.deb",
            ],
        )

    def test_a_missing_package_is_rejected(self) -> None:
        self._write()
        (self.directory / "make_4.3-4.1build2_amd64.deb").unlink()
        with self.assertRaisesRegex(NativeToolchainError, "missing pinned packages"):
            verify_package_directory(self.lock, self.directory)

    def test_an_unpinned_file_is_rejected(self) -> None:
        self._write()
        (self.directory / "extra_1_amd64.deb").write_bytes(b"three")
        with self.assertRaisesRegex(NativeToolchainError, "unpinned files"):
            verify_package_directory(self.lock, self.directory)

    def test_a_modified_package_is_rejected(self) -> None:
        self._write(second=b"tw0")
        with self.assertRaisesRegex(NativeToolchainError, "the lock pins sha256:"):
            verify_package_directory(self.lock, self.directory)

    def test_a_truncated_package_is_rejected(self) -> None:
        self._write(second=b"t")
        with self.assertRaisesRegex(NativeToolchainError, "the lock pins 3"):
            verify_package_directory(self.lock, self.directory)

    def test_a_symlink_is_rejected(self) -> None:
        self._write()
        target = self.directory / "make_4.3-4.1build2_amd64.deb"
        target.unlink()
        target.symlink_to(self.directory / "libc6_2.39-0ubuntu8.8_amd64.deb")
        with self.assertRaisesRegex(NativeToolchainError, "not a regular file"):
            verify_package_directory(self.lock, self.directory)

    def test_a_missing_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(NativeToolchainError, "not a package directory"):
            verify_package_directory(self.lock, self.directory / "absent")


class CommittedLockTests(unittest.TestCase):
    """The lock this repository actually pins the native toolchain with."""

    def setUp(self) -> None:
        self.lock = load_package_lock(ROOT)

    def test_the_committed_lock_is_valid(self) -> None:
        self.assertTrue(self.lock["packages"])
        self.assertTrue(self.lock["snapshot"].startswith("https://snapshot.ubuntu.com/"))

    def test_the_identity_is_the_file_digest(self) -> None:
        expected = hashlib.sha256((ROOT / LOCK_PATH).read_bytes()).hexdigest()
        self.assertEqual(self.lock["identity"], expected)

    def test_every_requested_package_is_pinned(self) -> None:
        names = {package["name"] for package in self.lock["packages"]}
        for request in self.lock["requests"]:
            self.assertIn(request, names)

    def test_the_toolchain_can_compile_and_capture(self) -> None:
        # The requested set is a decision, so it is asserted rather than left to
        # whatever a later regeneration happens to ask for.
        self.assertEqual(
            self.lock["requests"],
            [
                "cmake",
                "gcc",
                "libgl1-mesa-dri",
                "libglx-mesa0",
                "libsdl2-dev",
                "make",
                "tcpdump",
                "xvfb",
            ],
        )


if __name__ == "__main__":
    unittest.main()
