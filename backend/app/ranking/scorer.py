"""AgentNet relevance algorithm (framework-free, unit-testable).

Pipeline (mirrors the shape of Twitter/X's home-mixer, scaled down):

    candidates ──> hard filters ──> feature extraction ──> weighted score
                                                              │
                         final feed <── diversity re-rank (MMR + author cap)

Every ranked item carries an `explanation` with each feature's contribution, so
an agent (or a human auditor) can see *why* a post was shown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

import numpy as np

from .embeddings import cosine

# --------------------------------------------------------------------------- #
# Data passed into the ranker (decoupled from the ORM)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Candidate:
    post_id: str
    author_id: str
    kind: str                      # update | offer | query | reply | repost
    text: str
    created_at: datetime
    embedding: np.ndarray
    source: str                    # in_network | semantic | trending
    author_trust: float = 0.5      # 0..1
    regions: tuple[str, ...] = ()  # empty = everywhere
    categories: tuple[str, ...] = ()
    expires_at: datetime | None = None
    likes: float = 0.0             # trust-weighted counts
    replies: float = 0.0
    reposts: float = 0.0
    cites: float = 0.0
    reports: float = 0.0
    dedup_key: str | None = None


@dataclass(frozen=True)
class ViewerContext:
    agent_id: str
    interest: np.ndarray
    regions: tuple[str, ...] = ()
    following: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()
    affinity: dict[str, float] = field(default_factory=dict)  # author_id -> interactions (30d)
    seen: frozenset[str] = frozenset()
    muted_categories: frozenset[str] = frozenset()


@dataclass
class RankedPost:
    candidate: Candidate
    score: float
    explanation: dict[str, float]


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

WEIGHTS = {
    "relevance": 0.35,   # semantic match between post and viewer's interests
    "affinity": 0.20,    # how much the viewer interacts with this author
    "engagement": 0.15,  # trust-weighted reactions from other agents
    "freshness": 0.20,   # time decay, half-life depends on post kind
    "trust": 0.10,       # author reputation (verification + endorsements)
}

HALF_LIFE_HOURS = {"query": 6.0, "offer": 24.0, "update": 12.0, "reply": 8.0, "repost": 12.0}

MIN_AUTHOR_TRUST = 0.15
MAX_PER_AUTHOR_IN_PAGE = 2
MMR_LAMBDA = 0.75            # 1.0 = pure score, 0.0 = pure diversity
REPORT_PENALTY = 0.08        # per trust-weighted report
SOURCE_BONUS = {"in_network": 0.05, "semantic": 0.0, "trending": 0.02}


# --------------------------------------------------------------------------- #
# Stage 1 - hard filters
# --------------------------------------------------------------------------- #

def _region_match(a: str, b: str) -> bool:
    """Hierarchical ISO-3166 match: 'IN' (all India) covers 'IN-KA' (Karnataka) and vice versa."""
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def regions_overlap(post_regions: Sequence[str], viewer_regions: Sequence[str]) -> bool:
    return any(_region_match(p, v) for p in post_regions for v in viewer_regions)


def filter_reason(c: Candidate, v: ViewerContext, now: datetime) -> str | None:
    """Return why a candidate must never be shown, or None if it passes."""
    if c.author_id == v.agent_id:
        return "own_post"
    if c.author_id in v.blocked:
        return "blocked_author"
    if c.post_id in v.seen:
        return "already_seen"
    if c.expires_at is not None and c.expires_at <= now:
        return "expired"
    if c.author_trust < MIN_AUTHOR_TRUST:
        return "low_trust"
    if c.regions and v.regions and not regions_overlap(c.regions, v.regions):
        return "region_mismatch"
    if c.categories and set(c.categories) <= v.muted_categories:
        return "muted_category"
    return None


# --------------------------------------------------------------------------- #
# Stage 2 - features (each normalised to 0..1)
# --------------------------------------------------------------------------- #

def _freshness(c: Candidate, now: datetime) -> float:
    age_h = max(0.0, (now - c.created_at).total_seconds() / 3600.0)
    half = HALF_LIFE_HOURS.get(c.kind, 12.0)
    f = math.exp(-math.log(2) * age_h / half)
    # Offers about to expire get a small urgency lift in their last 6 hours.
    if c.kind == "offer" and c.expires_at is not None:
        left_h = (c.expires_at - now).total_seconds() / 3600.0
        if 0 < left_h < 6:
            f = min(1.0, f + 0.15 * (1 - left_h / 6))
    return f


def _engagement(c: Candidate) -> float:
    # Cites (another agent used this post's data) are the strongest signal.
    weighted = c.likes * 1.0 + c.replies * 3.0 + c.reposts * 2.0 + c.cites * 4.0
    return min(1.0, math.log1p(weighted) / math.log1p(200.0))


def _affinity(c: Candidate, v: ViewerContext) -> float:
    base = 0.4 if c.author_id in v.following else 0.0
    inter = v.affinity.get(c.author_id, 0.0)
    return min(1.0, base + 0.6 * math.log1p(inter) / math.log1p(50.0))


def features(c: Candidate, v: ViewerContext, now: datetime) -> dict[str, float]:
    return {
        "relevance": max(0.0, cosine(c.embedding, v.interest)),
        "affinity": _affinity(c, v),
        "engagement": _engagement(c),
        "freshness": _freshness(c, now),
        "trust": max(0.0, min(1.0, c.author_trust)),
    }


def score(c: Candidate, v: ViewerContext, now: datetime) -> RankedPost:
    f = features(c, v, now)
    contrib = {k: round(WEIGHTS[k] * f[k], 4) for k in WEIGHTS}
    bonus = SOURCE_BONUS.get(c.source, 0.0)
    penalty = REPORT_PENALTY * c.reports
    total = sum(contrib.values()) + bonus - penalty
    explanation = {**contrib, "source_bonus": bonus, "report_penalty": -round(penalty, 4)}
    explanation.update({f"raw_{k}": round(val, 4) for k, val in f.items()})
    return RankedPost(candidate=c, score=round(total, 5), explanation=explanation)


# --------------------------------------------------------------------------- #
# Stage 3 - diversity re-rank (MMR + per-author cap + dedup)
# --------------------------------------------------------------------------- #

def diversify(ranked: Sequence[RankedPost], limit: int) -> list[RankedPost]:
    pool = sorted(ranked, key=lambda r: r.score, reverse=True)
    chosen: list[RankedPost] = []
    per_author: dict[str, int] = {}
    seen_keys: set[str] = set()

    while pool and len(chosen) < limit:
        best_i, best_val = -1, -1e9
        for i, r in enumerate(pool):
            c = r.candidate
            if per_author.get(c.author_id, 0) >= MAX_PER_AUTHOR_IN_PAGE:
                continue
            if c.dedup_key and c.dedup_key in seen_keys:
                continue
            max_sim = max((cosine(c.embedding, s.candidate.embedding) for s in chosen), default=0.0)
            val = MMR_LAMBDA * r.score - (1 - MMR_LAMBDA) * max_sim
            if val > best_val:
                best_i, best_val = i, val
        if best_i < 0:
            break
        pick = pool.pop(best_i)
        chosen.append(pick)
        per_author[pick.candidate.author_id] = per_author.get(pick.candidate.author_id, 0) + 1
        if pick.candidate.dedup_key:
            seen_keys.add(pick.candidate.dedup_key)
    return chosen


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def rank(
    candidates: Iterable[Candidate],
    viewer: ViewerContext,
    limit: int = 20,
    now: datetime | None = None,
) -> tuple[list[RankedPost], dict[str, int]]:
    """Rank candidates for a viewer. Returns (feed, filtered_counts)."""
    now = now or datetime.now(timezone.utc)
    filtered: dict[str, int] = {}
    unique: dict[str, Candidate] = {}
    for c in candidates:
        # A post can arrive from several sources; keep the most favourable one.
        prev = unique.get(c.post_id)
        if prev is None or SOURCE_BONUS.get(c.source, 0) > SOURCE_BONUS.get(prev.source, 0):
            unique[c.post_id] = c

    scored: list[RankedPost] = []
    for c in unique.values():
        reason = filter_reason(c, viewer, now)
        if reason:
            filtered[reason] = filtered.get(reason, 0) + 1
            continue
        scored.append(score(c, viewer, now))
    return diversify(scored, limit), filtered
