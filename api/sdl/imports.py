"""Bounded, offline licence CSV preflight. No executor or writer is accepted."""

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sdl.retrieval import TABLE_KEYS


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


class EvidenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_id: str = Field(min_length=1, max_length=200)
    amendment_note: str = Field(default="", max_length=2000)

    @field_validator("*")
    @classmethod
    def bounded_text(cls, value):
        if isinstance(value, str) and (len(value) > 2000 or value != value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Text must be trimmed, bounded to 2000 characters and contain no control characters")
        return value

    @field_validator("valid_from", "valid_to", "issued_at", "expires_at", "approved_at", mode="before", check_fields=False)
    @classmethod
    def timestamp(cls, value):
        return LicenseRow.explicit_timestamp(value)

    @model_validator(mode="after")
    def interval(self):
        for start, end in [("valid_from", "valid_to"), ("issued_at", "expires_at")]:
            if hasattr(self, start) and getattr(self, end) <= getattr(self, start):
                raise ValueError(f"{end} must be later than {start}")
        return self


class ClearanceRow(EvidenceRow):
    clearance_id: str = Field(min_length=1, max_length=200)
    asset_ref: str = Field(min_length=1, max_length=500)
    clearance_kind: Literal["MUSIC_SYNC", "MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT"]
    territory_code: str = Field(pattern=r"^[A-Z]{2}$")
    valid_from: datetime
    valid_to: datetime
    status: Literal["ACTIVE", "EXPIRED", "REVOKED"]


class RatingRow(EvidenceRow):
    rating_id: str = Field(min_length=1, max_length=200)
    territory_code: str = Field(pattern=r"^[A-Z]{2}$")
    rating_code: str = Field(min_length=1, max_length=100)
    issued_at: datetime
    expires_at: datetime
    status: Literal["VALID", "EXPIRED", "WITHDRAWN"]


class DeliveryRow(EvidenceRow):
    delivery_id: str = Field(min_length=1, max_length=200)
    master_version: str = Field(min_length=1, max_length=200)
    approved_at: datetime | None
    captions_state: Literal["APPROVED", "PENDING", "ABSENT"]
    audio_description_state: Literal["APPROVED", "PENDING", "ABSENT"]

    @field_validator("approved_at", mode="wrap")
    @classmethod
    def nullable_time(cls, value, handler):
        return None if value == "" or value is None else handler(value)


class ContinuityRow(EvidenceRow):
    exception_id: str = Field(min_length=1, max_length=200)
    scene_ref: str = Field(min_length=1, max_length=500)
    severity: Literal["BLOCKING", "ADVISORY"]
    state: Literal["OPEN", "RESOLVED", "WAIVED"]
    resolution_ref: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def resolution(self):
        if self.state != "OPEN" and not self.resolution_ref:
            raise ValueError("Resolved or waived exceptions require a resolution_ref")
        return self


class SyntheticRow(EvidenceRow):
    record_id: str = Field(min_length=1, max_length=200)
    asset_ref: str = Field(min_length=1, max_length=500)
    generation_kind: Literal["SYNTHETIC", "ASSISTED", "NONE"]
    tool_ref: str = Field(max_length=500)
    disclosure_obligation_ref: str = Field(max_length=500)


class ConsentRow(EvidenceRow):
    consent_id: str = Field(min_length=1, max_length=200)
    performer_ref: str = Field(min_length=1, max_length=500)
    consent_scope: Literal["likeness", "voice", "both"]
    territory_code: str = Field(pattern=r"^[A-Z]{2}$")
    valid_from: datetime
    valid_to: datetime
    status: Literal["ACTIVE", "WITHDRAWN", "EXPIRED"]


IMPORT_MODELS = {
    "title_licenses": LicenseRow, "clearances": ClearanceRow, "ratings": RatingRow,
    "deliveries": DeliveryRow, "continuity_exceptions": ContinuityRow,
    "synthetic_content": SyntheticRow, "performer_consents": ConsentRow,
}


def natural_key(table, row):
    key, territorial = TABLE_KEYS[table]
    return (row["title_id"], row["territory_code"], row[key]) if territorial else (row["title_id"], row[key])


class ImportPreflightBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: Literal["title_licenses", "clearances", "ratings", "deliveries", "continuity_exceptions", "synthetic_content", "performer_consents"]
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
    model = IMPORT_MODELS[body.table]
    required = {name for name, field in model.model_fields.items() if field.is_required()}
    allowed = set(model.model_fields)
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
                row = model.model_validate(dict(zip(header, cells)))
            except ValidationError as error:
                for detail in error.errors(include_input=False, include_context=False, include_url=False):
                    issues.append({"row": count, "field": ".".join(map(str, detail["loc"])) or "row", "message": detail["msg"]})
                continue
            key = natural_key(body.table, row.model_dump())
            if key in seen:
                issues.append({"row": count, "field": "license_id", "message": "Duplicate natural key within this file"})
            seen.add(key)
            normalized.append(row.model_dump(mode="json"))
    except csv.Error:
        raise ValueError("Malformed CSV") from None
    if count == 0:
        raise ValueError("CSV has no data rows")
    # Do not expose a partial set as an importable batch.
    rows = sorted(normalized, key=lambda row: natural_key(body.table, row)) if not issues else []
    payload = {"schema_version": 1, "table": body.table, "source_reference": body.source_reference, "rows": rows}
    return {
        **payload, "valid": not issues, "recorded": False, "row_count": count,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if not issues else None,
        "issues": issues,
        "limitations": ["No evidence was written or revision reserved", "Existing database keys and corrections were not checked", "Territory syntax is checked, not ISO membership or rights authenticity"],
    }
