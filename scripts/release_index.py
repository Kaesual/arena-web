# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict validator for the browser release index outside the served tree."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

INDEX_PATH = Path("release/browser-release.json")
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


class ReleaseIndexError(ValueError):
    pass


def _fail(message: str) -> None:
    raise ReleaseIndexError(message)


def _identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _exact(value: Any, keys: tuple[str, ...], what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(f"{what}: unexpected key set")
    return value


def _entry(value: Any, what: str) -> dict[str, Any]:
    entry = _exact(value, ("path", "sha256", "size"), what)
    path = entry["path"]
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or ".." in Path(path).parts
        or "\\" in path
    ):
        _fail(f"{what}.path: is not a safe relative path")
    if not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"]):
        _fail(f"{what}.sha256: is not a SHA-256 digest")
    if not isinstance(entry["size"], int) or entry["size"] <= 0:
        _fail(f"{what}.size: must be a positive integer")
    return entry


def validate_release_index(
    root: Path,
    expected_served: dict[str, dict[str, Any]],
    *,
    staged_root: Path | None = None,
) -> Path:
    path = root / INDEX_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"{INDEX_PATH}: cannot read release index: {error}")
    index = _exact(
        raw,
        (
            "authorities",
            "browserRoot",
            "compatibility",
            "digestAlgorithm",
            "formatVersion",
            "servedFiles",
        ),
        str(INDEX_PATH),
    )
    if index["formatVersion"] != 1 or index["digestAlgorithm"] != "sha256":
        _fail(f"{INDEX_PATH}: unsupported version or digest algorithm")
    if index["browserRoot"] != ".":
        _fail(f"{INDEX_PATH}.browserRoot: must be '.'")

    served = [_entry(item, f"servedFiles[{number}]") for number, item in enumerate(index["servedFiles"])]
    served_paths = [item["path"] for item in served]
    if served_paths != sorted(served_paths) or len(served_paths) != len(set(served_paths)):
        _fail(f"{INDEX_PATH}.servedFiles: must be unique and path-sorted")
    if set(served_paths) != set(expected_served):
        _fail(f"{INDEX_PATH}.servedFiles: does not name the exact served set")
    for item in served:
        expected = expected_served[item["path"]]
        if staged_root is not None:
            source = staged_root / item["path"]
            if not source.is_file():
                _fail(f"staged {item['path']}: is missing")
            digest, size = _identity(source)
        elif expected["kind"] == "artifact":
            digest, size = expected["sha256"], expected["size"]
        else:
            digest, size = _identity(expected["source"])
        if item["sha256"] != digest or item["size"] != size:
            _fail(f"{INDEX_PATH}.servedFiles: {item['path']} has another identity")

    authorities = index["authorities"]
    if not isinstance(authorities, dict) or not authorities:
        _fail(f"{INDEX_PATH}.authorities: must be a non-empty object")
    if list(authorities) != sorted(authorities):
        _fail(f"{INDEX_PATH}.authorities: roles must be sorted")
    for role, value in authorities.items():
        if not isinstance(role, str) or not role:
            _fail(f"{INDEX_PATH}.authorities: invalid role")
        item = _entry(value, f"authorities.{role}")
        source = root / item["path"]
        if not source.is_file() or source.resolve() == path.resolve():
            _fail(f"authorities.{role}: must name another committed file")
        if (item["sha256"], item["size"]) != _identity(source):
            _fail(f"authorities.{role}: identity does not match {item['path']}")

    compatibility = _exact(
        index["compatibility"],
        (
            "baselineIdentity",
            "browserManifestIdentity",
            "contentManifestIdentity",
            "contentPayloadIdentity",
            "engineCommit",
            "serverImageId",
            "serverManifestIdentity",
        ),
        f"{INDEX_PATH}.compatibility",
    )
    for name in (
        "baselineIdentity",
        "browserManifestIdentity",
        "contentManifestIdentity",
        "contentPayloadIdentity",
        "serverImageId",
        "serverManifestIdentity",
    ):
        if not isinstance(compatibility[name], str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", compatibility[name]
        ):
            _fail(f"compatibility.{name}: is not a SHA-256 identity")
    if not isinstance(compatibility["engineCommit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", compatibility["engineCommit"]
    ):
        _fail("compatibility.engineCommit: is not a Git commit")

    expected_compatibility = {
        "baselineIdentity": f"sha256:{authorities['baseline']['sha256']}",
        "browserManifestIdentity": f"sha256:{authorities['browserManifest']['sha256']}",
        "contentManifestIdentity": f"sha256:{authorities['contentManifest']['sha256']}",
        "contentPayloadIdentity": "sha256:ae244d1eb8948b17b4348bcf8617b86e2db68516bdb0d0616b29a9958b140664",
        "engineCommit": "596e56a6bf58f41e1ad9cc1685c7c11a75dba87a",
        "serverImageId": "sha256:c73ba3ee395d57f661d2a4884b287c7a638bbe7f25269169865fc18bc1c901bf",
        "serverManifestIdentity": f"sha256:{authorities['serverManifest']['sha256']}",
    }
    if compatibility != expected_compatibility:
        _fail(f"{INDEX_PATH}.compatibility: does not match its authorities")
    return path
