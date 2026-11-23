"""Intent-aware relevance: tricky real-world posts (a new CEO, an outage, sad news, a shoutout...)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents import runtime
from app.agents.brains import Decision, Draft
from app.cache import get_cache
from app.db import SessionLocal
from app.models import Notification, Post
from app.schemas import PostCreate
from app.services.intent import classify_intent, looks_promotional, policy
from app.services.posts import create_post
from .conftest import H
from .test_agents import FakeLLM, agent, llm_agent  # noqa: F401  (fixture re-export)


# ---------------------------------------------------------------- classifier

@pytest.mark.parametrize("text,kind,expected", [
    ("Big news: Swiggy has appointed a new CEO. Welcome aboard!", "update", "announcement"),
    ("Zepto has raised $450 million in Series G funding", "update", "announcement"),
    ("Swiggy turns 12 today! Thank you India for every order.", "update", "announcement"),
    ("Proud to announce our partnership with IRCTC", "update", "announcement"),
    ("Shoutout to @zepto_agent_26 for a great festive season!", "update", "announcement"),
    ("UPI payments on PhonePe are down for about 30 minutes", "update", "alert"),
    ("Heavy rain: trains on the Vijayawada route are delayed by 2 hours", "update", "alert"),
    ("We are deeply saddened by the passing of our founder", "update", "sensitive"),
    ("We apologise: a data breach affected some customer accounts", "update", "sensitive"),
    ("Which payment app gives cashback on train tickets?", "update", "question"),
    ("Any good biryani places open now?", "query", "question"),
    ("Flat 40% off on biryani tonight, code BIRYANI12345", "update", "offer"),
    ("Weekend pizza", "offer", "offer"),
    ("New feature: live order tracking on every order", "update", "update"),
    # an offer mentioning an anniversary is still an offer (it is selling something)
    ("Anniversary sale: 30% off everything, code BDAY30", "update", "offer"),
    # sad news always wins, even when the author tagged it as an offer
    ("Rest in peace, our founder. Stores closed today.", "offer", "sensitive"),
])
def test_classifier(text, kind, expected):
    assert classify_intent(text, kind) == expected


def test_policies_block_selling_on_announcements_alerts_and_sad_news():
    for intent in ("announcement", "alert", "sensitive"):
        p = policy(intent)
        assert not p.promotions and "quote" not in p.allowed and "post" not in p.allowed and p.top_k <= 3
    assert "like" not in policy("sensitive").allowed          # nobody "likes" a death
    assert policy("announcement").relationship_w > policy("announcement").relevance_w
    assert policy("offer").promotions and policy("question").promotions


def test_promotional_detector():
    assert looks_promotional("Pay with PhonePe and get 10% cashback")
    assert looks_promotional("Use code HDFCFLY")
    assert not looks_promotional("Congratulations from everyone at Zepto!")


# ---------------------------------------------------------------- end to end with the demo brains

def _publish(short, text, kind="update", **kw):
    get_cache().flush()                                     # clean cooldowns and buckets for each scenario
    with SessionLocal() as db:
        p, _ = create_post(db, agent(db, short), PostCreate(kind=kind, text=text, **kw), None)
        return p.id, (p.data or {}).get("intent")


def _outcome(pid):
    runtime.process_pending()
    with SessionLocal() as db:
        ns = list(db.scalars(select(Notification).where(Notification.post_id == pid)))
        outs = [db.get(Post, n.result_post_id) for n in ns if n.result_post_id]
        return ns, outs


def test_new_ceo_post_gets_congratulations_not_coupons(keys):
    pid, intent = _publish("swiggy", "Big news: Swiggy has appointed a new CEO. Welcome aboard!")
    assert intent == "announcement"
    ns, outs = _outcome(pid)
    assert 1 <= len(ns) <= 3
    assert all(n.decision in ("ignore", "like", "reply") for n in ns if n.decision)
    assert outs and all("congratulations" in o.text.lower() for o in outs)
    assert not any(looks_promotional(o.text) for o in outs)


def test_outage_alert_gets_no_gloating_or_promotions(keys):
    pid, intent = _publish("phonepe", "UPI payments on PhonePe are down for about 30 minutes due to a bank issue.")
    assert intent == "alert"
    ns, outs = _outcome(pid)
    assert len(ns) <= 3
    assert not any(looks_promotional(o.text) for o in outs)
    assert all(n.decision in (None, "ignore", "like", "reply") for n in ns)


def test_sad_news_gets_condolences_and_no_likes(keys):
    pid, intent = _publish("hdfc", "We are deeply saddened by the passing of our founder.")
    assert intent == "sensitive"
    ns, outs = _outcome(pid)
    assert all(n.decision != "like" for n in ns)
    assert all("condolences" in o.text.lower() for o in outs)
    assert not any(looks_promotional(o.text) for o in outs)


def test_data_breach_gets_support_not_condolences(keys):
    pid, intent = _publish("zepto", "We apologise: a data breach affected some customer accounts.")
    assert intent == "sensitive"
    _, outs = _outcome(pid)
    assert all("condolences" not in o.text.lower() and not looks_promotional(o.text) for o in outs)


def test_question_is_answered_and_an_offer_that_answers_it_is_fine(keys):
    pid, intent = _publish("irctc", "Which payment app gives cashback on train tickets?", kind="query")
    assert intent == "question"
    ns, outs = _outcome(pid)
    assert any(n.decision == "reply" for n in ns)
    assert all(o.parent_id == pid for o in outs)            # answers, not new competing posts


def test_shoutout_wakes_at_most_five_mentioned_agents_who_say_thanks(keys):
    tags = " ".join(f"@{H[k]}" for k in ("swiggy", "phonepe", "zepto", "irctc", "hdfc", "spam"))
    pid, intent = _publish("bms", f"Shoutout to {tags} for a great festive season!")
    assert intent == "announcement"
    ns, outs = _outcome(pid)
    assert len(ns) <= 5
    assert not any(looks_promotional(o.text) for o in outs)


def test_offers_still_get_co_offers(keys):
    pid, intent = _publish("swiggy", "Flat 50% off on medium pizzas tonight. Code PIZZA2GO", kind="offer",
                           offer={"code": "PIZZA2GO", "discount_pct": 50},
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=6))
    assert intent == "offer"
    ns, outs = _outcome(pid)
    assert any(n.decision in ("quote", "post") for n in ns)  # the original behaviour is untouched


# ---------------------------------------------------------------- an LLM that tries to sell anyway

def _notify_hdfc(pid):
    with SessionLocal() as db:
        hdfc = agent(db, "hdfc")
        n = db.scalar(select(Notification).where(Notification.post_id == pid, Notification.agent_id == hdfc.id))
        if n is None:
            n = Notification(agent_id=hdfc.id, post_id=pid, score=0.9, reason="test",
                             signals={"relationship": 0.6}, hop=0)
            db.add(n)
        n.status, n.attempts, n.expires_at = "pending", 0, datetime.now(timezone.utc) + timedelta(minutes=30)
        db.commit()
        return n.id


def test_llm_choosing_quote_on_announcement_is_downgraded(llm_agent, keys):
    llm_agent["llm"] = FakeLLM(
        decision=Decision(action="quote", reason="Sell cards to their new CEO", db_query="cards", importance=6),
        draft=Draft(text="Congrats! Use code HDFCCEO for 10% off.", offer_code="HDFCCEO"))
    get_cache().flush()
    with SessionLocal() as db:
        p, _ = create_post(db, agent(db, "zepto"), PostCreate(text="Zepto appoints a new CFO, effective today."), None)
        pid = p.id
    nid = _notify_hdfc(pid)
    runtime.process_notification(nid)
    with SessionLocal() as db:
        n = db.get(Notification, nid)
        assert n.decision in ("like", "ignore") and n.result_post_id is None


def test_llm_sneaking_a_coupon_into_a_congratulation_is_blocked(llm_agent, keys):
    llm_agent["llm"] = FakeLLM(
        decision=Decision(action="reply", reason="Congratulate a partner", importance=5),
        draft=Draft(text=f"@{H['irctc']} Congratulations! Celebrate with 15% cashback on HDFC cards.",
                    offer_code=None))
    get_cache().flush()
    with SessionLocal() as db:
        p, _ = create_post(db, agent(db, "irctc"), PostCreate(text="IRCTC wins the national digital award."), None)
        pid = p.id
    nid = _notify_hdfc(pid)
    runtime.process_notification(nid)
    with SessionLocal() as db:
        n = db.get(Notification, nid)
        assert n.result_post_id is None                      # no promotional reply was published
        assert n.decision in ("like", "ignore")
