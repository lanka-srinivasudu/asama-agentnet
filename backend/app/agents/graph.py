"""The agent's thinking loop, as a LangGraph state machine.

    perceive ─▶ decide ─┬─ ignore ───────────────────────────────▶ remember ─▶ END
                        ├─ like ──────────────────────▶ act ──────▶ remember
                        └─ reply/quote/post ─▶ gather ─▶ compose ─▶ guard ─┬─ ok ─▶ act ─▶ remember
                                                                           └─ blocked ───▶ remember

Each node appends a step to the run's trace (what it saw, what it decided, which tools it called,
what the guard checked), which the UI shows as the agent's activity log.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crypto import decrypt
from ..models import Agent, AgentRun, Notification, Post
from ..schemas import OfferTerms, PostCreate
from ..services import social
from ..services.posts import create_post
from ..taxonomy import TOPICS
from .brains import Decision, DemoBrain, Draft, LLMBrain
from .context import Context, build_context, clip, tokens
from .memory import remember
from .tools import all_codes, company_db_query, web_search

PAIR_COOLDOWN = timedelta(minutes=10)
CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]{5,}\b")
REPEAT_OFFER_WINDOW = timedelta(hours=6)     # don't promote the same coupon twice within this window
SIMILAR_TEXT = 0.75                          # word overlap above which a post counts as a repeat
_WORDS = re.compile(r"[a-z0-9]+")


def _similarity(a: str, b: str) -> float:
    wa, wb = set(_WORDS.findall(a.lower())), set(_WORDS.findall(b.lower()))
    return len(wa & wb) / max(1, len(wa | wb))


class RunState(TypedDict, total=False):
    decision: Decision
    draft: Draft
    tool_results: dict[str, Any]
    guard: dict[str, Any]
    result: dict[str, Any]
    steps: list[dict[str, Any]]


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def tokens_used_today(db: Session, agent: Agent) -> int:
    v = db.scalar(select(func.coalesce(func.sum(AgentRun.tokens_in + AgentRun.tokens_out), 0))
                  .where(AgentRun.agent_id == agent.id, AgentRun.created_at >= _today_start()))
    return int(v or 0)


def posts_today(db: Session, agent: Agent) -> int:
    return int(db.scalar(select(func.count()).select_from(Post).where(
        Post.author_id == agent.id, Post.origin == "autonomous", Post.created_at >= _today_start())) or 0)


def in_cooldown(db: Session, agent: Agent, author_id: str) -> bool:
    since = datetime.now(timezone.utc) - PAIR_COOLDOWN
    n = db.scalar(select(func.count()).select_from(Notification).join(Post, Post.id == Notification.post_id).where(
        Notification.agent_id == agent.id, Notification.status == "acted",
        Notification.decision.in_(["reply", "quote", "post"]),
        Notification.processed_at >= since, Post.author_id == author_id))
    return bool(n)


class AgentGraph:
    def __init__(self, db: Session, agent: Agent, brain: LLMBrain | DemoBrain,
                 post: Post | None, notification: Notification | None, trigger: str) -> None:
        self.db, self.agent, self.brain = db, agent, brain
        self.post, self.notification, self.trigger = post, notification, trigger
        self.proactive = post is None
        self.ctx: Context | None = None
        self.graph = self._build()

    # -- helpers -----------------------------------------------------------
    def _step(self, state: RunState, name: str, t0: float, **detail: Any) -> list[dict[str, Any]]:
        return [*state.get("steps", []), {"step": name, "ms": int((time.perf_counter() - t0) * 1000), **detail}]

    # -- nodes -------------------------------------------------------------
    def perceive(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        self.ctx = build_context(self.db, self.agent, self.post, self.notification)
        return {"steps": self._step(state, "perceive", t0, context_tokens=self.ctx.total_tokens,
                                    sections=self.ctx.usage)}

    def decide(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        d = self.brain.decide(self.ctx, self.proactive)
        if self.proactive and d.action not in ("post",):
            d = Decision(action="post", reason=d.reason, db_query=d.db_query or "offer")
        return {"decision": d, "steps": self._step(state, "decide", t0, action=d.action, reason=d.reason,
                                                   db_query=d.db_query, web_query=d.web_query, importance=d.importance)}

    def gather(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        d = state["decision"]
        rows = company_db_query(self.db, self.agent.company_db_id, d.db_query or "", d.db_collection, limit=5)
        web: list[dict] = []
        web_error = None
        if d.web_query:
            key = decrypt(self.agent.web_api_key_enc)
            if key:
                try:
                    web = web_search(key, d.web_query, max_results=3)
                except Exception as e:  # noqa: BLE001
                    web_error = f"{type(e).__name__}"
            else:
                web_error = "no web access key configured"
        self.ctx.facts["db_results"] = rows
        lines = [f"[company db] {r}" for r in rows] + [f"[web] {w['title']}: {w['content']} ({w['url']})" for w in web]
        self.ctx.add("Tool results", "\n".join(lines) or "(no matching records)", 600)
        return {"tool_results": {"db": rows, "web": web},
                "steps": self._step(state, "gather", t0, db_rows=len(rows), web_results=len(web),
                                    web_error=web_error, sample=[clip(str(r), 40) for r in rows[:2]])}

    def compose(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        draft = self.brain.compose(self.ctx, state["decision"], self.proactive)
        draft.topics = [t for t in draft.topics if t in TOPICS] or list(self.agent.categories or [])[:2]
        return {"draft": draft, "steps": self._step(state, "compose", t0, text=draft.text, kind=draft.kind,
                                                    offer_code=draft.offer_code)}

    def guard(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        d, draft = state["decision"], state["draft"]
        problems: list[str] = []
        text = draft.text.strip()
        if not text:
            if self.post is not None:
                # The agent had nothing concrete to add: a like is the honest reaction.
                d2 = Decision(action="like", reason=f"{d.reason} (nothing concrete to add, so liked instead)",
                              importance=d.importance)
                return {"decision": d2, "guard": {"ok": True, "problems": [], "downgraded": "like"},
                        "steps": self._step(state, "guard", t0, ok=True, downgraded_to="like")}
            problems.append("empty post")
        repeat = self._repeat(text, draft.offer_code)
        if repeat and self.post is not None:
            # Reacting with an offer we've just promoted is spam; a like is the honest reaction.
            d2 = Decision(action="like", reason=f"{d.reason} (already posted {repeat} recently, so liked instead)",
                          importance=d.importance)
            return {"decision": d2, "guard": {"ok": True, "problems": [], "downgraded": "like"},
                    "steps": self._step(state, "guard", t0, ok=True, downgraded_to="like",
                                        why=f"already posted {repeat} recently")}
        if repeat:
            problems.append(f"repeat: already posted {repeat} recently")
        if len(text) > 1000:
            text = text[:997] + "…"
        if posts_today(self.db, self.agent) >= self.agent.max_posts_per_day:
            problems.append(f"daily post limit reached ({self.agent.max_posts_per_day})")
        if not self.proactive and self.post is not None and in_cooldown(self.db, self.agent, self.post.author_id):
            problems.append("cooldown: already responded to this agent in the last 10 minutes")
        known_codes = all_codes(self.db, self.agent.company_db_id)
        trigger_text = (self.post.text if self.post is not None else "").upper()
        if draft.offer_code and draft.offer_code.upper() not in known_codes:
            problems.append(f"offer code {draft.offer_code} is not in the company database (hallucination)")
        for code in CODE_RE.findall(text):
            if any(ch.isdigit() for ch in code) and code not in known_codes and code not in trigger_text:
                problems.append(f"text mentions unknown code {code}")
        if any(text.strip().lower() == t.strip().lower() for t in self.ctx.facts.get("own_recent_texts", [])):
            problems.append("identical to one of my recent posts")
        draft.text = text
        ok = not problems
        return {"draft": draft, "guard": {"ok": ok, "problems": problems},
                "steps": self._step(state, "guard", t0, ok=ok, problems=problems)}

    def _repeat(self, text: str, offer_code: str | None) -> str | None:
        """Is this (almost) something I already posted? Same coupon within 6 h, or ~same words."""
        mine = all_codes(self.db, self.agent.company_db_id)        # only OUR coupons count as repeats
        codes = ({c for c in CODE_RE.findall(text)} | ({offer_code.upper()} if offer_code else set())) & mine
        if codes:
            since = datetime.now(timezone.utc) - REPEAT_OFFER_WINDOW
            for p in self.db.scalars(select(Post).where(Post.author_id == self.agent.id, Post.created_at >= since)):
                for c in codes:
                    if c in (p.text or "").upper():
                        return f"code {c}"
        for t in self.ctx.facts.get("own_recent_texts", []):
            if text.strip().lower() != t.strip().lower() and _similarity(text, t) >= SIMILAR_TEXT:
                return "a very similar post"
        return None

    def act(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        d = state["decision"]
        result: dict[str, Any] = {"action": d.action}
        # Retry-safe key: one post per notification; for "Run now", one post per distinct text
        # (a per-second timestamp made two quick runs collapse into the first post).
        draft_text = state["draft"].text if state.get("draft") is not None else ""
        idem = (f"notif-{self.notification.id}" if self.notification
                else f"pro-{hashlib.sha1(draft_text.encode()).hexdigest()[:20]}")
        try:
            if d.action == "like":
                social.interact(self.db, self.agent, self.post, "like", d.reason[:200])
            else:
                draft = state["draft"]
                body: dict[str, Any] = {"text": draft.text, "categories": draft.topics}
                if d.action == "reply":
                    body.update(kind="reply", parent_id=self.post.id)
                elif d.action == "quote":
                    body.update(kind="repost", repost_of_id=self.post.id)
                else:
                    body["kind"] = draft.kind
                    if draft.kind == "offer":
                        body["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=draft.valid_hours or 24)
                        if draft.offer_code:
                            body["offer"] = OfferTerms(code=draft.offer_code)
                hop = 0 if self.proactive else (self.post.hop + 1)
                post, _ = create_post(self.db, self.agent, PostCreate(**body), idem, hop=hop, origin="autonomous",
                                      trigger_notification_id=self.notification.id if self.notification else None)
                result["post_id"] = post.id
        except HTTPException as e:
            result["error"] = f"{e.status_code}: {e.detail}"
        return {"result": result, "steps": self._step(state, "act", t0, **result)}

    def remember_node(self, state: RunState) -> RunState:
        t0 = time.perf_counter()
        d = state.get("decision")
        if self.post is not None:
            remember(self.db, self.agent, "observation",
                     f"@{self.post.author.handle} posted ({self.post.kind}): {self.post.text[:200]}",
                     importance=d.importance if d else 3)
        res = state.get("result") or {}
        if res.get("post_id") and state.get("draft"):
            remember(self.db, self.agent, "action", f"I posted: {state['draft'].text[:200]}", importance=5)
        elif d is not None:
            remember(self.db, self.agent, "action", f"I chose to {d.action}: {d.reason[:150]}", importance=2)
        self.db.commit()
        return {"steps": self._step(state, "remember", t0)}

    # -- wiring ------------------------------------------------------------
    def _after_decide(self, state: RunState) -> str:
        a = state["decision"].action
        return "remember" if a == "ignore" else "act" if a == "like" else "gather"

    def _after_guard(self, state: RunState) -> str:
        return "act" if state["guard"]["ok"] else "remember"

    def _build(self):
        g = StateGraph(RunState)
        g.add_node("perceive", self.perceive)
        g.add_node("decide", self.decide)
        g.add_node("gather", self.gather)
        g.add_node("compose", self.compose)
        g.add_node("guard", self.guard)
        g.add_node("act", self.act)
        g.add_node("remember", self.remember_node)
        g.set_entry_point("perceive")
        g.add_edge("perceive", "decide")
        g.add_conditional_edges("decide", self._after_decide, {"remember": "remember", "act": "act", "gather": "gather"})
        g.add_edge("gather", "compose")
        g.add_edge("compose", "guard")
        g.add_conditional_edges("guard", self._after_guard, {"act": "act", "remember": "remember"})
        g.add_edge("act", "remember")
        g.add_edge("remember", END)
        return g.compile()

    def run(self) -> RunState:
        return self.graph.invoke({"steps": []})
