# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the deterministic artifact manifest of a browser engine build.

The manifest is the committed evidence of an accepted build: the heavy
JavaScript, WebAssembly, QVM and generated shell/configuration files stay out
of Git, their identities do not.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from metadata import (
    ARTIFACT_SCHEMA,
    ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
    MetadataError,
    _canonical_json_identity,
    _fail,
    validate_artifact_manifest,
)

PRODUCER_NAME = "arena-web scripts/build-browser.sh"

# ioquake3's generated web configuration names the retail Quake III pak files,
# but no accepted arena-web build may read or emit one of them.
RETAIL_GAME_DATA_SUFFIXES = (".pk3",)

CHUNK_SIZE = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def collect_artifacts(root: Path) -> list[dict[str, Any]]:
    """Return every distributable build output below `root`, sorted by path."""
    if not root.is_dir():
        _fail(str(root), "is not a build output directory")
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            if path.is_symlink():
                _fail(f"{root}/{relative}", "is a symlinked directory")
            continue
        if path.is_symlink() or not path.is_file():
            _fail(f"{root}/{relative}", "is not a regular file")
        if path.suffix.lower() in RETAIL_GAME_DATA_SUFFIXES:
            _fail(f"{root}/{relative}", "is retail game data and must not be built")
        artifacts.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
        )
    if not artifacts:
        _fail(str(root), "contains no build output")
    return artifacts


def baseline_inputs(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact WP0 inputs an accepted browser build must declare."""
    builder = None
    for tool in baseline["tools"]:
        if tool["id"] == "emscripten-builder":
            builder = tool
    if builder is None:
        raise MetadataError("baseline.tools: does not record 'emscripten-builder'")
    return [
        {
            "id": "emscripten-builder",
            "identity": builder["immutableRef"],
            "kind": "oci-image",
        },
        {
            "id": "ioq3",
            "identity": f"git:{baseline['engine']['commit']}",
            "kind": "git",
        },
    ]


def build_manifest(
    baseline: dict[str, Any],
    artifacts: list[dict[str, Any]],
    producer_commit: str,
    *,
    extra_inputs: list[dict[str, Any]] | None = None,
    producer_name: str = PRODUCER_NAME,
) -> dict[str, Any]:
    inputs = baseline_inputs(baseline) + list(extra_inputs or [])
    manifest = {
        "$schema": ARTIFACT_SCHEMA,
        "artifacts": artifacts,
        "baselineIdentity": _canonical_json_identity(baseline),
        "baselineInputIds": sorted(ARTIFACT_REQUIRED_BASELINE_INPUT_IDS),
        "digestAlgorithm": "sha256",
        "formatVersion": 1,
        "inputs": sorted(inputs, key=lambda item: item["id"]),
        "producer": {"commit": producer_commit, "name": producer_name},
    }
    validate_artifact_manifest(
        manifest,
        "generated artifact manifest",
        baseline=baseline,
        required_baseline_input_ids=ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
    )
    return manifest
