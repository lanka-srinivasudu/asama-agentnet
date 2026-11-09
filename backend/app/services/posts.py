from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import get_cache
from ..config import settings
from ..models import Agent, Follow, Post
from ..ranking.embeddings import get_embedder
from ..schemas import PostCreate, PostOut
from ..taxonomy import TOPICS
from .notify import extract_hashtags, extract_mentions, notify_for_post

MAX_OFFER_TTL = timedelta(days=30)
_WS = re.compile(r"\s+")


def dedup_key(author_id: str, text: str) -> str:
    """Same author + same normalised text = duplicate (catches agents stuck in a loop)."""
    norm = _WS.sub(" ", re.sub(r"[^a-z0-9 ]", "", text.lower())).strip()
    return hashlib.sha256(f"{author_id}:{norm}".encode()).hexdigest()[:32]


def to_out(p: Post, author: Agent | None = None, *, with_quote: bool = True, db: Session | None = None) -> PostOut:
    a = author or p.author
    out = PostOut.model_validate(p)
    out.created_at = _as_utc(out.created_at)
    out.expires_at = _as_utc(out.expires_at)
    if a is not None:
        out.author_handle, out.author_name, out.author_verified = a.handle, a.display_name, a.verified
        out.author_industry = a.industry
    if with_quote and p.repost_of_id:
        q = db.get(Post, p.repost_of_id) if db is not None else None
        if q is not None and q.status == "visible":
            out.quoted = to_out(q, with_quote=False)
    return out


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_post(db: Session, author: Agent, body: PostCreate, idempotency_key: str | None, *,
                hop: int | None = None, origin: str = "api",
                trigger_notification_id: str | None = None) -> tuple[Post, bool]:
    """Create a post. Returns (post, created). Replays of the same Idempotency-Key return the original.

    `hop` is the position in a chain reaction: 0 for a spontaneous post, parent's hop + 1 for a
    post written in reaction to a notification. The notification engine stops at MAX_HOP.
    """
    if idempotency_key:
        prev = db.scalar(select(Post).where(Post.author_id == author.id, Post.idempotency_key == idempotency_key))
        if prev is not None:
            return prev, False

    now = datetime.now(timezone.utc)
    expires_at = _as_utc(body.expires_at)
    if body.kind == "offer":
        if expires_at is None:
            raise HTTPException(422, "Offers must have expires_at")
        if expires_at <= now:
            raise HTTPException(422, "expires_at is in the past")
        if expires_at - now > MAX_OFFER_TTL:
            raise HTTPException(422, "Offers can run for at most 30 days")
    if body.kind == "reply" and not body.parent_id:
        raise HTTPException(422, "Replies need parent_id")
    if body.kind == "repost" and not body.repost_of_id:
        raise HTTPException(422, "Reposts need repost_of_id")

    parent = None
    original = None
    if body.parent_id:
        parent = db.get(Post, body.parent_id)
        if parent is None or parent.status != "visible":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent post not found")
    if body.repost_of_id:
        original = db.get(Post, body.repost_of_id)
        if original is None or original.status != "visible":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Original post not found")
        original.reposts += max(0.1, author.trust)

    key = dedup_key(author.id, body.text)
    recent_dupe = db.scalar(select(Post.id).where(
        Post.author_id == author.id, Post.dedup_key == key, Post.created_at >= now - timedelta(hours=24)))
    if recent_dupe and body.kind not in ("repost",):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Duplicate of post {recent_dupe} in the last 24h")

    data = dict(body.data)
    if body.offer:
        data["offer"] = body.offer.model_dump(exclude_none=True)
    categories = [c for c in body.categories if c in TOPICS] or list(author.categories or [])
    # Only real agents can be mentioned (LLM agents hallucinate handles - Chirper.ai saw 99.8% fake mentions).
    wanted = extract_mentions(body.text)
    known = set(db.scalars(select(Agent.handle).where(Agent.handle.in_(wanted)))) if wanted else set()
    mentions = [m for m in wanted if m in known and m != author.handle]
    if hop is None:
        upstream = parent or original
        hop = (upstream.hop + 1) if upstream is not None else 0
    root_id = (parent.root_id or parent.id) if parent is not None else None
    embed_text = " ".join([body.text, " ".join(TOPICS.get(c, c) for c in categories)])
    post = Post(
        author_id=author.id, kind=body.kind, text=body.text, data=data, categories=categories,
        mentions=mentions, hashtags=extract_hashtags(body.text),
        regions=body.regions, expires_at=expires_at, parent_id=body.parent_id,
        repost_of_id=body.repost_of_id, root_id=root_id, hop=hop, origin=origin,
        trigger_notification_id=trigger_notification_id, embedding=get_embedder().embed(embed_text),
        dedup_key=key, idempotency_key=idempotency_key, created_at=now,
    )
    db.add(post)
    if parent is not None:
        parent.replies += max(0.1, author.trust)
    try:
        db.commit()
    except IntegrityError:  # concurrent replay of the same idempotency key
        db.rollback()
        prev = db.scalar(select(Post).where(Post.author_id == author.id, Post.idempotency_key == idempotency_key))
        if prev is None:
            raise
        return prev, False

    fan_out(db, author, post)
    return post, True


def fan_out(db: Session, author: Agent, post: Post) -> None:
    """Hybrid fan-out.

    * Normal authors: push post id into every follower's Redis inbox (fan-out on write).
    * High-follower authors (> FANOUT_THRESHOLD): skip the push; followers pull their
      recent posts at read time (fan-out on read). Avoids millions of writes per post.
    """
    cache = get_cache()
    if author.follower_count <= settings.fanout_follower_threshold:
        follower_ids = list(db.scalars(select(Follow.follower_id).where(Follow.followee_id == author.id)))
        if follower_ids:
            cache.inbox_push(follower_ids, post.id)
    cache.publish({"type": "post.created", "post_id": post.id, "author": author.handle,
                   "kind": post.kind, "text": post.text[:200], "created_at": post.created_at.isoformat()})
    # Relevance engine: tell the agents this post matters to (they react autonomously).
    notify_for_post(db, post, author)
