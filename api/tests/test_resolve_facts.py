"""End-to-end resolution: real ClickHouse rows -> Facts -> a decision.

These are the tests that would catch the product being wrong rather than the
code being wrong.
"""

from datetime import datetime, timezone

from sdl.evaluator import evaluate, ReleaseRequest
from sdl.resolve import resolve_facts

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"

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


def request_on(year: int, month: int, day: int) -> ReleaseRequest:
    return ReleaseRequest(
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=datetime(year, month, day, tzinfo=timezone.utc),
    )


def test_decision_at_revision_1_clears(http_executor):
    facts, _evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)

    result = evaluate(request_on(2026, 7, 30), facts, POLICY)

    assert result.outcome == "AVAILABLE"
    assert result.rule_hits == []


def test_same_date_holds_once_the_backdated_correction_is_known(http_executor):
    """The heart of it.

    Identical request, identical policy, identical effective date. Only the
    pinned revision differs — and the answer flips, because revision 3
    retroactively restated the grant as AVOD-only. A record pinned at
    revision 1 must therefore keep its original outcome forever.
    """
    facts, _evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=3)

    result = evaluate(request_on(2026, 7, 30), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["LIC-002"]


def test_expired_sync_clearance_holds_at_the_later_date(http_executor):
    facts, _evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=2)

    result = evaluate(request_on(2026, 8, 8), facts, POLICY)

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["CLR-001"]


def test_evidence_covers_every_table_with_a_hash_and_row_count(http_executor):
    _facts, evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=3)

    tables = {item.table_name for item in evidence}
    assert tables == {
        "title_licenses",
        "clearances",
        "ratings",
        "deliveries",
        "continuity_exceptions",
    }
    for item in evidence:
        assert item.canonical_query.startswith("SELECT *")
        assert len(item.result_hash) == 64
        assert item.row_count >= 0
        assert item.max_revision == 3


def test_result_hash_distinguishes_pinned_revisions(http_executor):
    _f1, evidence_1 = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)
    _f3, evidence_3 = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=3)

    hash_at_1 = next(e.result_hash for e in evidence_1 if e.table_name == "title_licenses")
    hash_at_3 = next(e.result_hash for e in evidence_3 if e.table_name == "title_licenses")

    assert hash_at_1 != hash_at_3


def test_result_hash_is_stable_for_an_unchanged_pin(http_executor):
    """A replay recomputes this hash and refuses to certify on mismatch, so an
    unstable hash would make every replay fail."""
    _f, first = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)
    _g, second = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)

    assert [e.result_hash for e in first] == [e.result_hash for e in second]
