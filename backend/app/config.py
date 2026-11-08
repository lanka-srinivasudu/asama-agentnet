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
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


settings = Settings()
