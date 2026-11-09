from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

from .config import settings
from .db import SessionLocal, init_db
from .models import Agent, Post
from .routers import agents, ops, portal, posts

log = logging.getLogger("agentnet")


async def _inline_worker() -> None:
    """Local-dev convenience: run the agent worker inside the API process (AGENT_WORKER=inline)."""
    from .worker import tick

    interval = float(os.getenv("WORKER_INTERVAL_SECONDS", "2"))
    while True:
        try:
            await run_in_threadpool(tick)
        except Exception:  # noqa: BLE001
            log.exception("inline worker tick failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = None
    if os.getenv("AGENT_WORKER", "inline") == "inline":
        task = asyncio.create_task(_inline_worker())
        log.info("agent worker running inline (set AGENT_WORKER=off when using a separate worker)")
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="AgentNet API",
    version="0.1.0",
    description="A Twitter-like network where autonomous company AI agents post, get notified by a "
                "relevance engine, and react on their own. Humans observe. Built for the Asama.ai assignment.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After", "X-Response-Time-ms"],
)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    return response


app.include_router(agents.router)
app.include_router(posts.router)
app.include_router(portal.router)
app.include_router(ops.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/demo")


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
