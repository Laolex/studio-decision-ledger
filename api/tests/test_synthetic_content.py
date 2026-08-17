"""Synthetic-content provenance and performer consent as decision evidence.

The split between the two rules here is the point, and it is easy to lose.

`SYN-001` is ESCALATE: the provenance says an asset is synthetic or assisted
and no consent record exists at all. The policy cannot make a safe
determination from a fact that is absent, so absence is reported as absence
rather than resolved in either direction (SPEC invariant 20).

`CON-001` is HOLD: a consent record exists and does not cover the request.
That is a failed condition, not an absent fact — the evidence is present and
it says no.

Collapsing the two into one outcome would make the product either
over-escalate every ordinary block or, worse, quietly resolve a missing
consent into a clean release.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sdl.evaluator import (
    Clearance,
    Delivery,
    Facts,
    License,
    PerformerConsent,
    Rating,
    ReleaseRequest,
    SyntheticContent,
    evaluate,
)

TERRITORY = "NG"
EFFECTIVE = datetime(2026, 7, 30, tzinfo=timezone.utc)
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2027, 6, 1, tzinfo=timezone.utc)

POLICY = {
    "release_path": "SVOD",
    "rules": [
        {"id": "CLR-001", "mandatory_kinds": ["MUSIC_MASTER"]},
    ],
}


def _baseline_facts(**overrides) -> Facts:
    """A fact set that clears every pre-existing rule, so a test only varies
    the thing it is about."""
    base = dict(
        licenses=[
            License(
                license_id="LIC-1",
                territory_code=TERRITORY,
                rights_scope="SVOD",
                valid_from=FROM,
                valid_to=TO,
                status="ACTIVE",
            )
        ],
        clearances=[
            Clearance(
                clearance_id="CLR-1",
                asset_ref="Midnight Drive",
                clearance_kind="MUSIC_MASTER",
                territory_code=TERRITORY,
                valid_from=FROM,
                valid_to=TO,
                status="ACTIVE",
            )
        ],
        ratings=[
            Rating(
                rating_id="RAT-1",
                territory_code=TERRITORY,
                rating_code="18",
                issued_at=FROM,
                expires_at=TO,
                status="VALID",
            )
        ],
        deliveries=[
            Delivery(
                delivery_id="DEL-1",
                master_version="v4",
                approved_at=FROM,
                captions_state="APPROVED",
                audio_description_state="APPROVED",
            )
        ],
        continuity_exceptions=[],
    )
    base.update(overrides)
    return Facts(**base)


def _evaluate(facts: Facts):
    return evaluate(
        ReleaseRequest(
            title_id="T-1", territory_code=TERRITORY, effective_at=EFFECTIVE
        ),
        facts,
        POLICY,
    )


def _consent(**overrides) -> PerformerConsent:
    fields = dict(
        consent_id="CON-1",
        performer_ref="A. Okafor",
        consent_scope="likeness",
        territory_code=TERRITORY,
        valid_from=FROM,
        valid_to=TO,
        status="ACTIVE",
    )
    fields.update(overrides)
    return PerformerConsent(**fields)


def _provenance(kind: str) -> SyntheticContent:
    return SyntheticContent(
        record_id="SYN-1",
        asset_ref="EP6 SC-14 de-aged flashback",
        generation_kind=kind,
        tool_ref="internal-vfx-2026.3",
        disclosure_obligation_ref="NG-DISC-01",
    )


def test_no_provenance_row_leaves_the_decision_untouched():
    """Option B: the rule applies to recorded provenance, not to its absence.

    Every title in the catalogue carries a row, so absence does not occur in
    practice; treating it as unknown would escalate every title that predates
    the backfill.
    """
    decision = _evaluate(_baseline_facts())
    assert decision.outcome == "AVAILABLE"
    assert decision.rule_hits == []


def test_provenance_of_none_requires_no_consent():
    facts = _baseline_facts(synthetic_content=[_provenance("NONE")])
    decision = _evaluate(facts)

    assert decision.outcome == "AVAILABLE"


@pytest.mark.parametrize("kind", ["SYNTHETIC", "ASSISTED"])
def test_generated_content_with_no_consent_record_escalates(kind):
    """The demo case: a de-aged scene with nothing on file for the performer."""
    facts = _baseline_facts(synthetic_content=[_provenance(kind)], performer_consents=[])
    decision = _evaluate(facts)

    assert decision.outcome == "ESCALATE"
    assert decision.rule_hits == ["SYN-001"]


def test_generated_content_with_covering_consent_releases():
    facts = _baseline_facts(
        synthetic_content=[_provenance("ASSISTED")],
        performer_consents=[_consent()],
    )
    decision = _evaluate(facts)

    assert decision.outcome == "AVAILABLE"


@pytest.mark.parametrize(
    "override",
    [
        {"territory_code": "GB"},
        {"valid_to": datetime(2026, 3, 1, tzinfo=timezone.utc)},
        {"status": "WITHDRAWN"},
        {"consent_scope": "voice"},
    ],
    ids=["wrong-territory", "expired", "withdrawn", "wrong-scope"],
)
def test_a_consent_that_does_not_cover_the_request_holds(override):
    """Present and not covering is a failed condition, not an absent fact."""
    facts = _baseline_facts(
        synthetic_content=[_provenance("ASSISTED")],
        performer_consents=[_consent(**override)],
    )
    decision = _evaluate(facts)

    assert decision.outcome == "HOLD"
    assert decision.rule_hits == ["CON-001"]


def test_consent_scope_of_both_covers_likeness():
    facts = _baseline_facts(
        synthetic_content=[_provenance("SYNTHETIC")],
        performer_consents=[_consent(consent_scope="both")],
    )
    assert _evaluate(facts).outcome == "AVAILABLE"


def test_escalation_wins_over_an_unrelated_block():
    """An absent fact is not ranked against a failed condition.

    If the licence also fails, the honest report is still that no safe
    determination is possible — not HOLD with a tidy reason that happens to
    be knowable.
    """
    facts = _baseline_facts(
        licenses=[
            License(
                license_id="LIC-1",
                territory_code=TERRITORY,
                rights_scope="AVOD",
                valid_from=FROM,
                valid_to=TO,
                status="ACTIVE",
            )
        ],
        synthetic_content=[_provenance("ASSISTED")],
        performer_consents=[],
    )
    decision = _evaluate(facts)

    assert decision.outcome == "ESCALATE"
    assert decision.rule_hits == ["SYN-001"]
