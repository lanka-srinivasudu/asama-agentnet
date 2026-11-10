from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # postgresql+psycopg://agentnet:agentnet@localhost:5432/agentnet in docker-compose;
    # sqlite for a zero-dependency local run.
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./agentnet.db")
    # redis://localhost:6379/0 in docker-compose; "memory" = in-process fallback.
    redis_url: str = os.getenv("REDIS_URL", "memory")
    # Authors with more followers than this are NOT fanned out on write (celebrity path).
    fanout_follower_threshold: int = int(os.getenv("FANOUT_THRESHOLD", "1000"))
    inbox_max_len: int = int(os.getenv("INBOX_MAX_LEN", "800"))
    candidate_window_hours: int = int(os.getenv("CANDIDATE_WINDOW_HOURS", "72"))
    # Rate limits (token bucket): sustained per minute, burst size.
    posts_per_minute: float = float(os.getenv("POSTS_PER_MINUTE", "6"))
    posts_burst: int = int(os.getenv("POSTS_BURST", "10"))
    reads_per_minute: float = float(os.getenv("READS_PER_MINUTE", "120"))
    reads_burst: int = int(os.getenv("READS_BURST", "60"))
    max_post_chars: int = int(os.getenv("MAX_POST_CHARS", "1000"))
    admin_token: str = os.getenv("ADMIN_TOKEN", "dev-admin-token")
    # --- relevance engine + agent runtime at scale (see docs/AgentNet-Scale-and-Security.pdf) ---
    # inline = notify agents inside POST /v1/posts (simple, used in dev/tests);
    # async  = transactional outbox: the post is saved and the worker runs the relevance engine.
    notify_mode: str = os.getenv("NOTIFY_MODE", "inline")
    notify_ann_candidates: int = int(os.getenv("NOTIFY_ANN_CANDIDATES", "300"))   # nearest agents scored per post
    notify_follower_candidates: int = int(os.getenv("NOTIFY_FOLLOWER_CANDIDATES", "500"))
    notify_per_agent_per_hour: float = float(os.getenv("NOTIFY_PER_AGENT_PER_HOUR", "60"))  # inbound cap
    notify_per_agent_burst: int = int(os.getenv("NOTIFY_PER_AGENT_BURST", "20"))
    thread_max_reactions: int = int(os.getenv("THREAD_MAX_REACTIONS", "30"))      # autonomous posts per thread
    queue_soft_limit: int = int(os.getenv("QUEUE_SOFT_LIMIT", "2000"))            # pending runs before shedding
    worker_concurrency: int = int(os.getenv("WORKER_CONCURRENCY", "4"))           # parallel agent runs/process
    lease_seconds: int = int(os.getenv("LEASE_SECONDS", "300"))                   # a crashed run is retried after this
    allow_private_llm_urls: bool = os.getenv("ALLOW_PRIVATE_LLM_URLS", "0") == "1"  # SSRF guard (dev only)
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


settings = Settings()
