"""Ledger persistence: write decisions, read them back.

Reads use the same `Executor` contract as evidence retrieval, so the console
can serve records through the MCP path. Writes take a separate `Writer` that
never touches MCP — SPEC invariant 13. The asymmetry is the point.

Nothing here updates or deletes. A correction is a new record naming the one
it supersedes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from sdl.record import (
    DecisionRecord,
    EvidenceSnapshot,
    canonical_json,
    decision_insert_sql,
    snapshot_insert_sql,
)
from sdl.resolve import EVIDENCE_TABLES, Executor

Writer = Callable[[str], None]

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _parse(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def current_max_revision(executor: Executor, title_id: str) -> int:
    """Highest revision known across every evidence table for a title.

    This is what "as of now" means. Pinning a new decision to it is what makes
    the decision reproducible later.
    """
    unions = "\nUNION ALL\n".join(
        f"SELECT max(revision) AS r FROM sdl.{table} WHERE title_id = {_quote(title_id)}"
        for table in EVIDENCE_TABLES
    )
    rows = executor(f"SELECT max(r) AS max_revision FROM (\n{unions}\n)")
    value = rows[0]["max_revision"] if rows else 0
    return int(value or 0)


def read_policy(executor: Executor, policy_revision: str) -> tuple[dict, str]:
    rows = executor(
        "SELECT rules_payload, payload_sha256 FROM sdl.policy_revisions "
        f"WHERE policy_revision = {_quote(policy_revision)} LIMIT 1"
    )
    if not rows:
        raise KeyError(f"unknown policy revision {policy_revision!r}")
    return json.loads(rows[0]["rules_payload"]), rows[0]["payload_sha256"]


def write_snapshot(writer: Writer, snapshot: EvidenceSnapshot) -> None:
    writer(snapshot_insert_sql(snapshot))


def read_snapshot(executor: Executor, snapshot_id: str) -> EvidenceSnapshot | None:
    rows = executor(
        "SELECT snapshot_id, captured_at, max_revision, source_manifest_hash, facts_json "
        f"FROM sdl.decision_snapshots WHERE snapshot_id = {_quote(snapshot_id)} LIMIT 1"
    )
    if not rows:
        return None
    row = rows[0]
    return EvidenceSnapshot(
        snapshot_id=row["snapshot_id"],
        captured_at=_parse(row["captured_at"]),
        max_revision=int(row["max_revision"]),
        source_manifest_hash=row["source_manifest_hash"],
        facts=json.loads(row["facts_json"]),
    )


def write_decision(writer: Writer, record: DecisionRecord, query_evidence: list | None = None) -> None:
    writer(
        decision_insert_sql(
            decision_id=record.decision_id,
            title_id=record.title_id,
            territory_code=record.territory_code,
            effective_at=record.effective_at,
            policy_revision=record.policy_revision,
            policy_sha256=record.policy_sha256,
            snapshot_id=record.snapshot_id,
            outcome=record.outcome,
            rule_hits=record.rule_hits,
            query_evidence_json=canonical_json(query_evidence or []),
            model_rationale=record.model_rationale,
            model_config=record.model_config,
            prompt_template_revision=record.prompt_template_revision,
            decided_at=record.decided_at,
            supersedes=record.supersedes,
        )
    )


def read_decision(executor: Executor, decision_id: str) -> DecisionRecord | None:
    rows = executor(
        "SELECT decision_id, title_id, territory_code, effective_at, policy_revision, "
        "policy_sha256, snapshot_id, outcome, rule_hits, model_rationale, model_config, "
        "prompt_template_revision, decided_at, supersedes "
        f"FROM sdl.decision_records WHERE decision_id = {_quote(decision_id)} LIMIT 1"
    )
    if not rows:
        return None
    row = rows[0]
    return DecisionRecord(
        decision_id=row["decision_id"],
        title_id=row["title_id"],
        territory_code=row["territory_code"],
        effective_at=_parse(row["effective_at"]),
        policy_revision=row["policy_revision"],
        policy_sha256=row["policy_sha256"],
        snapshot_id=row["snapshot_id"],
        outcome=row["outcome"],
        rule_hits=list(row["rule_hits"]),
        decided_at=_parse(row["decided_at"]),
        model_rationale=row["model_rationale"],
        model_config=row["model_config"],
        prompt_template_revision=row["prompt_template_revision"],
        supersedes=row["supersedes"],
    )
