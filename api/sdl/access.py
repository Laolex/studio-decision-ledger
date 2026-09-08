"""Deployment-scoped API access. This is not row-level tenant isolation."""

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request


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
        raw = os.getenv("SDL_ACCESS_KEYS", "")
        if self.mode not in {"public", "private"}:
            raise ValueError("SDL_ACCESS_MODE must be public or private")
        if self.mode == "public":
            if raw or self.workspace:
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

    async def authorize(self, request: Request):
        if request.scope["route"].name == "health" or self.mode == "public":
            return
        headers = request.headers.getlist("authorization")
        parts = headers[0].split(" ") if len(headers) == 1 else []
        if len(parts) != 2 or parts[0].lower() != "bearer" or not 32 <= len(parts[1]) <= 512:
            raise HTTPException(401, "Bearer credentials required", headers={"WWW-Authenticate": "Bearer"})
        digest = hashlib.sha256(parts[1].encode()).hexdigest()
        principal = None
        for expected, candidate in self.credentials:
            if hmac.compare_digest(expected, digest):
                principal = candidate
        if principal is None:
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
        # The separately deployed agent has its own database and session store.
        # Do not imply that authenticating this proxy isolates that remote service.
        name = request.scope["route"].name
        if name == "agent_ask":
            raise HTTPException(503, "Agent access is disabled in private workspace mode")
        readers = {
            "workspace_session", "get_catalogue", "get_decisions", "get_decision",
            "preview_evidence", "probe_decision_integrity", "verify_decision",
            "ablate_decision",
            "compare_decision", "get_resolution_plan", "recheck_resolution_plan",
        }
        # Unknown/new routes require operator authority by default, including GETs.
        if principal.role != "operator" and name not in readers:
            raise HTTPException(403, "Operator permission required")
        request.state.principal = principal
