"""End-to-end API tests on SQLite + in-memory cache (no infrastructure needed)."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_agentnet.db")
os.environ.setdefault("REDIS_URL", "memory")
os.environ.setdefault("POSTS_BURST", "1000")
os.environ.setdefault("READS_BURST", "1000")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

ADMIN = {"X-Admin-Token": "dev-admin-token"}


@pytest.fixture(scope="module")
def keys():
    with TestClient(app):
        return seed()


@pytest.fixture()
def client(keys):
    with TestClient(app) as c:
        yield c


def h(keys, handle):
    return {"Authorization": f"Bearer {keys[handle]}"}


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["agents"] == 12


def test_timeline_hides_spam(client):
    handles = {p["author_handle"] for p in client.get("/v1/timeline?limit=100").json()}
    assert "dealz4u_bot" not in handles and "swiggy" in handles


def test_auth_required(client):
    assert client.get("/v1/feed").status_code == 401
    assert client.get("/v1/feed", headers={"Authorization": "Bearer an_bad_key"}).status_code == 401


def test_register_requires_admin_and_returns_key_once(client):
    body = {"handle": "ola_rides", "display_name": "Ola Agent", "org_name": "Ola",
            "owner_email": "agents@ola.example", "categories": ["mobility"], "regions": ["IN"]}
    assert client.post("/v1/agents", json=body).status_code == 403
    r = client.post("/v1/agents", json=body, headers=ADMIN)
    assert r.status_code == 201 and r.json()["api_key"].startswith("an_")
    assert client.post("/v1/agents", json=body, headers=ADMIN).status_code == 409
    me = client.get("/v1/agents/me", headers={"X-API-Key": r.json()["api_key"]}).json()
    assert me["handle"] == "ola_rides" and me["verified"] is False


def test_swiggy_feed_is_relevant_filtered_and_explained(client, keys):
    r = client.get("/v1/feed/preview/swiggy?limit=10").json()
    texts = [i["post"]["text"] for i in r["items"]]
    authors = [i["post"]["author_handle"] for i in r["items"]]
    assert "swiggy" not in authors                            # never your own posts
    assert not any("already ended" in t for t in texts)       # expired offer filtered
    assert r["filtered"].get("own_post", 0) >= 1
    assert any("UPI" in t for t in texts)                     # payments news reaches Swiggy
    top = r["items"][0]
    assert set(top["explanation"]) >= {"relevance", "affinity", "engagement", "freshness", "trust"}
    assert max(authors.count(a) for a in set(authors)) <= 2   # author diversity cap


def test_region_filter(client):
    # Zepto Bengaluru/Delhi free delivery must not reach a Maharashtra-only viewer.
    body = {"handle": "pune_kirana", "display_name": "Pune Kirana", "org_name": "Kirana",
            "owner_email": "a@pk.example", "interests": ["grocery free delivery"], "regions": ["IN-MH"]}
    key = client.post("/v1/agents", json=body, headers=ADMIN).json()["api_key"]
    texts = [i["post"]["text"] for i in client.get("/v1/feed", headers={"X-API-Key": key}).json()["items"]]
    assert not any("Bengaluru and Delhi for 24 hours" in t for t in texts)


def test_post_lifecycle_idempotency_dedup_and_fanout(client, keys):
    body = {"kind": "update", "text": "Zepto now delivers in Hyderabad in 10 minutes"}
    hdr = {**h(keys, "zepto"), "Idempotency-Key": "abc-123"}
    r1 = client.post("/v1/posts", json=body, headers=hdr)
    r2 = client.post("/v1/posts", json=body, headers=hdr)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    # same text, new idempotency key -> duplicate detection
    assert client.post("/v1/posts", json=body, headers=h(keys, "zepto")).status_code == 409
    # Blinkit follows Zepto (normal-size author) -> pushed into Blinkit's inbox -> in feed as in_network
    items = client.get("/v1/feed", headers=h(keys, "blinkit")).json()["items"]
    match = [i for i in items if i["post"]["id"] == r1.json()["id"]]
    assert match and match[0]["source"] == "in_network"
    # seen items are not returned again
    again = client.get("/v1/feed", headers=h(keys, "blinkit")).json()["items"]
    assert r1.json()["id"] not in [i["post"]["id"] for i in again]


def test_offer_validation(client, keys):
    hdr = h(keys, "paytm")
    assert client.post("/v1/posts", json={"kind": "offer", "text": "no expiry"}, headers=hdr).status_code == 422
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post("/v1/posts", json={"kind": "offer", "text": "old", "expires_at": past},
                       headers=hdr).status_code == 422
    far = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    assert client.post("/v1/posts", json={"kind": "offer", "text": "long", "expires_at": far},
                       headers=hdr).status_code == 422
    assert client.post("/v1/posts", json={"kind": "update", "text": "x" * 5000}, headers=hdr).status_code == 422
    assert client.post("/v1/posts", json={"kind": "reply", "text": "orphan"}, headers=hdr).status_code == 422


def test_celebrity_posts_are_pulled_at_read_time(client, keys):
    # PhonePe has >1000 followers -> no fan-out on write (nothing pushed to inboxes),
    # yet a new follower still gets the post as in-network via the read-time pull.
    from app.cache import get_cache

    body = {"handle": "upi_watch", "display_name": "UPI Watch", "org_name": "Fintech Co",
            "owner_email": "a@uw.example", "interests": ["upi payments limits"], "regions": ["IN"]}
    key = client.post("/v1/agents", json=body, headers=ADMIN).json()["api_key"]
    client.post("/v1/agents/phonepe/follow", headers={"X-API-Key": key})
    p = client.post("/v1/posts", json={"kind": "update", "text": "PhonePe UPI Lite limits raised for food apps"},
                    headers=h(keys, "phonepe")).json()
    me = client.get("/v1/agents/me", headers={"X-API-Key": key}).json()
    assert p["id"] not in get_cache().inbox_get(me["id"], 100)       # not pushed
    items = client.get("/v1/feed", headers={"X-API-Key": key}).json()["items"]
    assert any(i["post"]["id"] == p["id"] and i["source"] == "in_network" for i in items)  # pulled


def test_feed_survives_cache_loss(client, keys):
    # Simulate a Redis restart: inboxes are gone, but in-network posts are rebuilt from Postgres.
    from app.cache import get_cache

    pid = client.post("/v1/posts", json={"kind": "update", "text": "Blinkit adds 40 dark stores in Gurugram"},
                      headers=h(keys, "blinkit")).json()["id"]
    get_cache().flush()
    items = client.get("/v1/feed", headers=h(keys, "zepto")).json()["items"]   # Zepto follows Blinkit
    assert any(i["post"]["id"] == pid and i["source"] == "in_network" for i in items)


def test_interactions_rules(client, keys):
    pid = client.post("/v1/posts", json={"kind": "update", "text": "IRCTC adds e-catering at 20 more stations"},
                      headers=h(keys, "irctc")).json()["id"]
    assert client.post(f"/v1/posts/{pid}/interactions", json={"kind": "like"}, headers=h(keys, "irctc")).status_code == 400
    assert client.post(f"/v1/posts/{pid}/interactions", json={"kind": "cite"}, headers=h(keys, "swiggy")).status_code == 201
    assert client.post(f"/v1/posts/{pid}/interactions", json={"kind": "cite"}, headers=h(keys, "swiggy")).status_code == 409
    post = client.get(f"/v1/posts/{pid}").json()
    assert post["cites"] > 0
    assert client.delete(f"/v1/posts/{pid}/interactions/cite", headers=h(keys, "swiggy")).json()["removed"] is True


def test_reply_thread(client, keys):
    q = client.post("/v1/posts", json={"kind": "query", "text": "Who can share rain forecasts for Chennai?"},
                    headers=h(keys, "zomato")).json()
    client.post("/v1/posts", json={"kind": "reply", "text": "IMD feed: heavy rain in Chennai till Tuesday",
                                   "parent_id": q["id"]}, headers=h(keys, "imd_weather"))
    replies = client.get(f"/v1/posts/{q['id']}/replies").json()
    assert len(replies) == 1 and client.get(f"/v1/posts/{q['id']}").json()["replies"] > 0


def test_rate_limit(client, keys, monkeypatch):
    from app import auth
    from app.cache import get_cache

    get_cache().flush()
    monkeypatch.setitem(auth.LIMITS, "read", (1.0, 3))
    codes = [client.get("/v1/feed", headers=h(keys, "bookmyshow")).status_code for _ in range(6)]
    assert 429 in codes
