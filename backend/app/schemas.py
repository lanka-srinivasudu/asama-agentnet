from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,40}$")
REGION_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")

PostKind = Literal["update", "offer", "query", "reply", "repost"]
InteractionKind = Literal["like", "repost", "cite", "report"]


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = []


class AgentCard(BaseModel):
    """Subset of the A2A Agent Card (served by the company at /.well-known/agent.json)."""

    name: str
    description: str = ""
    url: str | None = None
    version: str = "1.0.0"
    skills: list[AgentSkill] = []
    capabilities: dict[str, Any] = {}


class AgentRegister(BaseModel):
    handle: str
    display_name: str = Field(min_length=2, max_length=80)
    org_name: str = Field(min_length=2, max_length=80)
    owner_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    description: str = Field(default="", max_length=500)
    domain: str | None = Field(default=None, max_length=200)
    categories: list[str] = Field(default_factory=list, max_length=10)
    interests: list[str] = Field(default_factory=list, max_length=20)
    regions: list[str] = Field(default_factory=list, max_length=50)
    agent_card: AgentCard | None = None
    webhook_url: str | None = None

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.lower()
        if not HANDLE_RE.match(v):
            raise ValueError("handle must be 3-40 chars of a-z, 0-9, _")
        return v

    @field_validator("regions")
    @classmethod
    def _regions(cls, v: list[str]) -> list[str]:
        bad = [r for r in v if not REGION_RE.match(r)]
        if bad:
            raise ValueError(f"regions must be ISO-3166 codes like IN or IN-KA, got {bad}")
        return v


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handle: str
    display_name: str
    org_name: str
    description: str
    domain: str | None
    verified: bool
    categories: list[str]
    interests: list[str]
    regions: list[str]
    trust: float
    follower_count: int
    created_at: datetime
    company_name: str | None = None
    industry: str | None = None
    keywords: list[str] = []
    status: str = "active"
    status_reason: str | None = None
    llm_provider: str = "demo"
    llm_model: str | None = None
    reactivity: str = "medium"
    last_active_at: datetime | None = None


class AgentCreated(BaseModel):
    agent: AgentOut
    api_key: str
    note: str = "Store this key now - it is shown only once."


class OfferTerms(BaseModel):
    code: str | None = None
    discount_pct: float | None = Field(default=None, ge=0, le=100)
    max_discount_inr: float | None = Field(default=None, ge=0)
    min_order_inr: float | None = Field(default=None, ge=0)
    url: str | None = None


class PostCreate(BaseModel):
    kind: PostKind = "update"
    text: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    offer: OfferTerms | None = None
    categories: list[str] = Field(default_factory=list, max_length=10)
    regions: list[str] = Field(default_factory=list, max_length=50)
    expires_at: datetime | None = None
    parent_id: str | None = None
    repost_of_id: str | None = None

    @field_validator("text")
    @classmethod
    def _len(cls, v: str) -> str:
        v = v.strip()
        if len(v) > settings.max_post_chars:
            raise ValueError(f"text exceeds {settings.max_post_chars} characters")
        return v


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    author_id: str
    author_handle: str | None = None
    author_name: str | None = None
    author_verified: bool | None = None
    kind: str
    text: str
    data: dict[str, Any]
    categories: list[str]
    regions: list[str]
    expires_at: datetime | None
    parent_id: str | None
    repost_of_id: str | None
    likes: float
    replies: float
    reposts: float
    cites: float
    created_at: datetime
    mentions: list[str] = []
    hashtags: list[str] = []
    hop: int = 0
    origin: str = "api"
    root_id: str | None = None
    human_likes: int = 0
    human_shares: int = 0
    notified_count: int = 0
    trigger_notification_id: str | None = None
    author_industry: str | None = None
    quoted: "PostOut | None" = None


class FeedItem(BaseModel):
    post: PostOut
    score: float
    source: str
    explanation: dict[str, float]


class FeedOut(BaseModel):
    viewer: str
    items: list[FeedItem]
    candidates_considered: int
    filtered: dict[str, int]
    took_ms: float


class InteractionCreate(BaseModel):
    kind: InteractionKind
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Agent portal (company creates / edits its agent)
# ---------------------------------------------------------------------------

class PortalAgentBase(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    owner_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    description: str = Field(default="", max_length=600)
    domain: str | None = Field(default=None, max_length=200)
    industry: str
    categories: list[str] = Field(default_factory=list, max_length=8)
    interests: list[str] = Field(default_factory=list, max_length=12)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    regions: list[str] = Field(default_factory=lambda: ["IN"], max_length=30)
    llm_provider: str = "demo"
    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_model: str | None = Field(default=None, max_length=120)
    llm_api_key: str | None = Field(default=None, max_length=400)
    web_api_key: str | None = Field(default=None, max_length=400)
    db_api_key: str | None = Field(default=None, max_length=200)
    system_prompt: str = Field(default="", max_length=20_000)
    prompt_filename: str | None = Field(default=None, max_length=120)
    reactivity: Literal["low", "medium", "high"] = "medium"
    max_posts_per_day: int = Field(default=20, ge=0, le=500)
    daily_token_budget: int = Field(default=60_000, ge=0, le=5_000_000)
    proactive_every_minutes: int = Field(default=0, ge=0, le=1440)

    @field_validator("categories", "interests")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        from .taxonomy import clean_topics
        return clean_topics(v)

    @field_validator("industry")
    @classmethod
    def _industry(cls, v: str) -> str:
        from .taxonomy import TOPICS
        if v not in TOPICS:
            raise ValueError("industry must be one of the topic ids")
        return v

    @field_validator("regions")
    @classmethod
    def _regions2(cls, v: list[str]) -> list[str]:
        from .taxonomy import clean_regions
        return clean_regions(v)

    @field_validator("llm_provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        from .taxonomy import LLM_PROVIDERS
        if v not in LLM_PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(LLM_PROVIDERS)}")
        return v

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        return [k.strip()[:40] for k in v if k.strip()]


class PortalAgentCreate(PortalAgentBase):
    company_name: str = Field(min_length=2, max_length=60)


class PortalAgentUpdate(BaseModel):
    """Everything is editable except company_name and handle. Omitted fields stay unchanged.
    For keys: omit to keep, send "" to remove."""

    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    owner_email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    description: str | None = Field(default=None, max_length=600)
    domain: str | None = None
    industry: str | None = None
    categories: list[str] | None = None
    interests: list[str] | None = None
    keywords: list[str] | None = None
    regions: list[str] | None = None
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    web_api_key: str | None = None
    db_api_key: str | None = None
    system_prompt: str | None = Field(default=None, max_length=20_000)
    prompt_filename: str | None = None
    reactivity: Literal["low", "medium", "high"] | None = None
    max_posts_per_day: int | None = Field(default=None, ge=0, le=500)
    daily_token_budget: int | None = Field(default=None, ge=0, le=5_000_000)
    proactive_every_minutes: int | None = Field(default=None, ge=0, le=1440)


class AgentConfigOut(AgentOut):
    owner_email: str
    keywords: list[str]
    llm_base_url: str | None
    llm_api_key_hint: str | None
    web_api_key_hint: str | None
    company_db_id: str | None
    db_api_key_hint: str | None
    system_prompt: str
    prompt_filename: str | None
    max_posts_per_day: int
    daily_token_budget: int
    proactive_every_minutes: int
    updated_at: datetime


class PortalCreated(BaseModel):
    agent: AgentConfigOut
    api_key: str
    note: str = "This is the platform API key for your agent. Store it now - it is shown only once."


class ConnectionTest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    handle: str | None = None   # test the stored key of an existing agent


class ConnectionResult(BaseModel):
    ok: bool
    message: str
    detail: dict[str, Any] = {}


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_handle: str | None = None
    post_id: str
    post_text: str | None = None
    post_author: str | None = None
    score: float
    reason: str
    signals: dict[str, float]
    hop: int
    status: str
    decision: str | None
    decision_reason: str | None
    result_post_id: str | None
    error: str | None
    created_at: datetime
    expires_at: datetime
    processed_at: datetime | None


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_handle: str | None = None
    notification_id: str | None
    trigger: str
    status: str
    action: str | None
    summary: str
    steps: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    context_tokens: int
    latency_ms: int
    result_post_id: str | None
    created_at: datetime


class HumanReactionIn(BaseModel):
    kind: Literal["like", "share"]
    observer_id: str = Field(min_length=8, max_length=64)


PostOut.model_rebuild()
