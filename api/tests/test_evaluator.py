"""Tests for the deterministic policy evaluator.

The evaluator is the object the replay verifier reproduces. Its contract is
narrow on purpose: given an identical request, an identical resolved fact set,
and an identical policy revision, it must return an identical outcome and an
identical ordered list of rule hits. Everything interesting about this product
rests on that being true, so it is tested directly rather than through the API.

Point-in-time resolution (latest row version per key at or below a pinned
revision) is NOT the evaluator's job — it happens in retrieval. The evaluator
receives facts already resolved.
"""

from datetime import datetime, timezone

from sdl.evaluator import (
    Clearance,
    Delivery,
    ContinuityException,
    Facts,
    License,
    Rating,
    ReleaseRequest,
    evaluate,
)


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


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


def request(effective_at: datetime | None = None) -> ReleaseRequest:
    return ReleaseRequest(
        title_id="NORTHSTAR-S01E06",
        territory_code="NG",
        effective_at=effective_at or dt(2026, 7, 30),
    )


def clearing_facts(**overrides) -> Facts:
    """A fact set in which every mandatory condition is met."""
    base = dict(
        licenses=[
            License(
                license_id="LIC-NG-0091",
                territory_code="NG",
                rights_scope="SVOD",
                valid_from=dt(2026, 6, 1),
                valid_to=dt(2027, 6, 1),
                status="ACTIVE",
            )
        ],
        clearances=[
            Clearance(
                clearance_id=f"CLR-{kind}",
                asset_ref="Midnight Drive",
                clearance_kind=kind,
                territory_code="NG",
                valid_from=dt(2026, 6, 1),
                valid_to=dt(2027, 6, 1),
                status="ACTIVE",
            )
            for kind in ("MUSIC_SYNC", "MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT")
        ],
        ratings=[
            Rating(
                rating_id="RTG-NG-0007",
                territory_code="NG",
                rating_code="15",
                issued_at=dt(2026, 5, 20),
                expires_at=dt(2028, 5, 20),
                status="VALID",
            )
        ],
        deliveries=[
            Delivery(
                delivery_id="DLV-0004",
                master_version="v1.2",
                approved_at=dt(2026, 5, 28),
                captions_state="APPROVED",
                audio_description_state="APPROVED",
            )
        ],
        continuity_exceptions=[],
    )
    base.update(overrides)
    return Facts(**base)


def test_all_conditions_met_returns_available():
    result = evaluate(request(), clearing_facts(), POLICY)

    assert result.outcome == "AVAILABLE"
    assert result.rule_hits == []


def test_no_licence_covering_effective_date_holds():
    facts = clearing_facts(
        licenses=[
            License(
                license_id="LIC-NG-0091",
                territory_code="NG",
                rights_scope="SVOD",
                valid_from=dt(2026, 6, 1),
                valid_to=dt(2026, 7, 1),
                status="ACTIVE",
            )
        ]
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["LIC-001"]


def test_licence_scope_not_covering_release_path_holds():
    facts = clearing_facts(
        licenses=[
            License(
                license_id="LIC-NG-0091",
                territory_code="NG",
                rights_scope="AVOD",
                valid_from=dt(2026, 6, 1),
                valid_to=dt(2027, 6, 1),
                status="ACTIVE",
            )
        ]
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["LIC-002"]


def test_expired_mandatory_clearance_holds():
    expired_sync = Clearance(
        clearance_id="CLR-MS-0031",
        asset_ref="Midnight Drive",
        clearance_kind="MUSIC_SYNC",
        territory_code="NG",
        valid_from=dt(2026, 6, 1),
        valid_to=dt(2026, 7, 31),
        status="ACTIVE",
    )
    others = [
        Clearance(
            clearance_id=f"CLR-{kind}",
            asset_ref="Midnight Drive",
            clearance_kind=kind,
            territory_code="NG",
            valid_from=dt(2026, 6, 1),
            valid_to=dt(2027, 6, 1),
            status="ACTIVE",
        )
        for kind in ("MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT")
    ]

    result = evaluate(request(dt(2026, 8, 8)), clearing_facts(clearances=[expired_sync, *others]), POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["CLR-001"]


def test_expired_rating_certificate_holds():
    facts = clearing_facts(
        ratings=[
            Rating(
                rating_id="RTG-NG-0007",
                territory_code="NG",
                rating_code="15",
                issued_at=dt(2024, 5, 20),
                expires_at=dt(2026, 5, 20),
                status="VALID",
            )
        ]
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["RTG-001"]


def test_delivery_without_final_approval_holds():
    facts = clearing_facts(
        deliveries=[
            Delivery(
                delivery_id="DLV-0004",
                master_version="v1.2",
                approved_at=None,
                captions_state="APPROVED",
                audio_description_state="APPROVED",
            )
        ]
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["DLV-001"]


def test_open_blocking_continuity_exception_holds():
    facts = clearing_facts(
        continuity_exceptions=[
            ContinuityException(
                exception_id="EXC-0031",
                scene_ref="Sc. 12 — unlicensed signage in frame",
                severity="BLOCKING",
                state="OPEN",
            )
        ]
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["CNT-001"]


def test_missing_rating_fact_escalates_rather_than_holding():
    """Absent facts are not the same as facts that fail.

    A missing rating certificate means the policy cannot make a safe
    determination. Reporting HOLD would assert something the evidence does not
    support; ESCALATE routes it to a human instead.
    """
    result = evaluate(request(dt(2026, 7, 30)), clearing_facts(ratings=[]), POLICY)

    assert result.outcome == "ESCALATE"
    assert result.rule_hits == ["ESC-001"]


def test_contradictory_licence_versions_escalate():
    contradictory = [
        License(
            license_id="LIC-NG-0091",
            territory_code="NG",
            rights_scope="SVOD",
            valid_from=dt(2026, 6, 1),
            valid_to=dt(2027, 6, 1),
            status="ACTIVE",
        ),
        License(
            license_id="LIC-NG-0091",
            territory_code="NG",
            rights_scope="SVOD",
            valid_from=dt(2026, 6, 1),
            valid_to=dt(2027, 6, 1),
            status="TERMINATED",
        ),
    ]

    result = evaluate(request(dt(2026, 7, 30)), clearing_facts(licenses=contradictory), POLICY)

    assert result.outcome == "ESCALATE"
    assert result.rule_hits == ["ESC-001"]


def test_multiple_failures_report_every_rule_hit_in_policy_order():
    """The operator needs the whole blocking picture, not just the first cause,
    and the order must be stable so the receipt reads the same on every replay.
    """
    facts = clearing_facts(
        ratings=[
            Rating(
                rating_id="RTG-NG-0007",
                territory_code="NG",
                rating_code="15",
                issued_at=dt(2024, 5, 20),
                expires_at=dt(2026, 5, 20),
                status="VALID",
            )
        ],
        deliveries=[
            Delivery(
                delivery_id="DLV-0004",
                master_version="v1.2",
                approved_at=None,
                captions_state="PENDING",
                audio_description_state="APPROVED",
            )
        ],
    )

    result = evaluate(request(dt(2026, 7, 30)), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["RTG-001", "DLV-001"]


def test_identical_inputs_produce_identical_decisions():
    """SPEC invariant 3. This is the property the replay verifier depends on."""
    first = evaluate(request(dt(2026, 7, 30)), clearing_facts(), POLICY)
    second = evaluate(request(dt(2026, 7, 30)), clearing_facts(), POLICY)

    assert first == second
