"""Point-in-time fact retrieval.

Resolves the version of each fact that was knowable at a pinned revision. The
generated SQL is *canonical*: identical inputs must produce a byte-identical
query string, because that string is hashed into the decision record and any
drift in formatting would fail an otherwise valid replay.

Resolution uses `LIMIT 1 BY <natural_key>` over `ORDER BY <key>, revision DESC`,
which keeps the newest surviving version of each key at or below the pin.
"""

from __future__ import annotations

# Natural key per evidence table, and whether the table is territory-scoped.
TABLE_KEYS: dict[str, tuple[str, bool]] = {
    "title_licenses": ("license_id", True),
    "clearances": ("clearance_id", True),
    "ratings": ("rating_id", True),
    "deliveries": ("delivery_id", False),
    "continuity_exceptions": ("exception_id", False),
}


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def point_in_time_query(
    table: str,
    title_id: str,
    territory_code: str,
    max_revision: int,
) -> str:
    if table not in TABLE_KEYS:
        raise KeyError(f"unknown evidence table {table!r}")
    natural_key, territory_scoped = TABLE_KEYS[table]

    predicates = [f"title_id = {_quote(title_id)}"]
    if territory_scoped:
        predicates.append(f"territory_code = {_quote(territory_code)}")
    predicates.append(f"revision <= {int(max_revision)}")

    return "\n".join(
        [
            "SELECT *",
            f"FROM sdl.{table}",
            f"WHERE {' AND '.join(predicates)}",
            f"ORDER BY {natural_key} ASC, revision DESC",
            f"LIMIT 1 BY {natural_key}",
        ]
    )
