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
