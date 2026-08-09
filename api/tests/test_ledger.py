"""Tests for ledger persistence: writing decisions and reading them back.

Every test writes with a unique decision id so runs do not collide, and reads
back through the same path the console will use. A decision that cannot be
read back exactly as written is not a ledger.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from sdl.ledger import (
    current_max_revision,
    read_decision,
    read_policy,
    read_snapshot,
    write_decision,
    write_snapshot,
)
from sdl.record import DecisionRecord, build_snapshot
from sdl.resolve import resolve_facts

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)
DECIDED_AT = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)


@pytest.fixture
def unique_id() -> str:
    return f"TEST-{uuid4().hex[:12]}"


def test_current_max_revision_reflects_the_seeded_corrections(writer, http_executor):
    assert current_max_revision(http_executor, TITLE) == 3


def test_policy_round_trips_with_its_recorded_hash(http_executor):
    policy, sha256 = read_policy(http_executor, "POL-2026.07")

    assert policy["policy_revision"] == "POL-2026.07"
    assert policy["release_path"] == "SVOD"
    assert len(sha256) == 64


def test_snapshot_reads_back_identically(writer, http_executor, unique_id):
    _facts, evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)
    snapshot = build_snapshot(evidence, unique_id, DECIDED_AT, max_revision=1)

    write_snapshot(writer, snapshot)
    loaded = read_snapshot(http_executor, unique_id)

    assert loaded is not None
    assert loaded.source_manifest_hash == snapshot.source_manifest_hash
    assert loaded.max_revision == 1
    assert loaded.facts == snapshot.facts


def test_decision_reads_back_identically(writer, http_executor, unique_id):
    record = DecisionRecord(
        decision_id=unique_id,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        policy_revision="POL-2026.07",
        policy_sha256="d" * 64,
        snapshot_id="RS-X",
        outcome="HOLD",
        rule_hits=["LIC-002", "CLR-001"],
        decided_at=DECIDED_AT,
        model_rationale="Blocked: the grant does not cover this release path.",
    )

    write_decision(writer, record)
    loaded = read_decision(http_executor, unique_id)

    assert loaded == record


def test_reading_an_unknown_decision_returns_none(http_executor):
    assert read_decision(http_executor, "D-DOES-NOT-EXIST") is None


def test_reading_an_unknown_snapshot_returns_none(http_executor):
    assert read_snapshot(http_executor, "RS-DOES-NOT-EXIST") is None
