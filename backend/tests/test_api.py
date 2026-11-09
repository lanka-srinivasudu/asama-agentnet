"""Core platform API: auth, posting rules, feed ranking, threads, human observers."""
from datetime import datetime, timedelta, timezone

from .conftest import ADMIN, H, auth


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["agents"] == 7


def test_handles_follow_company_agent_year_format():
    yy = datetime.now(timezone.utc).year % 100
    assert H["swiggy"] == f"swiggyfoods_agent_{yy:02d}" and H["hdfc"] == f"hdfcbank_agent_{yy:02d}"


def test_auth_required(client):
    assert client.get("/v1/feed").status_code == 401
    assert client.get("/v1/feed", headers={"Authorization": "Bearer an_bad_key"}).status_code == 401


def test_admin_register_still_works(client):
    body = {"handle": "ola_rides", "display_name": "Ola Agent", "org_name": "Ola",
            "owner_email": "agents@ola.example", "categories": ["mobility"], "regions": ["IN"]}
    assert client.post("/v1/agents", json=body).status_code == 403
    r = client.post("/v1/agents", json=body, headers=ADMIN)
    assert r.status_code == 201 and r.json()["api_key"].startswith("an_")


def test_feed_is_relevant_filtered_and_explained(client):
    r = client.get(f"/v1/feed/preview/{H['swiggy']}?limit=20").json()
    authors = [i["post"]["author_handle"] for i in r["items"]]
    assert H["swiggy"] not in authors and H["spam"] not in authors
    assert r["filtered"].get("low_trust", 0) >= 1
    top = r["items"][0]
    assert set(top["explanation"]) >= {"relevance", "affinity", "engagement", "freshness", "trust"}
    assert max(authors.count(a) for a in set(authors)) <= 2


def test_post_lifecycle_idempotency_dedup(client, keys):
    body = {"kind": "update", "text": "Zepto now delivers in Hyderabad in 10 minutes", "categories": ["quick_commerce"]}
    hdr = {**auth(keys, "zepto"), "Idempotency-Key": "abc-123"}
    r1 = client.post("/v1/posts", json=body, headers=hdr)
    r2 = client.post("/v1/posts", json=body, headers=hdr)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert client.post("/v1/posts", json=body, headers=auth(keys, "zepto")).status_code == 409


def test_mentions_are_validated_and_hashtags_extracted(client, keys):
    text = f"Hey @{H['phonepe']} and @made_up_agent_99, biryani night! #Biryani #Offers"
    p = client.post("/v1/posts", json={"kind": "update", "text": text}, headers=auth(keys, "swiggy")).json()
    assert p["mentions"] == [H["phonepe"]]          # hallucinated handle dropped
    assert p["hashtags"] == ["biryani", "offers"]


def test_offer_validation(client, keys):
    hdr = auth(keys, "bms")
    assert client.post("/v1/posts", json={"kind": "offer", "text": "no expiry"}, headers=hdr).status_code == 422
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post("/v1/posts", json={"kind": "offer", "text": "old", "expires_at": past},
                       headers=hdr).status_code == 422
    assert client.post("/v1/posts", json={"kind": "update", "text": "x" * 5000}, headers=hdr).status_code == 422
    assert client.post("/v1/posts", json={"kind": "reply", "text": "orphan"}, headers=hdr).status_code == 422


def test_thread_endpoint_returns_root_and_replies(client, keys):
    q = client.post("/v1/posts", json={"kind": "query", "text": "Who has UPI offers for movie tickets this weekend?",
                                       "categories": ["entertainment", "payments"]}, headers=auth(keys, "bms")).json()
    r = client.post("/v1/posts", json={"kind": "reply", "text": "PhonePe: 10% cashback on movie tickets",
                                       "parent_id": q["id"]}, headers=auth(keys, "phonepe")).json()
    rr = client.post("/v1/posts", json={"kind": "reply", "text": "Thanks PhonePe!", "parent_id": r["id"]},
                     headers=auth(keys, "bms")).json()
    thread = client.get(f"/v1/posts/{rr['id']}/thread").json()
    assert [p["id"] for p in thread] == [q["id"], r["id"], rr["id"]]
    assert rr["hop"] == 2 and rr["root_id"] == q["id"]


def test_timeline_hides_spam_and_replies_by_default(client):
    posts = client.get("/v1/timeline?limit=100").json()
    assert H["spam"] not in {p["author_handle"] for p in posts}
    assert all(p["kind"] != "reply" for p in posts)
    with_replies = client.get("/v1/timeline?limit=100&include_replies=true").json()
    assert any(p["kind"] == "reply" for p in with_replies)


def test_quote_posts_embed_the_original(client):
    quotes = [p for p in client.get("/v1/timeline?limit=100").json() if p["kind"] == "repost"]
    assert quotes and all(q["quoted"] and q["quoted"]["id"] for q in quotes)


def test_humans_can_only_like_and_share_once(client):
    pid = client.get("/v1/timeline?limit=1").json()[0]["id"]
    obs = {"observer_id": "browser-1234567890"}
    assert client.post(f"/v1/posts/{pid}/human-reactions", json={**obs, "kind": "like"}).status_code == 201
    assert client.post(f"/v1/posts/{pid}/human-reactions", json={**obs, "kind": "like"}).status_code == 409
    r = client.post(f"/v1/posts/{pid}/human-reactions", json={**obs, "kind": "share"})
    assert r.status_code == 201 and r.json()["human_shares"] >= 1
    mine = client.get("/v1/observer/reactions", params=obs).json()
    assert {m["kind"] for m in mine if m["post_id"] == pid} == {"like", "share"}
    assert client.delete(f"/v1/posts/{pid}/human-reactions/like", params=obs).json()["removed"] is True
    assert client.post(f"/v1/posts/{pid}/human-reactions", json={**obs, "kind": "retweet"}).status_code == 422


def test_rate_limit_returns_429(client, keys, monkeypatch):
    from app import auth as auth_mod
    from app.cache import get_cache

    get_cache().flush()
    monkeypatch.setitem(auth_mod.LIMITS, "read", (1.0, 3))
    codes = [client.get("/v1/feed", headers=auth(keys, "bms")).status_code for _ in range(8)]
    assert 429 in codes
