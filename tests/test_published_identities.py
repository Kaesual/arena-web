# SPDX-License-Identifier: GPL-2.0-or-later
"""Every published restatement of a release identity, against the index.

Two consumer-facing documents restate what `release/browser-release.json`
generates: `docs/wp11-integration-handoff.md`, which is an authority, and
`docs/integration-contract.md`, which is not. Restating is right — a consumer
reads prose, not JSON — but a copy nothing compares is a copy that drifts, and
both had: the contract carried WP-C's base archive, manifests, image and
producer, and the handoff's own "indivisible compatibility identity" block
carried three values that two reissues had moved. Neither made a build red.

So the copies are compared here, and in both directions:

* every identity a current-contract block names must equal its authority;
* the *set* of rows in each block must be exactly the expected one, because a
  rule whose only failure mode is a wrong value can be satisfied by deleting
  the row; and
* in the contract, which carries no historical evidence, every `sha256:`/`git:`
  token anywhere in the document must be one this release actually contains —
  that is what catches a stale copy in prose rather than in a table. The
  handoff deliberately also records what earlier builds produced, so the same
  blanket rule there would forbid true statements; its structured blocks are
  gated instead.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs/integration-contract.md"
HANDOFF = ROOT / "docs/wp11-integration-handoff.md"
INDEX = ROOT / "release/browser-release.json"

TOKEN = re.compile(r"(?:sha256:[0-9a-f]{64}|git:[0-9a-f]{40})")
# A commit id written without the `git:` prefix, which the contract's prose
# does when it names a producer. The lookarounds keep it from matching inside a
# `git:` token or inside a 64-character digest, so it finds only bare ids.
BARE_COMMIT = re.compile(r"(?<![0-9a-f:])[0-9a-f]{40}(?![0-9a-f])")
ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", re.M)

# Label -> the key of the expected identity. The mapping is the check.
CONTRACT_ROWS = {
    "Baseline lock": "baselineIdentity",
    "ioq3 engine": "engineCommit",
    "Browser loader producer": "browserProducer",
    "Browser artifact manifest": "browserManifestIdentity",
    "Content artifact manifest": "contentManifestIdentity",
    "Content base archive": "contentPayloadIdentity",
    "Server artifact manifest": "serverManifestIdentity",
    "Server image producer/build checkout": "serverProducer",
    "Accepted native server image ID": "serverImageId",
}
HANDOFF_ROWS = {
    "Baseline lock": "baselineIdentity",
    "Browser artifact manifest": "browserManifestIdentity",
    "Content artifact manifest": "contentManifestIdentity",
    "Base content archive": "contentPayloadIdentity",
    "OCI configuration/image ID": "serverImageId",
    "Server artifact manifest": "serverManifestIdentity",
    "Server profile": "serverProfileIdentity",
}
# The labels of the handoff's "indivisible compatibility identity" block, which
# is the tuple a consumer pins against.
HANDOFF_TUPLE = {
    "baseline": "baselineIdentity",
    "ioq3": "engineCommit",
    "browser manifest": "browserManifestIdentity",
    "content manifest": "contentManifestIdentity",
    "content base": "contentPayloadIdentity",
    "server manifest": "serverManifestIdentity",
    "server image ID": "serverImageId",
}


def _index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _producer(relative: str) -> str:
    record = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    return f"git:{record['producer']['commit']}"


def _expected() -> dict[str, str]:
    index = _index()
    compatibility = index["compatibility"]
    values = {
        name: compatibility[name]
        for name in (
            "baselineIdentity",
            "browserManifestIdentity",
            "contentManifestIdentity",
            "contentPayloadIdentity",
            "serverImageId",
            "serverManifestIdentity",
        )
    }
    values["engineCommit"] = f"git:{compatibility['engineCommit']}"
    values["browserProducer"] = _producer("manifests/browser-client.json")
    values["serverProducer"] = _producer("provenance/arena-web-server.json")
    values["serverProfileIdentity"] = (
        "sha256:" + index["authorities"]["serverProfile"]["sha256"]
    )
    return values


class PublishedIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = CONTRACT.read_text(encoding="utf-8")
        self.handoff = HANDOFF.read_text(encoding="utf-8")
        self.expected = _expected()

    def _rows(self, text: str, labels: dict[str, str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for label, value in ROW.findall(text):
            if label not in labels:
                continue
            tokens = TOKEN.findall(value)
            self.assertEqual(
                len(tokens),
                1,
                f"row {label!r} must carry exactly one identity, found {tokens}",
            )
            self.assertNotIn(label, found, f"row {label!r} appears twice")
            found[label] = tokens[0]
        return found

    def _assert_rows(self, text: str, labels: dict[str, str], what: str) -> None:
        rows = self._rows(text, labels)
        # A missing row is a failure, not a relief.
        self.assertEqual(set(rows), set(labels), f"{what}: unexpected row set")
        for label, value in sorted(rows.items()):
            with self.subTest(document=what, row=label):
                self.assertEqual(value, self.expected[labels[label]])

    def test_the_contract_table_matches_its_authorities(self) -> None:
        self._assert_rows(self.contract, CONTRACT_ROWS, "integration contract")

    def test_the_handoff_tables_match_their_authorities(self) -> None:
        self._assert_rows(self.handoff, HANDOFF_ROWS, "integration handoff")

    def test_the_handoff_tuple_block_matches_its_authorities(self) -> None:
        """The block a consumer pins against, which is why it is a block."""
        marker = "## 5. Indivisible compatibility identity"
        self.assertIn(marker, self.handoff)
        block = (
            self.handoff.split(marker, 1)[1].split("```text", 1)[1].split("```", 1)[0]
        )
        found: dict[str, str] = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            tokens = TOKEN.findall(line)
            self.assertEqual(len(tokens), 1, f"line {line!r} names {tokens}")
            label = line[: line.index(tokens[0])].strip()
            self.assertNotIn(label, found, f"line {label!r} appears twice")
            found[label] = tokens[0]
        self.assertEqual(set(found), set(HANDOFF_TUPLE))
        for label, value in sorted(found.items()):
            with self.subTest(line=label):
                self.assertEqual(value, self.expected[HANDOFF_TUPLE[label]])

    def test_the_base_archive_size_is_the_published_one(self) -> None:
        artifacts = json.loads(
            (ROOT / "provenance/arena-web-ffa-content-manifest.json").read_text(
                encoding="utf-8"
            )
        )["artifacts"]
        base = next(
            item
            for item in artifacts
            if f"sha256:{item['sha256']}" == self.expected["contentPayloadIdentity"]
        )
        self.assertIn(f"{base['size']:,} bytes", self.handoff)

    def test_the_handoff_resource_table_matches_the_record(self) -> None:
        """The other hand-copied generated data in the same document.

        Its three measured rows and the three figures around them are a copy of
        `records/wp11-server-resources.json`, and they had drifted the same way
        the identities had: the read-only image size was still the one-map
        image, two reissues out of date, which is the kind of number a consumer
        sizes a host with.
        """
        record = json.loads(
            (ROOT / "records/wp11-server-resources.json").read_text(encoding="utf-8")
        )
        measurement = record["measurement"]
        maxima = measurement["maximums"]
        headroom = record["headroom"]
        limits = record["candidateAndAcceptedLimits"]
        expected = [
            f"| CPU | {limits['cpuCores']} core | "
            f"{maxima['peakSampleCpuCores']}-core peak sample | "
            f"{headroom['cpuCores']} core; {headroom['cpuFactor']}x |",
            f"| Memory | {limits['memoryBytes']:,} bytes | "
            f"{maxima['peakCgroupMemoryBytes']:,}-byte peak cgroup; "
            f"{measurement['busy']['peakProcessHwmBytes']:,}-byte process HWM | "
            f"{headroom['memoryBytes']:,} bytes; "
            f"{headroom['memoryFactor']}x against cgroup peak |",
            f"| Writable home | {limits['writableHomeBytes']:,} bytes | "
            f"{maxima['peakWritableHomeBytes']:,} bytes | "
            f"{headroom['writableHomeBytes']:,} bytes; "
            f"{headroom['writableHomeFactor']:,}x |",
            f"Startup readiness was {measurement['startupReadySeconds']} seconds",
            f"averaged {measurement['idle']['meanCpuCores']} cores",
            f"active, averaged {measurement['busy']['meanCpuCores']} cores",
            f"{measurement['readOnlyImageBytes']:,} bytes; the measured container "
            "writable layer after stop",
            f"was {measurement['containerWritableLayerBytesAfterStop']:,} bytes and is "
            "disposable",
            "The measured graceful exit took "
            f"{record['lifecycle']['gracefulStopSeconds']} seconds.",
        ]
        for line in expected:
            with self.subTest(line=line[:48]):
                self.assertIn(line, self.handoff)

    def test_no_identity_in_the_contract_is_stale(self) -> None:
        """The contract carries only the current release, so every token in it
        must belong to that release — including the ones in prose, which is
        where two of the stale copies were."""
        index = _index()
        allowed = set(self.expected.values())
        allowed.update(
            f"sha256:{entry['sha256']}" for entry in index["authorities"].values()
        )
        allowed.update(f"sha256:{entry['sha256']}" for entry in index["servedFiles"])
        unknown = sorted(set(TOKEN.findall(self.contract)) - allowed)
        self.assertEqual(
            unknown, [], "identities that belong to no part of this release"
        )

        # And the same rule for a commit written without its prefix, which is
        # how the contract's prose names the producer `reproduce-release.sh`
        # reads out of the records. That spelling escaped the check above for
        # two releases and drifted a batch behind the table three rows earlier:
        # a value restated in prose and gated nowhere is the failure this whole
        # test file exists for.
        commits = {
            self.expected[name].removeprefix("git:")
            for name in ("engineCommit", "browserProducer", "serverProducer")
        }
        commits.add(
            json.loads(
                (ROOT / "provenance/arena-web-ffa-content-manifest.json").read_text(
                    encoding="utf-8"
                )
            )["producer"]["commit"]
        )
        found = set(BARE_COMMIT.findall(self.contract))
        stale = sorted(found - commits)
        self.assertEqual(
            stale, [], "commit ids in prose that no record of this release names"
        )
        # And the rule needs a subject, or deleting the sentence satisfies it.
        # The contract tells a reader to reproduce the release from the content
        # pack's producer commit, so that id must actually be in the document.
        self.assertIn(
            json.loads(
                (ROOT / "provenance/arena-web-ffa-content-manifest.json").read_text(
                    encoding="utf-8"
                )
            )["producer"]["commit"],
            found,
            "the contract no longer names the producer commit to reproduce from",
        )

    def test_the_staged_file_list_is_the_served_set(self) -> None:
        marker = "Derive the set from the release index rather than asserting a number:"
        self.assertIn(marker, self.contract)
        block = (
            self.contract.split(marker, 1)[1].split("```text", 1)[1].split("```", 1)[0]
        )
        listed = [line.strip() for line in block.splitlines() if line.strip()]
        served = [entry["path"] for entry in _index()["servedFiles"]]
        self.assertEqual(sorted(listed), sorted(served))
        self.assertEqual(len(listed), len(set(listed)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class RotationCeilingRestatementTest(unittest.TestCase):
    """The published rotation figures, recomputed and matched to the documents.

    Same reason as the identities above, one class of number further out.
    `docs/wp3-content-closure.md` and `docs/wp11-integration-handoff.md` both
    tell an operator how long a rotation may be and how close the byte bound
    is, and until this test those figures were hand-copied: the ceiling had
    been carried across two releases as "15" with the *reason* for it wrong,
    and a byte figure was published that had been measured by joining the
    arguments with spaces instead of through `engine_command_line`.

    So the numbers are computed here from the published map set and the
    committed server arguments, and the documents must contain what comes out.
    A batch that publishes a map and does not re-derive them turns red.
    """

    def test_the_documents_carry_the_figures_this_release_produces(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        from arena_runtime import engine_command_line, rotation_arguments
        from arena_server import (
            CONTENT_MANIFEST,
            load_profile,
            max_server_rotation,
            published_maps,
        )

        profile = load_profile(ROOT)
        fixed = list(profile["serverArguments"])
        names = published_maps(ROOT, CONTENT_MANIFEST)
        ceiling = max_server_rotation(ROOT, profile)

        def size(rotation: list[str]) -> int:
            return len(engine_command_line(rotation_arguments(rotation) + fixed))

        closure = (ROOT / "docs/wp3-content-closure.md").read_text(encoding="utf-8")
        handoff = HANDOFF.read_text(encoding="utf-8")
        sentence = f"{ceiling} of the {len(names)} published maps"
        for name, text in (("closure", closure), ("handoff", handoff)):
            with self.subTest(document=name):
                self.assertIn(sentence, text)

        # The three rotations the closure document tabulates: what
        # `max_rotation_length` answers for, the worst distinct one, and the
        # repeating one it deliberately does not cover.
        longest = sorted(names, key=lambda name: (-len(name), name))[:ceiling]
        repeated = [max(names, key=len)] * ceiling
        for label, rotation in (
            ("alphabetically first", names[:ceiling]),
            ("longest", longest),
            ("repeated", repeated),
        ):
            with self.subTest(rotation=label):
                self.assertIn(str(size(rotation)), closure)
        for label, rotation in (
            ("alphabetically first", names[:ceiling]),
            ("longest", longest),
        ):
            with self.subTest(rotation=label, document="handoff"):
                self.assertIn(str(size(rotation)), handoff)

        # And the headroom, in the unit the documents state it in rather than
        # as a constant restated here: the budget check refuses *at* maxBytes
        # and a rotated name costs one byte per character, so this is how many
        # characters the worst distinct rotation has left. Both documents spell
        # the figure out in words, so the check is against the sentence a
        # reader acts on.
        limit = profile["_commandLineLimits"]["maxBytes"]
        room = limit - size(longest) - 1
        words = {
            0: "no",
            1: "one",
            2: "two",
            3: "three",
            4: "four",
            5: "five",
            6: "six",
        }
        self.assertIn(room, words, "the headroom left the range the documents word")
        phrase = f"room for **{words[room]}** further characters"
        for name, text in (("closure", closure), ("handoff", handoff)):
            with self.subTest(document=name, claim="headroom"):
                # The documents are hard-wrapped, so compare on the sentence
                # rather than on the line breaks.
                self.assertIn(phrase, " ".join(text.split()))
