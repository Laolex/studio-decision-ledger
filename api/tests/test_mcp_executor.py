"""The ClickHouse MCP server is the production retrieval path.

The hackathon requires the partner service to be genuinely called at runtime,
and the product requires that what the MCP path returns is exactly what a
decision was recorded against. Parity with a direct query is therefore a
correctness property, not a nicety: if the two paths disagree on so much as a
timestamp format, a decision recorded through one and replayed through the
other would fail to certify.
"""

from datetime import datetime, timezone

from sdl.evaluator import evaluate, ReleaseRequest
from sdl.mcp_executor import ClickHouseMCPExecutor
from sdl.resolve import EVIDENCE_TABLES, resolve_facts
from sdl.retrieval import point_in_time_query

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


def test_mcp_rows_are_identical_to_direct_query_rows(clickhouse_env, http_executor):
    sql = point_in_time_query("title_licenses", TITLE, TERRITORY, max_revision=1)

    with ClickHouseMCPExecutor(clickhouse_env) as execute:
        via_mcp = execute(sql)

    assert via_mcp == http_executor(sql)


def test_timestamps_survive_the_mcp_round_trip(clickhouse_env):
    """Resolution parses these into datetimes; a format change breaks it."""
    sql = point_in_time_query("clearances", TITLE, TERRITORY, max_revision=2)

    with ClickHouseMCPExecutor(clickhouse_env) as execute:
        rows = execute(sql)

    sync_row = next(r for r in rows if r["clearance_kind"] == "MUSIC_SYNC")
    assert sync_row["valid_to"] == "2026-07-31 00:00:00.000"


def test_full_decision_through_the_mcp_path(clickhouse_env):
    """The graded path, end to end: MCP -> resolution -> deterministic outcome."""
    with ClickHouseMCPExecutor(clickhouse_env) as execute:
        facts, evidence = resolve_facts(execute, TITLE, TERRITORY, max_revision=3)

    result = evaluate(
        ReleaseRequest(
            title_id=TITLE,
            territory_code=TERRITORY,
            effective_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        ),
        facts,
        POLICY,
    )

    assert result.outcome == "HOLD"
    assert result.rule_hits == ["LIC-002"]
    assert len(evidence) == len(EVIDENCE_TABLES)
    assert all(len(item.result_hash) == 64 for item in evidence)


def test_evidence_hashes_match_across_both_paths(clickhouse_env, http_executor):
    """A decision recorded through MCP must replay identically. If the hashes
    diverge by path, every replay would report NOT_CERTIFIED."""
    _f, http_evidence = resolve_facts(http_executor, TITLE, TERRITORY, max_revision=1)

    with ClickHouseMCPExecutor(clickhouse_env) as execute:
        _g, mcp_evidence = resolve_facts(execute, TITLE, TERRITORY, max_revision=1)

    assert [e.result_hash for e in mcp_evidence] == [e.result_hash for e in http_evidence]
