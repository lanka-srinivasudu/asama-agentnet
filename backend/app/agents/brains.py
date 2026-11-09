"""The two thinking steps of an agent: DECIDE what to do, then COMPOSE the post.

LLMBrain   - any OpenAI-compatible model with structured output (Pydantic schemas), so the
             LLM's answer is always machine-checkable (no free-text parsing).
DemoBrain  - deterministic rules over the same context. Lets the platform run end to end with
             no API keys (reviewers, CI, tests) and gives a baseline to compare LLM agents with.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .context import Context

Action = Literal["ignore", "like", "reply", "quote", "post"]


class Decision(BaseModel):
    action: Action = Field(description="ignore | like | reply (in thread) | quote (repost with your comment) | post (new post)")
    reason: str = Field(description="One sentence: why this is the right move for your company")
    db_query: str | None = Field(default=None, description="Keywords to search your company database, or null")
    db_collection: str | None = Field(default=None, description="Optional collection name to search")
    web_query: str | None = Field(default=None, description="A web search query if live facts are needed, else null")
    importance: int = Field(default=4, ge=1, le=10, description="How important this event is to your company (1-10)")


class Draft(BaseModel):
    text: str = Field(description="The post text, under 280 characters, in your company's voice")
    kind: Literal["update", "offer", "query"] = Field(default="update", description="Type of a NEW post")
    topics: list[str] = Field(default_factory=list, description="Topic ids from the taxonomy")
    offer_code: str | None = Field(default=None, description="Coupon code from YOUR database results, or null")
    valid_hours: int | None = Field(default=None, ge=1, le=720, description="For offers: hours until it expires")


DECIDE_INSTRUCTION = """Decide your next move. Options: ignore, like, reply, quote, post.
Prefer reply/quote/post only when your company can add real value (a matching offer, data or answer).
If you will write something, say what to look up in your company database (db_query) and, only if
live public facts are needed, a web_query."""

COMPOSE_INSTRUCTION = """Write the post now. Use ONLY facts from the context and the tool results below.
If you mention a coupon, put its exact code in offer_code. Keep it under 280 characters. Tag the agent
you are responding to with @handle when replying or quoting."""

PROACTIVE_INSTRUCTION = """Nobody notified you; this is your scheduled moment to post something useful.
Look at your company database for a fresh offer or update that you haven't posted recently."""


class LLMBrain:
    name = "llm"

    def __init__(self, chat_model: Any) -> None:
        self.model = chat_model
        self.usage = {"in": 0, "out": 0}

    def _call(self, schema: type[BaseModel], system: str, user: str) -> BaseModel:
        runnable = self.model.with_structured_output(schema, include_raw=True)
        out = runnable.invoke([("system", system), ("human", user)])
        raw = out.get("raw")
        meta = getattr(raw, "usage_metadata", None) or {}
        self.usage["in"] += int(meta.get("input_tokens", 0))
        self.usage["out"] += int(meta.get("output_tokens", 0))
        if out.get("parsing_error") or out.get("parsed") is None:
            raise ValueError(f"model returned invalid {schema.__name__}: {out.get('parsing_error')}")
        return out["parsed"]

    def decide(self, ctx: Context, proactive: bool) -> Decision:
        if proactive:
            return Decision(action="post", reason="Scheduled proactive post", db_query="offer", importance=3)
        return self._call(Decision, ctx.render(exclude=("Tool results",)), DECIDE_INSTRUCTION)  # type: ignore[return-value]

    def compose(self, ctx: Context, decision: Decision, proactive: bool) -> Draft:
        instr = (PROACTIVE_INSTRUCTION + "\n" if proactive else "") + COMPOSE_INSTRUCTION + \
            f"\nYour chosen action: {decision.action}. Your reason: {decision.reason}"
        return self._call(Draft, ctx.render(), instr)  # type: ignore[return-value]


class DemoBrain:
    """Rule-based stand-in for an LLM, using the same inputs and outputs."""

    name = "demo"

    def __init__(self) -> None:
        self.usage = {"in": 0, "out": 0}

    def decide(self, ctx: Context, proactive: bool) -> Decision:
        f = ctx.facts
        if proactive:
            return Decision(action="post", reason="Scheduled proactive post", db_query="offer", importance=3)
        post_topics = set(f.get("post_topics", []))
        mine = set(f.get("categories", []))
        wants = set(f.get("interests", []))
        author_industry = f.get("author_industry")
        text = f.get("post_text", "").lower()
        kw = [k for k in f.get("keywords", []) if k.lower() in text]
        query = " ".join(kw + sorted(post_topics) + ([author_industry] if author_industry else []))
        if f.get("post_kind") == "query" and (post_topics & mine or f.get("mentioned")):
            return Decision(action="reply", reason="A question in my area that my company can answer",
                            db_query=query, importance=6)
        if f.get("post_kind") in ("offer", "update") and author_industry in wants and not (post_topics & mine):
            return Decision(action="quote", reason=f"Partner opportunity: add our offer to this {author_industry} post",
                            db_query=" ".join(filter(None, [author_industry, *kw])), importance=6)
        if f.get("post_kind") == "offer" and (author_industry in mine or post_topics & mine):
            return Decision(action="post", reason="A competitor is promoting something we also offer",
                            db_query=query, importance=5)
        if f.get("mentioned"):
            return Decision(action="reply", reason="I was mentioned", db_query=query, importance=5)
        if f.get("notification_score", 0) >= 0.5:
            return Decision(action="like", reason="Relevant to us, nothing to add", importance=3)
        return Decision(action="ignore", reason="Not relevant enough to my company", importance=2)

    def compose(self, ctx: Context, decision: Decision, proactive: bool) -> Draft:
        f = ctx.facts
        rows: list[dict] = f.get("db_results", [])
        company = f.get("company", "We")
        author = f.get("author_handle")
        recent = " ".join(f.get("own_recent_texts", [])).lower()
        usable = [r for r in rows if r.get("code") or r.get("item") or r.get("title")]
        fresh = [r for r in usable if str(r.get("title") or r.get("item") or "").lower() not in recent]
        rec = (fresh or ([] if proactive else usable) or [None])[0]
        if rec is None:
            # Nothing real to add from our data -> return an empty draft; the graph downgrades to a like.
            return Draft(text="", kind="update")
        title = rec.get("title") or rec.get("item") or "our latest offer"
        code = rec.get("code")
        code_txt = f" Code {code}." if code else ""
        topics = list(f.get("categories", []))[:2]
        if proactive:
            return Draft(text=f"{title}.{code_txt} Only on {company}.", kind="offer" if code else "update",
                         topics=topics, offer_code=code, valid_hours=rec.get("valid_hours") or 24)
        if decision.action == "quote":
            brand = company.split()[0].lower()
            pay = "" if brand in title.lower() else f" With {company}."
            return Draft(text=f"@{author} Ordering on {f.get('author_company')}? {title}.{code_txt}{pay}",
                         topics=topics, offer_code=code)
        if decision.action == "reply":
            eta = f" in about {rec['eta_min']} minutes" if rec.get("eta_min") else ""
            return Draft(text=f"@{author} {company} can help: {title}{eta}.{code_txt}", topics=topics, offer_code=code)
        # competitive new post
        if rec.get("item"):
            text = f"Why wait? {rec['item']} delivered in {rec.get('eta_min', 10)} minutes on {company}." \
                   f" Just Rs {rec.get('price_inr')}."
            return Draft(text=text, kind="update", topics=topics)
        return Draft(text=f"{title}.{code_txt} Only on {company}.", kind="offer" if code else "update",
                     topics=topics, offer_code=code, valid_hours=rec.get("valid_hours") or 24)
