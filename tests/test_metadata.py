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
    REDISTRIBUTED_IMAGE_KIND,
    REQUIRED_REDISTRIBUTED_IMAGES,
    MetadataError,
    _baseline_input_identities,
    _canonical_json_identity,
    _check_supported_schema,
    _load_json,
    _validate_license,
    _validate_schema_instance,
    validate_artifact_manifest,
    validate_baseline,
    validate_content_provenance,
    validate_measurement_vector,
    validate_repository,
    verify_documented_baseline_identity,
    verify_engine_patch_series,
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
        candidate["engine"]["branch"] = "main"
        with self.assertRaisesRegex(MetadataError, "must be web"):
            validate_baseline(candidate)

    def test_committed_engine_enumerates_what_the_pin_adds(self) -> None:
        engine = self.baseline["engine"]
        self.assertNotEqual(engine["commit"], engine["upstreamBase"]["commit"])
        self.assertTrue(engine["appliedPatches"])
        for patch in engine["appliedPatches"]:
            self.assertTrue(patch["paths"])
            self.assertEqual(patch["upstreamStatus"], "not-submitted")

    def test_patched_pin_must_enumerate_its_patches(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["engine"]["appliedPatches"]
        with self.assertRaisesRegex(MetadataError, "must enumerate every patch"):
            validate_baseline(candidate)

    def test_empty_patch_list_is_not_a_second_way_of_saying_unmodified(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"] = []
        with self.assertRaisesRegex(MetadataError, "must enumerate every patch"):
            validate_baseline(candidate)

    def test_unmodified_pin_may_not_claim_a_patch(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["upstreamBase"]["commit"] = candidate["engine"]["commit"]
        with self.assertRaisesRegex(MetadataError, "must be absent"):
            validate_baseline(candidate)

    def test_unmodified_pin_omits_the_patch_list(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["upstreamBase"]["commit"] = candidate["engine"]["commit"]
        del candidate["engine"]["appliedPatches"]
        validate_baseline(candidate)

    def test_engine_requires_an_upstream_base(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["engine"]["upstreamBase"]
        with self.assertRaisesRegex(MetadataError, "upstreamBase"):
            validate_baseline(candidate)

    def test_upstream_base_repository_must_be_public_https(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["upstreamBase"][
            "repository"
        ] = "git@github.com:ioquake3/ioq3.git"
        with self.assertRaisesRegex(MetadataError, "upstreamBase.repository"):
            validate_baseline(candidate)

    def test_upstream_base_rejects_unknown_fields(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["upstreamBase"]["tag"] = "5.17.0"
        with self.assertRaisesRegex(MetadataError, "upstreamBase"):
            validate_baseline(candidate)

    def test_patch_record_rejects_unknown_fields(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"][0]["author"] = "somebody"
        with self.assertRaisesRegex(MetadataError, "appliedPatches"):
            validate_baseline(candidate)

    def test_patch_upstream_status_is_a_closed_vocabulary(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"][0]["upstreamStatus"] = "merged-upstream"
        with self.assertRaisesRegex(MetadataError, "upstreamStatus"):
            validate_baseline(candidate)

    def test_patch_id_must_be_a_slug(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"][0]["id"] = "Renderer Fix"
        with self.assertRaisesRegex(MetadataError, "lowercase hyphenated"):
            validate_baseline(candidate)

    def test_patch_rationale_must_be_one_substantial_line(self) -> None:
        for rationale in ("too short", "a long enough rationale\nwith two lines"):
            with self.subTest(rationale=rationale):
                candidate = copy.deepcopy(self.baseline)
                candidate["engine"]["appliedPatches"][0]["rationale"] = rationale
                with self.assertRaisesRegex(MetadataError, "one line"):
                    validate_baseline(candidate)

    def test_patch_paths_must_be_normalized_relative_paths(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"][0]["paths"] = [
            "../code/renderergl2/tr_glsl.c"
        ]
        with self.assertRaisesRegex(MetadataError, "normalized relative"):
            validate_baseline(candidate)

    def test_patch_paths_must_be_sorted_and_unique(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["appliedPatches"][0]["paths"] = [
            "code/renderergl2/tr_glsl.c",
            "code/renderercommon/tr_types.h",
        ]
        with self.assertRaisesRegex(MetadataError, "must be sorted"):
            validate_baseline(candidate)

    def test_patch_ids_must_be_unique_and_sorted(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        first = candidate["engine"]["appliedPatches"][0]
        candidate["engine"]["appliedPatches"] = [
            first,
            copy.deepcopy(first),
        ]
        with self.assertRaisesRegex(MetadataError, "must not contain duplicates"):
            validate_baseline(candidate)

        candidate = copy.deepcopy(self.baseline)
        first = candidate["engine"]["appliedPatches"][0]
        later = copy.deepcopy(first)
        later["id"] = "aaa-earlier-by-sort-order"
        candidate["engine"]["appliedPatches"] = [first, later]
        with self.assertRaisesRegex(MetadataError, "must be sorted by id"):
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
            "main",
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

    def test_committed_patch_series_is_exactly_the_real_diff(self) -> None:
        """The enumerated series, against the submodule this checkout has."""
        verify_engine_patch_series(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    @mock.patch("metadata._git_is_ancestor")
    def test_unmodified_pin_needs_no_git_inspection(
        self, is_ancestor: mock.Mock, git_output: mock.Mock
    ) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["engine"]["upstreamBase"]["commit"] = candidate["engine"]["commit"]
        del candidate["engine"]["appliedPatches"]
        verify_engine_patch_series(ROOT, candidate)
        is_ancestor.assert_not_called()
        git_output.assert_not_called()

    @mock.patch("metadata._git_is_ancestor", return_value=False)
    def test_upstream_base_must_be_an_ancestor_of_the_pin(
        self, is_ancestor: mock.Mock
    ) -> None:
        with self.assertRaisesRegex(MetadataError, "is not an ancestor"):
            verify_engine_patch_series(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    @mock.patch("metadata._git_is_ancestor", return_value=True)
    def test_undeclared_changed_path_is_rejected(
        self, is_ancestor: mock.Mock, git_output: mock.Mock
    ) -> None:
        declared = {
            path
            for patch in self.baseline["engine"]["appliedPatches"]
            for path in patch["paths"]
        }
        git_output.return_value = "\n".join(
            sorted(declared | {"code/client/client.h"})
        )
        with self.assertRaisesRegex(MetadataError, "undeclared changed paths"):
            verify_engine_patch_series(ROOT, self.baseline)

    @mock.patch("metadata._git_output")
    @mock.patch("metadata._git_is_ancestor", return_value=True)
    def test_declared_path_that_does_not_differ_is_rejected(
        self, is_ancestor: mock.Mock, git_output: mock.Mock
    ) -> None:
        git_output.return_value = ""
        with self.assertRaisesRegex(
            MetadataError, "declared paths that do not differ"
        ):
            verify_engine_patch_series(ROOT, self.baseline)

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


class RedistributedProductImageTests(unittest.TestCase):
    """The runtime base arena-web ships rather than only builds with."""

    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")

    def only_image(self, candidate: dict) -> dict:
        return candidate["redistributedProductImages"][0]

    def test_committed_image_is_valid(self) -> None:
        validate_baseline(self.baseline)

    def test_published_schema_executes_for_the_committed_image(self) -> None:
        schema = _load_json(ROOT / "schemas" / "baseline-lock.schema.json")
        _validate_schema_instance(self.baseline, schema, schema, "baseline")

    def test_schema_rejects_a_moving_image_reference(self) -> None:
        schema = _load_json(ROOT / "schemas" / "baseline-lock.schema.json")
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["immutableRef"] = "docker.io/library/debian:13-slim"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "baseline")

    def test_schema_rejects_an_unknown_redistribution_obligation(self) -> None:
        schema = _load_json(ROOT / "schemas" / "baseline-lock.schema.json")
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["redistributionObligations"] = [
            "license-notice",
            "no-obligation-at-all",
        ]
        with self.assertRaisesRegex(MetadataError, "must be one of"):
            _validate_schema_instance(candidate, schema, schema, "baseline")

    def test_redistributed_collection_is_required(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["redistributedProductImages"]
        with self.assertRaisesRegex(
            MetadataError, "missing.*redistributedProductImages"
        ):
            validate_baseline(candidate)

    def test_empty_redistributed_collection_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["redistributedProductImages"] = []
        with self.assertRaisesRegex(MetadataError, "must not be empty"):
            validate_baseline(candidate)

    def test_unknown_image_field_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(MetadataError, "unknown.*digest"):
            validate_baseline(candidate)

    def test_missing_corresponding_source_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del self.only_image(candidate)["correspondingSource"]
        with self.assertRaisesRegex(MetadataError, "missing.*correspondingSource"):
            validate_baseline(candidate)

    def test_image_kind_is_identity_bearing(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["kind"] = "oci-image"
        with self.assertRaisesRegex(MetadataError, "redistributed-product-image"):
            validate_baseline(candidate)

    def test_image_index_and_platform_digest_must_differ(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        image = self.only_image(candidate)
        image["indexDigest"] = image["platformDigest"]
        with self.assertRaisesRegex(MetadataError, "must differ"):
            validate_baseline(candidate)

    def test_tagged_image_repository_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        image = self.only_image(candidate)
        image["image"] += ":13-slim"
        image["immutableRef"] = f"{image['image']}@{image['platformDigest']}"
        with self.assertRaisesRegex(MetadataError, "repository name"):
            validate_baseline(candidate)

    def test_moving_image_reference_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["immutableRef"] = (
            "docker.io/library/debian@sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(MetadataError, "must be image@platformDigest"):
            validate_baseline(candidate)

    def test_image_platform_is_fixed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["platform"] = "linux/arm64"
        with self.assertRaisesRegex(MetadataError, "linux/amd64"):
            validate_baseline(candidate)

    def test_image_human_tag_must_match_version(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["humanTag"] = "latest"
        with self.assertRaisesRegex(MetadataError, "descriptive version"):
            validate_baseline(candidate)

    def test_tool_only_license_class_is_rejected_for_an_image(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        image = self.only_image(candidate)
        image["license"]["expression"] = "LicenseRef-Ubuntu-Image-Aggregate"
        with self.assertRaisesRegex(
            MetadataError, "registered redistributed-image LicenseRef"
        ):
            validate_baseline(candidate)

    def test_product_input_license_class_is_rejected_for_an_image(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["license"]["expression"] = "GPL-2.0-or-later"
        with self.assertRaisesRegex(
            MetadataError, "registered redistributed-image LicenseRef"
        ):
            validate_baseline(candidate)

    def test_image_must_declare_the_redistribution_boundary(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["license"][
            "distribution"
        ] = "build-only-not-redistributed"
        with self.assertRaisesRegex(
            MetadataError, "redistributed product-image boundary"
        ):
            validate_baseline(candidate)

    def test_image_license_evidence_must_name_an_in_image_path(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del self.only_image(candidate)["license"]["evidencePath"]
        with self.assertRaisesRegex(MetadataError, "the image itself carries"):
            validate_baseline(candidate)

    def test_image_license_evidence_must_be_the_image(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        image = self.only_image(candidate)
        image["license"]["evidenceIdentity"] = image["indexDigest"]
        with self.assertRaisesRegex(MetadataError, "exact redistributed image"):
            validate_baseline(candidate)

    def test_unknown_license_class_fails_closed(self) -> None:
        image = self.only_image(self.baseline)
        with self.assertRaisesRegex(MetadataError, "unknown license class"):
            _validate_license(
                image["license"],
                "image.license",
                set(),
                set(),
                set(),
                license_class="anything-goes",
            )

    def test_generic_license_gate_refuses_the_redistributed_class(self) -> None:
        """The class's gate is one unit; this entry point cannot half-grant it."""
        image = self.only_image(self.baseline)
        with self.assertRaisesRegex(MetadataError, "unknown license class"):
            _validate_license(
                image["license"],
                "image.license",
                set(),
                set(),
                {image["license"]["expression"]},
                license_class=REDISTRIBUTED_IMAGE_KIND,
            )

    def test_malformed_index_digest_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["indexDigest"] = "sha256:not-a-digest"
        with self.assertRaisesRegex(MetadataError, "must be sha256:"):
            validate_baseline(candidate)

    def test_malformed_platform_digest_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["platformDigest"] = "sha256:" + "0" * 63
        with self.assertRaisesRegex(MetadataError, "must be sha256:"):
            validate_baseline(candidate)

    def test_build_only_tool_image_may_not_be_redistributed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        builder = next(
            tool for tool in candidate["tools"] if tool["id"] == "native-builder-base"
        )
        image = self.only_image(candidate)
        for field in ("image", "immutableRef", "indexDigest", "platformDigest"):
            image[field] = builder[field]
        with self.assertRaisesRegex(
            MetadataError, "'native-builder-base' pins as build-only"
        ):
            validate_baseline(candidate)

    def test_build_only_tool_index_may_not_be_redistributed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        builder = next(
            tool for tool in candidate["tools"] if tool["id"] == "emscripten-builder"
        )
        self.only_image(candidate)["indexDigest"] = builder["indexDigest"]
        with self.assertRaisesRegex(
            MetadataError, "'emscripten-builder' pins as build-only"
        ):
            validate_baseline(candidate)

    def test_unknown_corresponding_source_field_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["correspondingSource"]["note"] = "see the archive"
        with self.assertRaisesRegex(MetadataError, "unknown.*note"):
            validate_baseline(candidate)

    def test_unknown_redistribution_obligation_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["redistributionObligations"] = [
            "license-notice",
            "preserve-copyright-files",
            "trust-the-vendor",
        ]
        with self.assertRaisesRegex(MetadataError, "unknown obligations"):
            validate_baseline(candidate)

    def test_missing_redistribution_obligation_is_rejected(self) -> None:
        for obligation in ("license-notice", "preserve-copyright-files"):
            with self.subTest(obligation=obligation):
                candidate = copy.deepcopy(self.baseline)
                image = self.only_image(candidate)
                image["redistributionObligations"] = [
                    item
                    for item in image["redistributionObligations"]
                    if item != obligation
                ]
                with self.assertRaisesRegex(MetadataError, "must include"):
                    validate_baseline(candidate)

    def test_unavailable_preferred_source_is_rejected_for_an_image(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["preferredSource"] = {
            "reason": "The vendor does not publish the image build definition.",
            "status": "unavailable",
        }
        with self.assertRaisesRegex(MetadataError, "obtainable preferred source"):
            validate_baseline(candidate)

    def test_corresponding_source_form_is_fixed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["correspondingSource"][
            "form"
        ] = "vendor-support-contract"
        with self.assertRaisesRegex(MetadataError, "distribution-source-archive"):
            validate_baseline(candidate)

    def test_corresponding_source_url_must_be_https(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["correspondingSource"][
            "url"
        ] = "http://snapshot.debian.org/archive/debian/20260824T000000Z/"
        with self.assertRaisesRegex(MetadataError, "absolute HTTPS URL"):
            validate_baseline(candidate)

    def test_unknown_corresponding_source_obligation_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["correspondingSource"]["obligations"] = [
            "ask-nicely",
            "complete-corresponding-source",
            "public-archive-availability",
        ]
        with self.assertRaisesRegex(MetadataError, "unknown obligations"):
            validate_baseline(candidate)

    def test_missing_corresponding_source_obligation_is_rejected(self) -> None:
        for obligation in (
            "complete-corresponding-source",
            "public-archive-availability",
        ):
            with self.subTest(obligation=obligation):
                candidate = copy.deepcopy(self.baseline)
                corresponding = self.only_image(candidate)["correspondingSource"]
                corresponding["obligations"] = [
                    item for item in corresponding["obligations"] if item != obligation
                ]
                with self.assertRaisesRegex(MetadataError, "must include"):
                    validate_baseline(candidate)

    def test_duplicate_image_id_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["redistributedProductImages"].append(
            copy.deepcopy(self.only_image(candidate))
        )
        with self.assertRaisesRegex(MetadataError, "must not contain duplicates"):
            validate_baseline(candidate)

    def test_unsorted_images_are_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        second = copy.deepcopy(self.only_image(candidate))
        second["id"] = "another-runtime-base"
        candidate["redistributedProductImages"].append(second)
        with self.assertRaisesRegex(MetadataError, "must be sorted by id"):
            validate_baseline(candidate)

    def test_required_image_set_is_closed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["id"] = "ambient-runtime-base"
        with self.assertRaisesRegex(MetadataError, "must contain exactly"):
            validate_baseline(candidate)

    def test_image_role_is_closed(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.only_image(candidate)["role"] = "native-builder-base"
        with self.assertRaisesRegex(MetadataError, "reviewed image role"):
            validate_baseline(candidate)

    def test_committed_image_matches_the_reviewed_contract(self) -> None:
        images = self.baseline["redistributedProductImages"]
        self.assertEqual(
            sorted(REQUIRED_REDISTRIBUTED_IMAGES),
            [image["id"] for image in images],
        )
        image = images[0]
        self.assertEqual(REDISTRIBUTED_IMAGE_KIND, image["kind"])
        self.assertEqual(
            f"{image['image']}@{image['platformDigest']}", image["immutableRef"]
        )
        self.assertEqual(image["platformDigest"], image["license"]["evidenceIdentity"])
        self.assertNotEqual(
            self.baseline["tools"][1]["immutableRef"], image["immutableRef"]
        )
        self.assertEqual(
            ["license-notice", "preserve-copyright-files"],
            image["redistributionObligations"],
        )
        # The archive is Debian's, not arena-web's, so the record also carries
        # the offer arena-web owes in its own name for the copyleft packages.
        self.assertEqual(
            [
                "complete-corresponding-source",
                "public-archive-availability",
                "written-offer-on-request",
            ],
            image["correspondingSource"]["obligations"],
        )
        # evidenceUrl is a locator for this class, so it has to be a pinned one.
        self.assertRegex(
            image["license"]["evidenceUrl"],
            r"^https://github\.com/docker-library/repo-info/blob/[0-9a-f]{40}/",
        )


class LicensePolicyTests(unittest.TestCase):
    """The three license registries are separate gates, not one pool."""

    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")

    def test_redistributed_image_registry_is_required(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        del candidate["licensePolicy"]["redistributedImageLicenseRefs"]
        with self.assertRaisesRegex(
            MetadataError, "missing.*redistributedImageLicenseRefs"
        ):
            validate_baseline(candidate)

    def test_redistributed_image_registry_rejects_a_plain_expression(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["licensePolicy"]["redistributedImageLicenseRefs"] = ["GPL-2.0-only"]
        with self.assertRaisesRegex(MetadataError, "invalid LicenseRef"):
            validate_baseline(candidate)

    def test_product_and_tool_registries_must_stay_disjoint(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["licensePolicy"]["productInputLicenseRefs"] = sorted(
            candidate["licensePolicy"]["productInputLicenseRefs"]
            + ["LicenseRef-LCC-1998"]
        )
        with self.assertRaisesRegex(
            MetadataError,
            "must keep productInputLicenseRefs and toolOnlyLicenseRefs disjoint",
        ):
            validate_baseline(candidate)

    def test_image_and_tool_registries_must_stay_disjoint(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["licensePolicy"]["toolOnlyLicenseRefs"] = sorted(
            candidate["licensePolicy"]["toolOnlyLicenseRefs"]
            + ["LicenseRef-Debian-Image-Aggregate"]
        )
        with self.assertRaisesRegex(MetadataError, "disjoint"):
            validate_baseline(candidate)

    def test_image_and_product_registries_must_stay_disjoint(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        candidate["licensePolicy"]["productInputLicenseRefs"] = sorted(
            candidate["licensePolicy"]["productInputLicenseRefs"]
            + ["LicenseRef-Debian-Image-Aggregate"]
        )
        with self.assertRaisesRegex(MetadataError, "disjoint"):
            validate_baseline(candidate)

    def test_tool_gate_still_rejects_a_redistributed_image_reference(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        builder = next(
            tool for tool in candidate["tools"] if tool["id"] == "emscripten-builder"
        )
        builder["license"]["expression"] = "LicenseRef-Debian-Image-Aggregate"
        with self.assertRaisesRegex(MetadataError, "registered tool-only LicenseRef"):
            validate_baseline(candidate)

    def test_tool_gate_still_rejects_a_redistribution_boundary(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        builder = next(
            tool for tool in candidate["tools"] if tool["id"] == "emscripten-builder"
        )
        builder["license"]["distribution"] = "product-image-redistributed"
        with self.assertRaisesRegex(MetadataError, "keep the tool out"):
            validate_baseline(candidate)


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


class RedistributedImageBaselineInputTests(unittest.TestCase):
    """A generated image may declare the runtime base it ships as an input.

    The WP0 amendment pinned the runtime base without letting anything consume
    it: `_baseline_input_identities` mapped only the engine and `tools[]`, so a
    manifest naming `server-runtime-base` failed closed. WP5 needs exactly that
    declaration, because for a server image the base is not a tool that vanishes
    when the build ends — it is most of the distributed bytes.
    """

    def setUp(self) -> None:
        self.baseline = load_fixture("locks/baseline.json")
        self.image = next(
            image
            for image in self.baseline["redistributedProductImages"]
            if image["id"] == "server-runtime-base"
        )
        self.builder = next(
            tool
            for tool in self.baseline["tools"]
            if tool["id"] == "native-builder-base"
        )
        self.manifest = {
            "$schema": ARTIFACT_SCHEMA,
            "artifacts": [
                {"path": "opt/arena-web/ioq3ded", "sha256": "a" * 64, "size": 4096}
            ],
            "baselineIdentity": _canonical_json_identity(self.baseline),
            "baselineInputIds": [
                "ioq3",
                "native-builder-base",
                "server-runtime-base",
            ],
            "digestAlgorithm": "sha256",
            "formatVersion": 1,
            "inputs": [
                {
                    "id": "ioq3",
                    "identity": "git:" + self.baseline["engine"]["commit"],
                    "kind": "git",
                },
                {
                    "id": "native-builder-base",
                    "identity": self.builder["immutableRef"],
                    "kind": "oci-image",
                },
                {
                    "id": "server-runtime-base",
                    "identity": self.image["immutableRef"],
                    "kind": "oci-image",
                },
            ],
            "producer": {"commit": "d" * 40, "name": "arena-web WP5"},
        }

    def test_runtime_base_is_an_accepted_baseline_input(self) -> None:
        validate_artifact_manifest(
            self.manifest,
            "manifest",
            baseline=self.baseline,
            required_baseline_input_ids=("ioq3", "server-runtime-base"),
        )

    def test_identities_cover_all_three_collections(self) -> None:
        identities = _baseline_input_identities(self.baseline)
        self.assertEqual(identities["ioq3"][0], "git")
        self.assertEqual(
            identities["native-builder-base"],
            ("oci-image", self.builder["immutableRef"]),
        )
        self.assertEqual(
            identities["server-runtime-base"],
            ("oci-image", self.image["immutableRef"]),
        )
        self.assertEqual(
            identities["chrome-for-testing"][0],
            "archive",
            "the archive branch must stay reachable",
        )

    def test_wrong_runtime_base_digest_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["inputs"][2]["identity"] = "docker.io/library/debian@sha256:" + "0" * 64
        with self.assertRaisesRegex(MetadataError, "committed baseline"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_runtime_base_declared_as_the_wrong_kind_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["inputs"][2]["kind"] = "archive"
        candidate["inputs"][2]["identity"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(MetadataError, "committed baseline"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_renamed_runtime_base_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["baselineInputIds"] = [
            "ioq3",
            "native-builder-base",
            "server-runtime-base-13",
        ]
        candidate["inputs"][2]["id"] = "server-runtime-base-13"
        with self.assertRaisesRegex(MetadataError, "unknown baseline input"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_present_but_undeclared_runtime_base_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.manifest)
        candidate["baselineInputIds"] = ["ioq3", "native-builder-base"]
        with self.assertRaisesRegex(MetadataError, "does not declare baseline inputs"):
            validate_artifact_manifest(candidate, "manifest", baseline=self.baseline)

    def test_an_id_in_two_collections_is_refused(self) -> None:
        # A baseline this shape cannot pass validate_baseline — both id sets are
        # closed — but the mapping is the one place a collision would silently
        # resolve to one record, so it refuses rather than picks.
        candidate = copy.deepcopy(self.baseline)
        candidate["redistributedProductImages"][0]["id"] = "native-builder-base"
        with self.assertRaisesRegex(MetadataError, "two input collections"):
            _baseline_input_identities(candidate)

    def test_a_record_that_shadows_the_engine_id_is_refused(self) -> None:
        # The engine registers first, so a tool or an image claiming its id is
        # the other order of the same collision and has to fail the same way.
        for collection, index in (("tools", 1), ("redistributedProductImages", 0)):
            with self.subTest(collection=collection):
                candidate = copy.deepcopy(self.baseline)
                candidate[collection][index]["id"] = candidate["engine"]["id"]
                with self.assertRaisesRegex(MetadataError, "two input collections"):
                    _baseline_input_identities(candidate)

    def test_the_committed_server_manifest_agrees_with_the_baseline(self) -> None:
        # Deliberately not a skip. The manifest is committed evidence, not a
        # build artifact: if it is missing, the gate has to go red.
        path = ROOT / "provenance" / "arena-web-server.json"
        self.assertTrue(path.is_file(), f"{path} is committed evidence and must exist")
        manifest = load_fixture("provenance/arena-web-server.json")
        validate_artifact_manifest(
            manifest,
            str(path),
            baseline=self.baseline,
            required_baseline_input_ids=(
                "ioq3",
                "native-builder-base",
                "server-runtime-base",
            ),
        )
        declared = {item["id"]: item for item in manifest["inputs"]}
        self.assertEqual(
            declared["server-runtime-base"]["identity"], self.image["immutableRef"]
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

    def test_build_scripts_derive_source_date_epoch_from_the_upstream_base(
        self,
    ) -> None:
        """The embedded PRODUCT_DATE follows the base, not the patch series.

        Both accepted builds assert the same value, and it is the upstream
        base's committer timestamp rather than the pin's: PRODUCT_DATE is
        ioquake3's product version string, and a fork patch does not make a new
        ioquake3 release. Deriving it from the base is also what keeps a
        renderer-only patch from moving the QVMs and the dedicated server, which
        do not compile a line of it.
        """
        if os.environ.get("ARENA_WITHOUT_GIT_METADATA") == "1":
            self.skipTest(
                "public source is mounted without linked-worktree Git metadata"
            )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT / self.baseline["engine"]["submodulePath"]),
                "show",
                "-s",
                "--format=%ct",
                self.baseline["engine"]["upstreamBase"]["commit"],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for name in ("build-browser.sh", "build-native.sh"):
            with self.subTest(script=name):
                script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                match = re.search(
                    r"(?m)^expected_source_date_epoch=([0-9]+)$",
                    script,
                )
                self.assertIsNotNone(match, f"{name} must pin SOURCE_DATE_EPOCH")
                self.assertEqual(match.group(1), result.stdout.strip())
                self.assertIn(
                    'show -s --format=%ct "${engine_base_commit}"',
                    script,
                    f"{name} must read the epoch from the upstream base commit",
                )

    def test_server_image_build_disables_the_layer_cache(self) -> None:
        # The layer cache was observed matching the LABEL step on its
        # unexpanded instruction text, producing an image whose id and labels
        # described the previous baseline — and the two-image reproducibility
        # check then compared that cache hit with itself. --no-cache is the
        # fix for a check that cannot report its own failure; this pins it.
        script = (ROOT / "scripts" / "build-server-image.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--no-cache", script)

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
            "formatVersion": 2,
            "archives": [
                {
                    "path": "baseq3/example.pk3",
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
            ],
            "package": {"id": "prototype-content", "name": "Prototype content"},
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
        candidate["archives"][0]["sources"][0]["sourceIdentity"] = "main"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "provenance")

    def test_schema_rejects_parent_source_path(self) -> None:
        schema = _load_json(ROOT / "schemas" / "content-provenance.schema.json")
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["sourcePath"] = "../../asset.map"
        with self.assertRaisesRegex(MetadataError, "schema pattern"):
            _validate_schema_instance(candidate, schema, schema, "provenance")

    def test_member_without_source_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["sourceId"] = "missing"
        with self.assertRaisesRegex(MetadataError, "declared source"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_non_free_member_license_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["licenseExpression"] = "CC-BY-NC-4.0"
        with self.assertRaisesRegex(MetadataError, "allowed product-input license"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_missing_required_obligation_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["obligations"].remove("attribution")
        with self.assertRaisesRegex(MetadataError, "must include"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_all_gnu_copyleft_families_require_source_obligation(self) -> None:
        for expression in ("AGPL-3.0-or-later", "LGPL-2.0-or-later"):
            with self.subTest(expression=expression):
                candidate = copy.deepcopy(self.provenance)
                candidate["archives"][0]["sources"][0]["licenseExpression"] = expression
                for member in candidate["archives"][0]["members"]:
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
        candidate["archives"][0]["sources"][0]["licenseExpression"] = expression
        for member in candidate["archives"][0]["members"]:
            member["licenseExpression"] = expression
        with self.assertRaisesRegex(MetadataError, "copyleft-source"):
            validate_content_provenance(
                candidate,
                "provenance",
                allowed_licenses=self.allowed | {expression},
            )

    def test_empty_notice_binding_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["noticePaths"] = []
        with self.assertRaisesRegex(MetadataError, "packaged notice member"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_dangling_notice_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][0]["noticePaths"] = ["notices/missing.txt"]
        with self.assertRaisesRegex(MetadataError, "member of the same archive"):
            validate_content_provenance(
                candidate, "provenance", allowed_licenses=self.allowed
            )

    def test_notice_path_must_name_notice_role(self) -> None:
        candidate = copy.deepcopy(self.provenance)
        candidate["archives"][0]["members"][1]["role"] = "metadata"
        candidate["archives"][0]["members"][1]["noticePaths"] = ["notices/map.txt"]
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
