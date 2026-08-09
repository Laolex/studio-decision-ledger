#!/usr/bin/env python3
"""Apply a .sql file to ClickHouse over the HTTPS interface.

The HTTP interface takes one statement per request, so this splits the file on
statement boundaries and posts each in order, stopping at the first failure.

Reads connection settings from api/.env. Nothing here prints the password.

Usage:
    python3 db/apply.py db/schema.sql
    python3 db/apply.py db/seed.sql
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "api" / ".env"


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(f"missing {ENV_PATH} — copy api/.env.example and fill it in")
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    missing = [
        k
        for k in ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD")
        if not env.get(k)
    ]
    if missing:
        sys.exit(f"missing values in api/.env: {', '.join(missing)}")
    return env


def split_statements(sql: str) -> list[str]:
    """Split on statement-terminating semicolons.

    A semicolon terminates a statement only when it ends a line, which holds
    for everything we generate. Comment-only fragments are dropped.
    """
    statements: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(buffer).strip()
            if any(
                l.strip() and not l.strip().startswith("--")
                for l in statement.splitlines()
            ):
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail and any(l.strip() and not l.strip().startswith("--") for l in tail.splitlines()):
        statements.append(tail)
    return statements


def execute(env: dict[str, str], statement: str) -> str:
    url = f"https://{env['CLICKHOUSE_HOST']}:{env['CLICKHOUSE_PORT']}/"
    credentials = b64encode(
        f"{env['CLICKHOUSE_USER']}:{env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()
    request = urllib.request.Request(
        url,
        data=statement.encode("utf-8"),
        headers={"Authorization": f"Basic {credentials}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def summarize(statement: str) -> str:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped[:78]
    return "(empty)"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python3 db/apply.py <file.sql>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"no such file: {path}")

    env = load_env()
    statements = split_statements(path.read_text(encoding="utf-8"))
    print(f"{path}: {len(statements)} statement(s) -> {env['CLICKHOUSE_HOST']}")

    for index, statement in enumerate(statements, start=1):
        try:
            execute(env, statement)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace").strip()
            print(f"  [{index}] FAILED  {summarize(statement)}")
            sys.exit(f"\n{body[:1200]}")
        print(f"  [{index}] ok      {summarize(statement)}")

    print("done")


if __name__ == "__main__":
    main()
