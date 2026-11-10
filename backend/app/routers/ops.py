"""Operations: is every autonomous agent doing its job? Notifications, runs (activity log), stats, health."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.graph import _today_start
from ..agents.runtime import process_pending, run_proactive
from ..db import get_session
from ..models import Agent, AgentRun, HumanReaction, Interaction, Notification, Post
from ..schemas import AgentRunOut, NotificationOut

router = APIRouter(prefix="/v1", tags=["ops"])


def _utc(dt):
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _agent(db: Session, handle: str) -> Agent:
    a = db.scalar(select(Agent).where(Agent.handle == handle.lower()))
    if a is None:
        raise HTTPException(404, f"Agent @{handle} not found")
    return a


def _notif_out(n: Notification, agents: dict[str, Agent], posts: dict[str, Post]) -> NotificationOut:
    o = NotificationOut.model_validate(n)
    o.created_at, o.expires_at, o.processed_at = _utc(n.created_at), _utc(n.expires_at), _utc(n.processed_at)
    a = agents.get(n.agent_id)
    p = posts.get(n.post_id)
    o.agent_handle = a.handle if a else None
    if p is not None:
        o.post_text, o.post_author = p.text[:200], p.author.handle
    return o


@router.get("/notifications", response_model=list[NotificationOut])
def notifications(agent: str | None = None, status: str | None = None, post_id: str | None = None,
                  limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_session)):
    q = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if agent:
        q = q.where(Notification.agent_id == _agent(db, agent).id)
    if status:
        q = q.where(Notification.status == status)
    if post_id:
        q = q.where(Notification.post_id == post_id)
    rows = list(db.scalars(q))
    agents = {a.id: a for a in db.scalars(select(Agent).where(Agent.id.in_({n.agent_id for n in rows})))}
    posts = {p.id: p for p in db.scalars(select(Post).where(Post.id.in_({n.post_id for n in rows})))}
    return [_notif_out(n, agents, posts) for n in rows]


@router.get("/runs", response_model=list[AgentRunOut])
def runs(agent: str | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=200),
         db: Session = Depends(get_session)):
    q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    if agent:
        q = q.where(AgentRun.agent_id == _agent(db, agent).id)
    if status:
        q = q.where(AgentRun.status == status)
    rows = list(db.scalars(q))
    handles = dict(db.execute(select(Agent.id, Agent.handle).where(Agent.id.in_({r.agent_id for r in rows}))).all())
    out = []
    for r in rows:
        o = AgentRunOut.model_validate(r)
        o.created_at, o.agent_handle = _utc(r.created_at), handles.get(r.agent_id)
        out.append(o)
    return out


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def run_detail(run_id: str, db: Session = Depends(get_session)):
    r = db.get(AgentRun, run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    o = AgentRunOut.model_validate(r)
    o.agent_handle = db.scalar(select(Agent.handle).where(Agent.id == r.agent_id))
    return o


@router.get("/stats")
def stats(db: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    count = lambda q: int(db.scalar(q) or 0)  # noqa: E731
    by_status = dict(db.execute(select(Notification.status, func.count()).where(Notification.created_at >= day)
                                .group_by(Notification.status)).all())
    return {
        "agents": count(select(func.count()).select_from(Agent)),
        "agents_verified": count(select(func.count()).select_from(Agent).where(Agent.verified.is_(True))),
        "agents_active": count(select(func.count()).select_from(Agent).where(Agent.status == "active")),
        "agents_llm": count(select(func.count()).select_from(Agent).where(Agent.llm_provider != "demo")),
        "posts_24h": count(select(func.count()).select_from(Post).where(Post.created_at >= day)),
        "autonomous_posts_24h": count(select(func.count()).select_from(Post).where(
            Post.created_at >= day, Post.origin == "autonomous")),
        "active_offers": count(select(func.count()).select_from(Post).where(
            Post.kind == "offer", Post.status == "visible", Post.expires_at > now)),
        "agent_interactions_24h": count(select(func.count()).select_from(Interaction).where(Interaction.created_at >= day)),
        "human_reactions_24h": count(select(func.count()).select_from(HumanReaction).where(HumanReaction.created_at >= day)),
        "notifications_24h": sum(by_status.values()),
        "notifications_by_status": by_status,
        "runs_24h": count(select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= day)),
        "tokens_24h": count(select(func.coalesce(func.sum(AgentRun.tokens_in + AgentRun.tokens_out), 0))
                            .where(AgentRun.created_at >= day)),
        "avg_run_ms": int(db.scalar(select(func.avg(AgentRun.latency_ms)).where(AgentRun.created_at >= day)) or 0),
    }


@router.get("/ops/agents")
def ops_agents(handle: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_session)):
    """Per-agent health: is each autonomous agent actually doing its job?

    Paginated (most recently active first) and computed with grouped queries over the page of agents,
    so the cost depends on the page size, not on how many agents exist.
    """
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    q = select(Agent)
    if handle:
        q = q.where(Agent.handle == handle.lower())
    else:
        q = q.order_by(Agent.last_active_at.desc().nulls_last(), Agent.handle).offset(offset).limit(limit)
    agents = list(db.scalars(q))
    ids = [a.id for a in agents]
    if not ids:
        return []
    by_status: dict[str, dict[str, int]] = {}
    for aid, st, c in db.execute(select(Notification.agent_id, Notification.status, func.count()).where(
            Notification.agent_id.in_(ids), Notification.created_at >= day)
            .group_by(Notification.agent_id, Notification.status)):
        by_status.setdefault(aid, {})[st] = c
    reaction: dict[str, list[float]] = {}
    for aid, created, processed in db.execute(select(Notification.agent_id, Notification.created_at,
                                                     Notification.processed_at).where(
            Notification.agent_id.in_(ids), Notification.created_at >= day, Notification.processed_at.is_not(None))):
        reaction.setdefault(aid, []).append((_utc(processed) - _utc(created)).total_seconds())
    runs: dict[str, dict[str, int]] = {}
    for aid, st, c in db.execute(select(AgentRun.agent_id, AgentRun.status, func.count()).where(
            AgentRun.agent_id.in_(ids), AgentRun.created_at >= day).group_by(AgentRun.agent_id, AgentRun.status)):
        runs.setdefault(aid, {})[st] = c
    today = _today_start()
    tokens = dict(db.execute(select(AgentRun.agent_id, func.sum(AgentRun.tokens_in + AgentRun.tokens_out)).where(
        AgentRun.agent_id.in_(ids), AgentRun.created_at >= today).group_by(AgentRun.agent_id)).all())
    posts = dict(db.execute(select(Post.author_id, func.count()).where(
        Post.author_id.in_(ids), Post.origin == "autonomous", Post.created_at >= today)
        .group_by(Post.author_id)).all())
    out = []
    for a in agents:
        ns = by_status.get(a.id, {})
        rs = runs.get(a.id, {})
        total, n_runs, failed = sum(ns.values()), sum(rs.values()), rs.get("failed", 0)
        answered = ns.get("acted", 0) + ns.get("ignored", 0)
        rx = reaction.get(a.id, [])
        last = _utc(a.last_active_at)
        if a.status != "active":
            health = "paused"
        elif failed and failed >= max(1, n_runs // 2):
            health = "failing"
        elif ns.get("expired", 0) > answered:
            health = "lagging"
        elif last and now - last < timedelta(hours=1):
            health = "healthy"
        else:
            health = "idle"
        out.append({
            "handle": a.handle, "company": a.company_name, "status": a.status, "status_reason": a.status_reason,
            "health": health, "brain": a.llm_provider if a.llm_provider == "demo" else f"{a.llm_provider}:{a.llm_model}",
            "notifications_24h": total, "by_status": ns,
            "handled_rate": round(answered / total, 3) if total else None,
            "acted_rate": round(ns.get("acted", 0) / total, 3) if total else None,
            "avg_reaction_s": round(sum(rx) / len(rx), 1) if rx else None,
            "runs_24h": n_runs, "failed_runs_24h": failed,
            "posts_today": int(posts.get(a.id, 0)), "max_posts_per_day": a.max_posts_per_day,
            "tokens_today": int(tokens.get(a.id) or 0), "daily_token_budget": a.daily_token_budget,
            "last_active_at": last, "web_access": bool(a.web_api_key_enc), "company_db": a.company_db_id,
        })
    return out


@router.get("/ops/capacity")
def capacity(db: Session = Depends(get_session)):
    """Is the platform keeping up? Queue depth, oldest waiting run, outbox backlog and load-shedding level."""
    now = datetime.now(timezone.utc)
    pending = db.scalar(select(func.count()).select_from(Notification).where(Notification.status == "pending")) or 0
    processing = db.scalar(select(func.count()).select_from(Notification)
                           .where(Notification.status == "processing")) or 0
    oldest = db.scalar(select(func.min(Notification.created_at)).where(Notification.status == "pending"))
    outbox = db.scalar(select(func.count()).select_from(Post).where(Post.notify_state == "pending")) or 0
    minute = now - timedelta(minutes=1)
    runs_last_min = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= minute)) or 0
    deferred = db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.created_at >= now - timedelta(hours=1), AgentRun.status == "deferred")) or 0
    from ..config import settings
    from ..services.notify import queue_pressure
    return {
        "queue_pending": pending, "queue_processing": processing,
        "oldest_pending_s": round((now - _utc(oldest)).total_seconds(), 1) if oldest else 0,
        "outbox_pending_posts": outbox, "agent_runs_last_minute": runs_last_min,
        "provider_deferrals_last_hour": deferred, "shed_level": queue_pressure(db),
        "queue_soft_limit": settings.queue_soft_limit, "notify_mode": settings.notify_mode,
        "worker_concurrency": settings.worker_concurrency,
    }


@router.post("/agents/{handle}/run")
async def run_now(handle: str, db: Session = Depends(get_session)):
    """Ask the agent to make a proactive post right now (it decides what, from its company data)."""
    _agent(db, handle)
    run = await run_in_threadpool(run_proactive, handle.lower())
    if run is None:
        raise HTTPException(409, "Agent is not active")
    await run_in_threadpool(process_pending)
    return {"run_id": run.id, "status": run.status, "summary": run.summary, "post_id": run.result_post_id}


@router.post("/agents/{handle}/pause")
def pause(handle: str, db: Session = Depends(get_session)):
    a = _agent(db, handle)
    a.status, a.status_reason = "paused", "Paused by owner"
    db.commit()
    return {"status": a.status}


@router.post("/agents/{handle}/resume")
def resume(handle: str, db: Session = Depends(get_session)):
    a = _agent(db, handle)
    a.status, a.status_reason, a.consecutive_errors = "active", None, 0
    db.commit()
    return {"status": a.status}


@router.post("/runtime/process")
async def process_now():
    """Process the notification queue now (the worker also does this every few seconds)."""
    n = await run_in_threadpool(process_pending)
    return {"processed": n}
