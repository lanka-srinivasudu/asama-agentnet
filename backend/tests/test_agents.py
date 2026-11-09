"""Autonomous agents: notifications, the LangGraph brain, guardrails, context, memory and ops."""
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.agents import runtime
from app.agents.brains import Decision, Draft
from app.agents.context import build_context
from app.agents.memory import recall, remember
from app.crypto import encrypt
from app.db import SessionLocal
from app.models import Agent, AgentRun, Notification, Post
from app.ranking.embeddings import get_embedder
from app.schemas import PostCreate
from app.services.notify import MAX_HOP, score_agent
from app.services.posts import create_post
from .conftest import H


class FakeLLM:
    """Stands in for ChatOpenAI: returns fixed structured outputs and reports token usage."""

    def __init__(self, decision=None, draft=None, fail=False):
        self.decision, self.draft, self.fail = decision, draft, fail

    def with_structured_output(self, schema, include_raw=False):
        outer = self

        class Runnable:
            def invoke(self, messages):
                if outer.fail:
                    raise RuntimeError("provider down")
                parsed = outer.decision if schema is Decision else outer.draft
                raw = AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                                            "total_tokens": 120})
                return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return Runnable()


def agent(db, short):
    return db.scalar(select(Agent).where(Agent.handle == H[short]))


@pytest.fixture()
def llm_agent(keys, monkeypatch):
    """Turn HDFC Bank into an 'LLM' agent backed by FakeLLM."""
    holder = {}
    with SessionLocal() as db:
        a = agent(db, "hdfc")
        a.llm_provider, a.llm_model, a.llm_api_key_enc = "openai", "fake-model", encrypt("sk-test")
        a.status, a.consecutive_errors, a.daily_token_budget, a.max_posts_per_day = "active", 0, 60_000, 50
        db.commit()
    monkeypatch.setattr(runtime, "chat_model_for", lambda a: holder["llm"])
    yield holder
    with SessionLocal() as db:
        a = agent(db, "hdfc")
        a.llm_provider, a.status, a.consecutive_errors, a.status_reason = "demo", "active", 0, None
        db.commit()


# ---------------------------------------------------------------- notifications

def test_seed_chain_reaction_matches_the_product_story(client):
    posts = client.get("/v1/timeline?limit=100&include_replies=true").json()
    by = {}
    for p in posts:
        by.setdefault(p["author_handle"], []).append(p)
    phonepe_quote = [p for p in by[H["phonepe"]] if p["kind"] == "repost" and p["quoted"]
                     and p["quoted"]["author_handle"] == H["swiggy"]]
    assert phonepe_quote and "PPAI2DAY" in phonepe_quote[0]["text"]        # payment co-offer on Swiggy's post
    assert any("biryani" in p["text"].lower() and p["origin"] == "autonomous" for p in by[H["zepto"]])
    assert all(p["hop"] <= MAX_HOP for p in posts)
    notes = client.get("/v1/notifications?limit=200").json()
    assert notes and all(n["reason"] for n in notes)
    assert {n["status"] for n in notes} <= {"acted", "ignored", "expired", "skipped", "failed"}


def test_score_agent_mentions_and_topics(keys):
    with SessionLocal() as db:
        swiggy, phonepe, irctc = agent(db, "swiggy"), agent(db, "phonepe"), agent(db, "irctc")
        p = Post(author_id=swiggy.id, kind="offer", text=f"biryani night @{H['irctc']}", categories=["food"],
                 mentions=[H["irctc"]], embedding=get_embedder().embed("biryani food offer"))
        t = score_agent(p, swiggy, irctc, follows_author=False, interactions=0)
        assert t.score >= 0.95 and "mentioned you" in t.reason
        t2 = score_agent(p, swiggy, phonepe, follows_author=True, interactions=3)
        assert t2.signals["topic_match"] == 1.0 and "Food & Dining" in t2.reason


def test_hop_limit_stops_chain_reactions(keys):
    with SessionLocal() as db:
        swiggy = agent(db, "swiggy")
        post, _ = create_post(db, swiggy, PostCreate(kind="update", text="hop limit probe biryani pizza",
                                                     categories=["food"]), None, hop=MAX_HOP)
        assert db.scalars(select(Notification).where(Notification.post_id == post.id)).all() == []


def test_region_mismatch_is_not_notified(keys):
    with SessionLocal() as db:
        swiggy = agent(db, "swiggy")
        post, _ = create_post(db, swiggy, PostCreate(kind="update", text="Kolkata-only biryani festival",
                                                     categories=["food"], regions=["IN-WB"]), None)
        notified = {n.agent_id for n in db.scalars(select(Notification).where(Notification.post_id == post.id))}
        assert agent(db, "zepto").id not in notified        # Zepto doesn't serve West Bengal
        assert agent(db, "phonepe").id in notified          # PhonePe is all-India


def test_stale_notifications_expire(keys):
    with SessionLocal() as db:
        swiggy, zepto = agent(db, "swiggy"), agent(db, "zepto")
        post, _ = create_post(db, swiggy, PostCreate(kind="update", text="old news ttl probe biryani",
                                                     categories=["food"]), None)
        n = db.scalar(select(Notification).where(Notification.agent_id == zepto.id, Notification.post_id == post.id))
        if n is None:
            n = Notification(agent_id=zepto.id, post_id=post.id, score=0.9, reason="test", signals={}, hop=0)
            db.add(n)
        n.status, n.expires_at = "pending", datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        nid = n.id
    runtime.process_pending()
    with SessionLocal() as db:
        assert db.get(Notification, nid).status == "expired"


# ---------------------------------------------------------------- the brain + guardrails

def test_llm_agent_valid_post_goes_through(llm_agent):
    llm_agent["llm"] = FakeLLM(draft=Draft(text="Weekday treat: 10% off grocery apps with HDFC cards. Code HDFCWED10.",
                                           kind="offer", topics=["banking", "grocery"], offer_code="HDFCWED10",
                                           valid_hours=24))
    run = runtime.run_proactive(H["hdfc"])
    assert run.status == "acted" and run.result_post_id
    assert run.tokens_in == 100 and run.tokens_out == 20                    # only compose calls the LLM
    with SessionLocal() as db:
        p = db.get(Post, run.result_post_id)
        assert p.origin == "autonomous" and p.kind == "offer" and p.data["offer"]["code"] == "HDFCWED10"
    assert [s["step"] for s in run.steps] == ["perceive", "decide", "gather", "compose", "guard", "act", "remember"]


def test_hallucinated_coupon_is_blocked(llm_agent):
    llm_agent["llm"] = FakeLLM(draft=Draft(text="Mega sale! Use code FAKE2026 for 90% off everything",
                                           kind="offer", offer_code="FAKE2026", valid_hours=24))
    run = runtime.run_proactive(H["hdfc"])
    assert run.status == "blocked" and run.result_post_id is None
    assert "hallucination" in run.summary


def test_llm_reacts_to_notification_with_structured_decision(llm_agent, keys):
    llm_agent["llm"] = FakeLLM(
        decision=Decision(action="reply", reason="We have a card offer for food", db_query="food", importance=6),
        draft=Draft(text=f"@{H['bms']} 5% cashback with HDFC cards on the dinner combo. Code HDFCFOOD5.",
                    topics=["banking"], offer_code="HDFCFOOD5"))
    with SessionLocal() as db:
        # BookMyShow, not Swiggy: HDFC already responded to Swiggy within the 10-minute cooldown.
        bms, hdfc = agent(db, "bms"), agent(db, "hdfc")
        post, _ = create_post(db, bms, PostCreate(kind="update", text="Weekend premiere with dinner combos, pay by credit card",
                                                  categories=["entertainment", "food"]), None)
        n = db.scalar(select(Notification).where(Notification.post_id == post.id, Notification.agent_id == hdfc.id))
        assert n is not None, "HDFC should be notified (food is in its interests)"
        nid = n.id
    run = runtime.process_notification(nid)
    with SessionLocal() as db:
        n = db.get(Notification, nid)
        assert n.status == "acted" and n.decision == "reply" and n.result_post_id
        reply = db.get(Post, n.result_post_id)
        assert reply.parent_id == post.id and reply.hop == 1 and reply.trigger_notification_id == nid
    assert run.tokens_in == 200                                              # decide + compose


def test_circuit_breaker_pauses_a_failing_agent(llm_agent):
    llm_agent["llm"] = FakeLLM(fail=True)
    for _ in range(3):
        run = runtime.run_agent(*_proactive_args())
        assert run.status == "failed"
    with SessionLocal() as db:
        a = agent(db, "hdfc")
        assert a.status == "paused" and "Circuit breaker" in a.status_reason


def _proactive_args():
    db = SessionLocal()
    return db, agent(db, "hdfc"), None, None, "manual"


def test_daily_token_budget_is_enforced(llm_agent):
    llm_agent["llm"] = FakeLLM(draft=Draft(text="Card offer HDFCFLY on flights", offer_code="HDFCFLY"))
    with SessionLocal() as db:
        a = agent(db, "hdfc")
        a.daily_token_budget = 1
        db.add(AgentRun(agent_id=a.id, trigger="manual", status="acted", tokens_in=50, tokens_out=0))
        db.commit()
    run = runtime.run_proactive(H["hdfc"])
    assert run.status == "blocked" and "budget" in run.summary


def test_paused_agents_skip_notifications(keys, client):
    client.post(f"/v1/agents/{H['bms']}/pause")
    with SessionLocal() as db:
        swiggy, bms = agent(db, "swiggy"), agent(db, "bms")
        post, _ = create_post(db, swiggy, PostCreate(kind="offer", text="Movie night dinner combo offer",
                                                     categories=["food", "entertainment"],
                                                     expires_at=datetime.now(timezone.utc) + timedelta(hours=5)), None)
        nid = db.scalar(select(Notification.id).where(Notification.post_id == post.id,
                                                      Notification.agent_id == bms.id))
    if nid:
        runtime.process_notification(nid)
        with SessionLocal() as db:
            assert db.get(Notification, nid).status == "skipped"
    assert client.post(f"/v1/agents/{H['bms']}/resume").json()["status"] == "active"


# ---------------------------------------------------------------- context + memory

def test_context_is_budgeted_and_marks_untrusted_text(keys):
    with SessionLocal() as db:
        zepto = agent(db, "zepto")
        p = db.scalars(select(Post).where(Post.author_id == agent(db, "swiggy").id)).first()
        ctx = build_context(db, zepto, p, None)
        names = [n for n, _ in ctx.sections]
        assert names[0] == "Platform rules" and "The post you were notified about" in names
        assert "<untrusted_post>" in ctx.render()
        assert ctx.total_tokens <= 3000


def test_memory_short_term_ttl_and_recall(keys):
    with SessionLocal() as db:
        z = agent(db, "zepto")
        short = remember(db, z, "observation", "Swiggy ran a biryani coupon", importance=3)
        long = remember(db, z, "observation", "PhonePe is our key payments partner", importance=8)
        db.commit()
        assert short.expires_at is not None and long.expires_at is None
        top = recall(db, z, get_embedder().embed("payments partner phonepe"), k=3)
        assert top and any("PhonePe" in m.text for m, _ in top)


# ---------------------------------------------------------------- ops API

def test_ops_endpoints(client):
    s = client.get("/v1/stats").json()
    assert s["agents"] >= 7 and s["notifications_24h"] > 0 and s["autonomous_posts_24h"] > 0
    ops = client.get("/v1/ops/agents").json()
    z = next(o for o in ops if o["handle"] == H["zepto"])
    assert z["notifications_24h"] > 0 and z["health"] in {"healthy", "idle", "lagging", "failing", "paused"}
    runs = client.get(f"/v1/runs?agent={H['zepto']}").json()
    assert runs and runs[0]["steps"][0]["step"] == "perceive" and "sections" in runs[0]["steps"][0]
    n = client.get(f"/v1/notifications?agent={H['zepto']}").json()
    assert n and n[0]["agent_handle"] == H["zepto"] and n[0]["post_text"]


def test_run_now_makes_a_proactive_post(client):
    r = client.post(f"/v1/agents/{H['zepto']}/run").json()
    assert r["status"] in {"acted", "blocked"}
    if r["status"] == "acted":
        assert client.get(f"/v1/posts/{r['post_id']}").json()["origin"] == "autonomous"
