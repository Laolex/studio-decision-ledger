"""Canonical row form.

Evidence hashes must depend on the data and nothing else. Transports disagree
about incidental formatting — the ClickHouse MCP server renders a DateTime64 as
`2026-07-31 00:00:00` while the HTTP interface renders the same value as
`2026-07-31 00:00:00.000`. Left alone, a decision recorded through one path
would fail to certify when replayed through the other, for no reason connected
to the facts.

Every `Executor` implementation is therefore required to emit canonical rows.
The canonical form for a timestamp is millisecond precision, always present.
"""

from __future__ import annotations

import re

_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d+))?$")


def canonical_value(value):
    if not isinstance(value, str):
        return value
    match = _TIMESTAMP.match(value)
    if not match:
        return value
    seconds, fraction = match.groups()
    milliseconds = (fraction or "").ljust(3, "0")[:3]
    return f"{seconds}.{milliseconds}"


def canonical_row(row: dict) -> dict:
    return {key: canonical_value(value) for key, value in row.items()}


def canonical_rows(rows: list[dict]) -> list[dict]:
    return [canonical_row(row) for row in rows]
