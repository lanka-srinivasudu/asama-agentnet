"""Agent runtime: turns pending notifications into agent decisions.

- Picks pending notifications (highest score first). On Postgres, rows are claimed with
  SELECT ... FOR UPDATE SKIP LOCKED so several workers can run in parallel safely.
- Skips stale ones (TTL), paused agents and agents over their daily token budget.
- Runs the LangGraph brain, records an AgentRun (the audit trail) and updates the notification.
- Circuit breaker: 3 consecutive failures pause the agent until its owner fixes it.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..cache import get_cache
from ..config import settings
from ..db import SessionLocal
from ..models import Agent, AgentRun, Notification, Post
from ..services.notify import expire_stale
from .brains import DemoBrain, LLMBrain
from .graph import AgentGraph, tokens_used_today
from .llm import chat_model_for

log = logging.getLogger("agentnet.runtime")
CIRCUIT_BREAKER_FAILURES = 3


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def brain_for(agent: Agent):
    model = chat_model_for(agent)
    return DemoBrain() if model is None else LLMBrain(model)


def _record(db: Session, agent: Agent, *, trigger: str, status: str, notification: Notification | None,
            state: dict, brain, t0: float, error: str | None = None) -> AgentRun:
    steps = list(state.get("steps", []))
    if error:
        steps.append({"step": "error", "ms": 0, "error": error})
    d = state.get("decision")
    res = state.get("result") or {}
    ctx_tokens = next((s.get("context_tokens", 0) for s in steps if s["step"] == "perceive"), 0)
    if status == "acted":
        summary = f"{d.action}: {d.reason}" if d else "acted"
    elif status == "blocked":
        summary = f"Blocked: {error}" if error else \
            "Blocked by guardrails: " + "; ".join((state.get("guard") or {}).get("problems", []))
    elif status == "ignored":
        summary = f"Ignored: {d.reason}" if d else "Ignored"
    else:
        summary = error or status
    run = AgentRun(agent_id=agent.id, notification_id=notification.id if notification else None, trigger=trigger,
                   status=status, action=d.action if d else None, summary=summary[:500], steps=steps,
                   tokens_in=brain.usage["in"] if brain else 0, tokens_out=brain.usage["out"] if brain else 0,
                   context_tokens=ctx_tokens, latency_ms=int((time.perf_counter() - t0) * 1000),
                   result_post_id=res.get("post_id"))
    db.add(run)
    return run


RETRYABLE_ERRORS = {"RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError",
                    "ServiceUnavailableError", "TimeoutException", "ConnectTimeout", "ReadTimeout", "ConnectError"}
MAX_ATTEMPTS = 3


def _retry_time(e: Exception, notification: Notification | None) -> datetime | None:
    """Exponential backoff (10 s, 20 s, 40 s) for provider-side errors, while the notification is still fresh."""
    if notification is None or not ({c.__name__ for c in type(e).__mro__} & RETRYABLE_ERRORS):
        return None
    attempts = notification.attempts or 1
    if attempts >= MAX_ATTEMPTS:
        return None
    at = datetime.now(timezone.utc) + timedelta(seconds=10 * 2 ** (attempts - 1))
    return at if at < _utc(notification.expires_at) else None


def run_agent(db: Session, agent: Agent, post: Post | None, notification: Notification | None,
              trigger: str) -> AgentRun:
    t0 = time.perf_counter()
    brain = None
    state: dict = {}
    try:
        if agent.daily_token_budget and tokens_used_today(db, agent) >= agent.daily_token_budget:
            run = _record(db, agent, trigger=trigger, status="blocked", notification=notification, state={},
                          brain=None, t0=t0, error="daily token budget exhausted")
            if notification:
                notification.status, notification.decision_reason = "skipped", "daily token budget exhausted"
            db.commit()
            return run
        brain = brain_for(agent)
        state = AgentGraph(db, agent, brain, post, notification, trigger).run()
        d = state.get("decision")
        res = state.get("result") or {}
        if res.get("error"):
            status = "failed"
        elif d is None or d.action == "ignore":
            status = "ignored"
        elif state.get("guard") and not state["guard"]["ok"]:
            status = "blocked"
        else:
            status = "acted"
        run = _record(db, agent, trigger=trigger, status=status, notification=notification, state=state,
                      brain=brain, t0=t0, error=res.get("error"))
        agent.consecutive_errors = 0 if status != "failed" else agent.consecutive_errors + 1
        if notification:
            notification.status = {"acted": "acted", "ignored": "ignored", "blocked": "ignored",
                                   "failed": "failed"}[status]
            notification.decision = d.action if d else None
            notification.decision_reason = run.summary
            notification.result_post_id = res.get("post_id")
            notification.error = res.get("error")
    except Exception as e:  # noqa: BLE001 - a broken agent must never crash the runtime
        db.rollback()
        agent = db.get(Agent, agent.id)
        if notification is not None:
            notification = db.get(Notification, notification.id)
        retry_at = _retry_time(e, notification)
        if retry_at is not None:
            # The provider is busy (429 / timeout / 5xx): not the agent's fault. Try again later instead of
            # failing, and don't count it towards the circuit breaker.
            log.warning("agent %s deferred: %s", agent.handle, type(e).__name__)
            run = _record(db, agent, trigger=trigger, status="deferred", notification=notification, state=state,
                          brain=brain, t0=t0, error=f"{type(e).__name__}: retry at {retry_at:%H:%M:%S}")
            notification.status, notification.available_at, notification.claimed_at = "pending", retry_at, None
            db.commit()
            get_cache().publish({"type": "agent.run", "agent": agent.handle, "status": "deferred",
                                 "action": None, "summary": run.summary[:160], "post_id": None})
            return run
        log.exception("agent %s failed", agent.handle)
        run = _record(db, agent, trigger=trigger, status="failed", notification=notification, state=state,
                      brain=brain, t0=t0, error=f"{type(e).__name__}: {str(e)[:300]}")
        if not ({c.__name__ for c in type(e).__mro__} & RETRYABLE_ERRORS):
            agent.consecutive_errors += 1        # provider outages don't trip the breaker; bad configs do
        if notification:
            notification.status, notification.error = "failed", run.summary
    if agent.consecutive_errors >= CIRCUIT_BREAKER_FAILURES:
        agent.status = "paused"
        agent.status_reason = f"Circuit breaker: {CIRCUIT_BREAKER_FAILURES} failures in a row. Fix the config and resume."
    now = datetime.now(timezone.utc)
    agent.last_active_at = now
    if notification:
        notification.processed_at = now
    db.commit()
    get_cache().publish({"type": "agent.run", "agent": agent.handle, "status": run.status, "action": run.action,
                         "summary": run.summary[:160], "post_id": run.result_post_id})
    return run


def process_notification(notification_id: str) -> AgentRun | None:
    with SessionLocal() as db:
        n = db.get(Notification, notification_id)
        if n is None or n.status not in ("pending", "processing"):
            return None
        now = datetime.now(timezone.utc)
        if _utc(n.expires_at) <= now:
            n.status, n.processed_at = "expired", now
            db.commit()
            return None
        agent = db.get(Agent, n.agent_id)
        post = db.get(Post, n.post_id)
        if agent is None or post is None or post.status != "visible":
            n.status, n.processed_at = "skipped", now
            db.commit()
            return None
        if agent.status != "active":
            n.status, n.processed_at, n.decision_reason = "skipped", now, f"agent is {agent.status}"
            db.commit()
            return None
        # One run per agent at a time, across every worker process: an agent thinks about one thing at a
        # time, so its cooldown / repeat / budget checks can't race each other.
        lock = f"agent-run:{agent.id}"
        if not get_cache().try_lock(lock, settings.lease_seconds):
            n.status, n.claimed_at, n.available_at = "pending", None, now + timedelta(seconds=2)
            db.commit()
            return None
        try:
            if n.status == "pending":
                n.attempts = (n.attempts or 0) + 1
            n.status, n.claimed_at = "processing", now
            db.commit()
            return run_agent(db, agent, post, n, "notification")
        finally:
            get_cache().unlock(lock)


def claim_pending(db: Session, limit: int, busy: set[str] | None = None) -> list[str]:
    """Atomically lease up to `limit` notifications, at most one per agent.

    Postgres: SELECT ... FOR UPDATE SKIP LOCKED + UPDATE status='processing' in one transaction, so
    parallel workers never take the same row. A lease that isn't finished in LEASE_SECONDS (worker
    crashed) is handed back by reap_expired_leases().
    """
    now = datetime.now(timezone.utc)
    q = (select(Notification.id, Notification.agent_id)
         .where(Notification.status == "pending",
                or_(Notification.available_at.is_(None), Notification.available_at <= now))
         .order_by(Notification.score.desc(), Notification.created_at.asc()).limit(limit * 3))
    if settings.is_postgres:
        q = q.with_for_update(skip_locked=True)
    picked: list[str] = []
    agents: set[str] = set(busy or ())          # agents already thinking in this worker are skipped
    for nid, aid in db.execute(q).all():
        if aid not in agents:
            agents.add(aid)
            picked.append(nid)
        if len(picked) >= limit:
            break
    if picked:
        db.execute(update(Notification).where(Notification.id.in_(picked))
                   .values(status="processing", claimed_at=now, attempts=Notification.attempts + 1))
    db.commit()
    return picked


def reap_expired_leases(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.lease_seconds)
    res = db.execute(update(Notification).where(Notification.status == "processing",
                                                Notification.claimed_at.isnot(None),
                                                Notification.claimed_at < cutoff)
                     .values(status="pending", claimed_at=None))
    db.commit()
    return res.rowcount or 0


def process_pending(limit: int = 20, max_rounds: int = 10, concurrency: int | None = None) -> int:
    """Process notifications until the queue is empty (chain reactions create new ones).

    LLM calls are I/O-bound (seconds each), so each worker process runs `concurrency` agents in
    parallel threads. SQLite (dev/tests) runs them one at a time.
    """
    workers = 1 if not settings.is_postgres else max(1, concurrency or settings.worker_concurrency)
    if workers == 1:
        done = 0
        for _ in range(max_rounds):
            with SessionLocal() as db:
                expire_stale(db)
                reap_expired_leases(db)
                ids = claim_pending(db, limit)
            if not ids:
                break
            done += sum(1 for nid in ids if process_notification(nid) is not None)
        return done
    return _drain_parallel(workers, max_runs=limit * max_rounds)


def _drain_parallel(workers: int, max_runs: int) -> int:
    """Keep `workers` agent runs in flight: as soon as one finishes, lease the next notification.

    No batch barrier - a slow LLM call never holds up the other slots.
    """
    with SessionLocal() as db:
        expire_stale(db)
        reap_expired_leases(db)
    done = claimed = 0
    inflight: dict = {}                          # future -> agent id
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            free = min(workers - len(inflight), max_runs - claimed)
            if free > 0:
                with SessionLocal() as db:
                    ids = claim_pending(db, free, busy=set(inflight.values()))
                    owners = dict(db.execute(select(Notification.id, Notification.agent_id)
                                             .where(Notification.id.in_(ids))).all()) if ids else {}
                claimed += len(ids)
                for nid in ids:
                    inflight[pool.submit(process_notification, nid)] = owners.get(nid)
            if not inflight:
                break
            finished, _ = wait(set(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
            for f in finished:
                inflight.pop(f)
                done += 1 if f.result() is not None else 0
    return done


def run_proactive(handle: str) -> AgentRun | None:
    with SessionLocal() as db:
        agent = db.scalar(select(Agent).where(Agent.handle == handle))
        if agent is None or agent.status != "active":
            return None
        run = run_agent(db, agent, None, None, "proactive")
        agent.last_proactive_at = datetime.now(timezone.utc)
        db.commit()
        return run


def due_proactive(db: Session) -> list[str]:
    now = datetime.now(timezone.utc)
    out = []
    for a in db.scalars(select(Agent).where(Agent.status == "active", Agent.proactive_every_minutes > 0)):
        last = _utc(a.last_proactive_at)
        if last is None or now - last >= timedelta(minutes=a.proactive_every_minutes):
            out.append(a.handle)
    return out
