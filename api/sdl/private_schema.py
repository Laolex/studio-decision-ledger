"""Fresh private-service schema. Never apply to an existing/public database."""
import re

from sdl.imports import IMPORT_MODELS


def statements(base_schema):
    yield "CREATE DATABASE IF NOT EXISTS sdl"
    yield "CREATE TABLE sdl.workspace_identity (workspace_id String) ENGINE=MergeTree ORDER BY workspace_id"
    # One row is one whole evidence batch. Identical retries coalesce at read time;
    # ordinary views (not materialized views) always read FINAL.
    yield "CREATE TABLE sdl.workspace_imports (import_id String, revision UInt64, payload String) ENGINE=ReplacingMergeTree ORDER BY import_id SETTINGS fsync_after_insert=1, fsync_part_directory=1"
    for statement in re.findall(r"CREATE TABLE IF NOT EXISTS sdl\.(\w+)\s*\([\s\S]*?;", base_schema):
        if statement in IMPORT_MODELS:
            continue
        ddl = re.search(r"CREATE TABLE IF NOT EXISTS sdl\." + statement + r"\s*\([\s\S]*?;", base_schema).group(0)
        if statement == "decision_records":
            ddl = ddl.replace("decision_id", "recorded_by String DEFAULT '',\n    decision_id", 1)
        yield ddl
    for table, model in IMPORT_MODELS.items():
        fields = []
        for name in model.model_fields:
            extract = f"JSONExtractString(item, '{name}')"
            if name in {"valid_from", "valid_to", "issued_at", "expires_at", "approved_at"}:
                extract = f"parseDateTime64BestEffort{'OrNull' if name == 'approved_at' else ''}({extract}, 3, 'UTC')"
            fields.append(f"{extract} AS {name}")
        fields += ["revision", "parseDateTime64BestEffort(JSONExtractString(payload, 'recorded_at'), 3, 'UTC') AS recorded_at"]
        yield f"CREATE VIEW sdl.{table} AS SELECT " + ", ".join(fields) + " FROM sdl.workspace_imports FINAL ARRAY JOIN JSONExtractArrayRaw(payload, 'rows') AS item WHERE JSONExtractString(payload, 'table') = '" + table + "'"
