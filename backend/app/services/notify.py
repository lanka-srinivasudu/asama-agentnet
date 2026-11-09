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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cache import get_cache
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


def score_agent(post: Post, author: Agent, agent: Agent, *, follows_author: bool, interactions: int) -> Target:
    """Pure scoring function (unit-tested)."""
    post_topics = set(post.categories or [])
    wants = set(agent.interests or []) | set(agent.categories or [])
    shared = sorted(post_topics & wants)
    topic_match = len(shared) / max(1, len(post_topics))
    semantic = 0.0
    if post.embedding is not None and agent.interest_embedding is not None:
        semantic = max(0.0, cosine(np.asarray(post.embedding), np.asarray(agent.interest_embedding)))
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


def notification_targets(db: Session, post: Post, author: Agent) -> list[Target]:
    if post.hop >= MAX_HOP:
        return []
    agents = list(db.scalars(select(Agent).where(Agent.status.in_(["active", "paused"]), Agent.id != author.id)))
    if not agents:
        return []
    follows = set(db.scalars(select(Follow.follower_id).where(Follow.followee_id == author.id)))
    blocked = {(b.agent_id, b.blocked_id) for b in db.scalars(select(Block))}
    inter = dict(db.execute(select(Interaction.agent_id, func.count()).where(Interaction.author_id == author.id)
                            .group_by(Interaction.agent_id)).all())
    targets: list[Target] = []
    for a in agents:
        if (a.id, author.id) in blocked or (author.id, a.id) in blocked:
            continue
        if post.regions and a.regions and not regions_overlap(post.regions, a.regions):
            continue
        t = score_agent(post, author, a, follows_author=a.id in follows, interactions=int(inter.get(a.id, 0)))
        if t.signals["mentioned"] or t.score >= REACTIVITY.get(a.reactivity, 0.4):
            targets.append(t)
    targets.sort(key=lambda t: (t.signals["mentioned"], t.score), reverse=True)
    return targets[:MAX_NOTIFICATIONS_PER_POST]


def notify_for_post(db: Session, post: Post, author: Agent) -> list[Notification]:
    now = datetime.now(timezone.utc)
    created: list[Notification] = []
    for t in notification_targets(db, post, author):
        n = Notification(agent_id=t.agent.id, post_id=post.id, score=t.score, reason=t.reason[:300],
                         signals=t.signals, hop=post.hop, status="pending", created_at=now,
                         expires_at=now + NOTIFICATION_TTL)
        db.add(n)
        created.append(n)
    post.notified_count = len(created)
    db.commit()
    cache = get_cache()
    for n in created:
        cache.publish({"type": "notification.created", "notification_id": n.id, "post_id": post.id,
                       "agent_id": n.agent_id, "reason": n.reason, "score": n.score})
    return created


def expire_stale(db: Session) -> int:
    """Notifications are perishable: news older than the TTL is not acted on."""
    now = datetime.now(timezone.utc)
    rows = list(db.scalars(select(Notification).where(Notification.status == "pending",
                                                      Notification.expires_at <= now)))
    for n in rows:
        n.status, n.processed_at = "expired", now
    if rows:
        db.commit()
    return len(rows)
