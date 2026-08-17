#!/usr/bin/env python3
"""Negative control for the replay verifier.

Runs one controlled matrix through the production `verifier.verify()` — not a
reimplementation of it, and not the `/ablate` endpoint, which exercises a single
arm. Four arms, one shared fixture, one verifier.

    python3 scripts/negative_control.py

Offline and credential-free on purpose. It needs no ClickHouse, no Google Cloud
and no network, so a judge or a CI job can run it from a clean clone. The
fixture is built with the same hashing functions the production write path uses,
so it cannot drift out of agreement with them.

Every mutation is an in-memory copy. `DecisionRecord` is a frozen dataclass and
`dataclasses.replace` returns a new one; the snapshot's fact list is deep-copied
before a hash is altered. The canonical serialization of the original record is
captured before the first arm and compared after the last, so "the record was
not touched" is a measured result rather than a claim.

The uncomfortable result is the point of shipping this:

    Removing Gemini's prose changes nothing about verdict reproducibility.
    Gemini operates the workflow; deterministic evidence and policy determine
    the gate.

A note on the first two rows. An intact record certifies `C3_BOUNDARY` rather
than `C2` because it carries model text, and the verifier says so explicitly:
that is C2 plus an honest statement of where the evidence stops, not a
downgrade. Strip the prose and the same record certifies `C2`. Both arms
reproduce the recorded outcome — the verifier refuses to certify at all
otherwise — which is precisely the claim being made. Reporting the intact arm
as `C2` would have required a fixture with no rationale, which is not what the
production path records.
"""

from __future__ import annotations

import copy
import hashlib
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))

from sdl.canonical import canonical_rows  # noqa: E402
from sdl.record import (  # noqa: E402
    DecisionRecord,
    build_snapshot,
    canonical_json,
)
from sdl.resolve import (  # noqa: E402
    EVIDENCE_TABLES,
    QueryEvidence,
    canonical_result_hash,
)
from sdl.retrieval import point_in_time_query  # noqa: E402
from sdl.verifier import verify  # noqa: E402

TITLE = "CONTROL-S01E01"
TERRITORY = "GB"
MAX_REVISION = 2
EFFECTIVE_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)
DECIDED_AT = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

FROM = "2026-01-01 00:00:00.000"
TO = "2027-12-31 00:00:00.000"

POLICY = {
    "policy_revision": "POL-CONTROL",
    "release_path": "SVOD",
    "rules": [
        {"id": "CLR-001", "mandatory_kinds": ["MUSIC_MASTER"]},
    ],
}

# Rows the fixture's pinned queries return. Chosen to clear every rule, so the
# recorded outcome is AVAILABLE and any refusal in the matrix is attributable
# to the binding the arm broke rather than to the evidence.
ROWS: dict[str, list[dict]] = {
    "title_licenses": [
        {
            "license_id": "LIC-CTRL-1",
            "territory_code": TERRITORY,
            "rights_scope": "SVOD",
            "valid_from": FROM,
            "valid_to": TO,
            "status": "ACTIVE",
        }
    ],
    "clearances": [
        {
            "clearance_id": "CLR-CTRL-1",
            "asset_ref": "Control cue",
            "clearance_kind": "MUSIC_MASTER",
            "territory_code": TERRITORY,
            "valid_from": FROM,
            "valid_to": TO,
            "status": "ACTIVE",
        }
    ],
    "ratings": [
        {
            "rating_id": "RTG-CTRL-1",
            "territory_code": TERRITORY,
            "rating_code": "15",
            "issued_at": FROM,
            "expires_at": TO,
            "status": "VALID",
        }
    ],
    "deliveries": [
        {
            "delivery_id": "DLV-CTRL-1",
            "master_version": "v1.0",
            "approved_at": FROM,
            "captions_state": "APPROVED",
            "audio_description_state": "APPROVED",
        }
    ],
    "continuity_exceptions": [],
    "synthetic_content": [
        {
            "record_id": "SYN-CTRL-1",
            "asset_ref": "Episode 1 — full programme",
            "generation_kind": "NONE",
            "tool_ref": "",
            "disclosure_obligation_ref": "",
        }
    ],
    "performer_consents": [],
}


def build_executor():
    """A credential-free executor answering only the fixture's own queries.

    Raising on an unknown query matters: it means an arm that reached for
    evidence the fixture never pinned fails loudly instead of silently
    verifying against an empty result.
    """
    answers = {
        point_in_time_query(table, TITLE, TERRITORY, MAX_REVISION): canonical_rows(
            ROWS[table]
        )
        for table in EVIDENCE_TABLES
    }

    def execute(sql: str) -> list[dict]:
        if sql not in answers:
            raise KeyError(f"the fixture pins no answer for: {sql!r}")
        return answers[sql]

    return execute


def build_fixture():
    """A record, snapshot and policy that agree with each other by construction."""
    evidence = [
        QueryEvidence(
            table_name=table,
            canonical_query=point_in_time_query(
                table, TITLE, TERRITORY, MAX_REVISION
            ),
            result_hash=canonical_result_hash(canonical_rows(ROWS[table])),
            row_count=len(ROWS[table]),
            max_revision=MAX_REVISION,
        )
        for table in EVIDENCE_TABLES
    ]
    snapshot = build_snapshot(
        evidence, "RS-CONTROL-0001", DECIDED_AT, max_revision=MAX_REVISION
    )
    record = DecisionRecord(
        decision_id="D-CONTROL",
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        policy_revision=POLICY["policy_revision"],
        policy_sha256=hashlib.sha256(canonical_json(POLICY).encode("utf-8")).hexdigest(),
        snapshot_id=snapshot.snapshot_id,
        outcome="AVAILABLE",
        rule_hits=[],
        decided_at=DECIDED_AT,
        model_rationale=(
            "Every mandatory condition is met for this territory and date."
        ),
        model_config='{"provider":"vertex-ai","model":"gemini-2.5-flash"}',
        prompt_template_revision="rationale-control",
    )
    return record, snapshot


def snapshot_with_mutated_hash(snapshot):
    """A copy whose first fact claims a different result hash."""
    facts = copy.deepcopy(snapshot.facts)
    facts[0]["result_hash"] = "0" * 64
    return replace(snapshot, facts=facts)


def main() -> int:
    record, snapshot = build_fixture()
    executor = build_executor()

    # Captured before any arm runs. Compared after the last one.
    before = canonical_json(
        {
            "record": str(record),
            "snapshot_facts": snapshot.facts,
            "manifest": snapshot.source_manifest_hash,
        }
    )

    arms = [
        (
            "Intact record",
            lambda: verify(record, snapshot, POLICY, executor),
            "C3_BOUNDARY",
        ),
        (
            "Model rationale removed",
            lambda: verify(replace(record, model_rationale=""), snapshot, POLICY, executor),
            "C2",
        ),
        (
            "Snapshot binding removed",
            lambda: verify(record, None, POLICY, executor),
            "NOT_CERTIFIED",
        ),
        (
            "Result hash mutated",
            lambda: verify(record, snapshot_with_mutated_hash(snapshot), POLICY, executor),
            "NOT_CERTIFIED",
        ),
    ]

    rows: list[tuple[str, str, str, bool]] = []
    for name, run, expected in arms:
        actual = run().capability_class
        rows.append((name, expected, actual, actual == expected))

    after = canonical_json(
        {
            "record": str(record),
            "snapshot_facts": snapshot.facts,
            "manifest": snapshot.source_manifest_hash,
        }
    )
    unchanged = before == after
    rows.append(
        ("Original record unchanged", "PASS", "PASS" if unchanged else "MUTATED", unchanged)
    )

    width = max(len(name) for name, *_ in rows)
    print("\nNegative control — production verifier, offline fixture\n")
    for name, expected, actual, ok in rows:
        mark = "ok  " if ok else "FAIL"
        arrow = f"{actual}" if actual == expected else f"{actual} (expected {expected})"
        print(f"  {mark}  {name.ljust(width)}  →  {arrow}")

    failures = [name for name, _e, _a, ok in rows if not ok]
    if failures:
        print(f"\n{len(failures)} expectation(s) failed: {', '.join(failures)}\n")
        return 1

    print(
        "\nRemoving Gemini's prose changes nothing about verdict reproducibility:\n"
        "both certified arms reproduced the recorded outcome from pinned evidence,\n"
        "and the verifier refuses to certify at all when it cannot. Gemini operates\n"
        "the workflow; deterministic evidence and policy determine the gate.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
