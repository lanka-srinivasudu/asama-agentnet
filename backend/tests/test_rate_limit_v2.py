"""Rate limiter v2: remaining-budget headers and trust tiers."""
import pytest
from sqlalchemy import select

from app import auth as auth_mod
from app.cache import MemoryCache, get_cache
from app.db import SessionLocal
from app.models import Agent
from .conftest import H, auth


@pytest.fixture()
def tiers(keys):
    """Pin trust so the tiers are deterministic: BookMyShow standard (0.5), PhonePe trusted (1.0)."""
    with SessionLocal() as db:
        for short, t in (("bms", 0.5), ("phonepe", 1.0)):
            db.scalar(select(Agent).where(Agent.handle == H[short])).trust = t
        db.commit()
    get_cache().flush()


def test_allow_returns_remaining():
    c = MemoryCache(10)
    ok, retry, remaining = c.allow("k", per_minute=60, burst=3)
    assert ok is True and retry == 0 and remaining == 2
    c.allow("k", 60, 3)
    ok, retry, remaining = c.allow("k", 60, 3)
    assert ok is True and remaining == 0
    ok, retry, remaining = c.allow("k", 60, 3)
    assert ok is False and remaining == 0 and 0 < retry <= 1.0


def test_trust_tiers(monkeypatch):
    monkeypatch.setitem(auth_mod.LIMITS, "read", (60.0, 10))
    assert auth_mod.limits_for("read", 1.0) == (120.0, 20)
    assert auth_mod.limits_for("read", 0.75) == (120.0, 20)
    assert auth_mod.limits_for("read", 0.5) == (60.0, 10)
    assert auth_mod.limits_for("read", 0.1) == (30.0, 5)
    monkeypatch.setitem(auth_mod.LIMITS, "read", (1.0, 1))
    assert auth_mod.limits_for("read", 0.0)[1] == 1


def test_success_responses_carry_rate_limit_headers(client, keys, tiers, monkeypatch):
    monkeypatch.setitem(auth_mod.LIMITS, "read", (60.0, 5))
    r1 = client.get("/v1/feed", headers=auth(keys, "bms"))
    r2 = client.get("/v1/feed", headers=auth(keys, "bms"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.headers["X-RateLimit-Limit"] == "5"
    assert r1.headers["X-RateLimit-Remaining"] == "4"
    assert r2.headers["X-RateLimit-Remaining"] == "3"


def test_429_carries_retry_after_and_zero_remaining(client, keys, tiers, monkeypatch):
    monkeypatch.setitem(auth_mod.LIMITS, "read", (1.0, 2))
    codes = [client.get("/v1/feed", headers=auth(keys, "bms")) for _ in range(3)]
    assert [r.status_code for r in codes] == [200, 200, 429]
    blocked = codes[-1]
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


def test_trusted_agent_gets_bigger_bucket(client, keys, tiers, monkeypatch):
    monkeypatch.setitem(auth_mod.LIMITS, "read", (60.0, 5))
    trusted = client.get("/v1/feed", headers=auth(keys, "phonepe"))
    assert trusted.headers["X-RateLimit-Limit"] == "10"
