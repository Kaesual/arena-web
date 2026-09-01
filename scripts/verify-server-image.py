#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inspect the built dedicated-server image and emit its evidence.

What a reviewer needs to trust is what the distributed bytes are, so every check
here reads the built image rather than the build context:

1. the image contains exactly the content set the native profile declares, each
   file with the SHA-256 and byte length of the committed manifest entry or, for
   the server binary, of the build this repository produced;
2. the image adds nothing else: its whole filesystem, minus that declared set
   and minus the paths a container runtime injects, is byte-for-byte the pinned
   runtime base's filesystem;
3. every per-package ``/usr/share/doc/*/copyright`` file the base carries is
   still there, unchanged, and the base carries exactly as many of them as
   ``native/server-profile.json`` pins. ``preserve-copyright-files`` is a
   recorded redistribution obligation of the baseline's runtime-base record, and
   this is where it is discharged rather than asserted — the pinned count is
   what stops a mis-reading from being discharged against itself.
4. the complete OCI runtime configuration is the narrow public contract: exact
   platform, user, environment, entrypoint, empty command, workdir, UDP port and
   provenance labels, with no blanket aggregate licence label.

The artifact manifest it writes is the committed identity of the image content;
``provenance/arena-web-server.json`` is a copy of it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena_server import (  # noqa: E402
    ArenaServerError,
    file_sha256,
    load_profile,
    server_binary_path,
    server_tree_files,
)
from metadata import (  # noqa: E402
    ARTIFACT_SCHEMA,
    MetadataError,
    _canonical_json_identity,
    _load_json,
    validate_artifact_manifest,
    validate_baseline,
)
from native_toolchain import load_package_lock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

PRODUCER_NAME = "arena-web scripts/build-server-image.sh"

# The baseline inputs an accepted server image must declare. The engine commit
# is the source, the native builder base is what compiled it, and the runtime
# base is what the image ships.
SERVER_REQUIRED_BASELINE_INPUT_IDS = (
    "ioq3",
    "native-builder-base",
    "server-runtime-base",
)

# Paths a container runtime injects into every container. They are not part of
# any image and would otherwise show up as a difference from the base.
RUNTIME_INJECTED = (
    "/etc/hostname",
    "/etc/hosts",
    "/etc/resolv.conf",
)

# The listing is taken with one shell command so that the base and the built
# image are read exactly the same way. `-xdev` keeps it on the image's own
# filesystem, the pseudo-filesystems and the runtime's own tmpfs mounts are
# pruned by path, and the records are NUL-separated so that no file name can
# split a record.
LISTING_COMMAND = r"""
set -eu
find / -xdev \
  \( -path /proc -o -path /sys -o -path /dev -o -path /run -o -path /tmp \) -prune -o \
  -printf '%y %m %s %p\0'
"""

DIGEST_COMMAND = r"""
set -eu
for path in "$@"; do
  printf '%s %s\n' "$(sha256sum "${path}" | cut -d' ' -f1)" "${path}"
done
"""

# `find -L` on purpose. Two of the base's 78 package documentation directories
# are symlinks to another package's directory — `libgcc-s1` and `libstdc++6`
# both point at `gcc-14-base` — so a plain `find` would not descend into them
# and would silently verify 76 of the 78 copyright files the baseline's
# `preserve-copyright-files` obligation covers.
COPYRIGHT_COMMAND = r"""
set -eu
LC_ALL=C find -L /usr/share/doc -mindepth 2 -maxdepth 2 -name copyright -type f -print \
  | LC_ALL=C sort \
  | while read -r path; do
      printf '%s %s\n' "$(sha256sum "${path}" | cut -d' ' -f1)" "${path}"
    done
"""


class ImageVerificationError(RuntimeError):
    """The built image is not the image the profile and the baseline describe."""


def _inspect_image(runtime: str, image: str) -> dict[str, Any]:
    result = subprocess.run(
        [runtime, "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ImageVerificationError(
            f"inspecting OCI configuration for {image} failed: "
            f"{result.stderr.strip() or result.returncode}"
        )
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ImageVerificationError(
            f"inspecting OCI configuration for {image} returned invalid JSON"
        ) from error
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ImageVerificationError(
            f"inspecting OCI configuration for {image} did not return exactly one image"
        )
    return records[0]


def _verify_image_configuration(
    inspected: dict[str, Any],
    *,
    engine_commit: str,
    baseline_identity: str,
    producer_commit: str,
) -> dict[str, Any]:
    expected_config = {
        "Entrypoint": ["/opt/arena-web/ioq3ded"],
        "Env": [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME=/var/lib/arena",
        ],
        "ExposedPorts": {"27960/udp": {}},
        "Labels": {
            "com.kaesual.arena-web.baseline-identity": baseline_identity,
            "com.kaesual.arena-web.engine-commit": engine_commit,
            "com.kaesual.arena-web.producer-commit": producer_commit,
            "org.opencontainers.image.title": "arena-web dedicated server",
        },
        "User": "65534:65534",
        "WorkingDir": "/opt/arena-web",
    }
    actual_platform = {
        "architecture": inspected.get("Architecture"),
        "os": inspected.get("Os"),
    }
    expected_platform = {"architecture": "amd64", "os": "linux"}
    if actual_platform != expected_platform:
        raise ImageVerificationError(
            f"image platform is {actual_platform}, expected {expected_platform}"
        )
    if inspected.get("Created") != "1970-01-01T00:00:00Z":
        raise ImageVerificationError("image creation time is not the reproducible Unix epoch")
    if inspected.get("ManifestType") != "application/vnd.oci.image.manifest.v1+json":
        raise ImageVerificationError("image is not an OCI image manifest")
    actual_config = inspected.get("Config")
    if actual_config != expected_config:
        raise ImageVerificationError(
            "image OCI configuration differs from the exact runtime contract: "
            f"got {actual_config!r}, expected {expected_config!r}"
        )
    return {
        "configuration": expected_config,
        "created": inspected["Created"],
        "manifestType": inspected["ManifestType"],
        "platform": expected_platform,
    }


def _run_in_image(runtime: str, image: str, script: str, *arguments: str) -> str:
    command = [
        runtime,
        "run",
        "--rm",
        "--cap-drop",
        "all",
        "--network",
        "none",
        "--platform",
        "linux/amd64",
        "--pull",
        "never",
        "--read-only",
        "--security-opt",
        "label=disable",
        "--security-opt",
        "no-new-privileges",
        # Both images are read by the same identity on purpose: the server image
        # declares an unprivileged USER, and a listing taken as that user could
        # not descend into the base's root-only directories, which would look
        # like a difference between the two filesystems.
        "--user",
        "0:0",
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        script,
        "sh",
        *arguments,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ImageVerificationError(
            f"inspecting {image} failed: {result.stderr.strip() or result.returncode}"
        )
    return result.stdout


def _listing(runtime: str, image: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for record in _run_in_image(runtime, image, LISTING_COMMAND).split("\0"):
        if not record:
            continue
        kind, mode, size, path = record.split(" ", 3)
        if path in RUNTIME_INJECTED:
            continue
        entries[path] = f"{kind} {mode} {size}"
    if not entries:
        raise ImageVerificationError(f"{image}: produced an empty filesystem listing")
    return entries


def _digests(runtime: str, image: str, paths: list[str]) -> dict[str, str]:
    output = _run_in_image(runtime, image, DIGEST_COMMAND, *paths)
    digests: dict[str, str] = {}
    for line in output.splitlines():
        digest, path = line.split(" ", 1)
        digests[path] = digest
    missing = sorted(set(paths) - set(digests))
    if missing:
        raise ImageVerificationError(f"{image}: could not digest {missing}")
    return digests


def _copyright_files(runtime: str, image: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for line in _run_in_image(runtime, image, COPYRIGHT_COMMAND).splitlines():
        digest, path = line.split(" ", 1)
        files[path] = digest
    if not files:
        raise ImageVerificationError(f"{image}: carries no per-package copyright file")
    return files


def _expected_image_content(
    repo_root: Path, profile: dict[str, Any], binary_digest: str, binary_size: int
) -> dict[str, dict[str, Any]]:
    """Absolute image path -> expected identity, for everything the image adds."""
    expected: dict[str, dict[str, Any]] = {
        server_binary_path(profile): {
            "sha256": binary_digest,
            "size": binary_size,
            "role": "dedicated-server-binary",
        }
    }
    prefix = profile["gameDirectory"].rstrip("/")
    for relative, entry in server_tree_files(repo_root, profile).items():
        if entry["kind"] == "artifact":
            expected[f"{prefix}/{relative}"] = {
                "sha256": entry["sha256"],
                "size": entry["size"],
                "role": f"{entry['manifest']}-artifact",
            }
        else:
            source = entry["source"]
            expected[f"{prefix}/{relative}"] = {
                "sha256": file_sha256(source),
                "size": source.stat().st_size,
                "role": "product-configuration",
            }
    return expected


def build_manifest(
    baseline: dict[str, Any],
    content: dict[str, dict[str, Any]],
    producer_commit: str,
    *,
    toolchain_identity: str,
    repo_root: Path,
) -> dict[str, Any]:
    """The artifact manifest of the server image's content set."""
    engine_manifest_identity = _canonical_json_identity(
        _load_json(repo_root / "manifests/browser-client.json")
    )
    content_manifest_identity = _canonical_json_identity(
        _load_json(repo_root / "provenance/arena-web-ffa-content-manifest.json")
    )
    native_builder = next(
        tool for tool in baseline["tools"] if tool["id"] == "native-builder-base"
    )
    runtime_base = next(
        image
        for image in baseline["redistributedProductImages"]
        if image["id"] == "server-runtime-base"
    )
    inputs = [
        {
            "id": "arena-web-browser-client",
            "identity": engine_manifest_identity,
            "kind": "artifact-manifest",
        },
        {
            "id": "arena-web-ffa-content",
            "identity": content_manifest_identity,
            "kind": "artifact-manifest",
        },
        {
            "id": "ioq3",
            "identity": f"git:{baseline['engine']['commit']}",
            "kind": "git",
        },
        {
            "id": "native-builder-base",
            "identity": native_builder["immutableRef"],
            "kind": "oci-image",
        },
        {
            "id": "native-toolchain-packages",
            "identity": f"sha256:{toolchain_identity}",
            "kind": "archive",
        },
        {
            "id": "server-runtime-base",
            "identity": runtime_base["immutableRef"],
            "kind": "oci-image",
        },
    ]
    manifest = {
        "$schema": ARTIFACT_SCHEMA,
        "artifacts": [
            {
                "path": path.lstrip("/"),
                "sha256": entry["sha256"],
                "size": entry["size"],
            }
            for path, entry in sorted(content.items())
        ],
        "baselineIdentity": _canonical_json_identity(baseline),
        "baselineInputIds": sorted(SERVER_REQUIRED_BASELINE_INPUT_IDS),
        "digestAlgorithm": "sha256",
        "formatVersion": 1,
        "inputs": sorted(inputs, key=lambda item: item["id"]),
        "producer": {"commit": producer_commit, "name": PRODUCER_NAME},
    }
    validate_artifact_manifest(
        manifest,
        "generated server image manifest",
        baseline=baseline,
        required_baseline_input_ids=SERVER_REQUIRED_BASELINE_INPUT_IDS,
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="the built server image")
    parser.add_argument("--producer-commit", required=True)
    parser.add_argument(
        "--report", type=Path, required=True, help="where to write image-content.json"
    )
    parser.add_argument(
        "--manifest", type=Path, help="where to write the artifact manifest"
    )
    parser.add_argument("--runtime", default=None, help="container runtime")
    parser.add_argument(
        "--server-binary",
        type=Path,
        default=REPO_ROOT / "build/native-server/tree/Release/ioq3ded",
    )
    arguments = parser.parse_args()

    runtime = arguments.runtime or os.environ.get("CONTAINER_RUNTIME", "podman")

    try:
        baseline = validate_baseline(
            _load_json(REPO_ROOT / "locks/baseline.json"), "locks/baseline.json"
        )
        profile = load_profile(REPO_ROOT)
        runtime_base = next(
            image
            for image in baseline["redistributedProductImages"]
            if image["id"] == "server-runtime-base"
        )["immutableRef"]
        if not arguments.server_binary.is_file():
            raise ImageVerificationError(
                f"{arguments.server_binary} does not exist; build it first"
            )
        expected = _expected_image_content(
            REPO_ROOT,
            profile,
            file_sha256(arguments.server_binary),
            arguments.server_binary.stat().st_size,
        )

        baseline_identity = _canonical_json_identity(baseline)
        image_configuration = _verify_image_configuration(
            _inspect_image(runtime, arguments.tag),
            engine_commit=baseline["engine"]["commit"],
            baseline_identity=baseline_identity,
            producer_commit=arguments.producer_commit,
        )

        base_listing = _listing(runtime, runtime_base)
        image_listing = _listing(runtime, arguments.tag)

        added = sorted(set(image_listing) - set(base_listing))
        removed = sorted(set(base_listing) - set(image_listing))
        changed = sorted(
            path
            for path in set(image_listing) & set(base_listing)
            if image_listing[path] != base_listing[path]
        )
        # The parent directories of the added content are new too, and they are
        # directories rather than distributed bytes.
        added_files = [path for path in added if image_listing[path].startswith("f ")]
        added_other = [path for path in added if not image_listing[path].startswith("f ")]
        unexpected_directories = [
            path
            for path in added_other
            if not image_listing[path].startswith("d ")
            or not any(
                declared.startswith(f"{path}/") for declared in expected
            )
        ]
        if unexpected_directories:
            raise ImageVerificationError(
                f"the image adds entries the profile does not declare: {unexpected_directories}"
            )
        if sorted(added_files) != sorted(expected):
            raise ImageVerificationError(
                "the image content set is not the declared one: "
                f"added {sorted(added_files)}, declared {sorted(expected)}"
            )
        if removed:
            raise ImageVerificationError(f"the image removes base paths: {removed}")
        if changed:
            raise ImageVerificationError(f"the image changes base paths: {changed}")

        digests = _digests(runtime, arguments.tag, sorted(expected))
        for path, entry in sorted(expected.items()):
            actual = digests[path]
            size = int(image_listing[path].split(" ")[2])
            if actual != entry["sha256"] or size != entry["size"]:
                raise ImageVerificationError(
                    f"{path} is sha256:{actual} {size} bytes, expected "
                    f"sha256:{entry['sha256']} {entry['size']} bytes"
                )

        base_copyright = _copyright_files(runtime, runtime_base)
        # The count is required of the *base* before the two are compared.
        # Comparing image with base proves nothing if the reading itself is
        # wrong: a `find` without -L misses the two documentation directories
        # that are symlinks, and both sides would then agree on 76 of 78.
        expected_copyright = profile["runtimeBaseCopyrightFiles"]
        if len(base_copyright) != expected_copyright:
            raise ImageVerificationError(
                f"the runtime base carries {len(base_copyright)} per-package "
                f"copyright files, the profile pins {expected_copyright}"
            )
        image_copyright = _copyright_files(runtime, arguments.tag)
        if image_copyright != base_copyright:
            missing = sorted(set(base_copyright) - set(image_copyright))
            modified = sorted(
                path
                for path in set(base_copyright) & set(image_copyright)
                if base_copyright[path] != image_copyright[path]
            )
            raise ImageVerificationError(
                "the image does not preserve the runtime base's copyright files "
                f"(missing {missing}, modified {modified})"
            )

        toolchain_identity = load_package_lock(REPO_ROOT)["identity"]
        manifest = build_manifest(
            baseline,
            expected,
            arguments.producer_commit,
            toolchain_identity=toolchain_identity,
            repo_root=REPO_ROOT,
        )
    except (ArenaServerError, ImageVerificationError, MetadataError) as error:
        print(f"server image refused: {error}", file=sys.stderr)
        return 1

    report = {
        "baselineIdentity": baseline_identity,
        "content": {
            path: {
                "role": entry["role"],
                "sha256": entry["sha256"],
                "size": entry["size"],
            }
            for path, entry in sorted(expected.items())
        },
        "copyrightFiles": len(image_copyright),
        "engineCommit": baseline["engine"]["commit"],
        "image": arguments.tag,
        "oci": image_configuration,
        "runtimeBase": runtime_base,
        "serverArguments": profile["serverArguments"],
        "toolchainPackageLockIdentity": toolchain_identity,
    }
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_path = arguments.manifest or arguments.report.parent / "artifact-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"verified {len(expected)} image content files in {arguments.tag}")
    print(f"preserved {len(image_copyright)} per-package copyright files from the base")
    print(f"wrote {arguments.report}")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
