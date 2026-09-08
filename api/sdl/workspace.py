"""Durable single-host import coordination and browser sessions.

The lock and SQLite file must live together on a persistent, local filesystem.
ClickHouse remains the evidence/decision store and MCP remains the read path.
"""
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from sdl.record import canonical_json, _quote


class WorkspaceStore:
    def __init__(self, directory: str, workspace: str):
        self.directory = Path(directory)
        if not self.directory.is_absolute() or os.getenv("K_SERVICE"):
            raise ValueError("Workspace state requires an absolute persistent local directory, not Cloud Run")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "workspace.sqlite3"
        self.workspace = workspace
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS identity (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, credential TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (client TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS imports (
                    import_id TEXT PRIMARY KEY, revision INTEGER UNIQUE NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO identity VALUES (?)", (workspace,))
            if db.execute("SELECT name FROM identity").fetchall() != [(workspace,)]:
                raise ValueError("Workspace directory belongs to another workspace")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lock(self):
        with (self.directory / "publish.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, "An import is being published. Retry shortly.") from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def login_attempt(self, client):
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE at < ?", (now - 600,))
            count = db.execute("SELECT count(*) FROM login_attempts WHERE client = ?", (client,)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, "Too many sign-in attempts. Retry in ten minutes.")
            db.execute("INSERT INTO login_attempts VALUES (?, ?)", (client, now))

    def session(self, credential):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (self.digest(token), credential, time.time() + 28800))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def credential(self, token):
        with self.db() as db:
            row = db.execute("SELECT credential FROM sessions WHERE digest = ? AND expires > ?", (self.digest(token), time.time())).fetchone()
        return row[0] if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE digest = ?", (self.digest(token),))

    def receipts(self):
        with self.db() as db:
            rows = db.execute("SELECT status, payload FROM imports ORDER BY revision DESC LIMIT 100").fetchall()
        return [{**json.loads(payload), "status": status} for status, payload in rows]

    def head(self):
        with self.db() as db:
            return db.execute("SELECT coalesce(max(revision), 0) FROM imports WHERE status = 'published'").fetchone()[0]

    def check_identity(self, execute):
        rows = execute("SELECT workspace_id FROM sdl.workspace_identity")
        if rows != [{"workspace_id": self.workspace}]:
            raise HTTPException(503, "The evidence service does not belong to this workspace")

    def check_head(self, execute):
        self.check_identity(execute)
        head = int(execute("SELECT max(revision) AS revision FROM sdl.workspace_imports FINAL")[0]["revision"] or 0)
        if head != self.head():
            raise HTTPException(409, "Evidence and coordination state disagree. Reconcile the pending import before proceeding.")

    def review(self, result, execute):
        if not result["valid"]:
            return result
        with self.lock():
            self.check_head(execute)
            with self.db() as db:
                pending = db.execute("SELECT import_id FROM imports WHERE status = 'pending'").fetchone()
                if pending:
                    raise HTTPException(409, f"Import {pending[0]} is pending. Retry it from import history.")
                previous = db.execute("SELECT payload FROM imports WHERE status = 'published' ORDER BY revision").fetchall()
            from sdl.imports import natural_key
            existing = {}
            for (payload,) in previous:
                batch = json.loads(payload)
                if batch["table"] == result["table"]:
                    for row in batch["rows"]:
                        existing[natural_key(result["table"], row)] = row
            changes = [{"key": list(natural_key(result["table"], row)), "before": existing.get(natural_key(result["table"], row)), "after": row} for row in result["rows"]]
            corrections = sum(change["before"] is not None and change["before"] != change["after"] for change in changes)
            return {**result, "expected_head": self.head(), "changes": changes, "corrections": corrections}

    def publish(self, reviewed, expected_head, allow_corrections, actor, execute, write):
        with self.lock():
            import_id = "I-" + reviewed["content_sha256"]
            with self.db() as db:
                found = db.execute("SELECT status, payload FROM imports WHERE import_id = ?", (import_id,)).fetchone()
                pending = db.execute("SELECT import_id FROM imports WHERE status = 'pending'").fetchone()
            self.check_identity(execute)
            if found:
                if found[0] == "published":
                    return self._published(json.loads(found[1]), execute)
                return self._send(json.loads(found[1]), execute, write)
            if pending:
                raise HTTPException(409, f"Import {pending[0]} is pending. Retry it first.")
            self.check_head(execute)
            if expected_head != self.head():
                raise HTTPException(409, "Evidence changed since review. Review the file again.")
            # Rebuild conflict detection while holding the publication lock.
            from sdl.imports import natural_key
            existing = {}
            with self.db() as db:
                previous = db.execute("SELECT payload FROM imports ORDER BY revision").fetchall()
            for (payload,) in previous:
                batch = json.loads(payload)
                if batch["table"] == reviewed["table"]:
                    for row in batch["rows"]:
                        existing[natural_key(batch["table"], row)] = row
            changes = [row for row in reviewed["rows"] if natural_key(reviewed["table"], row) in existing and existing[natural_key(reviewed["table"], row)] != row]
            if changes and not allow_corrections:
                raise HTTPException(409, "This file corrects existing evidence. Review and explicitly approve corrections.")
            if any(not row.get("amendment_note") for row in changes):
                raise HTTPException(422, "Every correction requires an amendment_note")
            payload = {key: reviewed[key] for key in ("table", "source_reference", "rows", "content_sha256", "input_sha256")}
            payload.update(import_id=import_id, revision=expected_head + 1, actor=actor, recorded_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), workspace_id=self.workspace)
            with self.db() as db:
                db.execute("INSERT INTO imports VALUES (?, ?, 'pending', ?)", (import_id, payload["revision"], canonical_json(payload)))
            return self._send(payload, execute, write)

    def retry(self, import_id, execute, write):
        with self.lock():
            self.check_identity(execute)
            with self.db() as db:
                row = db.execute("SELECT status, payload FROM imports WHERE import_id = ?", (import_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Import not found")
            if row[0] == "published":
                return self._published(json.loads(row[1]), execute)
            return self._send(json.loads(row[1]), execute, write)

    def _published(self, payload, execute):
        self.check_head(execute)
        found = execute(f"SELECT payload FROM sdl.workspace_imports FINAL WHERE import_id = {_quote(payload['import_id'])}")
        if found != [{"payload": canonical_json(payload)}]:
            raise HTTPException(503, "Published import is missing or differs from its receipt. Administrator reconciliation is required.")
        return {**payload, "status": "published"}

    def _send(self, payload, execute, write):
        head = int(execute("SELECT max(revision) AS revision FROM sdl.workspace_imports FINAL")[0]["revision"] or 0)
        if head not in {self.head(), payload["revision"]}:
            raise HTTPException(409, "Pending import cannot be replayed across a divergent evidence head. Administrator reconciliation is required.")
        query = f"SELECT payload FROM sdl.workspace_imports FINAL WHERE import_id = {_quote(payload['import_id'])}"
        expected = canonical_json(payload)
        try:
            found = execute(query)
            if not found:
                event = {"import_id": payload["import_id"], "revision": payload["revision"], "payload": expected}
                write("INSERT INTO sdl.workspace_imports SETTINGS async_insert=0 FORMAT JSONEachRow\n" + canonical_json(event))
                found = execute(query)
            if found != [{"payload": expected}]:
                raise RuntimeError("Published payload did not match")
        except Exception as error:
            raise HTTPException(503, f"Import {payload['import_id']} remains pending. Retry this import; do not submit a replacement.") from error
        with self.db() as db:
            db.execute("UPDATE imports SET status = 'published' WHERE import_id = ?", (payload["import_id"],))
        return {**payload, "status": "published"}
