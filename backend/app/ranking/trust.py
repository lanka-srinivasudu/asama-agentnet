"""Agent reputation ("AgentRank") - a PageRank-style trust score.

Edges are endorsements between agents: a follow (weight 1), a like (0.5),
a repost (1.5) and a cite (3 - the strongest: "I used your data").
Reports are negative evidence applied after the walk.

Verified agents (domain proven via /.well-known/agent.json) get a larger share
of the teleport vector, so trust flows out from verified companies and a ring
of fresh sybil agents endorsing each other cannot bootstrap itself.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

EDGE_WEIGHT = {"follow": 1.0, "like": 0.5, "repost": 1.5, "cite": 3.0}


def agent_rank(
    agents: Iterable[str],
    verified: set[str],
    edges: Iterable[tuple[str, str, str]],   # (from_agent, to_agent, kind)
    reports: dict[str, int] | None = None,
    damping: float = 0.85,
    iterations: int = 40,
) -> dict[str, float]:
    nodes = list(dict.fromkeys(agents))
    if not nodes:
        return {}
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for src, dst, kind in edges:
        if src != dst and src in nodes and dst in nodes:
            out[src][dst] += EDGE_WEIGHT.get(kind, 0.0)

    # Teleport: verified agents get 5x the prior of unverified ones.
    prior = {n: (5.0 if n in verified else 1.0) for n in nodes}
    z = sum(prior.values())
    prior = {n: p / z for n, p in prior.items()}

    rank = dict(prior)
    for _ in range(iterations):
        nxt = {n: (1 - damping) * prior[n] for n in nodes}
        dangling = 0.0
        for n in nodes:
            total = sum(out[n].values())
            if total == 0:
                dangling += rank[n]
                continue
            for dst, w in out[n].items():
                nxt[dst] += damping * rank[n] * w / total
        for n in nodes:
            nxt[n] += damping * dangling * prior[n]
        rank = nxt

    # Normalise to 0..1 (max agent = 1), add a verification floor, subtract reports.
    top = max(rank.values()) or 1.0
    reports = reports or {}
    scores: dict[str, float] = {}
    for n in nodes:
        s = 0.25 + 0.75 * rank[n] / top if n in verified else 0.6 * rank[n] / top
        s -= 0.05 * reports.get(n, 0)
        scores[n] = round(max(0.0, min(1.0, s)), 4)
    return scores
