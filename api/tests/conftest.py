"""Test fixtures.

The executor indirection matters. Production resolves facts through the
ClickHouse MCP server — that is the graded integration and the decision flow
has no direct-driver bypass. Tests get the same SQL through a plain HTTPS
executor so the retrieval logic can be exercised quickly, with a separate
test proving the MCP path returns identical results for identical SQL.
"""

from __future__ import annotations

import json
import urllib.request
from base64 import b64encode
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


@pytest.fixture(scope="session")
def clickhouse_env() -> dict[str, str]:
    env = load_env()
    required = ("CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD")
    if not all(env.get(key) for key in required):
        pytest.skip("api/.env has no ClickHouse credentials")
    return env


@pytest.fixture(scope="session")
def http_executor(clickhouse_env):
    """Execute SQL and return rows as dicts."""

    host = clickhouse_env["CLICKHOUSE_HOST"]
    port = clickhouse_env.get("CLICKHOUSE_PORT", "8443")
    credentials = b64encode(
        f"{clickhouse_env['CLICKHOUSE_USER']}:{clickhouse_env['CLICKHOUSE_PASSWORD']}".encode()
    ).decode()

    def execute(sql: str) -> list[dict]:
        request = urllib.request.Request(
            f"https://{host}:{port}/",
            data=f"{sql} FORMAT JSONEachRow".encode("utf-8"),
            headers={"Authorization": f"Basic {credentials}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8").strip()
        return [json.loads(line) for line in body.splitlines() if line]

    return execute
