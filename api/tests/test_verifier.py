"""Tests for the replay verifier.

The verifier's value is entirely in what it refuses. A verifier that certifies
whatever it is handed is worse than no verifier, because it launders an
unreproducible decision into an apparently audited one. Most of these tests are
therefore about refusal.

Records here are built through the real pipeline against real ClickHouse rather
than hand-assembled, so a certified replay means the whole path agrees with
itself.
"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sdl.evaluator import evaluate, ReleaseRequest
from sdl.record import DecisionRecord, build_snapshot, canonical_json
from sdl.resolve import resolve_facts
from sdl.verifier import verify

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)
DECIDED_AT = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)

POLICY = {
    "policy_revision": "POL-2026.07",
    "release_path": "SVOD",
    "rules": [
        {"id": "LIC-001", "outcome_when_unmet": "HOLD"},
        {"id": "LIC-002", "outcome_when_unmet": "HOLD"},
        {
            "id": "CLR-001",
            "outcome_when_unmet": "HOLD",
            "mandatory_kinds": ["MUSIC_SYNC", "MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT"],
        },
        {"id": "RTG-001", "outcome_when_unmet": "HOLD"},
        {"id": "DLV-001", "outcome_when_unmet": "HOLD"},
        {"id": "CNT-001", "outcome_when_unmet": "HOLD"},
        {"id": "ESC-001", "outcome_when_unmet": "ESCALATE"},
    ],
}


def policy_hash() -> str:
    import hashlib

    return hashlib.sha256(canonical_json(POLICY).encode("utf-8")).hexdigest()


@pytest.fixture
def recorded_decision(http_executor):
    """A genuine decision produced by the real pipeline at revision 1."""
    facts, evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)
    decision = evaluate(
        ReleaseRequest(title_id=TITLE, territory_code=TERRITORY, effective_at=EFFECTIVE_AT),
        facts,
        POLICY,
    )
    snapshot = build_snapshot(evidence, "RS-2026-07-30-0001", DECIDED_AT, max_revision=1)
    record = DecisionRecord(
        decision_id="D-1846",
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        policy_revision=POLICY["policy_revision"],
        policy_sha256=policy_hash(),
        snapshot_id=snapshot.snapshot_id,
        outcome=decision.outcome,
        rule_hits=decision.rule_hits,
        model_rationale="",
        decided_at=DECIDED_AT,
    )
    return record, snapshot


def test_intact_record_certifies_c2(recorded_decision, http_executor):
    record, snapshot = recorded_decision

    result = verify(record, snapshot, POLICY, http_executor)

    assert result.capability_class == "C2"
    assert result.failed_requirement == ""
    assert record.outcome == "AVAILABLE"


def test_record_with_model_rationale_reports_the_c3_boundary(recorded_decision, http_executor):
    """Reproducing the evidence-to-outcome path says nothing about whether the
    model's stated reasoning is what actually drove its text."""
    record, snapshot = recorded_decision
    record = replace(record, model_rationale="Cleared: licence and clearances active.")

    result = verify(record, snapshot, POLICY, http_executor)

    assert result.capability_class == "C3_BOUNDARY"
    assert result.failed_requirement == ""
    assert "rationale" in result.detail.lower()


def test_missing_snapshot_refuses_certification(recorded_decision, http_executor):
    """The ablation beat: strip the binding and the record stops being evidence."""
    record, _snapshot = recorded_decision

    result = verify(record, None, POLICY, http_executor)

    assert result.capability_class == "NOT_CERTIFIED"
    assert "snapshot" in result.failed_requirement.lower()


def test_missing_policy_refuses_certification(recorded_decision, http_executor):
    record, snapshot = recorded_decision

    result = verify(record, snapshot, None, http_executor)

    assert result.capability_class == "NOT_CERTIFIED"
    assert "policy" in result.failed_requirement.lower()


def test_policy_hash_mismatch_refuses_certification(recorded_decision, http_executor):
    """The policy text was edited after the fact; the binding no longer holds."""
    record, snapshot = recorded_decision
    tampered = {**POLICY, "release_path": "AVOD"}

    result = verify(record, snapshot, tampered, http_executor)

    assert result.capability_class == "NOT_CERTIFIED"
    assert "policy" in result.failed_requirement.lower()


def test_result_hash_mismatch_refuses_certification(recorded_decision, http_executor):
    record, snapshot = recorded_decision
    corrupted_facts = [dict(fact) for fact in snapshot.facts]
    corrupted_facts[0]["result_hash"] = "0" * 64
    corrupted = replace(snapshot, facts=corrupted_facts)

    result = verify(record, corrupted, POLICY, http_executor)

    assert result.capability_class == "NOT_CERTIFIED"
    assert "hash" in result.failed_requirement.lower()


def test_outcome_mismatch_refuses_certification(recorded_decision, http_executor):
    """A record claiming an outcome the evidence does not produce is exactly
    what the verifier exists to catch."""
    record, snapshot = recorded_decision
    record = replace(record, outcome="HOLD", rule_hits=["LIC-002"])

    result = verify(record, snapshot, POLICY, http_executor)

    assert result.capability_class == "NOT_CERTIFIED"
    assert "outcome" in result.failed_requirement.lower()


def test_verifier_never_reports_a_percentage(recorded_decision, http_executor):
    record, snapshot = recorded_decision

    result = verify(record, snapshot, POLICY, http_executor)

    assert result.capability_class in {"C2", "C3_BOUNDARY", "NOT_CERTIFIED"}
    assert "%" not in result.detail
