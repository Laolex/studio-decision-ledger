"""Service-level tests, including recording a decision at a historical pin.

The demo needs a decision taken before a correction landed. That is not a test
fixture — it is the ordinary case the product exists for, so the service has to
support it directly rather than through a back door.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from sdl.ledger import read_decision, read_snapshot
from sdl.service import make_decision
from sdl.verifier import verify
from sdl.ledger import read_policy

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"
EFFECTIVE_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)


@pytest.fixture
def ids():
    token = uuid4().hex[:10].upper()
    return f"D-{token}", f"RS-{token}"


def test_decision_pinned_to_an_earlier_revision_clears(http_executor, writer, ids):
    decision_id, snapshot_id = ids

    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        max_revision=1,
        decision_id=decision_id,
        snapshot_id=snapshot_id,
        now=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
    )

    assert recorded.record.outcome == "AVAILABLE"
    assert recorded.snapshot.max_revision == 1
    assert recorded.record.decision_id == decision_id


def test_an_unpinned_decision_uses_current_evidence(http_executor, writer, ids):
    decision_id, snapshot_id = ids

    recorded = make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        decision_id=decision_id,
        snapshot_id=snapshot_id,
    )

    assert recorded.snapshot.max_revision == 3
    assert recorded.record.outcome == "HOLD"


def test_a_historical_record_still_certifies_after_the_correction(http_executor, writer, ids):
    """The property the whole product exists to hold.

    The decision was taken at revision 1. Revision 3 then restated the grant
    retroactively. The record must still replay to its original outcome.
    """
    decision_id, snapshot_id = ids
    make_decision(
        http_executor,
        writer,
        title_id=TITLE,
        territory_code=TERRITORY,
        effective_at=EFFECTIVE_AT,
        max_revision=1,
        decision_id=decision_id,
        snapshot_id=snapshot_id,
    )

    record = read_decision(http_executor, decision_id)
    snapshot = read_snapshot(http_executor, snapshot_id)
    policy, _sha = read_policy(http_executor, record.policy_revision)
    result = verify(record, snapshot, policy, http_executor)

    assert record.outcome == "AVAILABLE"
    assert result.capability_class == "C2"
