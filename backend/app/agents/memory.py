"""Agent memory stream (after Park et al., "Generative Agents", 2023).

Every observation ("PhonePe posted a cashback offer") and action ("I replied with code X")
is stored with an importance score. When the agent thinks, the most useful memories are
retrieved by   recency + importance + relevance   and put into its context.

TTLs: ordinary memories are short-term and expire after 24 h; important ones (>= 7) are kept.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Agent, AgentMemory
from ..ranking.embeddings import cosine, get_embedder

SHORT_TERM_TTL = timedelta(hours=24)
LONG_TERM_IMPORTANCE = 7
RECENCY_DECAY_PER_HOUR = 0.99


def remember(db: Session, agent: Agent, kind: str, text: str, importance: int = 3) -> AgentMemory:
    importance = max(1, min(10, int(importance)))
    now = datetime.now(timezone.utc)
    m = AgentMemory(agent_id=agent.id, kind=kind, text=text[:500], importance=importance,
                    embedding=get_embedder().embed(text), created_at=now,
                    expires_at=None if importance >= LONG_TERM_IMPORTANCE else now + SHORT_TERM_TTL)
    db.add(m)
    return m


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def recall(db: Session, agent: Agent, query_vec: np.ndarray, k: int = 5) -> list[tuple[AgentMemory, float]]:
    now = datetime.now(timezone.utc)
    rows = list(db.scalars(select(AgentMemory).where(
        AgentMemory.agent_id == agent.id,
        or_(AgentMemory.expires_at.is_(None), AgentMemory.expires_at > now),
    ).order_by(AgentMemory.created_at.desc()).limit(200)))
    scored = []
    for m in rows:
        hours = max(0.0, (now - _utc(m.created_at)).total_seconds() / 3600)
        recency = RECENCY_DECAY_PER_HOUR ** hours
        relevance = max(0.0, cosine(np.asarray(m.embedding), query_vec)) if m.embedding is not None else 0.0
        scored.append((m, round(recency + m.importance / 10 + relevance, 4)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
