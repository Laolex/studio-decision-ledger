"""Execute decision-relevant SQL through the ClickHouse MCP server.

This is the production retrieval path. Facts that determine an outcome are
read through MCP, and there is deliberately no direct-driver fallback here —
a silent bypass would make the integration claim false.

The MCP session is async and long-lived; callers of `Executor` are synchronous.
So the session runs on an event loop in a dedicated thread and queries are
submitted to it. Starting a subprocess per query would otherwise cost seconds
of server startup on every retrieval.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sdl.canonical import canonical_rows

SERVER_COMMAND = "mcp-clickhouse"
QUERY_TOOL = "run_query"


class MCPQueryError(RuntimeError):
    """The MCP server reported a failure for a query."""


def _rows_from_payload(payload: dict[str, Any]) -> list[dict]:
    """`run_query` answers with columns plus positional rows."""
    columns = payload.get("columns", [])
    return canonical_rows([dict(zip(columns, row)) for row in payload.get("rows", [])])


class ClickHouseMCPExecutor:
    """Context manager yielding a synchronous `(sql) -> list[dict]` callable."""

    def __init__(self, env: dict[str, str], server_command: str | None = None):
        self._env = {**os.environ, **env}
        self._command = server_command or self._resolve_command()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._ready = threading.Event()
        self._shutdown: asyncio.Future | None = None
        self._startup_error: BaseException | None = None

    @staticmethod
    def _resolve_command() -> str:
        """Prefer the server installed alongside this interpreter."""
        import shutil
        import sys
        from pathlib import Path

        candidate = Path(sys.executable).parent / SERVER_COMMAND
        if candidate.exists():
            return str(candidate)
        found = shutil.which(SERVER_COMMAND)
        if not found:
            raise FileNotFoundError(
                f"{SERVER_COMMAND} not on PATH — install mcp-clickhouse"
            )
        return found

    def __enter__(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=120):
            raise TimeoutError("ClickHouse MCP server did not become ready")
        if self._startup_error is not None:
            raise self._startup_error
        return self._execute

    def __exit__(self, exc_type, exc, tb):
        if self._loop is not None and self._shutdown is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set_result, None)
        if self._thread is not None:
            self._thread.join(timeout=30)
        return False

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as error:  # surfaced to __enter__
            self._startup_error = error
            self._ready.set()
        finally:
            loop.close()

    async def _serve(self) -> None:
        params = StdioServerParameters(command=self._command, args=[], env=self._env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self._shutdown = asyncio.get_running_loop().create_future()
                self._ready.set()
                await self._shutdown

    async def _call(self, sql: str) -> list[dict]:
        assert self._session is not None
        result = await self._session.call_tool(QUERY_TOOL, {"query": sql})
        if result.isError:
            detail = " ".join(getattr(c, "text", "") for c in result.content)
            raise MCPQueryError(detail.strip() or "unknown MCP error")
        text = next(
            (getattr(c, "text", None) for c in result.content if getattr(c, "text", None)),
            None,
        )
        if text is None:
            raise MCPQueryError("MCP response carried no text content")
        return _rows_from_payload(json.loads(text))

    def _execute(self, sql: str) -> list[dict]:
        if self._loop is None:
            raise RuntimeError("executor is not running")
        future: Future = asyncio.run_coroutine_threadsafe(self._call(sql), self._loop)
        return future.result(timeout=120)
