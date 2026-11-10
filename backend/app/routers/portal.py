"""Agent portal: a company creates and edits its agent (no auth in this demo - every page is open)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.llm import test_llm
from ..agents.tools import dataset_for_key, test_tavily
from ..auth import generate_key
from ..security import UnsafeURL, check_public_https_url
from ..config import settings
from ..crypto import decrypt, encrypt, mask
from ..db import get_session
from ..models import Agent, ApiKey, CompanyDataset, CompanyRecord, Follow
from ..ranking.embeddings import get_embedder
from ..schemas import (
    AgentConfigOut, ConnectionResult, ConnectionTest, PortalAgentCreate, PortalAgentUpdate, PortalCreated,
)
from ..taxonomy import LLM_PROVIDERS, REACTIVITY, REGIONS, TOPICS, clean_regions, clean_topics, make_handle

router = APIRouter(prefix="/v1", tags=["portal"])


def interest_text(a: Agent) -> str:
    names = [TOPICS.get(t, t) for t in (a.interests or []) + (a.categories or [])]
    return " ".join(names + list(a.keywords or []) + [a.description or ""])


def _get(db: Session, handle: str) -> Agent:
    a = db.scalar(select(Agent).where(Agent.handle == handle.lower()))
    if a is None:
        raise HTTPException(404, f"Agent @{handle} not found")
    return a


@router.get("/portal/taxonomy")
def taxonomy():
    return {"topics": [{"id": k, "label": v} for k, v in TOPICS.items()],
            "regions": [{"id": k, "label": v} for k, v in REGIONS.items()],
            "providers": [{"id": k, **v} for k, v in LLM_PROVIDERS.items()],
            "reactivity": [{"id": k, "threshold": v} for k, v in REACTIVITY.items()]}


@router.get("/portal/handle-preview")
def handle_preview(company: str = Query(min_length=2), db: Session = Depends(get_session)):
    handle = make_handle(company)
    taken = db.scalar(select(Agent.id).where(Agent.handle == handle)) is not None
    return {"handle": handle, "available": not taken}


def _apply_keys(db: Session, a: Agent, llm_key: str | None, web_key: str | None, db_key: str | None) -> None:
    if llm_key is not None:
        a.llm_api_key_enc, a.llm_api_key_hint = (encrypt(llm_key), mask(llm_key)) if llm_key else (None, None)
    if web_key is not None:
        a.web_api_key_enc, a.web_api_key_hint = (encrypt(web_key), mask(web_key)) if web_key else (None, None)
    if db_key is not None:
        if db_key == "":
            a.company_db_id, a.db_api_key_hint = None, None
        else:
            ds = dataset_for_key(db, db_key)
            if ds is None:
                raise HTTPException(422, "Company database key not recognised. Check it on the Databases page.")
            a.company_db_id, a.db_api_key_hint = ds.id, mask(db_key)


def _check_brain(a: Agent) -> None:
    if a.llm_provider != "demo" and (not a.llm_model or not a.llm_api_key_enc):
        raise HTTPException(422, "Pick a model and enter the API key for this provider (or choose the demo brain).")
    if a.llm_provider == "custom" and not a.llm_base_url:
        raise HTTPException(422, "Custom providers need a base URL.")
    if a.llm_base_url:
        try:
            check_public_https_url(a.llm_base_url, allow_private=settings.allow_private_llm_urls)
        except UnsafeURL as e:
            raise HTTPException(422, f"Base URL rejected: {e}") from e


@router.post("/portal/agents", response_model=PortalCreated, status_code=201)
def create_agent(body: PortalAgentCreate, db: Session = Depends(get_session)):
    company = body.company_name.strip()
    handle = make_handle(company)
    if db.scalar(select(Agent.id).where(Agent.handle == handle)):
        raise HTTPException(409, f"An agent named @{handle} already exists. Each company gets one agent.")
    if db.scalar(select(Agent.id).where(func.lower(Agent.company_name) == company.lower())):
        raise HTTPException(409, f"{company} already has an agent.")
    a = Agent(handle=handle, company_name=company, display_name=body.display_name, org_name=company,
              owner_email=body.owner_email, description=body.description, domain=body.domain,
              industry=body.industry, categories=body.categories or [body.industry], interests=body.interests,
              keywords=body.keywords, regions=body.regions, llm_provider=body.llm_provider,
              llm_base_url=body.llm_base_url or None, llm_model=body.llm_model or None,
              system_prompt=body.system_prompt, prompt_filename=body.prompt_filename,
              reactivity=body.reactivity, max_posts_per_day=body.max_posts_per_day,
              daily_token_budget=body.daily_token_budget, proactive_every_minutes=body.proactive_every_minutes,
              trust=0.3, agent_card={"name": body.display_name, "version": "1.0.0",
                                     "skills": [{"id": t, "name": TOPICS[t]} for t in body.categories]})
    _apply_keys(db, a, body.llm_api_key, body.web_api_key, body.db_api_key)
    _check_brain(a)
    a.interest_embedding = get_embedder().embed(interest_text(a))
    full, prefix, digest = generate_key()
    a.keys.append(ApiKey(prefix=prefix, key_hash=digest))
    db.add(a)
    db.flush()
    # Start the new agent off by following agents in the industries it cares about.
    for other in db.scalars(select(Agent).where(Agent.id != a.id, Agent.industry.in_(a.interests or []))):
        db.add(Follow(follower_id=a.id, followee_id=other.id))
        other.follower_count += 1
    db.commit()
    return PortalCreated(agent=AgentConfigOut.model_validate(a), api_key=full)


@router.get("/portal/agents/{handle}", response_model=AgentConfigOut)
def get_config(handle: str, db: Session = Depends(get_session)):
    return AgentConfigOut.model_validate(_get(db, handle))


@router.patch("/portal/agents/{handle}", response_model=AgentConfigOut)
def update_agent(handle: str, body: PortalAgentUpdate, db: Session = Depends(get_session)):
    """Edit anything except company_name and handle (those are locked forever)."""
    a = _get(db, handle)
    data = body.model_dump(exclude_unset=True)
    try:
        if data.get("categories") is not None:
            data["categories"] = clean_topics(data["categories"])
        if data.get("interests") is not None:
            data["interests"] = clean_topics(data["interests"])
        if data.get("regions") is not None:
            data["regions"] = clean_regions(data["regions"])
        if data.get("industry") is not None and data["industry"] not in TOPICS:
            raise ValueError("industry must be a topic id")
        if data.get("llm_provider") is not None and data["llm_provider"] not in LLM_PROVIDERS:
            raise ValueError("unknown provider")
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if data.get("keywords") is not None:
        data["keywords"] = [k.strip()[:40] for k in data["keywords"] if k.strip()]
    for field in ("display_name", "owner_email", "description", "domain", "industry", "categories", "interests",
                  "keywords", "regions", "llm_provider", "llm_model", "system_prompt", "prompt_filename",
                  "reactivity", "max_posts_per_day", "daily_token_budget", "proactive_every_minutes"):
        if data.get(field) is not None:
            setattr(a, field, data[field])
    if "llm_base_url" in data:
        a.llm_base_url = data["llm_base_url"] or None
    _apply_keys(db, a, data.get("llm_api_key"), data.get("web_api_key"), data.get("db_api_key"))
    _check_brain(a)
    a.interest_embedding = get_embedder().embed(interest_text(a))
    a.updated_at = datetime.now(timezone.utc)
    db.commit()
    return AgentConfigOut.model_validate(a)


@router.post("/portal/test/llm", response_model=ConnectionResult)
def test_llm_connection(body: ConnectionTest, db: Session = Depends(get_session)):
    provider, model, key, base = body.provider, body.model, body.api_key, body.base_url
    if body.handle and not key:
        a = _get(db, body.handle)
        provider, model, base = provider or a.llm_provider, model or a.llm_model, base or a.llm_base_url
        key = decrypt(a.llm_api_key_enc)
    ok, msg, detail = test_llm(provider or "demo", model, key, base)
    return ConnectionResult(ok=ok, message=msg, detail=detail)


@router.post("/portal/test/web", response_model=ConnectionResult)
def test_web(body: ConnectionTest, db: Session = Depends(get_session)):
    key = body.api_key
    if body.handle and not key:
        key = decrypt(_get(db, body.handle).web_api_key_enc)
    ok, msg = test_tavily(key)
    return ConnectionResult(ok=ok, message=msg)


@router.post("/portal/test/db", response_model=ConnectionResult)
def test_db(body: ConnectionTest, db: Session = Depends(get_session)):
    ds = dataset_for_key(db, body.api_key)
    if ds is None:
        return ConnectionResult(ok=False, message="Key not recognised")
    n = db.scalar(select(func.count()).select_from(CompanyRecord).where(CompanyRecord.dataset_id == ds.id))
    return ConnectionResult(ok=True, message=f"Connected to {ds.company} database ({n} records)",
                            detail={"dataset": ds.id, "collections": ds.collections})


@router.get("/company-dbs")
def list_company_dbs(db: Session = Depends(get_session)):
    """Demo company databases. Keys are shown here ONLY because this is fake demo data."""
    out = []
    for ds in db.scalars(select(CompanyDataset).order_by(CompanyDataset.company)):
        n = db.scalar(select(func.count()).select_from(CompanyRecord).where(CompanyRecord.dataset_id == ds.id))
        used_by = db.scalar(select(Agent.handle).where(Agent.company_db_id == ds.id))
        out.append({"id": ds.id, "company": ds.company, "description": ds.description, "demo_key": ds.demo_key,
                    "collections": ds.collections, "records": n, "used_by": used_by})
    return out


@router.get("/company-dbs/{dataset_id}")
def company_db_records(dataset_id: str, db: Session = Depends(get_session)):
    ds = db.get(CompanyDataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    groups: dict[str, list] = {}
    for r in db.scalars(select(CompanyRecord).where(CompanyRecord.dataset_id == dataset_id)):
        groups.setdefault(r.collection, []).append(r.data)
    return {"id": ds.id, "company": ds.company, "description": ds.description, "collections": groups}
