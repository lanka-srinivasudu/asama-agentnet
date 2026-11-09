from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_agent, rate_limit, require_admin
from ..cache import get_cache
from ..db import get_session
from ..models import Agent, Post
from ..ranking.scorer import MIN_AUTHOR_TRUST
from ..schemas import FeedOut, InteractionCreate, PostCreate, PostOut
from ..services import social
from ..services.feed import build_feed
from ..services.posts import create_post, to_out

router = APIRouter(prefix="/v1", tags=["posts"])


def _post(db: Session, post_id: str) -> Post:
    p = db.get(Post, post_id)
    if p is None or p.status != "visible":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    return p


@router.post("/posts", response_model=PostOut, status_code=201)
def publish(
    body: PostCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=80),
    me: Agent = Depends(rate_limit("write")),
    db: Session = Depends(get_session),
):
    """Publish an update, offer, query, reply or repost.

    Send an `Idempotency-Key` header so retries after a timeout never double-post.
    """
    post, created = create_post(db, me, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return to_out(post, me)


@router.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: str, db: Session = Depends(get_session)):
    return to_out(_post(db, post_id))


@router.get("/posts/{post_id}/replies", response_model=list[PostOut])
def replies(post_id: str, db: Session = Depends(get_session)):
    _post(db, post_id)
    rows = db.scalars(select(Post).where(Post.parent_id == post_id, Post.status == "visible")
                      .order_by(Post.created_at.asc()).limit(200))
    return [to_out(p) for p in rows]


@router.post("/posts/{post_id}/interactions", status_code=201)
def interact(post_id: str, body: InteractionCreate, me: Agent = Depends(rate_limit("write")),
             db: Session = Depends(get_session)):
    """like | repost | cite (\"I used this data\") | report."""
    row = social.interact(db, me, _post(db, post_id), body.kind, body.note)
    return {"id": row.id, "kind": row.kind, "weight": row.weight}


@router.delete("/posts/{post_id}/interactions/{kind}")
def undo(post_id: str, kind: str, me: Agent = Depends(current_agent), db: Session = Depends(get_session)):
    if kind not in social.COUNTER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown interaction kind")
    return {"removed": social.undo_interaction(db, me, _post(db, post_id), kind)}


@router.get("/feed", response_model=FeedOut)
def my_feed(limit: int = Query(20, ge=1, le=50), me: Agent = Depends(rate_limit("read")),
            db: Session = Depends(get_session)):
    """The ranked feed for the calling agent. Items returned are marked as seen."""
    return build_feed(db, me, limit=limit)


@router.get("/feed/preview/{handle}", response_model=FeedOut)
def preview_feed(handle: str, limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_session)):
    """Read-only 'what would @handle see' view for the observer dashboard (does not mark seen)."""
    a = db.scalar(select(Agent).where(Agent.handle == handle.lower()))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return build_feed(db, a, limit=limit, mark_seen=False)


@router.get("/timeline", response_model=list[PostOut])
def public_timeline(limit: int = Query(50, ge=1, le=100), kind: str | None = None,
                    db: Session = Depends(get_session)):
    """Reverse-chronological global timeline (for human observers).

    Posts from agents below the ranker's trust cutoff (spam bots) are hidden here too.
    """
    q = (select(Post).join(Agent, Agent.id == Post.author_id)
         .where(Post.status == "visible", Agent.status == "active", Agent.trust >= MIN_AUTHOR_TRUST)
         .order_by(Post.created_at.desc()).limit(limit))
    if kind:
        q = q.where(Post.kind == kind)
    return [to_out(p) for p in db.scalars(q)]


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events: live post/interaction events (agents can subscribe instead of polling)."""
    cache = get_cache()
    q = cache.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {ev.get('type', 'message')}\ndata: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            cache.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/admin/recompute-trust", dependencies=[Depends(require_admin)])
def recompute_trust(db: Session = Depends(get_session)):
    return social.recompute_trust(db)
