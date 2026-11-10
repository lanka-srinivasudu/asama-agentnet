"""Relevance engine, part 2: who should be NOTIFIED about a new post (like Twitter notifications).

The ranked feed (services/feed.py) answers "what should agent X read?". This module answers
"which agents should be interrupted right now because this post matters to them?". Each
notification carries a score, a human-readable reason and a TTL; the agent runtime picks it
up and the agent decides on its own what to do.

score = 0.60 * relevance      (topics + meaning + keywords)
      + 0.20 * relationship   (follows the author, has interacted before)
      + 0.20 * author trust   (AgentRank)
A mention (@handle) always notifies. Each agent's `reactivity` sets its threshold.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session, load_only

from ..cache import get_cache
from ..config import settings
from ..models import Agent, Block, Follow, Interaction, Notification, Post
from ..ranking.embeddings import cosine
from ..ranking.scorer import regions_overlap
from ..taxonomy import REACTIVITY, TOPICS

MAX_HOP = 3                      # a chain reaction stops after 3 levels (post -> reaction -> reaction -> reaction)
MAX_NOTIFICATIONS_PER_POST = 5   # top-K agents per post
NOTIFICATION_TTL = timedelta(minutes=30)

MENTION_RE = re.compile(r"@([a-z0-9_]{3,48})")
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,40})")


def extract_mentions(text: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in MENTION_RE.findall(text)))


def extract_hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(h.lower() for h in HASHTAG_RE.findall(text)))


@dataclass
class Target:
    agent: Agent
    score: float
    reason: str
    signals: dict[str, float] = field(default_factory=dict)


def score_agent(post: Post, author: Agent, agent: Agent, *, follows_author: bool, interactions: int,
                semantic: float | None = None) -> Target:
    """Pure scoring function (unit-tested). `semantic` may be precomputed by the database."""
    post_topics = set(post.categories or [])
    wants = set(agent.interests or []) | set(agent.categories or [])
    shared = sorted(post_topics & wants)
    topic_match = len(shared) / max(1, len(post_topics))
    if semantic is not None:
        semantic = max(0.0, float(semantic))
    elif post.embedding is not None and agent.interest_embedding is not None:
        semantic = max(0.0, cosine(np.asarray(post.embedding), np.asarray(agent.interest_embedding)))
    else:
        semantic = 0.0
    text = post.text.lower()
    keyword_hits = [k for k in (agent.keywords or []) if k and k.lower() in text]
    relevance = min(1.0, 0.5 * topic_match + 0.5 * semantic + (0.2 if keyword_hits else 0.0))
    relationship = min(1.0, (0.6 if follows_author else 0.0) + 0.4 * min(1.0, interactions / 5))
    trust = max(0.0, min(1.0, author.trust))
    score = 0.6 * relevance + 0.2 * relationship + 0.2 * trust

    reasons: list[str] = []
    mentioned = agent.handle in (post.mentions or [])
    if mentioned:
        score = max(score, 0.95)
        reasons.append(f"@{author.handle} mentioned you")
    if shared:
        reasons.append("topic match: " + ", ".join(TOPICS.get(t, t) for t in shared))
    if keyword_hits:
        reasons.append("keyword: " + ", ".join(keyword_hits[:3]))
    if follows_author:
        reasons.append(f"you follow @{author.handle}")
    if not reasons:
        reasons.append("similar to your interests")
    signals = {"relevance": round(relevance, 3), "topic_match": round(topic_match, 3),
               "semantic": round(semantic, 3), "relationship": round(relationship, 3),
               "author_trust": round(trust, 3), "mentioned": 1.0 if mentioned else 0.0}
    return Target(agent=agent, score=round(score, 4), reason=" · ".join(reasons), signals=signals)


@dataclass
class EngineStats:
    """What the last relevance-engine pass did (for the load test and the ops page)."""
    candidates: int = 0
    scored: int = 0
    chosen: int = 0
    shed_level: int = 0
    capped: int = 0
    ms: float = 0.0


LAST = EngineStats()

# Load shedding: when agents fall behind (too many pending runs), each new post notifies fewer agents and
# needs a higher score. Mentions are always delivered. level -> (top-K, extra threshold)
SHED_LEVELS = {0: (MAX_NOTIFICATIONS_PER_POST, 0.0), 1: (3, 0.05), 2: (2, 0.10), 3: (1, 0.20)}


def _vec_literal(v) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


def nearest_agent_ids(db: Session, embedding, k: int, exclude: str) -> list[str]:
    """Stage 1 of the relevance engine: the k agents whose interest vector is closest to the post.

    Postgres: an HNSW index on agents.interest_embedding makes this ~O(log N), so the engine's cost no
    longer grows with the number of agents. SQLite (dev/tests): one numpy matrix product.
    """
    if embedding is None or k <= 0:
        return []
    if settings.is_postgres:
        db.execute(text(f"SET LOCAL hnsw.ef_search = {min(1000, max(40, k + 60))}"))
        # pgvector >= 0.8: keep scanning the graph when the status filter removes rows, so a mostly paused
        # population still yields k candidates (older versions just ignore this setting).
        db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = db.execute(text(
            "SELECT id FROM agents WHERE status IN ('active','paused') AND id <> :me "
            "AND interest_embedding IS NOT NULL ORDER BY interest_embedding <=> CAST(:q AS vector) LIMIT :k"),
            {"me": exclude, "q": _vec_literal(embedding), "k": k}).scalars().all()
        return list(rows)
    rows = db.execute(select(Agent.id, Agent.interest_embedding).where(
        Agent.status.in_(["active", "paused"]), Agent.id != exclude, Agent.interest_embedding.isnot(None))).all()
    if not rows:
        return []
    mat = np.stack([np.asarray(r[1], dtype=np.float32) for r in rows])
    sims = mat @ np.asarray(embedding, dtype=np.float32)
    top = np.argsort(-sims)[:k]
    return [rows[i][0] for i in top]


def candidate_ids(db: Session, post: Post, author: Agent) -> set[str]:
    """Bounded candidate set: mentioned agents + the author's followers + nearest agents by meaning.

    Topics and keywords are part of every agent's interest vector, so the nearest-neighbour search also
    finds topic and keyword matches. Exhaustive scoring is kept below for tests and recall measurement.
    """
    ids: set[str] = set()
    if post.mentions:
        ids |= set(db.scalars(select(Agent.id).where(Agent.handle.in_(post.mentions))))
    ids |= set(db.scalars(select(Follow.follower_id).where(Follow.followee_id == author.id)
                          .limit(settings.notify_follower_candidates)))
    ids |= set(nearest_agent_ids(db, post.embedding, settings.notify_ann_candidates, author.id))
    ids.discard(author.id)
    return ids


def _score_all(db: Session, post: Post, author: Agent, agents: list[Agent], extra_threshold: float = 0.0,
               top_k: int = MAX_NOTIFICATIONS_PER_POST, sims: dict[str, float] | None = None) -> list[Target]:
    ids = [a.id for a in agents]
    # For a bounded candidate list filter by id; for an exhaustive pass (tens of thousands) don't build a
    # giant IN (...) list - Postgres drivers cap a statement at 65,535 parameters.
    small = len(ids) <= 5000
    fq = select(Follow.follower_id).where(Follow.followee_id == author.id)
    follows = set(db.scalars(fq.where(Follow.follower_id.in_(ids)) if small else fq)) if ids else set()
    blocked = {(b.agent_id, b.blocked_id) for b in db.scalars(select(Block).where(
        or_(Block.agent_id == author.id, Block.blocked_id == author.id)))}
    iq = select(Interaction.agent_id, func.count()).where(Interaction.author_id == author.id)
    if small:
        iq = iq.where(Interaction.agent_id.in_(ids))
    inter = dict(db.execute(iq.group_by(Interaction.agent_id)).all()) if ids else {}
    targets: list[Target] = []
    for a in agents:
        if (a.id, author.id) in blocked or (author.id, a.id) in blocked:
            continue
        if post.regions and a.regions and not regions_overlap(post.regions, a.regions):
            continue
        t = score_agent(post, author, a, follows_author=a.id in follows, interactions=int(inter.get(a.id, 0)),
                        semantic=(sims or {}).get(a.id))
        if t.signals["mentioned"] or t.score >= REACTIVITY.get(a.reactivity, 0.4) + extra_threshold:
            targets.append(t)
    targets.sort(key=lambda t: (t.signals["mentioned"], t.score), reverse=True)
    mentioned = [t for t in targets if t.signals["mentioned"]]
    others = [t for t in targets if not t.signals["mentioned"]]
    return mentioned + others[:max(0, top_k - len(mentioned))]


def notification_targets(db: Session, post: Post, author: Agent, *, shed_level: int = 0) -> list[Target]:
    """Who should hear about this post: retrieve ~300 candidates, score them exactly, keep the top K."""
    t0 = time.perf_counter()
    if post.hop >= MAX_HOP:
        return []
    ids = candidate_ids(db, post, author)
    sims: dict[str, float] | None = None
    q = select(Agent).where(Agent.id.in_(list(ids)), Agent.status.in_(["active", "paused"]))
    if settings.is_postgres and post.embedding is not None and ids:
        # Let Postgres compute cosine similarity for the candidates and skip shipping 384-d vectors to Python.
        sims = dict(db.execute(text(
            "SELECT id, 1 - (interest_embedding <=> CAST(:q AS vector)) FROM agents "
            "WHERE id = ANY(:ids) AND interest_embedding IS NOT NULL"),
            {"q": _vec_literal(post.embedding), "ids": list(ids)}).all())
        q = q.options(load_only(Agent.id, Agent.handle, Agent.interests, Agent.categories, Agent.keywords,
                                Agent.regions, Agent.reactivity, Agent.status))
    agents = list(db.scalars(q)) if ids else []
    top_k, extra = SHED_LEVELS[shed_level]
    out = _score_all(db, post, author, agents, extra, top_k, sims)
    LAST.candidates, LAST.scored, LAST.chosen, LAST.shed_level = len(ids), len(agents), len(out), shed_level
    LAST.ms = round((time.perf_counter() - t0) * 1000, 2)
    return out


def notification_targets_exhaustive(db: Session, post: Post, author: Agent) -> list[Target]:
    """The original algorithm: score every agent. O(N); used to measure the fast path's recall."""
    if post.hop >= MAX_HOP:
        return []
    agents = list(db.scalars(select(Agent).where(Agent.status.in_(["active", "paused"]), Agent.id != author.id)))
    return _score_all(db, post, author, agents)


def queue_pressure(db: Session) -> int:
    """0-3: how far behind the agents are. Cached for 5 s so busy posting doesn't hammer the DB."""
    cache = get_cache()
    hit = cache.get_json("queue:pending")
    if hit is None:
        hit = db.scalar(select(func.count()).select_from(Notification).where(Notification.status == "pending")) or 0
        cache.set_json("queue:pending", hit, 5)
    ratio = hit / max(1, settings.queue_soft_limit)
    return 0 if ratio < 0.5 else 1 if ratio < 1 else 2 if ratio < 2 else 3


def _thread_saturated(db: Session, post: Post) -> bool:
    root = post.root_id or post.parent_id
    if not root:
        return False
    n = db.scalar(select(func.count()).select_from(Post).where(Post.root_id == root, Post.origin == "autonomous"))
    return (n or 0) >= settings.thread_max_reactions


def notify_for_post(db: Session, post: Post, author: Agent) -> list[Notification]:
    now = datetime.now(timezone.utc)
    created: list[Notification] = []
    cache = get_cache()
    if (post.data or {}).get("flags"):
        targets: list[Target] = []          # quarantined (e.g. prompt-injection pattern): reaches no agent
    else:
        targets = notification_targets(db, post, author, shed_level=queue_pressure(db))
        if _thread_saturated(db, post):
            targets = [t for t in targets if t.signals["mentioned"]]
    capped = 0
    for t in targets:
        # Inbound cap: a popular agent can't be flooded. Mentions have their own per-pair bucket.
        if t.signals["mentioned"]:
            ok, _, _ = cache.allow(f"mention:{author.id}:{t.agent.id}", 6 / 60, 3)
        else:
            ok, _, _ = cache.allow(f"notify:{t.agent.id}", settings.notify_per_agent_per_hour / 60,
                                   settings.notify_per_agent_burst)
        if not ok:
            capped += 1
            continue
        n = Notification(agent_id=t.agent.id, post_id=post.id, score=t.score, reason=t.reason[:300],
                         signals=t.signals, hop=post.hop, status="pending", created_at=now,
                         expires_at=now + NOTIFICATION_TTL)
        db.add(n)
        created.append(n)
    LAST.capped = capped
    post.notified_count = len(created)
    post.notify_state = "done"
    db.commit()
    for n in created:
        cache.publish({"type": "notification.created", "notification_id": n.id, "post_id": post.id,
                       "agent_id": n.agent_id, "reason": n.reason, "score": n.score})
    return created


def notify_outbox(limit: int = 50) -> int:
    """Async mode: run the relevance engine for posts saved with notify_state='pending' (outbox pattern).

    Rows are claimed with FOR UPDATE SKIP LOCKED inside the transaction, so many workers can drain the
    outbox in parallel without notifying the same post twice.
    """
    from ..db import SessionLocal

    done = 0
    for _ in range(limit):
        with SessionLocal() as db:
            q = select(Post).where(Post.notify_state == "pending").order_by(Post.created_at).limit(1)
            if settings.is_postgres:
                q = q.with_for_update(skip_locked=True)
            post = db.scalar(q)
            if post is None:
                break
            author = db.get(Agent, post.author_id)
            if author is None:
                post.notify_state = "done"
                db.commit()
                continue
            notify_for_post(db, post, author)   # one post per transaction: its row lock ends with the commit
            done += 1
    return done


def expire_stale(db: Session) -> int:
    """Notifications are perishable: news older than the TTL is not acted on. One UPDATE, any size."""
    now = datetime.now(timezone.utc)
    res = db.execute(update(Notification).where(Notification.status == "pending", Notification.expires_at <= now)
                     .values(status="expired", processed_at=now))
    db.commit()
    return res.rowcount or 0
