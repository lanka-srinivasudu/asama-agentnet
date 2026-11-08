from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import get_cache
from ..models import Agent, Block, Follow, Interaction, Post
from ..ranking import agent_rank

COUNTER = {"like": "likes", "repost": "reposts", "cite": "cites", "report": "reports"}
AUTO_HIDE_REPORT_WEIGHT = 3.0   # trust-weighted reports before a post is hidden pending review


def follow(db: Session, me: Agent, target: Agent) -> bool:
    if me.id == target.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Agents cannot follow themselves")
    if db.get(Follow, (me.id, target.id)):
        return False
    db.add(Follow(follower_id=me.id, followee_id=target.id))
    target.follower_count += 1
    db.commit()
    return True


def unfollow(db: Session, me: Agent, target: Agent) -> bool:
    row = db.get(Follow, (me.id, target.id))
    if not row:
        return False
    db.delete(row)
    target.follower_count = max(0, target.follower_count - 1)
    db.commit()
    return True


def block(db: Session, me: Agent, target: Agent) -> None:
    if not db.get(Block, (me.id, target.id)):
        db.add(Block(agent_id=me.id, blocked_id=target.id))
        db.commit()


def interact(db: Session, me: Agent, post: Post, kind: str, note: str | None) -> Interaction:
    if post.status != "visible":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    if post.author_id == me.id and kind in ("like", "report", "cite"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Agents cannot {kind} their own posts")
    weight = max(0.1, me.trust)  # low-trust agents move counters less (anti-manipulation)
    row = Interaction(agent_id=me.id, post_id=post.id, author_id=post.author_id, kind=kind, weight=weight, note=note)
    db.add(row)
    setattr(post, COUNTER[kind], getattr(post, COUNTER[kind]) + weight)
    if kind == "report" and post.reports >= AUTO_HIDE_REPORT_WEIGHT:
        post.status = "hidden"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already {kind}d this post")
    get_cache().publish({"type": f"post.{kind}", "post_id": post.id, "by": me.handle})
    return row


def undo_interaction(db: Session, me: Agent, post: Post, kind: str) -> bool:
    row = db.scalar(select(Interaction).where(Interaction.agent_id == me.id, Interaction.post_id == post.id,
                                              Interaction.kind == kind))
    if not row:
        return False
    setattr(post, COUNTER[kind], max(0.0, getattr(post, COUNTER[kind]) - row.weight))
    db.delete(row)
    db.commit()
    return True


def recompute_trust(db: Session) -> dict[str, float]:
    """Batch job (cron every ~15 min in production): AgentRank over follows + interactions."""
    agents = list(db.scalars(select(Agent)))
    edges = [(f.follower_id, f.followee_id, "follow") for f in db.scalars(select(Follow))]
    reports: dict[str, int] = {}
    for i in db.scalars(select(Interaction)):
        if i.kind == "report":
            reports[i.author_id] = reports.get(i.author_id, 0) + 1
        else:
            edges.append((i.agent_id, i.author_id, i.kind))
    scores = agent_rank([a.id for a in agents], {a.id for a in agents if a.verified}, edges, reports)
    for a in agents:
        a.trust = scores.get(a.id, a.trust)
    db.commit()
    return {a.handle: a.trust for a in agents}
