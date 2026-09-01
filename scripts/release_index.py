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

# The game directory inside the server image, as native/server-profile.json
# names it and scripts/arena_server.py derives the image tree from.
SERVER_GAME_DIRECTORY = "opt/arena-web/arena"

# Where the per-map recipe fragments live, and the content-manifest input id
# prefix under which each one enters the release identity.
MAP_FRAGMENT_DIRECTORY = "content/maps"
MAP_FRAGMENT_INPUT_PREFIX = "arena-web-map-"

# How many hex characters of an artifact's own SHA-256 its served name carries.
SERVED_DIGEST_PREFIX_LENGTH = 16
AUTHORITY_PATHS = {
    "baseline": "locks/baseline.json",
    "browserAcceptance": "scripts/accept-host-lifecycle.py",
    "browserBuild": "scripts/build-browser.sh",
    "browserLicenseClosure": "docs/wp1-build-evidence.md",
    "browserManifest": "manifests/browser-client.json",
    "browserProfile": "arena/game-profile.json",
    "contentAssembly": "scripts/build-content-pack.sh",
    "contentLicenseClosure": "docs/wp3-content-closure.md",
    "contentManifest": "provenance/arena-web-ffa-content-manifest.json",
    "contentMemberProvenance": "provenance/arena-web-ffa-content.json",
    "contentRecipe": "content/pack-recipe.json",
    "integrationHandoff": "docs/wp11-integration-handoff.md",
    "projectLicense": "LICENSE",
    "relayProfile": "arena/relay-profile.json",
    "resourceMeasurement": "records/wp11-server-resources.json",
    "serverAssembly": "scripts/build-server-image.sh",
    "serverContainer": "native/server.Containerfile",
    "serverManifest": "provenance/arena-web-server.json",
    "serverProfile": "native/server-profile.json",
    "serverVerification": "scripts/verify-server-image.py",
}


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


def _json_authority(root: Path, authorities: dict[str, Any], role: str) -> dict[str, Any]:
    source = root / authorities[role]["path"]
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"authorities.{role}: cannot read JSON authority: {error}")
    if not isinstance(value, dict):
        _fail(f"authorities.{role}: JSON authority must be an object")
    return value


def _inputs(manifest: dict[str, Any], what: str) -> dict[str, str]:
    raw = manifest.get("inputs")
    if not isinstance(raw, list):
        _fail(f"{what}.inputs: must be an array")
    result: dict[str, str] = {}
    for number, item in enumerate(raw):
        if not isinstance(item, dict):
            _fail(f"{what}.inputs[{number}]: must be an object")
        identifier = item.get("id")
        identity = item.get("identity")
        if not isinstance(identifier, str) or not isinstance(identity, str):
            _fail(f"{what}.inputs[{number}]: has no string id/identity")
        if identifier in result:
            _fail(f"{what}.inputs: duplicate id {identifier}")
        result[identifier] = identity
    return result


def _artifact_identity(manifest: dict[str, Any], path: str, what: str) -> str:
    raw = manifest.get("artifacts")
    if not isinstance(raw, list):
        _fail(f"{what}.artifacts: must be an array")
    matches = [item for item in raw if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1 or not isinstance(matches[0].get("sha256"), str):
        _fail(f"{what}.artifacts: must name {path} exactly once")
    digest = matches[0]["sha256"]
    if not SHA256.fullmatch(digest):
        _fail(f"{what}.artifacts: {path} has no SHA-256 identity")
    return f"sha256:{digest}"


def _map_fragment_inputs(content_inputs: dict[str, str]) -> dict[str, str]:
    return {
        identifier[len(MAP_FRAGMENT_INPUT_PREFIX) :]: identity
        for identifier, identity in content_inputs.items()
        if identifier.startswith(MAP_FRAGMENT_INPUT_PREFIX)
    }


def _check_map_fragments(root: Path, content_inputs: dict[str, str]) -> None:
    """The per-map recipe fragments, both ways, against the content manifest.

    The fragments decide what each map archive holds. The root recipe does not
    list them — that would put the map set inside the base archive's own
    selection input and move the base's bytes whenever a map was added — so the
    content manifest's inputs are where they enter the release identity, and
    this is the check that keeps that set closed: an enumerated fragment that is
    missing, a fragment on disk that is not enumerated, and a digest that does
    not match are all failures.
    """
    declared = _map_fragment_inputs(content_inputs)
    directory = root / MAP_FRAGMENT_DIRECTORY
    on_disk: set[str] = set()
    if directory.is_dir():
        for item in sorted(directory.iterdir()):
            # Every entry counts, not only the well-formed ones: ignoring a
            # stray file here would make this validator and the build disagree
            # about what the directory means, and the build is the stricter of
            # the two. A symlink is refused outright — its target may be
            # outside the repository, so the digest recorded for it would be of
            # content the release does not contain.
            if item.is_symlink():
                _fail(
                    f"authorities.contentManifest: {MAP_FRAGMENT_DIRECTORY}/"
                    f"{item.name} is a symlink"
                )
            if not item.is_file() or not item.name.endswith(".json"):
                _fail(
                    f"authorities.contentManifest: {MAP_FRAGMENT_DIRECTORY}/"
                    f"{item.name} is not a map fragment"
                )
            on_disk.add(item.name[: -len(".json")])
    if on_disk != set(declared):
        undeclared = sorted(on_disk - set(declared))
        missing = sorted(set(declared) - on_disk)
        _fail(
            f"authorities.contentManifest: map fragments do not match "
            f"{MAP_FRAGMENT_DIRECTORY} (undeclared {undeclared}, missing {missing})"
        )
    for name in sorted(declared):
        source = root / MAP_FRAGMENT_DIRECTORY / f"{name}.json"
        digest, _size = _identity(source)
        if declared[name] != f"sha256:{digest}":
            _fail(
                f"authorities.contentManifest: {MAP_FRAGMENT_DIRECTORY}/{name}.json "
                "is not the fragment the manifest records"
            )


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
        # A content archive is served `immutable` for a year under a name that
        # carries its own digest. A name published with a stale hash over
        # current bytes would throw in the loader with no recovery path, so the
        # name is checked against the bytes rather than trusted.
        if expected["kind"] == "artifact" and expected.get("hashedName"):
            short = digest[:SERVED_DIGEST_PREFIX_LENGTH]
            if f"-{short}." not in item["path"].rsplit("/", 1)[-1]:
                _fail(
                    f"{INDEX_PATH}.servedFiles: {item['path']} does not carry its "
                    f"own digest {short}"
                )

    authorities = index["authorities"]
    if not isinstance(authorities, dict) or set(authorities) != set(AUTHORITY_PATHS):
        _fail(f"{INDEX_PATH}.authorities: must name the exact authority role set")
    if list(authorities) != sorted(AUTHORITY_PATHS):
        _fail(f"{INDEX_PATH}.authorities: roles must be path-bound and sorted")
    for role in sorted(AUTHORITY_PATHS):
        value = authorities[role]
        item = _entry(value, f"authorities.{role}")
        if item["path"] != AUTHORITY_PATHS[role]:
            _fail(
                f"authorities.{role}: must name {AUTHORITY_PATHS[role]}, "
                f"not {item['path']}"
            )
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

    baseline = _json_authority(root, authorities, "baseline")
    browser_manifest = _json_authority(root, authorities, "browserManifest")
    content_manifest = _json_authority(root, authorities, "contentManifest")
    server_manifest = _json_authority(root, authorities, "serverManifest")
    resource_measurement = _json_authority(root, authorities, "resourceMeasurement")

    engine = baseline.get("engine")
    if not isinstance(engine, dict):
        _fail("authorities.baseline: engine must be an object")
    engine_commit = engine.get("commit")
    if not isinstance(engine_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", engine_commit):
        _fail("authorities.baseline: engine.commit is not a Git commit")
    baseline_identity = f"sha256:{authorities['baseline']['sha256']}"
    browser_identity = f"sha256:{authorities['browserManifest']['sha256']}"
    content_identity = f"sha256:{authorities['contentManifest']['sha256']}"
    server_identity = f"sha256:{authorities['serverManifest']['sha256']}"
    engine_identity = f"git:{engine_commit}"

    for role, manifest in (
        ("browserManifest", browser_manifest),
        ("contentManifest", content_manifest),
        ("serverManifest", server_manifest),
    ):
        if manifest.get("baselineIdentity") != baseline_identity:
            _fail(f"authorities.{role}: baseline identity drift")
    browser_inputs = _inputs(browser_manifest, "authorities.browserManifest")
    content_inputs = _inputs(content_manifest, "authorities.contentManifest")
    server_inputs = _inputs(server_manifest, "authorities.serverManifest")
    if browser_inputs.get("ioq3") != engine_identity:
        _fail("authorities.browserManifest: engine input drift")
    if content_inputs.get("ioq3") != engine_identity:
        _fail("authorities.contentManifest: engine input drift")
    if content_inputs.get("arena-web") != f"sha256:{authorities['contentRecipe']['sha256']}":
        _fail("authorities.contentManifest: recipe input drift")
    expected_server_links = {
        "arena-web-browser-client": browser_identity,
        "arena-web-ffa-content": content_identity,
        "ioq3": engine_identity,
    }
    for identifier, identity in expected_server_links.items():
        if server_inputs.get(identifier) != identity:
            _fail(f"authorities.serverManifest: {identifier} input drift")

    content_recipe = _json_authority(root, authorities, "contentRecipe")
    _check_map_fragments(root, content_inputs)
    base_pack = content_recipe.get("basePackPath")
    map_template = content_recipe.get("mapPackTemplate")
    if not isinstance(base_pack, str) or not isinstance(map_template, str):
        _fail("authorities.contentRecipe: has no base pack path or map template")
    packs = [base_pack] + [
        map_template.format(map=name)
        for name in sorted(_map_fragment_inputs(content_inputs))
    ]
    # Every archive the client is handed must be the archive the server runs,
    # byte for byte. With sv_pure 0 and cl_allowDownload 0 on both profiles the
    # engine performs no content-agreement check at all, so this comparison is
    # the *only* thing binding the two sides together.
    for pack in packs:
        client_identity = _artifact_identity(
            content_manifest, pack, "authorities.contentManifest"
        )
        server_identity_for_pack = _artifact_identity(
            server_manifest,
            f"{SERVER_GAME_DIRECTORY}/{pack.rsplit('/', 1)[-1]}",
            "authorities.serverManifest",
        )
        if server_identity_for_pack != client_identity:
            _fail(f"authorities.serverManifest: {pack} payload drift")
    # `contentPayloadIdentity` names the *base* archive. The map archives are
    # covered transitively, through `contentManifestIdentity`, and by the
    # equality above.
    content_payload_identity = _artifact_identity(
        content_manifest, base_pack, "authorities.contentManifest"
    )

    release = resource_measurement.get("release")
    if not isinstance(release, dict):
        _fail("authorities.resourceMeasurement: release must be an object")
    if release.get("engineCommit") != engine_commit:
        _fail("authorities.resourceMeasurement: engine commit drift")
    if release.get("serverArtifactManifest") != server_identity:
        _fail("authorities.resourceMeasurement: server manifest drift")
    server_image_id = release.get("serverImageId")
    if not isinstance(server_image_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", server_image_id
    ):
        _fail("authorities.resourceMeasurement: server image ID is invalid")

    expected_compatibility = {
        "baselineIdentity": baseline_identity,
        "browserManifestIdentity": browser_identity,
        "contentManifestIdentity": content_identity,
        "contentPayloadIdentity": content_payload_identity,
        "engineCommit": engine_commit,
        "serverImageId": server_image_id,
        "serverManifestIdentity": server_identity,
    }
    if compatibility != expected_compatibility:
        _fail(f"{INDEX_PATH}.compatibility: does not match its authorities")
    return path
