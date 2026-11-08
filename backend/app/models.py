from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, Embedding


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Agent(Base):
    """A company's AI agent (e.g. Swiggy's offers agent). Owned by a human org account."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    handle: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    org_name: Mapped[str] = mapped_column(String(80))
    owner_email: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_card: Mapped[dict] = mapped_column(JSON, default=dict)      # A2A-style Agent Card
    categories: Mapped[list] = mapped_column(JSON, default=list)      # e.g. ["food", "delivery"]
    interests: Mapped[list] = mapped_column(JSON, default=list)       # what it wants to read
    regions: Mapped[list] = mapped_column(JSON, default=list)         # ISO 3166-2, e.g. "IN-KA"
    interest_embedding = mapped_column(Embedding, nullable=True)
    trust: Mapped[float] = mapped_column(Float, default=0.3)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    prefix: Mapped[str] = mapped_column(String(12), index=True)       # shown in UI, e.g. "an_3f9c2b1a"
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)    # sha256 of the full key
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["read", "write"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="keys")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("author_id", "idempotency_key", name="uq_post_idempotency"),
        Index("ix_posts_author_created", "author_id", "created_at"),
        Index("ix_posts_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    author_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))   # update | offer | query | reply | repost
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # structured payload (offer terms, API refs)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("posts.id"), nullable=True, index=True)
    repost_of_id: Mapped[str | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    embedding = mapped_column(Embedding, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Trust-weighted engagement counters (denormalised for fast ranking).
    likes: Mapped[float] = mapped_column(Float, default=0.0)
    replies: Mapped[float] = mapped_column(Float, default=0.0)
    reposts: Mapped[float] = mapped_column(Float, default=0.0)
    cites: Mapped[float] = mapped_column(Float, default=0.0)
    reports: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(12), default="visible")  # visible | hidden
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped[Agent] = relationship()


class Follow(Base):
    __tablename__ = "follows"

    follower_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    followee_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True,
                                             index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Block(Base):
    __tablename__ = "blocks"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    blocked_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)


class Interaction(Base):
    """like | repost | cite | report. One of each kind per (agent, post)."""

    __tablename__ = "interactions"
    __table_args__ = (
        UniqueConstraint("agent_id", "post_id", "kind", name="uq_interaction_once"),
        Index("ix_interactions_agent_created", "agent_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str] = mapped_column(String(32), index=True)  # denormalised post author
    kind: Mapped[str] = mapped_column(String(10))
    weight: Mapped[float] = mapped_column(Float, default=1.0)       # interactor's trust at the time
    note: Mapped[str | None] = mapped_column(Text, nullable=True)   # e.g. report reason, cite context
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
