#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recompute the WP6 network-sizing arithmetic from the committed records.

This is the tool the WP6 acceptance means by "a reviewer can recompute the
decision from committed reports and scripts". It reads
`records/wp2-routed-measurement.json` and `records/wp5-packet-census.json`,
re-validates the routed record against the committed measurement vector, and
prints every number `docs/wp6-network-sizing.md` states — the per-direction
observed maxima, the relay overhead applied to each, the two candidate budgets,
which packet classes fit at each, the code-level boundary cases the short census
session could not produce, and the cost of the candidate fragment sizes.

It reads no network and no clock, so two runs on the same bytes agree.

    scripts/derive-network-sizing.py            # the readable derivation
    scripts/derive-network-sizing.py --json     # the same numbers as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from metadata import MetadataError, _load_json, validate_measurement_vector  # noqa: E402
from network_sizing import (  # noqa: E402
    CONNECTIONLESS,
    DEFAULT_ALIGNMENT_BYTES,
    DEFAULT_RESERVE_BYTES,
    NetworkSizingError,
    derive,
)
from relay_probe import MeasurementPlan  # noqa: E402

ROUTED_RECORD = Path("records/wp2-routed-measurement.json")
CENSUS_RECORD = Path("records/wp5-packet-census.json")
MEASUREMENT_VECTOR = Path("locks/relay-measurement-vector.json")


def _rule(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def _terms(row: dict) -> str:
    parts = [f"{item['bytes']} ({item['label']})" for item in row["terms"]]
    return " + ".join(parts)


def render(result: dict) -> str:
    lines: list[str] = []
    path = result["path"]
    census = result["census"]
    budgets = result["budgets"]
    framing = path["framing"]

    lines.append("arena-web WP6 network sizing")
    lines.append("=" * 27)
    lines.append(
        f"engine {result['engine']['commit']} "
        f"(census driven at {result['engine']['censusCommit']})"
    )

    lines.append(_rule("The measured path"))
    lines.append(
        f"{path['sessions']} session(s); reported datagram maximum "
        f"{path['reportedDatagramMaximumBytes']} bytes"
        f"{'' if path['reportedMaximumConstantAcrossSessions'] else ' (NOT constant)'}"
    )
    lines.append(
        f"relay overhead per single-datagram frame: "
        f"{framing['relayHeaderBytes']} (header) + "
        f"{framing['datagramLengthPrefixBytes']} (length prefix) = "
        f"{framing['singleDatagramOverheadBytes']} bytes"
    )
    lines.append(
        f"record-backed contiguous inner floor: "
        f"{path['recordBackedInnerFloorBytes']} bytes "
        f"(largest echoed {path['largestEchoedInnerBytes']}, "
        f"smallest refused {path['smallestRefusedInnerBytes']})"
    )
    lines.append(
        f"derived inner budget: {path['reportedDatagramMaximumBytes']} - "
        f"{framing['singleDatagramOverheadBytes']} = "
        f"{path['derivedInnerBudgetBytes']} bytes"
    )
    if path["untestedInnerRange"]:
        low, high = path["untestedInnerRange"]
        lines.append(
            f"NOT EXERCISED: no single case was sent between {low} and {high} "
            "bytes, so the derived budget is arithmetic, not evidence"
        )

    lines.append(_rule("Observed traffic"))
    lines.append(f"{census['datagrams']} datagrams in the census")
    for direction, maximum in census["maximumByDirection"].items():
        lines.append(
            f"  {direction}: largest {maximum} bytes -> "
            f"{maximum + framing['singleDatagramOverheadBytes']} byte frame; "
            f"largest non-fragment "
            f"{census['largestUnfragmentedByDirection'].get(direction)} bytes"
        )
    for header, value in census["headerBytes"].items():
        lines.append(f"  netchan header {header}: {value} bytes")
    for message in census["fragmentedMessages"]:
        lines.append(
            f"  fragmented message: {message['messageBytes']} bytes in "
            f"{message['fragments']} fragments, largest datagram "
            f"{message['largestDatagramBytes']} bytes ({message['direction']})"
        )

    lines.append(_rule("Budgets"))
    for key, value in budgets.items():
        lines.append(f"  {key}: {value} inner bytes")

    lines.append(_rule("Boundary cases at the stock fragment size"))
    lines.append(
        "Sizes the code can emit in the fixed profile. Out-of-band classes are "
        "not fragmentable, so their size must fit as it stands."
    )
    for row in result["boundaryCases"]:
        verdicts = ", ".join(
            f"{key}={'fits' if fits else 'OVER'}" for key, fits in row["fits"].items()
        )
        marker = "" if row["onRelayPath"] else "  [off the relay path]"
        lines.append(
            f"  {row['name']} ({row['direction']}, {row['kind']}): "
            f"{row['innerBytes']} bytes -> "
            f"{row['innerBytes'] + framing['singleDatagramOverheadBytes']} byte "
            f"frame; {verdicts}{marker}"
        )
        lines.append(f"      {_terms(row)} = {row['innerBytes']}")
        lines.append(f"      {row['citation']}")
        if row["kind"] == CONNECTIONLESS:
            lines.append(
                f"      engine ceiling on this path: {row['codeCeilingBytes']} bytes "
                f"({row['ceilingCitation']})"
            )
        if row["offPathReason"]:
            lines.append(f"      off path: {row['offPathReason']}")

    lines.append(_rule("Strategy 1 - intact datagrams, no engine change"))
    intact = result["strategies"]["intactDatagrams"]
    lines.append(
        f"largest datagram an unchanged engine can emit: "
        f"{intact['requiredInnerBytes']} bytes -> "
        f"{intact['requiredFrameBytes']} byte frame"
    )
    lines.append(
        f"largest datagram the census actually observed: "
        f"{intact['observedRequiredInnerBytes']} bytes -> "
        f"{intact['observedRequiredFrameBytes']} byte frame"
    )
    lines.append(
        f"viable at the record-backed floor: {intact['viableAtRecordBackedFloor']}; "
        f"at the derived budget: {intact['viableAtDerivedBudget']}"
    )
    lines.append(
        "even the traffic actually observed does not fit: "
        f"record-backed floor {intact['observedTrafficViableAtRecordBackedFloor']}, "
        f"derived budget {intact['observedTrafficViableAtDerivedBudget']}"
    )
    for case in intact["refutedByMeasuredCases"]:
        lines.append(
            f"  measured case at {case['innerBytes']} inner bytes "
            f"({case['frameBytes']} byte frame): {case['outcome']}"
        )

    lines.append(_rule("Strategy 2 - symmetric fragment-size reduction"))
    for key, block in result["strategies"]["symmetricFragmentSizeReduction"].items():
        target = block["target"]
        lines.append(
            f"  {key}: budget {target['innerBudgetBytes']} bytes, "
            f"reserve {target['reserveBytes']}, "
            f"alignment {target['alignmentBytes']} -> "
            f"FRAGMENT_SIZE {target['candidateFragmentSize']}"
        )
        lines.append(
            f"      largest datagram {target['largestDatagramBytes']} bytes "
            f"({target['bindingDirection']} fragment) -> "
            f"{target['largestFrameBytes']} byte frame; "
            f"margin {target['marginBytes']} bytes"
        )
        lines.append(f"      every netchan case fits: {block['fitsEveryNetchanCase']}")
        if block["connectionlessCasesOverBudget"]:
            lines.append(
                "      out-of-band classes still over budget ON the relay path: "
                + ", ".join(block["connectionlessCasesOverBudget"])
            )
        else:
            lines.append(
                "      no out-of-band class on the relay path is over budget"
            )
        if block["connectionlessCasesOverBudgetOffRelayPath"]:
            lines.append(
                "      over budget but kept off the relay path by the profile: "
                + ", ".join(block["connectionlessCasesOverBudgetOffRelayPath"])
            )
        for cap in block["requiredProfileCaps"]:
            scope = "on the relay path" if cap["onRelayPath"] else "off the relay path"
            lines.append(
                f"      cap needed ({scope}): {cap['name']} fits only if "
                f"\"{cap['term']}\" is at most {cap['requiredCapBytes']} bytes "
                f"(fixed part {cap['fixedBytes']}); achievable: {cap['achievable']}"
            )
        for direction, fact in sorted(
            block["newlyFragmentingObservedTraffic"].items()
        ):
            lines.append(
                f"      {direction}: largest observed non-fragment message "
                f"{fact['largestNonFragmentMessageBytes']} bytes; begins to "
                f"fragment at this size: {fact['beginsToFragment']}"
            )
        for cost in block["messageCosts"]:
            stock = cost["atStockFragmentSize"]
            candidate = cost["atCandidateFragmentSize"]
            lines.append(
                f"      {stock['messageBytes']} byte message: "
                f"{stock['datagrams']} fragments at {stock['fragmentSize']} -> "
                f"{candidate['datagrams']} at {candidate['fragmentSize']}; "
                f"wire bytes {stock['totalFrameBytes']} -> "
                f"{candidate['totalFrameBytes']}"
            )
        worst = block["worstCaseMessageCost"]
        lines.append(
            f"      MAX_MSGLEN message ({worst['messageBytes']} bytes): "
            f"{worst['datagrams']} fragments, {worst['totalFrameBytes']} wire bytes"
        )

    lines.append(_rule("Strategy 3 - bounded engine-pair tunnel fragmentation"))
    tunnel = result["strategies"]["boundedTunnelFragmentation"]
    lines.append(
        f"required for netchan traffic: {tunnel['requiredForNetchanTraffic']}"
    )
    lines.append(
        f"required if the profile bounds are rejected: "
        f"{tunnel['requiredIfProfileBoundsAreRejected']}"
    )
    if tunnel["connectionlessCasesItWouldCover"]:
        lines.append(
            "  classes it would cover: "
            + ", ".join(tunnel["connectionlessCasesItWouldCover"])
        )
    lines.append(f"  {tunnel['note']}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--routed-record",
        type=Path,
        default=ROOT / ROUTED_RECORD,
        help="the WP2 routed measurement record",
    )
    parser.add_argument(
        "--census-record",
        type=Path,
        default=ROOT / CENSUS_RECORD,
        help="the WP5 packet census record",
    )
    parser.add_argument(
        "--measurement-vector",
        type=Path,
        default=ROOT / MEASUREMENT_VECTOR,
        help="the committed measurement vector the routed record is validated against",
    )
    parser.add_argument(
        "--reserve-bytes",
        type=int,
        default=DEFAULT_RESERVE_BYTES,
        help="bytes held back below the budget for the netchan header and headroom",
    )
    parser.add_argument(
        "--alignment-bytes",
        type=int,
        default=DEFAULT_ALIGNMENT_BYTES,
        help="round the candidate fragment size down to a multiple of this",
    )
    arguments = parser.parse_args()

    try:
        vector = _load_json(arguments.measurement_vector)
        validate_measurement_vector(vector, str(arguments.measurement_vector))
        plan = MeasurementPlan.from_vector(vector)
        result = derive(
            _load_json(arguments.routed_record),
            _load_json(arguments.census_record),
            plan=plan,
            reserve_bytes=arguments.reserve_bytes,
            alignment_bytes=arguments.alignment_bytes,
        )
    except (MetadataError, NetworkSizingError, ValueError) as error:
        print(f"network sizing failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
