"""Deployment-scoped API access. This is not row-level tenant isolation."""

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request
from urllib.parse import urlsplit
from pathlib import Path
from sdl.workspace import WorkspaceStore

COOKIE = "__Host-sdl_session"


def _require(condition):
    if not condition:
        raise ValueError("Invalid access configuration")


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str


class WorkspaceAccess:
    def __init__(self):
        self.mode = os.getenv("SDL_ACCESS_MODE", "public")
        self.workspace = os.getenv("SDL_WORKSPACE_ID", "")
        self.credentials: list[tuple[str, Principal]] = []
        self.store = None
        self.origin = os.getenv("SDL_PUBLIC_ORIGIN", "")
        raw = os.getenv("SDL_ACCESS_KEYS", "")
        key_file = os.getenv("SDL_ACCESS_KEYS_FILE", "")
        if key_file:
            if raw:
                raise ValueError("Configure only one of SDL_ACCESS_KEYS or SDL_ACCESS_KEYS_FILE")
            raw = Path(key_file).read_text()
        if self.mode not in {"public", "private"}:
            raise ValueError("SDL_ACCESS_MODE must be public or private")
        if self.mode == "public":
            if raw or self.workspace or os.getenv("SDL_WORKSPACE_STATE_DIR") or self.origin:
                raise ValueError("Workspace credentials require SDL_ACCESS_MODE=private")
            return
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.workspace):
            raise ValueError("Private mode requires a valid SDL_WORKSPACE_ID")
        try:
            entries = json.loads(raw)
            _require(isinstance(entries, list) and 1 <= len(entries) <= 100)
            seen = set()
            for entry in entries:
                _require(isinstance(entry, dict) and set(entry) == {"sha256", "subject", "role"})
                digest, subject, role = entry["sha256"], entry["subject"], entry["role"]
                _require(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest))
                _require(isinstance(subject, str) and re.fullmatch(r"[a-zA-Z0-9_.@-]{1,100}", subject))
                _require(isinstance(role, str) and role in {"reader", "operator"} and digest not in seen)
                seen.add(digest)
                self.credentials.append((digest, Principal(subject, role)))
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid SDL_ACCESS_KEYS configuration") from None
        directory = os.getenv("SDL_WORKSPACE_STATE_DIR")
        if directory:
            parsed = urlsplit(self.origin)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError("Browser onboarding requires SDL_PUBLIC_ORIGIN as an HTTPS origin without a path")
            self.store = WorkspaceStore(directory, self.workspace)

    def principal_for(self, digest):
        principal = None
        for expected, candidate in self.credentials:
            if hmac.compare_digest(expected, digest or ""):
                principal = candidate
        return principal

    def require_origin(self, request):
        if request.headers.get("origin") != self.origin:
            raise HTTPException(403, "Same-origin request required")

    async def authorize(self, request: Request):
        if request.scope["route"].name in {"health", "workspace_config", "workspace_login"} or self.mode == "public":
            return
        headers = request.headers.getlist("authorization")
        parts = headers[0].split(" ") if len(headers) == 1 else []
        cookie = request.cookies.get(COOKIE)
        if not headers and cookie and self.store:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                self.require_origin(request)
            digest = self.store.credential(cookie)
        elif len(parts) == 2 and parts[0].lower() == "bearer" and 32 <= len(parts[1]) <= 512:
            digest = hashlib.sha256(parts[1].encode()).hexdigest()
        else:
            raise HTTPException(401, "Bearer credentials required", headers={"WWW-Authenticate": "Bearer"})
        principal = self.principal_for(digest)
        if principal is None:
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
        # The separately deployed agent has its own database and session store.
        # Do not imply that authenticating this proxy isolates that remote service.
        name = request.scope["route"].name
        if name == "agent_ask":
            raise HTTPException(503, "Agent access is disabled in private workspace mode")
        if name == "draft_memo_for" and self.store:
            raise HTTPException(503, "Model-generated memos are disabled for private studio evidence")
        readers = {
            "workspace_session", "get_catalogue", "get_decisions", "get_decision",
            "preview_evidence", "probe_decision_integrity", "verify_decision",
            "ablate_decision",
            "compare_decision", "get_resolution_plan", "recheck_resolution_plan",
            "workspace_logout", "import_templates", "import_history",
        }
        # Unknown/new routes require operator authority by default, including GETs.
        if principal.role != "operator" and name not in readers:
            raise HTTPException(403, "Operator permission required")
        request.state.principal = principal
