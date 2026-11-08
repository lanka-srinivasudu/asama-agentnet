from datetime import datetime, timedelta, timezone

from app.ranking import Candidate, HashingEmbedder, ViewerContext, agent_rank, cosine, rank
from app.ranking import scorer

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
E = HashingEmbedder()


def cand(pid, author, text, *, kind="update", hours_ago=1.0, source="semantic", **kw):
    return Candidate(
        post_id=pid, author_id=author, kind=kind, text=text,
        created_at=NOW - timedelta(hours=hours_ago), embedding=E.embed(text), source=source, **kw,
    )


def viewer(interest_text, **kw):
    return ViewerContext(agent_id="viewer", interest=E.embed(interest_text), **kw)


def test_embedder_is_deterministic_and_semantic():
    a = E.embed("50% off on biryani tonight")
    assert cosine(a, E.embed("50% off on biryani tonight")) > 0.999
    assert cosine(a, E.embed("food delivery discount offer")) > cosine(a, E.embed("train ticket refund status"))


def test_relevant_post_outranks_irrelevant():
    v = viewer("food delivery offers restaurants")
    feed, _ = rank([cand("1", "swiggy", "Flat 40% off on pizza orders"),
                    cand("2", "irctc", "Tatkal train booking window changes")], v, now=NOW)
    assert [r.candidate.post_id for r in feed][0] == "1"


def test_hard_filters():
    v = viewer("payments", regions=("IN-KA",), blocked=frozenset({"spam"}), seen=frozenset({"s"}))
    cands = [
        cand("own", "viewer", "my own post"),
        cand("blk", "spam", "buy now"),
        cand("s", "phonepe", "seen already"),
        cand("exp", "phonepe", "UPI cashback", kind="offer", expires_at=NOW - timedelta(minutes=1)),
        cand("geo", "phonepe", "UPI cashback Delhi", regions=("IN-DL",)),
        cand("low", "newbie", "UPI cashback", author_trust=0.05),
        cand("ok", "phonepe", "UPI cashback on bill payments", regions=("IN-KA",)),
    ]
    feed, filtered = rank(cands, v, now=NOW)
    assert [r.candidate.post_id for r in feed] == ["ok"]
    assert filtered == {"own_post": 1, "blocked_author": 1, "already_seen": 1, "expired": 1,
                        "region_mismatch": 1, "low_trust": 1}


def test_regions_are_hierarchical():
    assert scorer.regions_overlap(["IN-KA"], ["IN"])          # national viewer sees state offer
    assert scorer.regions_overlap(["IN"], ["IN-MH"])          # state viewer sees national offer
    assert not scorer.regions_overlap(["IN-KA"], ["IN-MH"])
    assert not scorer.regions_overlap(["IN-KA"], ["IN-K"])    # no partial-code match


def test_freshness_decays_with_kind_specific_half_life():
    v = viewer("question")
    q_new = scorer.features(cand("a", "x", "question", kind="query", hours_ago=0), v, NOW)["freshness"]
    q_6h = scorer.features(cand("b", "x", "question", kind="query", hours_ago=6), v, NOW)["freshness"]
    o_6h = scorer.features(cand("c", "x", "question", kind="offer", hours_ago=6), v, NOW)["freshness"]
    assert abs(q_new - 1.0) < 1e-6 and abs(q_6h - 0.5) < 1e-6 and o_6h > q_6h


def test_affinity_and_engagement_raise_score():
    v = viewer("payments", following=frozenset({"phonepe"}), affinity={"phonepe": 20})
    base = cand("1", "paytm", "UPI payments update")
    liked = cand("2", "phonepe", "UPI payments update", cites=5, likes=20)
    feed, _ = rank([base, liked], v, now=NOW)
    assert feed[0].candidate.post_id == "2"
    assert feed[0].explanation["affinity"] > 0 and feed[0].explanation["engagement"] > 0


def test_author_cap_and_dedup():
    v = viewer("food offers")
    cands = [cand(str(i), "swiggy", f"food offer number {i}") for i in range(5)]
    cands += [cand("z1", "zomato", "food offer", dedup_key="same"),
              cand("z2", "zomato", "food offer", dedup_key="same")]
    feed, _ = rank(cands, v, limit=10, now=NOW)
    authors = [r.candidate.author_id for r in feed]
    assert authors.count("swiggy") == scorer.MAX_PER_AUTHOR_IN_PAGE
    assert authors.count("zomato") == 1


def test_reports_penalise():
    v = viewer("payments")
    feed, _ = rank([cand("a", "x", "UPI payments", reports=3), cand("b", "y", "UPI payments")], v, now=NOW)
    assert feed[0].candidate.post_id == "b"


def test_explanation_sums_to_score():
    v = viewer("food")
    (r,), _ = rank([cand("a", "x", "food offer", source="in_network")], v, now=NOW)
    parts = sum(val for k, val in r.explanation.items() if not k.startswith("raw_"))
    assert abs(parts - r.score) < 1e-3


def test_agent_rank_resists_sybil_ring():
    agents = ["swiggy", "phonepe", "zomato", "s1", "s2", "s3"]
    edges = [("zomato", "swiggy", "cite"), ("phonepe", "swiggy", "follow"), ("swiggy", "phonepe", "follow")]
    # sybils endorse each other heavily
    edges += [(a, b, "cite") for a in ("s1", "s2", "s3") for b in ("s1", "s2", "s3") if a != b]
    t = agent_rank(agents, verified={"swiggy", "phonepe", "zomato"}, edges=edges)
    assert min(t["swiggy"], t["phonepe"]) > max(t["s1"], t["s2"], t["s3"])
    assert all(0 <= v <= 1 for v in t.values())
