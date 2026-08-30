# SPDX-License-Identifier: GPL-2.0-or-later
"""The pinned package set the WP5 native toolchain image installs.

WP0 pins the native builder *base* by platform digest and says in as many
words that the pin alone is not permission to use an unversioned package
repository during an accepted build. That base carries no compiler, so WP5 has
to add one, and this module owns the contract that keeps the addition as
immutable as the base:

* the archive is one Canonical snapshot at an exact timestamp, not a suite that
  moves under the build;
* every package is pinned by name, version, size and SHA-256, taken from that
  snapshot's GPG-signed index at resolution time;
* the accepted build installs from a locally verified directory with the
  network off, so it resolves nothing and downloads nothing.

`scripts/resolve-native-packages.sh` regenerates the lock; everything else
treats it as immutable input and fails closed on anything it cannot verify.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

CHUNK_SIZE = 1024 * 1024

LOCK_PATH = "locks/native-toolchain-packages.conf"

# Only an immutable snapshot archive is admissible. `archive.ubuntu.com` and a
# bare suite name are exactly the moving references WP0 forbids, so the
# accepted shape is spelled out rather than merely preferred.
SNAPSHOT_URL_RE = re.compile(
    r"\Ahttps://snapshot\.ubuntu\.com/ubuntu/[0-9]{8}T[0-9]{6}Z\Z"
)
PACKAGE_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9+.-]*\Z")
# Debian version grammar, restricted to what an Ubuntu binary package uses.
PACKAGE_VERSION_RE = re.compile(r"\A[0-9][A-Za-z0-9.+:~-]*\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
SUITE_RE = re.compile(r"\A[a-z][a-z0-9-]*\Z")
COMPONENT_RE = re.compile(r"\A[a-z]+\Z")

DIRECTIVES = ("component", "package", "request", "snapshot", "suite")

# The architectures a `linux/amd64` toolchain may install from.
ALLOWED_DEB_ARCHITECTURES = ("all", "amd64")


class NativeToolchainError(ValueError):
    """Raised when the pinned toolchain lock violates its fail-closed contract."""


def _fail(message: str) -> None:
    raise NativeToolchainError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _package_file_name(pool_path: str) -> str:
    return PurePosixPath(pool_path).name


def _validate_pool_path(pool_path: str, name: str, version: str) -> None:
    if pool_path != PurePosixPath(pool_path).as_posix():
        _fail(f"package {name}: pool path {pool_path!r} is not normalised")
    if pool_path.startswith("/") or ".." in PurePosixPath(pool_path).parts:
        _fail(f"package {name}: pool path {pool_path!r} must be relative and contained")
    if not pool_path.startswith("pool/"):
        _fail(f"package {name}: pool path {pool_path!r} must be under 'pool/'")
    file_name = _package_file_name(pool_path)
    if not file_name.endswith(".deb"):
        _fail(f"package {name}: {file_name!r} is not a .deb")
    stem = file_name[: -len(".deb")]
    fields = stem.split("_")
    if len(fields) != 3:
        _fail(f"package {name}: {file_name!r} is not name_version_arch.deb")
    file_package, file_version, architecture = fields
    if file_package != name:
        _fail(f"package {name}: pool file names package {file_package!r}")
    # A pool file name normally drops the epoch entirely, and apt
    # percent-encodes the separator when it keeps it. Both spellings are the
    # same version; anything else is not.
    upstream_version = version.split(":", 1)[-1]
    if file_version.replace("%3a", ":") not in (version, upstream_version):
        _fail(f"package {name}: pool file names version {file_version!r}, not {version!r}")
    if architecture not in ALLOWED_DEB_ARCHITECTURES:
        _fail(f"package {name}: architecture {architecture!r} is not installable here")


def parse_package_lock(text: str) -> dict[str, Any]:
    """Parse and fully validate the committed toolchain package lock."""
    snapshot: str | None = None
    suites: list[str] = []
    components: list[str] = []
    requests: list[str] = []
    packages: list[dict[str, Any]] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        if line != raw_line or line.strip() != line:
            _fail(f"line {number}: has leading or trailing whitespace")
        fields = line.split(" ")
        directive = fields[0]
        if directive not in DIRECTIVES:
            _fail(f"line {number}: unknown directive {directive!r}")
        if directive == "snapshot":
            if len(fields) != 2:
                _fail(f"line {number}: snapshot takes exactly one URL")
            if snapshot is not None:
                _fail(f"line {number}: a second snapshot archive is not a pin")
            if not SNAPSHOT_URL_RE.fullmatch(fields[1]):
                _fail(
                    f"line {number}: {fields[1]!r} is not an immutable Ubuntu snapshot URL"
                )
            snapshot = fields[1]
        elif directive == "suite":
            if len(fields) != 2 or not SUITE_RE.fullmatch(fields[1]):
                _fail(f"line {number}: suite takes exactly one suite name")
            suites.append(fields[1])
        elif directive == "component":
            if len(fields) != 2 or not COMPONENT_RE.fullmatch(fields[1]):
                _fail(f"line {number}: component takes exactly one component name")
            components.append(fields[1])
        elif directive == "request":
            if len(fields) != 2 or not PACKAGE_NAME_RE.fullmatch(fields[1]):
                _fail(f"line {number}: request takes exactly one package name")
            requests.append(fields[1])
        else:
            if len(fields) != 6:
                _fail(
                    f"line {number}: package takes name, version, sha256, size and pool path"
                )
            _, name, version, sha256, size, pool_path = fields
            if not PACKAGE_NAME_RE.fullmatch(name):
                _fail(f"line {number}: {name!r} is not a package name")
            if not PACKAGE_VERSION_RE.fullmatch(version):
                _fail(f"line {number}: {version!r} is not a package version")
            if not SHA256_RE.fullmatch(sha256):
                _fail(f"line {number}: {sha256!r} is not a SHA-256 digest")
            if not size.isdigit() or int(size) <= 0:
                _fail(f"line {number}: {size!r} is not a positive byte count")
            _validate_pool_path(pool_path, name, version)
            packages.append(
                {
                    "name": name,
                    "poolPath": pool_path,
                    "sha256": sha256,
                    "size": int(size),
                    "version": version,
                }
            )

    if snapshot is None:
        _fail("no snapshot archive is pinned")
    for label, values in (
        ("suite", suites),
        ("component", components),
        ("request", requests),
    ):
        if not values:
            _fail(f"no {label} is recorded")
        if values != sorted(values):
            _fail(f"{label} rows must be sorted")
        if len(set(values)) != len(values):
            _fail(f"{label} rows must be unique")
    if not packages:
        _fail("no package is pinned")
    names = [package["name"] for package in packages]
    if names != sorted(names):
        _fail("package rows must be sorted by name")
    if len(set(names)) != len(names):
        _fail("a package may be pinned only once")
    digests = [package["sha256"] for package in packages]
    if len(set(digests)) != len(digests):
        _fail("two packages share one digest, which cannot be two different files")
    file_names = [_package_file_name(package["poolPath"]) for package in packages]
    if len(set(file_names)) != len(file_names):
        _fail("two packages share one file name")
    missing = sorted(set(requests) - set(names))
    if missing:
        _fail(f"requested packages are not in the resolved set: {missing}")

    return {
        "components": components,
        "packages": packages,
        "requests": requests,
        "snapshot": snapshot,
        "suites": suites,
    }


def load_package_lock(repo_root: Path) -> dict[str, Any]:
    path = repo_root / LOCK_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail(f"{LOCK_PATH}: cannot be read: {error}")
    lock = parse_package_lock(text)
    # The lock's own bytes identify the toolchain: a package change moves it,
    # which is what the derived image is tagged with.
    lock["identity"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return lock


def package_url(lock: dict[str, Any], package: dict[str, Any]) -> str:
    return f"{lock['snapshot']}/{package['poolPath']}"


def package_file_name(package: dict[str, Any]) -> str:
    return _package_file_name(package["poolPath"])


def verify_package_directory(lock: dict[str, Any], directory: Path) -> list[str]:
    """Verify a fetched package directory against the lock, fail-closed.

    Returns the sorted file names on success. An extra file is an error too: the
    accepted build installs everything in this directory, so an unpinned .deb
    lying beside the pinned ones would be installed without ever being reviewed.
    """
    if not directory.is_dir():
        _fail(f"{directory}: is not a package directory")
    expected = {package_file_name(package): package for package in lock["packages"]}
    present = sorted(item.name for item in directory.iterdir())
    for item in directory.iterdir():
        if item.is_symlink() or not item.is_file():
            _fail(f"{directory}/{item.name}: is not a regular file")
    unexpected = sorted(set(present) - set(expected))
    if unexpected:
        _fail(f"{directory}: contains unpinned files {unexpected}")
    missing = sorted(set(expected) - set(present))
    if missing:
        _fail(f"{directory}: is missing pinned packages {missing}")
    for file_name in sorted(expected):
        package = expected[file_name]
        path = directory / file_name
        size = path.stat().st_size
        if size != package["size"]:
            _fail(
                f"{file_name}: is {size} bytes, the lock pins {package['size']}"
            )
        digest = file_sha256(path)
        if digest != package["sha256"]:
            _fail(
                f"{file_name}: is sha256:{digest}, the lock pins sha256:{package['sha256']}"
            )
    return sorted(expected)
