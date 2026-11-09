"""Tools an agent can use while thinking: its company's database and the live web (Tavily).

Both are cached with TTLs so repeated questions don't re-hit the database or burn web-search
credits: company data 5 minutes (offers/stock change), web results 1 hour.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cache import get_cache
from ..models import CompanyDataset, CompanyRecord

DB_TTL_SECONDS = 300
WEB_TTL_SECONDS = 3600
TAVILY_URL = "https://api.tavily.com/search"
_WORD = re.compile(r"[a-z0-9]+")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def dataset_for_key(db: Session, key: str | None) -> CompanyDataset | None:
    if not key:
        return None
    return db.scalar(select(CompanyDataset).where(CompanyDataset.key_hash == key_hash(key.strip())))


def company_db_query(db: Session, dataset_id: str | None, query: str = "", collection: str | None = None,
                     limit: int = 5) -> list[dict[str, Any]]:
    """Keyword search over the agent's OWN company data. Read-only by construction."""
    if not dataset_id:
        return []
    ck = f"db:{dataset_id}:{collection or '*'}:{query.lower().strip()}:{limit}"
    cache = get_cache()
    hit = cache.get_json(ck)
    if hit is not None:
        return hit
    q = select(CompanyRecord).where(CompanyRecord.dataset_id == dataset_id)
    if collection:
        q = q.where(CompanyRecord.collection == collection)
    rows = list(db.scalars(q))
    words = set(_WORD.findall(query.lower()))
    scored = []
    for r in rows:
        hay = set(_WORD.findall(r.search_text))
        overlap = len(words & hay) if words else 0
        scored.append((overlap, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [r for s, r in scored if s > 0][:limit] or ([r for _, r in scored][:limit] if not words else [])
    result = [{"collection": r.collection, **r.data} for r in picked]
    cache.set_json(ck, result, DB_TTL_SECONDS)
    return result


def all_codes(db: Session, dataset_id: str | None) -> set[str]:
    """Every coupon/offer code in the company DB - used by the anti-hallucination guard."""
    if not dataset_id:
        return set()
    codes = set()
    for r in db.scalars(select(CompanyRecord).where(CompanyRecord.dataset_id == dataset_id)):
        c = r.data.get("code")
        if c:
            codes.add(str(c).upper())
    return codes


def web_search(api_key: str | None, query: str, max_results: int = 3) -> list[dict[str, str]]:
    """Live web search through Tavily (https://docs.tavily.com). Free tier = 1,000 credits/month."""
    if not api_key or not query.strip():
        return []
    ck = f"web:{hashlib.sha1(query.lower().encode()).hexdigest()}:{max_results}"
    cache = get_cache()
    hit = cache.get_json(ck)
    if hit is not None:
        return hit
    r = httpx.post(TAVILY_URL, json={"query": query, "max_results": max_results, "search_depth": "basic"},
                   headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    r.raise_for_status()
    results = [{"title": x.get("title", "")[:120], "url": x.get("url", ""), "content": x.get("content", "")[:300]}
               for x in r.json().get("results", [])[:max_results]]
    cache.set_json(ck, results, WEB_TTL_SECONDS)
    return results


def test_tavily(api_key: str | None) -> tuple[bool, str]:
    if not api_key:
        return False, "No web access key provided."
    try:
        res = web_search(api_key, "latest UPI news India", max_results=1)
        return True, f"Tavily works ({len(res)} result)"
    except httpx.HTTPStatusError as e:
        return False, f"Tavily rejected the key (HTTP {e.response.status_code})"
    except Exception as e:  # noqa: BLE001
        return False, f"Could not reach Tavily: {type(e).__name__}"
