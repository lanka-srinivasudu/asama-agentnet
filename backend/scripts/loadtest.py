"""AgentNet load test: how far does one deployment go before it bends?

    python -m scripts.loadtest populate --agents 10000      # add synthetic agents (keeps existing ones)
    python -m scripts.loadtest notify                       # relevance-engine cost per post at the current size
    python -m scripts.loadtest write  --seconds 20 --concurrency 8    # POST /v1/posts through HTTP (API must be running)
    python -m scripts.loadtest read   --seconds 20 --concurrency 32   # GET /v1/timeline + feed preview
    python -m scripts.loadtest worker --max 400                       # agent runs/sec (demo brain) on the queue
    python -m scripts.loadtest sse    --clients 1000                  # live connections: fan-out latency

Run against the production stack (PostgreSQL + pgvector + Redis). For the write test, start the API with
POSTS_BURST=100000 POSTS_PER_MINUTE=1000000 AGENT_WORKER=off so we measure the system, not the per-agent limiter.
Every command prints one JSON line so results can be pasted into the design doc.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import string
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, insert, select

from app.db import SessionLocal, init_db
from app.models import Agent, Notification, Post
from app.ranking.embeddings import get_embedder
from app.taxonomy import REGIONS, TOPICS

API = os.getenv("API", "http://127.0.0.1:8000")
RNG = random.Random(42)
TOPIC_IDS = list(TOPICS)
REGION_IDS = [r for r in REGIONS if r != "IN"]
WORDS = ["biryani", "pizza", "upi", "cashback", "train", "tatkal", "flight", "concert", "cricket", "movie",
         "grocery", "delivery", "ride", "auto", "loan", "insurance", "recharge", "rain", "sale", "exam",
         "pharmacy", "fashion", "phone", "laptop", "stadium", "airport", "metro", "festival", "gold", "rent"]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(p / 100 * len(s)))], 2)


def emit(name: str, **data) -> None:
    print(json.dumps({"test": name, **data}), flush=True)


# ------------------------------------------------------------------ populate

def populate(target: int) -> None:
    init_db()
    emb = get_embedder()
    with SessionLocal() as db:
        have = db.scalar(select(func.count()).select_from(Agent)) or 0
    t0 = time.perf_counter()
    batch: list[dict] = []
    for i in range(have, target):
        industry = RNG.choice(TOPIC_IDS)
        cats = list({industry, RNG.choice(TOPIC_IDS)})
        interests = RNG.sample(TOPIC_IDS, 4)
        keywords = RNG.sample(WORDS, 2)
        regions = ["IN"] if RNG.random() < 0.5 else RNG.sample(REGION_IDS, 2)
        name = f"Synth{i:06d}"
        text = " ".join([TOPICS[t] for t in interests + cats] + keywords)
        batch.append(dict(
            id=uuid.uuid4().hex, handle=f"synth{i:06d}_agent_26",
            company_name=name, display_name=name, org_name=name, owner_email=f"ops@{name.lower()}.example",
            description="synthetic load-test agent", verified=False, agent_card={}, industry=industry,
            categories=cats, interests=interests, keywords=keywords, regions=regions,
            interest_embedding=emb.embed(text).tolist(), trust=round(RNG.uniform(0.2, 0.9), 2),
            follower_count=0, status="active", llm_provider="demo", system_prompt="",
            reactivity=RNG.choice(["low", "medium", "high"]), max_posts_per_day=20, daily_token_budget=60000,
            proactive_every_minutes=0, consecutive_errors=0,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
        if len(batch) >= 1000:
            _flush(batch)
    _flush(batch)
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Agent))
    emit("populate", agents=total, added=max(0, target - have), seconds=round(time.perf_counter() - t0, 1))


def _flush(batch: list[dict]) -> None:
    if not batch:
        return
    with SessionLocal() as db:
        db.execute(insert(Agent), batch)
        db.commit()
    batch.clear()


# ------------------------------------------------------------------ relevance engine cost

def sample_post(author: Agent) -> Post:
    cats = RNG.sample(TOPIC_IDS, 2)
    text = f"{RNG.choice(WORDS)} offer today: {RNG.choice(WORDS)} and {RNG.choice(WORDS)} #deal"
    return Post(id="x" * 32, author_id=author.id, kind="offer", text=text, categories=cats, mentions=[],
                hashtags=[], regions=[], hop=0, embedding=get_embedder().embed(text + " " + " ".join(cats)).tolist())


def notify_bench(samples: int, recall_samples: int) -> None:
    """Relevance-engine cost per post, and recall@K of the fast path against scoring every agent."""
    from app.services import notify
    init_db()
    with SessionLocal() as db:
        n_agents = db.scalar(select(func.count()).select_from(Agent))
        author = db.scalar(select(Agent).order_by(Agent.trust.desc()).limit(1))
        fast_ms, full_ms, chosen, scored, hits, total = [], [], [], [], 0, 0
        for i in range(samples):
            post = sample_post(author)
            t0 = time.perf_counter()
            fast = notify.notification_targets(db, post, author)
            fast_ms.append((time.perf_counter() - t0) * 1000)
            chosen.append(len(fast))
            scored.append(notify.LAST.scored)
            if i < recall_samples:
                t0 = time.perf_counter()
                full = notify.notification_targets_exhaustive(db, post, author)
                full_ms.append((time.perf_counter() - t0) * 1000)
                want = {t.agent.id for t in full}
                hits += len(want & {t.agent.id for t in fast})
                total += len(want)
            db.rollback()
    emit("notify", agents=n_agents, samples=samples, fast_p50_ms=pct(fast_ms, 50), fast_p95_ms=pct(fast_ms, 95),
         exhaustive_p50_ms=pct(full_ms, 50), speedup=round(pct(full_ms, 50) / max(0.01, pct(fast_ms, 50)), 1),
         avg_agents_scored=round(statistics.mean(scored)), avg_targets=round(statistics.mean(chosen), 2),
         recall_at_k=round(hits / total, 3) if total else None,
         posts_per_sec_one_core=round(1000 / pct(fast_ms, 50), 1))


# ------------------------------------------------------------------ HTTP load

async def _http_loop(fn, seconds: float, concurrency: int):
    import httpx
    lat: list[float] = []
    codes: dict[int, int] = {}
    stop = time.perf_counter() + seconds
    async with httpx.AsyncClient(base_url=API, timeout=30) as client:
        async def worker(wid: int):
            i = 0
            while time.perf_counter() < stop:
                t0 = time.perf_counter()
                try:
                    r = await fn(client, wid, i)
                    codes[r.status_code] = codes.get(r.status_code, 0) + 1
                except Exception:  # noqa: BLE001
                    codes[0] = codes.get(0, 0) + 1
                lat.append((time.perf_counter() - t0) * 1000)
                i += 1
        t_start = time.perf_counter()
        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        elapsed = time.perf_counter() - t_start
    return lat, codes, elapsed


def write_test(seconds: float, concurrency: int) -> None:
    keys = list(json.load(open("seed_keys.json")).values())
    run_id = uuid.uuid4().hex[:8]

    async def fn(client, wid, i):
        cats = RNG.sample(TOPIC_IDS, 2)
        body = {"kind": "update", "text": f"load {run_id} {wid}-{i}: {RNG.choice(WORDS)} news on {RNG.choice(WORDS)}",
                "categories": cats}
        return await client.post("/v1/posts", json=body, headers={"Authorization": f"Bearer {keys[wid % len(keys)]}",
                                                                   "Idempotency-Key": f"{run_id}-{wid}-{i}"})
    lat, codes, elapsed = asyncio.run(_http_loop(fn, seconds, concurrency))
    ok = codes.get(201, 0)
    with SessionLocal() as db:
        n_agents = db.scalar(select(func.count()).select_from(Agent))
    emit("write", agents=n_agents, concurrency=concurrency, seconds=round(elapsed, 1), requests=len(lat),
         ok=ok, codes=codes, posts_per_sec=round(ok / elapsed, 1), posts_per_min=round(60 * ok / elapsed),
         p50_ms=pct(lat, 50), p95_ms=pct(lat, 95), p99_ms=pct(lat, 99))


def read_test(seconds: float, concurrency: int) -> None:
    handles = list(json.load(open("seed_keys.json")).keys())

    async def fn(client, wid, i):
        if i % 2 == 0:
            return await client.get("/v1/timeline?limit=30")
        return await client.get(f"/v1/feed/preview/{handles[wid % len(handles)]}?limit=20")
    lat, codes, elapsed = asyncio.run(_http_loop(fn, seconds, concurrency))
    emit("read", concurrency=concurrency, seconds=round(elapsed, 1), requests=len(lat), codes=codes,
         rps=round(len(lat) / elapsed, 1), p50_ms=pct(lat, 50), p95_ms=pct(lat, 95), p99_ms=pct(lat, 99))


# ------------------------------------------------------------------ agent worker throughput

def worker_test(max_runs: int) -> None:
    from app.agents import runtime
    with SessionLocal() as db:
        pending = db.scalar(select(func.count()).select_from(Notification).where(Notification.status == "pending"))
    t0 = time.perf_counter()
    done = runtime.process_pending(limit=50, max_rounds=max(1, max_runs // 50))
    elapsed = time.perf_counter() - t0
    emit("worker", pending_before=pending, runs=done, seconds=round(elapsed, 1),
         runs_per_sec=round(done / elapsed, 1) if elapsed else None, ms_per_run=round(1000 * elapsed / max(1, done), 1))


# ------------------------------------------------------------------ live connections (SSE)

def sse_test(clients: int) -> None:
    import httpx
    keys = list(json.load(open("seed_keys.json")).values())

    async def main():
        received: list[float] = []
        ready = asyncio.Event()
        connected = 0
        sent_at = {"t": 0.0}
        marker = "sse-" + uuid.uuid4().hex[:10]
        limits = httpx.Limits(max_connections=clients + 10, max_keepalive_connections=clients + 10)
        async with httpx.AsyncClient(base_url=API, timeout=None, limits=limits) as client:
            async def listen():
                nonlocal connected
                try:
                    async with client.stream("GET", "/v1/stream") as r:
                        connected += 1
                        if connected >= clients:
                            ready.set()
                        async for line in r.aiter_lines():
                            if marker in line:
                                received.append((time.perf_counter() - sent_at["t"]) * 1000)
                                return
                except Exception:  # noqa: BLE001
                    return
            tasks = [asyncio.create_task(listen()) for _ in range(clients)]
            try:
                await asyncio.wait_for(ready.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1)
            sent_at["t"] = time.perf_counter()
            await client.post("/v1/posts", json={"kind": "update", "text": f"{marker} live fan-out probe",
                                                 "categories": ["news"]},
                              headers={"Authorization": f"Bearer {keys[0]}"})
            await asyncio.sleep(10)
            for t in tasks:
                t.cancel()
        return connected, received
    connected, received = asyncio.run(main())
    emit("sse", clients=clients, connected=connected, delivered=len(received),
         p50_ms=pct(received, 50), p95_ms=pct(received, 95), max_ms=round(max(received), 1) if received else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("populate"); p.add_argument("--agents", type=int, required=True)
    p = sub.add_parser("notify"); p.add_argument("--samples", type=int, default=30)
    p.add_argument("--recall-samples", type=int, default=10)
    for name in ("write", "read"):
        p = sub.add_parser(name)
        p.add_argument("--seconds", type=float, default=20)
        p.add_argument("--concurrency", type=int, default=8 if name == "write" else 32)
    p = sub.add_parser("worker"); p.add_argument("--max", type=int, default=400)
    p = sub.add_parser("sse"); p.add_argument("--clients", type=int, default=500)
    a = ap.parse_args()
    if a.cmd == "populate":
        populate(a.agents)
    elif a.cmd == "notify":
        notify_bench(a.samples, a.recall_samples)
    elif a.cmd == "write":
        write_test(a.seconds, a.concurrency)
    elif a.cmd == "read":
        read_test(a.seconds, a.concurrency)
    elif a.cmd == "worker":
        worker_test(a.max)
    elif a.cmd == "sse":
        sse_test(a.clients)


if __name__ == "__main__":
    main()
