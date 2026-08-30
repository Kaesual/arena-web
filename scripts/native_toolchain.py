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
INDEX_LOCK_PATH = "locks/native-toolchain-indexes.conf"

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

INDEX_DIRECTIVES = ("index", "snapshot")

# The signed index files the package lock's per-package digests came out of.
# `InRelease` is the clearsigned root the Ubuntu archive key covers; `Release`
# plus `Release.gpg` is the detached spelling of the same thing; each `Packages`
# file is what a package digest was read from.
SUITE_INDEX_KINDS = ("InRelease", "Release", "Release.gpg")
COMPONENT_INDEX_KINDS = ("Packages.gz", "Packages.xz")
INDEX_KINDS = SUITE_INDEX_KINDS + COMPONENT_INDEX_KINDS

# The row that has to be present for a suite to be verifiable at all: without
# the clearsigned root, nothing below it is anchored to the archive key.
REQUIRED_SUITE_INDEX_KIND = "InRelease"

NO_COMPONENT = "-"

# The binary architecture the toolchain installs; the lock rejects any other.
INDEX_ARCHITECTURE = "binary-amd64"

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


def _canonical_index_path(suite: str, component: str, kind: str) -> str:
    if component == NO_COMPONENT:
        return f"dists/{suite}/{kind}"
    return f"dists/{suite}/{component}/{INDEX_ARCHITECTURE}/{kind}"


def parse_index_lock(text: str) -> dict[str, Any]:
    """Parse and fully validate the committed index sidecar.

    The sidecar is what makes the package lock's trust root re-checkable
    offline. It does not repeat the package digests and it is not a second
    source of truth for them: it records the exact signed index files those
    digests were read out of, so a reviewer can fetch them from the immutable
    snapshot, verify `InRelease` against the Ubuntu archive keyring and confirm
    the chain. Trusting that keyring is still required.
    """
    snapshot: str | None = None
    indexes: list[dict[str, Any]] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        if line != raw_line or line.strip() != line:
            _fail(f"index line {number}: has leading or trailing whitespace")
        fields = line.split(" ")
        directive = fields[0]
        if directive not in INDEX_DIRECTIVES:
            _fail(f"index line {number}: unknown directive {directive!r}")
        if directive == "snapshot":
            if len(fields) != 2:
                _fail(f"index line {number}: snapshot takes exactly one URL")
            if snapshot is not None:
                _fail(f"index line {number}: a second snapshot archive is not a pin")
            if not SNAPSHOT_URL_RE.fullmatch(fields[1]):
                _fail(
                    f"index line {number}: {fields[1]!r} is not an immutable "
                    "Ubuntu snapshot URL"
                )
            snapshot = fields[1]
            continue
        if len(fields) != 7:
            _fail(
                f"index line {number}: index takes suite, component, kind, "
                "sha256, size and path"
            )
        _, suite, component, kind, sha256, size, path = fields
        if not SUITE_RE.fullmatch(suite):
            _fail(f"index line {number}: {suite!r} is not a suite name")
        if component != NO_COMPONENT and not COMPONENT_RE.fullmatch(component):
            _fail(f"index line {number}: {component!r} is not a component name")
        if kind not in INDEX_KINDS:
            _fail(f"index line {number}: {kind!r} is not an index kind")
        if kind in SUITE_INDEX_KINDS and component != NO_COMPONENT:
            _fail(f"index line {number}: {kind} belongs to a suite, not a component")
        if kind in COMPONENT_INDEX_KINDS and component == NO_COMPONENT:
            _fail(f"index line {number}: {kind} needs a component")
        if not SHA256_RE.fullmatch(sha256):
            _fail(f"index line {number}: {sha256!r} is not a SHA-256 digest")
        if not size.isdigit() or int(size) <= 0:
            _fail(f"index line {number}: {size!r} is not a positive byte count")
        expected_path = _canonical_index_path(suite, component, kind)
        if path != expected_path:
            _fail(f"index line {number}: path must be {expected_path!r}, not {path!r}")
        indexes.append(
            {
                "component": component,
                "kind": kind,
                "path": path,
                "sha256": sha256,
                "size": int(size),
                "suite": suite,
            }
        )

    if snapshot is None:
        _fail("the index sidecar pins no snapshot archive")
    if not indexes:
        _fail("the index sidecar records no index")
    keys = [(item["suite"], item["component"], item["kind"]) for item in indexes]
    if len(set(keys)) != len(keys):
        _fail("an index may be recorded only once")
    if keys != sorted(keys):
        _fail("index rows must be sorted by suite, component and kind")
    return {"indexes": indexes, "snapshot": snapshot}


def verify_index_lock(package_lock: dict[str, Any], index_lock: dict[str, Any]) -> None:
    """Require the sidecar to describe the same archive the package lock used."""
    if index_lock["snapshot"] != package_lock["snapshot"]:
        _fail(
            "the index sidecar pins a different snapshot than the package lock: "
            f"{index_lock['snapshot']} vs {package_lock['snapshot']}"
        )
    suites = {item["suite"] for item in index_lock["indexes"]}
    if suites != set(package_lock["suites"]):
        _fail(
            "the index sidecar covers suites "
            f"{sorted(suites)}, the package lock resolved against "
            f"{package_lock['suites']}"
        )
    components = {
        item["component"]
        for item in index_lock["indexes"]
        if item["component"] != NO_COMPONENT
    }
    if components != set(package_lock["components"]):
        _fail(
            "the index sidecar covers components "
            f"{sorted(components)}, the package lock resolved against "
            f"{package_lock['components']}"
        )
    for suite in package_lock["suites"]:
        kinds = {
            item["kind"] for item in index_lock["indexes"] if item["suite"] == suite
        }
        if REQUIRED_SUITE_INDEX_KIND not in kinds:
            _fail(
                f"the index sidecar has no {REQUIRED_SUITE_INDEX_KIND} for "
                f"suite {suite!r}, so nothing anchors it to the archive key"
            )
        for component in package_lock["components"]:
            present = {
                item["kind"]
                for item in index_lock["indexes"]
                if item["suite"] == suite and item["component"] == component
            }
            if not present & set(COMPONENT_INDEX_KINDS):
                _fail(
                    "the index sidecar records no Packages file for "
                    f"{suite}/{component}, which is where the package digests "
                    "were read from"
                )


def load_index_lock(repo_root: Path, package_lock: dict[str, Any]) -> dict[str, Any]:
    path = repo_root / INDEX_LOCK_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail(f"{INDEX_LOCK_PATH}: cannot be read: {error}")
    index_lock = parse_index_lock(text)
    verify_index_lock(package_lock, index_lock)
    index_lock["identity"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return index_lock


def index_url(index_lock: dict[str, Any], index: dict[str, Any]) -> str:
    return f"{index_lock['snapshot']}/{index['path']}"


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
