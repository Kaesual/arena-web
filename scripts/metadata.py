# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict, dependency-free validation for arena-web metadata formats."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

BASELINE_SCHEMA = "../schemas/baseline-lock.schema.json"
MEASUREMENT_SCHEMA = "../schemas/relay-measurement-vector.schema.json"
ARTIFACT_SCHEMA = (
    "https://kaesual.github.io/arena-web/schemas/artifact-manifest.schema.json"
)
CONTENT_SCHEMA = (
    "https://kaesual.github.io/arena-web/schemas/content-provenance.schema.json"
)

# A committed artifact manifest describes a generated engine build, so it must
# name both the engine source and the toolchain that produced it. Declaring
# only one of them would let a rebuilt artifact keep an identity that no longer
# says which compiler emitted it.
ARTIFACT_REQUIRED_BASELINE_INPUT_IDS = ("emscripten-builder", "ioq3")

# The lock distinguishes three license classes, and each has its own gate.
# `_validate_license` owns two of them: a build or test tool never leaves the
# workstation, so it may carry an aggregate reference that no product could use,
# while a product input is compiled or packaged into an arena-web artifact.
#
# The third class, a redistributed product image, is deliberately not one of
# these values. arena-web ships its third-party binaries unchanged inside a
# server image, so its gate is more than an expression and a distribution
# value: it also binds license evidence to the exact image bytes and demands
# the obligations that redistribution creates. That whole gate lives in
# `_validate_redistributed_image_license`, and `_validate_license` refuses the
# class outright so no caller can reach a partial pass through it.
PRODUCT_INPUT_LICENSE = "product-input"
TOOL_ONLY_LICENSE = "tool-only"

# The record kind that carries the third class in committed metadata.
REDISTRIBUTED_IMAGE_KIND = "redistributed-product-image"

TOOL_ONLY_DISTRIBUTIONS = {
    "build-only-not-redistributed",
    "build-tool-source-submodule-only",
    "test-only-not-redistributed",
}
REDISTRIBUTED_IMAGE_DISTRIBUTION = "product-image-redistributed"

# Shipping a distribution's binaries obliges arena-web to carry their notices
# and to leave the per-package copyright files the image already contains in
# place. `share-alike` is admitted because a base may carry share-alike content;
# it is never a substitute for the two obligations every such image has.
REDISTRIBUTED_IMAGE_OBLIGATIONS = {
    "license-notice",
    "preserve-copyright-files",
    "share-alike",
}
REDISTRIBUTED_IMAGE_REQUIRED_OBLIGATIONS = {
    "license-notice",
    "preserve-copyright-files",
}

# The copyleft packages in such an image oblige arena-web to make their complete
# corresponding source obtainable. Naming a public archive is the primary
# channel, but an archive somebody else operates is not by itself a discharge
# for a distributor, so `written-offer-on-request` records the obligation
# arena-web keeps in its own name.
CORRESPONDING_SOURCE_FORM = "distribution-source-archive"
CORRESPONDING_SOURCE_OBLIGATIONS = {
    "complete-corresponding-source",
    "public-archive-availability",
    "written-offer-on-request",
}
CORRESPONDING_SOURCE_REQUIRED_OBLIGATIONS = {
    "complete-corresponding-source",
    "public-archive-availability",
}

# Exactly one redistributed runtime base is reviewed: the one the WP5 dedicated
# server image is built from. The set is closed for the same reason the tool set
# is: a second runtime base is a licensing decision, not a build detail.
REQUIRED_REDISTRIBUTED_IMAGES = {"server-runtime-base": "server-runtime-base"}

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LICENSE_REF_RE = re.compile(r"^LicenseRef-[A-Za-z0-9.-]+$")
LICENSE_REF_TOKEN_RE = re.compile(r"LicenseRef-[A-Za-z0-9.-]+")
SOURCE_IDENTITY_RE = re.compile(r"^(?:git:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
OCI_IDENTITY_RE = re.compile(r"^[^@\s:]+(?:/[^@\s:]+)+@sha256:[0-9a-f]{64}$")
EVIDENCE_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

SUPPORTED_SCHEMA_KEYS = {
    "$comment",
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "const",
    "description",
    "enum",
    "format",
    "items",
    "maximum",
    "maxItems",
    "minLength",
    "minimum",
    "minItems",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
    "uniqueItems",
}


class MetadataError(ValueError):
    """Raised when committed metadata violates its fail-closed contract."""


def _fail(path: str, message: str) -> None:
    raise MetadataError(f"{path}: {message}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, f"must be an integer >= {minimum}")
    return value


def _exact_keys(value: dict[str, Any], expected: Iterable[str], path: str) -> None:
    expected_set = set(expected)
    actual_set = set(value)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unknown {extra}")
        _fail(path, "; ".join(details))


def _unique(values: list[Any], path: str) -> None:
    if len(values) != len(set(values)):
        _fail(path, "must not contain duplicates")


def _sorted_unique_strings(value: Any, path: str) -> list[str]:
    values = _array(value, path)
    for index, item in enumerate(values):
        _string(item, f"{path}[{index}]")
    _unique(values, path)
    if values != sorted(values):
        _fail(path, "must be sorted")
    return values


def _https_url(value: Any, path: str) -> str:
    url = _string(value, path)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username:
        _fail(path, "must be an absolute HTTPS URL without user information")
    return url


def _commit(value: Any, path: str) -> str:
    commit = _string(value, path)
    if not COMMIT_RE.fullmatch(commit):
        _fail(path, "must be a lowercase 40-character Git commit")
    return commit


def _sha256(value: Any, path: str) -> str:
    digest = _string(value, path)
    if not SHA256_RE.fullmatch(digest):
        _fail(path, "must be a lowercase 64-character SHA-256")
    return digest


def _sha256_digest(value: Any, path: str) -> str:
    digest = _string(value, path)
    if not SHA256_DIGEST_RE.fullmatch(digest):
        _fail(path, "must be sha256:<64 lowercase hex characters>")
    return digest


def _numeric_version(value: Any, path: str) -> tuple[int, ...]:
    version = _string(value, path)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        _fail(path, "must be a dotted numeric version")
    return tuple(int(part) for part in version.split("."))


def _relative_path(value: Any, path: str) -> str:
    candidate = _string(value, path)
    pure = PurePosixPath(candidate)
    if (
        pure.is_absolute()
        or candidate.startswith("./")
        or str(pure) != candidate
        or "\\" in candidate
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        _fail(path, "must be a normalized relative POSIX path")
    return candidate


def _load_json(path: Path) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(str(path), f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        raw_bytes = path.read_bytes()
        raw = raw_bytes.decode("utf-8", errors="strict")
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail(str(path), f"cannot read strict UTF-8 JSON: {error}")
    canonical = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if raw_bytes != canonical.encode("utf-8"):
        _fail(str(path), "must use canonical sorted, two-space-indented JSON")
    return value


def _canonical_json_identity(value: Any) -> str:
    encoded = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _license_identifiers(expression: str) -> set[str]:
    """Return the license-like identifiers from an SPDX-style expression."""
    return set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", expression))


def _resolve_local_ref(root_schema: dict[str, Any], reference: str, path: str) -> Any:
    if not reference.startswith("#/"):
        _fail(path, f"uses unsupported non-local schema reference {reference!r}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            _fail(path, f"cannot resolve schema reference {reference!r}")
        value = value[part]
    return value


def _check_supported_schema(schema: Any, path: str) -> None:
    record = _object(schema, path)
    unknown = sorted(set(record) - SUPPORTED_SCHEMA_KEYS)
    if unknown:
        _fail(path, f"uses unsupported JSON Schema keywords {unknown}")
    if "additionalProperties" in record and record["additionalProperties"] is not False:
        _fail(path, "supports additionalProperties only when it is false")
    for key in ("$defs", "properties"):
        if key in record:
            children = _object(record[key], f"{path}.{key}")
            for name, child in children.items():
                _check_supported_schema(child, f"{path}.{key}.{name}")
    if "items" in record:
        _check_supported_schema(record["items"], f"{path}.items")
    if "oneOf" in record:
        choices = _array(record["oneOf"], f"{path}.oneOf")
        if not choices:
            _fail(f"{path}.oneOf", "must not be empty")
        for index, choice in enumerate(choices):
            _check_supported_schema(choice, f"{path}.oneOf[{index}]")


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "array": isinstance(value, list),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }.get(expected, False)


def _validate_schema_instance(
    value: Any,
    schema: Any,
    root_schema: dict[str, Any],
    path: str,
) -> None:
    record = _object(schema, f"{path} (schema)")
    if "$ref" in record:
        referenced = _resolve_local_ref(
            root_schema,
            _string(record["$ref"], f"{path} (schema).$ref"),
            path,
        )
        _validate_schema_instance(value, referenced, root_schema, path)
    if "oneOf" in record:
        matches = 0
        for choice in record["oneOf"]:
            try:
                _validate_schema_instance(value, choice, root_schema, path)
            except MetadataError:
                continue
            matches += 1
        if matches != 1:
            _fail(
                path, f"must match exactly one schema alternative (matched {matches})"
            )
    if "type" in record:
        expected_type = _string(record["type"], f"{path} (schema).type")
        if not _schema_type_matches(value, expected_type):
            _fail(path, f"must have JSON Schema type {expected_type}")
    if "const" in record and value != record["const"]:
        _fail(path, f"must equal {record['const']!r}")
    if "enum" in record and value not in record["enum"]:
        _fail(path, f"must be one of {record['enum']!r}")
    if isinstance(value, str):
        if "minLength" in record and len(value) < record["minLength"]:
            _fail(path, f"must contain at least {record['minLength']} characters")
        if "pattern" in record and not re.search(record["pattern"], value):
            _fail(path, f"does not match schema pattern {record['pattern']!r}")
        if record.get("format") == "uri":
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                _fail(path, "must be an absolute URI")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in record and value < record["minimum"]:
            _fail(path, f"must be >= {record['minimum']}")
        if "maximum" in record and value > record["maximum"]:
            _fail(path, f"must be <= {record['maximum']}")
    if isinstance(value, list):
        if "minItems" in record and len(value) < record["minItems"]:
            _fail(path, f"must contain at least {record['minItems']} items")
        if "maxItems" in record and len(value) > record["maxItems"]:
            _fail(path, f"must contain at most {record['maxItems']} items")
        if record.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                _fail(path, "must contain unique items")
        if "items" in record:
            for index, item in enumerate(value):
                _validate_schema_instance(
                    item,
                    record["items"],
                    root_schema,
                    f"{path}[{index}]",
                )
    if isinstance(value, dict):
        properties = record.get("properties", {})
        required = record.get("required", [])
        for key in required:
            if key not in value:
                _fail(path, f"is missing required schema property {key!r}")
        if record.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                _fail(path, f"contains schema-unknown properties {unknown}")
        for key, child_schema in properties.items():
            if key in value:
                _validate_schema_instance(
                    value[key], child_schema, root_schema, f"{path}.{key}"
                )


def _validate_preferred_source(value: Any, path: str) -> None:
    record = _object(value, path)
    status = record.get("status")
    if status == "available":
        _exact_keys(record, ("revision", "status", "url"), path)
        _https_url(record["url"], f"{path}.url")
        revision = _string(record["revision"], f"{path}.revision")
        if not (COMMIT_RE.fullmatch(revision) or SHA256_DIGEST_RE.fullmatch(revision)):
            _fail(
                f"{path}.revision",
                "must be an exact Git commit or SHA-256 digest",
            )
    elif status == "unavailable":
        _exact_keys(record, ("reason", "status"), path)
        if len(_string(record["reason"], f"{path}.reason")) < 20:
            _fail(f"{path}.reason", "must explain why preferred source is unavailable")
    else:
        _fail(f"{path}.status", "must be 'available' or 'unavailable'")


def _validate_license_evidence(value: Any, path: str) -> dict[str, Any]:
    """Check the evidence shape every license record shares, whatever its class.

    Returns the record so a class gate can apply its own rules to it. This says
    nothing about whether the expression is admissible anywhere; that is the
    caller's gate, and there is no path through this function that grants one.
    """
    record = _object(value, path)
    evidence_identity = _string(
        record.get("evidenceIdentity"), f"{path}.evidenceIdentity"
    )
    if not SOURCE_IDENTITY_RE.fullmatch(evidence_identity):
        _fail(
            f"{path}.evidenceIdentity",
            "must pin the evidence as git:<commit> or sha256:<digest>",
        )
    expected_keys = {
        "distribution",
        "evidenceIdentity",
        "evidenceUrl",
        "expression",
    }
    if "evidencePath" in record:
        expected_keys.add("evidencePath")
    if evidence_identity.startswith("sha256:"):
        expected_keys.add("evidenceRetrievedAt")
    _exact_keys(record, expected_keys, path)
    _string(record["distribution"], f"{path}.distribution")
    _string(record["expression"], f"{path}.expression")
    _https_url(record["evidenceUrl"], f"{path}.evidenceUrl")
    if evidence_identity.startswith("sha256:"):
        retrieved_at = _string(
            record["evidenceRetrievedAt"], f"{path}.evidenceRetrievedAt"
        )
        if not EVIDENCE_DATE_RE.fullmatch(retrieved_at):
            _fail(f"{path}.evidenceRetrievedAt", "must be an ISO calendar date")
        try:
            retrieved_date = date.fromisoformat(retrieved_at)
        except ValueError:
            _fail(f"{path}.evidenceRetrievedAt", "must be a real calendar date")
        if retrieved_date > date.today():
            _fail(f"{path}.evidenceRetrievedAt", "must not be in the future")
    if "evidencePath" in record:
        _relative_path(record["evidencePath"], f"{path}.evidencePath")
    return record


def _validate_license(
    value: Any,
    path: str,
    product_allowed: set[str],
    product_refs: set[str],
    tool_refs: set[str],
    *,
    license_class: str,
) -> None:
    record = _validate_license_evidence(value, path)
    distribution = record["distribution"]
    expression = record["expression"]
    if license_class == PRODUCT_INPUT_LICENSE:
        if expression not in product_allowed and expression not in product_refs:
            _fail(f"{path}.expression", "is not an allowed product-input license")
        unregistered_refs = set(LICENSE_REF_TOKEN_RE.findall(expression)) - product_refs
        if unregistered_refs:
            _fail(
                f"{path}.expression",
                f"contains unregistered product LicenseRefs {sorted(unregistered_refs)}",
            )
        if distribution != "product-source-and-binaries":
            _fail(f"{path}.distribution", "must permit product distribution")
    elif license_class == TOOL_ONLY_LICENSE:
        if expression not in tool_refs:
            _fail(f"{path}.expression", "is not a registered tool-only LicenseRef")
        if distribution not in TOOL_ONLY_DISTRIBUTIONS:
            _fail(f"{path}.distribution", "must keep the tool out of distribution")
    else:
        # Deliberately including the redistributed-image class: its gate is
        # larger than this function and lives in one piece elsewhere, so
        # reaching it from here could only ever be a partial pass.
        _fail(path, f"is validated with an unknown license class {license_class!r}")


def _validate_redistributed_image_license(
    image: dict[str, Any],
    path: str,
    platform_digest: str,
    image_refs: set[str],
) -> None:
    """Apply the whole licensing gate of one redistributed product image.

    This is the third license class in one piece: the expression registry it
    must come from, the redistribution boundary it must declare, the binding of
    its license evidence to the exact image bytes, and the two obligation sets
    that redistributing somebody else's binaries creates. Keeping it whole is
    the point — a caller cannot take the expression check without the
    obligations, and `_validate_license` refuses the class outright.
    """
    license_path = f"{path}.license"
    record = _validate_license_evidence(image["license"], license_path)
    if record["expression"] not in image_refs:
        _fail(
            f"{license_path}.expression",
            "is not a registered redistributed-image LicenseRef",
        )
    if record["distribution"] != REDISTRIBUTED_IMAGE_DISTRIBUTION:
        _fail(
            f"{license_path}.distribution",
            "must record the redistributed product-image boundary",
        )
    # An aggregate expression says nothing on its own. The image carries the
    # per-package license evidence, so the record has to name where, and that
    # evidence has to be the bytes this record pins rather than a lookalike.
    if "evidencePath" not in record:
        _fail(license_path, "must name the license evidence the image itself carries")
    if record["evidenceIdentity"] != platform_digest:
        _fail(
            f"{license_path}.evidenceIdentity",
            "must bind the license evidence to the exact redistributed image",
        )
    obligations = _sorted_unique_strings(
        image["redistributionObligations"], f"{path}.redistributionObligations"
    )
    unknown_obligations = sorted(set(obligations) - REDISTRIBUTED_IMAGE_OBLIGATIONS)
    if unknown_obligations:
        _fail(
            f"{path}.redistributionObligations",
            f"contains unknown obligations {unknown_obligations}",
        )
    if not REDISTRIBUTED_IMAGE_REQUIRED_OBLIGATIONS.issubset(obligations):
        _fail(
            f"{path}.redistributionObligations",
            f"must include {sorted(REDISTRIBUTED_IMAGE_REQUIRED_OBLIGATIONS)} "
            "for a redistributed image",
        )
    # A build tool may explain an unavailable preferred source; an image whose
    # binaries arena-web hands on may not.
    _validate_preferred_source(image["preferredSource"], f"{path}.preferredSource")
    if image["preferredSource"]["status"] != "available":
        _fail(
            f"{path}.preferredSource.status",
            "must name an obtainable preferred source for a redistributed image",
        )
    source_path = f"{path}.correspondingSource"
    corresponding = _object(image["correspondingSource"], source_path)
    _exact_keys(corresponding, ("form", "obligations", "url"), source_path)
    if corresponding["form"] != CORRESPONDING_SOURCE_FORM:
        _fail(f"{source_path}.form", f"must be {CORRESPONDING_SOURCE_FORM!r}")
    _https_url(corresponding["url"], f"{source_path}.url")
    source_obligations = _sorted_unique_strings(
        corresponding["obligations"], f"{source_path}.obligations"
    )
    unknown_source_obligations = sorted(
        set(source_obligations) - CORRESPONDING_SOURCE_OBLIGATIONS
    )
    if unknown_source_obligations:
        _fail(
            f"{source_path}.obligations",
            f"contains unknown obligations {unknown_source_obligations}",
        )
    if not CORRESPONDING_SOURCE_REQUIRED_OBLIGATIONS.issubset(source_obligations):
        _fail(
            f"{source_path}.obligations",
            f"must include {sorted(CORRESPONDING_SOURCE_REQUIRED_OBLIGATIONS)} "
            "for a redistributed image",
        )


def _validate_redistributed_product_images(
    value: Any,
    path: str,
    image_refs: set[str],
    tools: list[dict[str, Any]],
) -> None:
    """Validate the images arena-web ships rather than only builds with.

    A build tool leaves nothing behind once the build ends; a base image that
    becomes part of a distributed server image does. These records therefore
    carry the same digest-pinned identity contract as an `oci-image` tool, plus
    three things a tool record has no place for: license evidence bound to the
    exact image bytes, what redistributing those bytes obliges, and the channel
    the complete corresponding source is obtainable from.

    This function owns the identity contract; the licensing gate is
    `_validate_redistributed_image_license`.
    """
    images = _array(value, path)
    if not images:
        _fail(path, "must not be empty")
    # An image the lock pins as a build-only tool has been reviewed on the
    # promise that it never reaches a distribution. Shipping the same bytes
    # under a second record would break that promise silently, so the two
    # populations are required to be disjoint by digest and by reference.
    tool_identity_owner: dict[str, str] = {}
    for tool in tools:
        if tool.get("kind") != "oci-image":
            continue
        for field in ("immutableRef", "indexDigest", "platformDigest"):
            tool_identity_owner[tool[field]] = tool["id"]
    image_ids: list[str] = []
    for index, raw_image in enumerate(images):
        image_path = f"{path}[{index}]"
        record = _object(raw_image, image_path)
        _exact_keys(
            record,
            (
                "correspondingSource",
                "humanTag",
                "id",
                "image",
                "immutableRef",
                "indexDigest",
                "kind",
                "license",
                "platform",
                "platformDigest",
                "preferredSource",
                "redistributionObligations",
                "role",
                "version",
            ),
            image_path,
        )
        image_ids.append(_string(record["id"], f"{image_path}.id"))
        if record["kind"] != REDISTRIBUTED_IMAGE_KIND:
            _fail(f"{image_path}.kind", f"must be {REDISTRIBUTED_IMAGE_KIND!r}")
        index_digest = _sha256_digest(
            record["indexDigest"], f"{image_path}.indexDigest"
        )
        platform_digest = _sha256_digest(
            record["platformDigest"], f"{image_path}.platformDigest"
        )
        if index_digest == platform_digest:
            _fail(
                f"{image_path}.platformDigest",
                "must differ from the multi-platform index digest",
            )
        image_name = _string(record["image"], f"{image_path}.image")
        if "@" in image_name or ":" in image_name.rsplit("/", 1)[-1]:
            _fail(f"{image_path}.image", "must be a repository name, not a reference")
        if record["immutableRef"] != f"{image_name}@{platform_digest}":
            _fail(f"{image_path}.immutableRef", "must be image@platformDigest")
        if record["platform"] != "linux/amd64":
            _fail(f"{image_path}.platform", "must be linux/amd64 for WP0")
        human_tag = _string(record["humanTag"], f"{image_path}.humanTag")
        version = _string(record["version"], f"{image_path}.version")
        if human_tag != version:
            _fail(f"{image_path}.humanTag", "must equal the descriptive version")
        _string(record["role"], f"{image_path}.role")
        for field in ("immutableRef", "indexDigest", "platformDigest"):
            owning_tool = tool_identity_owner.get(record[field])
            if owning_tool is not None:
                _fail(
                    f"{image_path}.{field}",
                    f"redistributes the image tool {owning_tool!r} pins as build-only",
                )
        _validate_redistributed_image_license(
            record, image_path, platform_digest, image_refs
        )
    _unique(image_ids, f"{path}[].id")
    if image_ids != sorted(image_ids):
        _fail(path, "must be sorted by id")
    if set(image_ids) != set(REQUIRED_REDISTRIBUTED_IMAGES):
        _fail(path, f"must contain exactly {sorted(REQUIRED_REDISTRIBUTED_IMAGES)}")
    for index, record in enumerate(images):
        if record["role"] != REQUIRED_REDISTRIBUTED_IMAGES[record["id"]]:
            _fail(f"{path}[{index}].role", "does not match the reviewed image role")


def validate_baseline(value: Any, path: str = "baseline") -> dict[str, Any]:
    baseline = _object(value, path)
    _exact_keys(
        baseline,
        (
            "$schema",
            "acceptancePlatform",
            "engine",
            "formatVersion",
            "licensePolicy",
            "redistributedProductImages",
            "relayTrust",
            "tools",
            "upstreamEvidence",
        ),
        path,
    )
    if baseline["$schema"] != BASELINE_SCHEMA or baseline["formatVersion"] != 1:
        _fail(path, "has an unsupported schema or formatVersion")

    policy = _object(baseline["licensePolicy"], f"{path}.licensePolicy")
    _exact_keys(
        policy,
        (
            "productInputAllowedExpressions",
            "productInputLicenseRefs",
            "redistributedImageLicenseRefs",
            "toolOnlyLicenseRefs",
        ),
        f"{path}.licensePolicy",
    )
    product_allowed = set(
        _sorted_unique_strings(
            policy["productInputAllowedExpressions"],
            f"{path}.licensePolicy.productInputAllowedExpressions",
        )
    )
    product_refs = set(
        _sorted_unique_strings(
            policy["productInputLicenseRefs"],
            f"{path}.licensePolicy.productInputLicenseRefs",
        )
    )
    tool_refs = set(
        _sorted_unique_strings(
            policy["toolOnlyLicenseRefs"],
            f"{path}.licensePolicy.toolOnlyLicenseRefs",
        )
    )
    image_refs = set(
        _sorted_unique_strings(
            policy["redistributedImageLicenseRefs"],
            f"{path}.licensePolicy.redistributedImageLicenseRefs",
        )
    )
    for field, values in (
        ("productInputLicenseRefs", product_refs),
        ("redistributedImageLicenseRefs", image_refs),
        ("toolOnlyLicenseRefs", tool_refs),
    ):
        if any(not LICENSE_REF_RE.fullmatch(item) for item in values):
            _fail(
                f"{path}.licensePolicy.{field}",
                "contains an invalid LicenseRef",
            )
    # Each gate admits its own references only. A reference registered for two
    # classes would let a redistributed aggregate pass as a build tool, or a
    # tool aggregate pass as something arena-web hands on.
    for left_field, left, right_field, right in (
        ("productInputLicenseRefs", product_refs, "toolOnlyLicenseRefs", tool_refs),
        (
            "productInputLicenseRefs",
            product_refs,
            "redistributedImageLicenseRefs",
            image_refs,
        ),
        (
            "redistributedImageLicenseRefs",
            image_refs,
            "toolOnlyLicenseRefs",
            tool_refs,
        ),
    ):
        if left & right:
            _fail(
                f"{path}.licensePolicy",
                f"must keep {left_field} and {right_field} disjoint",
            )

    engine = _object(baseline["engine"], f"{path}.engine")
    _exact_keys(
        engine,
        (
            "branch",
            "commit",
            "id",
            "kind",
            "licenseComponents",
            "preferredSource",
            "repository",
            "submodulePath",
            "treeClosure",
        ),
        f"{path}.engine",
    )
    if engine["kind"] != "git" or engine["id"] != "ioq3":
        _fail(f"{path}.engine", "must identify the ioq3 Git input")
    if engine["branch"] != "main":
        _fail(f"{path}.engine.branch", "must be main for the immutable WP0 pin")
    commit = _commit(engine["commit"], f"{path}.engine.commit")
    _https_url(engine["repository"], f"{path}.engine.repository")
    if (
        _relative_path(engine["submodulePath"], f"{path}.engine.submodulePath")
        != "ioq3"
    ):
        _fail(f"{path}.engine.submodulePath", "must be ioq3")
    license_components = _array(
        engine["licenseComponents"], f"{path}.engine.licenseComponents"
    )
    # These contracts describe the pinned *source tree*. Two of the roles below
    # are WP0 provisional claims that WP1 corrected by observing an accepted
    # build: `openal-headers` and `sdl-headers` are not inputs to the browser
    # artifact at all, because the Emscripten SDK supplies both. The corrected
    # mapping is the component table in docs/wp1-build-evidence.md; a component
    # absent from the final link is reported there, not erased from here.
    expected_component_contracts = {
        "curl-headers": (
            "emscripten-disabled-source",
            ("code/thirdparty/curl-8.15.0",),
        ),
        "ijg": ("browser-build-source", ("code/thirdparty/jpeg-9f",)),
        "ioq3-core": ("engine-core", ("code",)),
        "lcc-build-tool": ("qvm-build-tool", ("code/tools/lcc",)),
        "libogg": ("browser-build-source", ("code/thirdparty/libogg-1.3.6",)),
        "libvorbis": (
            "browser-build-source",
            ("code/thirdparty/libvorbis-1.3.7",),
        ),
        "minizip": (
            "browser-build-source",
            (
                "code/qcommon/ioapi.c",
                "code/qcommon/ioapi.h",
                "code/qcommon/unzip.c",
                "code/qcommon/unzip.h",
            ),
        ),
        "mumble-link": (
            "browser-build-source",
            ("code/client/libmumblelink.c", "code/client/libmumblelink.h"),
        ),
        "openal-headers": (
            "browser-interface-header",
            ("code/thirdparty/openal-soft-1.24.3",),
        ),
        "opus": ("browser-build-source", ("code/thirdparty/opus-1.5.2",)),
        "opusfile": (
            "browser-build-source",
            ("code/thirdparty/opusfile-0.12",),
        ),
        "puff": (
            "browser-build-source",
            ("code/renderercommon/puff.c", "code/renderercommon/puff.h"),
        ),
        "public-domain-md5": ("browser-build-source", ("code/qcommon/md5.c",)),
        "public-domain-updater": (
            "feature-disabled-source",
            ("code/sys/sys_autoupdater.c",),
        ),
        "qvm-libc": ("qvm-build-source", ("code/game/bg_lib.c",)),
        "sdl-headers": (
            "emscripten-disabled-source",
            ("code/thirdparty/SDL2-2.32.8",),
        ),
        "sdl-prebuilt-libraries": (
            "native-only-prebuilt",
            ("code/thirdparty/libs",),
        ),
        "snd-adpcm": ("browser-build-source", ("code/client/snd_adpcm.c",)),
        "zlib": ("browser-build-source", ("code/thirdparty/zlib-1.3.1",)),
    }
    non_product_components = {"lcc-build-tool"}
    component_ids: list[str] = []
    component_paths_by_id: dict[str, list[str]] = {}
    excluded_paths_by_id: dict[str, list[str]] = {}
    for index, raw_component in enumerate(license_components):
        component_path = f"{path}.engine.licenseComponents[{index}]"
        component = _object(raw_component, component_path)
        _exact_keys(
            component,
            ("excludedPaths", "id", "license", "paths", "sourceRole"),
            component_path,
        )
        component_id = _string(component["id"], f"{component_path}.id")
        component_ids.append(component_id)
        component_paths = _sorted_unique_strings(
            component["paths"], f"{component_path}.paths"
        )
        if not component_paths:
            _fail(f"{component_path}.paths", "must not be empty")
        for component_path_index, source_path in enumerate(component_paths):
            _relative_path(
                source_path, f"{component_path}.paths[{component_path_index}]"
            )
        excluded_paths = _sorted_unique_strings(
            component["excludedPaths"], f"{component_path}.excludedPaths"
        )
        for excluded_index, excluded_path in enumerate(excluded_paths):
            _relative_path(
                excluded_path, f"{component_path}.excludedPaths[{excluded_index}]"
            )
        component_paths_by_id[component_id] = component_paths
        excluded_paths_by_id[component_id] = excluded_paths
        _validate_license(
            component["license"],
            f"{component_path}.license",
            product_allowed,
            product_refs,
            tool_refs,
            license_class=(
                TOOL_ONLY_LICENSE
                if component_id in non_product_components
                else PRODUCT_INPUT_LICENSE
            ),
        )
        evidence_identity = component["license"]["evidenceIdentity"]
        if (
            evidence_identity.startswith("git:")
            and evidence_identity != f"git:{commit}"
        ):
            _fail(
                f"{component_path}.license.evidenceIdentity",
                "must use the pinned engine commit for repository-local evidence",
            )
    _unique(component_ids, f"{path}.engine.licenseComponents[].id")
    if component_ids != sorted(component_ids):
        _fail(f"{path}.engine.licenseComponents", "must be sorted by id")
    if set(component_ids) != set(expected_component_contracts):
        _fail(
            f"{path}.engine.licenseComponents",
            f"must contain exactly {sorted(expected_component_contracts)}",
        )
    specific_paths = [
        source_path
        for component_id, source_paths in component_paths_by_id.items()
        if component_id != "ioq3-core"
        for source_path in source_paths
    ]
    _unique(specific_paths, f"{path}.engine.licenseComponents[].paths")
    if any(not source_path.startswith("code/") for source_path in specific_paths):
        _fail(
            f"{path}.engine.licenseComponents",
            "exception paths must be descendants of the code core",
        )
    for index, source_path in enumerate(specific_paths):
        for other_path in specific_paths[index + 1 :]:
            if source_path.startswith(f"{other_path}/") or other_path.startswith(
                f"{source_path}/"
            ):
                _fail(
                    f"{path}.engine.licenseComponents",
                    "specific component paths must not overlap",
                )
    tree_closure = _object(engine["treeClosure"], f"{path}.engine.treeClosure")
    _exact_keys(
        tree_closure,
        ("thirdpartyEntries", "toolEntries"),
        f"{path}.engine.treeClosure",
    )
    thirdparty_entries = _sorted_unique_strings(
        tree_closure["thirdpartyEntries"],
        f"{path}.engine.treeClosure.thirdpartyEntries",
    )
    tool_entries = _sorted_unique_strings(
        tree_closure["toolEntries"],
        f"{path}.engine.treeClosure.toolEntries",
    )
    if not thirdparty_entries or not tool_entries:
        _fail(f"{path}.engine.treeClosure", "entry sets must not be empty")
    expected_thirdparty_entries = sorted(
        {
            PurePosixPath(source_path).parts[2]
            for source_path in specific_paths
            if len(PurePosixPath(source_path).parts) >= 3
            and PurePosixPath(source_path).parts[:2] == ("code", "thirdparty")
        }
    )
    if thirdparty_entries != expected_thirdparty_entries:
        _fail(
            f"{path}.engine.treeClosure.thirdpartyEntries",
            "must equal the top-level thirdparty entries in the component inventory",
        )
    for component_id, excluded_paths in excluded_paths_by_id.items():
        expected_exclusions = (
            sorted(specific_paths) if component_id == "ioq3-core" else []
        )
        if excluded_paths != expected_exclusions:
            _fail(
                f"{path}.engine.licenseComponents[{component_id}].excludedPaths",
                "must make the core-minus-exceptions coverage explicit",
            )
    for index, component in enumerate(license_components):
        component_id = component["id"]
        expected_role, expected_paths = expected_component_contracts[component_id]
        if (
            component.get("sourceRole") != expected_role
            or tuple(component_paths_by_id[component_id]) != expected_paths
        ):
            _fail(
                f"{path}.engine.licenseComponents[{index}]",
                "does not match the reviewed role and path inventory",
            )
    _validate_preferred_source(
        engine["preferredSource"], f"{path}.engine.preferredSource"
    )
    if engine["preferredSource"].get("revision") != commit:
        _fail(f"{path}.engine.preferredSource.revision", "must equal engine.commit")

    tools = _array(baseline["tools"], f"{path}.tools")
    if not tools:
        _fail(f"{path}.tools", "must not be empty")
    tool_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_tool in enumerate(tools):
        tool_path = f"{path}.tools[{index}]"
        tool = _object(raw_tool, tool_path)
        tool_id = _string(tool.get("id"), f"{tool_path}.id")
        if tool_id in tool_by_id:
            _fail(f"{tool_path}.id", "must be unique")
        kind = tool.get("kind")
        if kind == "oci-image":
            _exact_keys(
                tool,
                (
                    "humanTag",
                    "id",
                    "image",
                    "immutableRef",
                    "indexDigest",
                    "kind",
                    "license",
                    "platform",
                    "platformDigest",
                    "preferredSource",
                    "role",
                    "version",
                ),
                tool_path,
            )
            index_digest = _sha256_digest(
                tool["indexDigest"], f"{tool_path}.indexDigest"
            )
            platform_digest = _sha256_digest(
                tool["platformDigest"], f"{tool_path}.platformDigest"
            )
            if index_digest == platform_digest:
                _fail(
                    f"{tool_path}.platformDigest",
                    "must differ from the multi-platform index digest",
                )
            image = _string(tool["image"], f"{tool_path}.image")
            if "@" in image or ":" in image.rsplit("/", 1)[-1]:
                _fail(
                    f"{tool_path}.image", "must be a repository name, not a reference"
                )
            if tool["immutableRef"] != f"{image}@{platform_digest}":
                _fail(f"{tool_path}.immutableRef", "must be image@platformDigest")
            if tool["platform"] != "linux/amd64":
                _fail(f"{tool_path}.platform", "must be linux/amd64 for WP0")
            human_tag = _string(tool["humanTag"], f"{tool_path}.humanTag")
            version = _string(tool["version"], f"{tool_path}.version")
            if human_tag != version:
                _fail(f"{tool_path}.humanTag", "must equal the descriptive version")
        elif kind == "archive":
            _exact_keys(
                tool,
                (
                    "fileName",
                    "id",
                    "kind",
                    "license",
                    "platform",
                    "preferredSource",
                    "revision",
                    "role",
                    "sha256",
                    "size",
                    "url",
                    "version",
                ),
                tool_path,
            )
            _relative_path(tool["fileName"], f"{tool_path}.fileName")
            _https_url(tool["url"], f"{tool_path}.url")
            _sha256(tool["sha256"], f"{tool_path}.sha256")
            _integer(tool["size"], f"{tool_path}.size", minimum=1)
            _string(tool["revision"], f"{tool_path}.revision")
            version = _string(tool["version"], f"{tool_path}.version")
            archive_platform = _string(tool["platform"], f"{tool_path}.platform")
            if tool_id == "chrome-for-testing":
                expected_file = f"chrome-{archive_platform}.zip"
                expected_url = (
                    "https://storage.googleapis.com/chrome-for-testing-public/"
                    f"{version}/{archive_platform}/{expected_file}"
                )
                if tool["fileName"] != expected_file or tool["url"] != expected_url:
                    _fail(
                        tool_path,
                        "must keep Chrome version, platform, filename and URL consistent",
                    )
        else:
            _fail(f"{tool_path}.kind", "must be 'oci-image' or 'archive'")
        _string(tool["role"], f"{tool_path}.role")
        _validate_license(
            tool["license"],
            f"{tool_path}.license",
            product_allowed,
            product_refs,
            tool_refs,
            license_class=TOOL_ONLY_LICENSE,
        )
        _validate_preferred_source(
            tool["preferredSource"], f"{tool_path}.preferredSource"
        )
        tool_by_id[tool_id] = tool

    required_tools = {
        "chrome-for-testing": ("archive", "acceptance-browser"),
        "emscripten-builder": ("oci-image", "wasm-builder"),
        "native-builder-base": ("oci-image", "native-builder-base"),
    }
    if set(tool_by_id) != set(required_tools):
        _fail(f"{path}.tools", f"must contain exactly {sorted(required_tools)}")
    for tool_id, (kind, role) in required_tools.items():
        if tool_by_id[tool_id]["kind"] != kind or tool_by_id[tool_id]["role"] != role:
            _fail(f"{path}.tools[{tool_id}]", "has the wrong kind or role")

    _validate_redistributed_product_images(
        baseline["redistributedProductImages"],
        f"{path}.redistributedProductImages",
        image_refs,
        tools,
    )

    platform = _object(baseline["acceptancePlatform"], f"{path}.acceptancePlatform")
    _exact_keys(
        platform,
        (
            "architecture",
            "browserToolId",
            "desktop",
            "installationMedia",
            "license",
            "name",
            "preferredSource",
            "version",
        ),
        f"{path}.acceptancePlatform",
    )
    if platform["browserToolId"] != "chrome-for-testing":
        _fail(
            f"{path}.acceptancePlatform.browserToolId", "must select the pinned browser"
        )
    expected_platform = {
        "architecture": "x86_64",
        "desktop": "GNOME",
        "name": "Fedora Linux Workstation",
        "version": "44",
    }
    for key, expected in expected_platform.items():
        if platform[key] != expected:
            _fail(f"{path}.acceptancePlatform.{key}", f"must be {expected!r}")
    _validate_license(
        platform["license"],
        f"{path}.acceptancePlatform.license",
        product_allowed,
        product_refs,
        tool_refs,
        license_class=TOOL_ONLY_LICENSE,
    )
    _validate_preferred_source(
        platform["preferredSource"], f"{path}.acceptancePlatform.preferredSource"
    )
    media = _object(
        platform["installationMedia"], f"{path}.acceptancePlatform.installationMedia"
    )
    _exact_keys(
        media,
        ("acquisitionUrl", "checksumEvidence", "fileName", "sha256", "size"),
        f"{path}.acceptancePlatform.installationMedia",
    )
    _https_url(
        media["acquisitionUrl"],
        f"{path}.acceptancePlatform.installationMedia.acquisitionUrl",
    )
    _relative_path(
        media["fileName"], f"{path}.acceptancePlatform.installationMedia.fileName"
    )
    _sha256(media["sha256"], f"{path}.acceptancePlatform.installationMedia.sha256")
    _integer(
        media["size"], f"{path}.acceptancePlatform.installationMedia.size", minimum=1
    )
    release = "44-1.7"
    expected_file = f"Fedora-Workstation-Live-{release}.x86_64.iso"
    expected_url = (
        "https://download.fedoraproject.org/pub/fedora/linux/releases/44/"
        f"Workstation/x86_64/iso/{expected_file}"
    )
    if media["fileName"] != expected_file or media["acquisitionUrl"] != expected_url:
        _fail(
            f"{path}.acceptancePlatform.installationMedia",
            "must keep Fedora version, architecture, filename and URL consistent",
        )
    checksum = _object(
        media["checksumEvidence"],
        f"{path}.acceptancePlatform.installationMedia.checksumEvidence",
    )
    _exact_keys(
        checksum,
        ("format", "sha256", "url"),
        f"{path}.acceptancePlatform.installationMedia.checksumEvidence",
    )
    if checksum["format"] != "OpenPGP-cleartext-signature":
        _fail(
            f"{path}.acceptancePlatform.installationMedia.checksumEvidence.format",
            "must be OpenPGP-cleartext-signature",
        )
    _sha256(
        checksum["sha256"],
        f"{path}.acceptancePlatform.installationMedia.checksumEvidence.sha256",
    )
    expected_checksum_url = (
        "https://download.fedoraproject.org/pub/fedora/linux/releases/44/"
        "Workstation/x86_64/iso/Fedora-Workstation-44-1.7-x86_64-CHECKSUM"
    )
    if (
        _https_url(
            checksum["url"],
            f"{path}.acceptancePlatform.installationMedia.checksumEvidence.url",
        )
        != expected_checksum_url
    ):
        _fail(
            f"{path}.acceptancePlatform.installationMedia.checksumEvidence.url",
            "must identify the signed checksum for the selected release",
        )
    if platform["license"]["evidenceIdentity"] != f"sha256:{checksum['sha256']}":
        _fail(
            f"{path}.acceptancePlatform.license.evidenceIdentity",
            "must bind the acceptance aggregate to its signed checksum record",
        )

    trust = _object(baseline["relayTrust"], f"{path}.relayTrust")
    _exact_keys(
        trust,
        (
            "certificateHashProvisioning",
            "hashAlgorithm",
            "maximumCertificateValidityDays",
            "mechanism",
            "requiredCertificateKeyAlgorithm",
            "specificationUrl",
        ),
        f"{path}.relayTrust",
    )
    expected_trust = {
        "certificateHashProvisioning": "runtime-only",
        "hashAlgorithm": "sha-256",
        "maximumCertificateValidityDays": 14,
        "mechanism": "serverCertificateHashes",
        "requiredCertificateKeyAlgorithm": "ECDSA-P256",
    }
    for key, expected in expected_trust.items():
        if trust[key] != expected:
            _fail(f"{path}.relayTrust.{key}", f"must be {expected!r}")
    _https_url(trust["specificationUrl"], f"{path}.relayTrust.specificationUrl")

    evidence = _object(baseline["upstreamEvidence"], f"{path}.upstreamEvidence")
    _exact_keys(
        evidence,
        (
            "compatibilityGate",
            "ioq3Commit",
            "ioq3WorkflowPath",
            "mapping",
            "targetEmscriptenVersion",
            "upstreamEmscriptenAction",
            "upstreamEmscriptenVersion",
            "versionRelationship",
        ),
        f"{path}.upstreamEvidence",
    )
    if _commit(evidence["ioq3Commit"], f"{path}.upstreamEvidence.ioq3Commit") != commit:
        _fail(f"{path}.upstreamEvidence.ioq3Commit", "must equal engine.commit")
    if (
        evidence["targetEmscriptenVersion"]
        != tool_by_id["emscripten-builder"]["version"]
    ):
        _fail(
            f"{path}.upstreamEvidence.targetEmscriptenVersion",
            "must equal builder version",
        )
    upstream_version = _numeric_version(
        evidence["upstreamEmscriptenVersion"],
        f"{path}.upstreamEvidence.upstreamEmscriptenVersion",
    )
    target_version = _numeric_version(
        evidence["targetEmscriptenVersion"],
        f"{path}.upstreamEvidence.targetEmscriptenVersion",
    )
    width = max(len(upstream_version), len(target_version))
    upstream_order = upstream_version + (0,) * (width - len(upstream_version))
    target_order = target_version + (0,) * (width - len(target_version))
    if target_order > upstream_order:
        expected_relationship = "target-upgrade"
    elif target_order < upstream_order:
        expected_relationship = "target-downgrade"
    else:
        expected_relationship = "upstream-aligned"
    if evidence["versionRelationship"] != expected_relationship:
        _fail(
            f"{path}.upstreamEvidence.versionRelationship",
            f"must be {expected_relationship!r} for the recorded versions",
        )
    for key in (
        "compatibilityGate",
        "ioq3WorkflowPath",
        "mapping",
        "upstreamEmscriptenAction",
        "upstreamEmscriptenVersion",
        "versionRelationship",
    ):
        _string(evidence[key], f"{path}.upstreamEvidence.{key}")
    if "@v" not in evidence["upstreamEmscriptenAction"]:
        _fail(
            f"{path}.upstreamEvidence.upstreamEmscriptenAction",
            "must record the upstream action ref",
        )
    return baseline


def validate_measurement_vector(value: Any, path: str = "measurement") -> None:
    vector = _object(value, path)
    _exact_keys(
        vector,
        (
            "$schema",
            "directions",
            "formatVersion",
            "framing",
            "packedCases",
            "payloadIdentification",
            "requiredBoundaryBytes",
        ),
        path,
    )
    if vector["$schema"] != MEASUREMENT_SCHEMA or vector["formatVersion"] != 1:
        _fail(path, "has an unsupported schema or formatVersion")
    framing = _object(vector["framing"], f"{path}.framing")
    _exact_keys(
        framing,
        (
            "datagramLengthPrefixBytes",
            "relayHeaderBytes",
            "singleDatagramOverheadBytes",
        ),
        f"{path}.framing",
    )
    for key, expected in (
        ("datagramLengthPrefixBytes", 2),
        ("relayHeaderBytes", 40),
    ):
        if framing[key] != expected:
            _fail(f"{path}.framing.{key}", f"must be {expected}")
    if (
        framing["singleDatagramOverheadBytes"]
        != framing["relayHeaderBytes"] + framing["datagramLengthPrefixBytes"]
    ):
        _fail(f"{path}.framing", "must preserve the reviewed 40 + 2 byte contract")
    identification = _object(
        vector["payloadIdentification"], f"{path}.payloadIdentification"
    )
    _exact_keys(
        identification,
        (
            "minimumTaggedInnerBytes",
            "nonceBytes",
            "placement",
            "smallerCasesRunSequentially",
        ),
        f"{path}.payloadIdentification",
    )
    nonce_bytes = _integer(
        identification["nonceBytes"],
        f"{path}.payloadIdentification.nonceBytes",
        minimum=16,
    )
    if nonce_bytes > 64:
        _fail(f"{path}.payloadIdentification.nonceBytes", "must be <= 64")
    if identification["placement"] != "payload-prefix":
        _fail(
            f"{path}.payloadIdentification.placement",
            "must be 'payload-prefix'",
        )
    if identification["minimumTaggedInnerBytes"] != nonce_bytes:
        _fail(
            f"{path}.payloadIdentification.minimumTaggedInnerBytes",
            "must equal nonceBytes",
        )
    if identification["smallerCasesRunSequentially"] is not True:
        _fail(
            f"{path}.payloadIdentification.smallerCasesRunSequentially",
            "must be true",
        )

    boundaries = _array(
        vector["requiredBoundaryBytes"], f"{path}.requiredBoundaryBytes"
    )
    required_boundaries = [1300, 1307, 1309, 1312, 1314]
    if boundaries != required_boundaries:
        _fail(f"{path}.requiredBoundaryBytes", f"must be {required_boundaries}")
    directions = _object(vector["directions"], f"{path}.directions")
    _exact_keys(
        directions, ("browserToServer", "serverToBrowser"), f"{path}.directions"
    )
    parsed_directions: dict[str, list[int]] = {}
    for name, raw_sizes in directions.items():
        size_path = f"{path}.directions.{name}"
        sizes = _array(raw_sizes, size_path)
        for index, size in enumerate(sizes):
            parsed = _integer(size, f"{size_path}[{index}]")
            if parsed > 65535:
                _fail(f"{size_path}[{index}]", "must fit the u16 framing field")
        _unique(sizes, size_path)
        if sizes != sorted(sizes):
            _fail(size_path, "must be sorted")
        for boundary in required_boundaries:
            for adjacent in (boundary - 1, boundary, boundary + 1):
                if adjacent not in sizes:
                    _fail(
                        size_path, f"must include {adjacent} around boundary {boundary}"
                    )
        parsed_directions[name] = sizes
    if parsed_directions["browserToServer"] != parsed_directions["serverToBrowser"]:
        _fail(
            f"{path}.directions", "must exercise the same size vector bidirectionally"
        )
    required_resolution = {
        0,
        1,
        16,
        64,
        256,
        512,
        768,
        1024,
        1100,
        1150,
        1175,
        1190,
        1200,
        1210,
        1220,
        1230,
        1240,
        1250,
        1260,
        1270,
        1280,
        1290,
        1298,
        1400,
        1401,
        2048,
        4096,
        8192,
        16384,
    }
    for name, sizes in parsed_directions.items():
        missing_resolution = sorted(required_resolution - set(sizes))
        if missing_resolution:
            _fail(
                f"{path}.directions.{name}",
                f"must include resolution and connectionless cases {missing_resolution}",
            )

    packed_cases = _array(vector["packedCases"], f"{path}.packedCases")
    if not packed_cases:
        _fail(f"{path}.packedCases", "must not be empty")
    seen_packed: set[tuple[int, ...]] = set()
    for case_index, raw_case in enumerate(packed_cases):
        case_path = f"{path}.packedCases[{case_index}]"
        case_record = _object(raw_case, case_path)
        _exact_keys(case_record, ("direction", "sizes"), case_path)
        if case_record["direction"] != "browserToServer":
            _fail(
                f"{case_path}.direction",
                "must be browserToServer because reverse frames carry one datagram",
            )
        case = _array(case_record["sizes"], f"{case_path}.sizes")
        if not 2 <= len(case) <= 4:
            _fail(f"{case_path}.sizes", "must contain between two and four datagrams")
        for item_index, size in enumerate(case):
            parsed = _integer(size, f"{case_path}[{item_index}]")
            if parsed > 65535:
                _fail(f"{case_path}[{item_index}]", "must fit the u16 framing field")
            if parsed < identification["minimumTaggedInnerBytes"]:
                _fail(
                    f"{case_path}.sizes[{item_index}]",
                    "must fit the nonce because packed cases are correlation evidence",
                )
        identity = tuple(case)
        if identity in seen_packed:
            _fail(case_path, "duplicates an earlier packed case")
        seen_packed.add(identity)


def _baseline_input_identities(
    baseline: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Every baseline record a generated artifact may declare as an input.

    The three collections are all admissible and all bound the same way, by the
    exact identity the lock pins:

    * the engine, by commit;
    * a `tools[]` entry, by image reference or archive digest — what produced
      the artifact;
    * a `redistributedProductImages[]` entry, by image reference — what an
      artifact that is itself an image *ships*.

    The third was deliberately absent while the WP0 amendment only pinned the
    runtime base and nothing consumed it. The WP5 server image does consume it:
    the base is not a tool that vanishes when the build ends, it is part of the
    distributed bytes, and a manifest that could not say so would have to leave
    its largest input undeclared. Adding it changes nothing about how an input
    is checked — the id must still exist here, the manifest must still carry a
    matching input record, and its kind and identity must still agree exactly.

    The three id spaces cannot collide in a valid baseline: the engine id is
    fixed to `ioq3`, and the tool and image id sets are each required to equal
    their own closed set, neither of which contains it. This function is
    nonetheless the one place where a collision would silently resolve to one
    record rather than fail, so it refuses one outright.
    """

    def _register(
        identities: dict[str, tuple[str, str]], record_id: str, identity: tuple[str, str]
    ) -> None:
        if record_id in identities:
            _fail(
                "baseline",
                f"records {record_id!r} in two input collections, so a manifest "
                "input naming it would not identify one pinned record",
            )
        identities[record_id] = identity

    identities: dict[str, tuple[str, str]] = {}
    _register(
        identities,
        baseline["engine"]["id"],
        ("git", f"git:{baseline['engine']['commit']}"),
    )
    for tool in baseline["tools"]:
        if tool["kind"] == "oci-image":
            _register(identities, tool["id"], ("oci-image", tool["immutableRef"]))
        else:
            _register(
                identities, tool["id"], ("archive", f"sha256:{tool['sha256']}")
            )
    for image in baseline["redistributedProductImages"]:
        _register(identities, image["id"], ("oci-image", image["immutableRef"]))
    return identities


def validate_artifact_manifest(
    value: Any,
    path: str,
    *,
    baseline: dict[str, Any] | None = None,
    expected_schema: str = ARTIFACT_SCHEMA,
    required_baseline_input_ids: Iterable[str] = (),
) -> None:
    manifest = _object(value, path)
    _exact_keys(
        manifest,
        (
            "$schema",
            "artifacts",
            "baselineIdentity",
            "baselineInputIds",
            "digestAlgorithm",
            "formatVersion",
            "inputs",
            "producer",
        ),
        path,
    )
    if manifest["$schema"] != expected_schema or manifest["formatVersion"] != 1:
        _fail(path, "has an unsupported schema or formatVersion")
    if manifest["digestAlgorithm"] != "sha256":
        _fail(f"{path}.digestAlgorithm", "must be sha256")
    baseline_identity = _sha256_digest(
        manifest["baselineIdentity"], f"{path}.baselineIdentity"
    )
    baseline_input_ids = _sorted_unique_strings(
        manifest["baselineInputIds"], f"{path}.baselineInputIds"
    )
    if not baseline_input_ids:
        _fail(f"{path}.baselineInputIds", "must not be empty")
    missing_required = sorted(
        set(required_baseline_input_ids) - set(baseline_input_ids)
    )
    if missing_required:
        _fail(
            f"{path}.baselineInputIds",
            f"must declare the required baseline inputs {missing_required}",
        )
    producer = _object(manifest["producer"], f"{path}.producer")
    _exact_keys(producer, ("commit", "name"), f"{path}.producer")
    _commit(producer["commit"], f"{path}.producer.commit")
    _string(producer["name"], f"{path}.producer.name")

    inputs = _array(manifest["inputs"], f"{path}.inputs")
    if not inputs:
        _fail(f"{path}.inputs", "must not be empty")
    input_ids = []
    input_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_input in enumerate(inputs):
        input_path = f"{path}.inputs[{index}]"
        item = _object(raw_input, input_path)
        _exact_keys(item, ("id", "identity", "kind"), input_path)
        input_id = _string(item["id"], f"{input_path}.id")
        input_ids.append(input_id)
        identity = _string(item["identity"], f"{input_path}.identity")
        kind = item["kind"]
        if kind not in {"archive", "artifact-manifest", "git", "oci-image"}:
            _fail(f"{input_path}.kind", "is not an allowed input kind")
        if kind == "git" and not re.fullmatch(r"git:[0-9a-f]{40}", identity):
            _fail(f"{input_path}.identity", "must be git:<40-character commit>")
        if kind in {"archive", "artifact-manifest"} and not SHA256_DIGEST_RE.fullmatch(
            identity
        ):
            _fail(f"{input_path}.identity", "must be sha256:<64-character digest>")
        if kind == "oci-image" and not OCI_IDENTITY_RE.fullmatch(identity):
            _fail(f"{input_path}.identity", "must be repository@sha256:<digest>")
        input_by_id[input_id] = item
    _unique(input_ids, f"{path}.inputs[].id")
    if input_ids != sorted(input_ids):
        _fail(f"{path}.inputs", "must be sorted by id")
    if baseline is not None:
        expected_baseline_identity = _canonical_json_identity(baseline)
        if baseline_identity != expected_baseline_identity:
            _fail(
                f"{path}.baselineIdentity",
                f"must equal the committed baseline identity {expected_baseline_identity}",
            )
        expected_inputs = _baseline_input_identities(baseline)
        for baseline_input_id in baseline_input_ids:
            if baseline_input_id not in expected_inputs:
                _fail(
                    f"{path}.baselineInputIds",
                    f"contains unknown baseline input {baseline_input_id!r}",
                )
            if baseline_input_id not in input_by_id:
                _fail(
                    f"{path}.baselineInputIds",
                    f"names missing manifest input {baseline_input_id!r}",
                )
            expected_kind, expected_identity = expected_inputs[baseline_input_id]
            item = input_by_id[baseline_input_id]
            if item["kind"] != expected_kind or item["identity"] != expected_identity:
                _fail(
                    f"{path}.inputs[{baseline_input_id}]",
                    "does not agree with the committed baseline",
                )
        undeclared = sorted(
            (set(input_by_id) & set(expected_inputs)) - set(baseline_input_ids)
        )
        if undeclared:
            _fail(
                f"{path}.baselineInputIds",
                f"does not declare baseline inputs present in the manifest {undeclared}",
            )

    artifacts = _array(manifest["artifacts"], f"{path}.artifacts")
    if not artifacts:
        _fail(f"{path}.artifacts", "must not be empty")
    artifact_paths = []
    for index, raw_artifact in enumerate(artifacts):
        artifact_path = f"{path}.artifacts[{index}]"
        item = _object(raw_artifact, artifact_path)
        _exact_keys(item, ("path", "sha256", "size"), artifact_path)
        artifact_paths.append(_relative_path(item["path"], f"{artifact_path}.path"))
        _sha256(item["sha256"], f"{artifact_path}.sha256")
        _integer(item["size"], f"{artifact_path}.size")
    _unique(artifact_paths, f"{path}.artifacts[].path")
    if artifact_paths != sorted(artifact_paths):
        _fail(f"{path}.artifacts", "must be sorted by path")


def validate_content_provenance(
    value: Any,
    path: str,
    *,
    allowed_licenses: set[str],
    baseline: dict[str, Any] | None = None,
    expected_schema: str = CONTENT_SCHEMA,
) -> None:
    provenance = _object(value, path)
    _exact_keys(
        provenance,
        (
            "$schema",
            "baselineIdentity",
            "formatVersion",
            "members",
            "package",
            "sources",
        ),
        path,
    )
    if provenance["$schema"] != expected_schema or provenance["formatVersion"] != 1:
        _fail(path, "has an unsupported schema or formatVersion")
    baseline_identity = _sha256_digest(
        provenance["baselineIdentity"], f"{path}.baselineIdentity"
    )
    if baseline is not None and baseline_identity != _canonical_json_identity(baseline):
        _fail(
            f"{path}.baselineIdentity",
            "must equal the committed baseline identity",
        )
    package = _object(provenance["package"], f"{path}.package")
    _exact_keys(package, ("id", "name"), f"{path}.package")
    _string(package["id"], f"{path}.package.id")
    _string(package["name"], f"{path}.package.name")

    sources = _array(provenance["sources"], f"{path}.sources")
    if not sources:
        _fail(f"{path}.sources", "must not be empty")
    source_ids = []
    source_licenses: dict[str, str] = {}
    for index, raw_source in enumerate(sources):
        source_path = f"{path}.sources[{index}]"
        source = _object(raw_source, source_path)
        _exact_keys(
            source,
            (
                "id",
                "licenseEvidenceUrl",
                "licenseExpression",
                "preferredSourceRevision",
                "preferredSourceUrl",
                "sourceIdentity",
                "sourceUrl",
            ),
            source_path,
        )
        source_ids.append(_string(source["id"], f"{source_path}.id"))
        for key in ("licenseEvidenceUrl", "preferredSourceUrl", "sourceUrl"):
            _https_url(source[key], f"{source_path}.{key}")
        license_expression = _string(
            source["licenseExpression"], f"{source_path}.licenseExpression"
        )
        if license_expression not in allowed_licenses:
            _fail(
                f"{source_path}.licenseExpression",
                "is not an allowed product-input license",
            )
        identity = _string(source["sourceIdentity"], f"{source_path}.sourceIdentity")
        if not SOURCE_IDENTITY_RE.fullmatch(identity):
            _fail(
                f"{source_path}.sourceIdentity",
                "must pin git:<commit> or sha256:<digest>",
            )
        preferred_revision = _string(
            source["preferredSourceRevision"], f"{source_path}.preferredSourceRevision"
        )
        if not SOURCE_IDENTITY_RE.fullmatch(preferred_revision):
            _fail(
                f"{source_path}.preferredSourceRevision",
                "must pin git:<commit> or sha256:<digest>",
            )
        source_licenses[source["id"]] = license_expression
    _unique(source_ids, f"{path}.sources[].id")
    if source_ids != sorted(source_ids):
        _fail(f"{path}.sources", "must be sorted by id")

    members = _array(provenance["members"], f"{path}.members")
    if not members:
        _fail(f"{path}.members", "must not be empty")
    member_paths = []
    known_sources = set(source_ids)
    for index, raw_member in enumerate(members):
        member_path = f"{path}.members[{index}]"
        member = _object(raw_member, member_path)
        _exact_keys(
            member,
            (
                "licenseExpression",
                "noticePaths",
                "obligations",
                "path",
                "role",
                "sha256",
                "size",
                "sourceId",
                "sourcePath",
                "transformation",
            ),
            member_path,
        )
        member_paths.append(_relative_path(member["path"], f"{member_path}.path"))
        _sha256(member["sha256"], f"{member_path}.sha256")
        _integer(member["size"], f"{member_path}.size")
        if member["sourceId"] not in known_sources:
            _fail(f"{member_path}.sourceId", "does not name a declared source")
        _relative_path(member["sourcePath"], f"{member_path}.sourcePath")
        _string(member["transformation"], f"{member_path}.transformation")
        if member["licenseExpression"] not in allowed_licenses:
            _fail(
                f"{member_path}.licenseExpression",
                "is not an allowed product-input license",
            )
        if member["licenseExpression"] != source_licenses[member["sourceId"]]:
            _fail(
                f"{member_path}.licenseExpression",
                "must equal the declared source license expression",
            )
        if member["role"] not in {
            "asset",
            "game-module",
            "metadata",
            "notice",
            "source-code",
        }:
            _fail(f"{member_path}.role", "is not an allowed distribution-member role")
        obligations = _sorted_unique_strings(
            member["obligations"], f"{member_path}.obligations"
        )
        allowed_obligations = {
            "attribution",
            "copyleft-source",
            "license-notice",
            "share-alike",
        }
        unknown_obligations = sorted(set(obligations) - allowed_obligations)
        if unknown_obligations:
            _fail(
                f"{member_path}.obligations",
                f"contains unknown obligations {unknown_obligations}",
            )
        expression = member["licenseExpression"]
        license_identifiers = _license_identifiers(expression)
        required_obligations = {"license-notice"}
        if any(
            identifier.startswith(("AGPL-", "GPL-", "LGPL-"))
            for identifier in license_identifiers
        ):
            required_obligations.add("copyleft-source")
        if any(identifier.startswith("CC-BY-") for identifier in license_identifiers):
            required_obligations.add("attribution")
        if any(
            identifier.startswith("CC-BY-SA-") for identifier in license_identifiers
        ):
            required_obligations.update(("attribution", "share-alike"))
        if not required_obligations.issubset(obligations):
            _fail(
                f"{member_path}.obligations",
                f"must include {sorted(required_obligations)} for {expression}",
            )
        notice_paths = _sorted_unique_strings(
            member["noticePaths"], f"{member_path}.noticePaths"
        )
        if member["role"] != "notice" and not notice_paths:
            _fail(
                f"{member_path}.noticePaths",
                "must name a packaged notice member for distribution obligations",
            )
        for notice_index, notice_path in enumerate(member["noticePaths"]):
            _relative_path(notice_path, f"{member_path}.noticePaths[{notice_index}]")
    _unique(member_paths, f"{path}.members[].path")
    if member_paths != sorted(member_paths):
        _fail(f"{path}.members", "must be sorted by path")
    known_member_paths = set(member_paths)
    member_roles = {member["path"]: member["role"] for member in members}
    for index, member in enumerate(members):
        for notice_index, notice_path in enumerate(member["noticePaths"]):
            if notice_path not in known_member_paths:
                _fail(
                    f"{path}.members[{index}].noticePaths[{notice_index}]",
                    "does not name a declared provenance member",
                )
            if member_roles[notice_path] != "notice":
                _fail(
                    f"{path}.members[{index}].noticePaths[{notice_index}]",
                    "must name a member whose role is 'notice'",
                )


def _git_output(arguments: list[str], path: str) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        _fail(path, f"cannot inspect Git identity: {error}")
    return result.stdout.strip()


def verify_engine_tree(root: Path, baseline: dict[str, Any]) -> None:
    """Bind the reviewed component inventory to the checked-out source tree."""
    engine_root = root / baseline["engine"]["submodulePath"]
    components = baseline["engine"]["licenseComponents"]
    recorded_paths = {
        source_path
        for component in components
        for field in ("paths", "excludedPaths")
        for source_path in component[field]
    }
    missing_paths = sorted(
        source_path
        for source_path in recorded_paths
        if not (engine_root / source_path).exists()
    )
    if missing_paths:
        _fail(
            "engine.licenseInventoryTree",
            f"records paths absent from the pinned checkout {missing_paths}",
        )

    closure = baseline["engine"]["treeClosure"]
    expected_thirdparty = set(closure["thirdpartyEntries"])
    thirdparty_root = engine_root / "code" / "thirdparty"
    try:
        actual_thirdparty = {entry.name for entry in thirdparty_root.iterdir()}
    except OSError as error:
        _fail("engine.licenseInventoryTree", f"cannot inspect thirdparty tree: {error}")
    if actual_thirdparty != expected_thirdparty:
        _fail(
            "engine.licenseInventoryTree",
            "thirdparty entries differ from the closed inventory: "
            f"expected {sorted(expected_thirdparty)}, got {sorted(actual_thirdparty)}",
        )

    tools_root = engine_root / "code" / "tools"
    try:
        actual_tools = {entry.name for entry in tools_root.iterdir()}
    except OSError as error:
        _fail("engine.licenseInventoryTree", f"cannot inspect tools tree: {error}")
    expected_tools = set(closure["toolEntries"])
    if actual_tools != expected_tools:
        _fail(
            "engine.licenseInventoryTree",
            "tool entries differ from the reviewed inventory: "
            f"expected {sorted(expected_tools)}, got {sorted(actual_tools)}",
        )


def verify_documented_baseline_identity(root: Path, baseline: dict[str, Any]) -> None:
    expected_line = f"# {_canonical_json_identity(baseline)}"
    document_path = root / "docs" / "immutable-baseline.md"
    try:
        document = document_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail(str(document_path), f"cannot read documented baseline identity: {error}")
    identity_lines = re.findall(r"(?m)^# sha256:[0-9a-f]{64}$", document)
    if identity_lines != [expected_line]:
        _fail(
            str(document_path),
            f"must document exactly the current baseline identity as {expected_line}",
        )


def verify_engine_pin(root: Path, baseline: dict[str, Any]) -> None:
    expected = baseline["engine"]["commit"]
    expected_repository = baseline["engine"]["repository"]
    expected_branch = baseline["engine"]["branch"]
    gitmodules_changes = _git_output(
        ["git", "-C", str(root), "diff", "--name-only", "--", ".gitmodules"],
        "engine.gitmodulesWorktree",
    )
    if gitmodules_changes:
        _fail(
            "engine.gitmodulesWorktree",
            "has unstaged changes; the index is the validated commit candidate",
        )
    submodule_url = _git_output(
        [
            "git",
            "-C",
            str(root),
            "config",
            "--blob",
            ":.gitmodules",
            "--get",
            "submodule.ioq3.url",
        ],
        "engine.submoduleUrl",
    )
    submodule_branch = _git_output(
        [
            "git",
            "-C",
            str(root),
            "config",
            "--blob",
            ":.gitmodules",
            "--get",
            "submodule.ioq3.branch",
        ],
        "engine.submoduleBranch",
    )
    gitlink = _git_output(
        ["git", "-C", str(root), "rev-parse", ":ioq3"],
        "engine.gitlink",
    )
    checkout = _git_output(
        ["git", "-C", str(root / "ioq3"), "rev-parse", "HEAD"],
        "engine.checkout",
    )
    checkout_changes = _git_output(
        [
            "git",
            "-C",
            str(root / "ioq3"),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        "engine.checkoutWorktree",
    )
    if submodule_url != expected_repository:
        _fail(
            "engine.submoduleUrl",
            f"is {submodule_url}, lock requires {expected_repository}",
        )
    if submodule_branch != expected_branch:
        _fail(
            "engine.submoduleBranch",
            f"is {submodule_branch}, lock requires {expected_branch}",
        )
    if gitlink != expected:
        _fail("engine.gitlink", f"is {gitlink}, lock requires {expected}")
    if checkout != expected:
        _fail("engine.checkout", f"is {checkout}, lock requires {expected}")
    if checkout_changes:
        _fail("engine.checkoutWorktree", "must be clean, including untracked files")


def validate_repository(root: Path, *, verify_git: bool = True) -> list[Path]:
    expected_schema_ids = {
        "artifact-manifest.schema.json": ARTIFACT_SCHEMA,
        "baseline-lock.schema.json": "https://kaesual.github.io/arena-web/schemas/baseline-lock.schema.json",
        "content-provenance.schema.json": CONTENT_SCHEMA,
        "relay-measurement-vector.schema.json": "https://kaesual.github.io/arena-web/schemas/relay-measurement-vector.schema.json",
    }
    schema_dir = root / "schemas"
    try:
        schema_entries = sorted(schema_dir.iterdir())
    except OSError as error:
        _fail(str(schema_dir), f"cannot inspect required schema directory: {error}")
    if any(not item.is_file() for item in schema_entries):
        _fail(str(schema_dir), "must contain schema files only, without subdirectories")
    schema_paths = schema_entries
    if {item.name for item in schema_paths} != set(expected_schema_ids):
        _fail(str(schema_dir), f"must contain exactly {sorted(expected_schema_ids)}")
    schemas: dict[str, dict[str, Any]] = {}
    validated: list[Path] = []
    for path in schema_paths:
        schema = _object(_load_json(path), str(path))
        _check_supported_schema(schema, str(path))
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            _fail(str(path), "must declare JSON Schema draft 2020-12")
        if schema.get("$id") != expected_schema_ids[path.name]:
            _fail(str(path), "has an unexpected $id")
        if (
            schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
        ):
            _fail(str(path), "must reject non-objects and unknown top-level fields")
        schemas[path.name] = schema
        validated.append(path)

    baseline_path = root / "locks" / "baseline.json"
    raw_baseline = _load_json(baseline_path)
    _validate_schema_instance(
        raw_baseline,
        schemas["baseline-lock.schema.json"],
        schemas["baseline-lock.schema.json"],
        str(baseline_path),
    )
    baseline = validate_baseline(raw_baseline, str(baseline_path))
    verify_engine_tree(root, baseline)
    verify_documented_baseline_identity(root, baseline)
    if verify_git:
        verify_engine_pin(root, baseline)
    allowed_licenses = set(baseline["licensePolicy"]["productInputAllowedExpressions"])

    validated.append(baseline_path)
    measurement_path = root / "locks" / "relay-measurement-vector.json"
    if not measurement_path.is_file():
        _fail(str(measurement_path), "is a required WP0 lock")
    manifests_root = root / "manifests"
    metadata_roots = (root / "locks", manifests_root, root / "provenance")
    for metadata_root in metadata_roots:
        if not metadata_root.exists():
            continue
        required_artifact_inputs: tuple[str, ...] = ()
        if metadata_root == manifests_root:
            required_artifact_inputs = ARTIFACT_REQUIRED_BASELINE_INPUT_IDS
        for path in sorted(metadata_root.rglob("*.json")):
            if path == baseline_path:
                continue
            value = _load_json(path)
            record = _object(value, str(path))
            schema = record.get("$schema")
            if schema == MEASUREMENT_SCHEMA:
                schema_document = schemas["relay-measurement-vector.schema.json"]
                _validate_schema_instance(
                    record, schema_document, schema_document, str(path)
                )
                validate_measurement_vector(record, str(path))
            elif schema == ARTIFACT_SCHEMA:
                schema_document = schemas["artifact-manifest.schema.json"]
                _validate_schema_instance(
                    record, schema_document, schema_document, str(path)
                )
                validate_artifact_manifest(
                    record,
                    str(path),
                    baseline=baseline,
                    required_baseline_input_ids=required_artifact_inputs,
                )
            elif schema == CONTENT_SCHEMA:
                schema_document = schemas["content-provenance.schema.json"]
                _validate_schema_instance(
                    record, schema_document, schema_document, str(path)
                )
                validate_content_provenance(
                    record,
                    str(path),
                    allowed_licenses=allowed_licenses,
                    baseline=baseline,
                )
            else:
                _fail(str(path), f"uses unknown schema {schema!r}")
            validated.append(path)
    return validated
