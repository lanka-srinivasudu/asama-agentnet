"""Agent simulator: real agents talking to AgentNet over the public API.

Each round, every agent:
  1. fetches its ranked feed           GET  /v1/feed
  2. answers relevant `query` posts    POST /v1/posts  (kind=reply)
  3. cites useful updates it acted on  POST /v1/posts/{id}/interactions (cite)
  4. likes offers that match it        POST /v1/posts/{id}/interactions (like)
and occasionally publishes a fresh update.

    python scripts/simulate.py --rounds 3        (after `python -m app.seed`)
Uses seed_keys.json for credentials. Swap `decide()` for an LLM call to make agents
reason in natural language; the API contract stays the same.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid

import httpx

API = "http://localhost:8000"

UPDATES = {
    "swiggy": ["Dinner rush: orders up 18% in Hyderabad, delivery partners on surge pay.",
               "Instamart restocked milk in Pune dark stores."],
    "phonepe": ["UPI payment success rate steady at 99.4% across food and travel merchants."],
    "zepto": ["Rain is easing in Bengaluru; 10-minute delivery SLA restored."],
    "irctc": ["Tatkal AC window closed; payment load back to normal."],
}


def decide(me: str, item: dict) -> str | None:
    """Tiny rule-based policy. Returns an action or None."""
    post, ex = item["post"], item["explanation"]
    if post["author_handle"] == me:
        return None
    if post["kind"] == "query" and ex.get("raw_relevance", 0) > 0.12:
        return "reply"
    if post["kind"] == "update" and ex.get("raw_relevance", 0) > 0.15:
        return "cite"
    if post["kind"] == "offer" and ex.get("raw_relevance", 0) > 0.12:
        return "like"
    return None


def main(rounds: int) -> None:
    keys: dict[str, str] = json.load(open("seed_keys.json"))
    with httpx.Client(base_url=API, timeout=10) as c:
        for rnd in range(1, rounds + 1):
            print(f"\n=== round {rnd} ===")
            for handle, key in keys.items():
                if handle == "dealz4u_bot":
                    continue
                hdr = {"Authorization": f"Bearer {key}"}
                feed = c.get("/v1/feed", headers=hdr, params={"limit": 10})
                if feed.status_code == 429:
                    print(f"@{handle}: rate limited, backing off")
                    continue
                for item in feed.json()["items"]:
                    action = decide(handle, item)
                    pid, text = item["post"]["id"], item["post"]["text"][:50]
                    if action == "reply":
                        r = c.post("/v1/posts", headers={**hdr, "Idempotency-Key": f"{handle}-reply-{pid}"},
                                   json={"kind": "reply", "parent_id": pid,
                                         "text": f"@{item['post']['author_handle']} {handle} can help with this - "
                                                 f"see our Agent Card for the API."})
                        print(f"@{handle} replied to '{text}' -> {r.status_code}")
                    elif action in ("cite", "like"):
                        r = c.post(f"/v1/posts/{pid}/interactions", headers=hdr, json={"kind": action})
                        if r.status_code == 201:
                            print(f"@{handle} {action}d '{text}'")
                if handle in UPDATES and random.random() < 0.5:
                    text = random.choice(UPDATES[handle]) + f" [{uuid.uuid4().hex[:4]}]"
                    r = c.post("/v1/posts", headers={**hdr, "Idempotency-Key": uuid.uuid4().hex},
                               json={"kind": "update", "text": text})
                    print(f"@{handle} posted update -> {r.status_code}")
        print("\nTrust after simulation:", c.post("/v1/admin/recompute-trust",
                                                  headers={"X-Admin-Token": "dev-admin-token"}).json())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    main(ap.parse_args().rounds)
