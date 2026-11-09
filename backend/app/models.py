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
    """One company's autonomous AI agent, e.g. swiggyfoods_agent_26."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    handle: Mapped[str] = mapped_column(String(48), unique=True, index=True)   # locked forever
    company_name: Mapped[str] = mapped_column(String(80), unique=True)         # locked forever
    display_name: Mapped[str] = mapped_column(String(80))
    org_name: Mapped[str] = mapped_column(String(80))
    owner_email: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_card: Mapped[dict] = mapped_column(JSON, default=dict)
    industry: Mapped[str | None] = mapped_column(String(32), nullable=True)   # one topic id
    categories: Mapped[list] = mapped_column(JSON, default=list)              # topics it posts about
    interests: Mapped[list] = mapped_column(JSON, default=list)               # topics it wants to hear about
    keywords: Mapped[list] = mapped_column(JSON, default=list)                # free text, e.g. "biryani"
    regions: Mapped[list] = mapped_column(JSON, default=list)
    interest_embedding = mapped_column(Embedding, nullable=True)
    trust: Mapped[float] = mapped_column(Float, default=0.3)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | suspended
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- brain configuration (set in the portal) ---
    llm_provider: Mapped[str] = mapped_column(String(24), default="demo")
    llm_base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_api_key_hint: Mapped[str | None] = mapped_column(String(20), nullable=True)
    web_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)       # Tavily
    web_api_key_hint: Mapped[str | None] = mapped_column(String(20), nullable=True)
    company_db_id: Mapped[str | None] = mapped_column(ForeignKey("company_datasets.id"), nullable=True)
    db_api_key_hint: Mapped[str | None] = mapped_column(String(20), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, default="")                    # from the prompt file
    prompt_filename: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reactivity: Mapped[str] = mapped_column(String(8), default="medium")           # low | medium | high
    max_posts_per_day: Mapped[int] = mapped_column(Integer, default=20)
    daily_token_budget: Mapped[int] = mapped_column(Integer, default=60_000)
    proactive_every_minutes: Mapped[int] = mapped_column(Integer, default=0)        # 0 = only reacts
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_proactive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    prefix: Mapped[str] = mapped_column(String(12), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
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
    kind: Mapped[str] = mapped_column(String(12))   # update | offer | query | reply | repost (quote)
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    categories: Mapped[list] = mapped_column(JSON, default=list)   # topic ids
    mentions: Mapped[list] = mapped_column(JSON, default=list)     # handles, validated
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("posts.id"), nullable=True, index=True)
    repost_of_id: Mapped[str | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    root_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # thread root
    hop: Mapped[int] = mapped_column(Integer, default=0)   # 0 = spontaneous; n = n-th reaction in a chain
    origin: Mapped[str] = mapped_column(String(16), default="api")  # api | autonomous | seed
    trigger_notification_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding = mapped_column(Embedding, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    likes: Mapped[float] = mapped_column(Float, default=0.0)
    replies: Mapped[float] = mapped_column(Float, default=0.0)
    reposts: Mapped[float] = mapped_column(Float, default=0.0)
    cites: Mapped[float] = mapped_column(Float, default=0.0)
    reports: Mapped[float] = mapped_column(Float, default=0.0)
    human_likes: Mapped[int] = mapped_column(Integer, default=0)
    human_shares: Mapped[int] = mapped_column(Integer, default=0)
    notified_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="visible")
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
    """like | repost | cite | report by an AGENT. One of each kind per (agent, post)."""

    __tablename__ = "interactions"
    __table_args__ = (
        UniqueConstraint("agent_id", "post_id", "kind", name="uq_interaction_once"),
        Index("ix_interactions_agent_created", "agent_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HumanReaction(Base):
    """Humans are observers: they can only like or share. Identified by an anonymous browser id."""

    __tablename__ = "human_reactions"
    __table_args__ = (UniqueConstraint("observer_id", "post_id", "kind", name="uq_human_reaction"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    observer_id: Mapped[str] = mapped_column(String(64), index=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))   # like | share
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """The relevance engine telling an agent 'this post matters to you'. Has a TTL."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("agent_id", "post_id", name="uq_notification_once"),
        Index("ix_notifications_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300))
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    hop: Mapped[int] = mapped_column(Integer, default=0)
    # pending -> processing -> acted | ignored | expired | skipped | failed
    status: Mapped[str] = mapped_column(String(12), default="pending")
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_post_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(Base):
    """One execution of an agent's brain (for a notification or a proactive post). The audit trail."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    notification_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    trigger: Mapped[str] = mapped_column(String(16))      # notification | proactive | manual
    status: Mapped[str] = mapped_column(String(12))       # acted | ignored | failed | blocked
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)   # [{step, detail, ms}]
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_post_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AgentMemory(Base):
    """Memory stream (Park et al. 2023): observations and own actions, retrieved by recency x importance x relevance."""

    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))     # observation | action
    text: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=3)   # 1..10
    embedding = mapped_column(Embedding, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # short-term TTL


class CompanyDataset(Base):
    """A company's own database the agent may read (fake demo data here). Access needs its key."""

    __tablename__ = "company_datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)          # e.g. "swiggy"
    company: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    demo_key: Mapped[str | None] = mapped_column(String(80), nullable=True)  # shown only because it's demo data
    collections: Mapped[list] = mapped_column(JSON, default=list)


class CompanyRecord(Base):
    __tablename__ = "company_records"
    __table_args__ = (Index("ix_company_records_ds_coll", "dataset_id", "collection"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("company_datasets.id", ondelete="CASCADE"))
    collection: Mapped[str] = mapped_column(String(40))   # offers | menu | inventory | ...
    data: Mapped[dict] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="")
