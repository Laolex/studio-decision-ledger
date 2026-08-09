"""Tests for snapshot construction and the decision-record write path.

A decision record is the product's only durable output. If the manifest hash
is unstable, every replay fails; if it is insensitive to a change in the
evidence, replay certifies decisions it should refuse.
"""

from datetime import datetime, timezone

import pytest

from sdl.record import (
    build_snapshot,
    decision_insert_sql,
    snapshot_insert_sql,
)
from sdl.resolve import QueryEvidence

CAPTURED_AT = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)


def evidence(result_hash: str = "a" * 64, table: str = "title_licenses") -> QueryEvidence:
    return QueryEvidence(
        table_name=table,
        canonical_query=f"SELECT *\nFROM sdl.{table}",
        result_hash=result_hash,
        row_count=1,
        max_revision=1,
    )


def test_manifest_hash_is_stable_for_identical_evidence():
    first = build_snapshot([evidence()], "RS-1", CAPTURED_AT, max_revision=1)
    second = build_snapshot([evidence()], "RS-1", CAPTURED_AT, max_revision=1)

    assert first.source_manifest_hash == second.source_manifest_hash
    assert len(first.source_manifest_hash) == 64


def test_manifest_hash_changes_when_a_result_hash_changes():
    original = build_snapshot([evidence()], "RS-1", CAPTURED_AT, max_revision=1)
    altered = build_snapshot([evidence(result_hash="b" * 64)], "RS-1", CAPTURED_AT, max_revision=1)

    assert original.source_manifest_hash != altered.source_manifest_hash


def test_manifest_hash_is_independent_of_evidence_ordering():
    """Retrieval order is an implementation detail; the pinned evidence is not."""
    a, b = evidence(table="title_licenses"), evidence(table="ratings")

    forward = build_snapshot([a, b], "RS-1", CAPTURED_AT, max_revision=1)
    reverse = build_snapshot([b, a], "RS-1", CAPTURED_AT, max_revision=1)

    assert forward.source_manifest_hash == reverse.source_manifest_hash


def test_snapshot_carries_every_retrieval_in_its_manifest():
    snapshot = build_snapshot(
        [evidence(table="title_licenses"), evidence(table="clearances")],
        "RS-1",
        CAPTURED_AT,
        max_revision=1,
    )

    assert {fact["table_name"] for fact in snapshot.facts} == {"title_licenses", "clearances"}
    assert all("canonical_query" in fact and "result_hash" in fact for fact in snapshot.facts)


def test_insert_sql_escapes_quotes_in_evidence():
    """Rationale text is model output and reaches SQL; a stray quote must not
    terminate the statement."""
    sql = decision_insert_sql(
        decision_id="D-1",
        title_id="T'X",
        territory_code="NG",
        effective_at=CAPTURED_AT,
        policy_revision="POL-1",
        policy_sha256="c" * 64,
        snapshot_id="RS-1",
        outcome="HOLD",
        rule_hits=["LIC-002"],
        query_evidence_json='{"a":1}',
        model_rationale="it's blocked",
        model_config="",
        prompt_template_revision="",
        decided_at=CAPTURED_AT,
    )

    assert "T\\'X" in sql
    assert "it\\'s blocked" in sql


def test_snapshot_insert_sql_targets_the_snapshot_table():
    snapshot = build_snapshot([evidence()], "RS-1", CAPTURED_AT, max_revision=1)

    sql = snapshot_insert_sql(snapshot)

    assert sql.startswith("INSERT INTO sdl.decision_snapshots")
    assert snapshot.source_manifest_hash in sql
