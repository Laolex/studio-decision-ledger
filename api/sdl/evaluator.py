"""Deterministic policy evaluator.

Given an identical request, resolved fact set, and policy revision, this must
return an identical outcome and an identical ordered list of rule hits. The
replay verifier reproduces this function and nothing else — Gemini is never in
the path that determines an outcome.

Facts arrive already resolved to a point in time. Snapshot filtering is the
retrieval layer's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ReleaseRequest:
    title_id: str
    territory_code: str
    effective_at: datetime


@dataclass(frozen=True)
class License:
    license_id: str
    territory_code: str
    rights_scope: str
    valid_from: datetime
    valid_to: datetime
    status: str


@dataclass(frozen=True)
class Clearance:
    clearance_id: str
    asset_ref: str
    clearance_kind: str
    territory_code: str
    valid_from: datetime
    valid_to: datetime
    status: str


@dataclass(frozen=True)
class Rating:
    rating_id: str
    territory_code: str
    rating_code: str
    issued_at: datetime
    expires_at: datetime
    status: str


@dataclass(frozen=True)
class Delivery:
    delivery_id: str
    master_version: str
    approved_at: datetime | None
    captions_state: str
    audio_description_state: str


@dataclass(frozen=True)
class ContinuityException:
    exception_id: str
    scene_ref: str
    severity: str
    state: str


@dataclass(frozen=True)
class SyntheticContent:
    """Generation provenance recorded against an asset.

    `generation_kind` is one of SYNTHETIC, ASSISTED or NONE. It is a recorded
    fact retrieved into the snapshot, never inferred from the asset itself and
    never supplied by the model (SPEC invariant 19).
    """

    record_id: str
    asset_ref: str
    generation_kind: str
    tool_ref: str
    disclosure_obligation_ref: str


@dataclass(frozen=True)
class PerformerConsent:
    consent_id: str
    performer_ref: str
    consent_scope: str
    territory_code: str
    valid_from: datetime
    valid_to: datetime
    status: str


@dataclass(frozen=True)
class Facts:
    licenses: list[License] = field(default_factory=list)
    clearances: list[Clearance] = field(default_factory=list)
    ratings: list[Rating] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    continuity_exceptions: list[ContinuityException] = field(default_factory=list)
    synthetic_content: list[SyntheticContent] = field(default_factory=list)
    performer_consents: list[PerformerConsent] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    outcome: str
    rule_hits: list[str]


def _covers(valid_from: datetime, valid_to: datetime, moment: datetime) -> bool:
    """Half-open interval [valid_from, valid_to).

    Half-open avoids the off-by-one argument entirely: a window ending
    2026-07-31 00:00 does not cover 2026-07-31 00:00.
    """
    return valid_from <= moment < valid_to


def _rule(policy: dict, rule_id: str) -> dict:
    for rule in policy["rules"]:
        if rule["id"] == rule_id:
            return rule
    raise KeyError(f"policy {policy.get('policy_revision')!r} has no rule {rule_id!r}")


def _has_duplicate_keys(records: list, key: str) -> bool:
    """More than one surviving version of the same natural key.

    Resolved facts carry exactly one version per key. Two means point-in-time
    resolution failed upstream, and the evaluator must not guess which is
    authoritative.
    """
    seen = [getattr(record, key) for record in records]
    return len(seen) != len(set(seen))


def evaluate(request: ReleaseRequest, facts: Facts, policy: dict) -> Decision:
    rule_hits: list[str] = []

    # ESC-001 runs first. An absent or contradictory fact is not a failed
    # condition — reporting HOLD would assert something the evidence does not
    # support. Route it to a human instead.
    required = (
        facts.licenses,
        facts.clearances,
        facts.ratings,
        facts.deliveries,
    )
    contradictory = (
        _has_duplicate_keys(facts.licenses, "license_id")
        or _has_duplicate_keys(facts.clearances, "clearance_id")
        or _has_duplicate_keys(facts.ratings, "rating_id")
        or _has_duplicate_keys(facts.deliveries, "delivery_id")
    )
    if any(len(group) == 0 for group in required) or contradictory:
        return Decision(outcome="ESCALATE", rule_hits=["ESC-001"])

    # SYN-001 also escalates, and for the same reason: an asset recorded as
    # generated with no consent on file anywhere is a gap in the record, not a
    # condition that failed. It is checked before the HOLD rules so a knowable
    # block never masks an unknowable one — reporting HOLD with a tidy reason
    # would be a determination the evidence does not support.
    generated = [
        record
        for record in facts.synthetic_content
        if record.generation_kind in ("SYNTHETIC", "ASSISTED")
    ]
    if generated and not facts.performer_consents:
        return Decision(outcome="ESCALATE", rule_hits=["SYN-001"])

    covering = [
        licence
        for licence in facts.licenses
        if licence.territory_code == request.territory_code
        and licence.status == "ACTIVE"
        and _covers(licence.valid_from, licence.valid_to, request.effective_at)
    ]
    if not covering:
        rule_hits.append("LIC-001")
    else:
        release_path = policy["release_path"]
        if not any(licence.rights_scope == release_path for licence in covering):
            rule_hits.append("LIC-002")

    clearance_rule = _rule(policy, "CLR-001")
    mandatory_kinds = clearance_rule.get("mandatory_kinds", [])
    active_kinds = {
        clearance.clearance_kind
        for clearance in facts.clearances
        if clearance.territory_code == request.territory_code
        and clearance.status == "ACTIVE"
        and _covers(clearance.valid_from, clearance.valid_to, request.effective_at)
    }
    if any(kind not in active_kinds for kind in mandatory_kinds):
        rule_hits.append("CLR-001")

    valid_rating = any(
        rating.territory_code == request.territory_code
        and rating.status == "VALID"
        and _covers(rating.issued_at, rating.expires_at, request.effective_at)
        for rating in facts.ratings
    )
    if not valid_rating:
        rule_hits.append("RTG-001")

    approved_delivery = any(
        delivery.approved_at is not None and delivery.captions_state == "APPROVED"
        for delivery in facts.deliveries
    )
    if not approved_delivery:
        rule_hits.append("DLV-001")

    if any(
        exception.severity == "BLOCKING" and exception.state == "OPEN"
        for exception in facts.continuity_exceptions
    ):
        rule_hits.append("CNT-001")

    # CON-001. Consent is on file; the question is whether it reaches this
    # request. Scope is satisfied by an explicit likeness grant or by a grant
    # of both — a voice-only consent does not cover a de-aged image.
    if generated:
        covering_consent = [
            consent
            for consent in facts.performer_consents
            if consent.territory_code == request.territory_code
            and consent.status == "ACTIVE"
            and consent.consent_scope in ("likeness", "both")
            and _covers(consent.valid_from, consent.valid_to, request.effective_at)
        ]
        if not covering_consent:
            rule_hits.append("CON-001")

    if rule_hits:
        return Decision(outcome="HOLD", rule_hits=rule_hits)
    return Decision(outcome="AVAILABLE", rule_hits=[])
