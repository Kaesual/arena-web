# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import artifact_manifest  # noqa: E402
from metadata import (  # noqa: E402
    ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
    ARTIFACT_SCHEMA,
    CONTENT_SCHEMA,
    MetadataError,
    _canonical_json_identity,
    _check_supported_schema,
    _load_json,
    _validate_schema_instance,
    validate_artifact_manifest,
    validate_baseline,
    validate_content_provenance,
    validate_measurement_vector,
    validate_repository,
    verify_documented_baseline_identity,
    verify_engine_pin,
    verify_engine_tree,
)


def load_fixture(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")

    def write_minimal_engine_tree(self, root: Path) -> Path:
        engine_root = root / "ioq3"
        recorded_paths = {
            source_path
            for component in self.baseline["engine"]["licenseComponents"]
            for field in ("paths", "excludedPaths")
            for source_path in component[field]
        }
        for source_path in recorded_paths:
            target = engine_root / source_path
            source = ROOT / "ioq3" / source_path
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
        (engine_root / "code" / "tools" / "asm").mkdir(parents=True, exist_ok=True)
        (engine_root / "code" / "tools" / "stringify.c").touch()
        return engine_root

    def test_committed_baseline_is_valid(self) -> None:
        validate_baseline(self.baseline)

    def test_committed_repository_matches_schemas(self) -> None:
        validate_repository(ROOT, verify_git=False)

    def test_missing_schema_directory_fails_as_metadata_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MetadataError, "required schema directory"):
                validate_repository(Path(directory), verify_git=False)

    def test_committed_repository_matches_gitlink(self) -> None:
        if os.environ.get("ARENA_WITHOUT_GIT_METADATA") == "1":
            self.skipTest(
                "public source is mounted without linked-worktree Git metadata"
            )
        validate_repository(ROOT)

    def test_published_schema_rejects_empty_tool_records(self) -> None:
        schema = _load_json(ROOT / "schemas" / "baseline-lock.schema.json")
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"] = [{}, {}, {}]
        with self.assertRaisesRegex(MetadataError, "schema"):
            _validate_schema_instance(candidate, schema, schema, "baseline")

    def test_unsupported_schema_keyword_is_rejected(self) -> None:
        with self.assertRaisesRegex(MetadataError, "unsupported JSON Schema"):
            _check_supported_schema(
                {"allOf": [], "type": "string"}, "unsupported-schema"
            )

    def test_subschema_additional_properties_is_rejected(self) -> None:
        with self.assertRaisesRegex(MetadataError, "additionalProperties only"):
            _check_supported_schema(
                {
                    "additionalProperties": {"type": "string"},
                    "type": "object",
                },
                "unsupported-schema",
            )

    def test_ref_sibling_constraint_is_executed(self) -> None:
        schema = {
            "$defs": {"text": {"type": "string"}},
            "$ref": "#/$defs/text",
            "minLength": 2,
        }
        with self.assertRaisesRegex(MetadataError, "at least 2"):
            _validate_schema_instance("x", schema, schema, "value")

    def test_platform_digest_is_required(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["tools"][0]["platformDigest"]
        with self.assertRaisesRegex(MetadataError, "missing.*platformDigest"):
            validate_baseline(candidate)

    def test_index_and_platform_digest_must_differ(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][0]["indexDigest"] = candidate["tools"][0]["platformDigest"]
        with self.assertRaisesRegex(MetadataError, "must differ"):
            validate_baseline(candidate)

    def test_moving_oci_reference_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][0]["immutableRef"] = "docker.io/emscripten/emsdk:3.1.58"
        with self.assertRaisesRegex(MetadataError, "must be image@platformDigest"):
            validate_baseline(candidate)

    def test_tagged_oci_repository_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][0]["image"] += ":6.0.8"
        with self.assertRaisesRegex(MetadataError, "repository name"):
            validate_baseline(candidate)

    def test_human_tag_must_match_version(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][0]["humanTag"] = "latest"
        with self.assertRaisesRegex(MetadataError, "descriptive version"):
            validate_baseline(candidate)

    def test_chrome_url_must_match_version(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][2]["version"] = "153.0.0.0"
        with self.assertRaisesRegex(MetadataError, "Chrome version"):
            validate_baseline(candidate)

    def test_required_tool_set_is_closed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][2]["id"] = "ambient-browser"
        with self.assertRaisesRegex(MetadataError, "must contain exactly"):
            validate_baseline(candidate)

    def test_unknown_engine_component_license_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["licenseComponents"][0]["license"][
            "expression"
        ] = "LicenseRef-Unknown"
        with self.assertRaisesRegex(MetadataError, "allowed product-input license"):
            validate_baseline(candidate)

    def test_engine_component_inventory_is_closed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["licenseComponents"].pop()
        with self.assertRaisesRegex(MetadataError, "must contain exactly"):
            validate_baseline(candidate)

    def test_engine_core_exclusions_must_cover_every_exception_path(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        core = next(
            component
            for component in candidate["engine"]["licenseComponents"]
            if component["id"] == "ioq3-core"
        )
        core["excludedPaths"].pop()
        with self.assertRaisesRegex(MetadataError, "core-minus-exceptions"):
            validate_baseline(candidate)

    def test_engine_component_paths_must_not_overlap(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        curl = next(
            component
            for component in candidate["engine"]["licenseComponents"]
            if component["id"] == "curl-headers"
        )
        curl["paths"] = ["code/thirdparty/jpeg-9f/include"]
        with self.assertRaisesRegex(MetadataError, "must not overlap"):
            validate_baseline(candidate)

    def test_product_component_must_permit_distribution(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["licenseComponents"][0]["license"][
            "distribution"
        ] = "build-only-not-redistributed"
        with self.assertRaisesRegex(MetadataError, "permit product distribution"):
            validate_baseline(candidate)

    def test_build_tool_must_stay_out_of_distribution(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        lcc = next(
            component
            for component in candidate["engine"]["licenseComponents"]
            if component["id"] == "lcc-build-tool"
        )
        lcc["license"]["distribution"] = "product-source-and-binaries"
        with self.assertRaisesRegex(MetadataError, "keep the tool out"):
            validate_baseline(candidate)

    def test_engine_branch_is_identity_bearing(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["branch"] = "web"
        with self.assertRaisesRegex(MetadataError, "immutable WP0 pin"):
            validate_baseline(candidate)

    def test_engine_tree_rejects_unreviewed_thirdparty_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_root = self.write_minimal_engine_tree(root)
            (engine_root / "code" / "thirdparty" / "ambient-lib").mkdir()
            with self.assertRaisesRegex(MetadataError, "thirdparty entries differ"):
                verify_engine_tree(root, self.baseline)

    def test_engine_tree_rejects_missing_recorded_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_root = self.write_minimal_engine_tree(root)
            (engine_root / "code" / "client" / "snd_adpcm.c").unlink()
            with self.assertRaisesRegex(MetadataError, "paths absent"):
                verify_engine_tree(root, self.baseline)

    def test_engine_tree_rejects_unreviewed_tool_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_root = self.write_minimal_engine_tree(root)
            (engine_root / "code" / "tools" / "ambient-tool.c").touch()
            with self.assertRaisesRegex(MetadataError, "tool entries differ"):
                verify_engine_tree(root, self.baseline)

    def test_documented_identity_must_match_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "immutable-baseline.md").write_text(
                "# sha256:" + "0" * 64 + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(MetadataError, "current baseline identity"):
                verify_documented_baseline_identity(root, self.baseline)

    def test_unknown_metadata_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            shutil.copytree(ROOT / "locks", root / "locks")
            (root / "docs").mkdir()
            shutil.copy2(
                ROOT / "docs" / "immutable-baseline.md",
                root / "docs" / "immutable-baseline.md",
            )
            os.symlink(ROOT / "ioq3", root / "ioq3", target_is_directory=True)
            (root / "locks" / "unknown.json").write_text(
                json.dumps(
                    {"$schema": "https://example.invalid/unknown.schema.json"},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MetadataError, "uses unknown schema"):
                validate_repository(root, verify_git=False)

    def test_sha_license_evidence_requires_retrieval_date(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["tools"][2]["license"]["evidenceRetrievedAt"]
        with self.assertRaisesRegex(MetadataError, "evidenceRetrievedAt"):
            validate_baseline(candidate)

    def test_future_license_evidence_date_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"][2]["license"]["evidenceRetrievedAt"] = "2999-01-01"
        with self.assertRaisesRegex(MetadataError, "must not be in the future"):
            validate_baseline(candidate)

    def test_incomplete_preferred_source_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["engine"]["preferredSource"]["revision"]
        with self.assertRaisesRegex(MetadataError, "missing.*revision"):
            validate_baseline(candidate)

    def test_fedora_version_must_match_media(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["acceptancePlatform"]["version"] = "45"
        with self.assertRaisesRegex(MetadataError, "must be '44'"):
            validate_baseline(candidate)

    def test_relay_trust_contract_is_closed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["relayTrust"]["maximumCertificateValidityDays"] = 15
        with self.assertRaisesRegex(MetadataError, "must be 14"):
            validate_baseline(candidate)

    def test_version_relationship_distinguishes_downgrade(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["upstreamEvidence"]["upstreamEmscriptenVersion"] = "7.0.0"
        with self.assertRaisesRegex(MetadataError, "target-downgrade"):
            validate_baseline(candidate)

    @mock.patch("metadata._git_output")
    def test_wrong_gitlink_is_rejected(self, git_output: mock.Mock) -> None:
        expected = self.baseline["engine"]["commit"]
        git_output.side_effect = [
            "",
            self.baseline["engine"]["repository"],
            self.baseline["engine"]["branch"],
            "0" * 40,
            expected,
            "",
        ]
        with self.assertRaisesRegex(MetadataError, "engine.gitlink"):
            verify_engine_pin(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    def test_wrong_checkout_is_rejected(self, git_output: mock.Mock) -> None:
        expected = self.baseline["engine"]["commit"]
        git_output.side_effect = [
            "",
            self.baseline["engine"]["repository"],
            self.baseline["engine"]["branch"],
            expected,
            "0" * 40,
            "",
        ]
        with self.assertRaisesRegex(MetadataError, "engine.checkout"):
            verify_engine_pin(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    def test_private_submodule_url_is_rejected(self, git_output: mock.Mock) -> None:
        expected = self.baseline["engine"]["commit"]
        git_output.side_effect = [
            "",
            "git@github.com:Kaesual/ioq3.git",
            self.baseline["engine"]["branch"],
            expected,
            expected,
            "",
        ]
        with self.assertRaisesRegex(MetadataError, "engine.submoduleUrl"):
            verify_engine_pin(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    def test_wrong_submodule_branch_is_rejected(self, git_output: mock.Mock) -> None:
        expected = self.baseline["engine"]["commit"]
        git_output.side_effect = [
            "",
            self.baseline["engine"]["repository"],
            "web",
            expected,
            expected,
            "",
        ]
        with self.assertRaisesRegex(MetadataError, "engine.submoduleBranch"):
            verify_engine_pin(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    def test_unstaged_gitmodules_is_rejected(self, git_output: mock.Mock) -> None:
        git_output.side_effect = [".gitmodules"]
        with self.assertRaisesRegex(MetadataError, "engine.gitmodulesWorktree"):
            verify_engine_pin(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    def test_dirty_engine_checkout_is_rejected(self, git_output: mock.Mock) -> None:
        expected = self.baseline["engine"]["commit"]
        git_output.side_effect = [
            "",
            self.baseline["engine"]["repository"],
            self.baseline["engine"]["branch"],
            expected,
            expected,
            " M code/client/client.h",
        ]
        with self.assertRaisesRegex(MetadataError, "engine.checkoutWorktree"):
            verify_engine_pin(ROOT, self.baseline)

    def test_container_check_uses_locked_builder(self) -> None:
        builder = next(
            tool
            for tool in self.baseline["tools"]
            if tool["id"] == "emscripten-builder"
        )
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "check-container.sh"), "--print-image"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), builder["immutableRef"])


class MeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vector = load_fixture("locks/relay-measurement-vector.json")

    def test_committed_vector_is_valid(self) -> None:
        validate_measurement_vector(self.vector)

    def test_boundary_adjacent_case_is_required(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["directions"]["browserToServer"].remove(1313)
        with self.assertRaisesRegex(MetadataError, "must include 1313"):
            validate_measurement_vector(candidate)

    def test_decision_region_resolution_is_required(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["directions"]["browserToServer"].remove(1250)
        candidate["directions"]["serverToBrowser"].remove(1250)
        with self.assertRaisesRegex(MetadataError, "1250"):
            validate_measurement_vector(candidate)

    def test_direction_asymmetry_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["directions"]["serverToBrowser"].append(17000)
        with self.assertRaisesRegex(MetadataError, "same size vector"):
            validate_measurement_vector(candidate)

    def test_relay_overhead_is_fixed(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["framing"]["singleDatagramOverheadBytes"] = 40
        with self.assertRaisesRegex(MetadataError, "40 \\+ 2 byte contract"):
            validate_measurement_vector(candidate)

    def test_nonce_size_is_fixed_to_tag_threshold(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["payloadIdentification"]["minimumTaggedInnerBytes"] = 17
        with self.assertRaisesRegex(MetadataError, "must equal nonceBytes"):
            validate_measurement_vector(candidate)

    def test_reverse_packed_case_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["packedCases"][0]["direction"] = "serverToBrowser"
        with self.assertRaisesRegex(MetadataError, "browserToServer"):
            validate_measurement_vector(candidate)

    def test_duplicate_packed_case_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["packedCases"].append(copy.deepcopy(candidate["packedCases"][0]))
        with self.assertRaisesRegex(MetadataError, "duplicates"):
            validate_measurement_vector(candidate)

    def test_untagged_packed_datagram_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.vector)
        candidate["packedCases"][0]["sizes"][0] = 1
        with self.assertRaisesRegex(MetadataError, "must fit the nonce"):
            validate_measurement_vector(candidate)


class ArtifactManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")
        builder = next(
            tool
            for tool in self.baseline["tools"]
            if tool["id"] == "emscripten-builder"
        )
        self.manifest = {
            "$schema": ARTIFACT_SCHEMA,
            "artifacts": [
                {"path": "Release/ioquake3.js", "sha256": "a" * 64, "size": 123}
            ],
            "baselineIdentity": _canonical_json_identity(self.baseline),
            "baselineInputIds": ["emscripten-builder", "ioq3"],
            "digestAlgorithm": "sha256",
            "formatVersion": 1,
            "inputs": [
                {
                    "id": "emscripten-builder",
                    "identity": builder["immutableRef"],
                    "kind": "oci-image",
                },
                {
                    "id": "ioq3",
                    "identity": "git:" + self.baseline["engine"]["commit"],
                    "kind": "git",
                },
            ],
            "producer": {"commit": "c" * 40, "name": "arena-web WP1"},
        }

    def test_manifest_is_valid(self) -> None:
        validate_artifact_manifest(self.manifest, "manifest", baseline=self.baseline)

    def test_published_schema_executes_for_manifest_fixture(self) -> None:
        schema = _load_json(ROOT / "schemas" / "artifact-manifest.schema.json")
        _validate_schema_instance(self.manifest, schema, schema, "manifest")

    def test_parent_path_is_rejected_by_schema(self) -> None:
        schema = _load_json(ROOT / "schemas" / "artifact-manifest.schema.json")
        candidate = copy.deepcopy(self.manifest)
        candidate["artifacts"][0]["path"] = "../ioquake3.js"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "manifest")

    def test_unsorted_artifacts_are_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["artifacts"] = [
            {"path": "z.wasm", "sha256": "d" * 64, "size": 1},
            {"path": "a.js", "sha256": "e" * 64, "size": 1},
        ]
        with self.assertRaisesRegex(MetadataError, "sorted by path"):
            validate_artifact_manifest(candidate, "manifest")

    def test_moving_input_identity_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["inputs"][1]["identity"] = "main"
        with self.assertRaisesRegex(MetadataError, "git:<40-character commit>"):
            validate_artifact_manifest(candidate, "manifest")

    def test_baseline_input_disagreement_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["inputs"][0]["identity"] = (
            "docker.io/emscripten/emsdk@sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(MetadataError, "committed baseline"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_baseline_identity_disagreement_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["baselineIdentity"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(MetadataError, "committed baseline identity"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_baseline_input_cannot_be_present_but_undeclared(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        browser = next(
            tool
            for tool in self.baseline["tools"]
            if tool["id"] == "chrome-for-testing"
        )
        candidate["inputs"].insert(
            0,
            {
                "id": "chrome-for-testing",
                "identity": "sha256:" + browser["sha256"],
                "kind": "archive",
            },
        )
        with self.assertRaisesRegex(MetadataError, "does not declare baseline inputs"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_missing_required_baseline_input_is_rejected(self) -> None:
        for required in ARTIFACT_REQUIRED_BASELINE_INPUT_IDS:
            with self.subTest(required=required):
                candidate = copy.deepcopy(self.manifest)
                candidate["baselineInputIds"] = [
                    item for item in candidate["baselineInputIds"] if item != required
                ]
                candidate["inputs"] = [
                    item for item in candidate["inputs"] if item["id"] != required
                ]
                with self.assertRaisesRegex(MetadataError, "required baseline inputs"):
                    validate_artifact_manifest(
                        candidate,
                        "manifest",
                        baseline=self.baseline,
                        required_baseline_input_ids=(
                            ARTIFACT_REQUIRED_BASELINE_INPUT_IDS
                        ),
                    )

    def test_renamed_baseline_input_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["baselineInputIds"] = ["emscripten-builder-6.0.8", "ioq3"]
        candidate["inputs"][0]["id"] = "emscripten-builder-6.0.8"
        with self.assertRaisesRegex(MetadataError, "unknown baseline input"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)
        with self.assertRaisesRegex(MetadataError, "required baseline inputs"):
            validate_artifact_manifest(
                candidate,
                "manifest",
                baseline=self.baseline,
                required_baseline_input_ids=ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
            )

    def test_extra_pinned_build_input_is_allowed(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["inputs"].insert(
            1,
            {
                "id": "emscripten-port-sdl2",
                "identity": "sha256:" + "b" * 64,
                "kind": "archive",
            },
        )
        validate_artifact_manifest(
            candidate,
            "manifest",
            baseline=self.baseline,
            required_baseline_input_ids=ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
        )


class CommittedBrowserManifestTests(unittest.TestCase):
    """The committed evidence of the accepted WP1 browser build."""

    MANIFEST_PATH = "manifests/browser-client.json"

    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")
        self.manifest = load_fixture(self.MANIFEST_PATH)

    def test_repository_validator_covers_the_committed_manifest(self) -> None:
        validated = validate_repository(ROOT, verify_git=False)
        self.assertIn(ROOT / self.MANIFEST_PATH, validated)

    def test_committed_manifest_declares_the_required_baseline_inputs(self) -> None:
        self.assertEqual(
            sorted(ARTIFACT_REQUIRED_BASELINE_INPUT_IDS),
            self.manifest["baselineInputIds"],
        )
        validate_artifact_manifest(
            self.manifest,
            self.MANIFEST_PATH,
            baseline=self.baseline,
            required_baseline_input_ids=ARTIFACT_REQUIRED_BASELINE_INPUT_IDS,
        )

    def test_repository_validator_rejects_a_manifest_without_the_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            shutil.copytree(ROOT / "locks", root / "locks")
            (root / "docs").mkdir()
            shutil.copy2(
                ROOT / "docs" / "immutable-baseline.md",
                root / "docs" / "immutable-baseline.md",
            )
            os.symlink(ROOT / "ioq3", root / "ioq3", target_is_directory=True)
            (root / "manifests").mkdir()
            candidate = copy.deepcopy(self.manifest)
            candidate["baselineInputIds"] = ["emscripten-builder"]
            candidate["inputs"] = [
                item for item in candidate["inputs"] if item["id"] != "ioq3"
            ]
            (root / "manifests" / "browser-client.json").write_text(
                json.dumps(candidate, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MetadataError, "required baseline inputs"):
                validate_repository(root, verify_git=False)

    def test_committed_manifest_carries_no_retail_game_data(self) -> None:
        for artifact in self.manifest["artifacts"]:
            self.assertNotIn(
                Path(artifact["path"]).suffix.lower(),
                artifact_manifest.RETAIL_GAME_DATA_SUFFIXES,
            )

    def test_committed_manifest_is_what_the_generator_produces(self) -> None:
        extra_inputs = [
            item
            for item in self.manifest["inputs"]
            if item["id"] not in ARTIFACT_REQUIRED_BASELINE_INPUT_IDS
        ]
        generated = artifact_manifest.build_manifest(
            self.baseline,
            self.manifest["artifacts"],
            self.manifest["producer"]["commit"],
            extra_inputs=extra_inputs,
            producer_name=self.manifest["producer"]["name"],
        )
        self.assertEqual(self.manifest, generated)


class BrowserBuildManifestTests(unittest.TestCase):
    """The code that generates the WP1 browser artifact manifest."""

    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")

    def test_collect_artifacts_is_sorted_and_digested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseq3" / "vm").mkdir(parents=True)
            (root / "ioquake3.wasm").write_bytes(b"wasm")
            (root / "baseq3" / "vm" / "ui.qvm").write_bytes(b"qvm")
            artifacts = artifact_manifest.collect_artifacts(root)
        self.assertEqual(
            ["baseq3/vm/ui.qvm", "ioquake3.wasm"],
            [artifact["path"] for artifact in artifacts],
        )
        self.assertEqual(hashlib.sha256(b"wasm").hexdigest(), artifacts[1]["sha256"])
        self.assertEqual(4, artifacts[1]["size"])

    def test_collect_artifacts_rejects_retail_game_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseq3").mkdir()
            (root / "ioquake3.wasm").write_bytes(b"wasm")
            (root / "baseq3" / "pak0.pk3").write_bytes(b"retail")
            with self.assertRaisesRegex(MetadataError, "retail game data"):
                artifact_manifest.collect_artifacts(root)

    def test_collect_artifacts_rejects_a_symlinked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ioquake3.wasm").write_bytes(b"wasm")
            (root / "alias.wasm").symlink_to(root / "ioquake3.wasm")
            with self.assertRaisesRegex(MetadataError, "not a regular file"):
                artifact_manifest.collect_artifacts(root)

    def test_collect_artifacts_rejects_an_empty_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MetadataError, "no build output"):
                artifact_manifest.collect_artifacts(Path(directory))

    def test_collect_artifacts_rejects_a_symlinked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseq3").mkdir()
            (root / "baseq3" / "ui.qvm").write_bytes(b"qvm")
            (root / "vm").symlink_to(root / "baseq3", target_is_directory=True)
            with self.assertRaisesRegex(MetadataError, "symlinked directory"):
                artifact_manifest.collect_artifacts(root)

    def test_collect_artifacts_rejects_a_missing_build_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "Release"
            with self.assertRaisesRegex(MetadataError, "not a build output directory"):
                artifact_manifest.collect_artifacts(missing)

    def test_baseline_inputs_requires_the_locked_builder(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["tools"] = [
            tool for tool in candidate["tools"] if tool["id"] != "emscripten-builder"
        ]
        with self.assertRaisesRegex(MetadataError, "emscripten-builder"):
            artifact_manifest.baseline_inputs(candidate)

    def test_build_script_derives_source_date_epoch_from_the_pin(self) -> None:
        if os.environ.get("ARENA_WITHOUT_GIT_METADATA") == "1":
            self.skipTest(
                "public source is mounted without linked-worktree Git metadata"
            )
        script = (ROOT / "scripts" / "build-browser.sh").read_text(encoding="utf-8")
        match = re.search(
            r"(?m)^expected_source_date_epoch=([0-9]+)$",
            script,
        )
        self.assertIsNotNone(match, "build-browser.sh must pin SOURCE_DATE_EPOCH")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT / self.baseline["engine"]["submodulePath"]),
                "show",
                "-s",
                "--format=%ct",
                self.baseline["engine"]["commit"],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(match.group(1), result.stdout.strip())

    def test_port_archive_pin_matches_the_committed_manifest(self) -> None:
        script = (ROOT / "scripts" / "fetch-emscripten-ports.sh").read_text(
            encoding="utf-8"
        )
        match = re.search(r'(?m)^port_sha256="([0-9a-f]{64})"$', script)
        self.assertIsNotNone(match, "the port pin must record a SHA-256")
        manifest = load_fixture("manifests/browser-client.json")
        port_input = next(
            item for item in manifest["inputs"] if item["id"] == "emscripten-port-sdl2"
        )
        self.assertEqual(f"sha256:{match.group(1)}", port_input["identity"])

    def test_build_script_uses_the_locked_builder(self) -> None:
        builder = next(
            tool
            for tool in self.baseline["tools"]
            if tool["id"] == "emscripten-builder"
        )
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "build-browser.sh"), "--print-image"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), builder["immutableRef"])

    def test_baseline_inputs_helper_reports_locked_identities(self) -> None:
        expected = {
            "baseline-identity": _canonical_json_identity(self.baseline),
            "engine-commit": self.baseline["engine"]["commit"],
            "engine-submodule-path": self.baseline["engine"]["submodulePath"],
        }
        for field, value in expected.items():
            with self.subTest(field=field):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "baseline-inputs.py"),
                        field,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout.strip(), value)


class ContentProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")
        self.allowed = {"CC-BY-SA-4.0", "GPL-2.0-or-later"}
        self.provenance = {
            "$schema": CONTENT_SCHEMA,
            "baselineIdentity": _canonical_json_identity(self.baseline),
            "formatVersion": 1,
            "members": [
                {
                    "licenseExpression": "CC-BY-SA-4.0",
                    "noticePaths": ["notices/map.txt"],
                    "obligations": [
                        "attribution",
                        "license-notice",
                        "share-alike",
                    ],
                    "path": "maps/example.bsp",
                    "role": "asset",
                    "sha256": "a" * 64,
                    "size": 456,
                    "sourceId": "example-map",
                    "sourcePath": "build/maps/example.bsp",
                    "transformation": "deterministic q3map2 build",
                },
                {
                    "licenseExpression": "CC-BY-SA-4.0",
                    "noticePaths": [],
                    "obligations": [
                        "attribution",
                        "license-notice",
                        "share-alike",
                    ],
                    "path": "notices/map.txt",
                    "role": "notice",
                    "sha256": "b" * 64,
                    "size": 123,
                    "sourceId": "example-map",
                    "sourcePath": "LICENSE.txt",
                    "transformation": "copied verbatim",
                },
            ],
            "package": {"id": "prototype-content", "name": "Prototype content"},
            "sources": [
                {
                    "id": "example-map",
                    "licenseEvidenceUrl": "https://example.invalid/LICENSE",
                    "licenseExpression": "CC-BY-SA-4.0",
                    "preferredSourceRevision": "git:" + "b" * 40,
                    "preferredSourceUrl": "https://example.invalid/source.git",
                    "sourceIdentity": "git:" + "b" * 40,
                    "sourceUrl": "https://example.invalid/source.git",
                }
            ],
        }

    def test_provenance_is_valid(self) -> None:
        validate_content_provenance(
            self.provenance,
            "provenance",
            allowed_licenses=self.allowed,
            baseline=self.baseline,
        )

    def test_published_schema_executes_for_provenance_fixture(self) -> None:
        schema = _load_json(ROOT / "schemas" / "content-provenance.schema.json")
        _validate_schema_instance(self.provenance, schema, schema, "provenance")

    def test_schema_rejects_moving_source_identity(self) -> None:
        schema = _load_json(ROOT / "schemas" / "content-provenance.schema.json")
        candidate = copy.deepcopy(self.provenance)
        candidate["sources"][0]["sourceIdentity"] = "main"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "provenance")

    def test_schema_rejects_parent_source_path(self) -> None:
        schema = _load_json(ROOT / "schemas" / "content-provenance.schema.json")
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["sourcePath"] = "../../asset.map"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "provenance")

    def test_member_without_source_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["sourceId"] = "missing"
        with self.assertRaisesRegex(MetadataError, "declared source"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_non_free_member_license_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["licenseExpression"] = "CC-BY-NC-4.0"
        with self.assertRaisesRegex(MetadataError, "allowed product-input license"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_missing_required_obligation_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["obligations"].remove("attribution")
        with self.assertRaisesRegex(MetadataError, "must include"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_all_gnu_copyleft_families_require_source_obligation(self) -> None:
        for expression in ("AGPL-3.0-or-later", "LGPL-2.0-or-later"):
            with self.subTest(expression=expression):
                candidate = copy.deepcopy(self.provenance)
                candidate["sources"][0]["licenseExpression"] = expression
                for member in candidate["members"]:
                    member["licenseExpression"] = expression
                with self.assertRaisesRegex(MetadataError, "copyleft-source"):
                    validate_content_provenance(
                        candidate,
                        "provenance",
                        allowed_licenses=self.allowed | {expression},
                    )

    def test_compound_license_expression_derives_every_obligation(self) -> None:
        expression = "CC-BY-SA-4.0 AND GPL-2.0-or-later"
        candidate = copy.deepcopy(self.provenance)
        candidate["sources"][0]["licenseExpression"] = expression
        for member in candidate["members"]:
            member["licenseExpression"] = expression
        with self.assertRaisesRegex(MetadataError, "copyleft-source"):
            validate_content_provenance(
                candidate,
                "provenance",
                allowed_licenses=self.allowed | {expression},
            )

    def test_empty_notice_binding_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["noticePaths"] = []
        with self.assertRaisesRegex(MetadataError, "packaged notice member"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_dangling_notice_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][0]["noticePaths"] = ["notices/missing.txt"]
        with self.assertRaisesRegex(MetadataError, "declared provenance member"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_notice_path_must_name_notice_role(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["members"][1]["role"] = "metadata"
        candidate["members"][1]["noticePaths"] = ["notices/map.txt"]
        with self.assertRaisesRegex(MetadataError, "role is 'notice'"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_baseline_identity_disagreement_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["baselineIdentity"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(MetadataError, "committed baseline identity"):
            validate_content_provenance(
                candidate,
                "provenance",
                allowed_licenses=self.allowed,
                baseline=self.baseline,
            )


class CanonicalJsonTests(unittest.TestCase):
    def test_duplicate_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"a": 1, "a": 2}\n', encoding="utf-8")
            with self.assertRaisesRegex(MetadataError, "duplicate JSON key"):
                _load_json(path)

    def test_noncanonical_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unordered.json"
            path.write_text('{"b": 1, "a": 2}\n', encoding="utf-8")
            with self.assertRaisesRegex(MetadataError, "canonical sorted"):
                _load_json(path)

    def test_crlf_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crlf.json"
            path.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
            with self.assertRaisesRegex(MetadataError, "canonical sorted"):
                _load_json(path)


if __name__ == "__main__":
    unittest.main()
