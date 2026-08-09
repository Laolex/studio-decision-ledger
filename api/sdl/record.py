"""Snapshots and the decision-record write path.

Writes never travel through MCP. The MCP surface is what the agent can reach,
and an agent that can write could forge or amend a decision record — which
would make immutability a matter of good behaviour rather than architecture.
The ledger is written by the application service alone.

The manifest hash binds a decision to exactly what was read. It is computed
over a canonical serialization sorted by table name, so retrieval order (an
implementation detail) cannot change it, while any change to a query, a result
hash, or a row count does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from sdl.resolve import QueryEvidence

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


@dataclass(frozen=True)
class DecisionRecord:
    """An immutable decision. A correction is a new record naming `supersedes`.

    `model_rationale` is stored apart from `rule_hits` on purpose: it is an
    artifact of the explanation, never a determinant of the outcome.
    """

    decision_id: str
    title_id: str
    territory_code: str
    effective_at: datetime
    policy_revision: str
    policy_sha256: str
    snapshot_id: str
    outcome: str
    rule_hits: list[str]
    decided_at: datetime
    model_rationale: str = ""
    model_config: str = ""
    prompt_template_revision: str = ""
    supersedes: str = ""


@dataclass(frozen=True)
class EvidenceSnapshot:
    snapshot_id: str
    captured_at: datetime
    max_revision: int
    source_manifest_hash: str
    facts: list[dict]


def _format_timestamp(moment: datetime) -> str:
    return moment.strftime(TIMESTAMP_FORMAT)[:-3]


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_snapshot(
    evidence: Iterable[QueryEvidence],
    snapshot_id: str,
    captured_at: datetime,
    max_revision: int,
) -> EvidenceSnapshot:
    facts = sorted(
        (
            {
                "table_name": item.table_name,
                "canonical_query": item.canonical_query,
                "result_hash": item.result_hash,
                "row_count": item.row_count,
                "max_revision": item.max_revision,
            }
            for item in evidence
        ),
        key=lambda fact: fact["table_name"],
    )
    manifest_hash = hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()
    return EvidenceSnapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        max_revision=max_revision,
        source_manifest_hash=manifest_hash,
        facts=facts,
    )


def snapshot_insert_sql(snapshot: EvidenceSnapshot) -> str:
    values = ", ".join(
        [
            _quote(snapshot.snapshot_id),
            _quote(_format_timestamp(snapshot.captured_at)),
            str(int(snapshot.max_revision)),
            _quote(snapshot.source_manifest_hash),
            _quote(canonical_json(snapshot.facts)),
        ]
    )
    return (
        "INSERT INTO sdl.decision_snapshots "
        "(snapshot_id, captured_at, max_revision, source_manifest_hash, facts_json) VALUES "
        f"({values})"
    )


def decision_insert_sql(
    *,
    decision_id: str,
    title_id: str,
    territory_code: str,
    effective_at: datetime,
    policy_revision: str,
    policy_sha256: str,
    snapshot_id: str,
    outcome: str,
    rule_hits: Sequence[str],
    query_evidence_json: str,
    model_rationale: str,
    model_config: str,
    prompt_template_revision: str,
    decided_at: datetime,
    supersedes: str = "",
) -> str:
    hits = "[" + ", ".join(_quote(hit) for hit in rule_hits) + "]"
    values = ", ".join(
        [
            _quote(decision_id),
            _quote(title_id),
            _quote(territory_code),
            _quote(_format_timestamp(effective_at)),
            _quote(policy_revision),
            _quote(policy_sha256),
            _quote(snapshot_id),
            _quote(outcome),
            hits,
            _quote(query_evidence_json),
            _quote(model_rationale),
            _quote(model_config),
            _quote(prompt_template_revision),
            _quote(_format_timestamp(decided_at)),
            _quote(supersedes),
        ]
    )
    return (
        "INSERT INTO sdl.decision_records "
        "(decision_id, title_id, territory_code, effective_at, policy_revision, "
        "policy_sha256, snapshot_id, outcome, rule_hits, query_evidence, "
        "model_rationale, model_config, prompt_template_revision, decided_at, supersedes) "
        f"VALUES ({values})"
    )
