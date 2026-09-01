# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit checks for the exact dedicated-server OCI runtime configuration."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

MODULE_SPEC = importlib.util.spec_from_file_location(
    "verify_server_image", ROOT / "scripts" / "verify-server-image.py"
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
VERIFY_SERVER_IMAGE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(VERIFY_SERVER_IMAGE)
ImageVerificationError = VERIFY_SERVER_IMAGE.ImageVerificationError
_verify_image_configuration = VERIFY_SERVER_IMAGE._verify_image_configuration


ENGINE = "a" * 40
BASELINE = "sha256:" + "b" * 64
PRODUCER = "c" * 40


def accepted_inspect() -> dict:
    return {
        "Architecture": "amd64",
        "Config": {
            "Entrypoint": ["/opt/arena-web/ioq3ded"],
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME=/var/lib/arena",
            ],
            "ExposedPorts": {"27960/udp": {}},
            "Labels": {
                "com.kaesual.arena-web.baseline-identity": BASELINE,
                "com.kaesual.arena-web.engine-commit": ENGINE,
                "com.kaesual.arena-web.producer-commit": PRODUCER,
                "org.opencontainers.image.title": "arena-web dedicated server",
            },
            "User": "65534:65534",
            "WorkingDir": "/opt/arena-web",
        },
        "Created": "1970-01-01T00:00:00Z",
        "ManifestType": "application/vnd.oci.image.manifest.v1+json",
        "Os": "linux",
    }


class ServerImageConfigurationTests(unittest.TestCase):
    def verify(self, inspected: dict) -> dict:
        return _verify_image_configuration(
            inspected,
            engine_commit=ENGINE,
            baseline_identity=BASELINE,
            producer_commit=PRODUCER,
        )

    def test_exact_configuration_is_accepted(self) -> None:
        result = self.verify(accepted_inspect())
        self.assertEqual(result["platform"], {"architecture": "amd64", "os": "linux"})

    def test_blanket_license_label_is_refused(self) -> None:
        inspected = accepted_inspect()
        inspected["Config"]["Labels"]["org.opencontainers.image.licenses"] = (
            "GPL-2.0-or-later"
        )
        with self.assertRaisesRegex(ImageVerificationError, "exact runtime contract"):
            self.verify(inspected)

    def test_every_runtime_field_is_part_of_the_exact_contract(self) -> None:
        for field in accepted_inspect()["Config"]:
            with self.subTest(field=field):
                inspected = copy.deepcopy(accepted_inspect())
                del inspected["Config"][field]
                with self.assertRaisesRegex(ImageVerificationError, "exact runtime contract"):
                    self.verify(inspected)

    def test_platform_epoch_and_manifest_type_are_exact(self) -> None:
        mutations = (
            ("Architecture", "arm64", "platform"),
            ("Os", "windows", "platform"),
            ("Created", "2026-09-01T00:00:00Z", "Unix epoch"),
            ("ManifestType", "application/vnd.docker.distribution.manifest.v2+json", "OCI"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field):
                inspected = accepted_inspect()
                inspected[field] = value
                with self.assertRaisesRegex(ImageVerificationError, message):
                    self.verify(inspected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
