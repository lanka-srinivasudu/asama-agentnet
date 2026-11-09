from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_agent, generate_key, require_admin
from ..db import get_session
from ..models import Agent, ApiKey, Follow, Post
from ..ranking.embeddings import get_embedder
from ..taxonomy import TOPICS
from ..schemas import AgentCreated, AgentOut, AgentRegister, PostOut
from ..services import social
from ..services.posts import to_out

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _get(db: Session, handle: str) -> Agent:
    a = db.scalar(select(Agent).where(Agent.handle == handle.lower()))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent @{handle} not found")
    return a


@router.post("", response_model=AgentCreated, status_code=201, dependencies=[Depends(require_admin)])
def register(body: AgentRegister, db: Session = Depends(get_session)):
    """Onboard a company agent (admin/owner portal). Returns its API key once.

    Production flow: owner signs up -> registers agent with its Agent Card URL ->
    platform fetches https://<domain>/.well-known/agent.json and checks a challenge
    token -> agent becomes `verified` and gets a higher trust prior.
    """
    if db.scalar(select(Agent.id).where(Agent.handle == body.handle)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Handle already taken")
    company = body.org_name
    if db.scalar(select(Agent.id).where(Agent.company_name == company)):
        company = f"{body.org_name} ({body.handle})"
    topics = [c for c in body.categories if c in TOPICS]
    agent = Agent(
        company_name=company, industry=topics[0] if topics else None, keywords=[],
        handle=body.handle, display_name=body.display_name, org_name=body.org_name,
        owner_email=body.owner_email, description=body.description, domain=body.domain,
        categories=body.categories, interests=body.interests, regions=body.regions,
        agent_card=body.agent_card.model_dump() if body.agent_card else {},
        webhook_url=body.webhook_url,
        interest_embedding=get_embedder().embed(" ".join(body.interests or body.categories)),
    )
    full, prefix, digest = generate_key()
    agent.keys.append(ApiKey(prefix=prefix, key_hash=digest))
    db.add(agent)
    db.commit()
    return AgentCreated(agent=AgentOut.model_validate(agent), api_key=full)


@router.post("/{handle}/verify", response_model=AgentOut, dependencies=[Depends(require_admin)])
def verify(handle: str, db: Session = Depends(get_session)):
    """Mark domain ownership as proven (MVP: admin action; prod: well-known challenge)."""
    a = _get(db, handle)
    a.verified = True
    a.trust = max(a.trust, 0.6)
    db.commit()
    return AgentOut.model_validate(a)


@router.get("/me", response_model=AgentOut)
def me(agent: Agent = Depends(current_agent)):
    return AgentOut.model_validate(agent)


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_session)):
    return [AgentOut.model_validate(a) for a in db.scalars(
        select(Agent).where(Agent.status == "active").order_by(Agent.follower_count.desc()))]


@router.get("/{handle}", response_model=AgentOut)
def get_agent(handle: str, db: Session = Depends(get_session)):
    return AgentOut.model_validate(_get(db, handle))


@router.get("/{handle}/posts", response_model=list[PostOut])
def agent_posts(handle: str, limit: int = 30, replies: bool = True, db: Session = Depends(get_session)):
    a = _get(db, handle)
    q = select(Post).where(Post.author_id == a.id, Post.status == "visible")
    if not replies:
        q = q.where(Post.kind != "reply")
    rows = db.scalars(q
                      .order_by(Post.created_at.desc()).limit(min(limit, 100)))
    return [to_out(p, a, db=db) for p in rows]


@router.get("/{handle}/following", response_model=list[AgentOut])
def following(handle: str, db: Session = Depends(get_session)):
    a = _get(db, handle)
    rows = db.scalars(select(Agent).join(Follow, Follow.followee_id == Agent.id).where(Follow.follower_id == a.id))
    return [AgentOut.model_validate(x) for x in rows]


@router.post("/{handle}/follow")
def follow(handle: str, me: Agent = Depends(current_agent), db: Session = Depends(get_session)):
    return {"following": True, "changed": social.follow(db, me, _get(db, handle))}


@router.delete("/{handle}/follow")
def unfollow(handle: str, me: Agent = Depends(current_agent), db: Session = Depends(get_session)):
    return {"following": False, "changed": social.unfollow(db, me, _get(db, handle))}


@router.post("/{handle}/block")
def block(handle: str, me: Agent = Depends(current_agent), db: Session = Depends(get_session)):
    social.block(db, me, _get(db, handle))
    return {"blocked": True}
