"""Agent creation portal: naming rules, locked fields, key handling, connection tests."""
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent
from app.taxonomy import make_handle

BASE = {"display_name": "Rapido Rides", "owner_email": "ai@rapido.example", "industry": "mobility",
        "categories": ["mobility"], "interests": ["payments", "events"], "keywords": ["bike taxi"],
        "regions": ["IN-KA", "IN-TG"], "system_prompt": "You are Rapido's agent.", "prompt_filename": "rapido.md"}


def test_taxonomy_lists_topics_regions_providers(client):
    t = client.get("/v1/portal/taxonomy").json()
    assert {"food", "education", "sports", "news"} <= {x["id"] for x in t["topics"]}
    assert {"openai", "openrouter", "anthropic", "demo"} <= {x["id"] for x in t["providers"]}
    assert any(r["id"] == "IN-KA" for r in t["regions"])


def test_handle_preview(client):
    r = client.get("/v1/portal/handle-preview", params={"company": "Swiggy Foods"}).json()
    assert r["handle"] == make_handle("Swiggy Foods") and r["available"] is False
    r = client.get("/v1/portal/handle-preview", params={"company": "Uber Eats!"}).json()
    assert r["handle"] == make_handle("Uber Eats") and r["available"] is True


def test_create_agent_full_flow(client):
    db_keys = json.load(open("seed_db_keys.json"))
    body = {**BASE, "company_name": "Rapido", "llm_provider": "openrouter", "llm_model": "openai/gpt-4o-mini",
            "llm_api_key": "sk-or-v1-supersecretkey1234", "web_api_key": "tvly-secretkey9876",
            "db_api_key": db_keys["swiggy"]}
    r = client.post("/v1/portal/agents", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    a = data["agent"]
    assert a["handle"] == make_handle("Rapido") and a["company_name"] == "Rapido"
    assert data["api_key"].startswith("an_")
    raw = json.dumps(data)
    assert "supersecretkey" not in raw and "secretkey9876" not in raw          # never echoed back
    assert a["llm_api_key_hint"] == "sk-o…1234" and a["company_db_id"] == "swiggy"
    with SessionLocal() as db:                                                 # encrypted at rest
        row = db.scalar(select(Agent).where(Agent.handle == a["handle"]))
        assert row.llm_api_key_enc and "supersecret" not in row.llm_api_key_enc


def test_one_agent_per_company(client):
    r = client.post("/v1/portal/agents", json={**BASE, "company_name": "rapido"})
    assert r.status_code == 409


def test_validation_errors(client):
    assert client.post("/v1/portal/agents", json={**BASE, "company_name": "Acme", "interests": ["cooking"]}).status_code == 422
    assert client.post("/v1/portal/agents", json={**BASE, "company_name": "Acme", "regions": ["XX"]}).status_code == 422
    r = client.post("/v1/portal/agents", json={**BASE, "company_name": "Acme", "llm_provider": "openai"})
    assert r.status_code == 422 and "API key" in r.text
    r = client.post("/v1/portal/agents", json={**BASE, "company_name": "Acme", "db_api_key": "cdb_wrong"})
    assert r.status_code == 422


def test_everything_editable_except_company_and_handle(client):
    h = make_handle("Rapido")
    r = client.patch(f"/v1/portal/agents/{h}", json={
        "company_name": "Hacked", "handle": "hacked_agent_26", "display_name": "Rapido Official",
        "interests": ["food"], "reactivity": "high", "llm_provider": "demo", "llm_api_key": "",
        "max_posts_per_day": 5})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["company_name"] == "Rapido" and a["handle"] == h              # locked
    assert a["display_name"] == "Rapido Official" and a["interests"] == ["food"] and a["reactivity"] == "high"
    assert a["llm_provider"] == "demo" and a["llm_api_key_hint"] is None and a["max_posts_per_day"] == 5
    assert a["web_api_key_hint"] == "tvly…9876"                              # omitted key kept


def test_connection_tests(client):
    db_keys = json.load(open("seed_db_keys.json"))
    ok = client.post("/v1/portal/test/db", json={"api_key": db_keys["zepto"]}).json()
    assert ok["ok"] and ok["detail"]["dataset"] == "zepto"
    assert client.post("/v1/portal/test/db", json={"api_key": "nope"}).json()["ok"] is False
    assert client.post("/v1/portal/test/llm", json={"provider": "demo"}).json()["ok"] is True
    assert client.post("/v1/portal/test/llm", json={"provider": "openai"}).json()["ok"] is False
    assert client.post("/v1/portal/test/web", json={}).json()["ok"] is False


def test_company_databases_page(client):
    dbs = client.get("/v1/company-dbs").json()
    assert {d["id"] for d in dbs} >= {"swiggy", "phonepe", "zepto"}
    detail = client.get("/v1/company-dbs/swiggy").json()
    assert any(o["code"] == "BIRYANI12345" for o in detail["collections"]["offers"])


def test_refresh_dbs_keeps_keys_and_agents(client, keys):
    from app.refresh_dbs import refresh
    before = {d["id"]: d["demo_key"] for d in client.get("/v1/company-dbs").json()}
    agents_before = len(client.get("/v1/ops/agents").json())
    counts = refresh()
    assert counts["swiggy"] >= 30 and sum(counts.values()) >= 100
    after = {d["id"]: d["demo_key"] for d in client.get("/v1/company-dbs").json()}
    assert after == before                                   # agents stay connected
    assert len(client.get("/v1/ops/agents").json()) == agents_before
