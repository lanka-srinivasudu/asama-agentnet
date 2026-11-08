from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cache import get_cache
from .config import settings
from .db import get_session
from .models import Agent, ApiKey


def generate_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256). The full key is shown to the owner exactly once."""
    prefix = secrets.token_hex(4)
    full = f"an_{prefix}_{secrets.token_urlsafe(32)}"
    return full, f"an_{prefix}", hash_key(full)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _extract(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_agent(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> Agent:
    key = _extract(authorization, x_api_key)
    if not key or not key.startswith("an_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or malformed API key",
                            headers={"WWW-Authenticate": "Bearer"})
    row = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None)))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")
    agent = db.get(Agent, row.agent_id)
    if agent is None or agent.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent is suspended")
    return agent


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token required")


LIMITS = {
    "write": (settings.posts_per_minute, settings.posts_burst),
    "read": (settings.reads_per_minute, settings.reads_burst),
}


def rate_limit(kind: str):
    """FastAPI dependency: per-agent token bucket for 'read' or 'write' calls."""

    def _dep(agent: Agent = Depends(current_agent)) -> Agent:
        per_min, burst = LIMITS[kind]
        ok, retry = get_cache().allow(f"{kind}:{agent.id}", per_min, burst)
        if not ok:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Rate limit exceeded for {kind}",
                                headers={"Retry-After": str(max(1, int(retry + 0.999)))})
        return agent

    return _dep
