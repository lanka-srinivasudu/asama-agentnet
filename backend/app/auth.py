from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, Response, status
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


def limits_for(kind: str, trust: float) -> tuple[float, int]:
    """Trust tiers: verified, well-endorsed agents get more room; new or reported ones get less.

    trust >= 0.75 -> 2x (trusted) · trust < 0.30 -> 0.5x (probation) · otherwise 1x (standard)
    """
    per_min, burst = LIMITS[kind]
    factor = 2.0 if trust >= 0.75 else 0.5 if trust < 0.30 else 1.0
    return per_min * factor, max(1, int(burst * factor))


def rate_limit(kind: str):
    """FastAPI dependency: per-agent token bucket for 'read' or 'write' calls.

    Every response tells the agent its budget (X-RateLimit-Limit / -Remaining), and a 429
    says when to come back (Retry-After), so well-behaved agents can pace themselves.
    """

    def _dep(response: Response, agent: Agent = Depends(current_agent)) -> Agent:
        per_min, burst = limits_for(kind, agent.trust)
        ok, retry, remaining = get_cache().allow(f"{kind}:{agent.id}", per_min, burst)
        if not ok:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Rate limit exceeded for {kind}",
                                headers={"Retry-After": str(max(1, int(retry + 0.999))),
                                         "X-RateLimit-Limit": str(burst), "X-RateLimit-Remaining": "0"})
        response.headers["X-RateLimit-Limit"] = str(burst)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return agent

    return _dep
