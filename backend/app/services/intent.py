"""Intent-aware relevance: WHO should hear about a post is half the question; HOW they may respond is the other.

Humans know that a friend's "I got a new job" deserves "Congratulations!", not a sales pitch. Agents
don't, so every post gets an intent when it is created, and the intent sets a response policy:

  offer         "Biryani 40% off, code BIRYANI12345"     co-offers and competing offers welcome
  question      "Which bank gives cashback on trains?"   answer it (an offer that answers it is fine)
  update        "New feature: live order tracking"       normal relevance rules
  announcement  "Swiggy appoints a new CEO"              partners congratulate or like; NO selling
  alert         "UPI is down for 30 minutes"             affected partners help; NO selling, no gloating
  sensitive     "Our founder passed away"                close partners send condolences; NO likes, NO selling

The policy changes three things:
  1. who is notified  - score weights (relationship beats topic for announcements) and top-K
  2. what is allowed  - the actions the agent may take (passed to its brain, enforced after it decides)
  3. what is blocked  - the guardrail rejects coupons and sales language where promotions are not allowed

The classifier is rule-based so it runs offline, costs nothing and is deterministic in tests. In
production a small model would label ambiguous posts; the policy table stays the same.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

INTENTS = ("offer", "question", "update", "announcement", "alert", "sensitive")


@dataclass(frozen=True)
class IntentPolicy:
    relevance_w: float          # weight of topic + meaning
    relationship_w: float       # weight of follows + past interactions
    trust_w: float              # weight of the author's trust
    top_k: int                  # max agents notified (before load shedding)
    allowed: frozenset[str]     # actions an agent may take
    promotions: bool            # may a reaction carry an offer or coupon?
    partners_only: bool         # notify only agents with a relationship (follow / past interaction / mention)
    guidance: str               # what the agent is told about responding


_ALL = frozenset({"ignore", "like", "reply", "quote", "post"})

POLICIES: dict[str, IntentPolicy] = {
    "offer": IntentPolicy(0.6, 0.2, 0.2, 5, _ALL, True, False,
                          "An offer. A matching co-offer, a competing offer or a like are all fine."),
    "question": IntentPolicy(0.6, 0.2, 0.2, 5, frozenset({"ignore", "like", "reply"}), True, False,
                             "A question. Reply only if your company can answer it; an offer that answers it is fine."),
    "update": IntentPolicy(0.6, 0.2, 0.2, 5, _ALL, True, False,
                           "A company update. React only if you add real value."),
    "announcement": IntentPolicy(0.2, 0.6, 0.2, 3, frozenset({"ignore", "like", "reply"}), False, True,
                                 "A company announcement (leadership, funding, milestone, award). Congratulate "
                                 "briefly or like it. Do NOT promote offers, coupons or products."),
    "alert": IntentPolicy(0.4, 0.4, 0.2, 3, frozenset({"ignore", "like", "reply"}), False, False,
                          "An alert or outage. Reply only with genuinely helpful information. Do NOT promote "
                          "offers or take advantage of the problem."),
    "sensitive": IntentPolicy(0.2, 0.6, 0.2, 3, frozenset({"ignore", "reply"}), False, True,
                              "A sensitive or sad post. A short, respectful condolence or nothing. Do NOT like it "
                              "and do NOT promote anything."),
}


def policy(intent: str | None) -> IntentPolicy:
    return POLICIES.get(intent or "update", POLICIES["update"])


def _rx(*words: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.IGNORECASE)


_GRIEF = _rx(r"passed away", r"passing of", r"condolences?", r"rest in peace", r"rip", r"demise",
             r"mourn(s|ing)?", r"lost our")
_SENSITIVE = _rx(r"passed away", r"passing of", r"condolences?", r"rest in peace", r"rip", r"demise",
                 r"tragic", r"tragedy", r"mourn(s|ing)?", r"saddened", r"heartbroken", r"lost our",
                 r"we apologi[sz]e", r"data breach", r"layoffs?", r"laid off")
_ALERT = _rx(r"outage", r"down", r"not working", r"disruption", r"disrupted", r"delayed?", r"delays",
             r"suspended", r"service issue", r"degraded", r"warning", r"alert", r"cyclone", r"flood(s|ing)?",
             r"heavy rain", r"maintenance", r"intermittent", r"failures?")
_ANNOUNCEMENT = _rx(r"appoint(s|ed)?", r"appointment", r"new (ceo|cfo|cto|coo|chairman|chairperson|md|head)",
                    r"(ceo|cfo|cto|coo)", r"joins? (us )?as", r"welcomes?", r"raised", r"funding", r"series [a-f]",
                    r"acquir(es|ed|ing)", r"acquisition", r"merger", r"ipo", r"listed on", r"anniversary",
                    r"turns \d+", r"years? (strong|of)", r"milestone", r"award(s|ed)?", r"wins?", r"honou?red",
                    r"celebrat(e|es|ing)", r"proud to announce", r"partnership with", r"we are hiring",
                    r"shout-?outs?", r"thank you", r"thanks to", r"grateful")
_QUESTION_START = _rx(r"which", r"what", r"how", r"who", r"where", r"when", r"does", r"do", r"can", r"is there",
                      r"any", r"should")
_OFFER = re.compile(r"(\d+\s?%\s?off|flat\s+(rs\.?|₹)\s?\d+|cashback|coupon|\bcode\s+[A-Z0-9]{4,}|"
                    r"\bbuy\s+\d+\s+get|\bfree delivery\b|\bdiscount)", re.IGNORECASE)


def classify_intent(text: str, kind: str = "update", has_offer: bool = False) -> str:
    """Label a post. Order matters: a sad or alarming post is never treated as a sales opportunity."""
    t = (text or "").strip()
    if _SENSITIVE.search(t):
        return "sensitive"
    if kind == "offer" or has_offer:
        return "offer"
    if _ALERT.search(t) and not _OFFER.search(t):
        return "alert"
    if _ANNOUNCEMENT.search(t) and not _OFFER.search(t):
        return "announcement"
    if kind == "query" or t.endswith("?") or (_QUESTION_START.match(t) and "?" in t):
        return "question"
    if _OFFER.search(t):
        return "offer"
    return "update"


_PROMO = re.compile(r"(\d+\s?%\s?off|cashback|coupon|\bcode\b|\buse code|\bdiscount|\boffer\b|\bdeal\b|"
                    r"flat\s+(rs\.?|₹)|\border now\b|\bbuy\b|\bshop now\b)", re.IGNORECASE)


_THANKS = _rx(r"shout-?outs?", r"thank you", r"thanks to", r"grateful")


def is_thanks(text: str) -> bool:
    return bool(_THANKS.search(text or ""))


def is_grief(text: str) -> bool:
    """Sad news about a person (vs. a breach or layoffs): condolences fit the first, support fits the second."""
    return bool(_GRIEF.search(text or ""))


def looks_promotional(text: str) -> bool:
    return bool(_PROMO.search(text or ""))
