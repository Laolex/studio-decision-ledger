"""Bounded, offline licence CSV preflight. No executor or writer is accepted."""

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class LicenseRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_id: str = Field(min_length=1, max_length=200)
    title_id: str = Field(min_length=1, max_length=200)
    territory_code: str = Field(pattern=r"^[A-Z]{2}$")
    rights_scope: Literal["SVOD", "AVOD", "FAST", "TVOD"]
    valid_from: datetime
    valid_to: datetime
    status: Literal["ACTIVE", "SUSPENDED", "TERMINATED"]
    amendment_note: str = Field(default="", max_length=2000)

    @field_validator("license_id", "title_id", "amendment_note")
    @classmethod
    def clean_text(cls, value):
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        if value != value.strip():
            raise ValueError("Leading or trailing whitespace is not allowed")
        return value

    @field_validator("valid_from", "valid_to", mode="before")
    @classmethod
    def explicit_timestamp(cls, value):
        # Avoid accepting Unix numeric strings, date-only values or naive times.
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})", value):
            raise ValueError("Use an ISO timestamp with explicit timezone and at most millisecond precision")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid ISO timestamp") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("An explicit timezone is required")
        try:
            parsed = parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("Timestamp outside supported range") from None
        if not 1900 <= parsed.year <= 2299 or parsed == datetime(1970, 1, 1, tzinfo=timezone.utc):
            raise ValueError("Timestamp outside supported range or reserved epoch sentinel")
        if parsed.microsecond % 1000:
            raise ValueError("Use millisecond precision; finer timestamps would lose evidence")
        return parsed

    @model_validator(mode="after")
    def nonempty_interval(self):
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


class ImportPreflightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: Literal["title_licenses"]
    source_reference: str = Field(min_length=1, max_length=500)
    csv_text: str = Field(min_length=1, max_length=262144)

    @field_validator("source_reference")
    @classmethod
    def source_not_blank(cls, value):
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("A nonblank source reference without control characters is required")
        return value


def preflight_licenses(body: ImportPreflightBody) -> dict:
    raw = body.csv_text.encode("utf-8")
    if len(raw) > 262144:
        raise ValueError("CSV exceeds 256 KiB")
    reader = csv.reader(io.StringIO(body.csv_text.removeprefix("\ufeff"), newline=""), strict=True)
    required = set(LicenseRow.model_fields) - {"amendment_note"}
    allowed = set(LicenseRow.model_fields)
    normalized, issues, seen = [], [], set()
    count = 0
    try:
        header = next(reader, [])
        if len(header) != len(set(header)) or not required <= set(header) or not set(header) <= allowed:
            raise ValueError("CSV headers must contain the licence fields, optional amendment_note, and no duplicates or extra columns")
        for count, cells in enumerate(reader, 1):
            if count > 500:
                raise ValueError("CSV exceeds 500 data rows")
            if len(cells) != len(header):
                issues.append({"row": count, "field": "row", "message": "Column count does not match header"})
                continue
            try:
                row = LicenseRow.model_validate(dict(zip(header, cells)))
            except ValidationError as error:
                for detail in error.errors(include_input=False, include_context=False, include_url=False):
                    issues.append({"row": count, "field": ".".join(map(str, detail["loc"])) or "row", "message": detail["msg"]})
                continue
            key = (row.title_id, row.territory_code, row.license_id)
            if key in seen:
                issues.append({"row": count, "field": "license_id", "message": "Duplicate natural key within this file"})
            seen.add(key)
            normalized.append(row.model_dump(mode="json"))
    except csv.Error:
        raise ValueError("Malformed CSV") from None
    if count == 0:
        raise ValueError("CSV has no data rows")
    # Do not expose a partial set as an importable batch.
    rows = sorted(normalized, key=lambda row: (row["title_id"], row["territory_code"], row["license_id"])) if not issues else []
    payload = {"schema_version": 1, "table": body.table, "source_reference": body.source_reference, "rows": rows}
    return {
        **payload, "valid": not issues, "recorded": False, "row_count": count,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if not issues else None,
        "issues": issues,
        "limitations": ["No evidence was written or revision reserved", "Existing database keys and corrections were not checked", "Territory syntax is checked, not ISO membership or rights authenticity"],
    }
