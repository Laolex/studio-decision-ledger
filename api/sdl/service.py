"""Decision service: the operations the console performs.

Presentation lives here rather than in the API layer so that grouping and
plain-language blocking conditions are testable without HTTP, and so the
console never has to interpret a rule id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sdl.evaluator import Decision, Facts, ReleaseRequest, evaluate
from sdl.ledger import (
    Writer,
    current_max_revision,
    read_policy,
    write_decision,
    write_snapshot,
)
from sdl.record import DecisionRecord, EvidenceSnapshot, build_snapshot
from sdl.resolve import Executor, QueryEvidence, resolve_facts

DEFAULT_POLICY_REVISION = "POL-2026.07"

# A rule id is an index into a policy document, not an explanation. An operator
# needs to know what to go and fix.
RULE_LANGUAGE = {
    "LIC-001": "No active licence covers this territory on the requested date.",
    "LIC-002": "The territory grant does not cover this release path.",
    "CLR-001": "A required clearance is not active on the requested date.",
    "RTG-001": "No valid rating certificate is on file for this territory.",
    "DLV-001": "Final delivery approval is missing or captions are not approved.",
    "CNT-001": "A blocking continuity exception is still open.",
    "ESC-001": "The facts on file are incomplete or contradictory, so no safe determination is possible.",
}


@dataclass(frozen=True)
class RecordedDecision:
    record: DecisionRecord
    snapshot: EvidenceSnapshot
    decision: Decision
    facts: Facts
    evidence: list[QueryEvidence]


@dataclass(frozen=True)
class PreviewedDecision:
    """A decision reached but deliberately not recorded."""

    decision: Decision
    facts: Facts
    evidence: list[QueryEvidence]
    policy_revision: str
    policy_sha256: str
    max_revision: int


def _timestamp_id(prefix: str, moment: datetime) -> str:
    return f"{prefix}-{moment.strftime('%Y-%m-%d')}-{uuid4().hex[:4].upper()}"


def blocking_condition(decision: Decision) -> str:
    if not decision.rule_hits:
        return ""
    return " ".join(RULE_LANGUAGE.get(hit, hit) for hit in decision.rule_hits)


def _tone(hits: list[str], owned: set[str]) -> str:
    return "hold" if any(hit in owned for hit in hits) else "clear"


def evidence_groups(facts: Facts, decision: Decision, policy_revision: str) -> list[dict]:
    """Fold five tables into the three groups an operator thinks in."""
    hits = list(decision.rule_hits)

    rights_items = [
        {
            "name": f"Licence {licence.license_id}",
            "value": f"{licence.rights_scope} · through {licence.valid_to:%d %b %Y}",
            "tone": "hold" if "LIC-002" in hits or "LIC-001" in hits else "clear",
        }
        for licence in facts.licenses
    ] + [
        {
            "name": f"{clearance.clearance_kind.replace('_', ' ').title()}: {clearance.asset_ref}",
            "value": f"Covered through {clearance.valid_to:%d %b %Y}",
            "tone": "clear",
        }
        for clearance in facts.clearances
    ]

    delivery_items = [
        {
            "name": f"Master {delivery.master_version}",
            "value": (
                f"Approved {delivery.approved_at:%d %b %Y}"
                if delivery.approved_at
                else "Not approved"
            ),
            "tone": "clear" if delivery.approved_at else "hold",
        }
        for delivery in facts.deliveries
    ] + [
        {
            "name": f"Continuity {exception.exception_id}",
            "value": f"{exception.scene_ref} · {exception.state.title()}",
            "tone": "hold" if exception.severity == "BLOCKING" and exception.state == "OPEN" else "clear",
        }
        for exception in facts.continuity_exceptions
    ]

    policy_items = [
        {
            "name": f"Rating {rating.rating_code}",
            "value": f"Valid through {rating.expires_at:%d %b %Y}",
            "tone": "hold" if "RTG-001" in hits else "clear",
        }
        for rating in facts.ratings
    ] + [
        {"name": "Policy revision", "value": policy_revision, "tone": "clear"}
    ]

    return [
        {
            "label": "Rights & clearances",
            "tone": _tone(hits, {"LIC-001", "LIC-002", "CLR-001"}),
            "summary": _summary(hits, {"LIC-001", "LIC-002", "CLR-001"}),
            "items": rights_items,
        },
        {
            "label": "Delivery & continuity",
            "tone": _tone(hits, {"DLV-001", "CNT-001"}),
            "summary": _summary(hits, {"DLV-001", "CNT-001"}),
            "items": delivery_items,
        },
        {
            "label": "Release policy",
            "tone": _tone(hits, {"RTG-001", "ESC-001"}),
            "summary": _summary(hits, {"RTG-001", "ESC-001"}),
            "items": policy_items,
        },
    ]


def _summary(hits: list[str], owned: set[str]) -> str:
    count = sum(1 for hit in hits if hit in owned)
    if count == 0:
        return "Ready to release"
    return f"{count} blocking condition" + ("s" if count > 1 else "")


def preview_decision(
    executor: Executor,
    *,
    title_id: str,
    territory_code: str,
    effective_at: datetime,
    policy_revision: str = DEFAULT_POLICY_REVISION,
    max_revision: int | None = None,
) -> PreviewedDecision:
    """Reach a decision without recording one.

    This exists for the operator-facing agent. The agent answers questions an
    operator asks while thinking, and most of those questions should not leave
    a decision in the ledger — a receipt is a deliberate act, not a side effect
    of asking.

    It takes no `writer`, which is the whole point. The read-only guarantee is
    a property of the signature rather than of the implementation, so it cannot
    be lost by a later edit to the body.
    """
    if max_revision is None:
        max_revision = current_max_revision(executor, title_id)
    policy, policy_sha256 = read_policy(executor, policy_revision)

    facts, evidence = resolve_facts(executor, title_id, territory_code, max_revision)
    decision = evaluate(
        ReleaseRequest(
            title_id=title_id, territory_code=territory_code, effective_at=effective_at
        ),
        facts,
        policy,
    )
    return PreviewedDecision(
        decision=decision,
        facts=facts,
        evidence=evidence,
        policy_revision=policy_revision,
        policy_sha256=policy_sha256,
        max_revision=max_revision,
    )


def make_decision(
    executor: Executor,
    writer: Writer,
    *,
    title_id: str,
    territory_code: str,
    effective_at: datetime,
    policy_revision: str = DEFAULT_POLICY_REVISION,
    now: datetime | None = None,
    max_revision: int | None = None,
    decision_id: str | None = None,
    snapshot_id: str | None = None,
) -> RecordedDecision:
    """Record a decision.

    `max_revision` pins the decision to the evidence knowable at a given point
    in system time. Left unset it means "everything known now", which is the
    ordinary case. Setting it is how a decision taken before a correction is
    recorded — not a test affordance, but the situation the product exists for.

    The evaluation itself is `preview_decision`. Sharing that path is what
    guarantees the agent's explanation and the recorded receipt can never
    describe different outcomes for the same evidence.
    """
    now = now or datetime.now(timezone.utc)
    previewed = preview_decision(
        executor,
        title_id=title_id,
        territory_code=territory_code,
        effective_at=effective_at,
        policy_revision=policy_revision,
        max_revision=max_revision,
    )
    decision = previewed.decision
    facts = previewed.facts
    evidence = previewed.evidence
    max_revision = previewed.max_revision
    policy_sha256 = previewed.policy_sha256

    snapshot = build_snapshot(
        evidence, snapshot_id or _timestamp_id("RS", now), now, max_revision=max_revision
    )
    record = DecisionRecord(
        decision_id=decision_id or _timestamp_id("D", now),
        title_id=title_id,
        territory_code=territory_code,
        effective_at=effective_at,
        policy_revision=policy_revision,
        policy_sha256=policy_sha256,
        snapshot_id=snapshot.snapshot_id,
        outcome=decision.outcome,
        rule_hits=list(decision.rule_hits),
        decided_at=now,
    )

    # Snapshot first: a decision naming a snapshot that does not exist would be
    # unverifiable, whereas an orphan snapshot is merely unused.
    write_snapshot(writer, snapshot)
    write_decision(
        writer,
        record,
        query_evidence=[
            {
                "table_name": item.table_name,
                "canonical_query": item.canonical_query,
                "result_hash": item.result_hash,
                "row_count": item.row_count,
            }
            for item in evidence
        ],
    )

    return RecordedDecision(
        record=record, snapshot=snapshot, decision=decision, facts=facts, evidence=evidence
    )
