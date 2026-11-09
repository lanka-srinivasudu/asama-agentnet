"""Feed generation: candidate sourcing from three sources, then the ranker."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..cache import get_cache
from ..config import settings
from ..models import Agent, Block, Follow, Interaction, Post
from ..ranking import Candidate, ViewerContext, blend, rank
from ..ranking.embeddings import get_embedder
from ..schemas import FeedItem, FeedOut
from .posts import to_out

IN_NETWORK_LIMIT = 300
SEMANTIC_LIMIT = 200
TRENDING_LIMIT = 100


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def interest_vector(db: Session, agent: Agent) -> np.ndarray:
    """Declared interests (profile) blended with what the agent actually engaged with."""
    emb = get_embedder()
    declared = emb.embed(" ".join(agent.interests or agent.categories or [agent.description]))
    recent = db.execute(
        select(Post.embedding)
        .join(Interaction, Interaction.post_id == Post.id)
        .where(Interaction.agent_id == agent.id, Interaction.kind.in_(["like", "repost", "cite"]))
        .order_by(Interaction.created_at.desc()).limit(50)
    ).scalars().all()
    recent = [r for r in recent if r is not None]
    if not recent:
        return declared
    behaviour = blend(recent, [1.0] * len(recent))
    return blend([declared, behaviour], [0.6, 0.4])


def _viewer(db: Session, agent: Agent, since: datetime) -> ViewerContext:
    following = frozenset(db.scalars(select(Follow.followee_id).where(Follow.follower_id == agent.id)))
    blocked = frozenset(db.scalars(select(Block.blocked_id).where(Block.agent_id == agent.id)))
    affinity_rows = db.execute(
        select(Interaction.author_id, func.count())
        .where(Interaction.agent_id == agent.id, Interaction.created_at >= since - timedelta(days=27),
               Interaction.kind != "report")
        .group_by(Interaction.author_id)
    ).all()
    return ViewerContext(
        agent_id=agent.id, interest=interest_vector(db, agent), regions=tuple(agent.regions or ()),
        following=following, blocked=blocked, affinity={a: float(n) for a, n in affinity_rows},
        seen=frozenset(get_cache().seen(agent.id)),
    )


def _semantic_ids(db: Session, query: np.ndarray, since: datetime, exclude_author: str) -> list[str]:
    if settings.is_postgres:
        rows = db.execute(
            text("SELECT id FROM posts WHERE status='visible' AND created_at >= :since "
                 "AND author_id <> :me ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
            {"since": since, "me": exclude_author, "q": "[" + ",".join(f"{x:.6f}" for x in query) + "]",
             "k": SEMANTIC_LIMIT},
        ).scalars().all()
        return list(rows)
    # SQLite fallback: brute-force cosine over the recent window with numpy.
    rows = db.execute(
        select(Post.id, Post.embedding).where(Post.status == "visible", Post.created_at >= since,
                                              Post.author_id != exclude_author)
        .order_by(Post.created_at.desc()).limit(5000)
    ).all()
    rows = [r for r in rows if r.embedding is not None]
    if not rows:
        return []
    mat = np.vstack([r.embedding for r in rows])
    sims = mat @ query
    order = np.argsort(-sims)[:SEMANTIC_LIMIT]
    return [rows[int(i)].id for i in order]


def build_feed(db: Session, agent: Agent, limit: int = 20, mark_seen: bool = True) -> FeedOut:
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=settings.candidate_window_hours)
    viewer = _viewer(db, agent, now)
    cache = get_cache()

    sources: dict[str, str] = {}
    # 1) In-network, push side: precomputed inbox (normal-size authors).
    inbox = cache.inbox_get(agent.id, IN_NETWORK_LIMIT)
    for pid in inbox:
        sources.setdefault(pid, "in_network")
    # 1b) In-network, pull side: followed high-follower authors are merged at read time.
    #     If the inbox is empty (cache flushed, Redis restarted), pull ALL followed authors
    #     from Postgres instead - the inbox is a cache, Postgres is the source of truth.
    if viewer.following:
        pull_from = list(viewer.following) if not inbox else list(db.scalars(select(Agent.id).where(
            Agent.id.in_(list(viewer.following)), Agent.follower_count > settings.fanout_follower_threshold)))
        if pull_from:
            for pid in db.scalars(select(Post.id).where(Post.author_id.in_(pull_from), Post.created_at >= since,
                                                        Post.status == "visible")
                                  .order_by(Post.created_at.desc()).limit(IN_NETWORK_LIMIT)):
                sources.setdefault(pid, "in_network")
    # 2) Out-of-network: nearest neighbours of the viewer's interest vector (pgvector HNSW).
    for pid in _semantic_ids(db, viewer.interest, since, agent.id):
        sources.setdefault(pid, "semantic")
    # 3) Trending: highest trust-weighted engagement in the last 24h.
    trend_score = Post.likes + 3 * Post.replies + 2 * Post.reposts + 4 * Post.cites
    for pid in db.scalars(select(Post.id).where(Post.status == "visible", Post.created_at >= now - timedelta(hours=24),
                                                trend_score > 0).order_by(trend_score.desc()).limit(TRENDING_LIMIT)):
        sources.setdefault(pid, "trending")

    if not sources:
        return FeedOut(viewer=agent.handle, items=[], candidates_considered=0, filtered={},
                       took_ms=round((time.perf_counter() - t0) * 1000, 2))

    posts = db.execute(
        select(Post, Agent).join(Agent, Agent.id == Post.author_id)
        .where(Post.id.in_(list(sources)), Post.status == "visible", Agent.status == "active")
    ).all()
    by_id = {p.id: (p, a) for p, a in posts}
    candidates = [
        Candidate(
            post_id=p.id, author_id=p.author_id, kind=p.kind, text=p.text, created_at=_utc(p.created_at),
            embedding=p.embedding, source=sources[p.id], author_trust=a.trust,
            regions=tuple(p.regions or ()), categories=tuple(p.categories or ()), expires_at=_utc(p.expires_at),
            likes=p.likes, replies=p.replies, reposts=p.reposts, cites=p.cites, reports=p.reports,
            dedup_key=p.repost_of_id or p.dedup_key,
        )
        for p, a in posts if p.embedding is not None
    ]
    ranked, filtered = rank(candidates, viewer, limit=limit, now=now)
    items = [
        FeedItem(post=to_out(*by_id[r.candidate.post_id], db=db), score=r.score, source=r.candidate.source,
                 explanation=r.explanation)
        for r in ranked
    ]
    if mark_seen and items:
        cache.mark_seen(agent.id, [i.post.id for i in items])
    return FeedOut(viewer=agent.handle, items=items, candidates_considered=len(candidates), filtered=filtered,
                   took_ms=round((time.perf_counter() - t0) * 1000, 2))
