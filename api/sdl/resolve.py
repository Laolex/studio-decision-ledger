"""Resolve pinned evidence into the fact set the evaluator consumes.

Two outputs, both required. The `Facts` feed the deterministic evaluator; the
`QueryEvidence` list is what makes the resulting decision reproducible — it
records the exact query issued, a hash of exactly what came back, and the pin
those results were read at.

The hash is computed over a canonical serialization of the rows rather than
the raw response body, so it survives incidental transport differences (key
order, whitespace) while still changing the moment the data changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from sdl.evaluator import (
    Clearance,
    ContinuityException,
    Delivery,
    Facts,
    License,
    PerformerConsent,
    Rating,
    SyntheticContent,
)
from sdl.retrieval import TABLE_KEYS, point_in_time_query

Executor = Callable[[str], list[dict]]

EVIDENCE_TABLES = (
    "title_licenses",
    "clearances",
    "ratings",
    "deliveries",
    "continuity_exceptions",
    "synthetic_content",
    "performer_consents",
)


@dataclass(frozen=True)
class QueryEvidence:
    table_name: str
    canonical_query: str
    result_hash: str
    row_count: int
    max_revision: int


def _parse_timestamp(value: str | None) -> datetime | None:
    """ClickHouse DateTime64 arrives as 'YYYY-MM-DD HH:MM:SS.mmm', naive UTC."""
    if value in (None, "", "1970-01-01 00:00:00.000"):
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def _require_timestamp(value: str | None) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"expected a timestamp, got {value!r}")
    return parsed


def canonical_result_hash(rows: Iterable[dict]) -> str:
    """Stable over key order and whitespace; sensitive to any value change."""
    payload = json.dumps(
        [dict(sorted(row.items())) for row in rows],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_facts(
    executor: Executor,
    title_id: str,
    territory_code: str,
    max_revision: int,
) -> tuple[Facts, list[QueryEvidence]]:
    rows_by_table: dict[str, list[dict]] = {}
    evidence: list[QueryEvidence] = []

    for table in EVIDENCE_TABLES:
        if table not in TABLE_KEYS:
            raise KeyError(f"unknown evidence table {table!r}")
        query = point_in_time_query(table, title_id, territory_code, max_revision)
        rows = executor(query)
        rows_by_table[table] = rows
        evidence.append(
            QueryEvidence(
                table_name=table,
                canonical_query=query,
                result_hash=canonical_result_hash(rows),
                row_count=len(rows),
                max_revision=max_revision,
            )
        )

    facts = Facts(
        licenses=[
            License(
                license_id=row["license_id"],
                territory_code=row["territory_code"],
                rights_scope=row["rights_scope"],
                valid_from=_require_timestamp(row["valid_from"]),
                valid_to=_require_timestamp(row["valid_to"]),
                status=row["status"],
            )
            for row in rows_by_table["title_licenses"]
        ],
        clearances=[
            Clearance(
                clearance_id=row["clearance_id"],
                asset_ref=row["asset_ref"],
                clearance_kind=row["clearance_kind"],
                territory_code=row["territory_code"],
                valid_from=_require_timestamp(row["valid_from"]),
                valid_to=_require_timestamp(row["valid_to"]),
                status=row["status"],
            )
            for row in rows_by_table["clearances"]
        ],
        ratings=[
            Rating(
                rating_id=row["rating_id"],
                territory_code=row["territory_code"],
                rating_code=row["rating_code"],
                issued_at=_require_timestamp(row["issued_at"]),
                expires_at=_require_timestamp(row["expires_at"]),
                status=row["status"],
            )
            for row in rows_by_table["ratings"]
        ],
        deliveries=[
            Delivery(
                delivery_id=row["delivery_id"],
                master_version=row["master_version"],
                approved_at=_parse_timestamp(row["approved_at"]),
                captions_state=row["captions_state"],
                audio_description_state=row["audio_description_state"],
            )
            for row in rows_by_table["deliveries"]
        ],
        continuity_exceptions=[
            ContinuityException(
                exception_id=row["exception_id"],
                scene_ref=row["scene_ref"],
                severity=row["severity"],
                state=row["state"],
            )
            for row in rows_by_table["continuity_exceptions"]
        ],
        synthetic_content=[
            SyntheticContent(
                record_id=row["record_id"],
                asset_ref=row["asset_ref"],
                generation_kind=row["generation_kind"],
                tool_ref=row["tool_ref"],
                disclosure_obligation_ref=row["disclosure_obligation_ref"],
            )
            for row in rows_by_table["synthetic_content"]
        ],
        performer_consents=[
            PerformerConsent(
                consent_id=row["consent_id"],
                performer_ref=row["performer_ref"],
                consent_scope=row["consent_scope"],
                territory_code=row["territory_code"],
                valid_from=_require_timestamp(row["valid_from"]),
                valid_to=_require_timestamp(row["valid_to"]),
                status=row["status"],
            )
            for row in rows_by_table["performer_consents"]
        ],
    )

    return facts, evidence
