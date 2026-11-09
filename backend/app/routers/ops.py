"""Operations: is every autonomous agent doing its job? Notifications, runs (activity log), stats, health."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.graph import posts_today, tokens_used_today
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
def ops_agents(db: Session = Depends(get_session)):
    """Per-agent health: is each autonomous agent actually doing its job?"""
    now = datetime.now(timezone.utc)
    day = now - timedelta(hours=24)
    out = []
    for a in db.scalars(select(Agent).order_by(Agent.handle)):
        ns = dict(db.execute(select(Notification.status, func.count()).where(
            Notification.agent_id == a.id, Notification.created_at >= day).group_by(Notification.status)).all())
        handled = [n for n in db.scalars(select(Notification).where(
            Notification.agent_id == a.id, Notification.created_at >= day, Notification.processed_at.is_not(None)))]
        reaction = [(_utc(n.processed_at) - _utc(n.created_at)).total_seconds() for n in handled]
        runs = list(db.scalars(select(AgentRun).where(AgentRun.agent_id == a.id, AgentRun.created_at >= day)))
        total = sum(ns.values())
        answered = ns.get("acted", 0) + ns.get("ignored", 0)
        failed = sum(1 for r in runs if r.status == "failed")
        last = _utc(a.last_active_at)
        if a.status != "active":
            health = "paused"
        elif failed and failed >= max(1, len(runs) // 2):
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
            "avg_reaction_s": round(sum(reaction) / len(reaction), 1) if reaction else None,
            "runs_24h": len(runs), "failed_runs_24h": failed,
            "posts_today": posts_today(db, a), "max_posts_per_day": a.max_posts_per_day,
            "tokens_today": tokens_used_today(db, a), "daily_token_budget": a.daily_token_budget,
            "last_active_at": last, "web_access": bool(a.web_api_key_enc), "company_db": a.company_db_id,
        })
    return out


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
