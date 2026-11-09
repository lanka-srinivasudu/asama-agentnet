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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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
        log.exception("agent %s failed", agent.handle)
        agent = db.get(Agent, agent.id)
        if notification is not None:
            notification = db.get(Notification, notification.id)
        run = _record(db, agent, trigger=trigger, status="failed", notification=notification, state=state,
                      brain=brain, t0=t0, error=f"{type(e).__name__}: {str(e)[:300]}")
        agent.consecutive_errors += 1
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
        if n is None or n.status != "pending":
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
        n.status = "processing"
        db.commit()
        return run_agent(db, agent, post, n, "notification")


def claim_pending(db: Session, limit: int) -> list[str]:
    q = (select(Notification.id).where(Notification.status == "pending")
         .order_by(Notification.score.desc(), Notification.created_at.asc()).limit(limit))
    if settings.is_postgres:
        q = q.with_for_update(skip_locked=True)
    return list(db.scalars(q))


def process_pending(limit: int = 20, max_rounds: int = 10) -> int:
    """Process notifications until the queue is empty (chain reactions create new ones)."""
    done = 0
    for _ in range(max_rounds):
        with SessionLocal() as db:
            expire_stale(db)
            ids = claim_pending(db, limit)
            db.commit()
        if not ids:
            break
        for nid in ids:
            if process_notification(nid) is not None:
                done += 1
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
