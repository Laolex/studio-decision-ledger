"""Deterministic work plans for closing a recorded release blocker."""

from __future__ import annotations

from dataclasses import dataclass

from sdl.record import DecisionRecord, EvidenceSnapshot


class UnsupportedRule(ValueError):
    """Raised rather than inventing a remediation for an unknown policy rule."""


RULE_PLANS = {
    "LIC-001": ("CORRECT_KNOWN_FAILURE", "title_licenses", "Record an active licence covering the territory and release date."),
    "LIC-002": ("CORRECT_KNOWN_FAILURE", "title_licenses", "Correct the licence grant so it covers the requested release path."),
    "CLR-001": ("CORRECT_KNOWN_FAILURE", "clearances", "Record an active clearance covering the asset, territory and release date."),
    "RTG-001": ("CORRECT_KNOWN_FAILURE", "ratings", "Record a valid rating certificate for the territory and release date."),
    "DLV-001": ("CORRECT_KNOWN_FAILURE", "deliveries", "Complete final delivery approval and required accessibility checks."),
    "CNT-001": ("CORRECT_KNOWN_FAILURE", "continuity_exceptions", "Resolve the blocking continuity exception with its evidence reference."),
    "ESC-001": ("ACQUIRE_MISSING_EVIDENCE", "", "Acquire the missing or contradictory evidence named by the release review."),
    "SYN-001": ("ACQUIRE_MISSING_EVIDENCE", "performer_consents", "Record the missing performer consent for the generated asset."),
    "CON-001": ("CORRECT_KNOWN_FAILURE", "performer_consents", "Record consent covering the required scope, territory and date."),
}


@dataclass(frozen=True)
class ResolutionItem:
    rule_id: str
    kind: str
    instruction: str
    evidence_sources: tuple[dict, ...]
    completion_condition: str
    status: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind,
            "instruction": self.instruction,
            "evidence_sources": list(self.evidence_sources),
            "completion_condition": self.completion_condition,
            "status": self.status,
        }


def build_resolution_plan(
    record: DecisionRecord,
    snapshot: EvidenceSnapshot,
    *,
    current_rule_hits: list[str] | None,
) -> list[ResolutionItem]:
    """Build one independently rerunnable item per original rule hit."""
    items: list[ResolutionItem] = []
    for rule_id in record.rule_hits:
        if rule_id not in RULE_PLANS:
            raise UnsupportedRule(f"no resolution mapping exists for {rule_id}")
        kind, table, instruction = RULE_PLANS[rule_id]
        sources = tuple(
            {
                "table_name": fact["table_name"],
                "canonical_query": fact["canonical_query"],
                "result_hash": fact["result_hash"],
                "snapshot_id": snapshot.snapshot_id,
            }
            for fact in snapshot.facts
            if not table or fact["table_name"] == table
        )
        if current_rule_hits is None:
            status = "UNKNOWN"
        else:
            status = "OPEN" if rule_id in current_rule_hits else "COMPLETE"
        items.append(
            ResolutionItem(
                rule_id=rule_id,
                kind=kind,
                instruction=instruction,
                evidence_sources=sources,
                completion_condition=(
                    f"A current-evidence evaluation under {record.policy_revision} no longer "
                    f"fires {rule_id}."
                ),
                status=status,
            )
        )
    return items
