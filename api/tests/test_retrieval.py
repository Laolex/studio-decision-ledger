"""Tests for point-in-time fact retrieval.

Everything downstream trusts this layer. If resolution returns today's truth
when a decision pinned yesterday's, the replay verifier certifies a lie — so
these run against real ClickHouse rather than a fake.
"""

from sdl.retrieval import point_in_time_query

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"


def test_licence_in_force_at_revision_1_is_the_original_svod_grant(http_executor):
    sql = point_in_time_query("title_licenses", TITLE, TERRITORY, max_revision=1)

    rows = http_executor(sql)

    assert [row["rights_scope"] for row in rows] == ["SVOD"]


def test_backdated_correction_is_visible_at_revision_3(http_executor):
    """Revision 3 restates the grant as AVOD-only and backdates it.

    Same title, same territory, same query shape — only the pin differs. This
    is the property the whole product rests on.
    """
    sql = point_in_time_query("title_licenses", TITLE, TERRITORY, max_revision=3)

    rows = http_executor(sql)

    assert [row["rights_scope"] for row in rows] == ["AVOD"]


def test_resolution_returns_one_row_per_natural_key(http_executor):
    """Two surviving versions of one key would make the evaluator escalate."""
    sql = point_in_time_query("clearances", TITLE, TERRITORY, max_revision=3)

    rows = http_executor(sql)
    clearance_ids = [row["clearance_id"] for row in rows]

    assert len(clearance_ids) == len(set(clearance_ids))


def test_query_text_is_stable_across_calls():
    """The query text is hashed into the decision record, so it must be byte
    identical for identical inputs or replay fails on a formatting change."""
    first = point_in_time_query("title_licenses", TITLE, TERRITORY, max_revision=1)
    second = point_in_time_query("title_licenses", TITLE, TERRITORY, max_revision=1)

    assert first == second
