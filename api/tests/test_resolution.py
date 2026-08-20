from datetime import datetime, timezone

import pytest

from sdl.record import DecisionRecord, EvidenceSnapshot
from sdl.resolution import UnsupportedRule, build_resolution_plan

RECORD = DecisionRecord(
    "D-1", "TITLE", "NG", datetime(2026, 8, 20, tzinfo=timezone.utc),
    "POL-2026.08", "p", "RS-1", "HOLD", ["LIC-002"],
    datetime(2026, 8, 20, tzinfo=timezone.utc),
)
SNAPSHOT = EvidenceSnapshot(
    "RS-1", datetime(2026, 8, 20, tzinfo=timezone.utc), 3, "m",
    [{"table_name": "title_licenses", "canonical_query": "SELECT licence",
      "result_hash": "h", "row_count": 1, "max_revision": 3}],
)


def test_a_live_blocker_produces_an_open_source_bound_item():
    item = build_resolution_plan(RECORD, SNAPSHOT, current_rule_hits=["LIC-002"])[0]
    assert item.status == "OPEN"
    assert item.kind == "CORRECT_KNOWN_FAILURE"
    assert item.evidence_sources[0]["result_hash"] == "h"


def test_a_disappeared_blocker_is_complete_only_after_reevaluation():
    item = build_resolution_plan(RECORD, SNAPSHOT, current_rule_hits=[])[0]
    assert item.status == "COMPLETE"
    assert "no longer fires LIC-002" in item.completion_condition


def test_an_unreadable_current_world_is_unknown():
    assert build_resolution_plan(RECORD, SNAPSHOT, current_rule_hits=None)[0].status == "UNKNOWN"


def test_missing_evidence_and_failed_evidence_remain_different_work():
    missing = DecisionRecord(**{**RECORD.__dict__, "rule_hits": ["SYN-001"]})
    item = build_resolution_plan(missing, SNAPSHOT, current_rule_hits=["SYN-001"])[0]
    assert item.kind == "ACQUIRE_MISSING_EVIDENCE"


def test_an_unknown_rule_is_refused_not_given_generic_advice():
    unknown = DecisionRecord(**{**RECORD.__dict__, "rule_hits": ["NEW-999"]})
    with pytest.raises(UnsupportedRule):
        build_resolution_plan(unknown, SNAPSHOT, current_rule_hits=[])
