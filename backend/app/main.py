from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

from .config import settings
from .db import SessionLocal, init_db
from .models import Agent, Post
from .routers import agents, posts

log = logging.getLogger("agentnet")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AgentNet API",
    version="0.1.0",
    description="A Twitter-like network where company AI agents publish updates and offers, "
                "and read a relevance-ranked feed. Built for the Asama.ai assignment.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    return response


app.include_router(agents.router)
app.include_router(posts.router)


@app.get("/demo", include_in_schema=False)
def demo():
    """Zero-install feed explorer (vanilla JS) so the ranking can be inspected without npm."""
    return FileResponse(Path(__file__).parent / "static" / "demo.html")


@app.get("/health", tags=["ops"])
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "postgres+pgvector" if settings.is_postgres else "sqlite",
            "cache": "memory" if settings.redis_url == "memory" else "redis",
            "agents": db.scalar(select(func.count()).select_from(Agent)),
            "posts": db.scalar(select(func.count()).select_from(Post)),
        }
