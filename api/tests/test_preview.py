"""The read-only evaluate path.

`make_decision` records. `preview_decision` answers the same question and
records nothing, which is what the operator-facing agent is allowed to reach.
The two must never disagree about an outcome: a preview that could differ from
the recorded decision would make the agent's explanation describe a decision
the ledger does not contain.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from sdl.service import make_decision, preview_decision

EFFECTIVE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _first_title(http_executor) -> tuple[str, str]:
    rows = http_executor(
        "SELECT title_id, territory_code FROM sdl.title_licenses ORDER BY title_id LIMIT 1"
    )
    assert rows, "seed data is missing licences"
    return rows[0]["title_id"], rows[0]["territory_code"]


def test_preview_takes_no_writer_at_all():
    """The guarantee is in the signature, not in a comment.

    A reviewer checking whether the agent path can write should be able to
    read one line to find out.
    """
    assert "writer" not in inspect.signature(preview_decision).parameters


def test_preview_and_recorded_decision_agree(http_executor, writer):
    title_id, territory_code = _first_title(http_executor)

    previewed = preview_decision(
        http_executor,
        title_id=title_id,
        territory_code=territory_code,
        effective_at=EFFECTIVE,
    )
    recorded = make_decision(
        http_executor,
        writer,
        title_id=title_id,
        territory_code=territory_code,
        effective_at=EFFECTIVE,
    )

    assert previewed.decision.outcome == recorded.decision.outcome
    assert list(previewed.decision.rule_hits) == list(recorded.decision.rule_hits)


def test_preview_writes_no_decision_row(http_executor):
    title_id, territory_code = _first_title(http_executor)
    before = http_executor("SELECT count() AS n FROM sdl.decision_records")[0]["n"]

    preview_decision(
        http_executor,
        title_id=title_id,
        territory_code=territory_code,
        effective_at=EFFECTIVE,
    )

    after = http_executor("SELECT count() AS n FROM sdl.decision_records")[0]["n"]
    assert after == before
